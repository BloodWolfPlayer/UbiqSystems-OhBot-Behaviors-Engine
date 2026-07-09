# OhBot Control GUI — Implementation Plan

> **Audience:** a Claude (Opus) coding agent implementing this end-to-end, plus human reviewers.
> **Repo:** `UbiqSystems-OhBot-Behaviors-Engine`. Read `README.md` first; the Python engine
> (`src/obot/`) already runs the whole robot: LLM streaming, custom TTS/speech stack
> (`src/obot/speech/`), servo motion + behaviors (`src/obot/robot/`), digital face sim
> (`src/obot/sim/`), mic input (`src/obot/audio/`). Verify anything on a machine without the
> robot via `python -m obot --sim` or `--console` (venv: `OhBots\Scripts\python.exe`).

## 1. Goal

A desktop GUI that controls the **whole** OhBot workflow, replacing the current console
pickers and hand-editing of `config.json`:

- **Setup**: first-run experience — pick microphone (with live level test), STT engine,
  TTS voices (with "speak a test sentence" button), COM port, API keys.
- **Config**: every tunable in `config.json` (`speech.*`, `motion.*`, `behaviors.*`,
  `audio.*`, `ohbot_port`, keys) editable with sensible controls (sliders for mouth
  tuning, toggles for behavior modules), saved atomically.
- **Session control**: quick-switch LLM service (Gemini ⇄ remote Ollama ⇄ scripted) and
  model *without restarting*, start/stop a conversation session, typed input, live
  transcript, big INTERRUPT button, state indicator (idle / listening / speaking).
- Nice-to-have: live face preview (joint positions streamed from the engine), log pane.

## 2. Architecture decision (read this before writing any code)

**All intelligence stays in Python.** The GUI is a thin client talking to a small
**control server inside the Python engine** over a local WebSocket (JSON-RPC-ish).
Rationale: the engine already owns asyncio pipelines, serial, audio devices and COM
objects; duplicating any of that in .NET would be madness, and a socket boundary means
the GUI framework is swappable and the engine can later run on the Pi with the GUI
connecting over LAN.

> **UPDATE (as built):** shipped on **Avalonia** (`gui/ObotControl.App`), not WinUI 3. A WinUI
> app was built first but dropped to avoid maintaining two front-ends — Avalonia gives the same
> Fluent look, one codebase for Windows + Linux + Pi, and builds with plain `dotnet`. The Core
> split below is exactly what made the swap a one-project change. Original decision kept for context:

**GUI framework: WinUI 3 (Windows App SDK) on .NET 10** — per Sebastian's preference;
nicest native Fluent look. Accepted trade-off: Windows-only (fine — the GUI runs on the
operator's laptop, the robot/Pi only runs the Python engine).
*Considered alternatives:* Avalonia (near-identical Fluent look, would also run on
Linux/Pi — pick this instead only if a robot-side touchscreen UI ever becomes a
requirement); local web dashboard (no native look, but zero-install — rejected for now).
Because of the socket boundary, switching later is cheap.

```
┌────────────────────────┐   WebSocket (127.0.0.1:8765, JSON)   ┌──────────────────────────┐
│  ObotControl (WinUI 3) │ ───────────────────────────────────► │  python -m obot --serve  │
│  MVVM, thin client     │ ◄─────────────────────────────────── │  src/obot/server/        │
└────────────────────────┘        calls ↑ / events ↓            │  wraps existing pipeline │
                                                                └──────────────────────────┘
```

## 3. Phase 0 — Python control server (`src/obot/server/`) — do this FIRST

New package `src/obot/server/` + CLI flag `python -m obot --serve [--port 8765] [--sim|--console]`.
Use the `websockets` library (add `websockets>=12` to `requirements/windows.txt` — it is
already an indirect dep of the venv, pin it explicitly).

### 3.1 Required engine refactors (small, surgical)

1. **Pickers → API.** `__main__._voice_session`, `audio/input.py` pickers and
   `llm/picker.py` are `input()`-driven. Extract the *data* parts so the server can call
   them: `list_input_devices()` (exists), `test_input_device()` (refactor: return a
   stream of level values + a result instead of printing), `list_gemini_models()` /
   Ollama tags (exist). **Do not break the console flow** — keep the pickers working.
2. **Event bus.** The engine `print()`s everything. Add a tiny observer hook
   (`src/obot/core/events.py`: `subscribe(topic, cb)` / `emit(topic, data)`) and emit
   from the key places: sentence spoken / cut off, action & emotion fired, TTS engine
   used or benched, state changes (idle/listening/speaking), joint positions (throttled
   ~15 Hz from the motor mixer), user transcript, errors. Console printing stays.
3. **Session manager.** A `ServerSession` class that owns what `_voice_session` does
   today (controller + pipeline + interrupt + behaviors) but is driven by RPC instead of
   the keyboard: `start(backend, model)`, `stop()`, `send_text(text)`, `interrupt()`,
   `set_mic_mode(vad|ptt|muted)`. Mic capture (`AudioInput`) keeps working exactly as in
   the console app; the GUI just flips modes.

### 3.2 Protocol (keep it this simple)

Requests `{ "type":"call", "id":1, "method":"...", "params":{...} }` →
`{ "type":"result", "id":1, "ok":true, "data":{...} }` (or `"ok":false, "error":"msg"`).
Events (server-push): `{ "type":"event", "topic":"state|transcript|speech|joints|log|miclevel", "data":{...} }`.

| Method | Params → Data |
|---|---|
| `get_config` / `set_config` | full JSON ⇄ validated + atomically saved; hot-applies `speech.mouth`, `behaviors`, `motion` where the engine reads them live |
| `list_mics` / `test_mic` | — → `[{index,name}]`; `{index}` → emits `miclevel` events for ~3 s then result |
| `list_gemini_models` / `list_ollama_models` | key/tunnel from config |
| `list_tts_voices` | → gemini prebuilt list (static), piper voices (`piper.download_voices.list_voices`), SAPI voices |
| `speak_test` | `{engine, voice, text}` → synthesizes + plays on the engine host |
| `session_start` / `session_stop` | `{backend:"gemini"\|"ollama"\|"scripted", model, controller:"hardware"\|"sim"\|"console"}` |
| `send_text` | `{text}` — one conversation turn (works alongside the mic) |
| `interrupt` | — (same path as SPACE/barge-in: word-boundary stop) |
| `set_mic_mode` | `{mode:"vad"\|"ptt"\|"muted"}` |
| `set_joint` / `release_joint` / `release_all_joints` | `{joint:"HeadNod"\|...\|id, position:0..10}` → hold/release one joint's absolute position for manual GUI control (a motor test panel); a held joint overrides ambient behaviors/speech until released. Needs an active session. |
| `list_emotions` | — → `{emotions:["Neutral","Happy",...]}` (no session needed; static table in `robot/emotions.py`) |
| `set_emotion` | `{emotion:"Sad"\|...}` → applies that emotion's default mouth/eyes/nod pose, persisting until the next `set_emotion` call (same path the LLM's `(Emotion)` markers use). Needs an active session. |
| `get_state` | → `{session, backend, model, state, tts_engine_active, emotion}` |

