using System.Drawing;
using System.Drawing.Imaging;
using System.Drawing.Text;
using Xunit;
using Xunit.Abstractions;

namespace AutoPointerVergleich.Tests;

/// <summary>Befund Ahmad 09.10.2026 (1.5.12): Messung, mit der die Aufbereitungen des zweiten Blicks gewaehlt wurden —
/// gezeichnete Technik-Tabellen (3 Schriften × 3 Glaettungen × 3 Zeilenfarben × 40 Autos = 1.080 Lesungen), je Feld:
/// wie oft liest der erste Durchgang richtig, wie oft jede Aufbereitung allein, wie oft liegt der richtige Text mit dem
/// zweiten Blick (Hauptwert oder Alternative) vor, und was kostet es an Zeit. Dauert ~10 min und braucht die
/// Windows-Texterkennung — laeuft deshalb nur mit <c>ZWEITER_BLICK_MESSEN=1</c>:
/// <code>ZWEITER_BLICK_MESSEN=1 dotnet test tests\AutoPointerVergleich.Tests.csproj --filter ZweiterBlickMessung
///   --logger "console;verbosity=detailed"</code></summary>
[Collection("Protokolldateien")]
public class ZweiterBlickMessung
{
    private readonly ITestOutputHelper _aus;
    public ZweiterBlickMessung(ITestOutputHelper aus) => _aus = aus;

    private static readonly string[] Modelle =
    {
        "Hyundai i30", "VW T-Roc", "Mercedes-Benz Andere", "Hyundai i10", "BMW 320", "Audi Q8", "Kia cee'd",
        "VW Passat Variant", "Opel Mokka X", "Skoda Octavia", "Peugeot 3008", "Fiat 500L", "Volvo XC60", "Seat Ibiza",
        "Ford Fiesta", "Renault Clio", "Toyota Yaris", "Citroen C3", "Dacia Sandero", "Mini Cooper S",
        "Land Rover Range Rover Evoque", "Smart ForTwo", "Hyundai IONIQ 5", "Tesla Model 3", "BMW i3",
        "Audi A4 Allroad", "Mazda CX-5", "Nissan Qashqai", "VW ID.3", "Opel Corsa", "VW Golf", "Kia Sportage",
        "Hyundai Tucson", "Skoda Fabia", "BMW X1", "Audi A3", "Mercedes-Benz C 220", "VW Polo", "Ford Kuga", "Opel Astra",
    };

    private static readonly string[] Kraftstoffe =
    {
        "Benzin", "Diesel", "Elektro", "Hybrid (Benzin/Elektro)", "Autogas (LPG)", "Erdgas (CNG)", "Plug-in-Hybrid", "Elektro",
    };

    /// <summary>Technik-Tabelle wie in AutoPointer; eine Zeile wahlweise farbig markiert.</summary>
    private static Bitmap Tabelle(Font schrift, TextRenderingHint glaettung, (string, string)[] zeilen, Color? markiert,
                                  int markiertZeile, Color schriftMarkiert)
    {
        int zh = schrift.Size > 8.5 ? 22 : 20;
        var bmp = new Bitmap(420, zh * zeilen.Length + 10, PixelFormat.Format32bppRgb);
        using var g = Graphics.FromImage(bmp);
        g.Clear(Color.White);
        g.TextRenderingHint = glaettung;
        for (int i = 0; i < zeilen.Length; i++)
        {
            var farbe = Color.Black;
            if (markiert is { } m && i == markiertZeile)
            {
                using var p = new SolidBrush(m);
                g.FillRectangle(p, 0, 2 + zh * i, 420, zh);
                farbe = schriftMarkiert;
            }
            using var pinsel = new SolidBrush(farbe);
            g.DrawString(zeilen[i].Item1, schrift, pinsel, 12, 4 + zh * i);
            g.DrawString(zeilen[i].Item2, schrift, pinsel, 212, 4 + zh * i);
        }
        return bmp;
    }

