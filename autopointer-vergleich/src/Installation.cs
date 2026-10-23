using System.Diagnostics;
using System.IO.MemoryMappedFiles;
using System.Text;

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
    /// <param name="versuche">1.5.9 (B): nach dem Ersetzen einer laufenden Version kann deren Datei noch einen Moment
    /// gesperrt sein — dann bis zu so viele Versuche mit je 500 ms Pause.</param>
    public static void Sicherstellen(int versuche = 1)
    {
        for (int versuch = 1; ; versuch++)
        {
            try
            {
                SicherstellenEinmal();
                return;
            }
            catch (IOException ex) when (versuch < versuche)
            {
                Protokoll.Schreibe($"Programm noch nicht an den festen Platz kopiert ({versuch}. Versuch, wird benutzt?): {ex.Message}");
                Thread.Sleep(500);
            }
            catch (IOException ex) { Protokoll.Schreibe("Programm nicht an den festen Platz kopiert (wird benutzt?): " + ex.Message); return; }
            catch (UnauthorizedAccessException ex) { Protokoll.Schreibe("Programm nicht an den festen Platz kopiert (kein Zugriff): " + ex.Message); return; }
            catch (Exception ex) { Protokoll.Schreibe("Programm nicht an den festen Platz kopiert: " + ex.Message); return; }
        }
    }

    private static void SicherstellenEinmal()
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

    /// <summary>Pruefung 08.10.2026 (1.5.9, F): "Wieder starten: Startmenü → AutoSchnell Vergleich" — dafuer muss es den
    /// Eintrag geben. Verknuepfung im Startmenue (Benutzer) auf die feste Kopie; gibt es die nicht, nichts. Nur, wenn sie
    /// fehlt oder woanders hinzeigt (nicht bei jedem Start neu schreiben). Fehler nur ins Protokoll.</summary>
    public static string StartmenuePfad =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), TrayApp.Name + ".lnk");

    /// <summary>Muss die Startmenue-Verknuepfung (neu) geschrieben werden? (rein, fuer Tests)</summary>
    internal static bool StartmenueAnlegen(bool kopieVorhanden, bool verknuepfungVorhanden, string? zielJetzt, string fester) =>
        kopieVorhanden && (!verknuepfungVorhanden || !IstFesterPfad(zielJetzt, fester));

    public static void StartmenueVerknuepfung()
    {
        object? shell = null, lnk = null;
        try
        {
            string datei = StartmenuePfad;
            bool vorhanden = File.Exists(datei);
            var typ = Type.GetTypeFromProgID("WScript.Shell");
            if (typ == null || !File.Exists(FesterPfad)) return;
            shell = Activator.CreateInstance(typ);
            if (shell == null) return;
            lnk = ((dynamic)shell).CreateShortcut(datei);
            dynamic v = lnk!;
            string? ziel = vorhanden ? (string)v.TargetPath : null;
            if (!StartmenueAnlegen(true, vorhanden, ziel, FesterPfad)) return;
            v.TargetPath = FesterPfad;
            v.Arguments = "";
            v.WorkingDirectory = Ordner;
            v.IconLocation = FesterPfad + ",0";
            v.Description = TrayApp.Name + " – Vergleiche aus AutoPointer öffnen";
            Directory.CreateDirectory(Path.GetDirectoryName(datei)!);
            v.Save();
            Protokoll.Schreibe("Startmenü-Eintrag angelegt: " + datei);
        }
        catch (Exception ex) { Protokoll.Schreibe("Startmenü-Eintrag nicht angelegt: " + ex.Message); }
        finally
        {
            if (OperatingSystem.IsWindows())
            {
                if (lnk != null) try { System.Runtime.InteropServices.Marshal.ReleaseComObject(lnk); } catch (Exception) { }
                if (shell != null) try { System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell); } catch (Exception) { }
            }
        }
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

/// <summary>Pruefung 08.10.2026 (1.5.9, B): ein Update muss das laufende Programm wirklich ersetzen. Vorher holte ein
/// zweiter Start nur das Fenster der laufenden Instanz nach vorne und beendete sich — wer die neue Version herunterlud und
/// startete, waehrend die alte (Autostart) lief, bekam weiter die alte; auch die feste Kopie konnte nicht ersetzt werden
/// (Datei in Benutzung). Jetzt veroeffentlicht die laufende Instanz ihre Version (geteilter Speicher) und wartet auf das
/// Signal <see cref="TrayApp.BeendenSignalName"/>; ein zweiter Start mit NEUERER Version fragt, beendet die alte und
/// startet normal weiter (Installation kopiert dann die neue Datei an den festen Platz).</summary>
internal static class LaufendesProgramm
{
    internal const string SpeicherName = @"Local\AutoSchnell.AutoPointerVergleich.Version";
    private const int Groesse = 256;
    /// <summary>So lange wartet ein zweiter Start hoechstens, bis die alte Instanz die Sperre freigibt.</summary>
    internal const int ErsetzenMs = 15_000;

