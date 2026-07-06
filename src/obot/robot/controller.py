from __future__ import annotations

import asyncio
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from ..core import events
from ..core.models import SpeechMarker
from ..speech.config import MotionSettings, SpeechSettings
from ..speech.engine import SpeechEngine, estimate_duration_s
from ..speech.player import PlaybackError
from ..speech.tts import TTSError
from . import joints

#* ohbot is imported lazily inside HardwareObotController.__init__ so the COM port scan
#* does not block startup. Module-level None until the hardware controller is first created.
ohbot = None  # type: ignore

MarkerCallback = Callable[[SpeechMarker], Awaitable[None]]


def _import_ohbot(preferred_port: str | None = None) -> None:
    """Import the ohbot module, trying preferred_port first to avoid a slow full scan."""
    global ohbot
    import serial.tools.list_ports as _lp

    _orig = None
    if preferred_port:
        _orig = _lp.comports

        def _preferred_first():
            all_ports = _orig()
            preferred = [p for p in all_ports if p[0] == preferred_port]
            rest = [p for p in all_ports if p[0] != preferred_port]
            return preferred + rest

        _lp.comports = _preferred_first

    try:
        from ohbot import ohbot as _mod
        ohbot = _mod
    except ImportError:
        ohbot = None
    finally:
        if _orig is not None:
            _lp.comports = _orig


@dataclass(slots=True)
class MotionOffset:
    """Relative servo movement request consumed by the mixer thread."""
    joint_id: int
    delta: float
    duration_s: float


class ObotController(ABC):
    """Robot-facing commands live here."""

    @abstractmethod
    async def speak_sentence(
        self,
        sentence: str,
        markers: Sequence[SpeechMarker] = (),
        on_marker: MarkerCallback | None = None,
    ) -> None:
        """Speak a completed sentence, firing each marker at the matching word."""

    @abstractmethod
    async def nod(self) -> None: ...

    @abstractmethod
    async def look_left(self) -> None: ...

    @abstractmethod
    async def look_right(self) -> None: ...

    @abstractmethod
    async def blink(self, announce: bool = True) -> None: ...

    @abstractmethod
    async def wink(self) -> None: ...

    @abstractmethod
    async def shake_head(self) -> None: ...

    @abstractmethod
    async def set_emotion(self, emotion: str) -> None: ...

    def prepare_sentence(self, sentence: str) -> None:
        """Hint that this sentence will be spoken soon (TTS prefetch). Optional."""

    def turn_finished(self) -> None:
        """A speaking turn ended; drop cached audio for sentences never voiced."""

    def enqueue_offset(self, joint_id: int, delta: float, duration_s: float) -> bool:
        """Request a temporary servo offset. Returns False when there are no servos.

        This is the public hook behavior modules use for small ambient motions
        (sway, eye wander). Controllers without motors simply ignore it.
        """
        del joint_id, delta, duration_s
        return False

    async def stop_speaking(self) -> None:
        """Stop current speech at the next word boundary. Override to actually halt TTS."""

    def close(self) -> None:
        """Release hardware resources. Override to actually stop threads/hardware."""


