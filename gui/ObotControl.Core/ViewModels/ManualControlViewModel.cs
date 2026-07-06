using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ObotControl.Core.Protocol;
using ObotControl.Core.Services;

namespace ObotControl.Core.ViewModels;

/// <summary>One motor's live position plus the slider target used to jog it.</summary>
public partial class ManualJointViewModel : ObservableObject
{
    public ManualJointViewModel(string name, string displayName)
    {
        Name = name;
        DisplayName = displayName;
    }

    /// <summary>Wire name, matching the engine's <c>joints</c> event keys (e.g. "HeadNod").</summary>
    public string Name { get; }
    public string DisplayName { get; }

    /// <summary>Last position reported by the engine's joint stream (0..10).</summary>
    [ObservableProperty] private double _current = 5.0;

    /// <summary>Slider value; only sent to the engine while <see cref="Editable"/>.</summary>
    [ObservableProperty] private double _target = 5.0;

    /// <summary>Whether the slider should accept input right now (manual mode active).</summary>
    [ObservableProperty] private bool _editable;

    /// <summary>Raised whenever <see cref="Target"/> changes (slider drag or a reset), so the
    /// owning view-model can push the new hold position to the engine.</summary>
    public event Action<ManualJointViewModel>? TargetChanged;

    partial void OnTargetChanged(double value) => TargetChanged?.Invoke(this);
}

/// <summary>
/// Manual motor control: jog each of the seven joints with a slider and watch its live
/// position, for testing/calibrating the servos without going through a conversation.
/// "Enable" snapshots every joint at its current pose and holds it there (no jump); each
/// slider drag then overrides that joint in the engine's motor mixer — ambient behaviors
/// and speech keep running on any joint you haven't touched. "Release" hands every joint
/// back to automatic control. Requires a running session (any backend/controller).
/// </summary>
public partial class ManualControlViewModel : ObservableObject
{
    //* Jog commands are cheap "hold this position" calls, but a slider drag can fire many
    //* per second; debounce per-joint so dragging doesn't flood the socket; short enough
    //* to feel live (close to the mixer's own ~15 Hz joint-stream rate).
    private static readonly TimeSpan JogDebounce = TimeSpan.FromMilliseconds(40);

    private static readonly (string Name, string Display)[] JointDefs =
    {
        ("HeadNod", "Head nod"),
        ("HeadTurn", "Head turn"),
        ("EyeTurn", "Eye turn"),
        ("LidBlink", "Lid / blink"),
        ("TopLip", "Top lip"),
        ("BottomLip", "Bottom lip"),
        ("EyeTilt", "Eye tilt"),
    };

    private readonly EngineApi _api;
    private readonly LogsViewModel _logs;
    private readonly Dictionary<string, CancellationTokenSource> _pendingSets = new();

    public ManualControlViewModel(EngineApi api, LogsViewModel logs)
    {
        _api = api;
        _logs = logs;
        foreach (var (name, display) in JointDefs)
        {
            var joint = new ManualJointViewModel(name, display);
            joint.TargetChanged += ScheduleSetJoint;
            Joints.Add(joint);
        }
    }

    public ObservableCollection<ManualJointViewModel> Joints { get; } = new();

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanEnable))]
    [NotifyPropertyChangedFor(nameof(CanEdit))]
    private bool _connected;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanEnable))]
    [NotifyPropertyChangedFor(nameof(CanEdit))]
    private bool _sessionActive;

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanEnable))]
    [NotifyPropertyChangedFor(nameof(CanEdit))]
    private bool _manualModeActive;

    /// <summary>True once a session is running and manual mode isn't already on.</summary>
    public bool CanEnable => Connected && SessionActive && !ManualModeActive;

    /// <summary>True while sliders should accept input and push positions to the engine.</summary>
    public bool CanEdit => Connected && SessionActive && ManualModeActive;

    partial void OnConnectedChanged(bool value) => SyncEditable();
    partial void OnSessionActiveChanged(bool value) => SyncEditable();
    partial void OnManualModeActiveChanged(bool value) => SyncEditable();

    private void SyncEditable()
    {
        foreach (var joint in Joints) joint.Editable = CanEdit;
    }

    // -- mode toggle ---------------------------------------------------------------------

    [RelayCommand]
    private async Task EnableManualModeAsync()
    {
        if (!CanEnable) return;
        //* Snapshot first (while still non-editable, so this doesn't fire jog RPCs), then
        //* flip the mode on and hold every joint exactly where it already is — no jump.
        foreach (var joint in Joints) joint.Target = joint.Current;
        ManualModeActive = true;
        try
        {
            foreach (var joint in Joints)
                await _api.SetJointAsync(joint.Name, joint.Target).ConfigureAwait(false);
            _logs.Append("info", "manual joint control enabled — behaviors overridden until released");
        }
        catch (Exception ex)
        {
            _logs.Append("error", $"enable manual control failed: {ex.Message}");
        }
    }

    [RelayCommand]
    private async Task DisableManualModeAsync()
    {
        if (!ManualModeActive) return;
        ManualModeActive = false;
        try
        {
            await _api.ReleaseAllJointsAsync();
            _logs.Append("info", "manual joint control released");
        }
        catch (Exception ex)
        {
            _logs.Append("error", $"release joints failed: {ex.Message}");
        }
    }

    [RelayCommand]
    private void CenterAll()
    {
        if (!CanEdit) return;
        foreach (var joint in Joints) joint.Target = 5.0;
    }

    [RelayCommand]
    private void ResetJoint(ManualJointViewModel? joint)
    {
        if (joint is null || !CanEdit) return;
        joint.Target = 5.0;
    }

    // -- jogging ---------------------------------------------------------------------------

    private void ScheduleSetJoint(ManualJointViewModel joint)
    {
        if (!CanEdit) return;
        if (_pendingSets.TryGetValue(joint.Name, out var existing)) existing.Cancel();
        var cts = new CancellationTokenSource();
        _pendingSets[joint.Name] = cts;
        _ = SetJointDebouncedAsync(joint, cts.Token);
    }

    private async Task SetJointDebouncedAsync(ManualJointViewModel joint, CancellationToken ct)
    {
        try
        {
            await Task.Delay(JogDebounce, ct).ConfigureAwait(false);
            if (ct.IsCancellationRequested) return;
            await _api.SetJointAsync(joint.Name, joint.Target).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            //* Superseded by a newer drag position for the same joint.
        }
        catch (Exception ex)
        {
            _logs.Append("error", $"set_joint {joint.Name} failed: {ex.Message}");
        }
    }

    // -- events / connection ---------------------------------------------------------------

    public void HandleEvent(EngineEvent evt)
    {
        if (evt.Topic != Topics.Joints) return;
        var data = ObotJson.Deserialize<JointsEvent>(evt.Data);
        if (data is null) return;
        foreach (var joint in Joints) joint.Current = data.Get(joint.Name);
    }

    /// <summary>Called by the shell whenever the dashboard's session state changes.</summary>
    public void NotifySessionActive(bool active)
    {
        SessionActive = active;
        if (!active) ManualModeActive = false; // the engine already dropped the controller
    }

    public void OnConnectionChanged(bool connected)
    {
        Connected = connected;
        if (!connected)
        {
            SessionActive = false;
            ManualModeActive = false;
        }
    }
}
