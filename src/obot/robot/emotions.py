"""Per-emotion default motor poses.

While an emotion is active, the mixer (:meth:`AnimatedObotController._mixer_loop`
in ``controller.py``) adds these deltas to :data:`joints.REST_POSITION` every
tick instead of settling back to plain neutral — see the comment above the
``self._emotion_pose`` blend in ``_mixer_loop``. Ambient behaviors (blinks, sway,
idle wander) and live speech visemes are computed exactly as before and simply
ride on top of this shifted baseline, so e.g. a sad face still blinks and talks
normally, just around a lower mouth / downward gaze instead of dead-center rest.

Each pose is a servo-position delta (same 0..10 units as everything else, 0 =
no change from rest) keyed by joint id. Every value below is tagged with a
``#!<Emotion>Default`` comment marking exactly what to nudge when fine-tuning a
pose against real hardware or the sim face.

:data:`NEUTRAL` is special, tagged ``#!Default``: it is not just "the pose
selected while idle", it is a standing override of what "rest" actually means
per joint, applied underneath *every* emotion (including itself) via
:func:`combined_pose`. That is the knob to turn if a joint's true physical rest
isn't :data:`joints.REST_POSITION` (e.g. hardware where LidBlink rests fully
open around 10, not 5) or the robot's neutral stance should just sit somewhere
else -- edit it once here and every emotion's pose shifts with it.
"""

from __future__ import annotations

from . import joints

#* joint_id -> delta from REST_POSITION.
EmotionPose = dict[int, float]

NEUTRAL: EmotionPose = {
    joints.TOPLIP: 0.0,
    joints.BOTTOMLIP: 0.0,
    joints.EYETILT: 0.0,
    joints.HEADNOD: 0.0,
}  #!Default -- no bias; the plain rest pose (every joint at REST_POSITION).

HAPPY: EmotionPose = {
    joints.TOPLIP: -3.0,     #!HappyDefault -- mouth corners raised into a smile
    joints.BOTTOMLIP: 3.0,  #!HappyDefault
    joints.EYETILT: 1.5,    #!HappyDefault -- eyes tilted up, bright/alert look
    joints.HEADNOD: 0.5,    #!HappyDefault -- head held slightly up
}

SAD: EmotionPose = {
    joints.TOPLIP: 2.0,    #!SadDefault -- mouth corners pulled down into a frown
    joints.BOTTOMLIP: -2.0,  #!SadDefault
    joints.EYETILT: -2.5,   #!SadDefault -- eyes looking downward
    joints.HEADNOD: -1.5,   #!SadDefault -- head hangs low
}

CONFUSED: EmotionPose = {
    joints.HEADTURN: 1.5,   #!ConfusedDefault -- head cocked to one side
    joints.EYETILT: 0.5,    #!ConfusedDefault -- eyes tilted slightly up, questioning
    joints.TOPLIP: -1.0,    #!ConfusedDefault -- uncertain, slightly pursed mouth
}

ANGRY: EmotionPose = {
    joints.TOPLIP: -2.0,    #!AngryDefault -- tight, downturned mouth
    joints.BOTTOMLIP: 1.0,  #!AngryDefault
    joints.EYETILT: -1.0,   #!AngryDefault -- lowered, browed gaze
    joints.HEADNOD: -1.0,   #!AngryDefault -- head lowered, confrontational
}

EXHAUSTED: EmotionPose = {
    joints.LIDBLINK: -4.0,   #!ExhaustedDefault -- heavy, half-closed eyelids
    joints.HEADNOD: -2.0,    #!ExhaustedDefault -- head drooping
    joints.TOPLIP: -1.5,     #!ExhaustedDefault -- slack, tired mouth
    joints.BOTTOMLIP: -1.5,  #!ExhaustedDefault
}

WHISPERING: EmotionPose = {
    joints.TOPLIP: -0.5,     #!WhisperingDefault -- mouth barely open
    joints.BOTTOMLIP: -0.5,  #!WhisperingDefault
}

SHOUTING: EmotionPose = {
    joints.TOPLIP: 4.0,      #!ShoutingDefault -- mouth wide open
    joints.BOTTOMLIP: 4.0,   #!ShoutingDefault
    joints.HEADNOD: 1.0,     #!ShoutingDefault -- head thrown back, assertive
    joints.LIDBLINK: 2.0,    #!ShoutingDefault -- eyes wide open, alert
}

#* Name -> pose, in the same order as the "Available Emotions" list in
#* system_prompt.txt (Neutral first, as the GUI's "clear emotion" option). Both the
#* LLM's (Emotion) markers and the GUI's manual control panel key into this by name.
EMOTIONS: dict[str, EmotionPose] = {
    "Neutral": NEUTRAL,
    "Happy": HAPPY,
    "Sad": SAD,
    "Confused": CONFUSED,
    "Angry": ANGRY,
    "Exhausted": EXHAUSTED,
    "Whispering": WHISPERING,
    "Shouting": SHOUTING,
}


def resolve(name: str) -> EmotionPose | None:
    """Look up a pose by name (case-insensitive). None for an unrecognised name --
    callers still surface the raw name (e.g. an `emotion` event/transcript chip) even
    when it doesn't map to a pose, since the LLM's persona can name any emotion word.
    """
    if name in EMOTIONS:
        return EMOTIONS[name]
    lowered = name.lower()
    for key, pose in EMOTIONS.items():
        if key.lower() == lowered:
            return pose
    return None


def combined_pose(name: str) -> EmotionPose | None:
    """The pose actually applied to the mixer for ``name``: :data:`NEUTRAL`'s
    rest-position override plus that emotion's own expression on top, so
    recalibrating NEUTRAL shifts every emotion's baseline, not just the plain
    "Neutral" state. None for an unrecognised name (see :func:`resolve`).
    """
    pose = resolve(name)
    if pose is None:
        return None
    if pose is NEUTRAL:
        return NEUTRAL
    merged = dict(NEUTRAL)
    for joint_id, delta in pose.items():
        merged[joint_id] = merged.get(joint_id, 0.0) + delta
    return merged
