using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 09.10.2026 (1.5.13, "wenn er etwas ausliest und nicht erkennt: bitte uns automatisch ein
/// Screenshot des nicht Erkannten geben"): das Bild, das die Texterkennung gelesen hat — Kopf-Tabelle ueber
/// Technik-Tabelle, KEIN neues Abbild und nie PrintWindow —, samt Grund und Rohtext fuer POST …/lesebild. Bei AutoSchnell
/// sieht man dann, was auf dem Bildschirm stand, und der Server kann daraus lernen (Reparaturen, Katalog).</summary>
/// <param name="Grund"><see cref="Lesebilder.PflichtfeldFehlt"/>, <see cref="Lesebilder.MarkeUnbekannt"/>,
/// <see cref="Lesebilder.ModellUnbekannt"/> oder <see cref="Lesebilder.InseratIdFehlt"/>.</param>
/// <param name="Bild">PNG (24 Bit, hoechstens <see cref="Lesebilder.MaxBytes"/>).</param>
/// <param name="Fehlt">Nur bei pflichtfeld_fehlt: die Bezeichnungen aus <see cref="DetailLeser.Fehlend"/>.</param>
/// <param name="VorgangId">Die Vorgangsnummer aus der /vergleich-Antwort, wenn es schon eine gab.</param>
internal sealed record Lesebild(string Grund, byte[] Bild, string Rohtext, Fahrzeug? Fahrzeug = null,
                                IReadOnlyList<string>? Fehlt = null, string? VorgangId = null);

/// <summary>Die reinen Teile zum Lesebild: Zusammensetzen, PNG mit Groessengrenze, Anfrage an den Server.</summary>
internal static class Lesebilder
{
    public const string PflichtfeldFehlt = "pflichtfeld_fehlt";
    public const string MarkeUnbekannt = "marke_unbekannt";
    public const string ModellUnbekannt = "modell_unbekannt";
    public const string InseratIdFehlt = "inserat_id_fehlt";

    /// <summary>Groesser darf das PNG nicht sein (der Server lehnt mehr mit 413 ab). Eine Detailansicht ist als PNG
    /// meist 30-300 KB — die Grenze greift praktisch nur bei riesigen Bildschirmen mit hoher Skalierung.</summary>
    public const int MaxBytes = 1_500_000;
    /// <summary>Um so viel wird je Schritt verkleinert, bis das PNG passt.</summary>
    public const double Schritt = 0.75;
    public const int MaxRohtext = 6000;
    public const int MaxFeld = 160;

    /// <summary>Kopf ueber Technik in EINEM Bild, 24 Bit, Originalgroesse, weisser Grund (die Kopf-Tabelle ist meist
    /// breiter als die Technik-Tabelle). Liest beide Quellbilder sofort und 1:1 (kein Verwischen durch halbe Pixel) —
    /// danach duerfen sie der Texterkennung gehoeren (GDI+ erlaubt kein gleichzeitiges Lesen desselben Bildes).</summary>
    public static Bitmap Stapeln(Bitmap technik, Bitmap? kopf)
    {
        int breite = Math.Max(technik.Width, kopf?.Width ?? 0);
        int hoehe = technik.Height + (kopf?.Height ?? 0);
        var bild = new Bitmap(breite, hoehe, PixelFormat.Format24bppRgb);
        try
        {
            using var g = Graphics.FromImage(bild);
            g.InterpolationMode = InterpolationMode.NearestNeighbor;
            g.PixelOffsetMode = PixelOffsetMode.Half;
            g.Clear(Color.White);
            int y = 0;
            if (kopf != null)
            {
                Zeichne(g, kopf, 0);
                y = kopf.Height;
            }
            Zeichne(g, technik, y);
            return bild;
        }
        catch
        {
            bild.Dispose();
            throw;
        }
    }

    private static void Zeichne(Graphics g, Bitmap quelle, int y) =>
        g.DrawImage(quelle, new Rectangle(0, y, quelle.Width, quelle.Height),
                    new Rectangle(0, 0, quelle.Width, quelle.Height), GraphicsUnit.Pixel);

    /// <summary>Stapeln und im Hintergrund als PNG kodieren — zum Aufruf VOR der Texterkennung: das Zusammensetzen liest
    /// die Abbilder sofort (danach gehoeren sie der Texterkennung), das Kodieren (einige ms) laeuft nebenher auf dem
    /// Threadpool und kostet das Lesen keine Zeit. null statt Ausnahme: ein Lesebild ist nie wichtiger als der Vergleich.</summary>
    public static Task<byte[]?> PngImHintergrund(Bitmap technik, Bitmap? kopf)
    {
        Bitmap gestapelt;
        try { gestapelt = Stapeln(technik, kopf); }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Lesebild nicht zusammengesetzt: " + ex.Message);
            return Task.FromResult<byte[]?>(null);
        }
        return Task.Run(() =>
        {
            try { return Png(gestapelt); }
            catch (Exception ex)
            {
                Protokoll.Schreibe("Lesebild nicht kodiert: " + ex.Message);
                return null;
            }
            finally { gestapelt.Dispose(); }
        });
    }

    /// <summary>Als PNG kodieren; ist es groesser als <paramref name="maxBytes"/>, in Schritten von <see cref="Schritt"/>
    /// verkleinern, bis es passt. null, wenn selbst ein winziges Bild nicht passt (praktisch nie). Nie vergroessern,
    /// passt es gleich, bleibt es unveraendert. Gibt <paramref name="bild"/> NICHT frei. Teuer (einige ms) — nicht auf
    /// dem Oberflaechen-Thread.</summary>
    public static byte[]? Png(Bitmap bild, int maxBytes = MaxBytes)
    {
        Bitmap aktuell = bild;
        try
        {
            while (true)
            {
                byte[] bytes = Kodieren(aktuell);
                if (bytes.Length <= maxBytes) return bytes;
                int b = (int)Math.Round(aktuell.Width * Schritt), h = (int)Math.Round(aktuell.Height * Schritt);
                if (b < 16 || h < 16) return null;
                var kleiner = Verkleinern(aktuell, b, h);
                if (!ReferenceEquals(aktuell, bild)) aktuell.Dispose();
                aktuell = kleiner;
            }
        }
        finally
        {
            if (!ReferenceEquals(aktuell, bild)) aktuell.Dispose();
        }
    }

    private static byte[] Kodieren(Bitmap b)
    {
        using var ms = new MemoryStream();
        b.Save(ms, ImageFormat.Png);
        return ms.ToArray();
    }

    private static Bitmap Verkleinern(Bitmap quelle, int breite, int hoehe)
    {
        var ziel = new Bitmap(breite, hoehe, PixelFormat.Format24bppRgb);
        try
        {
            using var g = Graphics.FromImage(ziel);
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.Clear(Color.White);
            g.DrawImage(quelle, new Rectangle(0, 0, breite, hoehe), new Rectangle(0, 0, quelle.Width, quelle.Height), GraphicsUnit.Pixel);
            return ziel;
        }
        catch
        {
            ziel.Dispose();
            throw;
        }
    }

    /// <summary>Anfrage an POST …/lesebild (Feldnamen wie routes/werkzeuge.LesebildIn): grund, rohtext (≤ 6000),
    /// vorgang_id (oder null), bild (Base64-PNG); fehlt nur bei pflichtfeld_fehlt; fahrzeug nur, wenn eines gelesen
    /// wurde (je Feld ≤ 160 Zeichen). (rein, fuer Tests)</summary>
    public static Dictionary<string, object?> Nutzlast(Lesebild b)
    {
        static string K(string? s, int n)
        {
            s = (s ?? "").Trim();
            return s.Length > n ? s[..n] : s;
        }
        var nutzlast = new Dictionary<string, object?>
        {
            ["grund"] = b.Grund,
            ["rohtext"] = K(b.Rohtext, MaxRohtext),
            ["vorgang_id"] = b.VorgangId,
            ["bild"] = Convert.ToBase64String(b.Bild),
        };
        if (b.Grund == PflichtfeldFehlt && b.Fehlt is { Count: > 0 })
            nutzlast["fehlt"] = b.Fehlt.Select(f => K(f, 60)).Where(f => f.Length > 0).ToList();
        if (b.Fahrzeug is { } f)
            nutzlast["fahrzeug"] = new Dictionary<string, string>
            {
                ["marke_modell_text"] = K(f.MarkeModellText, MaxFeld),
                ["titel"] = K(f.Titel, MaxFeld),
                ["quelle"] = K(f.Quelle, MaxFeld),
                ["inserat_id"] = K(f.InseratId, MaxFeld),
            };
        return nutzlast;
    }
}
