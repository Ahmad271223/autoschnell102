using System.Drawing;
using System.Drawing.Imaging;
using Xunit;
using Xunit.Abstractions;

namespace AutoPointerVergleich.Tests;

/// <summary>Nur zum Messen (laeuft nur mit der Umgebungsvariablen LISTEN_MESSUNG = "bild.png;x,y,b,h;x,y,b,h…"):
/// was liest der Schrift-Abgleich in den angegebenen Ausschnitten eines Bildschirmfotos?</summary>
public class ListenMessung
{
    private readonly ITestOutputHelper _aus;
    public ListenMessung(ITestOutputHelper aus) => _aus = aus;

    /// <summary>LISTEN_BILDER = "x,y,b,h;bild1.png;bild2.png…": das Listen-Gitter aus Bildschirmfotos ausschneiden und
    /// alle Zeilen lesen.</summary>
    [Fact]
    public void Ganze_Liste_lesen()
    {
        string? was = Environment.GetEnvironmentVariable("LISTEN_BILDER");
        if (string.IsNullOrWhiteSpace(was)) return;
        var teile = was.Split(';', StringSplitOptions.RemoveEmptyEntries);
        var z = teile[0].Split(',').Select(int.Parse).ToArray();
        foreach (string datei in teile.Skip(1))
        {
            using var ganz = new Bitmap(datei);
            using var liste = ganz.Clone(new Rectangle(z[0], z[1], z[2], z[3]), PixelFormat.Format32bppRgb);
            var p = ListenLeser.Punkte(liste);
            var r = ListenLeser.FindeRaster(p, liste.Width, liste.Height);
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            int nr = 0;
            string? ordner = Environment.GetEnvironmentVariable("LISTEN_PROBE");
            ListenLeser.Probe = ordner == null ? null : (b, s) =>
            {
                if (!s.Contains("fett")) return;
                b.Save(Path.Combine(ordner, $"probe-{Path.GetFileNameWithoutExtension(datei)}-{nr}.png"));
                _aus.WriteLine($"[    probe {nr++} {b.Width}x{b.Height}: {s}");
            };
            var zeilen = ListenLeser.LiesAlle(liste);
            ListenLeser.Probe = null;
            _aus.WriteLine($"[{Path.GetFileName(datei)}] {uhr.ElapsedMilliseconds} ms, Raster {r?.Beschreibung}/{r?.Anbieter}/{r?.Datenquelle}/{r?.Rechts}, "
                           + $"Zeilen {string.Join(" ", r?.Zeilen.Select(x => $"{x.Oben}-{x.Unten}") ?? Array.Empty<string>())}");
            foreach (var l in zeilen)
                _aus.WriteLine($"[  {l.Oben}: lesbar={l.Lesbar} privat={l.Privat} preis={l.Preis} | {l.Portal} | {l.Zeit} | {l.Titel} | {l.Art} | {l.Daten} | {l.Ausstattung}"
                               + $" => Zustand {l.Zustand}, {l.Jahr}, {l.Km} km, {l.Ps} PS, {l.Kraftstoff}"
                               + $" ## Anbieter „{l.Anbieter}“, Tel [{string.Join("; ", l.Nummern)}], {l.EntfernungKm} km weg, Ort „{l.Ort}“");
        }
    }

    /// <summary>KOPF_BILDER = "x,y,b,h;bild.png…": die Kopf-Tabelle der Detailansicht ausschneiden und lesen.</summary>
    [Fact]
    public void Kopf_lesen()
    {
        string? was = Environment.GetEnvironmentVariable("KOPF_BILDER");
        if (string.IsNullOrWhiteSpace(was)) return;
        var teile = was.Split(';', StringSplitOptions.RemoveEmptyEntries);
        var z = teile[0].Split(',').Select(int.Parse).ToArray();
        foreach (string datei in teile.Skip(1))
        {
            using var ganz = new Bitmap(datei);
            using var kopf = ganz.Clone(new Rectangle(z[0], z[1], z[2], z[3]), PixelFormat.Format32bppRgb);
            var p = ListenLeser.Punkte(kopf);
            var b = ListenLeser.Baender(p, kopf.Width, 0, kopf.Width, 0, kopf.Height);
            var k = Dauerlauf.Kopf(kopf);
            _aus.WriteLine($"[{Path.GetFileName(datei)}] Bänder {string.Join(" ", b.Select(x => $"{x.Von}-{x.Bis}"))}: „{k.Zeile}“ | „{k.Titel}“ | {k.Preis}");
        }
    }