class AnimatedObotController(ObotController):
    """Shared base for controllers with actual joints (hardware servos or the sim face).

    Owns two cooperating pieces:

    * the **motor mixer** — a thread that every ``tick_s`` blends all active
      motion offsets plus the live lip position from speech, applies slew-rate
      limiting so joints travel at a bounded speed instead of snapping, and
      writes only joints that actually changed;
    * the **speech engine** — the custom say(): TTS (Gemini/local), playback,
      mouth animation, word-timed markers, and word-boundary interruption.

    Subclasses implement :meth:`_write_motor` (serial servo write / sim canvas).
    """

    def __init__(
        self,
        speech_settings: SpeechSettings | None = None,
        motion_settings: MotionSettings | None = None,
        gemini_api_key: str = "",
    ) -> None:
        super().__init__()
        self.motion = motion_settings or MotionSettings()
        self.speech = SpeechEngine(speech_settings or SpeechSettings(), gemini_api_key)

        self._offset_requests: list[MotionOffset] = []
        self._offset_lock = threading.Lock()
        #* (top_delta, bottom_delta) written by the speech engine's mouth sink;
        #* read by the mixer thread. Replaced atomically as a tuple.
        self._lips: tuple[float, float] = (0.0, 0.0)
        self._speech_stopped = threading.Event()
        self._stop_event = threading.Event()

        self._mixer_thread = threading.Thread(target=self._mixer_loop, daemon=True)
        self._mixer_thread.start()

    # -- motor mixing ------------------------------------------------------------------

    @abstractmethod
    def _write_motor(self, joint_id: int, position: float, speed: int) -> None:
        """Send one joint position (0..10) to the physical/virtual robot."""

    def enqueue_offset(self, joint_id: int, delta: float, duration_s: float) -> bool:
        duration_s = max(0.0, duration_s)
        with self._offset_lock:
            self._offset_requests.append(
                MotionOffset(joint_id=joint_id, delta=delta, duration_s=duration_s)
            )
        return True

    def _mixer_loop(self) -> None:
        """Blend offsets + speech lips into joint targets and chase them at a bounded rate."""
        tick = max(0.01, self.motion.tick_s)
        current = {j: joints.REST_POSITION for j in joints.ALL_JOINTS}
        written = dict(current)
        #* Joint stream for the GUI face preview, throttled to ~15 Hz and only built
        #* when something is actually listening (the console/hardware flow pays nothing).
        last_emit = 0.0
        emit_period = 1.0 / 15.0

        while not self._stop_event.wait(tick):
            targets = {j: joints.REST_POSITION for j in joints.ALL_JOINTS}
            sums = {j: 0.0 for j in joints.ALL_JOINTS}
            counts = {j: 1 for j in joints.ALL_JOINTS}

            with self._offset_lock:
                expired: list[MotionOffset] = []
                for req in self._offset_requests:
                    if req.duration_s <= 0:
                        expired.append(req)
                        continue
                    #* Offsets are blended by averaging (baseline counts once), so
                    #* overlapping actions soften each other instead of stacking
                    #* into a slam past the servo limits.
                    sums[req.joint_id] += req.delta
                    counts[req.joint_id] += 1
                    req.duration_s -= tick
                for req in expired:
                    self._offset_requests.remove(req)

            for j in joints.ALL_JOINTS:
                targets[j] = joints.REST_POSITION + sums[j] / counts[j]

            #* Speech lips ride on top of whatever the offsets decided (an emotion
            #* can hold the mouth corners while the visemes open/close it).
            lip_top, lip_bottom = self._lips
            targets[joints.TOPLIP] += lip_top
            targets[joints.BOTTOMLIP] += lip_bottom

            for j in joints.ALL_JOINTS:
                target = min(10.0, max(0.0, targets[j]))
                #* Slew-rate limiting: travel toward the target at a bounded
                #* positions-per-second speed so motion is smooth, not snappy.
                #* Lips and lids use a much higher rate — visemes and blinks
                #* have to hit their pose within a frame or two.
                rate = (
                    self.motion.lip_rate_limit
                    if j in joints.FAST_JOINTS
                    else self.motion.rate_limit
                )
                max_step = rate * tick
                step = min(max_step, max(-max_step, target - current[j]))
                current[j] += step

                if abs(current[j] - written[j]) >= self.motion.write_epsilon:
                    self._write_motor(j, current[j], self.motion.move_speed)
                    written[j] = current[j]

            #* Stream the smoothed pose so a GUI can mirror the face. Gated so the
            #* dict is not even built when unobserved; throttled below the tick rate.
            now = time.monotonic()
            if now - last_emit >= emit_period and events.has_subscribers(events.JOINTS):
                last_emit = now
                events.emit(
                    events.JOINTS,
                    {joints.JOINT_NAMES[j]: round(current[j], 3) for j in joints.ALL_JOINTS},
                )

    # -- speech ------------------------------------------------------------------------

    def _set_lips(self, top_delta: float, bottom_delta: float) -> None:
        self._lips = (top_delta, bottom_delta)

    def prepare_sentence(self, sentence: str) -> None:
        if any(ch.isalnum() for ch in sentence):
            self.speech.prepare(sentence)

    def turn_finished(self) -> None:
        self.speech.discard_prefetches()

    async def speak_sentence(
        self,
        sentence: str,
        markers: Sequence[SpeechMarker] = (),
        on_marker: MarkerCallback | None = None,
    ) -> None:
        print(f"[speech] {sentence}")
        #* Announce the sentence up front so a GUI shows the bot bubble with no latency;
        #* the active engine is only known once synthesis returns (emitted below).
        events.emit(events.SPEECH, {"text": sentence, "event": "spoken", "engine": None})
        #* Cleared per sentence so a stop from the previous sentence doesn't suppress this one.
        self._speech_stopped.clear()

        if not any(ch.isalnum() for ch in sentence):
            await asyncio.sleep(0.3)
            return

        try:
            result = await self.speech.speak(
                sentence, markers=markers, on_marker=on_marker, mouth_sink=self._set_lips
            )
            if not result.completed:
                print("[speech] (cut off at word boundary)")
            events.emit(events.SPEECH, {
                "text": sentence,
                "event": "cutoff" if not result.completed else "done",
                "engine": result.engine,
            })
        except (TTSError, PlaybackError) as exc:
            #* No audio possible: keep the conversation alive with simulated pacing
            #* so sentences, interrupts and turn-taking still behave sensibly.
            print(f"[speech] audio unavailable ({exc}); simulating timing.")
            events.emit(events.LOG, {"level": "warn", "message": f"audio unavailable ({exc}); simulating timing."})
            await self._simulate_sentence(sentence, markers, on_marker)
            events.emit(events.SPEECH, {"text": sentence, "event": "done", "engine": None})

    async def _simulate_sentence(
        self,
        sentence: str,
        markers: Sequence[SpeechMarker],
        on_marker: MarkerCallback | None,
    ) -> None:
        duration = estimate_duration_s(sentence, self.speech.settings.estimate_wpm)
        pending = sorted(markers, key=lambda m: m.char_pos)
        fired: list[asyncio.Task] = []
        elapsed = 0.0
        step = 0.05
        length = max(1, len(sentence))
        while elapsed < duration:
            if self._speech_stopped.is_set():
                break
            while pending and (pending[0].char_pos / length) * duration <= elapsed:
                marker = pending.pop(0)
                if on_marker is not None:
                    fired.append(asyncio.create_task(on_marker(marker)))
            await asyncio.sleep(step)
            elapsed += step
        if fired:
            await asyncio.gather(*fired, return_exceptions=True)

    async def stop_speaking(self) -> None:
        self._speech_stopped.set()
        self.speech.request_stop()

    def close(self) -> None:
        #* Defensive: if __init__ bailed early these attributes may not exist,
        #* and __del__ must not raise during garbage collection.
        speech = getattr(self, "speech", None)
        if speech is not None:
            speech.close()
        stop_event = getattr(self, "_stop_event", None)
        if stop_event is not None:
            stop_event.set()
        mixer = getattr(self, "_mixer_thread", None)
        if mixer is not None and mixer.is_alive():
            mixer.join(timeout=1.0)

    def __del__(self) -> None:
        self.close()

    # -- movement actions ---------------------------------------------------------------
    # Each action feeds offset requests into the mixer: joint id, delta, duration.

    async def nod(self) -> None:
        print("[action] nod")
        events.emit(events.ACTION, {"name": "nod"})

        moveSteps = 5
        for _ in range(moveSteps):
            self.enqueue_offset(joints.HEADNOD, +0.6, 0.1)
            await asyncio.sleep(0.1)
            self.enqueue_offset(joints.HEADNOD, -0.6, 0.15)
            await asyncio.sleep(0.15)

    async def look_left(self) -> None:
        print("[action] look_left")
        events.emit(events.ACTION, {"name": "look_left"})
        self.enqueue_offset(joints.EYETURN, +5.0, 1.5)
        await asyncio.sleep(1.5)

    async def look_right(self) -> None:
        print("[action] look_right")
        events.emit(events.ACTION, {"name": "look_right"})
        self.enqueue_offset(joints.EYETURN, -5.0, 1.5)
        await asyncio.sleep(1.5)

    async def blink(self, announce: bool = True) -> None:
        if announce:
            print("[action] blink")
            #* Only script/marker-driven blinks are announced; ambient auto_blink
            #* passes announce=False so it stays off both the console and the event feed.
            events.emit(events.ACTION, {"name": "blink"})
        self.enqueue_offset(joints.LIDBLINK, -8.0, 0.5)
        await asyncio.sleep(0.5)

    async def wink(self) -> None:
        #* Obot has a single shared lid servo, so a wink is rendered as a quick,
        #* snappier blink — the closest the hardware can manage.
        print("[action] wink")
        events.emit(events.ACTION, {"name": "wink"})
        self.enqueue_offset(joints.LIDBLINK, -8.0, 0.2)
        await asyncio.sleep(0.3)

    async def shake_head(self) -> None:
        print("[action] shake_head")
        events.emit(events.ACTION, {"name": "shake_head"})
        self.enqueue_offset(joints.HEADTURN, -2.0, 0.5)
        self.enqueue_offset(joints.EYETURN, +2.0, 0.5)
        await asyncio.sleep(0.5)
        self.enqueue_offset(joints.HEADTURN, +2.0, 1.0)
        self.enqueue_offset(joints.EYETURN, -2.0, 1.0)
        await asyncio.sleep(1)
        self.enqueue_offset(joints.HEADTURN, -2.0, 0.5)
        self.enqueue_offset(joints.EYETURN, +2.0, 0.5)
        await asyncio.sleep(0.5)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion] {emotion}")
        events.emit(events.EMOTION, {"name": emotion})
        #* Emotions hold the mouth corners/eyes with offsets; speech visemes stack
        #* on top of them in the mixer, so the face keeps emoting while talking.
        ## Emotions basics: Happy, Sad
        ## Need Testing
        if emotion == "Happy":
            self.enqueue_offset(joints.TOPLIP, +3.0, 1.0)
            self.enqueue_offset(joints.BOTTOMLIP, +3.0, 1.0)
            self.enqueue_offset(joints.EYETURN, +2.0, 1.0)
        elif emotion == "Sad":
            self.enqueue_offset(joints.TOPLIP, -3.0, 1.0)
            self.enqueue_offset(joints.BOTTOMLIP, -3.0, 1.0)
            self.enqueue_offset(joints.EYETURN, -2.0, 1.0)


