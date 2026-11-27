using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Text;
using System.Text.RegularExpressions;
using Xunit;
using Xunit.Abstractions;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 27.11.2026 ("es darf keine Lesefehler geben"): misst, wie genau die Texterkennung das
/// Beschreibungsfeld aus AutoPointer liest — je Bildaufbereitung die Fehlerquote gegen den exakten Text. Laeuft nur mit
/// <c>BESCHREIBUNG_MESSUNG</c> = Ordner mit Unterordnern, je einer mit <c>beschreibung.png</c> (aus
/// <c>--einmal --ganz --bilder</c>) und <c>wahr.txt</c> (der exakte Text):
/// <code>$env:BESCHREIBUNG_MESSUNG="C:\…\paare"
/// dotnet test tests --filter BeschreibungMessung --logger "console;verbosity=detailed"</code></summary>
[Collection("Protokolldateien")]
public class BeschreibungMessung
{
    private readonly ITestOutputHelper _aus;
    public BeschreibungMessung(ITestOutputHelper aus) => _aus = aus;

    private sealed record Variante(string Name, double Faktor, bool Grau = false, double Breiter = 1.0,
                                   InterpolationMode Art = InterpolationMode.HighQualityBicubic, bool Zeilenweise = false,
                                   double Kontrast = 1.0);

    [Fact]
    public async Task Fehlerquote_je_Aufbereitung()
    {
        string? ordner = Environment.GetEnvironmentVariable("BESCHREIBUNG_MESSUNG");
        if (string.IsNullOrWhiteSpace(ordner) || !Directory.Exists(ordner)) return;
        var ocr = TextErkennung.Erstelle(out _);
        if (ocr == null) return;
        Protokoll.DateiAktiv = false;

        var varianten = new List<Variante>
        {
            new("wie bisher (2,4)", 2.4),
            new("3", 3), new("4", 4), new("5", 5), new("6", 6),
            new("4 grau", 4, Grau: true), new("5 grau", 5, Grau: true),
            new("4 grau breiter 1,3", 4, Grau: true, Breiter: 1.3),
            new("4 grau breiter 1,6", 4, Grau: true, Breiter: 1.6),
            new("5 grau breiter 1,3", 5, Grau: true, Breiter: 1.3),
            new("3 grau breiter 1,6", 3, Grau: true, Breiter: 1.6),
            new("4 bilinear grau", 4, Grau: true, Art: InterpolationMode.HighQualityBilinear),
            new("4 grau kontrast 1,5", 4, Grau: true, Kontrast: 1.5),
            new("4 grau breiter 1,3 kontrast 1,5", 4, Grau: true, Breiter: 1.3, Kontrast: 1.5),
            new("zeilenweise 4 grau", 4, Grau: true, Zeilenweise: true),
            new("zeilenweise 5 grau breiter 1,3", 5, Grau: true, Breiter: 1.3, Zeilenweise: true),
            new("zeilenweise 6 grau breiter 1,3", 6, Grau: true, Breiter: 1.3, Zeilenweise: true),
        };
        var sb = new StringBuilder();
        foreach (var paar in Directory.GetDirectories(ordner).Where(d => File.Exists(Path.Combine(d, "wahr.txt"))))
        {
            string wahr = Normal(File.ReadAllText(Path.Combine(paar, "wahr.txt")));
            using var roh = new Bitmap(Path.Combine(paar, "beschreibung.png"));
            sb.AppendLine($"== {Path.GetFileName(paar)}: {roh.Width}x{roh.Height}, {wahr.Length} Zeichen");
            foreach (var v in varianten)
            {
                var uhr = System.Diagnostics.Stopwatch.StartNew();
                string text = Normal(await LiesAsync(ocr, roh, v));
                uhr.Stop();
                int fehler = Abstand(wahr, text);
                sb.AppendLine($"{fehler,4} Fehler ({100.0 * fehler / wahr.Length:0.00} %)  {uhr.ElapsedMilliseconds,5} ms  {v.Name}");
                File.WriteAllText(Path.Combine(paar, "gelesen-" + Regex.Replace(v.Name, "[^a-z0-9]+", "-", RegexOptions.IgnoreCase) + ".txt"), text);
            }
        }
        _aus.WriteLine(sb.ToString());
        File.WriteAllText(Path.Combine(ordner, "messung.txt"), sb.ToString());
    }