Auth: none, bind 127.0.0.1 only. (LAN/Pi support later: `--host 0.0.0.0` + token param — out of scope now.)

### 3.3 Acceptance for Phase 0
`python -m obot --serve --sim` + a 30-line Python websocket test script (put it in
`tests/`) that: reads config, lists mics/models, starts a scripted-backend session,
sends a text turn, receives `transcript`/`state`/`joints` events, interrupts mid-speech,
stops. Must run green on a machine with no robot.

## 4. Phase 1 — .NET solution scaffold

- Location: `gui/ObotControl/` in this repo (`ObotControl.sln`); extend `.gitignore`
  with `gui/**/bin/`, `gui/**/obj/`.
- Stack: .NET 10, WinUI 3 (Windows App SDK, latest stable), **CommunityToolkit.Mvvm**
  (source-generated `[ObservableProperty]`/`[RelayCommand]`), `System.Net.WebSockets`,
  `System.Text.Json`. No other heavyweight deps.
- Projects: `ObotControl` (app) + `ObotControl.Core` (protocol client + models,
  UI-framework-free so it survives a framework swap) + `ObotControl.Core.Tests` (xunit:
  protocol serialization round-trips against captured JSON fixtures from Phase 0).
- `EngineClient` service: connect/reconnect loop, `Task<T> CallAsync(method, params)`,
  `IObservable`-style event subscription, connection-state exposed to the UI.
- **Engine lifecycle:** "Launch engine" button = spawn
  `<repo>\OhBots\Scripts\python.exe -m obot --serve --sim|--hardware` as a child process
  (working dir = repo root — the engine requires it), capture stdout into the log pane,
  kill on app exit (Job Object or `Process.Kill(entireProcessTree:true)`). Also support
  "Attach" to an already-running server (host/port field).
  > **UPDATE (as built, Milestone 4):** `<repo>\OhBots` is no longer assumed to exist.
  > `PythonEnvironmentService` (Windows only) discovers it — or any other venv, or a
  > system Python 3.12 — and only falls back to silently installing a private Python
  > (python.org installer into `%LOCALAPPDATA%\ObotControl`, no admin/terminal) if
  > nothing usable is found; `PythonSetupViewModel` surfaces this as "0. Python
  > environment" atop the Setup page and gates "Launch engine" on it.
- Navigation shell (NavigationView): Dashboard, Setup, Configuration, Logs.

## 5. Phase 2 — Setup & Configuration pages