class HardwareObotController(AnimatedObotController):
    """Drives the physical Obot via the ohbot library's servo commands.

    Speech no longer goes through ohbot.say(): audio comes from the
    SpeechEngine (Gemini TTS or the local voice) played on the host's audio
    output, and the lips are driven by the mixer like every other joint.
    """

    def __init__(
        self,
        port: str | None = None,
        speech_settings: SpeechSettings | None = None,
        motion_settings: MotionSettings | None = None,
        gemini_api_key: str = "",
    ):
        global ohbot
        if ohbot is None:
            _import_ohbot(port)
        if ohbot is None:
            raise RuntimeError(
                "the 'ohbot' library is not installed, so the hardware controller "
                "cannot start. Install it on the robot/Pi, use --sim for the digital "
                "face, or ConsoleObotController (the demo falls back to it automatically)."
            )

        #* Serialise access to the ohbot library, which is not thread-safe.
        self._ohbot_lock = threading.Lock()
        with self._ohbot_lock:
            ohbot.reset()

        super().__init__(
            speech_settings=speech_settings,
            motion_settings=motion_settings,
            gemini_api_key=gemini_api_key,
        )

    def _write_motor(self, joint_id: int, position: float, speed: int) -> None:
        with self._ohbot_lock:
            ohbot.move(joint_id, position, speed)