    /// <summary>TABELLE_BILDER = "x,y,b,h;x,y,b,h;bild.png…": Technische Daten und Ausstattung ausschneiden und lesen.</summary>
    [Fact]
    public void Tabellen_lesen()
    {
        string? was = Environment.GetEnvironmentVariable("TABELLE_BILDER");
        if (string.IsNullOrWhiteSpace(was)) return;
        var teile = was.Split(';', StringSplitOptions.RemoveEmptyEntries);
        var t = teile[0].Split(',').Select(int.Parse).ToArray();
        var a = teile[1].Split(',').Select(int.Parse).ToArray();
        foreach (string datei in teile.Skip(2))
        {
            using var ganz = new Bitmap(datei);
            using var technik = ganz.Clone(new Rectangle(t[0], t[1], t[2], t[3]), PixelFormat.Format32bppRgb);
            using var ausst = ganz.Clone(new Rectangle(a[0], a[1], a[2], a[3]), PixelFormat.Format32bppRgb);
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            var pp = ListenLeser.Punkte(technik);
            var zz = TabellenLeser.Zeilen(pp, technik.Width, technik.Height);
            _aus.WriteLine($"[   Zeilen {zz.Count}: {string.Join(" ", zz.Take(4).Select(x => $"{x.Oben}-{x.Unten}"))}");
            foreach (var (o, u) in zz.Take(3))
            {
                int gr = pp[(o + 1) * technik.Width + 2];
                var st = TabellenLeser.Stuecke(pp, technik.Width, o, u, gr, 14);
                _aus.WriteLine($"[   Zeile {o}: Grund {gr:X6}, Stücke {string.Join(" ", st.Select(x => $"{x.Von}-{x.Bis}"))}, links {string.Join(",", Enumerable.Range(0, 8).Select(x => pp[(o + 1) * technik.Width + x].ToString("X6")))}");
            }
            var m = TabellenLeser.Technik(technik);
            long ms = uhr.ElapsedMilliseconds;
            _aus.WriteLine($"[{Path.GetFileName(datei)}] {m.Count} Merkmale in {ms} ms: {string.Join(" | ", m.Select(x => x.Key + "=" + x.Value))}");
            _aus.WriteLine($"[   Ausstattung: {string.Join(" | ", TabellenLeser.Ausstattung(ausst))}");
            var f = new Fahrzeug();
            TabellenLeser.Uebernehmen(f, m);
            _aus.WriteLine($"[   => {f.MarkeModellText}, {f.Preis} EUR, {f.Kilometer} km, EZ {f.EzMonat}/{f.EzJahr}, {f.Kw} kW/{f.Ps} PS, {f.Kraftstoff}, {f.Getriebe}, Nr. {f.InseratId}");
        }
    }

    [Fact]
    public void Ausschnitte_lesen()
    {
        string? was = Environment.GetEnvironmentVariable("LISTEN_MESSUNG");
        if (string.IsNullOrWhiteSpace(was)) return;
        var teile = was.Split(';', StringSplitOptions.RemoveEmptyEntries);
        using var ganz = new Bitmap(teile[0]);
        foreach (string t in teile.Skip(1))
        {
            var z = t.Split(',').Select(int.Parse).ToArray();
            using var stueck = ganz.Clone(new Rectangle(z[0], z[1], z[2], z[3]), PixelFormat.Format32bppRgb);
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            bool st = Environment.GetEnvironmentVariable("LISTEN_STUECK") != "0";
            bool fett = Environment.GetEnvironmentVariable("LISTEN_FETT") == "1";
            int grund = Convert.ToInt32(Environment.GetEnvironmentVariable("LISTEN_GRUND") ?? "FFFFFF", 16);
            var l = SchriftLeser.LiesFeld(stueck, st, fett, 0, grund);
            _aus.WriteLine($"[{t}] {uhr.ElapsedMilliseconds} ms, px {l?.Px}, Lücken {l?.Luecken}: „{l?.Text.Replace("\n", " ⏎ ")}“");
            if (l is { Luecken: 0, Zeilen.Count: > 0 }) continue;
            int px = int.TryParse(Environment.GetEnvironmentVariable("LISTEN_PX"), out int p) ? p : 14;
            for (int oben = -2; oben <= 6; oben++)
                _aus.WriteLine($"[   oben {oben}: {SchriftLeser.Fuer(px, st, fett, grund).SpurBei(stueck, oben)}");
        }
    }
}
