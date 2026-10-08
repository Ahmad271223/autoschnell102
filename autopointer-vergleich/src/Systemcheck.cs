namespace AutoPointerVergleich;

internal enum PruefStufe { Ok, Hinweis, Fehler }

internal sealed record PruefPunkt(string Name, PruefStufe Stufe, string Text);

/// <summary>Pruefbericht 03.10.2026 (Nr. 6 und 8): "Laeuft alles?" auf einen Blick — Windows, Texterkennung,
/// AutoSchnell erreichbar, Verbindung/Abo, Programmversion, AutoPointer, eine Probe-Lesung des angezeigten Autos
/// samt Probe-Vergleich beim Server (ohne Browser), Browser und installierte App. Ersetzt keinen Test mit dem
/// echten AutoPointer in der CI (der ist dort nicht moeglich), macht ihn aber auf jedem PC in Sekunden moeglich:
/// Fenster-Knopf "Systemcheck", Menue, oder <c>AutoSchnell-Vergleich.exe --systemcheck</c>.</summary>
internal static class Systemcheck
{
    /// <summary>Was die Pruefung ermittelt hat (aus den echten Quellen bzw. in Tests von Hand).</summary>
    internal sealed record Befund
    {
        public Version? Windows { get; init; }
        public string? OcrSprache { get; init; }
        public string? OcrFehler { get; init; }
        public bool ServerErreichbar { get; init; }
        public string Server { get; init; } = "";
        public bool Verbunden { get; init; }
        public StatusAntwort? Status { get; init; }
        public string? StatusFehler { get; init; }
        public string EigeneVersion { get; init; } = "";
        public bool AutoPointerLaeuft { get; init; }
        public bool FahrzeugAngezeigt { get; init; }
        public Fahrzeug? Gelesen { get; init; }
        public string? LeseFehler { get; init; }
        public VergleichAntwort? Probe { get; init; }
        public string? ProbeFehler { get; init; }
        public bool BrowserGefunden { get; init; }
        public bool AppInstalliert { get; init; }
    }

    /// <summary>Mindestens Windows 10 1809 (Build 17763) — darunter fehlt die Windows-Texterkennung.</summary>
    internal static readonly Version MindestWindows = new(10, 0, 17763);