class SimulatedObotController(AnimatedObotController):
    """Digital twin: identical motion/speech pipeline, rendered in the sim window.

    Everything (mixer, speech, lips, behaviors) behaves exactly like the
    hardware controller — only :meth:`_write_motor` differs, painting a tkinter
    face instead of writing servo serial commands. Audio still plays on the
    host speakers, so lip-sync and interruption can be tested without a robot.
    """

    def __init__(
        self,
        face,
        speech_settings: SpeechSettings | None = None,
        motion_settings: MotionSettings | None = None,
        gemini_api_key: str = "",
    ) -> None:
        #* Duck-typed face: anything with set_motor(joint_id, position). Normally a
        #* :class:`obot.sim.face.FaceWindow`.
        self._face = face
        super().__init__(
            speech_settings=speech_settings,
            motion_settings=motion_settings,
            gemini_api_key=gemini_api_key,
        )

    def _write_motor(self, joint_id: int, position: float, speed: int) -> None:
        del speed
        self._face.set_motor(joint_id, position)

    def close(self) -> None:
        super().close()
        face = getattr(self, "_face", None)
        if face is not None and hasattr(face, "close"):
            face.close()


class VirtualObotController(AnimatedObotController):
    """Headless twin: the full motor mixer + speech pipeline with no on-host window.

    Identical to :class:`SimulatedObotController` in every way that matters — mixer,
    slew limiting, real TTS audio on the host speakers, lip-sync, behaviors — but it
    renders nowhere. The joint positions are streamed on the ``joints`` event topic
    instead, so a remote GUI can draw the face itself (``--serve`` + face preview).
    This is the sensible controller for the control server on a machine without the
    robot and without wanting the tkinter sim window to pop up.
    """

    def _write_motor(self, joint_id: int, position: float, speed: int) -> None:
        #* No physical or on-screen output: the mixer still computes and emits every
        #* joint's position (see _mixer_loop), which is all a GUI face preview needs.
        del joint_id, position, speed


