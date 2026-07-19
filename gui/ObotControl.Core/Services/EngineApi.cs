using ObotControl.Core.Models;

namespace ObotControl.Core.Services;

/// <summary>
/// Typed, discoverable wrappers over <see cref="EngineClient.CallAsync"/> for every RPC
/// method in the protocol. View-models call these instead of stringly-typed method names.
/// </summary>
public sealed class EngineApi
{
    private readonly EngineClient _client;

    public EngineApi(EngineClient client) => _client = client;

    public EngineClient Client => _client;

    public async Task<ObotConfig> GetConfigAsync() =>
        (await _client.CallAsync<ObotConfig>("get_config").ConfigureAwait(false))!;

    public async Task<ObotConfig> SetConfigAsync(ObotConfig config) =>
        (await _client.CallAsync<ObotConfig>("set_config", new { config }).ConfigureAwait(false))!;

    public async Task<List<MicDevice>> ListMicsAsync() =>
        (await _client.CallAsync<MicList>("list_mics").ConfigureAwait(false))!.Mics;

    public async Task<MicTestResult> TestMicAsync(int? index, double durationS = 3.0) =>
        (await _client.CallAsync<MicTestResult>("test_mic",
            new { index, duration_s = durationS },
            timeout: TimeSpan.FromSeconds(durationS + 10)).ConfigureAwait(false))!;

    public async Task<List<string>> ListGeminiModelsAsync(string? apiKey = null) =>
        (await _client.CallAsync<ModelList>("list_gemini_models",
            apiKey is null ? null : new { api_key = apiKey }).ConfigureAwait(false))!.Models;

    public async Task<List<string>> ListOllamaModelsAsync() =>
        (await _client.CallAsync<ModelList>("list_ollama_models",
            timeout: TimeSpan.FromSeconds(30)).ConfigureAwait(false))!.Models;

    public async Task<VoiceList> ListTtsVoicesAsync() =>
        (await _client.CallAsync<VoiceList>("list_tts_voices").ConfigureAwait(false))!;

    public async Task<string> SpeakTestAsync(string engine, string voice, string text) =>
        (await _client.CallAsync<SpeakTestResult>("speak_test",
            new { engine, voice, text },
            timeout: TimeSpan.FromSeconds(60)).ConfigureAwait(false))!.Engine;

    public async Task<SessionState> StartSessionAsync(string backend, string model, string controller) =>
        (await _client.CallAsync<SessionState>("session_start",
            new { backend, model, controller },
            timeout: TimeSpan.FromSeconds(60)).ConfigureAwait(false))!;

    public async Task<SessionState> StopSessionAsync() =>
        (await _client.CallAsync<SessionState>("session_stop").ConfigureAwait(false))!;

    public Task SendTextAsync(string text) => _client.CallAsync("send_text", new { text });

    public Task InterruptAsync() => _client.CallAsync("interrupt");

    public Task SetMicModeAsync(string mode) => _client.CallAsync("set_mic_mode", new { mode });

    public Task TriggerPttAsync() => _client.CallAsync("trigger_ptt");

    /// <summary>Hold one joint (by name, e.g. "HeadNod") at an absolute position (0..10).</summary>
    public Task SetJointAsync(string joint, double position) =>
        _client.CallAsync("set_joint", new { joint, position });

    /// <summary>Release a single manually-held joint back to ambient/automatic control.</summary>
    public Task ReleaseJointAsync(string joint) => _client.CallAsync("release_joint", new { joint });

    /// <summary>Release every manually-held joint at once.</summary>
    public Task ReleaseAllJointsAsync() => _client.CallAsync("release_all_joints");

    /// <summary>Names of every emotion with a default pose (e.g. "Happy", "Sad"), Neutral first.</summary>
    public async Task<List<string>> ListEmotionsAsync() =>
        (await _client.CallAsync<EmotionList>("list_emotions").ConfigureAwait(false))!.Emotions;

    /// <summary>Apply an emotion's default mouth/eyes/nod pose  the same path the LLM's
    /// (Emotion) markers use. Persists until the next call. Needs an active session.</summary>
    public Task SetEmotionAsync(string emotion) => _client.CallAsync("set_emotion", new { emotion });

    public async Task<SessionState> GetStateAsync() =>
        (await _client.CallAsync<SessionState>("get_state").ConfigureAwait(false))!;
}
