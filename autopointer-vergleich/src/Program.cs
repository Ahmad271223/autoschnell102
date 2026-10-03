namespace AutoPointerVergleich;

internal static class Program
{
    /// <summary>
    ///   (ohne)           Hintergrundprogramm mit Symbol im Infobereich
    ///   --probelauf      wie oben, oeffnet aber keinen Browser (nur Protokoll)
    ///   --server &lt;url&gt;   anderer AutoSchnell-Server (Test), Standard app.auto-schnellkauf.de
    ///   --verbinden &lt;code&gt; ohne Fenster mit dem 6-stelligen Code aus AutoSchnell verbinden
    ///   --einmal         liest das gerade angezeigte Fahrzeug einmal und gibt die Werte
    ///                    aus; ist das Programm verbunden, fragt es den Server (Probelauf)
    ///                    nach den Links. --oeffnen oeffnet sie, --bilder &lt;Ordner&gt;
    ///                    speichert die Erkennungsbilder
    /// </summary>
    [STAThread]
    private static int Main(string[] args)
    {
        Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
        string? server = KonsolenModus.Argument(args, "--server");
        if (args.Contains("--einmal")) return KonsolenModus.EinmalAsync(args, server).GetAwaiter().GetResult();
        if (KonsolenModus.Argument(args, "--verbinden") is { } code)
            return KonsolenModus.VerbindenAsync(code, server).GetAwaiter().GetResult();

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
        Application.Run(new TrayApp(args.Contains("--probelauf"), server));
        return 0;
    }
}

internal static class KonsolenModus
{
    public static async Task<int> EinmalAsync(string[] args, string? server)
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
        Zuordner.Zuordnen(f, Katalog.Laden());
        foreach (var z in f.Beschreibung()) Console.WriteLine("  " + z);
        Console.WriteLine("  Schlüssel: " + f.Schluessel);
        Console.WriteLine("  An den Server: " + System.Text.Json.JsonSerializer.Serialize(AutoSchnellDienst.Nutzlast(f)));
        if (fehlt.Count > 0)
        {
            Console.WriteLine("Fahrzeug konnte nicht eindeutig erkannt werden – fehlt: " + string.Join(", ", fehlt));
            return 6;
        }
        var e = Einstellungen.Laden();
        if (!string.IsNullOrWhiteSpace(server)) e.Server = server.Trim().TrimEnd('/');
        var dienst = new AutoSchnellDienst(e.Server, () => e.Schluessel());
        if (!dienst.Verbunden)
        {
            Console.WriteLine($"Nicht mit AutoSchnell verbunden ({e.Server}) – keine Links.");
            return 0;
        }
        try
        {
            var antwort = await dienst.VergleichAsync(f, probelauf: true);
            Console.WriteLine($"Regeln: {antwort.Profil}");
            foreach (var v in antwort.Links) Console.WriteLine($"{v.Portal}: {v.Url}");
            foreach (var h in antwort.Hinweise) Console.WriteLine("Hinweis: " + h);
            if (args.Contains("--oeffnen") && antwort.Links.Count > 0)
                BrowserOeffner.Oeffne(antwort.Links.Select(l => l.Url).ToList(), e.Browser);
            return 0;
        }
        catch (DienstFehler ex)
        {
            Console.WriteLine("AutoSchnell: " + ex.Message);
            return 7;
        }
    }

    /// <summary>--verbinden 123456: ohne Fenster verbinden (Support, Tests).</summary>
    public static async Task<int> VerbindenAsync(string code, string? server)
    {
        Native.AttachConsole(-1);
        var e = Einstellungen.Laden();
        if (!string.IsNullOrWhiteSpace(server)) e.Server = server.Trim().TrimEnd('/');
        var dienst = new AutoSchnellDienst(e.Server, () => null);
        try
        {
            var (name, kennung) = AutoSchnellDienst.PcAngaben();
            var r = await dienst.VerbindenAsync(code, name, kennung);
            e.SchluesselSetzen(r.Schluessel, $"{r.Name} ({r.Konto}) · {r.Firma}");
            e.Speichern();
            Console.WriteLine($"Verbunden als {e.VerbundenAls} ({e.Server}).");
            return 0;
        }
        catch (DienstFehler ex)
        {
            Console.WriteLine("Nicht verbunden: " + ex.Message);
            return 8;
        }
    }

    public static string? Argument(string[] args, string name)
    {
        int i = Array.IndexOf(args, name);
        return i >= 0 && i + 1 < args.Length ? args[i + 1] : null;
    }
}