class ConsoleObotController(ObotController):
    """Hardware-free controller: prints what the robot *would* do and simulates timing.

    This is the reusable "run the whole program without an Obot" path — it needs no
    ohbot library, no servos, and no audio output, so the full pipeline (LLM
    streaming, the processor, interruption, and microphone input) can be exercised
    on a plain dev machine. Speech "playback" is modelled as a short sleep, and
    :meth:`stop_speaking` lets an interrupt cut that simulated playback short,
    mirroring the hardware contract.
    """

    def __init__(self) -> None:
        super().__init__()
        #* Tripped by stop_speaking so an in-flight simulated utterance ends early and a
        #* still-queued one is skipped — the same semantics as the hardware controller.
        self._speech_stopped = threading.Event()

    async def speak_sentence(
        self,
        sentence: str,
        markers: Sequence[SpeechMarker] = (),
        on_marker: MarkerCallback | None = None,
    ) -> None:
        print(f"[speech] {sentence}")
        events.emit(events.SPEECH, {"text": sentence, "event": "spoken", "engine": "console"})
        self._speech_stopped.clear()
        #* Roughly track real TTS pacing so barge-in timing feels realistic, but poll the
        #* stop flag so an interrupt can cut the "playback" mid-sentence.
        estimated_duration = min(0.2 + len(sentence) / 80, 1.5)
        pending = sorted(markers, key=lambda m: m.char_pos)
        fired: list[asyncio.Task] = []
        length = max(1, len(sentence))
        elapsed = 0.0
        step = 0.05
        cut_off = False
        while elapsed < estimated_duration:
            if self._speech_stopped.is_set():
                print("[speech] (cut off)")
                cut_off = True
                break
            while pending and (pending[0].char_pos / length) * estimated_duration <= elapsed:
                marker = pending.pop(0)
                if on_marker is not None:
                    fired.append(asyncio.create_task(on_marker(marker)))
            await asyncio.sleep(step)
            elapsed += step
        else:
            #* Finished naturally: fire whatever was anchored to the sentence end.
            for marker in pending:
                if on_marker is not None:
                    fired.append(asyncio.create_task(on_marker(marker)))
        events.emit(events.SPEECH, {
            "text": sentence,
            "event": "cutoff" if cut_off else "done",
            "engine": "console",
        })
        if fired:
            await asyncio.gather(*fired, return_exceptions=True)

    async def stop_speaking(self) -> None:
        self._speech_stopped.set()

    async def nod(self) -> None:
        print("[action][sim] nod")
        events.emit(events.ACTION, {"name": "nod"})
        await asyncio.sleep(0.3)

    async def look_left(self) -> None:
        print("[action][sim] look_left")
        events.emit(events.ACTION, {"name": "look_left"})
        await asyncio.sleep(0.3)

    async def look_right(self) -> None:
        print("[action][sim] look_right")
        events.emit(events.ACTION, {"name": "look_right"})
        await asyncio.sleep(0.3)

    async def blink(self, announce: bool = True) -> None:
        if announce:
            print("[action][sim] blink")
            events.emit(events.ACTION, {"name": "blink"})
        await asyncio.sleep(0.2)

    async def wink(self) -> None:
        print("[action][sim] wink")
        events.emit(events.ACTION, {"name": "wink"})
        await asyncio.sleep(0.2)

    async def shake_head(self) -> None:
        print("[action][sim] shake_head")
        events.emit(events.ACTION, {"name": "shake_head"})
        await asyncio.sleep(0.4)

    async def set_emotion(self, emotion: str) -> None:
        print(f"[emotion][sim] {emotion}")
        events.emit(events.EMOTION, {"name": emotion})


#* Backwards-compatible name for old imports.
DemoObotController = HardwareObotController
