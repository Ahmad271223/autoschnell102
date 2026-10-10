using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace AutoPointerVergleich;

/// <summary>Symbol im Infobereich: Lupe auf farbigem Grund.
/// Gruen = aktiv, grau = Automatik aus, orange = AutoPointer nicht gefunden,
/// rot = nicht verbunden bzw. gesperrt (Abo/Freigabe).</summary>
internal static class Symbole
{
    public static readonly Color Aktiv = Color.FromArgb(22, 163, 74);
    public static readonly Color Pause = Color.FromArgb(120, 120, 120);
    public static readonly Color Warten = Color.FromArgb(217, 119, 6);
    /// <summary>Nicht verbunden / kein Abo / nicht freigeschaltet.</summary>
    public static readonly Color Fehler = Color.FromArgb(220, 38, 38);

    public static Bitmap Zeichne(int groesse, Color farbe)
    {
        var bmp = new Bitmap(groesse, groesse, PixelFormat.Format32bppArgb);
        using var g = Graphics.FromImage(bmp);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.Clear(Color.Transparent);
        float s = groesse;
        float r = s * 0.22f;
        using (var pfad = new GraphicsPath())
        {
            pfad.AddArc(0, 0, r * 2, r * 2, 180, 90);
            pfad.AddArc(s - r * 2 - 1, 0, r * 2, r * 2, 270, 90);
            pfad.AddArc(s - r * 2 - 1, s - r * 2 - 1, r * 2, r * 2, 0, 90);
            pfad.AddArc(0, s - r * 2 - 1, r * 2, r * 2, 90, 90);
            pfad.CloseFigure();
            using var pinsel = new SolidBrush(farbe);
            g.FillPath(pinsel, pfad);
        }
        float strich = Math.Max(1.6f, s * 0.12f);
        using var stift = new Pen(Color.White, strich) { StartCap = LineCap.Round, EndCap = LineCap.Round };
        float d = s * 0.42f;
        float x = s * 0.18f, y = s * 0.16f;
        g.DrawEllipse(stift, x, y, d, d);
        g.DrawLine(stift, x + d * 0.85f, y + d * 0.85f, s * 0.80f, s * 0.80f);
        return bmp;
    }

    public static Icon Icon(Color farbe)
    {
        int groesse = Math.Max(16, SystemInformation.SmallIconSize.Width);
        using var bmp = Zeichne(groesse, farbe);
        IntPtr h = bmp.GetHicon();
        try { return (Icon)System.Drawing.Icon.FromHandle(h).Clone(); }
        finally { Native.DestroyIcon(h); }
    }
}
