using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;

namespace AutoPointerVergleich;

/// <summary>Die beiden Tabellen der rechten Detailansicht in AutoPointer.</summary>
/// <remarks>
/// AutoPointer ist eine Delphi-Anwendung mit DevExpress-Tabellen (TcxGrid).
/// Gepruefte Lage am 03.10.2026 (AutoPointer Premium 3.9.9.9325): Weder
/// UI Automation noch MSAA liefern den Text der Tabellenzellen - die Zellen
/// werden gezeichnet, nicht als Steuerelemente angelegt. Deshalb:
/// Fenster-Handles finden (Klassennamen + Ueberschrift "Technische Daten"),
/// die Tabellen abfotografieren (zuerst nur der Bildschirminhalt, PrintWindow
/// nur wenn Zeilen fehlen) und mit der Windows-Texterkennung lesen. Die Handles machen das unabhaengig von Bildschirm-
/// aufloesung, Fenstergroesse und Position.
/// </remarks>
/// <param name="Beschreibung">Textfeld unter "Beschreibung" (THTMLStaticText) oder Zero — seit 1.5.1 gelesen,
/// damit der Server das Modell notfalls dort findet (Befund 04.10.2026: "meinen Mercedes C 300 e").</param>
internal sealed record DetailAnsicht(IntPtr Hauptfenster, IntPtr TechnikTabelle, IntPtr KopfTabelle,
                                     IntPtr Beschreibung = default);

internal static class AutoPointerFenster
{
    private static readonly string[] Prozessnamen = { "aprun" };

    /// <summary>Hauptfenster von AutoPointer oder <see cref="IntPtr.Zero"/>.</summary>
    public static IntPtr FindeHauptfenster()
    {
        IntPtr treffer = IntPtr.Zero;
        Native.EnumWindows((h, _) =>
        {
            if (Native.Klasse(h) != "TMainForm") return true;
            Native.GetWindowThreadProcessId(h, out uint pid);
            if (IstAutoPointerProzess(pid) || Native.Text(h).StartsWith("AutoPointer", StringComparison.OrdinalIgnoreCase))
            {
                treffer = h;
                return false;
            }
            return true;
        }, IntPtr.Zero);
        return treffer;
    }

    private static bool IstAutoPointerProzess(uint pid)
    {
        try
        {
            using var p = Process.GetProcessById((int)pid);
            if (Prozessnamen.Contains(p.ProcessName, StringComparer.OrdinalIgnoreCase)) return true;
            // Pruefbericht 03.10.2026 (Nr. 7): benennt ein Update die Programmdatei um, erkennt der Pfad der
            // Installation (Ordner "AutoPointer" bzw. Hersteller "vitdev") es trotzdem — zusaetzlich zum Fenstertitel.
            return IstAutoPointerPfad(ProgrammPfad(p));
        }
        catch (ArgumentException) { return false; }
        catch (InvalidOperationException) { return false; }
    }

    private static string? ProgrammPfad(Process p)
    {
        try { return p.MainModule?.FileName; }
        catch (Exception) { return null; }     // anderer Benutzer/hoehere Rechte: Pfad nicht lesbar
    }

    /// <summary>Liegt die Programmdatei in einer AutoPointer-Installation? (rein, fuer Tests)</summary>
    internal static bool IstAutoPointerPfad(string? pfad) =>
        !string.IsNullOrWhiteSpace(pfad)
        && (pfad.Contains(@"\AutoPointer", StringComparison.OrdinalIgnoreCase)
            || pfad.Contains(@"\vitdev", StringComparison.OrdinalIgnoreCase));

