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

        Protokoll.Schreibe($"Programmstart {Application.ProductVersion.Split('+')[0]} ({Environment.ProcessPath})");
        // Nur EINE Instanz. Befund 03.10.2026: "createdNew" taugte dafuer nicht — ein Startversuch, der
        // mit der Meldung "laeuft bereits" offen stand, hielt die Sperre am Leben, und jede neue Version
        // brach danach sofort ab. Jetzt zaehlt nur, ob eine LAUFENDE Instanz die Sperre wirklich besitzt;
        // ist sie abgestuerzt oder beendet worden (abandoned), startet die neue normal.
        using var mutex = new Mutex(false, @"Local\AutoSchnell.AutoPointerVergleich");
        bool erste;
        try { erste = mutex.WaitOne(0, false); }
        catch (AbandonedMutexException) { erste = true; }
        if (!erste)
        {
            // Wunsch Ahmad 03.10.2026: kein "laeuft bereits" mehr — das laufende Programm zeigt sein Fenster.
            if (TrayApp.ZeigenAnfordern())
            {
                Protokoll.Schreibe("Läuft bereits – Fenster nach vorne geholt.");
                return 0;
            }
            Protokoll.Schreibe("Läuft bereits – zweiter Start beendet.");
            MessageBox.Show("AutoPointer-Vergleich läuft bereits – Symbol unten rechts im Infobereich " +
                            "(Rechtsklick → „Mit AutoSchnell verbinden …“ bzw. „Beenden“).",
                "AutoPointer-Vergleich", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return 0;
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.ThreadException += (_, e) => Protokoll.Schreibe("Fehler: " + e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) => Protokoll.Schreibe("Fehler: " + e.ExceptionObject);
        try
        {
            Application.Run(new TrayApp(args.Contains("--probelauf"), server, minimiert: args.Contains("--autostart")));
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Start fehlgeschlagen: " + ex);
            MessageBox.Show("AutoPointer-Vergleich konnte nicht starten:\n\n" + ex.Message +
                            "\n\nDetails im Protokoll: " + Protokoll.Ordner, "AutoPointer-Vergleich",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            mutex.ReleaseMutex();
        }
        Protokoll.Schreibe("Programm beendet.");
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
        var lesung = await AutoPointerQuelle.LiesAnsichtAsync(ocr, ansicht, false, bilder == null ? null : (technik, kopf) =>
        {
            Directory.CreateDirectory(bilder);
            technik.Save(Path.Combine(bilder, "technik.png"));
            kopf?.Save(Path.Combine(bilder, "kopf.png"));
        });
        var dauer = (DateTime.Now - start).TotalMilliseconds;
        if (lesung == null) { Console.WriteLine("Tabelle nicht lesbar."); return 5; }

        Console.WriteLine($"Gelesen in {dauer:0} ms vom {(lesung.Weg == "Bildschirm" ? "Bildschirm (AutoPointer unberührt)" : "PrintWindow (Zeilen fehlten auf dem Bildschirm)")}, Texterkennung {ocr.Sprache}:");
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
            Console.WriteLine($"Inserat: {antwort.InseratUrl ?? "(Adresse unbekannt – für den Kaufvertrag selbst einfügen)"}");
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
            e.SchluesselSetzen(r.Schluessel, AutoSchnellDienst.KontoText(r.Name, r.Konto, r.Firma));
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
