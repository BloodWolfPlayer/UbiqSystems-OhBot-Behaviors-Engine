# OhBot Control: desktop GUI

A cross-platform desktop GUI for the whole OhBot workflow (setup, config, live
conversation), talking to the Python engine over the local WebSocket control server
(`python -m obot --serve`). See [../docs/gui-plan.md](../docs/gui-plan.md) for the design.

All the intelligence stays in Python. The GUI never re-implements the pipeline, it just
calls RPC methods and renders the events pushed back. It's built with **Avalonia**, so one
codebase runs natively on **Windows, Linux and the Raspberry Pi**.

| Project | What |
|---|---|
| `ObotControl.Core` | UI-framework-free brain: protocol client (`EngineClient`), typed API (`EngineApi`), config/event DTOs, engine-process launcher, shared help text, and all MVVM view-models (CommunityToolkit.Mvvm). |
| `ObotControl.App` | The Avalonia desktop app, thin XAML views over Core. |
| `ObotControl.Core.Tests` | xunit: protocol/config round-trips + a live end-to-end test that drives the real engine. |

## Pages

- **Dashboard**: pick backend/model/controller, Start, type or talk, big **Interrupt**
  button, mic-mode toggle, live state indicator and active-TTS badge.
- **Setup**: step 0 provisions the Python environment itself (see below), then API key,
  COM port, remote-Ollama SSH, microphone (with a live level test), STT engine, and TTS
  voices with per-engine **Test** buttons. Save/Reload.
- **Configuration**: TTS engine + per-engine voice settings, **mouth-tuning sliders that
  apply live while the robot talks**, servo motion limits, ambient behaviors. Save/Revert/Reload.
- **Manual control**: jog each of the seven motors with a slider and watch its live
  position, for testing/calibrating servos outside a conversation. **Enable** freezes every
  joint at its current pose (no jump), then dragging a slider overrides that motor in the
  engine's mixer while ambient behaviors keep running on the rest. **Release** hands
  everything back to automatic control. Needs a running session (any backend/controller).
- **Logs**: engine stdout + structured log/error events, with level filters.

The **face preview is docked on the right and visible on every page**. It draws the robot
with a pseudo-3D look — the head yaws and nods with parallax and shading, the eyes are
glossy spheres under sliding lids, the mouth is two brushed-metal lip plates — all driven
live from the engine's joint stream, so it moves exactly as the servos would.
A **? Help** button (and hover tooltips on every control) explains what everything does.

## Prerequisites

- **.NET 10 SDK** (`dotnet --version` ≥ 10).
- **Windows**: nothing else. The Setup tab's "0. Python environment" step provisions
  Python itself (see below). **Linux/Pi**: still follow the manual setup in the repo
  [README.md](../README.md) (`pip install -e .` + `requirements/linux.txt`/`pi.txt`);
  automatic provisioning is Windows-only for now.
- No extra native tooling needed. Avalonia restores from NuGet and builds with plain `dotnet`.

## Build & run

```bash
cd gui
dotnet test ObotControl.Core.Tests/ObotControl.Core.Tests.csproj   # 27 tests, incl. a live engine round-trip
dotnet run  --project ObotControl.App/ObotControl.App.csproj        # launch the GUI
# whole solution: dotnet build ObotControl.slnx
```

## Using it

1. First run: the **Setup** tab opens automatically with **"0. Python environment"** at
   the top. It prefers whatever's already on your machine (an existing `OhBots`/`.venv`
   venv, or a system Python 3.12) and only downloads/installs a private Python if nothing
   usable is found. No admin prompt, no terminal, and it doesn't touch PATH or any
   existing install. Once it reports Ready, **Launch engine** (spawns
   `python -m obot --serve` from the repo root and connects) lights up, or you can
   **Attach** to a server you started yourself (host/port fields).

   If something's already listening on that port (say, an engine left running from an
   earlier session), Launch engine just connects to it instead of failing. You'll see a
   note about it in the Logs tab rather than an error.
2. **Setup**: Refresh devices, pick your **microphone** (it's saved the moment you pick it,
   so the next session uses it), Test it, pick TTS voices (Test buttons), Save. Settings are
   the same `config.json` the terminal app uses, so the two stay in sync.
3. **Configuration**: drag the mouth-tuning sliders while the robot talks (applies live).
4. **Dashboard**: pick backend/model/controller, **Start**, then talk or type.

### Talking to it (microphone)

Start a session, then choose a **mic mode**:

- **vad**: open mic, just talk. It answers, and talking over the bot interrupts it. Your
  words show up live under the transcript (word-by-word with the offline **Vosk** STT engine;
  a "🎤 listening…" indicator + final text with Google STT).
- **ptt**: push-to-talk. The mic stays parked until you click **🎤 Talk** or press the
  **hotkey** (default Space; click *Set…* to rebind). One press captures one phrase. Typing in
  the message box never triggers the hotkey.
- **muted**: mic off.

When you open the mic, the **Logs** tab shows `microphone open on '<device>', listening`,
and any speech-to-text failure is logged there too, so if nothing is recognised you can
see which device opened and why.

The `virtual` controller runs the full motor mixer and real TTS with no window (the GUI
draws the face); `sim` opens the engine-side tkinter window; `console` is motion/audio-free.
