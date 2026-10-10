using System.Drawing;
using System.Drawing.Imaging;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Pruefung 05.10.2026 (Paket 3, F1): die drei Texterkennungs-Durchgaenge (Technik, Kopf, zweiter Durchgang)
/// laufen mit eigenen Engines parallel — das Ergebnis muss dasselbe sein wie nacheinander auf einer Engine.
/// Laeuft nur, wenn die Windows-Texterkennung auf dem PC vorhanden ist (sonst still uebersprungen, wie in der CI
/// ohne Sprachpaket).</summary>
[Collection("Protokolldateien")]
public class TexterkennungTests
{
    /// <summary>Gezeichnete Technik-Tabelle wie in AutoPointer (Bezeichnung links, Wert rechts, 22 px je Zeile).</summary>
    private static Bitmap Tabelle(params (string Bezeichnung, string Wert)[] zeilen)
    {
        var bmp = new Bitmap(420, 22 * zeilen.Length + 10, PixelFormat.Format32bppRgb);
        using var g = Graphics.FromImage(bmp);
        g.Clear(Color.White);
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        using var schrift = new Font("Segoe UI", 9f);
        for (int i = 0; i < zeilen.Length; i++)
        {
            g.DrawString(zeilen[i].Bezeichnung, schrift, Brushes.Black, 12, 4 + 22 * i);
            g.DrawString(zeilen[i].Wert, schrift, Brushes.Black, 212, 4 + 22 * i);
        }
        return bmp;
    }

    [Fact]
    public async Task Parallele_Durchgaenge_lesen_dasselbe_wie_nacheinander()
    {
        var ocr = TextErkennung.Erstelle(out _);
        var ocr2 = TextErkennung.Erstelle(out _);
        var ocr3 = TextErkennung.Erstelle(out _);
        if (ocr == null || ocr2 == null || ocr3 == null) return;      // keine Texterkennung auf diesem PC
        Protokoll.DateiAktiv = false;
        using var technik = Tabelle(("Marke, Modell:", "VW Golf"), ("Erstzulassung:", "05/2019"),
                                    ("Kilometerstand:", "61.000 km"), ("Leistung:", "110 kW (150 PS)"),
                                    ("Kraftstoff:", "Benzin"), ("Inserat-ID:", "3529712138"));
        using var kopf = Tabelle(("03.10.2026 12:07:54 - Inserat von Mobile.de", ""), ("VW Golf VII 1.5 TSI", ""));

        var nacheinander = await AutoPointerQuelle.LiesBilderAsync(ocr, technik, kopf, 96, false);
        var parallel = await AutoPointerQuelle.LiesBilderAsync(ocr, technik, kopf, 96, false, ocr2, ocr3);

        Assert.False(nacheinander.Leer);
        Assert.Equal(nacheinander.Rohtext, parallel.Rohtext);
        Assert.Equal(nacheinander.Fahrzeug.Schluessel, parallel.Fahrzeug.Schluessel);
        Assert.Equal(nacheinander.Fahrzeug.InseratId, parallel.Fahrzeug.InseratId);
        Assert.Equal(nacheinander.Fahrzeug.Kw, parallel.Fahrzeug.Kw);
        // und die Lesung stimmt (gezeichnete Schrift, 3-facher Zoom)
        Assert.Equal("VW Golf", parallel.Fahrzeug.MarkeModellText);
        Assert.Equal(2019, parallel.Fahrzeug.EzJahr);
        Assert.Equal(61000, parallel.Fahrzeug.Kilometer);
        // mehrmals hintereinander: keine Rennen zwischen den Engines
        for (int i = 0; i < 3; i++)
        {
            var noch = await AutoPointerQuelle.LiesBilderAsync(ocr, technik, kopf, 96, false, ocr2, ocr3);
            Assert.Equal(parallel.Rohtext, noch.Rohtext);
        }
    }

    [Fact]   // Pruefung 08.10.2026 (1.5.9, J): eine haengende Erkennung gilt nach der Frist als Lesefehler
    public async Task Haengende_Erkennung_wird_nach_der_Frist_ein_Lesefehler()
    {
        Assert.Equal(TimeSpan.FromSeconds(10), TextErkennung.Frist);
        var haengt = new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
        var aufgeraeumt = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var uhr = System.Diagnostics.Stopwatch.StartNew();
        var ex = await Assert.ThrowsAsync<TimeoutException>(
            () => TextErkennung.MitFrist(haengt.Task, TimeSpan.FromMilliseconds(100), () => aufgeraeumt.SetResult()));
        Assert.True(uhr.ElapsedMilliseconds < 5000, uhr.ElapsedMilliseconds.ToString());
        Assert.Contains("Texterkennung", ex.Message);
        Assert.False(aufgeraeumt.Task.IsCompleted);           // das Bild bleibt, solange die Erkennung noch laeuft
        haengt.SetResult(1);                                   // kommt sie doch noch zurueck, wird aufgeraeumt
        await aufgeraeumt.Task.WaitAsync(TimeSpan.FromSeconds(5));
        // rechtzeitig: das Ergebnis, kein Aufraeumen ueber den Umweg
        Assert.Equal(7, await TextErkennung.MitFrist(Task.FromResult(7), TimeSpan.FromSeconds(1), () => throw new InvalidOperationException()));
    }
}
