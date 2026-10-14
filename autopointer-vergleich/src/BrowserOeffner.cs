using System.Diagnostics;
using Microsoft.Win32;

namespace AutoPointerVergleich;

/// <summary>Oeffnet die Vergleiche als neue Tabs. Edge/Chrome bekommen die
/// Adressen per Aufruf (neue Tabs im zuletzt benutzten Fenster, die letzte seit 1.5.10
/// in einem eigenen Aufruf, damit sie vorne liegt); der Standardbrowser je Adresse
/// ueber die Windows-Verknuepfung. Immer gilt: die LETZTE Adresse liegt vorne.</summary>
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
            // 1.5.10 (Befund Ahmad 08.10.2026 abends, "es oeffnet das Inserat statt der Vergleiche"): Chrome/Edge machen
            // beim Aufruf mit mehreren Adressen die ERSTE zum aktiven Tab, der Standardbrowser (unten, je Adresse) die
            // LETZTE. Vorne soll immer die letzte liegen (ein Vergleich, nicht das Inserat) — deshalb die letzte in
            // einem eigenen Aufruf hinterher.
            if (urls.Count > 1)
            {
                Starte(exe, urls.Take(urls.Count - 1));
                Thread.Sleep(300);
            }
            Starte(exe, new[] { urls[^1] });
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

    private static void Starte(string exe, IEnumerable<string> urls)
    {
        var psi = new ProcessStartInfo(exe) { UseShellExecute = false };
        foreach (var url in urls) psi.ArgumentList.Add(url);
        Process.Start(psi)?.Dispose();
    }

    /// <summary>Systemcheck (Nr. 8): gibt es einen Browser fuer die Vergleiche?</summary>
    public static bool BrowserVorhanden(BrowserWahl wahl)
    {
        if (Pfad("msedge.exe") != null || Pfad("chrome.exe") != null) return true;
        try
        {
            using var k = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice");
            return k?.GetValue("ProgId") is string { Length: > 0 };
        }
        catch (Exception) { return false; }
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

    /// <summary>Pruefung 09.10.2026 (Befund 2): was ueber die Fenster bekannt ist, bevor AutoPointer nach vorne geholt
    /// wird — per Win32 gelesen in <see cref="FensterLageLesen"/>, in Tests frei gesetzt.</summary>
    /// <param name="Gueltig">Das Handle ist (noch) das AutoPointer-Hauptfenster (IsWindow + Klasse TMainForm — Handles
    /// werden von Windows wiederverwendet).</param>
    /// <param name="Minimiert">IsIconic — ein minimiertes Fenster darf nie zum Vordergrund werden: Windows stellt es
    /// nicht wieder her, Tastatureingaben gingen danach ins Leere (Befund 2b).</param>
    /// <param name="SchonVorne">Das Vordergrundfenster gehoert schon zu AutoPointer (gleiche Prozess-ID) — dann nie an
    /// AutoPointers eigenen Thread haengen, nichts tun (Befund 2a).</param>
    /// <param name="Aktiviert">IsWindowEnabled — false, wenn AutoPointer einen Modal-Dialog offen hat (Befund 2c).</param>
    /// <param name="HatPopup">GetLastActivePopup nennt ein anderes Fenster (den Dialog) — das wird dann das Ziel.</param>
    /// <param name="Haengt">IsHungAppWindow(AutoPointer) — antwortet seit ≥ 5 s nicht: nichts anfassen.</param>
    /// <param name="VorneHaengt">IsHungAppWindow(Vordergrundfenster) — z. B. eingefrorener Browser: nicht anhaengen (Befund 2d).</param>
    /// <param name="FremderThread">Thread des Vordergrundfensters (0 = unbekannt).</param>
    /// <param name="EigenerThread">Unser Oberflaechen-Thread.</param>
    internal sealed record FensterLage(bool Gueltig, bool Minimiert, bool SchonVorne, bool Aktiviert, bool HatPopup,
                                       bool Haengt, bool VorneHaengt, uint FremderThread, uint EigenerThread);

    internal enum ZurueckZuZiel
    {
        /// <summary>Nichts anfassen (ungueltig, minimiert, haengt, deaktiviert ohne Dialog) — Ergebnis "nicht vorne".</summary>
        Nichts,
        /// <summary>AutoPointer liegt schon vorne — nichts tun, Ergebnis "vorne".</summary>
        SchonVorne,
        /// <summary>Das Hauptfenster nach vorne holen.</summary>
        Hauptfenster,
        /// <summary>Den offenen Dialog (GetLastActivePopup) nach vorne holen.</summary>
        Popup,
    }

    /// <param name="Anhaengen">AttachThreadInput an den Thread des Vordergrundfensters (nur fremd, nie unser eigener, nie
    /// an ein haengendes Fenster).</param>
    internal sealed record ZurueckZuPlan(ZurueckZuZiel Ziel, bool Anhaengen);

    /// <summary>Pruefung 09.10.2026 (Befund 2): die Entscheidung, was beim Nach-vorne-Holen zu tun ist — rein, ohne Win32,
    /// damit sie sich pruefen laesst.</summary>
    internal static ZurueckZuPlan Planen(FensterLage l)
    {
        if (!l.Gueltig || l.Minimiert || l.Haengt) return new ZurueckZuPlan(ZurueckZuZiel.Nichts, false);
        if (l.SchonVorne) return new ZurueckZuPlan(ZurueckZuZiel.SchonVorne, false);
        if (!l.Aktiviert && !l.HatPopup) return new ZurueckZuPlan(ZurueckZuZiel.Nichts, false);
        bool anhaengen = l.FremderThread != 0 && l.FremderThread != l.EigenerThread && !l.VorneHaengt;
        return new ZurueckZuPlan(l.Aktiviert ? ZurueckZuZiel.Hauptfenster : ZurueckZuZiel.Popup, anhaengen);
    }

    /// <summary>Die Lage per Win32 lesen (alles Abfragen ohne Nachricht an AutoPointer).</summary>
    private static FensterLage FensterLageLesen(IntPtr fenster, out IntPtr vorne, out IntPtr popup)
    {
        vorne = Native.GetForegroundWindow();
        popup = IntPtr.Zero;
        if (!AutoPointerFenster.IstHauptfenster(fenster))
            return new FensterLage(false, false, false, false, false, false, false, 0, 0);
        uint fremd = vorne == IntPtr.Zero ? 0 : Native.GetWindowThreadProcessId(vorne, out _);
        bool aktiviert = Native.IsWindowEnabled(fenster);
        if (!aktiviert) popup = Native.GetLastActivePopup(fenster);
        bool hatPopup = popup != IntPtr.Zero && popup != fenster && Native.IsWindow(popup);
        return new FensterLage(true, Native.IsIconic(fenster), AutoPointerFenster.ImVordergrund(fenster), aktiviert, hatPopup,
                               Native.IsHungAppWindow(fenster), vorne != IntPtr.Zero && Native.IsHungAppWindow(vorne),
                               fremd, Native.GetCurrentThreadId());
    }

    /// <summary>Holt AutoPointer wieder nach vorne (Einstellung "Danach zurueck zu AutoPointer" — fuer Arbeitsplaetze
    /// mit zwei Bildschirmen — und vor "Vergleichen", 1.5.9 I). Muss im Oberflaechen-Thread laufen (AttachThreadInput
    /// braucht dessen Eingabe-Warteschlange).
    /// Pruefung 09.10.2026 (Befund 1/2): kein BringWindowToTop mehr (synchron an AutoPointer, fror uns ein); minimiert,
    /// haengend oder schon vorne wird nichts angefasst; bei offenem Modal-Dialog wird der Dialog geholt; nie an unseren
    /// eigenen oder einen haengenden Thread anhaengen. Liefert, ob danach ein AutoPointer-Fenster vorne liegt.</summary>
    public static bool ZurueckZu(IntPtr fenster)
    {
        var lage = FensterLageLesen(fenster, out _, out IntPtr popup);
        var plan = Planen(lage);
        switch (plan.Ziel)
        {
            case ZurueckZuZiel.Nichts: return false;
            case ZurueckZuZiel.SchonVorne: return true;
        }
        IntPtr ziel = plan.Ziel == ZurueckZuZiel.Popup ? popup : fenster;
        bool verbunden = plan.Anhaengen && Native.AttachThreadInput(lage.EigenerThread, lage.FremderThread, true);
        try { Native.SetForegroundWindow(ziel); }
        finally
        {
            if (verbunden) Native.AttachThreadInput(lage.EigenerThread, lage.FremderThread, false);
        }
        return AutoPointerFenster.ImVordergrund(fenster);
    }
}