    /// <summary>Sucht die sichtbare Detailansicht (Reiter "Übersicht"). null, wenn
    /// rechts gerade kein Fahrzeug bzw. ein anderer Reiter angezeigt wird.</summary>
    public static DetailAnsicht? FindeDetails(IntPtr hauptfenster)
    {
        if (hauptfenster == IntPtr.Zero || Native.IsIconic(hauptfenster)) return null;
        var kinder = Native.Kinder(hauptfenster);
        var eltern = kinder.ToDictionary(h => h, Native.GetParent);
        var klasse = new Dictionary<IntPtr, string>();
        string K(IntPtr h) => klasse.TryGetValue(h, out var k) ? k : klasse[h] = Native.Klasse(h);
        IEnumerable<IntPtr> DirekteKinder(IntPtr p, string kl) => kinder.Where(h => eltern[h] == p && K(h) == kl);

        foreach (var kopfzeile in kinder.Where(h => K(h) == "TJvNavPanelHeader"
                                                  && Native.IsWindowVisible(h)
                                                  && Native.Text(h).Trim() == "Technische Daten"))
        {
            var panel = eltern[kopfzeile];
            var grid = DirekteKinder(panel, "TcxGrid").FirstOrDefault();
            var technik = grid == IntPtr.Zero ? IntPtr.Zero : DirekteKinder(grid, "TcxGridSite").FirstOrDefault();
            if (!Brauchbar(technik)) continue;

            // Hoch bis zum Reiter-Steuerelement; dessen Elternteil ist der
            // Detail-Bereich, in dem oberhalb die Kopf-Tabelle (Quelle, Titel,
            // Preis) liegt.
            IntPtr reiter = panel;
            while (reiter != IntPtr.Zero && reiter != hauptfenster && K(reiter) != "TPageControl")
                reiter = eltern.TryGetValue(reiter, out var e) ? e : IntPtr.Zero;
            if (reiter == IntPtr.Zero || reiter == hauptfenster) continue;
            var bereich = eltern[reiter];

            IntPtr kopf = IntPtr.Zero;
            int kopfOben = int.MaxValue;
            foreach (var site in kinder.Where(h => K(h) == "TcxGridSite" && Brauchbar(h)))
            {
                if (!IstNachfahre(site, bereich, eltern) || IstNachfahre(site, reiter, eltern)) continue;
                Native.GetWindowRect(site, out var r);
                if (r.Top < kopfOben) { kopfOben = r.Top; kopf = site; }
            }
            IntPtr beschreibung = IntPtr.Zero;
            foreach (var bk in kinder.Where(h => K(h) == "TJvNavPanelHeader" && Native.IsWindowVisible(h)
                                                 && Native.Text(h).Trim() == "Beschreibung"))
            {
                var bp = eltern[bk];
                if (!IstNachfahre(bp, bereich, eltern)) continue;
                beschreibung = kinder.FirstOrDefault(h => eltern[h] == bp && h != bk
                                                          && K(h) != "TJvNavPanelHeader" && Brauchbar(h));
                if (beschreibung != IntPtr.Zero) break;
            }
            return new DetailAnsicht(hauptfenster, technik, kopf, beschreibung);
        }
        return null;
    }

    private static bool Brauchbar(IntPtr h)
    {
        if (h == IntPtr.Zero || !Native.IsWindowVisible(h)) return false;
        Native.GetWindowRect(h, out var r);
        return r.Width > 40 && r.Height > 20;
    }

    private static bool IstNachfahre(IntPtr h, IntPtr vorfahre, Dictionary<IntPtr, IntPtr> eltern)
    {
        for (var p = eltern.GetValueOrDefault(h); p != IntPtr.Zero; p = eltern.GetValueOrDefault(p))
            if (p == vorfahre) return true;
        return false;
    }

    public static bool NochGueltig(DetailAnsicht d) =>
        Native.IsWindow(d.TechnikTabelle) && Brauchbar(d.TechnikTabelle) && !Native.IsIconic(d.Hauptfenster)
        && (d.KopfTabelle == IntPtr.Zero || Brauchbar(d.KopfTabelle));

