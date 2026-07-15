using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
using ObotControl.Core.ViewModels;
using ObotControl.App.Views;

namespace ObotControl.App;

public partial class App : Application
{
    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            var shell = new ShellViewModel();
            // Marshal engine events + child-process output onto the UI thread.
            shell.UseDispatcher(action => Dispatcher.UIThread.Post(action));

            desktop.MainWindow = new MainWindow { DataContext = shell };
            desktop.ShutdownRequested += (_, _) => shell.DisconnectCommand.Execute(null);
        }

        base.OnFrameworkInitializationCompleted();
    }
}
