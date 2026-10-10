using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Runtime.InteropServices.WindowsRuntime;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;

namespace AutoPointerVergleich;

/// <summary>Eine erkannte Textzeile, Koordinaten im Originalbild (unskaliert).</summary>
internal sealed record OcrZeile(string Text, double X, double Y, double Breite, double Hoehe)
{
    public double MitteY => Y + Hoehe / 2;
    public double Rechts => X + Breite;
}

/// <summary>Windows-eigene Texterkennung (offline, kostenlos, in Windows 10/11
/// enthalten). Braucht das deutsche Sprachpaket bzw. dessen Texterkennung.</summary>
internal sealed class TextErkennung
{
    private readonly OcrEngine _engine;
    public string Sprache { get; }

    private TextErkennung(OcrEngine engine)
    {
        _engine = engine;
        Sprache = engine.RecognizerLanguage.LanguageTag;
    }

    public static TextErkennung? Erstelle(out string fehler)
    {
        fehler = "";
        OcrEngine? engine = null;
        try
        {
            engine = OcrEngine.TryCreateFromLanguage(new Windows.Globalization.Language("de-DE"))
                     ?? OcrEngine.TryCreateFromUserProfileLanguages();
        }
        // Pruefung 08.10.2026 (1.5.9, E): die (englische) .NET-Meldung nur ins Protokoll, nicht in den Text fuer den Sucher
        catch (Exception ex) { Protokoll.Schreibe("Texterkennung nicht erzeugt: " + ex); }
        if (engine == null)
        {
            fehler = "Windows-Texterkennung nicht verfügbar. Bitte unter Einstellungen > Zeit und Sprache > Sprache "
                   + "„Deutsch (Deutschland)“ mit „Optische Zeichenerkennung“ installieren.";
            return null;
        }
        return new TextErkennung(engine);
    }

    /// <summary>Pruefung 08.10.2026 (1.5.9, J): so lange darf eine Erkennung hoechstens dauern. RecognizeAsync hatte keine
    /// Frist und lief im Takt unter der Sperre des Ueberwachers — hing die Windows-Texterkennung, war das Programm bis zum
    /// Neustart taub. Nach 10 s gilt es als Lesefehler (TimeoutException; der Ueberwacher versucht es spaeter erneut).</summary>
    internal static TimeSpan Frist { get; set; } = TimeSpan.FromSeconds(10);

    /// <summary>Auf <paramref name="auftrag"/> hoechstens <paramref name="frist"/> warten; sonst TimeoutException (deutsch).
    /// Ein haengender Auftrag laeuft im Hintergrund weiter — <paramref name="danach"/> raeumt auf, wenn er doch noch
    /// endet (das Bild darf nicht unter ihm weg). (fuer Tests)</summary>
    internal static async Task<T> MitFrist<T>(Task<T> auftrag, TimeSpan frist, Action? danach = null)
    {
        try { return await auftrag.WaitAsync(frist); }
        catch (TimeoutException)
        {
            if (danach != null) _ = auftrag.ContinueWith(_ => danach(), TaskScheduler.Default);
            throw new TimeoutException(NichtGeantwortet(frist));
        }
    }

    internal static string NichtGeantwortet(TimeSpan frist) => $"Windows-Texterkennung hat nach {frist.TotalSeconds:0} s nicht geantwortet.";

    /// <summary>Pruefung 09.10.2026 (Befund 9): je Engine hoechstens EIN haengender Auftrag. Nach einer Zeitueberschreitung
    /// lief RecognizeAsync samt SoftwareBitmap (bis ~10 MB) im Hintergrund weiter — der Takt versuchte es 3-mal,
    /// "Vergleichen" beliebig oft: bei toter OcrEngine stapelten sich Auftraege und Speicher. Solange der haengende Auftrag
    /// nicht zurueck ist, wirft jeder neue sofort TimeoutException (gleicher Lesefehler, kein neuer Auftrag); kommt er
    /// doch noch, ist die Engine wieder frei. Eigene Klasse, damit es sich ohne OcrEngine pruefen laesst.</summary>
    internal sealed class Haengewache
    {
        private volatile bool _haengt;

        /// <summary>Ein Auftrag laeuft seit der Frist noch — die Engine nimmt keinen neuen an.</summary>
        public bool Haengt => _haengt;

        internal const string HaengtNoch = "Windows-Texterkennung hängt noch (seit der letzten Zeitüberschreitung) – kein neuer Auftrag gestartet.";

