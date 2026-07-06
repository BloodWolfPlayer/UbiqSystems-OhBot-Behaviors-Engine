using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ObotControl.Core.Protocol;
using ObotControl.Core.Services;

namespace ObotControl.Core.ViewModels;

/// <summary>
/// The application root: owns the engine link (client + optional child process), the
/// shared config store and every page view-model, and fans connection-state and engine
/// events out to the pages. Both GUIs new this up once and bind their shell to it.
/// </summary>
public partial class ShellViewModel : ObservableObject
{
    private Action<Action> _post = a => a();
    private EngineProcess? _engine;

    public EngineClient Client { get; }
    public EngineApi Api { get; }
    public ConfigStore Store { get; }

    public LogsViewModel Logs { get; }
    public DashboardViewModel Dashboard { get; }
    public SetupViewModel Setup { get; }
    public ConfigurationViewModel Configuration { get; }

    public string[] LaunchControllers { get; } = { "virtual", "sim", "console" };

    [ObservableProperty] private string _host = "127.0.0.1";
    [ObservableProperty] private int _port = 8765;
    [ObservableProperty] private string _launchController = "virtual";

    [ObservableProperty] private ConnectionState _connectionState = ConnectionState.Disconnected;
    [ObservableProperty] private string _connectionText = "Disconnected";
    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(CanLaunch))]
    private bool _isConnected;
    [ObservableProperty] private bool _engineOwned;
    [ObservableProperty] private string? _repoRoot;
    [ObservableProperty] private bool _showHelp;

    public bool CanLaunch => !IsConnected;

    public ShellViewModel()
    {
        Client = new EngineClient();
        Api = new EngineApi(Client);
        Store = new ConfigStore(Api);
        Logs = new LogsViewModel();
        Dashboard = new DashboardViewModel(Api, Logs);
        Setup = new SetupViewModel(Api, Store, Logs);
        Configuration = new ConfigurationViewModel(Api, Store, Logs);

        Client.StateChanged += (_, s) => HandleConnectionState(s);
        Client.EngineEventReceived += (_, e) => RouteEvent(e);

        RepoRoot = EngineProcess.LocateRepoRoot();
    }

    /// <summary>Wire the UI-thread marshaller; forwards to the client and to child-process output.</summary>
    public void UseDispatcher(Action<Action> post)
    {
        _post = post;
        Client.UseDispatcher(post);
    }

    // -- connect / launch --------------------------------------------------------------

    [RelayCommand]
    private void Attach()
    {
        Logs.Append("info", $"attaching to ws://{Host}:{Port}");
        Client.Connect(new Uri($"ws://{Host}:{Port}"));
    }

    [RelayCommand(CanExecute = nameof(CanLaunch))]
    private void LaunchEngine()
    {
        if (RepoRoot is null)
        {
            Logs.Append("error", "could not locate the engine repo (src/obot/__main__.py).");
            return;
        }
        StopEngineProcess();
        var options = new EngineLaunchOptions
        {
            RepoRoot = RepoRoot,
            PythonPath = EngineProcess.VenvPython(RepoRoot) ?? "",
            Host = Host,
            Port = Port,
            Controller = LaunchController == "virtual" ? "" : LaunchController,
        };
        var engine = new EngineProcess(options);
        engine.OutputReceived += (_, line) => _post(() => Logs.AppendRaw(line));
        engine.Exited += (_, code) => _post(() => Logs.Append("warn", $"engine exited ({code})"));
        try
        {
            engine.Start();
            _engine = engine;
            EngineOwned = true;
            Logs.Append("info", $"launched engine ({LaunchController}); connecting…");
            //* Auto-reconnect keeps retrying until the server's socket is up.
            Client.Connect(new Uri($"ws://{Host}:{Port}"));
        }
        catch (Exception ex)
        {
            Logs.Append("error", $"failed to launch engine: {ex.Message}");
            engine.Dispose();
        }
    }

    [RelayCommand]
    private async Task DisconnectAsync()
    {
        await Client.DisconnectAsync();
        StopEngineProcess();
    }

    [RelayCommand]
    private void ToggleHelp() => ShowHelp = !ShowHelp;

    [RelayCommand]
    private void CloseHelp() => ShowHelp = false;

    /// <summary>Re-fetch config + devices from the engine (discards unsaved page edits).</summary>
    [RelayCommand]
    private async Task ReloadAsync()
    {
        if (!IsConnected) return;
        await OnConnectedAsync();
    }

    private void StopEngineProcess()
    {
        _engine?.Dispose();
        _engine = null;
        EngineOwned = false;
    }

    // -- state & events ----------------------------------------------------------------

    private void HandleConnectionState(ConnectionState state)
    {
        ConnectionState = state;
        IsConnected = state == ConnectionState.Connected;
        ConnectionText = state switch
        {
            ConnectionState.Connected => $"Connected · {Host}:{Port}",
            ConnectionState.Connecting => "Connecting…",
            ConnectionState.Reconnecting => "Reconnecting…",
            ConnectionState.Faulted => "Connection error",
            _ => "Disconnected",
        };
        LaunchEngineCommand.NotifyCanExecuteChanged();

        Dashboard.OnConnectionChanged(IsConnected);
        Setup.OnConnectionChanged(IsConnected);
        Configuration.OnConnectionChanged(IsConnected);

        if (IsConnected)
        {
            _ = OnConnectedAsync();
        }
    }

    private async Task OnConnectedAsync()
    {
        try
        {
            await Store.LoadAsync();
            await Setup.RefreshDevicesCommand.ExecuteAsync(null);
            var state = await Api.GetStateAsync();
            Dashboard.ApplyState(state);
            Logs.Append("info", "connected; config + devices loaded");
        }
        catch (Exception ex)
        {
            Logs.Append("error", $"initial load failed: {ex.Message}");
        }
    }

    private void RouteEvent(EngineEvent evt)
    {
        switch (evt.Topic)
        {
            case Topics.Log:
                var log = ObotJson.Deserialize<LogEvent>(evt.Data);
                if (log is not null) Logs.Append(log.Level, log.Message);
                break;
            case Topics.Error:
                var err = ObotJson.Deserialize<ErrorEvent>(evt.Data);
                if (err is not null) Logs.Append("error", $"{err.Where}: {err.Message}");
                break;
            case Topics.MicLevel:
                Setup.HandleEvent(evt);
                break;
            default:
                Dashboard.HandleEvent(evt);
                break;
        }
    }
}