    /// <summary>Der Schrift-Abgleich (<see cref="SchriftLeser"/>) gegen denselben exakten Text.</summary>
    [Fact]
    public void Schrift_Abgleich()
    {
        string? ordner = Environment.GetEnvironmentVariable("BESCHREIBUNG_MESSUNG");
        if (string.IsNullOrWhiteSpace(ordner) || !Directory.Exists(ordner)) return;
        var sb = new StringBuilder();
        foreach (var paar in Directory.GetDirectories(ordner).Where(d => File.Exists(Path.Combine(d, "beschreibung.png"))))
        {
            using var roh = new Bitmap(Path.Combine(paar, "beschreibung.png"));
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            var l = SchriftLeser.LiesFeld(roh);
            uhr.Stop();
            var uhr2 = System.Diagnostics.Stopwatch.StartNew();
            SchriftLeser.LiesFeld(roh);
            sb.AppendLine($"   zweiter Lauf (Vorlagen schon gezeichnet): {uhr2.ElapsedMilliseconds} ms");
            if (l == null)
            {
                sb.AppendLine($"== {Path.GetFileName(paar)}: keine Schrift passt ({uhr.ElapsedMilliseconds} ms), "
                              + $"Zeilenabstand {SchriftLeser.Zeilenabstand(BeschreibungGanz.LeereZeilen(roh))}");
                foreach (int px in new[] { 13, 14 })
                    for (int oben = -2; oben <= 2; oben++)
                    {
                        var z = SchriftLeser.Fuer(px).ZeileBei(roh, oben);
                        sb.AppendLine($"   {px} px (Höhe {SchriftLeser.Fuer(px).Hoehe}), oben {oben}: exakt={z?.Exakt} „{z?.Text}“");
                    }
                continue;
            }
            sb.AppendLine($"== {Path.GetFileName(paar)}: {l.Px} px, {l.Zeilen.Count} Zeilen, {l.Luecken} Lücken, exakt={l.Exakt}, {uhr.ElapsedMilliseconds} ms");
            File.WriteAllText(Path.Combine(paar, "abgleich.txt"), l.Text);
            string wahrDatei = Path.Combine(paar, "wahr.txt");
            if (File.Exists(wahrDatei))
            {
                string wahr = Normal(File.ReadAllText(wahrDatei)), text = Normal(l.Text);
                sb.AppendLine($"   Abstand zum abgelesenen Text: {Abstand(wahr, text)} von {wahr.Length} Zeichen");
            }
            foreach (var z in l.Zeilen.Where(z => !z.Exakt)) sb.AppendLine("   nicht aufgegangen: " + z.Text);
        }
        _aus.WriteLine(sb.ToString());
        File.WriteAllText(Path.Combine(ordner, "abgleich-messung.txt"), sb.ToString());
    }

    private static async Task<string> LiesAsync(TextErkennung ocr, Bitmap roh, Variante v)
    {
        using var bild = Aufbereiten(roh, v);
        if (!v.Zeilenweise)
            return string.Join("\n", (await ocr.LiesAsync(bild, v.Faktor)).Select(z => z.Text));
        // Zeilenweise: an leeren Zeilen in einzelne Textzeilen schneiden, jede mit etwas Rand einzeln lesen
        var leer = BeschreibungGanz.LeereZeilen(bild);
        var teile = new List<string>();
        int y = 0;
        while (y < leer.Length)
        {
            while (y < leer.Length && leer[y]) y++;
            int von = y;
            while (y < leer.Length && !leer[y]) y++;
            if (y - von < 3) continue;
            int a = Math.Max(0, von - 3), b = Math.Min(leer.Length, y + 3);
            using var zeile = new Bitmap(bild.Width + 16, b - a + 8, PixelFormat.Format32bppRgb);
            using (var g = Graphics.FromImage(zeile))
            {
                g.Clear(Color.White);
                g.DrawImage(bild, new Rectangle(8, 4, bild.Width, b - a), new Rectangle(0, a, bild.Width, b - a), GraphicsUnit.Pixel);
            }
            teile.Add(string.Join(" ", (await ocr.LiesAsync(zeile, v.Faktor)).Select(z => z.Text)));
        }
        return string.Join("\n", teile);
    }

    private static Bitmap Aufbereiten(Bitmap roh, Variante v)
    {
        int b = (int)Math.Round(roh.Width * v.Breiter);
        var ziel = new Bitmap(b, roh.Height, PixelFormat.Format32bppRgb);
        using var g = Graphics.FromImage(ziel);
        g.Clear(Color.White);
        g.InterpolationMode = v.Art;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        using var attr = new ImageAttributes();
        if (v.Grau || v.Kontrast != 1.0)
        {
            float k = (float)v.Kontrast, t = 0.5f * (1 - k);
            float r = v.Grau ? 0.299f : 1, gr = v.Grau ? 0.587f : 0, bl = v.Grau ? 0.114f : 0;
            var m = v.Grau
                ? new ColorMatrix(new[]
                {
                    new[] { r * k, r * k, r * k, 0, 0 }, new[] { gr * k, gr * k, gr * k, 0, 0 },
                    new[] { bl * k, bl * k, bl * k, 0, 0 }, new float[] { 0, 0, 0, 1, 0 }, new[] { t, t, t, 0, 1 },
                })
                : new ColorMatrix(new[]
                {
                    new float[] { k, 0, 0, 0, 0 }, new float[] { 0, k, 0, 0, 0 }, new float[] { 0, 0, k, 0, 0 },
                    new float[] { 0, 0, 0, 1, 0 }, new[] { t, t, t, 0, 1 },
                });
            attr.SetColorMatrix(m);
        }
        attr.SetWrapMode(WrapMode.TileFlipXY);
        g.DrawImage(roh, new Rectangle(0, 0, b, roh.Height), 0, 0, roh.Width, roh.Height, GraphicsUnit.Pixel, attr);
        return ziel;
    }

    /// <summary>Fuer den Vergleich: Aufzaehlungspunkte und Leerraum zaehlen nicht.</summary>
    private static string Normal(string s) => Regex.Replace(Regex.Replace(s, "[•●·]", " "), @"\s+", " ").Trim();

    private static int Abstand(string a, string b)
    {
        var alt = new int[b.Length + 1];
        var neu = new int[b.Length + 1];
        for (int j = 0; j <= b.Length; j++) alt[j] = j;
        for (int i = 1; i <= a.Length; i++)
        {
            neu[0] = i;
            for (int j = 1; j <= b.Length; j++)
                neu[j] = Math.Min(Math.Min(alt[j] + 1, neu[j - 1] + 1), alt[j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1));
            (alt, neu) = (neu, alt);
        }
        return alt[b.Length];
    }
}
