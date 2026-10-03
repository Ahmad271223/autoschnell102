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
    internal sealed record Verknuepfung(string Programm, string Profil, string AppId);

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
        // Chrome schreibt die Adresse nicht in die Verknuepfung — dann zaehlt der Name ("AutoSchnell.lnk")
        if (!unsere && !argumente!.Contains("--app-url=", StringComparison.OrdinalIgnoreCase))
            unsere = Path.GetFileNameWithoutExtension(dateiname).StartsWith("AutoSchnell", StringComparison.OrdinalIgnoreCase);
        if (!unsere) return null;
        var p = Profil.Match(argumente!);
        string profil = p.Success ? (p.Groups[1].Success ? p.Groups[1].Value : p.Groups[2].Value) : "Default";
        return new Verknuepfung(ziel, profil, id.Groups[1].Value);
    }

    /// <summary>Befehlszeile: App mit diesem Ziel starten (so starten auch die Sprunglisten-Eintraege einer App).</summary>
    internal static string Argumente(Verknuepfung v, string url) =>
        $"--profile-directory=\"{v.Profil}\" --app-id={v.AppId} --app-launch-url-for-shortcuts-menu-item=\"{url}\"";

    public static Verknuepfung? Finden(string server)
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
                    try
                    {
                        dynamic lnk = ((dynamic)shell).CreateShortcut(datei);
                        var v = AusVerknuepfung((string)lnk.TargetPath, (string)lnk.Arguments, datei, server);
                        if (v != null && File.Exists(v.Programm)) return v;
                    }
                    catch (Exception) { /* kaputte Verknuepfung: weiter */ }
                }
            }
        }
        catch (Exception ex) { Protokoll.Schreibe("AutoSchnell-App nicht gesucht: " + ex.Message); }
        finally
        {
            if (shell != null && OperatingSystem.IsWindows()) System.Runtime.InteropServices.Marshal.FinalReleaseComObject(shell);
        }
        return null;
    }

    /// <summary>In der installierten App oeffnen; false = keine App da (dann Browser).</summary>
    public static bool Oeffnen(string url, string server)
    {
        var v = Finden(server);
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
            return false;
        }
    }
}
