# GUI Control Server (`--serve`)

The GUI doesn't re-implement any of the engine. Instead the engine exposes itself over a local WebSocket and the GUI is a thin client. Start the server with:

```bash
python -m obot --serve                        # headless "virtual" controller, ws://127.0.0.1:8765
python -m obot --serve --console              # no motion/audio (fastest; good for protocol tests)
python -m obot --serve --sim                  # default controller opens the tkinter face window
python -m obot --serve --port 9000            # pick the port
python -m obot --serve --no-gesture-model     # force the AI gesture model off for every session
python -m obot --serve --scripted-mouth       # ML model for head/eyes, scripted mouth for lips
```

It binds to `127.0.0.1` only (no auth by design; LAN/Pi mode is a later milestone).
`--sim`/`--console` set the **default** controller; a `session_start` call can override
it per session (`virtual`, `sim`, `hardware`, or `console`). `virtual` runs the full
motor mixer and real TTS with no window and streams joint positions on the `joints`
event topic, so a GUI can draw the face itself. `--no-gesture-model` is applied once
at startup (there's no per-session override, and no GUI control for it yet).

## Protocol

Requests `{"type":"call","id":1,"method":"...","params":{...}}` get a
`{"type":"result","id":1,"ok":true,"data":{...}}` (or `"ok":false,"error":"..."`).
The server also pushes `{"type":"event","topic":"...","data":{...}}` for
`state` (idle/listening/speaking), `transcript`, `speech` (sentence + active TTS
engine + cut-off markers), `action`, `emotion`, `joints` (~15 Hz), `miclevel`, `log`
and `error`.

## Methods

| Method | Purpose |
|---|---|
| `get_config` / `set_config` | full JSON in/out, validated and atomically saved; hot-applies `speech.mouth`, `motion`, `behaviors` to a running session |
| `list_mics` / `test_mic` | enumerate mics; `test_mic {index}` streams `miclevel` events for ~3 s |
| `list_gemini_models` / `list_ollama_models` | live model lists (key / SSH tunnel from config) |
| `list_tts_voices` / `speak_test` | voices per engine; synthesize a test sentence on the host |
| `session_start` / `session_stop` | `{backend, model, controller}`, starts/stops a conversation |
| `send_text` / `interrupt` / `set_mic_mode` | one typed turn; word-boundary interrupt; `vad`/`ptt`/`muted` |
| `get_state` | `{session, backend, model, controller, state, mic_mode, tts_engine_active}` |

## Smoke test

No robot or API key needed, runs against a scripted backend:

```bash
python tests/test_server_smoke.py                 # controller=virtual
python tests/test_server_smoke.py --controller console
```

It spawns its own server, reads config, lists mics/voices/models, starts a session,
sends a turn, checks `transcript`/`state`/`speech`/`joints` events, interrupts
mid-speech, and stops. Exit code 0 means all checks passed.