    /// <summary>Befund -> Liste fuer die Anzeige (rein, fuer Tests).</summary>
    internal static List<PruefPunkt> Auswerten(Befund b)
    {
        var p = new List<PruefPunkt>();
        p.Add(b.Windows != null && b.Windows >= MindestWindows
            ? new("Windows", PruefStufe.Ok, $"Version {b.Windows}")
            : new("Windows", PruefStufe.Fehler, $"Version {b.Windows?.ToString() ?? "?"} – mindestens Windows 10 (1809) nötig."));
        p.Add(b.OcrSprache != null
            ? new("Texterkennung", PruefStufe.Ok, $"Windows-Texterkennung bereit ({b.OcrSprache}).")
            : new("Texterkennung", PruefStufe.Fehler, b.OcrFehler ?? "Windows-Texterkennung nicht verfügbar."));
        p.Add(b.ServerErreichbar
            ? new("AutoSchnell", PruefStufe.Ok, $"erreichbar ({b.Server}).")
            : new("AutoSchnell", PruefStufe.Fehler, $"nicht erreichbar ({b.Server}) – Internet bzw. Firewall prüfen."));
        if (!b.Verbunden)
            p.Add(new("Verbindung", PruefStufe.Fehler, "nicht verbunden – auf der Leiste „Mehr ▾“ → „Mit AutoSchnell verbinden …“ und den Code aus AutoSchnell eingeben."));
        else if (b.Status != null)
            p.Add(new("Verbindung", PruefStufe.Ok,
                $"{AutoSchnellDienst.KontoText(b.Status.Name, b.Status.Konto, b.Status.Firma)}"
                + (b.Status.AboBis != null ? $" · Abo bis {AboDatum(b.Status.AboBis)}" : "")));
        else
            p.Add(new("Verbindung", PruefStufe.Fehler, b.StatusFehler ?? "Lizenz nicht prüfbar."));
        if (b.Status?.AktuelleVersion is { } neu && AutoSchnellDienst.NeuereVersion(neu, b.EigeneVersion))
            p.Add(new("Programmversion", PruefStufe.Hinweis,
                $"{b.EigeneVersion} – neue Version {neu} in AutoSchnell unter „{b.Status.ProgrammName ?? "Programme"}“."));
        else
            p.Add(new("Programmversion", PruefStufe.Ok, $"{b.EigeneVersion}{(b.Status?.AktuelleVersion != null ? " (aktuell)" : "")}"));
        if (!b.AutoPointerLaeuft)
            p.Add(new("AutoPointer", PruefStufe.Hinweis, "nicht geöffnet – für die Probe AutoPointer öffnen und ein Inserat anklicken."));
        else if (!b.FahrzeugAngezeigt)
            p.Add(new("AutoPointer", PruefStufe.Hinweis, "läuft, rechts wird aber kein Fahrzeug angezeigt (Reiter „Übersicht“)."));
        else
            p.Add(new("AutoPointer", PruefStufe.Ok, "läuft, Fahrzeug angezeigt."));
        if (b.Gelesen != null)
        {
            var fehlt = DetailLeser.Fehlend(b.Gelesen);
            var f = b.Gelesen;
            p.Add(fehlt.Count == 0
                ? new("Probe-Lesung", PruefStufe.Ok, $"{f.MarkeModellText} · EZ {f.EzText} · {f.Kilometer:N0} km"
                                                    + (f.Kw != null ? $" · {f.Kw} kW" : "")
                                                    + (f.InseratKennung != null ? " · Inserat-ID erkannt" : " · ohne Inserat-ID"))
                : new("Probe-Lesung", PruefStufe.Fehler, "unvollständig – fehlt: " + string.Join(", ", fehlt)
                                                         + ". Detailbereich in AutoPointer größer ziehen."));
        }
        else if (b.LeseFehler != null)
            p.Add(new("Probe-Lesung", PruefStufe.Fehler, b.LeseFehler));
        if (b.Probe != null)
            p.Add(b.Probe.Links.Count > 0
                ? new("Probe-Vergleich", PruefStufe.Ok,
                      $"{b.Probe.Links.Count} Vergleich(e): {string.Join(" + ", b.Probe.Links.Select(l => l.Portal))} (nicht geöffnet)")
                : new("Probe-Vergleich", PruefStufe.Hinweis, b.Probe.Hinweise.FirstOrDefault() ?? "keine Vergleichslinks."));
        else if (b.ProbeFehler != null)
            p.Add(new("Probe-Vergleich", PruefStufe.Fehler, b.ProbeFehler));
        p.Add(b.BrowserGefunden
            ? new("Browser", PruefStufe.Ok, "gefunden.")
            : new("Browser", PruefStufe.Fehler, "kein Browser gefunden – Edge oder Chrome installieren."));
        p.Add(b.AppInstalliert
            ? new("AutoSchnell-App", PruefStufe.Ok, "installiert – „Vertrag“ öffnet sie.")
            : new("AutoSchnell-App", PruefStufe.Hinweis, "nicht installiert – „Vertrag“ öffnet den Browser."));
        return p;
    }

    /// <summary>Muss der Check beim Start von selbst aufgehen? Nur bei Fehlern, die das Programm ganz lahmlegen
    /// (Windows zu alt, keine Texterkennung, kein Browser) — nicht, weil AutoPointer noch zu ist.</summary>
    internal static bool SchwererFehler(IEnumerable<PruefPunkt> punkte) =>
        punkte.Any(p => p.Stufe == PruefStufe.Fehler && p.Name is "Windows" or "Texterkennung" or "Browser");

    internal static string Text(IEnumerable<PruefPunkt> punkte) =>
        string.Join(Environment.NewLine, punkte.Select(p =>
            $"{(p.Stufe switch { PruefStufe.Ok => "✔", PruefStufe.Hinweis => "•", _ => "✖" })}  {p.Name}: {p.Text}"));

