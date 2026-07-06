using System.Text.Json.Serialization;

namespace ObotControl.Core.Models;

public sealed record MicDevice
{
    public int Index { get; init; }
    public string Name { get; init; } = "";
    public override string ToString() => Name;
}

public sealed record MicList
{
    public List<MicDevice> Mics { get; init; } = new();
}

public sealed record VoiceList
{
    public List<string> Gemini { get; init; } = new();
    public List<string> Piper { get; init; } = new();
    public List<string> Local { get; init; } = new();
    public List<string> Edge { get; init; } = new();
    public List<string> Kokoro { get; init; } = new();
    public List<string> Gtts { get; init; } = new();
}

public sealed record ModelList
{
    public List<string> Models { get; init; } = new();
}

public sealed record SpeakTestResult
{
    public string Engine { get; init; } = "";
}

public sealed record MicTestResult
{
    public double Peak { get; init; }
    public bool Ok { get; init; }
}

/// <summary>Mirror of the engine's get_state / session_start payload.</summary>
public sealed record SessionState
{
    public bool Session { get; init; }
    public string? Backend { get; init; }
    public string? Model { get; init; }
    public string? Controller { get; init; }
    public string State { get; init; } = "idle";

    [JsonPropertyName("mic_mode")] public string MicMode { get; init; } = "muted";
    [JsonPropertyName("mic_available")] public bool MicAvailable { get; init; }
    [JsonPropertyName("tts_engine_active")] public string? TtsEngineActive { get; init; }
}
