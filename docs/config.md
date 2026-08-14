# Configuration Reference

Copy `config.example.json` to `config.json` and fill in the keys you need. `config.json` is gitignored.

## Fields

| Field | Meaning |
|---|---|
| `gemini_api_key` | API key from Google AI Studio. Only needed for the Gemini backend. |
| `ollama_ssh.host` | Hostname or IP of the SSH server running Ollama. |
| `ollama_ssh.port` | SSH port, default `22`. |
| `ollama_ssh.user` | SSH username. |
| `ollama_ssh.key_path` | Path to the private key file (OpenSSH format). |
| `ollama_ssh.remote_ollama_host` | Where Ollama listens on the remote side. Usually `localhost`. |
| `ollama_ssh.remote_ollama_port` | Remote Ollama port, default `11434`. |
| `audio.input_device_index` | Saved microphone device index. Maintained automatically by the mic picker. |
| `audio.stt_engine` | Last STT engine picked: `vosk` or `google`. Maintained automatically. |
| `audio.vosk_model_path` | Path to an unzipped Vosk model directory. Required only for offline STT. |
| `recent_gemini_models` | MRU list of Gemini models (last 3). Maintained automatically. |
| `recent_ollama_models` | Same idea for Ollama. Maintained automatically. |
| `speech.tts.engine` | `"auto"` (edge → kokoro → piper → local), or pin one: `"edge"`, `"kokoro"`, `"gtts"`, `"gemini"`, `"piper"`, `"local"`. |
| `speech.tts.edge.*` | Edge neural voice: `voice` (e.g. `en-GB-SoniaNeural`), `rate`/`volume`/`pitch` prosody strings. |
| `speech.tts.kokoro.*` | Offline neural voice: `voice` (e.g. `bf_emma`), `speed`, `lang`; model auto-downloads. |
| `speech.tts.gtts.*` | Google Translate TTS: `lang` (`en`), `tld` accent (`co.uk`), `slow`. |
| `speech.tts.gemini.*` | TTS model, prebuilt voice name (`Kore`, `Puck`, `Leda`, ...), optional `style` instruction. |
| `speech.tts.piper.*` | Offline neural voice: `voice` name (auto-downloaded), or explicit `model_path`; `length_scale` = pace. |
| `speech.tts.local.*` | Basic voice substring (`zira`), speaking rate (wpm), volume. |
| `speech.gesture.enabled` | `true` to drive head/eyes/lids/lips from the BEAT2-trained audio model; `false` uses the scripted mouth track only. |
| `speech.gesture.checkpoint_path` | Path to a `train.py` checkpoint, e.g. `mlBehaviour/runs/my_experiment/best_model.pt`. |
| `speech.gesture.control_hz` | Must match the `--control-hz` used during training (default `20.0`). |
| `speech.gesture.device` | torch device: `"cpu"` or `"cuda"`. |
| `speech.gesture.intensity` | Scales predicted movement around rest: `1.0` = model output, `>1` exaggerates, `<1` dampens. |
| `speech.gesture.scripted_mouth` | `true` = ML model drives head/eyes/lids but lips use the scripted RMS-envelope mouth track. |
| `speech.mouth.*` | Lip-sync tuning: `gate`, `gamma`, `attack`, `release`, `top_gain`, `bottom_gain`, `fps`, `sync_offset_s`. Tune live in the simulator. |
| `speech.output_device_index` | sounddevice output device for TTS playback. `null` = default speakers. |
| `motion.*` | Servo mixer: tick rate, slew-rate limits (head vs lips), write threshold. |
| `behaviors.*` | Ambient behavior modules (see [speech docs](speech.md)). |

All of `speech`, `motion` and `behaviors` are optional; missing keys use built-in defaults.