    private static string AboDatum(string iso) =>
        DateTime.TryParse(iso, out var d) ? d.ToString("dd.MM.yyyy") : iso[..Math.Min(10, iso.Length)];

    /// <summary>Die echte Pruefung. <paramref name="mitProbe"/>: das angezeigte Auto lesen (nur Bildschirm, AutoPointer
    /// zeichnet nichts) und beim Server einen Probe-Vergleich holen (oeffnet nichts, zaehlt nicht).</summary>
    public static async Task<List<PruefPunkt>> PruefenAsync(Einstellungen e, AutoSchnellDienst dienst, bool mitProbe = true)
    {
        var ocr = TextErkennung.Erstelle(out string ocrFehler);
        bool erreichbar = await ErreichbarAsync(e.Server);
        StatusAntwort? status = null;
        string? statusFehler = null;
        if (dienst.Verbunden)
        {
            try { status = await dienst.StatusAsync(); }
            catch (DienstFehler ex) { statusFehler = ex.Message; }
        }
        // Paket 3 (F3): die App-Suche (COM) parallel im Hintergrund, nicht im Oberflaechen-Thread
        var app = AutoSchnellApp.FindenAsync(e.Server);
        IntPtr haupt = AutoPointerFenster.FindeHauptfenster();
        var ansicht = haupt == IntPtr.Zero ? null : AutoPointerFenster.FindeDetails(haupt);
        Fahrzeug? gelesen = null;
        string? leseFehler = null;
        VergleichAntwort? probe = null;
        string? probeFehler = null;
        if (mitProbe && ocr != null && ansicht != null)
        {
            try
            {
                var lesung = await AutoPointerQuelle.LiesAnsichtAsync(ocr, ansicht, false);
                if (lesung == null || lesung.Leer) leseFehler = "Tabelle nicht lesbar (verdeckt oder leer).";
                else gelesen = lesung.Fahrzeug;
            }
            catch (Exception ex)
            {
                // Pruefung 08.10.2026 (1.5.9, E): die .NET-Meldung (oft englisch) nur ins Protokoll
                Protokoll.Schreibe("Systemcheck: Lesen fehlgeschlagen: " + ex);
                leseFehler = ex is TimeoutException
                    ? "Lesen fehlgeschlagen – die Windows-Texterkennung hat nicht geantwortet."
                    : "Lesen fehlgeschlagen – AutoPointer neu anklicken und den Systemcheck wiederholen.";
            }
            if (gelesen != null && DetailLeser.Fehlend(gelesen).Count == 0 && status != null)
            {
                try { probe = await dienst.VergleichAsync(gelesen, probelauf: true); }
                catch (DienstFehler ex) { probeFehler = ex.Message; }
            }
        }
        return Auswerten(new Befund
        {
            Windows = Environment.OSVersion.Version,
            OcrSprache = ocr?.Sprache,
            OcrFehler = ocr == null ? ocrFehler : null,
            ServerErreichbar = erreichbar,
            Server = e.Server,
            Verbunden = dienst.Verbunden,
            Status = status,
            StatusFehler = statusFehler,
            EigeneVersion = Application.ProductVersion.Split('+')[0],
            AutoPointerLaeuft = haupt != IntPtr.Zero,
            FahrzeugAngezeigt = ansicht != null,
            Gelesen = gelesen,
            LeseFehler = leseFehler,
            Probe = probe,
            ProbeFehler = probeFehler,
            BrowserGefunden = BrowserOeffner.BrowserVorhanden(e.Browser),
            AppInstalliert = await app != null,
        });
    }

    private static async Task<bool> ErreichbarAsync(string server)
    {
        try
        {
            using var http = new HttpClient { Timeout = TimeSpan.FromSeconds(6) };
            using var r = await http.GetAsync(server.TrimEnd('/') + "/api/health");
            return r.IsSuccessStatusCode;
        }
        catch (Exception) { return false; }
    }
}
