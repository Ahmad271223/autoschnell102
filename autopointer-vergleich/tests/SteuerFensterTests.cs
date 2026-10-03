using System.Drawing;
using System.Drawing.Imaging;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 03.10.2026: "das sollen Buttons sein" — das Steuerfenster sagt in jedem Zustand
/// klar, was los ist, und die Knoepfe passen dazu.</summary>
public class SteuerFensterTests
{
    private static FensterZustand Zustand(Status s = Status.Aktiv, bool an = true, bool verbunden = true,
                                          string? auto = "Volkswagen Beetle · EZ 06/2017 · 41.000 km") =>
        new(s, an, verbunden, "Konto 10002 · Norden Autoankauf", auto, true, null, false);

    [Fact]
    public void Jeder_Zustand_hat_eine_klare_Aussage()
    {
        Assert.Equal("AKTIV", SteuerFenster.Anzeige(Zustand()).Titel);
        Assert.Equal("GESTOPPT", SteuerFenster.Anzeige(Zustand(an: false)).Titel);
        Assert.Equal("NICHT VERBUNDEN", SteuerFenster.Anzeige(Zustand(verbunden: false)).Titel);
        Assert.Equal("NICHT VERBUNDEN", SteuerFenster.Anzeige(Zustand(an: false, verbunden: false)).Titel);
        Assert.Equal("GESPERRT", SteuerFenster.Anzeige(Zustand(Status.Gesperrt)).Titel);
        Assert.Equal("AKTIV – WARTET", SteuerFenster.Anzeige(Zustand(Status.KeinAutoPointer)).Titel);
        Assert.Equal(Symbole.Aktiv, SteuerFenster.Anzeige(Zustand()).Farbe);
        Assert.Equal(Symbole.Fehler, SteuerFenster.Anzeige(Zustand(verbunden: false)).Farbe);
    }

    [Fact]
    public void Fenster_zeigt_Knoepfe_passend_zum_Zustand()
    {
        Exception? fehler = null;
        var t = new Thread(() =>
        {
            try
            {
                var zustand = Zustand();
                using var f = new SteuerFenster(() => zustand);
                f.CreateControl();
                var knoepfe = Alle<Button>(f).ToDictionary(b => b.Text.Trim(), b => b);
                Assert.False(knoepfe["✓  Ist aktiv"].Enabled);       // laeuft schon
                Assert.True(knoepfe["■  Stoppen"].Enabled);
                Assert.True(knoepfe["Beenden"].Enabled);
                Assert.True(knoepfe["Verbindung trennen"].Enabled);
                Bild(f, "steuerfenster-aktiv.png");

                zustand = Zustand(an: false);
                f.Aktualisieren();
                knoepfe = Alle<Button>(f).ToDictionary(b => b.Text.Trim(), b => b);
                Assert.True(knoepfe["▶  Aktivieren"].Enabled);
                Assert.False(knoepfe["✓  Ist gestoppt"].Enabled);

                zustand = Zustand(verbunden: false, auto: null);
                f.Aktualisieren();
                knoepfe = Alle<Button>(f).ToDictionary(b => b.Text.Trim(), b => b);
                Assert.True(knoepfe["Mit AutoSchnell verbinden …"].Enabled);
                Assert.False(knoepfe["Aktuelles Auto jetzt vergleichen"].Enabled);
                Bild(f, "steuerfenster-nicht-verbunden.png");
            }
            catch (Exception ex) { fehler = ex; }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (fehler != null) throw fehler;
    }

    private static IEnumerable<T> Alle<T>(Control c) where T : Control =>
        c.Controls.Cast<Control>().SelectMany(k => (k is T t ? new[] { t } : Array.Empty<T>()).Concat(Alle<T>(k)));

    /// <summary>Zum Anschauen bei der Entwicklung: AUTOSCHNELL_FENSTERBILDER=&lt;Ordner&gt;.</summary>
    private static void Bild(Form f, string name)
    {
        string? ordner = Environment.GetEnvironmentVariable("AUTOSCHNELL_FENSTERBILDER");
        if (string.IsNullOrEmpty(ordner)) return;
        f.StartPosition = FormStartPosition.Manual;
        f.Location = new Point(-6000, -6000);       // ausserhalb des Bildschirms
        f.ShowInTaskbar = false;
        f.Show();
        Application.DoEvents();
        using var bmp = new Bitmap(f.Width, f.Height, PixelFormat.Format32bppArgb);
        f.DrawToBitmap(bmp, new Rectangle(0, 0, f.Width, f.Height));
        Directory.CreateDirectory(ordner);
        bmp.Save(Path.Combine(ordner, name));
    }
}
