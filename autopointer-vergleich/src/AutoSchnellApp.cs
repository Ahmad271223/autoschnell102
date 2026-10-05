using System.Diagnostics;
using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 03.10.2026: "Vertrag" soll nicht einen Browser-Tab aufmachen, sondern die installierte
/// AutoSchnell-App — ist sie offen, dieses Fenster (die Web-App holt es per launch_handler "focus-existing" nach
/// vorne und uebernimmt das Auto), ist sie zu, startet sie. Nur ohne installierte App geht es in den Browser.
/// Erkannt wird die App an ihrer Verknuepfung (Edge/Chrome legen sie beim Installieren an: Desktop, Startmenue,
/// Autostart) mit "--app-id=" und der Adresse von AutoSchnell.</summary>
internal static class AutoSchnellApp
{
    /// <param name="PerAdresse">true: die Verknuepfung nennt die Adresse von AutoSchnell (sicher); false: nur am
    /// Namen erkannt (Chrome schreibt keine Adresse hinein).</param>
    internal sealed record Verknuepfung(string Programm, string Profil, string AppId, bool PerAdresse = false);

    /// <summary>Pruefbericht 03.10.2026 (Nr. 13): Name einer Chrome-Verknuepfung ohne Adresse — genau "AutoSchnell",
    /// hoechstens mit Zusatz in Klammern ("AutoSchnell (Profil 2)"). "AutoSchnell Test" o. Ae. zaehlt nicht mehr.</summary>
    private static readonly Regex AppName = new(@"^AutoSchnell(\s*\([^)]*\))?$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

    private static readonly string[] Browser = { "msedge_proxy.exe", "chrome_proxy.exe", "msedge.exe", "chrome.exe" };
    private static readonly Regex AppId = new(@"--app-id=([a-p]{32})", RegexOptions.Compiled);
    private static readonly Regex Profil = new(@"--profile-directory=(?:""([^""]+)""|(\S+))", RegexOptions.Compiled);

    /// <summary>Ist das eine Verknuepfung der AutoSchnell-App? (rein, fuer Tests)</summary>
    internal static Verknuepfung? AusVerknuepfung(string ziel, string argumente, string dateiname, string server)
    {
        if (string.IsNullOrWhiteSpace(ziel) || !Browser.Contains(Path.GetFileName(ziel), StringComparer.OrdinalIgnoreCase))
            return null;
        var id = AppId.Match(argumente ?? "");
        if (!id.Success) return null;
        string host = Uri.TryCreate(server, UriKind.Absolute, out var s) ? s.Host : "";
        bool unsere = host.Length > 0 && argumente!.Contains("://" + host, StringComparison.OrdinalIgnoreCase);
        bool perAdresse = unsere;
        // Chrome schreibt die Adresse nicht in die Verknuepfung — dann zaehlt der Name ("AutoSchnell.lnk")
        if (!unsere && !argumente!.Contains("--app-url=", StringComparison.OrdinalIgnoreCase))
            unsere = AppName.IsMatch(Path.GetFileNameWithoutExtension(dateiname).Trim());
        if (!unsere) return null;
        var p = Profil.Match(argumente!);
        string profil = p.Success ? (p.Groups[1].Success ? p.Groups[1].Value : p.Groups[2].Value) : "Default";
        return new Verknuepfung(ziel, profil, id.Groups[1].Value, perAdresse);
    }

    /// <summary>Nr. 13: aus allen gefundenen Verknuepfungen die richtige — eine mit der Adresse von AutoSchnell
    /// zuerst; sonst nur, wenn alle nur am Namen erkannten auf DIESELBE App zeigen. Mehrere verschiedene
    /// Kandidaten: nicht raten (null -> Browser).</summary>
    internal static Verknuepfung? Auswaehlen(IReadOnlyList<Verknuepfung> kandidaten)
    {
        var sicher = kandidaten.FirstOrDefault(k => k.PerAdresse);
        if (sicher != null) return sicher;
        if (kandidaten.Count == 0) return null;
        return kandidaten.Select(k => (k.AppId, k.Profil)).Distinct().Count() == 1 ? kandidaten[0] : null;
    }

    /// <summary>Befehlszeile: App mit diesem Ziel starten (so starten auch die Sprunglisten-Eintraege einer App).</summary>
    internal static string Argumente(Verknuepfung v, string url) =>
        $"--profile-directory=\"{v.Profil}\" --app-id={v.AppId} --app-launch-url-for-shortcuts-menu-item=\"{url}\"";

    /// <summary>Pruefung 05.10.2026 (Paket 3, F3): das Ergebnis der Suche wird behalten — gefunden 10 Minuten, nicht
    /// gefunden 1 Minute (die App kann gerade installiert werden) — und verworfen, sobald ein Start fehlschlaegt oder
    /// die Programmdatei weg ist. Vorher las jeder Klick auf "Vertrag" alle Verknuepfungen per COM neu.</summary>
    private static readonly object _cacheSperre = new();
    private static (string Server, Verknuepfung? Wahl, long Bis)? _cache;
    internal static readonly TimeSpan CacheGefunden = TimeSpan.FromMinutes(10), CacheNichtGefunden = TimeSpan.FromMinutes(1);

    /// <summary>Gilt der Eintrag noch? (rein, fuer Tests)</summary>
    internal static bool CacheGueltig((string Server, Verknuepfung? Wahl, long Bis)? eintrag, string server, long jetzt) =>
        eintrag is { } e && e.Server == server && jetzt < e.Bis;

    internal static void CacheLeeren()
    {
        lock (_cacheSperre) _cache = null;
    }

    public static Verknuepfung? Finden(string server)
    {
        lock (_cacheSperre)
            if (CacheGueltig(_cache, server, Environment.TickCount64) && (_cache!.Value.Wahl == null || File.Exists(_cache.Value.Wahl.Programm)))
                return _cache.Value.Wahl;
        var wahl = Suchen(server);
        lock (_cacheSperre)
            _cache = (server, wahl, Environment.TickCount64 + (long)(wahl != null ? CacheGefunden : CacheNichtGefunden).TotalMilliseconds);
        return wahl;
    }

    /// <summary>F3: die Suche (COM, Dateisystem) auf einem eigenen STA-Thread im Hintergrund — der Oberflaechen-Thread
    /// bleibt frei. WScript.Shell ist ein Apartment-Objekt; auf einem STA-Thread ohne Umweg ueber den Hauptthread.</summary>
    public static Task<Verknuepfung?> FindenAsync(string server)
    {
        var tcs = new TaskCompletionSource<Verknuepfung?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var t = new Thread(() =>
        {
            try { tcs.SetResult(Finden(server)); }
            catch (Exception ex) { tcs.SetException(ex); }
        }) { IsBackground = true, Name = "AutoSchnell-App suchen" };
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        return tcs.Task;
    }

    private static Verknuepfung? Suchen(string server)
    {
        var orte = new[]
        {
            Environment.GetFolderPath(Environment.SpecialFolder.Desktop),
            Environment.GetFolderPath(Environment.SpecialFolder.StartMenu),
            Environment.GetFolderPath(Environment.SpecialFolder.CommonStartMenu),
            Environment.GetFolderPath(Environment.SpecialFolder.Startup),
        };
        object? shell = null;
        try
        {
            var typ = Type.GetTypeFromProgID("WScript.Shell");
            if (typ == null) return null;
            shell = Activator.CreateInstance(typ);
            if (shell == null) return null;
            var kandidaten = new List<Verknuepfung>();
            foreach (var ort in orte.Where(o => o.Length > 0 && Directory.Exists(o)))
            {
                IEnumerable<string> dateien;
                try
                {
                    dateien = Directory.EnumerateFiles(ort, "*.lnk",
                        new EnumerationOptions { RecurseSubdirectories = true, MaxRecursionDepth = 3, IgnoreInaccessible = true });
                }
                catch (IOException) { continue; }
                catch (UnauthorizedAccessException) { continue; }
                foreach (var datei in dateien)
                {
                    object? lnk = null;
                    try
                    {
                        lnk = ((dynamic)shell).CreateShortcut(datei);
                        dynamic d = lnk!;
                        var v = AusVerknuepfung((string)d.TargetPath, (string)d.Arguments, datei, server);
                        if (v != null && File.Exists(v.Programm)) kandidaten.Add(v);
                    }
                    catch (Exception) { /* kaputte Verknuepfung: weiter */ }
                    finally
                    {
                        // F3: jedes COM-Objekt sofort freigeben — vorher blieben Hunderte bis zum Finalizer liegen
                        if (lnk != null && OperatingSystem.IsWindows())
                            try { System.Runtime.InteropServices.Marshal.ReleaseComObject(lnk); } catch (Exception) { }
                    }
                }
            }
            var wahl = Auswaehlen(kandidaten);
            if (wahl == null && kandidaten.Count > 1)
                Protokoll.Schreibe($"{kandidaten.Count} mögliche AutoSchnell-Apps gefunden, keine eindeutig – "
                                   + "Kaufvertrag öffnet im Browser.");
            return wahl;
        }
        catch (Exception ex) { Protokoll.Schreibe("AutoSchnell-App nicht gesucht: " + ex.Message); }
        finally
        {
            if (shell != null && OperatingSystem.IsWindows()) System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
        }
        return null;
    }

    /// <summary>In der installierten App oeffnen; false = keine App da (dann Browser). Die Suche laeuft im Hintergrund.</summary>
    public static async Task<bool> OeffnenAsync(string url, string server)
    {
        var v = await FindenAsync(server);
        if (v == null) return false;
        try
        {
            Process.Start(new ProcessStartInfo(v.Programm, Argumente(v, url)) { UseShellExecute = false })?.Dispose();
            Protokoll.Schreibe($"Kaufvertrag in der AutoSchnell-App geöffnet ({Path.GetFileName(v.Programm)}, App {v.AppId}).");
            return true;
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("AutoSchnell-App ließ sich nicht starten: " + ex.Message);
            CacheLeeren();                      // beim naechsten Klick neu suchen
            return false;
        }
    }
}
