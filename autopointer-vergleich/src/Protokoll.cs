using System.Globalization;

namespace AutoPointerVergleich;

/// <summary>Protokoll fuer die Fehlersuche: Tagesdatei unter
/// %LOCALAPPDATA%\AutoSchnell\AutoPointer-Vergleich\protokoll\ (14 Tage) und die
/// letzten Zeilen im Speicher fuer das Protokollfenster.
/// Seit 03.10.2026 (Wunsch Ahmad) VERSCHLUESSELT (<see cref="Tresor"/>, je Eintrag eine Base64-Zeile in
/// protokoll-JJJJ-MM-TT.dat) — lesbar nur ueber das Protokollfenster bzw. --protokoll unter demselben
/// Windows-Konto. Alte Klartext-Dateien (.log) werden beim Start verschluesselt und geloescht.</summary>
internal static class Protokoll
{
    private static readonly object Sperre = new();
    private static readonly LinkedList<string> Puffer = new();
    private const int MaxZeilen = 3000;

    public static event Action<string>? NeueZeile;
    /// <summary>Zusaetzliche Ausgabe (Konsolenmodus).</summary>
    public static Action<string>? Ausgabe { get; set; }
    public static bool DateiAktiv { get; set; } = true;

    /// <summary>Wie die Einstellungen: AUTOSCHNELL_VERGLEICH_DATEN legt alles woanders ab (Tests).</summary>
    public static string Ordner =>
        Environment.GetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN") is { Length: > 0 } eigener
            ? Path.Combine(eigener, "protokoll")
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                           "AutoSchnell", "AutoPointer-Vergleich", "protokoll");

    private static string Datei(DateTime tag) => Path.Combine(Ordner, $"protokoll-{tag:yyyy-MM-dd}.dat");

    public static void Schreibe(string text)
    {
        var jetzt = DateTime.Now;
        var zeilen = text.Replace("\r", "").Split('\n');
        var sb = new System.Text.StringBuilder();
        sb.Append(jetzt.ToString("HH:mm:ss")).Append(' ').Append(zeilen[0]);
        for (int i = 1; i < zeilen.Length; i++) sb.Append('\n').Append("         ").Append(zeilen[i]);
        string eintrag = sb.ToString();
        lock (Sperre)
        {
            Puffer.AddLast(eintrag);
            while (Puffer.Count > MaxZeilen) Puffer.RemoveFirst();
            if (DateiAktiv)
            {
                try
                {
                    Directory.CreateDirectory(Ordner);
                    File.AppendAllText(Datei(jetzt), Tresor.Zeile(eintrag) + Environment.NewLine);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
                catch (System.Security.Cryptography.CryptographicException) { }
            }
        }
        Ausgabe?.Invoke(eintrag);
        NeueZeile?.Invoke(eintrag);
    }

    public static List<string> Letzte()
    {
        lock (Sperre) return Puffer.ToList();
    }

    /// <summary>Entschluesselte Eintraege eines Tages (nur unter dem Windows-Konto, das sie geschrieben hat).</summary>
    public static List<string> Tag(DateTime tag)
    {
        string datei = Datei(tag);
        if (!File.Exists(datei)) return new List<string>();
        lock (Sperre)
        {
            try
            {
                return File.ReadAllLines(datei).Where(z => z.Length > 0)
                    .Select(z => Tresor.ZeileLesen(z) ?? "(nicht lesbar – anderer Windows-Benutzer oder beschädigt)").ToList();
            }
            catch (IOException) { return new List<string>(); }
        }
    }

    /// <summary>Tage mit Protokoll, neueste zuerst.</summary>
    public static List<DateTime> Tage()
    {
        if (!Directory.Exists(Ordner)) return new List<DateTime>();
        return Directory.GetFiles(Ordner, "protokoll-*.dat")
            .Select(d => DateTime.TryParseExact(Path.GetFileNameWithoutExtension(d)["protokoll-".Length..], "yyyy-MM-dd",
                                                CultureInfo.InvariantCulture, DateTimeStyles.None, out var t) ? t : (DateTime?)null)
            .Where(t => t != null).Select(t => t!.Value).OrderByDescending(t => t).ToList();
    }

    /// <summary>Alte Klartext-Protokolle (vor dem 03.10.2026 abends) verschluesseln und den Klartext loeschen.</summary>
    public static void KlartextUmstellen()
    {
        try
        {
            if (!Directory.Exists(Ordner)) return;
            foreach (var alt in Directory.GetFiles(Ordner, "protokoll-*.log"))
            {
                string ziel = Path.ChangeExtension(alt, ".dat");
                var zeilen = File.ReadAllLines(alt).Where(z => z.Length > 0).Select(Tresor.Zeile);
                lock (Sperre) File.AppendAllLines(ziel, zeilen);
                File.Delete(alt);
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        catch (System.Security.Cryptography.CryptographicException) { }
    }

    public static void Aufraeumen(int tage = 14)
    {
        try
        {
            if (!Directory.Exists(Ordner)) return;
            foreach (var datei in Directory.GetFiles(Ordner, "protokoll-*.*"))
                if (File.GetLastWriteTime(datei) < DateTime.Now.AddDays(-tage)) File.Delete(datei);
            var bilder = Path.Combine(Ordner, "bilder");
            if (Directory.Exists(bilder))
                foreach (var datei in Directory.GetFiles(bilder))
                    if (File.GetLastWriteTime(datei) < DateTime.Now.AddDays(-3)) File.Delete(datei);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
