using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text.Json;
using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 09.10.2026 (1.5.13): das gelesene Bild eines nicht erkannten Autos — Zusammensetzen (Kopf ueber
/// Technik, 24 Bit, Originalgroesse), Groessengrenze mit Verkleinern, Anfrage an den Server. Nur GDI+, keine Texterkennung.</summary>
public class LesebildTests
{
    private static Bitmap Flaeche(int breite, int hoehe, Color farbe)
    {
        var b = new Bitmap(breite, hoehe, PixelFormat.Format32bppRgb);     // wie AutoPointerFenster.Abbild
        using var g = Graphics.FromImage(b);
        g.Clear(farbe);
        return b;
    }

    [Fact]
    public void Kopf_steht_ueber_der_Technik_Tabelle_in_24_Bit_und_Originalgroesse()
    {
        using var technik = Flaeche(420, 100, Color.Red);
        using var kopf = Flaeche(846, 60, Color.Blue);
        using var bild = Lesebilder.Stapeln(technik, kopf);
        Assert.Equal((846, 160, PixelFormat.Format24bppRgb), (bild.Width, bild.Height, bild.PixelFormat));
        Assert.Equal(Color.Blue.ToArgb(), bild.GetPixel(0, 0).ToArgb());
        Assert.Equal(Color.Blue.ToArgb(), bild.GetPixel(845, 59).ToArgb());
        Assert.Equal(Color.Red.ToArgb(), bild.GetPixel(0, 60).ToArgb());
        Assert.Equal(Color.Red.ToArgb(), bild.GetPixel(419, 159).ToArgb());
        Assert.Equal(Color.White.ToArgb(), bild.GetPixel(600, 100).ToArgb());    // rechts neben der schmaleren Tabelle
        // die Quellbilder bleiben unberuehrt (sie gehoeren danach der Texterkennung)
        Assert.Equal(Color.Red.ToArgb(), technik.GetPixel(0, 0).ToArgb());

        byte[] png = Lesebilder.Png(bild)!;
        Assert.Equal(new byte[] { 0x89, (byte)'P', (byte)'N', (byte)'G' }, png.Take(4));
        using var zurueck = new Bitmap(new MemoryStream(png));
        Assert.Equal((846, 160, PixelFormat.Format24bppRgb), (zurueck.Width, zurueck.Height, zurueck.PixelFormat));
        Assert.Equal(Color.Red.ToArgb(), zurueck.GetPixel(10, 100).ToArgb());
    }

    [Fact]
    public void Ohne_Kopf_nur_die_Technik_Tabelle()
    {
        using var technik = Flaeche(420, 100, Color.Red);
        using var bild = Lesebilder.Stapeln(technik, null);
        Assert.Equal((420, 100, PixelFormat.Format24bppRgb), (bild.Width, bild.Height, bild.PixelFormat));
        Assert.Equal(Color.Red.ToArgb(), bild.GetPixel(419, 99).ToArgb());
    }

    [Fact]   // 1.5.13: PngImHintergrund liest die Quellen sofort — danach duerfen sie weg; das PNG kommt aus dem Hintergrund
    public async Task Png_im_Hintergrund_liest_die_Quellen_sofort()
    {
        Task<byte[]?> png;
        using (var technik = Flaeche(50, 20, Color.Green))
        using (var kopf = Flaeche(80, 10, Color.Yellow))
            png = Lesebilder.PngImHintergrund(technik, kopf);
        byte[]? bytes = await png;
        Assert.NotNull(bytes);
        using var zurueck = new Bitmap(new MemoryStream(bytes!));
        Assert.Equal((80, 30), (zurueck.Width, zurueck.Height));
        Assert.Equal(Color.Yellow.ToArgb(), zurueck.GetPixel(0, 0).ToArgb());
        Assert.Equal(Color.Green.ToArgb(), zurueck.GetPixel(0, 29).ToArgb());
    }

    [Fact]   // 1.5.13: zu gross -> in Schritten von 0,75 verkleinern, bis es passt; nie vergroessern; passt es, bleibt es
    public void Zu_grosses_PNG_wird_in_Schritten_von_drei_Vierteln_verkleinert()
    {
        // Rauschen laesst sich nicht komprimieren: ≈ 3 Byte je Pixel -> 400 x 400 ≈ 480 KB
        using var rauschen = new Bitmap(400, 400, PixelFormat.Format24bppRgb);
        var daten = rauschen.LockBits(new Rectangle(0, 0, 400, 400), ImageLockMode.WriteOnly, PixelFormat.Format24bppRgb);
        try
        {
            var bytes = new byte[daten.Stride * 400];
            new Random(7).NextBytes(bytes);
            Marshal.Copy(bytes, 0, daten.Scan0, bytes.Length);
        }
        finally { rauschen.UnlockBits(daten); }

        byte[] voll = Lesebilder.Png(rauschen)!;
        Assert.True(voll.Length > 400_000, voll.Length.ToString());
        Assert.Equal(voll, Lesebilder.Png(rauschen, maxBytes: voll.Length));   // passt gerade: unveraendert

        byte[] klein = Lesebilder.Png(rauschen, maxBytes: 200_000)!;
        Assert.True(klein.Length <= 200_000, klein.Length.ToString());
        using var zurueck = new Bitmap(new MemoryStream(klein));
        // 400 -> 300 (≈ 270 KB, zu gross) -> 225 (≈ 150 KB)
        Assert.Equal((225, 225, PixelFormat.Format24bppRgb), (zurueck.Width, zurueck.Height, zurueck.PixelFormat));
        Assert.Equal(0.75, Lesebilder.Schritt);
        Assert.Equal(1_500_000, Lesebilder.MaxBytes);

        Assert.Null(Lesebilder.Png(rauschen, maxBytes: 10));                    // passt nie: null statt Endlosschleife
        Assert.Equal((400, 400), (rauschen.Width, rauschen.Height));           // das Original bleibt
    }

    [Fact]   // die Anfrage an POST …/lesebild — Feldnamen und Grenzen wie routes/werkzeuge.LesebildIn
    public void Nutzlast_hat_die_Form_fuer_den_Server()
    {
        var f = Passat();
        f.Titel = new string('t', 200);
        var b = new Lesebild(Lesebilder.PflichtfeldFehlt, new byte[] { 1, 2, 3 }, new string('r', 7000), f,
                             new[] { "Marke/Modell", "Erstzulassung" });
        string json = JsonSerializer.Serialize(Lesebilder.Nutzlast(b), AutoSchnellDienst.Json);
        using var doc = JsonDocument.Parse(json);
        var r = doc.RootElement;
        Assert.Equal("pflichtfeld_fehlt", r.GetProperty("grund").GetString());
        Assert.Equal(new[] { "Marke/Modell", "Erstzulassung" }, r.GetProperty("fehlt").EnumerateArray().Select(x => x.GetString()));
        Assert.Equal(Lesebilder.MaxRohtext, r.GetProperty("rohtext").GetString()!.Length);
        Assert.Equal(Lesebilder.MaxFeld, r.GetProperty("fahrzeug").GetProperty("titel").GetString()!.Length);
        Assert.Equal("VW Passat Variant", r.GetProperty("fahrzeug").GetProperty("marke_modell_text").GetString());
        Assert.Equal("AQID", r.GetProperty("bild").GetString());
        Assert.Equal(JsonValueKind.Null, r.GetProperty("vorgang_id").ValueKind);    // ohne Vorgang ausdruecklich null

        // "fehlt" nur bei pflichtfeld_fehlt; ohne Fahrzeug kein "fahrzeug"; die Vorgangsnummer geht mit
        var n2 = Lesebilder.Nutzlast(new Lesebild(Lesebilder.MarkeUnbekannt, new byte[] { 1 }, "roh", null, new[] { "Erstzulassung" }, "abc"));
        Assert.False(n2.ContainsKey("fehlt"));
        Assert.False(n2.ContainsKey("fahrzeug"));
        Assert.Equal("abc", n2["vorgang_id"]);
        Assert.Equal(new[] { "pflichtfeld_fehlt", "marke_unbekannt", "modell_unbekannt", "inserat_id_fehlt" },
                     new[] { Lesebilder.PflichtfeldFehlt, Lesebilder.MarkeUnbekannt, Lesebilder.ModellUnbekannt, Lesebilder.InseratIdFehlt });
    }
}