- **Setup (wizard-ish page):** COM port textbox + "probe" (server ping of the port),
  Gemini API key (PasswordBox, stored only in engine's config.json via `set_config`),
  Ollama SSH fields, mic dropdown + Test (live level bar from `miclevel` events +
  playback confirmation), STT engine radio (vosk/google), TTS voice pickers per engine
  with **Test voice** buttons (`speak_test`).
- **Configuration:** three expandable sections mirroring config keys 1:1 —
  *Speech* (engine mode radio; per-engine voice settings; mouth tuning **sliders** with
  live apply: call `set_config` debounced ~250 ms so you can tune while it talks — the
  engine already reads `speech.mouth` live), *Motion* (tick, rate limits), *Behaviors*
  (per-module enable toggle + interval/intensity sliders).
- Dirty-state indicator + Save/Revert; `set_config` returns the canonical config, reload
  the form from it. **The engine owns the file** — the GUI never writes config.json.

## 6. Phase 3 — Session dashboard

- Backend/model quick-switch: two ComboBoxes (service, model — populated via
  `list_*_models`), Start/Stop session, controller selector (hardware / sim / console).
- Transcript list (user turns, bot sentences as they are spoken, cut-off markers,
  action/emotion chips inline), auto-scroll.
- Text input box (send turn), **big Interrupt button**, mic mode toggle (open/PTT/muted)
  with state badge, current state indicator (idle/listening/speaking) driven by `state`
  events, active-TTS-engine badge (gemini/piper/local — from `speech` events).
- Optional (do last): face preview `Canvas` mirroring `src/obot/sim/face.py` drawing from
  `joints` events — port the ~80 lines of draw code, don't overengineer.

## 7. Phase 4 — polish (only after 0-3 are accepted)

Profiles (save/load named config presets), log pane filters, MSIX vs. unpackaged
self-contained publish (**prefer unpackaged** — MSIX signing is friction for a two-person
team), `winget`-style install script, LAN mode for the Pi-hosted engine (token auth).

## 8. Risks / gotchas (learned the hard way in this repo — do not rediscover them)

- Engine **must run from the repo root** (ohbot lib + `ohbotData/` are CWD-relative).
- Windows COM: SAPI objects live on a dedicated thread (`speech/tts.py`); never call TTS
  from arbitrary threads. The server must reuse `SpeechEngine`, not spawn its own.
- Gemini TTS free tier ≈ 3 req/min → the chain (gemini→piper→sapi) handles it; surface
  the *active engine* in the UI instead of treating a bench as an error.
- tkinter sim window: thread-owned; if the server runs `--sim`, the window lives on the
  engine side. The GUI face preview replaces it long-term (`--console` + preview).
- `config.json` is gitignored and holds secrets — never log it, never commit fixtures
  containing a real key.
- WinUI 3 + WebSockets: marshal event callbacks onto the `DispatcherQueue` before
  touching bound properties.
- Don't block the engine's event loop: `speak_test` and mic tests must run as tasks.

## 9. Milestone order & definition of done

| # | Deliverable | Done when |
|---|---|---|
| 0 | ✅ `--serve` + protocol + test script | **DONE** (branch `gui-control`). `src/obot/server/` (WebSocket server + RPC + event push), event bus `src/obot/core/events.py`, `ServerSession`, headless `VirtualObotController`, `Config.from_dict/to_dict`, index-aware mic listing + `stream_input_level`, `list_tts_voices`/`speak_test`. `tests/test_server_smoke.py` green with `--controller console` and `--controller virtual` (joints). README section added. |
| 1 | ✅ Solution + EngineClient + shell + launch/attach | **DONE**. `gui/ObotControl.slnx`: `ObotControl.Core` (EngineClient reconnect loop, `EngineApi`, DTOs, `EngineProcess` launcher, all MVVM VMs) + `ObotControl.Core.Tests` (12 xunit tests incl. a **live** end-to-end run against the real engine). |
| 2 | ✅ Setup + Configuration pages | **DONE** in both front-ends: keys/COM/SSH, mic list + live level test, STT, TTS voices + test buttons; mouth-tuning sliders with **live debounced apply**, motion, behaviors, dirty/save/revert. |
| 3 | ✅ Dashboard | **DONE**: backend/model/controller quick-switch, start/stop, typed + mic turns, big Interrupt, mic mode, state indicator, active-TTS badge, and a live **face preview** (joint stream). |
| 4 | Polish | log filters, `virtual` headless controller, help overlay + hover tooltips on every control, a persistent face preview visible on all pages, a mechanically-faithful face (two separate silver lip plates, amber eyes + blue acrylic head), a **Manual control** page (per-motor sliders + live position readouts, `set_joint`/`release_joint`) for jogging/testing the servos directly, and ✅ **GUI-managed Python setup** (Windows) — `PythonEnvironmentService`/`PythonSetupViewModel` discover an existing venv or system Python 3.12 (preferred) or silently install a private one (python.org installer, no admin/terminal) and build `OhBots` from it; "Launch engine" is gated on it, so a teammate never opens a terminal. Profiles / packaging / LAN-token still open. |

**One cross-platform GUI over the shared Core.** Consolidated to a single **Avalonia**
app (`ObotControl.App`) that runs natively on Windows, Linux and the Pi (the earlier WinUI 3
project was dropped to avoid maintaining two front-ends). All logic (protocol client +
view-models) is in `ObotControl.Core` and unit-tested, incl. a live end-to-end run against
the real engine. Builds with plain `dotnet build`. See [gui/README.md](../gui/README.md).

Work in a branch (`gui-control`), one PR per milestone. Update `README.md` (GUI quick
start) and this document as decisions land.
