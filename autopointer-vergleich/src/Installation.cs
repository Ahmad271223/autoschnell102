using System.Diagnostics;

namespace AutoPointerVergleich;

/// <summary>Pruefung 05.10.2026 (Paket 2, A6): fester Installationsordner. Die EXE wird per Download verteilt und
/// landet in "Downloads", oft sogar in der ZIP-Vorschau (%TEMP%\Temp1_…) — von dort zeigte dann der Autostart auf
/// eine Datei, die beim naechsten Windows-Start weg oder veraltet war. Jetzt kopiert sich das Programm beim Start
/// nach %LOCALAPPDATA%\Programs\AutoSchnell-Vergleich\ (wenn die Kopie dort nicht neuer ist) und der Autostart zeigt
/// nur noch dorthin. Das laufende Programm laeuft weiter, wo es gestartet wurde.</summary>
internal static class Installation
{
    public const string Dateiname = "AutoSchnell-Vergleich.exe";

    public static string Ordner =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "AutoSchnell-Vergleich");

    public static string FesterPfad => Path.Combine(Ordner, Dateiname);

    /// <summary>Liegt die Datei schon am festen Platz? (rein, fuer Tests)</summary>
    internal static bool IstFesterPfad(string? pfad, string fester) =>
        !string.IsNullOrEmpty(pfad) && string.Equals(Voll(pfad), Voll(fester), StringComparison.OrdinalIgnoreCase);

    /// <summary>Temporaerer Ort (ZIP-Vorschau "Temp1_…", %TEMP%) oder "Downloads" — dort darf der Autostart nie
    /// hinzeigen: die Datei ist beim naechsten Windows-Start weg, verschoben oder eine aeltere. (rein, fuer Tests)</summary>
    internal static bool IstFluechtig(string? pfad, string? tempOrdner = null, string? profilOrdner = null)
    {
        if (string.IsNullOrWhiteSpace(pfad)) return true;
        string voll = Voll(pfad);
        string temp = Voll(tempOrdner ?? Path.GetTempPath());
        if (Darunter(voll, temp)) return true;
        string profil = profilOrdner ?? Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (profil.Length > 0 && Darunter(voll, Path.Combine(profil, "Downloads"))) return true;
        // Windows packt die ZIP-Vorschau nach %TEMP%\Temp1_<name>.zip\ — auch wenn %TEMP% woanders liegt
        return voll.Contains(@"\Temp1_", StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>Darf die Kopie am festen Platz ueberschrieben werden? Ja, wenn dort nichts liegt oder die Version
    /// dort aelter oder gleich ist. Eine NEUERE Kopie bleibt (der Sucher hat versehentlich eine alte Datei aus
    /// "Downloads" gestartet). (rein, fuer Tests)</summary>
    internal static bool Ueberschreiben(string? versionDort, string laufendeVersion) =>
        !AutoSchnellDienst.NeuereVersion(versionDort, laufendeVersion);

    /// <summary>Wohin der Autostart zeigt: auf die feste Kopie, sobald es sie gibt; sonst auf die laufende Datei,
    /// aber nie auf Temp/Downloads (dann null = Eintrag nicht anfassen). (rein, fuer Tests)</summary>
    internal static string? AutostartZiel(string? laufend, bool kopieVorhanden, string fester,
                                          string? tempOrdner = null, string? profilOrdner = null)
    {
        if (kopieVorhanden) return fester;
        if (string.IsNullOrEmpty(laufend) || IstFluechtig(laufend, tempOrdner, profilOrdner)) return null;
        return laufend;
    }

    public static string? AutostartZiel() =>
        AutostartZiel(Environment.ProcessPath, File.Exists(FesterPfad), FesterPfad);

    /// <summary>Beim Programmstart: Kopie am festen Platz anlegen bzw. aktualisieren. Fehler werden nur protokolliert
    /// (z. B. "wird von einem anderen Prozess verwendet" — die Kopie laeuft gerade selbst).</summary>
    public static void Sicherstellen()
    {
        try
        {
            string? laufend = Environment.ProcessPath;
            if (string.IsNullOrEmpty(laufend) || !File.Exists(laufend)) return;
            if (IstFesterPfad(laufend, FesterPfad)) return;
            string eigene = Application.ProductVersion.Split('+')[0];
            if (File.Exists(FesterPfad))
            {
                string? dort = Version(FesterPfad);
                if (!Ueberschreiben(dort, eigene))
                {
                    Protokoll.Schreibe($"Am festen Platz liegt schon eine neuere Version ({dort}, diese: {eigene}) – nicht überschrieben: {FesterPfad}");
                    return;
                }
                if (dort == eigene && new FileInfo(FesterPfad).Length == new FileInfo(laufend).Length) return;   // schon gleich
            }
            Directory.CreateDirectory(Ordner);
            File.Copy(laufend, FesterPfad, overwrite: true);
            Protokoll.Schreibe($"Programm nach {FesterPfad} kopiert (Version {eigene}; gestartet aus {laufend}).");
        }
        catch (IOException ex) { Protokoll.Schreibe("Programm nicht an den festen Platz kopiert (wird benutzt?): " + ex.Message); }
        catch (UnauthorizedAccessException ex) { Protokoll.Schreibe("Programm nicht an den festen Platz kopiert (kein Zugriff): " + ex.Message); }
        catch (Exception ex) { Protokoll.Schreibe("Programm nicht an den festen Platz kopiert: " + ex.Message); }
    }

    private static string? Version(string pfad)
    {
        try { return FileVersionInfo.GetVersionInfo(pfad).ProductVersion?.Split('+')[0]; }
        catch (Exception) { return null; }
    }

    private static string Voll(string pfad)
    {
        try { return Path.GetFullPath(pfad).TrimEnd('\\'); }
        catch (Exception) { return pfad; }
    }

    private static bool Darunter(string voll, string ordner)
    {
        string o = Voll(ordner);
        return o.Length > 0 && voll.StartsWith(o + "\\", StringComparison.OrdinalIgnoreCase);
    }
}