    /// <param name="Version">"1.5.9" (ohne Zusatz) oder null, wenn unbekannt.</param>
    /// <param name="MitSignal">true: ab 1.5.9 (veroeffentlicht, beendet sich auf das Signal); false: aeltere Version, nur
    /// ueber die Prozessliste gefunden — die versteht das Signal nicht und wird beendet (nur nach "Ja").</param>
    internal sealed record Instanz(string? Version, int Pid, bool MitSignal);

    /// <summary>Text im geteilten Speicher: "1.5.9|1234". (rein, fuer Tests)</summary>
    internal static string Inhalt(string version, int pid) => $"{version}|{pid}";

    /// <summary>"1.5.9|1234" -> Instanz; Unlesbares -> null. (rein, fuer Tests)</summary>
    internal static Instanz? AusInhalt(string? inhalt)
    {
        var teile = (inhalt ?? "").Trim('\0', ' ').Split('|');
        if (teile.Length < 2 || teile[0].Length == 0 || !int.TryParse(teile[1], out int pid) || pid <= 0) return null;
        return new Instanz(teile[0], pid, MitSignal: true);
    }

    /// <summary>Soll ein zweiter Start die laufende Instanz ersetzen? Nur, wenn sie sicher aelter ist — gleiche, neuere
    /// oder unbekannte Version: wie bisher nur ihr Fenster zeigen. (rein, fuer Tests)</summary>
    internal static string? ErsetzenFrage(Instanz? laufend, string neu) =>
        laufend?.Version is { } alt && AutoSchnellDienst.NeuereVersion(neu, alt)
            ? $"{TrayApp.Name} {alt} läuft noch. Jetzt durch Version {neu} ersetzen?"
            : null;

    /// <summary>Bis 06.10.2026 hiess das Produkt "AutoSchnell AutoPointer-Vergleich". (rein, fuer Tests)</summary>
    internal static bool UnserProdukt(string? produkt) =>
        produkt is "AutoSchnell Vergleich" or "AutoSchnell AutoPointer-Vergleich";

    /// <summary>Die eigene Version veroeffentlichen (bleibt, solange das Rueckgabe-Objekt lebt). Fehler: null.</summary>
    public static IDisposable? Veroeffentlichen(string version, int pid, string name = SpeicherName)
    {
        try
        {
            var speicher = MemoryMappedFile.CreateOrOpen(name, Groesse);
            using var sicht = speicher.CreateViewAccessor(0, Groesse);
            var bytes = new byte[Groesse];
            var text = Encoding.UTF8.GetBytes(Inhalt(version, pid));
            Array.Copy(text, bytes, Math.Min(text.Length, Groesse - 1));
            sicht.WriteArray(0, bytes, 0, Groesse);
            return speicher;
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Version für einen zweiten Start nicht veröffentlicht: " + ex.Message);
            return null;
        }
    }

