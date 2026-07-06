namespace ObotControl.Core;

/// <summary>
/// Centralized help strings, so the hover tooltips and the Help overlay always say the
/// same thing. Referenced from XAML via <c>{x:Static core:HelpText.Xxx}</c>.
/// </summary>
public static class HelpText
{
    // -- engine / connection bar -------------------------------------------------------
    public const string LaunchEngine =
        "Start the Python engine (python -m obot --serve) as a child process from the repo " +
        "root and connect to it. Use this if the engine isn't already running.";
    public const string Attach =
        "Connect to an engine you started yourself (or one on another machine). Set Host/Port first.";
    public const string Disconnect =
        "Disconnect from the engine and stop the engine process if this app launched it.";
    public const string Host = "Address of the control server. 127.0.0.1 for an engine on this machine.";
    public const string Port = "TCP port of the control server (default 8765).";
    public const string LaunchController =
        "Default robot target when launching the engine: 'virtual' = full motion + audio, no " +
        "window (this app draws the face); 'sim' = the engine's own face window; 'console' = no motion/audio.";
    public const string Help = "Show a guide to every page and control.";

    // -- dashboard ---------------------------------------------------------------------
    public const string Backend =
        "Where replies come from: 'scripted' echoes what you type (no key/network needed), " +
        "'gemini' uses Google's API, 'ollama' a remote Ollama over SSH.";
    public const string Model = "Which model of the selected backend to use. Click ↻ to fetch the live list.";
    public const string RefreshModels = "Fetch the available models for the selected backend.";
    public const string Controller =
        "What the session drives: 'virtual' (headless motion + audio), 'sim' (engine face window), " +
        "'console' (prints only), or 'hardware' (real servos over the COM port).";
    public const string StartSession = "Start a conversation session with the chosen backend/model/controller.";
    public const string StopSession = "End the current session.";
    public const string SwitchSession = "Restart the session to apply a new backend or model without a manual stop/start.";
    public const string MicMode =
        "Microphone: 'muted' ignores it, 'vad' listens continuously (just talk — it also " +
        "interrupts the bot), 'ptt' (push-to-talk) stays parked until you click Talk or press " +
        "the hotkey, then captures one phrase.";
    public const string PttTalk =
        "Capture one phrase now. Speak after clicking; it records until you pause. Also bound to " +
        "the hotkey shown below (works while the window is focused).";
    public const string PttHotkey =
        "Set the push-to-talk key: click Set…, then press the key you want (e.g. Space). Press it " +
        "any time (window focused) to talk. Typing in the message box is never intercepted.";
    public const string Send = "Send the typed message as one conversation turn.";
    public const string Interrupt =
        "Cut the robot off at the next word boundary — same as talking over it or pressing SPACE in the console.";
    public const string StateIndicator = "What the robot is doing right now: idle, listening, or speaking.";
    public const string ActiveEngine = "Which TTS voice actually produced the last speech (gemini / piper / local).";
    public const string FacePreview =
        "Live mirror of the robot's face — head pose, eyes, blinks and lips — from the engine's joint stream.";

    // -- setup -------------------------------------------------------------------------
    public const string RefreshDevices = "Re-query the engine host for microphones and installed TTS voices.";
    public const string ComPort = "Serial port the physical OhBot is on (e.g. COM7). Only used by the 'hardware' controller.";
    public const string ApiKey = "Google AI Studio key for the Gemini backend and Gemini TTS. Stored in config.json on the engine.";
    public const string SshFields = "Connection details for a remote machine running Ollama, reached over an SSH tunnel.";
    public const string MicPick = "Pick the microphone the robot listens through. Use Test to confirm it works.";
    public const string TestMic = "Record ~3 seconds from the selected mic; the bar shows the live level.";
    public const string SttEngine = "Speech-to-text: 'google' (online, accurate) or 'vosk' (offline, needs a model folder).";
    public const string VoskModel = "Folder of an unzipped Vosk model (contains am/, conf/, graph/). Only needed for offline STT.";
    public const string TtsEngine =
        "Which voice to speak with. 'auto' chains the best available (edge → kokoro → piper → " +
        "local), falling through on failure. Or pin one: edge (online, best), kokoro (offline, " +
        "best local), gtts/gemini (online), piper (offline), local (robotic last resort).";
    public const string TestVoice = "Speak the test sentence with this engine/voice on the engine host so you can hear it.";
    public const string Save = "Write these settings to config.json on the engine (the same file the terminal app reads).";
    public const string Reload = "Discard edits and reload the settings currently saved on the engine.";

    // -- manual control ------------------------------------------------------------------
    public const string EmotionsPanel =
        "Trigger an emotion's default pose directly — the same mouth/eyes/nod bias the LLM's " +
        "(Emotion) tags apply. It persists (ambient behaviors and speech still layer on top) " +
        "until you pick another; 'Neutral' clears it back to plain rest. Needs a running session.";
    public const string EnableManualControl =
        "Freeze every motor at its current pose and hand the sliders control. Ambient " +
        "behaviors/speech no longer move a joint once you drag its slider. Needs a running session.";
    public const string ReleaseManualControl =
        "Hand every motor back to automatic control (ambient behaviors, lip-sync, actions).";
    public const string CenterAllJoints = "Send every slider back to 5 (rest/neutral) while manual control is active.";
    public const string ManualControlPanel =
        "One row per motor: 'now' is the live position reported by the engine, the slider is " +
        "the position you're commanding, and Center resets that one motor to rest.";

    // -- configuration -----------------------------------------------------------------
    public const string MouthTuning =
        "Lip-sync tuning. These apply LIVE while the robot talks (saved after ~¼ s) so you can dial it in by ear.";
    public const string Motion = "Servo mixer: how fast joints move and how often positions are written.";
    public const string Behaviors =
        "Ambient life: blinking, attentive nods while you talk, sway while speaking, idle eye wander. " +
        "Toggle each and set how often / how strongly it fires.";
    public const string DirtyFlag = "You have unsaved changes on this page.";

    // -- pages (overview, used by the Help overlay) ------------------------------------
    public const string PageDashboard =
        "Run a conversation: choose the backend/model/controller, Start, then type or talk. " +
        "Watch the transcript, hit the big Interrupt button, switch the mic mode, and see the live state and face.";
    public const string PageSetup =
        "First-run setup: API key, COM port, remote-Ollama SSH, microphone (with a level test), " +
        "speech-to-text engine, and TTS voices with per-engine Test buttons. Save writes config.json.";
    public const string PageConfiguration =
        "Fine-tuning: TTS engine + per-engine voice settings, mouth-tuning sliders that apply live while " +
        "talking, servo motion limits, and the ambient behavior modules. Save/Revert with a dirty indicator.";
    public const string PageManualControl =
        "Jog each motor directly and watch its live position — for testing/calibrating servos outside " +
        "a conversation. Enable to freeze every joint where it is, drag sliders to move one, Release to " +
        "hand control back to ambient behaviors and speech. The Emotions row above triggers a default " +
        "mouth/eyes/nod pose directly, the same one the LLM's (Emotion) tags apply. Needs a running session.";
    public const string PageLogs =
        "Everything the engine prints plus structured log/error events, with Info/Warning/Error filters.";
    public const string Intro =
        "OhBot Control talks to the Python engine over a local WebSocket — all the intelligence stays in " +
        "Python; this app is a thin client. Start by clicking Launch engine (or Attach to one you started), " +
        "then work left-to-right through the tabs. Hover any control for a one-line explanation.";
}
