using System.Diagnostics;
using Microsoft.Win32;

namespace AutoPointerVergleich;

/// <summary>Oeffnet die Vergleiche als neue Tabs. Edge/Chrome bekommen alle
/// Adressen in einem Aufruf (neue Tabs im zuletzt benutzten Fenster); der
/// Standardbrowser je Adresse ueber die Windows-Verknuepfung.</summary>
internal static class BrowserOeffner
{
    public static void Oeffne(IReadOnlyList<string> urls, BrowserWahl wahl)
    {
        if (urls.Count == 0) return;
        string? exe = wahl switch
        {
            BrowserWahl.Edge => Pfad("msedge.exe"),
            BrowserWahl.Chrome => Pfad("chrome.exe"),
            _ => null,
        };
        if (exe != null)
        {
            var psi = new ProcessStartInfo(exe) { UseShellExecute = false };
            foreach (var url in urls) psi.ArgumentList.Add(url);
            Process.Start(psi)?.Dispose();
            return;
        }
        if (wahl != BrowserWahl.Standard)
            Protokoll.Schreibe($"{wahl} nicht gefunden – nehme den Standardbrowser.");
        for (int i = 0; i < urls.Count; i++)
        {
            Process.Start(new ProcessStartInfo(urls[i]) { UseShellExecute = true })?.Dispose();
            // kurze Pause, damit die Tabs in der richtigen Reihenfolge entstehen
            if (i < urls.Count - 1) Thread.Sleep(250);
        }
    }

    private static string? Pfad(string exe)
    {
        foreach (var basis in new[] { Registry.CurrentUser, Registry.LocalMachine })
        {
            try
            {
                using var k = basis.OpenSubKey($@"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}");
                if (k?.GetValue(null) is string p && File.Exists(p.Trim('"'))) return p.Trim('"');
            }
            catch (System.Security.SecurityException) { }
        }
        return null;
    }

    /// <summary>Holt AutoPointer wieder nach vorne (Einstellung "Danach zurueck zu
    /// AutoPointer") - fuer Arbeitsplaetze mit zwei Bildschirmen.</summary>
    public static void ZurueckZu(IntPtr fenster)
    {
        if (fenster == IntPtr.Zero || !Native.IsWindow(fenster)) return;
        IntPtr vorne = Native.GetForegroundWindow();
        uint fremd = Native.GetWindowThreadProcessId(vorne, out _);
        uint ich = Native.GetCurrentThreadId();
        bool verbunden = fremd != 0 && fremd != ich && Native.AttachThreadInput(ich, fremd, true);
        try
        {
            Native.BringWindowToTop(fenster);
            Native.SetForegroundWindow(fenster);
        }
        finally
        {
            if (verbunden) Native.AttachThreadInput(ich, fremd, false);
        }
    }
}