        /// <param name="auftrag">Wird NUR gestartet, wenn kein Auftrag haengt.</param>
        /// <param name="danach">Aufraeumen, wenn ein haengender Auftrag doch noch endet (laeuft dann einmal, nach dem Freigeben).</param>
        public async Task<T> MitFrist<T>(Func<Task<T>> auftrag, TimeSpan frist, Action? danach = null)
        {
            if (_haengt) throw new TimeoutException(HaengtNoch);
            Task<T> task = auftrag();
            try { return await task.WaitAsync(frist); }
            catch (TimeoutException)
            {
                // erst sperren, dann die Fortsetzung anhaengen — ist der Auftrag genau jetzt fertig, laeuft sie sofort
                // und gibt wieder frei (nie umgekehrt: sonst bliebe die Sperre fuer immer)
                _haengt = true;
                _ = task.ContinueWith(_ =>
                {
                    _haengt = false;
                    danach?.Invoke();
                }, TaskScheduler.Default);
                throw new TimeoutException(NichtGeantwortet(frist));
            }
        }
    }

    private readonly Haengewache _wache = new();

    /// <summary>Befund 9: haengt gerade ein Auftrag dieser Engine?</summary>
    internal bool Haengt => _wache.Haengt;

    /// <summary>Liest das Bild. Kleine Tabellenschrift wird vorher hochskaliert
    /// (Faktor 3 bei 96 dpi) - das macht die Erkennung deutlich sicherer.</summary>
    public async Task<List<OcrZeile>> LiesAsync(Bitmap bild, double faktor)
    {
        int max = (int)OcrEngine.MaxImageDimension - 8;
        faktor = Math.Min(faktor, Math.Min((double)max / bild.Width, (double)max / bild.Height));
        faktor = Math.Max(faktor, 0.5);
        int b = Math.Max(1, (int)Math.Round(bild.Width * faktor));
        int h = Math.Max(1, (int)Math.Round(bild.Height * faktor));

        using var gross = new Bitmap(b, h, PixelFormat.Format32bppArgb);
        using (var g = Graphics.FromImage(gross))
        {
            g.Clear(Color.White);
            g.InterpolationMode = InterpolationMode.HighQualityBicubic;
            g.PixelOffsetMode = PixelOffsetMode.HighQuality;
            g.DrawImage(bild, 0, 0, b, h);
        }
        var daten = gross.LockBits(new Rectangle(0, 0, b, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        byte[] puffer;
        try
        {
            puffer = new byte[daten.Stride * h];
            System.Runtime.InteropServices.Marshal.Copy(daten.Scan0, puffer, 0, puffer.Length);
        }
        finally { gross.UnlockBits(daten); }
        if (daten.Stride != b * 4) puffer = Verdichte(puffer, daten.Stride, b, h);
        for (int i = 3; i < puffer.Length; i += 4) puffer[i] = 255;

        // Befund 9 (09.10.2026): haengt noch ein Auftrag dieser Engine, gar nicht erst ein Bild anlegen
        if (_wache.Haengt) throw new TimeoutException(Haengewache.HaengtNoch);
        var sw = SoftwareBitmap.CreateCopyFromBuffer(puffer.AsBuffer(), BitmapPixelFormat.Bgra8, b, h, BitmapAlphaMode.Premultiplied);
        OcrResult ergebnis;
        bool haengt = false;
        try
        {
            // 1.5.9 (J): hoechstens 10 s — haengt sie, wird das Bild erst freigegeben, wenn sie doch noch fertig wird.
            // Befund 9: bis dahin nimmt die Engine keinen neuen Auftrag an (Haengewache)
            ergebnis = await _wache.MitFrist(() => _engine.RecognizeAsync(sw).AsTask(), Frist, () => sw.Dispose());
        }
        catch (TimeoutException) { haengt = true; throw; }
        finally { if (!haengt) sw.Dispose(); }
        var zeilen = new List<OcrZeile>();
        foreach (var zeile in ergebnis.Lines)
        {
            if (zeile.Words.Count == 0) continue;
            double x1 = zeile.Words.Min(w => w.BoundingRect.X);
            double y1 = zeile.Words.Min(w => w.BoundingRect.Y);
            double x2 = zeile.Words.Max(w => w.BoundingRect.X + w.BoundingRect.Width);
            double y2 = zeile.Words.Max(w => w.BoundingRect.Y + w.BoundingRect.Height);
            zeilen.Add(new OcrZeile(zeile.Text, x1 / faktor, y1 / faktor, (x2 - x1) / faktor, (y2 - y1) / faktor));
        }
        return zeilen;
    }

    private static byte[] Verdichte(byte[] quelle, int stride, int b, int h)
    {
        var ziel = new byte[b * 4 * h];
        for (int y = 0; y < h; y++) Buffer.BlockCopy(quelle, y * stride, ziel, y * b * 4, b * 4);
        return ziel;
    }
}
