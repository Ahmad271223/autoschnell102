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

    private static void ImSta(Action a)
    {
        Exception? fehler = null;
        var t = new Thread(() => { try { a(); } catch (Exception ex) { fehler = ex; } });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (fehler != null) throw fehler;
    }

    [Fact]   // Wunsch Ahmad 03.10.2026: klein, immer vorne, unten links oder rechts
    public void Leiste_sitzt_unten_in_der_gewaehlten_Ecke_und_schaltet()
    {
        ImSta(() =>
        {
            var zustand = Zustand();
            using var l = new Leiste(() => zustand, () => IntPtr.Zero);
            bool gestoppt = false, gestartet = false;
            l.Stoppen += () => { gestoppt = true; zustand = Zustand(an: false); };
            l.Aktivieren += () => { gestartet = true; zustand = Zustand(); };
            l.Show();
            Application.DoEvents();
            Assert.True(l.TopMost);
            var bereich = (Screen.PrimaryScreen ?? Screen.AllScreens[0]).WorkingArea;
            var links = l.Zielpunkt();
            Assert.True(links.X - bereich.Left < 40 && bereich.Bottom - (links.Y + l.Height) < 40, links.ToString());
            l.EckeSetzen(Leiste.Rechts);
            var rechts = l.Zielpunkt();
            Assert.True(bereich.Right - (rechts.X + l.Width) < 40 && rechts.Y == links.Y, rechts.ToString());
            Assert.True(l.Width < 600 && l.Height < 70, $"klein: {l.Size}");
            zustand = Zustand(verbunden: false);
            l.Aktualisieren();
            Bild(l, "leiste-nicht-verbunden.png");
            zustand = Zustand();
            l.Aktualisieren();

            var knoepfe = Alle<Button>(l).ToDictionary(b => b.Text.Trim(), b => b);
            Bild(l, "leiste-aktiv.png");
            knoepfe["■  Stopp"].PerformClick();
            Assert.True(gestoppt);
            knoepfe = Alle<Button>(l).ToDictionary(b => b.Text.Trim(), b => b);
            Assert.True(knoepfe.ContainsKey("▶  Start"));
            Bild(l, "leiste-gestoppt.png");
            knoepfe["▶  Start"].PerformClick();
            Assert.True(gestartet);
            l.EndgueltigSchliessen();
        });
    }

    [Fact]   // Wunsch Ahmad 04.10.2026: runder Punkt in der Ecke — gedrueckt halten und ziehen verschiebt die Leiste
    public void Leiste_mit_dem_Griff_verschieben_und_zurueck_in_die_Ecke()
    {
        ImSta(() =>
        {
            var zustand = Zustand();
            using var l = new Leiste(() => zustand, () => IntPtr.Zero);
            var gemeldet = new List<Point?>();
            l.PositionGeaendert += p => gemeldet.Add(p);
            l.Show();
            Application.DoEvents();
            var ecke = l.Location;
            var bereich = (Screen.PrimaryScreen ?? Screen.AllScreens[0]).WorkingArea;
            var ziel = new Point(bereich.Left + bereich.Width / 3, bereich.Top + bereich.Height / 3);

            l.GriffRunter(new Point(ecke.X + 8, ecke.Y + 17));          // Punkt gedrueckt
            l.GriffZiehen(new Point(ziel.X + 8, ziel.Y + 17));
            l.Platzieren();                                             // Takt waehrend des Ziehens: bleibt
            Assert.Equal(ziel, l.Location);
            l.GriffLos();
            Assert.Equal(ziel, l.Location);
            Assert.Equal(ziel, gemeldet.Last());
            l.Platzieren();                                             // Takt danach: bleibt an der neuen Stelle
            Assert.Equal(ziel, l.Location);

            l.PositionSetzen(new Point(-30000, -30000));                // Bildschirm weg -> zurueck in die Ecke
            Assert.Equal(ecke, l.Location);
            Assert.Null(gemeldet.Last());
            l.EndgueltigSchliessen();
        });
    }

    [Fact]   // die Leiste liegt immer oben: liegt sie ueber der Tabelle, wird nicht vom Bildschirm gelesen
    public void Leiste_ueber_der_Tabelle_wird_erkannt()
    {
        ImSta(() =>
        {
            using var tabelle = new Form { StartPosition = FormStartPosition.Manual, Location = new Point(-5000, -5000),
                                           Size = new Size(400, 300), ShowInTaskbar = false };
            using var leiste = new Form { StartPosition = FormStartPosition.Manual, Location = new Point(-4900, -4800),
                                          Size = new Size(200, 40), ShowInTaskbar = false };
            tabelle.Show();
            leiste.Show();
            var alt = AutoPointerFenster.EigeneFenster;
            try
            {
                AutoPointerFenster.EigeneFenster = () => new[] { leiste.Handle };
                Assert.True(AutoPointerFenster.Verdeckt(tabelle.Handle));
                leiste.Location = new Point(-2000, -2000);
                Assert.False(AutoPointerFenster.Verdeckt(tabelle.Handle));
                AutoPointerFenster.EigeneFenster = () => new[] { IntPtr.Zero };
                Assert.False(AutoPointerFenster.Verdeckt(tabelle.Handle));
            }
            finally { AutoPointerFenster.EigeneFenster = alt; }
        });
    }

    private static IEnumerable<T> Alle<T>(Control c) where T : Control =>
        c.Controls.Cast<Control>().SelectMany(k => (k is T t ? new[] { t } : Array.Empty<T>()).Concat(Alle<T>(k)));

    /// <summary>Zum Anschauen bei der Entwicklung: AUTOSCHNELL_FENSTERBILDER=&lt;Ordner&gt;.</summary>
    private static void Bild(Form f, string name)
    {
        string? ordner = Environment.GetEnvironmentVariable("AUTOSCHNELL_FENSTERBILDER");
        if (string.IsNullOrEmpty(ordner)) return;
        if (!f.Visible)
        {
            f.StartPosition = FormStartPosition.Manual;
            f.Location = new Point(-6000, -6000);       // ausserhalb des Bildschirms
            f.ShowInTaskbar = false;
            f.Show();
        }
        Application.DoEvents();
        using var bmp = new Bitmap(f.Width, f.Height, PixelFormat.Format32bppArgb);
        f.DrawToBitmap(bmp, new Rectangle(0, 0, f.Width, f.Height));
        Directory.CreateDirectory(ordner);
        bmp.Save(Path.Combine(ordner, name));
    }
}
