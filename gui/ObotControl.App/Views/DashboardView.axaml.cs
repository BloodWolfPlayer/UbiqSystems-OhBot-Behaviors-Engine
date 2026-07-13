using System.Collections.Specialized;
using Avalonia.Controls;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
using ObotControl.Core.ViewModels;

namespace ObotControl.App.Views;

public partial class DashboardView : UserControl
{
    private DashboardViewModel? _viewModel;

    public DashboardView()
    {
        AvaloniaXamlLoader.Load(this);
        DataContextChanged += (_, _) => HookTranscript();
    }

    // Keep the newest transcript line in view: scroll to the bottom whenever a
    // message arrives, after layout has placed it.
    private void HookTranscript()
    {
        if (_viewModel is not null)
            _viewModel.Transcript.CollectionChanged -= OnTranscriptChanged;

        _viewModel = DataContext as DashboardViewModel;
        if (_viewModel is not null)
            _viewModel.Transcript.CollectionChanged += OnTranscriptChanged;
    }

    private void OnTranscriptChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        if (e.Action != NotifyCollectionChangedAction.Add) return;
        Dispatcher.UIThread.Post(
            () => this.FindControl<ScrollViewer>("TranscriptScroll")?.ScrollToEnd(),
            DispatcherPriority.Loaded);
    }
}