    [Fact]
    public async Task Messen()
    {
        if (Environment.GetEnvironmentVariable("ZWEITER_BLICK_MESSEN") != "1") return;
        var ocr = TextErkennung.Erstelle(out _);
        var ocr2 = TextErkennung.Erstelle(out _);
        var ocr3 = TextErkennung.Erstelle(out _);
        if (ocr == null || ocr2 == null || ocr3 == null) { _aus.WriteLine("Keine Windows-Texterkennung."); return; }
        Protokoll.DateiAktiv = false;
        var rnd = new Random(7);
        var varianten = new (string Name, double Zoom, ZweiterBlick.Aufbereitung Art)[]
        {
            ("Gross x3", 3, ZweiterBlick.Aufbereitung.Gross), ("Gross x4", 4, ZweiterBlick.Aufbereitung.Gross),
            ("Gross x5", 5, ZweiterBlick.Aufbereitung.Gross), ("Gross x6", 6, ZweiterBlick.Aufbereitung.Gross),
            ("Kontrast x3", 3, ZweiterBlick.Aufbereitung.Kontrast), ("Kontrast x4", 4, ZweiterBlick.Aufbereitung.Kontrast),
            ("Kontrast x5", 5, ZweiterBlick.Aufbereitung.Kontrast),
        };
        var felder = new[] { Feld.MarkeModell, Feld.Kraftstoff, Feld.InseratId };
        var faelle = new[] { new List<(bool Erst, bool[] Var, bool Mit)>(), new(), new() };
        var ohne = new List<long>();
        var mit = new List<long>();
        var schriften = new[] { new Font("Tahoma", 8.25f), new Font("Segoe UI", 9f), new Font("Microsoft Sans Serif", 8.25f) };
        var glaettungen = new[] { TextRenderingHint.ClearTypeGridFit, TextRenderingHint.AntiAliasGridFit, TextRenderingHint.SingleBitPerPixelGridFit };
        var markierungen = new (Color? Farbe, Color Schrift)[]
        {
            (null, Color.Black), (Color.FromArgb(0xCD, 0xE8, 0xFF), Color.Black), (Color.FromArgb(0x31, 0x6A, 0xC5), Color.White),
        };
        foreach (var schrift in schriften)
        foreach (var glaettung in glaettungen)
        foreach (var (mFarbe, mSchrift) in markierungen)
        for (int k = 0; k < Modelle.Length; k++)
        {
            string mm = Modelle[k], kr = Kraftstoffe[k % Kraftstoffe.Length];
            string id = rnd.Next(2) == 0 ? rnd.NextInt64(100_000_000, 999_999_999).ToString()
                                         : rnd.NextInt64(1_000_000_000, 3_999_999_999).ToString();
            var zeilen = new[]
            {
                ("Marke, Modell:", mm), ("Preis:", "12.500 EUR"), ("Erstzulassung:", "05/2019"), ("Kilometerstand:", "61.000 km"),
                ("Leistung:", "110 kW (150 PS)"), ("Getriebeart:", "Schaltgetriebe"), ("Kraftstoff:", kr), ("Inserat-ID:", id),
            };
            using var bild = Tabelle(schrift, glaettung, zeilen, mFarbe, new[] { 0, 6, 7 }[k % 3], mSchrift);
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            var l1 = await AutoPointerQuelle.LiesBilderAsync(ocr, bild, null, 96, false, ocr2, ocr3, zweiterBlick: false);
            ohne.Add(uhr.ElapsedMilliseconds);
            uhr.Restart();
            var l2 = await AutoPointerQuelle.LiesBilderAsync(ocr, bild, null, 96, false, ocr2, ocr3, zweiterBlick: true);
            mit.Add(uhr.ElapsedMilliseconds);
            var f1 = l1.Fahrzeug;
            var f2 = l2.Fahrzeug;
            var wahr = new[] { mm, kr, id };
            var erst = new[] { f1.MarkeModellText, f1.Kraftstoff, f1.InseratId };
            var haupt = new[] { f2.MarkeModellText, f2.Kraftstoff, f2.InseratId };
            var alt = new[] { f2.AlternativenMarkeModell, f2.AlternativenKraftstoff, f2.AlternativenInseratId };

            // jede Aufbereitung einzeln, mit denselben Ausschnitten wie im Programm
            var zt = await ocr.LiesAsync(bild, 3);
            var bereiche = DetailLeser.WertBereiche(zt, bild.Width, bild.Height, ZweiterBlick.Felder);
            var liste = ZweiterBlick.Felder.Where(bereiche.ContainsKey).Select(x => (x, bereiche[x])).ToList();
            var je = new Dictionary<Feld, string>[varianten.Length];
            for (int v = 0; v < varianten.Length; v++)
            {
                je[v] = new();
                if (liste.Count == 0) continue;
                var (sammel, plaetze) = ZweiterBlick.Sammelbild(bild, liste, varianten[v].Zoom, varianten[v].Art);
                using (sammel) je[v] = ZweiterBlick.Zuordnen(await ocr.LiesAsync(sammel, 1.0), plaetze);
            }
            for (int i = 0; i < 3; i++)
            {
                var richtig = new bool[varianten.Length];
                for (int v = 0; v < varianten.Length; v++)
                    richtig[v] = ZweiterBlick.Saeubern(felder[i], je[v].GetValueOrDefault(felder[i])) == wahr[i];
                faelle[i].Add((erst[i] == wahr[i], richtig, haupt[i] == wahr[i] || alt[i].Contains(wahr[i])));
                if (erst[i] != wahr[i])
                    _aus.WriteLine($"{schrift.Name}/{glaettung}/{mFarbe?.Name ?? "weiss"}: „{erst[i]}“ statt „{wahr[i]}“ — "
                                   + $"Haupt „{haupt[i]}“, Alternativen {string.Join(", ", alt[i].Select(a => "„" + a + "“"))}");
            }
        }
        string[] namen = { "Marke/Modell", "Kraftstoff", "Inserat-ID" };
        for (int i = 0; i < 3; i++)
        {
            var l = faelle[i];
            _aus.WriteLine($"== {namen[i]}: {l.Count} Lesungen, erster Durchgang richtig {l.Count(x => x.Erst)}, "
                           + $"mit zweitem Blick (Hauptwert oder Alternative) {l.Count(x => x.Mit)}");
            for (int v = 0; v < varianten.Length; v++)
                _aus.WriteLine($"   {varianten[v].Name,-12} allein {l.Count(x => x.Var[v]),4}   erster + diese {l.Count(x => x.Erst || x.Var[v]),4}");
        }
        var paare = new List<(string Name, int Richtig)>();
        for (int a = 0; a < varianten.Length; a++)
            for (int b = a + 1; b < varianten.Length; b++)
                paare.Add(($"{varianten[a].Name} + {varianten[b].Name}", faelle.Sum(l => l.Count(x => x.Erst || x.Var[a] || x.Var[b]))));
        foreach (var (n, s) in paare.OrderByDescending(p => p.Richtig).Take(8)) _aus.WriteLine($"Paar {n,-28} erster + Paar richtig {s}");
        double Median(List<long> l) => l.OrderBy(x => x).ElementAt(l.Count / 2);
        _aus.WriteLine($"Zeit je Lesung (Median): ohne zweiten Blick {Median(ohne)} ms, mit {Median(mit)} ms; "
                       + $"Mittel {ohne.Average():0.0} / {mit.Average():0.0} ms ({ohne.Count} Lesungen)");
    }
}
