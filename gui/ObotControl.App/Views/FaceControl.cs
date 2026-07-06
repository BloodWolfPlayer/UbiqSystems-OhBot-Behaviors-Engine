using Avalonia;
using Avalonia.Controls;
using Avalonia.Media;
using ObotControl.Core.ViewModels;

namespace ObotControl.App.Views;

/// <summary>
/// Draws the OhBot as a recognisable machine from the live <see cref="JointPose"/>.
///
/// * The blue oval <b>base plate</b> stays put; the whole head assembly (head panel, eyes,
///   lips, neck) rides on a transform so HeadTurn <b>tilts</b> it left/right about the neck
///   and HeadNod <b>bobs/dips</b> it — the head moves, the plate does not.
/// * Each eye has a curved blue <b>eyelid</b> clipped to the eyeball, so a blink reads as a
///   lid sweeping down and any partial value (e.g. a tired half-lid) covers the eye partway.
/// * The mouth is <b>two independent silver lip plates</b> (TopLip lifts, BottomLip drops)
///   around a dark cavity, so it's obvious which mechanical part is moving.
/// Redraws whenever a joints event updates the pose.
/// </summary>
public class FaceControl : Control
{
    public static readonly StyledProperty<JointPose?> PoseProperty =
        AvaloniaProperty.Register<FaceControl, JointPose?>(nameof(Pose));

    public JointPose? Pose
    {
        get => GetValue(PoseProperty);
        set => SetValue(PoseProperty, value);
    }

    private static readonly IBrush HeadFill = new SolidColorBrush(Color.FromRgb(43, 108, 210));
    private static readonly IBrush HeadInner = new SolidColorBrush(Color.FromRgb(33, 86, 176));
    private static readonly IBrush LidFill = new SolidColorBrush(Color.FromRgb(58, 120, 220));
    private static readonly Pen HeadEdge = new(new SolidColorBrush(Color.FromRgb(120, 176, 236)), 2);
    private static readonly Pen LidCrease = new(new SolidColorBrush(Color.FromRgb(150, 96, 24)), 1);
    private static readonly IBrush EyeWhite = new SolidColorBrush(Color.FromRgb(243, 233, 206));
    private static readonly IBrush Iris = new SolidColorBrush(Color.FromRgb(216, 145, 43));
    private static readonly Pen IrisEdge = new(new SolidColorBrush(Color.FromRgb(150, 96, 24)), 1.5);
    private static readonly IBrush Pupil = new SolidColorBrush(Color.FromRgb(20, 23, 31));
    private static readonly IBrush Cavity = new SolidColorBrush(Color.FromRgb(10, 12, 16));
    private static readonly IBrush Silver = new SolidColorBrush(Color.FromRgb(199, 205, 214));
    private static readonly IBrush SilverHi = new SolidColorBrush(Color.FromRgb(232, 236, 242));
    private static readonly Pen SilverEdge = new(new SolidColorBrush(Color.FromRgb(138, 147, 163)), 1.5);
    private static readonly IBrush Servo = new SolidColorBrush(Color.FromRgb(28, 32, 42));
    private static readonly IBrush Neck = new SolidColorBrush(Color.FromRgb(36, 42, 54));
    private static readonly IBrush BaseFill = new SolidColorBrush(Color.FromRgb(30, 78, 158));

    private JointPose? _subscribed;

    protected override void OnPropertyChanged(AvaloniaPropertyChangedEventArgs change)
    {
        base.OnPropertyChanged(change);
        if (change.Property != PoseProperty) return;
        if (_subscribed is not null) _subscribed.Changed -= OnPoseChanged;
        _subscribed = Pose;
        if (_subscribed is not null) _subscribed.Changed += OnPoseChanged;
        InvalidateVisual();
    }

    private void OnPoseChanged(object? sender, EventArgs e) => InvalidateVisual();

    private static double D(double pos) => pos - 5.0; // 0..10 with 5 = rest

    public override void Render(DrawingContext ctx)
    {
        var b = Bounds;
        var bg = new LinearGradientBrush
        {
            StartPoint = new RelativePoint(0, 0, RelativeUnit.Relative),
            EndPoint = new RelativePoint(0, 1, RelativeUnit.Relative),
            GradientStops =
            {
                new GradientStop(Color.FromRgb(20, 24, 33), 0),
                new GradientStop(Color.FromRgb(11, 13, 18), 1),
            },
        };
        ctx.FillRectangle(bg, new Rect(0, 0, b.Width, b.Height));
        if (b.Width < 40 || b.Height < 40) return;

        var pose = Pose ?? new JointPose();
        double unit = Math.Min(b.Width, b.Height) * 0.92;

        // Fixed base plate (never moves).
        double baseCx = b.Width / 2;
        double baseCy = b.Height * 0.90;
        DrawBase(ctx, baseCx, baseCy, unit);

        // The head assembly pivots at the top of the base: HeadTurn tilts it, HeadNod dips it.
        double pivotY = baseCy - unit * 0.05;
        double headCy = pivotY - unit * 0.44;
        double angleRad = D(pose.HeadTurn) * 3.6 * Math.PI / 180.0;
        double dy = D(pose.HeadNod) * unit * 0.028;   // nod: dip down
        double dx = D(pose.HeadTurn) * unit * 0.010;  // slight lateral shift with the tilt

        var m = Matrix.CreateTranslation(-baseCx, -pivotY)
              * Matrix.CreateRotation(angleRad)
              * Matrix.CreateTranslation(baseCx + dx, pivotY + dy);

        using (ctx.PushTransform(m))
        {
            // Neck stem connects the head to the (fixed) plate and moves with the head.
            ctx.DrawRectangle(Neck, null, new Rect(baseCx - unit * 0.05, headCy + unit * 0.30, unit * 0.10, unit * 0.24), 3, 3);
            DrawHead(ctx, baseCx, headCy, unit);

            double eyeR = unit * 0.145;
            double eyeY = headCy - unit * 0.14;
            double eyeDX = unit * 0.185;
            DrawEye(ctx, baseCx - eyeDX, eyeY, eyeR, pose);
            DrawEye(ctx, baseCx + eyeDX, eyeY, eyeR, pose);

            DrawLips(ctx, baseCx, headCy + unit * 0.235, unit, pose);
        }
    }

