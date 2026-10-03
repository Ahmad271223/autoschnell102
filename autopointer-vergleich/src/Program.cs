namespace AutoPointerVergleich;

internal static class Program
{
    /// <summary>
    ///   (ohne)           Hintergrundprogramm mit Symbol im Infobereich
    ///   --probelauf      wie oben, oeffnet aber keinen Browser (nur Protokoll)
    ///   --einmal         liest das gerade angezeigte Fahrzeug einmal und gibt
    ///                    Werte + Links aus (Fehlersuche); --oeffnen oeffnet sie,
    ///                    --bilder &lt;Ordner&gt; speichert die Erkennungsbilder
    /// </summary>
    [STAThread]
    private static int Main(string[] args)
    {
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        if (args.Contains("--einmal")) return KonsolenModus.EinmalAsync(args).GetAwaiter().GetResult();

        using var mutex = new Mutex(true, @"Local\AutoSchnell.AutoPointerVergleich", out bool erste);
        if (!erste)
        {
            MessageBox.Show("AutoPointer-Vergleich läuft bereits – Symbol unten rechts im Infobereich.",
                "AutoPointer-Vergleich", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.ThreadException += (_, e) => Protokoll.Schreibe("Fehler: " + e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) => Protokoll.Schreibe("Fehler: " + e.ExceptionObject);
        Application.Run(new TrayApp(args.Contains("--probelauf")));
        return 0;
    }
}

internal static class KonsolenModus
{
    public static async Task<int> EinmalAsync(string[] args)
    {
        Native.AttachConsole(-1);
        Protokoll.DateiAktiv = false;
        Protokoll.Ausgabe = z => Console.WriteLine(z);
        string? bilder = Argument(args, "--bilder");

        var haupt = AutoPointerFenster.FindeHauptfenster();
        if (haupt == IntPtr.Zero) { Console.WriteLine("AutoPointer läuft nicht."); return 2; }
        var ansicht = AutoPointerFenster.FindeDetails(haupt);
        if (ansicht == null) { Console.WriteLine("AutoPointer zeigt rechts kein Fahrzeug (Reiter „Übersicht“)."); return 3; }
        var ocr = TextErkennung.Erstelle(out string fehler);
        if (ocr == null) { Console.WriteLine(fehler); return 4; }

        var start = DateTime.Now;
        using var technik = AutoPointerFenster.Fotografiere(ansicht.TechnikTabelle);
        using var kopf = AutoPointerFenster.Fotografiere(ansicht.KopfTabelle);
        if (technik == null) { Console.WriteLine("Tabelle nicht lesbar."); return 5; }
        var lesung = await AutoPointerQuelle.LiesBilderAsync(ocr, technik, kopf, Native.GetDpiForWindow(ansicht.TechnikTabelle), false);
        var dauer = (DateTime.Now - start).TotalMilliseconds;
        if (bilder != null)
        {
            Directory.CreateDirectory(bilder);
            technik.Save(Path.Combine(bilder, "technik.png"));
            kopf?.Save(Path.Combine(bilder, "kopf.png"));
        }

        Console.WriteLine($"Gelesen in {dauer:0} ms (Texterkennung {ocr.Sprache}):");
        Console.WriteLine("  " + lesung.Rohtext);
        var f = lesung.Fahrzeug;
        var fehlt = DetailLeser.Fehlend(f);
        var katalog = Katalog.Laden();
        var zuordnung = LinkBauer.Zuordnen(f, katalog);
        foreach (var z in f.Beschreibung()) Console.WriteLine("  " + z);
        Console.WriteLine("  Schlüssel: " + f.Schluessel);
        if (fehlt.Count > 0)
        {
            Console.WriteLine("Fahrzeug konnte nicht eindeutig erkannt werden – fehlt: " + string.Join(", ", fehlt));
            return 6;
        }
        var hinweise = new List<string>();
        var e = Einstellungen.Laden();
        var links = LinkBauer.Bauen(f, zuordnung, e, hinweise);
        foreach (var v in links) Console.WriteLine($"{v.Portal}: {v.Url}");
        foreach (var h in hinweise) Console.WriteLine("Hinweis: " + h);
        if (args.Contains("--oeffnen") && links.Count > 0) BrowserOeffner.Oeffne(links.Select(l => l.Url).ToList(), e.Browser);
        return 0;
    }

    private static string? Argument(string[] args, string name)
    {
        int i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