    /// <summary>Was die laufende Instanz (ab 1.5.9) veroeffentlicht hat; null = keine.</summary>
    public static Instanz? Gelesen(string name = SpeicherName)
    {
        try
        {
            using var speicher = MemoryMappedFile.OpenExisting(name, MemoryMappedFileRights.Read);
            using var sicht = speicher.CreateViewAccessor(0, Groesse, MemoryMappedFileAccess.Read);
            var bytes = new byte[Groesse];
            sicht.ReadArray(0, bytes, 0, Groesse);
            int ende = Array.IndexOf(bytes, (byte)0);
            return AusInhalt(Encoding.UTF8.GetString(bytes, 0, ende < 0 ? Groesse : ende));
        }
        catch (FileNotFoundException) { return null; }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Version der laufenden Instanz nicht lesbar: " + ex.Message);
            return null;
        }
    }

    /// <summary>Aeltere Versionen (vor 1.5.9) veroeffentlichen nichts — dann ueber die Prozessliste: gleiche Windows-
    /// Sitzung, anderer Prozess, Name beginnt mit "AutoSchnell" ("AutoSchnell-Vergleich (1).exe" aus Downloads auch),
    /// Produktname der Datei passt. null = nicht gefunden.</summary>
    public static Instanz? Suchen()
    {
        int ich = Environment.ProcessId;
        int sitzung;
        using (var eigener = Process.GetCurrentProcess()) sitzung = eigener.SessionId;
        var alle = Process.GetProcesses();
        try
        {
            foreach (var p in alle)
            {
                try
                {
                    if (p.Id == ich || p.SessionId != sitzung) continue;
                    if (!p.ProcessName.StartsWith("AutoSchnell", StringComparison.OrdinalIgnoreCase)) continue;
                    string? datei = p.MainModule?.FileName;
                    if (string.IsNullOrEmpty(datei)) continue;
                    var info = FileVersionInfo.GetVersionInfo(datei);
                    if (!UnserProdukt(info.ProductName)) continue;
                    return new Instanz(info.ProductVersion?.Split('+')[0], p.Id, MitSignal: false);
                }
                catch (Exception) { /* fremder oder geschuetzter Prozess: weiter */ }
            }
        }
        finally
        {
            foreach (var p in alle) p.Dispose();
        }
        return null;
    }

    /// <summary>Die laufende Instanz: erst das Veroeffentlichte (ab 1.5.9), sonst die Prozessliste (aeltere).</summary>
    public static Instanz? Finden() => Gelesen() ?? Suchen();

    /// <summary>Die laufende Instanz beenden und auf die Sperre warten (zusammen hoechstens <see cref="ErsetzenMs"/>).
    /// Ab 1.5.9 per Signal (sauber); antwortet sie nach 10 s nicht oder ist sie aelter (kein Signal), wird der Prozess
    /// beendet — nur nach "Ja" des Suchers und nur, wenn er wirklich dieses Programm ist. true = die Sperre gehoert jetzt
    /// diesem Start; dann ist auch die alte Datei frei (fuer Installation.Sicherstellen).</summary>
    public static bool Ersetzen(Mutex sperre, Instanz laufend)
    {
        // das Signal gibt es ab 1.5.9 (auch wenn das Veroeffentlichen dort nicht geklappt hat); aeltere kennen es nicht
        bool signal = SignalSetzen();
        if (signal) Protokoll.Schreibe($"Version {laufend.Version} gebeten, sich zu beenden.");
        else Abschiessen(laufend.Pid, laufend.Version);
        bool frei = Warten(sperre, signal ? ErsetzenMs - 5000 : ErsetzenMs);
        if (!frei && signal)
        {
            Protokoll.Schreibe($"Version {laufend.Version} hat sich nach 10 s nicht beendet – wird beendet.");
            Abschiessen(laufend.Pid, laufend.Version);
            frei = Warten(sperre, 5000);
        }
        if (frei) AufEndeWarten(laufend.Pid);
        return frei;
    }

    private static bool Warten(Mutex sperre, int ms)
    {
        try { return sperre.WaitOne(ms, false); }
        catch (AbandonedMutexException) { return true; }     // beendet, ohne freizugeben: gehoert jetzt uns
    }

    private static bool SignalSetzen()
    {
        try
        {
            using var signal = EventWaitHandle.OpenExisting(TrayApp.BeendenSignalName);
            return signal.Set();
        }
        catch (Exception ex) when (ex is WaitHandleCannotBeOpenedException or UnauthorizedAccessException or IOException)
        {
            return false;
        }
    }

    private static void Abschiessen(int pid, string? version)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            using var eigener = Process.GetCurrentProcess();
            string? datei = p.MainModule?.FileName;
            if (p.SessionId != eigener.SessionId || datei == null || !UnserProdukt(FileVersionInfo.GetVersionInfo(datei).ProductName))
            {
                Protokoll.Schreibe($"Prozess {pid} ist nicht {TrayApp.Name} – nicht beendet.");
                return;
            }
            p.Kill();
            Protokoll.Schreibe($"Alte Version {version} beendet (Prozess {pid}).");
        }
        catch (ArgumentException) { /* schon weg */ }
        catch (Exception ex) { Protokoll.Schreibe($"Alte Version {version} nicht beendet: {ex.Message}"); }
    }

    private static void AufEndeWarten(int pid)
    {
        try
        {
            using var p = Process.GetProcessById(pid);
            if (!p.WaitForExit(5000)) Protokoll.Schreibe($"Alte Version (Prozess {pid}) läuft nach 5 s noch.");
        }
        catch (ArgumentException) { /* schon weg */ }
        catch (Exception ex) { Protokoll.Schreibe("Auf das Ende der alten Version nicht gewartet: " + ex.Message); }
    }
}