    private static void DrawBase(DrawingContext ctx, double cx, double cy, double unit)
    {
        ctx.DrawEllipse(BaseFill, HeadEdge, new Point(cx, cy), unit * 0.44, unit * 0.10);
        ctx.DrawRectangle(Servo, null, new Rect(cx - unit * 0.11, cy - unit * 0.12, unit * 0.22, unit * 0.12), 3, 3);
    }

    private static void DrawHead(DrawingContext ctx, double cx, double cy, double unit)
    {
        double w = unit * 0.62, h = unit * 0.74;
        ctx.DrawRectangle(HeadFill, HeadEdge, new Rect(cx - w / 2, cy - h / 2, w, h), unit * 0.16, unit * 0.16);
        double iw = w * 0.82, ih = h * 0.84;
        ctx.DrawRectangle(HeadInner, null, new Rect(cx - iw / 2, cy - ih / 2, iw, ih), unit * 0.12, unit * 0.12);
    }

    private static void DrawEye(DrawingContext ctx, double cx, double cy, double r, JointPose pose)
    {
        var eyeRect = new Rect(cx - r, cy - r, r * 2, r * 2);
        ctx.DrawEllipse(EyeWhite, null, new Point(cx, cy), r, r);

        double px = cx + D(pose.EyeTurn) * r * 0.11;
        double py = cy + D(pose.EyeTilt) * r * 0.11;
        ctx.DrawEllipse(Iris, IrisEdge, new Point(px, py), r * 0.60, r * 0.60);
        ctx.DrawEllipse(Pupil, null, new Point(px, py), r * 0.28, r * 0.28);
        ctx.DrawEllipse(SilverHi, null, new Point(px - r * 0.18, py - r * 0.18), r * 0.09, r * 0.09);

        // Eyelid: a blue disc the size of the eye, clipped to the eyeball. LidBlink 5 = open
        // (disc lifted a full eye-height above), 0 = closed (disc centred over the eye); any
        // value in between covers the eye partway (e.g. a tired half-lid).
        double openness = Math.Clamp(pose.LidBlink / 5.0, 0, 1);
        double lidCy = cy - openness * (2 * r);
        using (ctx.PushGeometryClip(new EllipseGeometry(eyeRect)))
        {
            ctx.DrawEllipse(LidFill, null, new Point(cx, lidCy), r * 1.04, r);
            // crease line along the lid's lower rim for definition
            ctx.DrawEllipse(null, LidCrease, new Point(cx, lidCy), r * 1.04, r);
        }
        ctx.DrawEllipse(null, new Pen(HeadEdge.Brush!, 1.5), new Point(cx, cy), r, r);
    }

    /// <summary>Two separate silver lip plates around a dark cavity: TopLip lifts up,
    /// BottomLip drops down (deltas above rest), so the two moving parts are unmistakable.</summary>
    private static void DrawLips(DrawingContext ctx, double cx, double my, double unit, JointPose pose)
    {
        double mouthW = unit * 0.40;
        double lipH = unit * 0.075;
        double travel = unit * 0.11;
        double restGap = unit * 0.015;

        double topOpen = Math.Max(0, D(pose.TopLip)) / 5.0 * travel;
        double botOpen = Math.Max(0, D(pose.BottomLip)) / 5.0 * travel;
        double topFrown = Math.Max(0, -D(pose.TopLip)) / 5.0 * (unit * 0.03);
        double botFrown = Math.Max(0, -D(pose.BottomLip)) / 5.0 * (unit * 0.03);

        double topBottomEdge = my - restGap / 2 - topOpen + topFrown;
        double botTopEdge = my + restGap / 2 + botOpen - botFrown;

        double cavTop = topBottomEdge - lipH * 0.2;
        double cavBot = botTopEdge + lipH * 0.2;
        ctx.DrawRectangle(Cavity, null, new Rect(cx - mouthW / 2, cavTop, mouthW, Math.Max(1, cavBot - cavTop)), lipH * 0.4, lipH * 0.4);

        DrawLipPlate(ctx, cx, topBottomEdge - lipH, mouthW, lipH, top: true);
        DrawLipPlate(ctx, cx, botTopEdge, mouthW, lipH, top: false);
    }

    private static void DrawLipPlate(DrawingContext ctx, double cx, double y, double w, double h, bool top)
    {
        double r = h * 0.5; // pill-shaped plate
        ctx.DrawRectangle(Silver, SilverEdge, new Rect(cx - w / 2, y, w, h), r, r);
        double hi = h * 0.28;
        double hiY = top ? y + h * 0.12 : y + h * 0.6;
        ctx.DrawRectangle(SilverHi, null, new Rect(cx - w * 0.44, hiY, w * 0.88, hi), hi * 0.5, hi * 0.5);
    }
}
