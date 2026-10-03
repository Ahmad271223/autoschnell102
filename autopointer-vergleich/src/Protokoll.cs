using System.Text;

namespace AutoPointerVergleich;

/// <summary>Protokoll fuer die Fehlersuche: Tagesdatei unter
/// %LOCALAPPDATA%\AutoSchnell\AutoPointer-Vergleich\protokoll\ (14 Tage) und die
/// letzten Zeilen im Speicher fuer das Protokollfenster.</summary>
internal static class Protokoll
{
    private static readonly object Sperre = new();
    private static readonly LinkedList<string> Puffer = new();
    private const int MaxZeilen = 3000;

    public static event Action<string>? NeueZeile;
    /// <summary>Zusaetzliche Ausgabe (Konsolenmodus).</summary>
    public static Action<string>? Ausgabe { get; set; }
    public static bool DateiAktiv { get; set; } = true;

    public static string Ordner => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AutoSchnell", "AutoPointer-Vergleich", "protokoll");

    public static void Schreibe(string text)
    {
        var jetzt = DateTime.Now;
        var zeilen = text.Replace("\r", "").Split('\n');
        var sb = new StringBuilder();
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
                    File.AppendAllText(Path.Combine(Ordner, $"protokoll-{jetzt:yyyy-MM-dd}.log"),
                        eintrag.Replace("\n", Environment.NewLine) + Environment.NewLine, Encoding.UTF8);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }
        Ausgabe?.Invoke(eintrag);
        NeueZeile?.Invoke(eintrag);
    }

    public static List<string> Letzte()
    {
        lock (Sperre) return Puffer.ToList();
    }

    public static void Aufraeumen(int tage = 14)
    {
        try
        {
            if (!Directory.Exists(Ordner)) return;
            foreach (var datei in Directory.GetFiles(Ordner, "protokoll-*.log"))
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