    /// <summary>Laesst die Tabelle sich selbst in ein Bild zeichnen (PrintWindow).
    /// Liefert auch Zeilen, die in AutoPointer gerade weggescrollt bzw. verdeckt
    /// sind (Tueren, Inserat-ID ...).</summary>
    public static Bitmap? Fotografiere(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero) return null;
        return Native.ImDpiKontext(hwnd, () =>
        {
            if (!Native.GetClientRect(hwnd, out var r) || r.Width <= 0 || r.Height <= 0) return null;
            var bmp = new Bitmap(r.Width, r.Height, PixelFormat.Format32bppRgb);
            using (var g = Graphics.FromImage(bmp))
            {
                g.Clear(Color.White);
                IntPtr dc = g.GetHdc();
                try { Native.PrintWindow(hwnd, dc, 0); }
                finally { g.ReleaseHdc(dc); }
            }
            return bmp;
        });
    }

    /// <summary>Eigene Fenster, die ueber AutoPointer liegen koennen (die Leiste, immer im Vordergrund).
    /// Setzt TrayApp; liefert Handles, kein Zugriff auf Steuerelemente (laeuft im Lese-Thread).</summary>
    public static Func<IEnumerable<IntPtr>> EigeneFenster { get; set; } = () => Array.Empty<IntPtr>();

    /// <summary>Pruefbericht 03.10.2026 (Nr. 10): eigene Fenster (die Leiste) kurz unsichtbar machen (true) bzw.
    /// wieder zeigen (false), damit das Bildschirm-Abbild nur AutoPointer zeigt — ohne AutoPointer selbst zeichnen
    /// zu lassen (PrintWindow, loeste dort Abstuerze aus). Setzt TrayApp; null = nichts zu tun.</summary>
    public static Action<bool>? EigeneAusblenden { get; set; }

    /// <summary>Liegt ein eigenes Fenster ueber diesem? Dann zeigt der Bildschirm dort nicht nur AutoPointer —
    /// lieber PrintWindow als eine Leiste mitlesen.</summary>
    public static bool Verdeckt(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero || !Native.GetWindowRect(hwnd, out var r)) return false;
        foreach (var e in EigeneFenster())
            if (e != IntPtr.Zero && Native.IsWindowVisible(e) && Native.GetWindowRect(e, out var o)
                && o.Left < r.Right && o.Right > r.Left && o.Top < r.Bottom && o.Bottom > r.Top)
                return true;
        return false;
    }

    /// <summary>Kopie dessen, was die Tabelle gerade anzeigt (BitBlt) — anders als
    /// <see cref="Fotografiere"/> geht dabei KEINE Nachricht an AutoPointer, AutoPointer
    /// zeichnet nichts extra. Weggescrollte Zeilen fehlen natuerlich.</summary>
    public static Bitmap? Abbild(IntPtr hwnd)
    {
        if (hwnd == IntPtr.Zero) return null;
        return Native.ImDpiKontext(hwnd, () =>
        {
            if (!Native.GetClientRect(hwnd, out var r) || r.Width <= 0 || r.Height <= 0) return null;
            var bmp = new Bitmap(r.Width, r.Height, PixelFormat.Format32bppRgb);
            using (var g = Graphics.FromImage(bmp))
            {
                IntPtr ziel = g.GetHdc();
                IntPtr quelle = Native.GetDC(hwnd);
                try { Native.BitBlt(ziel, 0, 0, r.Width, r.Height, quelle, 0, 0, Native.SRCCOPY); }
                finally
                {
                    Native.ReleaseDC(hwnd, quelle);
                    g.ReleaseHdc(ziel);
                }
            }
            return bmp;
        });
    }

    /// <summary>Billige Pruefsumme des sichtbaren Inhalts (BitBlt, kein Aufruf in
    /// AutoPointer hinein) - nur um Aenderungen zu bemerken.</summary>
    public static ulong Pruefsumme(IntPtr hwnd)
    {
        using var bmp = Abbild(hwnd);
        return bmp == null ? 0UL : Bildsumme(bmp);
    }

    internal static unsafe ulong Bildsumme(Bitmap bmp)
    {
        var daten = bmp.LockBits(new Rectangle(0, 0, bmp.Width, bmp.Height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
        try
        {
            ulong h = 14695981039346656037UL;           // FNV-1a 64
            for (int y = 0; y < daten.Height; y++)
            {
                uint* zeile = (uint*)((byte*)daten.Scan0 + (long)y * daten.Stride);
                for (int x = 0; x < daten.Width; x++)
                {
                    h ^= zeile[x] & 0x00FFFFFFu;
                    h *= 1099511628211UL;
                }
            }
            return h ^ (ulong)(bmp.Width * 31 + bmp.Height);
        }
        finally { bmp.UnlockBits(daten); }
    }
}
