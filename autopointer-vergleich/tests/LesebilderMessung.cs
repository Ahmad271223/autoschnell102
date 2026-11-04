using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Text.Json;
using Xunit;
using Xunit.Abstractions;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 04.11.2026 ("alle Fotos von Inseraten, die nicht erkannt wurden, holen und verbessern"): liest
/// Lesebilder (Kopf ueber Technik, wie sie das Programm an AutoSchnell schickt — auch die 480-px-Vorschauen der
/// Betreiber-Seite oder den Export von <c>backend/scripts/lesebilder_export.py</c>) mit DERSELBEN Lesekette wie im
/// Betrieb: Texterkennung, zweiter Durchlauf, zweiter Blick, Mehrheit der Kennung. Je Bild eine JSON-Zeile mit der
/// Nutzlast an /vergleich, den fehlenden Feldern und dem Rohtext — die Auswertung auf der Server-Seite macht
/// <c>backend/scripts/lesebilder_auswerten.py</c>. Laeuft nur mit <c>LESEBILDER_ORDNER</c> (braucht die Windows-
/// Texterkennung):
/// <code>$env:LESEBILDER_ORDNER="C:\…\bilder"; $env:LESEBILDER_AUS="C:\…\messung.jsonl"
/// dotnet test tests\AutoPointerVergleich.Tests.csproj --filter LesebilderMessung --logger "console;verbosity=detailed"</code>
/// Verkleinerte Vorschauen werden vorher auf den normalen Zeilenabstand (≈ 22 px) hochgerechnet.</summary>
[Collection("Protokolldateien")]
public class LesebilderMessung
{
    private readonly ITestOutputHelper _aus;
    public LesebilderMessung(ITestOutputHelper aus) => _aus = aus;

    private const double NormalerZeilenabstand = 22.0;

    [Fact]
    public async Task Lesebilder_aus_dem_Ordner_lesen()
    {
        string? ordner = Environment.GetEnvironmentVariable("LESEBILDER_ORDNER");
        if (string.IsNullOrWhiteSpace(ordner) || !Directory.Exists(ordner)) return;
        var ocr = TextErkennung.Erstelle(out _);
        var ocr2 = TextErkennung.Erstelle(out _);
        var ocr3 = TextErkennung.Erstelle(out _);
        if (ocr == null || ocr2 == null || ocr3 == null) return;
        Protokoll.DateiAktiv = false;
        string? ausDatei = Environment.GetEnvironmentVariable("LESEBILDER_AUS");
        var zeilen = new List<string>();
        var dateien = Directory.GetFiles(ordner)
            .Where(p => p.EndsWith(".png", StringComparison.OrdinalIgnoreCase) || p.EndsWith(".jpg", StringComparison.OrdinalIgnoreCase))
            .OrderBy(p => p.Length).ThenBy(p => p, StringComparer.OrdinalIgnoreCase).ToList();
        foreach (var pfad in dateien)
        {
            using var roh = new Bitmap(pfad);
            using var bild = Hochrechnen(roh, await FaktorAsync(ocr, roh));
            // eigene Kopie fuer den Kopf: GDI+ erlaubt kein gleichzeitiges Lesen desselben Bildes (drei Engines parallel)
            using var kopf = new Bitmap(bild);
            var lesung = await AutoPointerQuelle.LiesBilderAsync(ocr, bild, kopf, 96, false, ocr2, ocr3, zweiterBlick: true);
            var f = lesung.Fahrzeug;
            var eintrag = new Dictionary<string, object?>
            {
                ["datei"] = Path.GetFileName(pfad),
                ["breite"] = bild.Width,
                ["nutzlast"] = AutoSchnellDienst.Nutzlast(f),
                ["fehlt"] = DetailLeser.Fehlend(f),
                ["unvollstaendig"] = DetailLeser.Unvollstaendig(f),
                ["inserat_id_lesungen"] = f.InseratIdLesungen,
                ["hash_lesungen"] = f.HashIdLesungen,
                ["zeilenabstand"] = lesung.Zeilenabstand,
                ["rohtext"] = lesung.Rohtext,
            };
            string json = JsonSerializer.Serialize(eintrag, new JsonSerializerOptions { WriteIndented = false });
            zeilen.Add(json);
            _aus.WriteLine(json);
        }
        if (!string.IsNullOrWhiteSpace(ausDatei)) File.WriteAllLines(ausDatei, zeilen);
    }

    /// <summary>Zeilenabstand aus einem ersten Durchgang; kleiner als normal (verkleinerte Vorschau) -> hochrechnen.</summary>
    private static async Task<double> FaktorAsync(TextErkennung ocr, Bitmap bild)
    {
        var z = await ocr.LiesAsync(bild, 3.0);
        double? abstand = DetailLeser.Zeilenabstand(z);
        // ohne erkennbare Tabelle (sehr kleine Vorschau): wie eine typische 480-px-Vorschau eines ~650 px breiten Bildes
        if (abstand is not { } a || a <= 0) return bild.Width <= 500 ? 1.4 : 1.0;
        return Math.Clamp(NormalerZeilenabstand / a, 1.0, 2.0);
    }

    private static Bitmap Hochrechnen(Bitmap quelle, double faktor)
    {
        int b = (int)Math.Round(quelle.Width * faktor), h = (int)Math.Round(quelle.Height * faktor);
        var ziel = new Bitmap(b, h, PixelFormat.Format24bppRgb);
        using var g = Graphics.FromImage(ziel);
        g.InterpolationMode = faktor > 1.0 ? InterpolationMode.HighQualityBicubic : InterpolationMode.NearestNeighbor;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        g.DrawImage(quelle, new Rectangle(0, 0, b, h), new Rectangle(0, 0, quelle.Width, quelle.Height), GraphicsUnit.Pixel);
        return ziel;
    }
}
