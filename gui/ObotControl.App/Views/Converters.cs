using System.Globalization;
using Avalonia.Data.Converters;
using Avalonia.Media;
using ObotControl.Core.Protocol;

namespace ObotControl.App.Views;

/// <summary>Small value converters for status colours (kept trivial; logic lives in Core).</summary>
public static class Converters
{
    public static readonly IValueConverter ConnectionBrush = new FuncValueConverter<bool, IBrush>(
        connected => new SolidColorBrush(connected ? Color.FromRgb(46, 160, 87) : Color.FromRgb(90, 98, 112)));

    public static readonly IValueConverter StateBrush = new FuncValueConverter<BotState, IBrush>(state => state switch
    {
        BotState.Speaking => new SolidColorBrush(Color.FromRgb(214, 132, 64)),
        BotState.Listening => new SolidColorBrush(Color.FromRgb(64, 160, 214)),
        _ => new SolidColorBrush(Color.FromRgb(90, 98, 112)),
    });

    public static readonly IValueConverter CutOffDecoration = new FuncValueConverter<bool, TextDecorationCollection?>(
        cut => cut ? TextDecorations.Strikethrough : null);

    /// <summary>Highlights the active emotion button in the manual control panel.</summary>
    public static readonly IValueConverter BoolToAccent = new FuncValueConverter<bool, IBrush>(
        active => new SolidColorBrush(active ? Color.FromRgb(47, 111, 214) : Color.FromRgb(42, 52, 64)));
}
