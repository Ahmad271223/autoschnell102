namespace AutoPointerVergleich;

internal enum Lage { KeinAutoPointer, KeineDetails, Details }

internal readonly record struct QuellenZustand(Lage Lage, ulong Summe);

/// <param name="Weg">"Bildschirm" (nur kopiert, was AutoPointer ohnehin zeigt) oder
/// "PrintWindow" (AutoPointer hat die Tabelle extra in ein Bild gezeichnet).</param>
internal sealed record Lesung(Fahrzeug Fahrzeug, bool Leer, string Rohtext, string Weg = "");

/// <summary>Woher die Detailansicht kommt - echt: <see cref="AutoPointerQuelle"/>,
/// in den Tests eine Attrappe.</summary>
internal interface IAnsichtQuelle
{
    IntPtr Hauptfenster { get; }
    QuellenZustand Pruefe();
    Task<Lesung?> LiesAsync();
}

internal interface IOeffner
{
    void Oeffne(IReadOnlyList<Vergleich> vergleiche, Einstellungen e, IntPtr autoPointer);
}

internal enum Status { Pause, KeinAutoPointer, Bereit, Aktiv, NichtVerbunden, Gesperrt }

/// <summary>Der Ablauf: Aenderung in der Detailansicht bemerken -> warten, bis sie
/// stillsteht -> lesen -> nur bei einem wirklich anderen Fahrzeug beim
/// AutoSchnell-Server die Vergleiche holen (Abo, Firmenregeln) und oeffnen.</summary>
internal sealed class Ueberwacher
{
    private readonly IAnsichtQuelle _quelle;
    private readonly Func<Einstellungen> _einstellungen;
    private readonly IOeffner _oeffner;
    private readonly IVergleichsDienst _dienst;
    private readonly Func<DateTime> _uhr;
    private readonly Func<TimeSpan, Task> _warte;
    private readonly SemaphoreSlim _einzeln = new(1, 1);

    private ulong _summe;
    private DateTime _seit;
    private bool _offen;
    private string? _letzterSchluessel;
    /// <summary>Inserat-Kennung des zuletzt gemerkten Autos (Pruefbericht 03.10.2026, Nr. 1).</summary>
    private string? _letzteKennung;
    private DateTime _letzteOeffnung = DateTime.MinValue;
    private bool _basis = true;
    private bool _ersterTick = true;
    private ulong _gemeldeteSumme;
    private Status? _status;
    private bool _gesperrt;
    /// <summary>Server meldete 401 — bis zum Neu-Verbinden (Neustart) keine Vergleiche.</summary>
    private bool _verloren;

    public bool Probelauf { get; set; }
    public Fahrzeug? LetztesFahrzeug { get; private set; }
    public IReadOnlyList<Vergleich> LetzteVergleiche { get; private set; } = Array.Empty<Vergleich>();
    /// <summary>Original-Inserat des zuletzt verglichenen Autos (fuer "Kaufvertrag: in AutoSchnell oeffnen").</summary>
    public string? LetzteInseratUrl { get; private set; }
    public Status Status => _status ?? Status.KeinAutoPointer;

    /// <summary>Statuswechsel (fuer das Symbol im Infobereich).</summary>
    public event Action<Status>? StatusGeaendert;
    /// <summary>Kurze Meldung fuer den Nutzer (Sprechblase). bool = Fehler.</summary>
    public event Action<string, bool>? Meldung;
    /// <summary>Der Server kennt den Schluessel nicht mehr (anderer PC, getrennt) -> neu verbinden.</summary>
    public event Action<string>? VerbindungVerloren;
    /// <summary>Ein neues Auto ist jetzt "das letzte" (Nr. 11: ab hier zaehlt eine neu kopierte Inserat-Adresse).</summary>
    public event Action<Fahrzeug>? FahrzeugGewechselt;

    public Ueberwacher(IAnsichtQuelle quelle, Func<Einstellungen> einstellungen, IOeffner oeffner,
                       IVergleichsDienst dienst, Func<DateTime>? uhr = null, Func<TimeSpan, Task>? warte = null)
    {
        _quelle = quelle;
        _einstellungen = einstellungen;
        _oeffner = oeffner;
        _dienst = dienst;
        _uhr = uhr ?? (() => DateTime.Now);
        _warte = warte ?? (t => Task.Delay(t));
    }

    /// <summary>Beim Start und beim Wiedereinschalten: Ein Fahrzeug, das gerade
    /// schon angezeigt wird, gilt als gesehen und oeffnet keine Tabs - erst das
    /// naechste angeklickte.</summary>
    public void Neustart()
    {
        _basis = true;
        _ersterTick = true;
        _verloren = false;
        _gesperrt = false;
        _offen = false;
        _summe = 0;
    }

    public async Task TickAsync()
    {
        var e = _einstellungen();
        if (!e.AutomatikAktiv)
        {
            SetzeStatus(Status.Pause);
            return;
        }
        if (!_dienst.Verbunden || _verloren)
        {
            SetzeStatus(Status.NichtVerbunden);
            return;
        }
        if (!await _einzeln.WaitAsync(0)) return;
        try
        {
            var z = _quelle.Pruefe();
            if (_ersterTick)
            {
                _ersterTick = false;
                if (z.Lage != Lage.Details) _basis = false;
            }
            if (z.Lage != Lage.Details)
            {
                SetzeStatus(z.Lage == Lage.KeinAutoPointer ? Status.KeinAutoPointer : Status.Bereit);
                _summe = 0;
                _offen = false;
                return;
            }
            SetzeStatus(_gesperrt ? Status.Gesperrt : Status.Aktiv);
            var jetzt = _uhr();
            if (z.Summe != _summe)
            {
                _summe = z.Summe;
                _seit = jetzt;
                _offen = true;
                return;
            }
            if (!_offen || (jetzt - _seit).TotalMilliseconds < e.WartezeitMs) return;

            var lesung = await _quelle.LiesAsync();
            var nach = _quelle.Pruefe();
            if (nach.Lage != Lage.Details || nach.Summe != _summe)
            {
                // Waehrend des Lesens hat AutoPointer weitergeblaettert: verwerfen,
                // sonst koennten Werte zweier Fahrzeuge gemischt werden.
                _summe = nach.Summe;
                _seit = _uhr();
                return;
            }
            _offen = false;
            if (lesung == null) return;
            await VerarbeiteAsync(lesung, e, erzwungen: false);
        }
        finally { _einzeln.Release(); }
    }

    /// <summary>Menue "Aktuelles Fahrzeug jetzt vergleichen": sofort lesen und
    /// oeffnen - auch bei Pause und auch, wenn es dasselbe Fahrzeug ist.</summary>
    public async Task JetztVergleichenAsync()
    {
        await _einzeln.WaitAsync();
        try
        {
            var z = _quelle.Pruefe();
            if (z.Lage != Lage.Details)
            {
                Melde(z.Lage == Lage.KeinAutoPointer
                    ? "AutoPointer ist nicht geöffnet."
                    : "In AutoPointer wird rechts gerade kein Fahrzeug angezeigt (Reiter „Übersicht“).", true);
                return;
            }
            if (!_dienst.Verbunden)
            {
                Melde("Nicht mit AutoSchnell verbunden – Rechtsklick auf das Symbol → „Mit AutoSchnell verbinden …“.", true);
                return;
            }
            var lesung = await _quelle.LiesAsync();
            if (lesung == null) return;
            _summe = _quelle.Pruefe().Summe;
            _offen = false;
            await VerarbeiteAsync(lesung, _einstellungen(), erzwungen: true);
        }
        finally { _einzeln.Release(); }
    }

    public void LetztenErneutOeffnen()
    {
        if (LetzteVergleiche.Count == 0)
        {
            // Nr. 2: nie die Links eines frueheren Autos — fuer das zuletzt angeklickte gibt es (noch) keine
            Melde(LetztesFahrzeug == null ? "Noch kein Vergleich vorhanden."
                : "Für das zuletzt angeklickte Auto gibt es keinen Vergleich – „Jetzt vergleichen“ drücken.", false);
            return;
        }
        Protokoll.Schreibe("Letzten Vergleich erneut geöffnet.");
        Oeffne(LetzteVergleiche, _einstellungen());
    }

    private async Task VerarbeiteAsync(Lesung lesung, Einstellungen e, bool erzwungen)
    {
        var f = lesung.Fahrzeug;
        if (lesung.Leer)
        {
            Protokoll.Schreibe("Detailansicht ohne lesbaren Text – nichts zu tun.");
            return;
        }
        var fehlt = DetailLeser.Fehlend(f);
        if (fehlt.Count > 0)
        {
            _basis = false;
            Protokoll.Schreibe($"Fahrzeug konnte nicht eindeutig erkannt werden – fehlt: {string.Join(", ", fehlt)}\n"
                               + $"Gelesen: {lesung.Rohtext}");
            MeldeEinmal("Fahrzeug konnte nicht eindeutig erkannt werden (fehlt: " + string.Join(", ", fehlt) + ")."
                        + (lesung.Weg == "Bildschirm"
                            ? " Tipp: Detailbereich in AutoPointer größer ziehen, damit alle Zeilen zu sehen sind." : ""));
            return;
        }

        // Seit 1.4.0: Marke/Modell erkennt der Server (Wunsch Ahmad 03.10.2026) — hier zaehlt der gelesene Text
        if (!erzwungen && Fahrzeug.GleichesAuto(_letzterSchluessel, _letzteKennung, f))
        {
            Protokoll.Schreibe($"Gleiches Fahrzeug ({f.MarkeModellText} · {f.EzText} · {f.Kilometer} km) – kein neuer Vergleich.");
            return;
        }

        Protokoll.Schreibe((lesung.Weg.Length > 0 ? $"Fahrzeug gelesen ({lesung.Weg})\n" : "Fahrzeug gelesen\n")
                           + string.Join("\n", f.Beschreibung()));
        bool basis = _basis && !erzwungen;
        _basis = false;
        if (basis)
        {
            Merken(f);
            LetztesFahrzeug = f;
            // Nr. 2: zu diesem Auto gibt es noch keinen Vergleich — keine Links/Inserat eines frueheren Autos
            LetzteVergleiche = Array.Empty<Vergleich>();
            LetzteInseratUrl = null;
            FahrzeugGewechselt?.Invoke(f);
            Protokoll.Schreibe("Fahrzeug war beim Start schon angezeigt – nicht automatisch geöffnet "
                               + "(Menü „Aktuelles Fahrzeug jetzt vergleichen“).");
            return;
        }
        VergleichAntwort antwort;
        try { antwort = await _dienst.VergleichAsync(f, Probelauf); }
        catch (DienstFehler ex)
        {
            // Schluessel bleibt der alte: nach Behebung (Abo, Verbindung) vergleicht
            // derselbe Klick bzw. "Jetzt vergleichen" erneut.
            Protokoll.Schreibe("AutoSchnell: " + ex.Message);
            if (ex.NichtVerbunden)
            {
                _verloren = true;
                SetzeStatus(Status.NichtVerbunden);
                VerbindungVerloren?.Invoke(ex.Message);
            }
            else if (ex.KeinAbo || ex.Status == 403)
            {
                _gesperrt = true;
                SetzeStatus(Status.Gesperrt);
            }
            Melde(ex.Message, true);
            return;
        }
        if (_gesperrt)
        {
            _gesperrt = false;
            SetzeStatus(Status.Aktiv);
        }
        Merken(f);
        if (antwort.ErkanntMarke != null)
        {
            f.Marke = antwort.ErkanntMarke;
            f.Modell = antwort.ErkanntModell;
            Protokoll.Schreibe($"Erkannt (AutoSchnell): {f.Marke} {f.Modell}".TrimEnd());
        }
        LetztesFahrzeug = f;
        LetzteInseratUrl = antwort.InseratUrl;
        // Pruefbericht 03.10.2026 (Nr. 2): ab hier gehoeren die "letzten Vergleiche" zu DIESEM Auto — auch wenn
        // es keine gibt. Vorher blieben die Links des vorigen Autos stehen ("erneut oeffnen" zeigte das falsche Auto).
        LetzteVergleiche = Array.Empty<Vergleich>();
        FahrzeugGewechselt?.Invoke(f);
        if (!antwort.MarkeErkannt)
        {
            MeldeEinmal($"Marke in „{f.MarkeModellText}“ nicht erkannt – kein Vergleich.");
            return;
        }
        foreach (var v in antwort.Links) Protokoll.Schreibe($"{v.Portal} URL (Regeln {antwort.Profil}): {v.Url}");
        foreach (var h in antwort.Hinweise) Protokoll.Schreibe("Hinweis: " + h);
        Protokoll.Schreibe(antwort.InseratUrl != null
            ? $"Inserat: {antwort.InseratUrl} – für den Kaufvertrag vorab ausgelesen: {antwort.VorabStatus}"
            : "Inserat-Adresse unbekannt – für den Kaufvertrag von Hand einfügen.");
        // Hinweise fuer den Kaufvertrag (Wunsch Ahmad 03.10.2026)
        var vertragsHinweise = new List<string>();
        if (antwort.InseratUrl == null && (f.Quelle ?? "").Contains("AutoScout", StringComparison.OrdinalIgnoreCase))
            vertragsHinweise.Add(KeinLinkHinweis);
        else if (antwort.VorabStatus is "limit" or "fehler" && antwort.VorabHinweis.Length > 0)
            vertragsHinweise.Add("Für den Kaufvertrag nicht vorab ausgelesen: " + antwort.VorabHinweis);
        var links = antwort.Links
            .Where(l => (l.Portal == "mobile.de" && e.MobileDe) || (l.Portal == "AutoScout24" && e.AutoScout24))
            .ToList();
        LetzteVergleiche = links;
        if (links.Count == 0)
        {
            if (vertragsHinweise.Count > 0) Melde(string.Join("\n", vertragsHinweise), false);
            MeldeEinmal($"{f.Marke} {f.Modell}: kein Vergleich geöffnet – "
                        + (antwort.Links.Count > 0 ? "kein Portal in den Einstellungen aktiv." : antwort.Hinweise.FirstOrDefault() ?? "keine Links."));
            return;
        }

        var abstand = _letzteOeffnung.AddMilliseconds(e.MindestabstandMs) - _uhr();
        if (abstand > TimeSpan.Zero) await _warte(abstand);
        Oeffne(links, e);
        _letzteOeffnung = _uhr();
        var fehlendePortale = antwort.Hinweise.Where(h => h.Contains("kein mobile.de-Vergleich") || h.Contains("kein AutoScout24-Vergleich")).ToList();
        fehlendePortale.AddRange(PlausibilitaetsHinweise(f, _uhr()));
        fehlendePortale.AddRange(vertragsHinweise);
        if (fehlendePortale.Count > 0) Melde(string.Join("\n", fehlendePortale), false);
    }

    private void Merken(Fahrzeug f)
    {
        _letzterSchluessel = f.Schluessel;
        _letzteKennung = f.InseratKennung;
    }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 9): Werte, die technisch moeglich, aber verdaechtig sind — nur als Hinweis
    /// (der Vergleich laeuft trotzdem): widerspruechliche Leistung, ungewoehnlich viele Kilometer fuer das Alter.</summary>
    internal static List<string> PlausibilitaetsHinweise(Fahrzeug f, DateTime jetzt)
    {
        var hinweise = new List<string>();
        if (f.LeistungUnsicher && f.Kw == null)
            hinweise.Add("Leistung nicht sicher gelesen (kW und PS passen nicht zusammen) – Vergleich ohne "
                         + "Leistungsfilter. Bitte in AutoPointer prüfen.");
        if (f.EzJahr != null && f.Kilometer != null)
        {
            double jahre = Math.Max(1.0, (jetzt.Year - f.EzJahr.Value) + ((jetzt.Month - (f.EzMonat ?? 6)) / 12.0));
            if (f.Kilometer.Value / jahre > 100_000)
                hinweise.Add($"Kilometerstand ungewöhnlich hoch ({f.Kilometer.Value:N0} km bei EZ {f.EzText}) – "
                             + "bitte prüfen, ob richtig gelesen.");
        }
        return hinweise;
    }

    internal const string KeinLinkHinweis =
        "AutoScout-Inserat: die Kennung (Hash-ID) ist in AutoPointer nicht vollständig sichtbar. Für den Kaufvertrag: in AutoPointer „Seite öffnen“, im Browser die Adresse kopieren (Strg+L, dann Strg+C) und hier noch einmal „Kaufvertrag“ drücken – das Programm übernimmt die kopierte Adresse. Tipp: Detailbereich in AutoPointer breiter ziehen – dann klappt es automatisch.";

    private void Oeffne(IReadOnlyList<Vergleich> links, Einstellungen e)
    {
        if (Probelauf)
        {
            Protokoll.Schreibe($"Probelauf: {links.Count} Vergleich(e) NICHT geöffnet.");
            return;
        }
        try
        {
            _oeffner.Oeffne(links, e, _quelle.Hauptfenster);
            Protokoll.Schreibe($"Vergleiche geöffnet ({string.Join(" + ", links.Select(l => l.Portal))}).");
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Browser konnte nicht geöffnet werden: " + ex.Message);
            Melde("Browser konnte nicht geöffnet werden: " + ex.Message, true);
        }
    }

    /// <summary>Fehlermeldung nur einmal je angezeigtem Inhalt - kein Dauerfeuer.</summary>
    private void MeldeEinmal(string text)
    {
        if (_gemeldeteSumme == _summe && _summe != 0) return;
        _gemeldeteSumme = _summe;
        Melde(text, true);
    }

    private void Melde(string text, bool fehler) => Meldung?.Invoke(text, fehler);

    private void SetzeStatus(Status s)
    {
        if (_status == s) return;
        _status = s;
        Protokoll.Schreibe(s switch
        {
            Status.Pause => "Automatik ist aus.",
            Status.KeinAutoPointer => "AutoPointer nicht gefunden – warte.",
            Status.Bereit => "AutoPointer gefunden – rechts wird kein Fahrzeug angezeigt.",
            Status.NichtVerbunden => "Nicht mit AutoSchnell verbunden – keine Vergleiche.",
            Status.Gesperrt => "AutoSchnell sperrt die Vergleiche (Abo/Freigabe).",
            _ => "AutoPointer-Detailansicht erkannt.",
        });
        StatusGeaendert?.Invoke(s);
    }
}

/// <summary>Die echte Quelle: AutoPointer-Fenster, PrintWindow, Windows-OCR.</summary>
internal sealed class AutoPointerQuelle : IAnsichtQuelle
{
    private readonly TextErkennung _ocr;
    private readonly Func<Einstellungen> _einstellungen;
    private DetailAnsicht? _ansicht;
    private IntPtr _haupt;
    private DateTime _letzteSuche = DateTime.MinValue;

    public AutoPointerQuelle(TextErkennung ocr, Func<Einstellungen> einstellungen)
    {
        _ocr = ocr;
        _einstellungen = einstellungen;
    }

    public IntPtr Hauptfenster => _haupt;

    public QuellenZustand Pruefe()
    {
        if (_ansicht == null || !AutoPointerFenster.NochGueltig(_ansicht))
        {
            _ansicht = null;
            if (_haupt == IntPtr.Zero || !Native.IsWindow(_haupt))
            {
                _haupt = IntPtr.Zero;
                if ((DateTime.Now - _letzteSuche).TotalSeconds < 2) return new QuellenZustand(Lage.KeinAutoPointer, 0);
                _letzteSuche = DateTime.Now;
                _haupt = AutoPointerFenster.FindeHauptfenster();
                if (_haupt == IntPtr.Zero) return new QuellenZustand(Lage.KeinAutoPointer, 0);
            }
            _ansicht = AutoPointerFenster.FindeDetails(_haupt);
            if (_ansicht == null) return new QuellenZustand(Lage.KeineDetails, 0);
        }
        // Ein anderes Fahrzeug kann nur erscheinen, wenn der Sucher in AutoPointer
        // klickt - dann ist es vorne. Liegt ein anderes Fenster (Browser) davor,
        // gilt die letzte Pruefsumme: kostet nichts und verdeckte Pixel loesen
        // kein erneutes Lesen aus.
        if (_letzteSumme != 0 && !ImVordergrund()) return new QuellenZustand(Lage.Details, _letzteSumme);
        ulong summe = AutoPointerFenster.Pruefsumme(_ansicht.TechnikTabelle);
        summe = (summe * 31) ^ AutoPointerFenster.Pruefsumme(_ansicht.KopfTabelle);
        _letzteSumme = summe == 0 ? 1 : summe;
        return new QuellenZustand(Lage.Details, _letzteSumme);
    }

    private ulong _letzteSumme;

    private bool ImVordergrund()
    {
        IntPtr vorne = Native.GetForegroundWindow();
        if (vorne == IntPtr.Zero) return false;
        Native.GetWindowThreadProcessId(vorne, out uint pidVorne);
        Native.GetWindowThreadProcessId(_haupt, out uint pidAp);
        return pidVorne == pidAp;
    }

    public async Task<Lesung?> LiesAsync()
    {
        var ansicht = _ansicht;
        if (ansicht == null) return null;
        var e = _einstellungen();
        return await LiesAnsichtAsync(_ocr, ansicht, e.ErkennungsbilderSpeichern);
    }

    /// <summary>Befund 03.10.2026: AutoPointer zeigte eine "Zugriffsverletzung" (aprun.exe). Es stuerzt
    /// nachweislich auch ohne uns ab (24.09.), aber PrintWindow laesst AutoPointer selbst zeichnen —
    /// deshalb zuerst nur den Bildschirminhalt kopieren (keine Nachricht an AutoPointer). PrintWindow
    /// nur noch, wenn darin Pflichtzeilen oder die Inserat-ID fehlen (weggescrollt, schmale Ansicht).</summary>
    /// <remarks>Zweiter Befund 03.10.2026 abends: dieselbe Zugriffsverletzung (Offset 16B050B) noch einmal —
    /// PrintWindow nur noch, wenn der Sucher es in den Einstellungen ausdruecklich erlaubt
    /// (<paramref name="zeichnenErlaubt"/>). Sonst gilt, was auf dem Bildschirm steht.</remarks>
    internal static async Task<Lesung?> LiesAnsichtAsync(TextErkennung ocr, DetailAnsicht ansicht, bool bilderSpeichern,
                                                         Action<System.Drawing.Bitmap, System.Drawing.Bitmap?>? bilder = null)
    {
        // Beschreibung GLEICHZEITIG mit der Tabelle lesen (eigene Texterkennung) — sonst +0,15-0,2 s je Auto
        var beschreibung = BeschreibungLesenAsync(BeschreibungsErkennung() ?? ocr, ansicht);
        var lesung = await LiesTabellenAsync(ocr, ansicht, bilderSpeichern, bilder);
        string? text = await beschreibung;
        if (lesung != null) lesung.Fahrzeug.BeschreibungText = text;
        return lesung;
    }

    private static TextErkennung? _beschreibungsErkennung;
    private static bool _beschreibungsErkennungVersucht;

    /// <summary>Zweite Texterkennung nur fuer die Beschreibung (laeuft parallel zur Tabelle).</summary>
    private static TextErkennung? BeschreibungsErkennung()
    {
        if (_beschreibungsErkennungVersucht) return _beschreibungsErkennung;
        _beschreibungsErkennungVersucht = true;
        _beschreibungsErkennung = TextErkennung.Erstelle(out _);
        return _beschreibungsErkennung;
    }

    /// <summary>Befund 04.10.2026 (Mercedes, Feld "Andere", Modell nur in der Beschreibung): den sichtbaren Anfang
    /// der Beschreibung lesen — nur vom Bildschirm (nie PrintWindow), nicht wenn die Leiste davor liegt.
    /// Was daraus wird, entscheidet der Server (nur wenn Feld und Ueberschrift kein Modell hergeben).</summary>
    internal static async Task<string?> BeschreibungLesenAsync(TextErkennung ocr, DetailAnsicht ansicht)
    {
        if (ansicht.Beschreibung == IntPtr.Zero || !Native.IsWindow(ansicht.Beschreibung)
            || AutoPointerFenster.Verdeckt(ansicht.Beschreibung)) return null;
        await Task.Yield();          // nicht im Takt des Aufrufers: die Tabelle wird derweil gelesen
        try
        {
            using var bild = AutoPointerFenster.Abbild(ansicht.Beschreibung);
            if (bild == null) return null;
            uint dpi = Native.GetDpiForWindow(ansicht.Beschreibung);
            double faktor = Math.Clamp(2.0 * 96 / (dpi == 0 ? 96 : dpi), 1.0, 2.0);
            var zeilen = await ocr.LiesAsync(bild, faktor);
            string text = string.Join(" ", zeilen.Select(z => z.Text)).Trim();
            return text.Length == 0 ? null : text.Length > 1500 ? text[..1500] : text;
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Beschreibung nicht gelesen: " + ex.Message);
            return null;
        }
    }

    private static async Task<Lesung?> LiesTabellenAsync(TextErkennung ocr, DetailAnsicht ansicht, bool bilderSpeichern,
                                                         Action<System.Drawing.Bitmap, System.Drawing.Bitmap?>? bilder)
    {
        uint dpi = Native.GetDpiForWindow(ansicht.TechnikTabelle);
        // Wunsch Ahmad 04.10.2026: AutoPointer NIE selbst zeichnen lassen (PrintWindow ist ganz entfernt) —
        // nur der Bildschirm. Liegt die eigene Leiste darueber, wird sie fuer das Abbild kurz ausgeblendet.
        bool verdeckt = AutoPointerFenster.Verdeckt(ansicht.TechnikTabelle) || AutoPointerFenster.Verdeckt(ansicht.KopfTabelle);
        {
            // Nr. 10: liegt die eigene Leiste ueber der Tabelle (und PrintWindow ist aus), wird sie fuer das
            // Abbild kurz unsichtbar — vorher stand sie mit im Bild und Zeilen fehlten.
            bool ausgeblendet = false;
            if (verdeckt && AutoPointerFenster.EigeneAusblenden is { } ausblenden)
            {
                try { ausblenden(true); ausgeblendet = true; await Task.Delay(150); }
                catch (Exception ex) { Protokoll.Schreibe("Leiste nicht ausgeblendet: " + ex.Message); }
            }
            System.Drawing.Bitmap? technikBild, kopfBild;
            try
            {
                technikBild = AutoPointerFenster.Abbild(ansicht.TechnikTabelle);
                kopfBild = AutoPointerFenster.Abbild(ansicht.KopfTabelle);
            }
            finally
            {
                if (ausgeblendet)
                    try { AutoPointerFenster.EigeneAusblenden?.Invoke(false); } catch (Exception) { }
            }
            using var technik = technikBild;
            using var kopf = kopfBild;
            if (technik != null)
            {
                var sicht = await LiesBilderAsync(ocr, technik, kopf, dpi, bilderSpeichern);
                bilder?.Invoke(technik, kopf);
                return sicht with { Weg = "Bildschirm" };
            }
        }
        return null;
    }

    /// <summary>Zwei Durchlaeufe: Zoom x3 (bei 96 dpi), fehlende Felder aus einem
    /// zweiten Durchlauf mit x2 ergaenzt.</summary>
    internal static async Task<Lesung> LiesBilderAsync(TextErkennung ocr, System.Drawing.Bitmap technik,
                                                       System.Drawing.Bitmap? kopf, uint dpi, bool bilderSpeichern)
    {
        double faktor = Math.Clamp(3.0 * 96 / (dpi == 0 ? 96 : dpi), 1.5, 3.0);
        var zt = await ocr.LiesAsync(technik, faktor);
        var zk = kopf != null ? await ocr.LiesAsync(kopf, faktor) : new List<OcrZeile>();
        var f = DetailLeser.Auswerten(zt, zk, kopf?.Width ?? 0);
        string roh = Rohtext(zt, zk);
        if (DetailLeser.Fehlend(f).Count > 0 || DetailLeser.Unvollstaendig(f).Count > 0 || f.HashId != null)
        {
            var zt2 = await ocr.LiesAsync(technik, faktor * 2 / 3);
            var f2 = DetailLeser.Auswerten(zt2, zk, kopf?.Width ?? 0);
            // Hash-ID (fuer den AutoScout-Link) nur, wenn beide Durchlaeufe genau dasselbe lesen
            string? hash = f.HashId != null && f.HashId == f2.HashId ? f.HashId : null;
            f = DetailLeser.Ergaenzen(f, f2);
            f.HashId = hash;
            roh += "  ||  2. Durchlauf: " + Rohtext(zt2, Array.Empty<OcrZeile>());
        }
        if (bilderSpeichern) Speichere(technik, kopf, roh);
        return new Lesung(f, zt.Count == 0, roh);
    }

    private static string Rohtext(IEnumerable<OcrZeile> technik, IEnumerable<OcrZeile> kopf) =>
        string.Join(" | ", kopf.Concat(technik).Select(z => z.Text));

    private static void Speichere(System.Drawing.Bitmap technik, System.Drawing.Bitmap? kopf, string roh)
    {
        try
        {
            string ordner = Path.Combine(Protokoll.Ordner, "bilder");
            Directory.CreateDirectory(ordner);
            string basis = Path.Combine(ordner, DateTime.Now.ToString("yyyyMMdd-HHmmss-fff"));
            // verschluesselt wie das Protokoll (Tresor); lesbar mit --entschluesseln <datei>
            static byte[] Png(System.Drawing.Bitmap b)
            {
                using var ms = new MemoryStream();
                b.Save(ms, System.Drawing.Imaging.ImageFormat.Png);
                return ms.ToArray();
            }
            File.WriteAllBytes(basis + "-technik.png.dat", Tresor.Verschluesseln(Png(technik)));
            if (kopf != null) File.WriteAllBytes(basis + "-kopf.png.dat", Tresor.Verschluesseln(Png(kopf)));
            File.WriteAllBytes(basis + "-text.txt.dat", Tresor.Verschluesseln(System.Text.Encoding.UTF8.GetBytes(roh)));
        }
        catch (Exception ex) { Protokoll.Schreibe("Erkennungsbild nicht gespeichert: " + ex.Message); }
    }
}

internal sealed class BrowserAusgabe : IOeffner
{
    private readonly SynchronizationContext? _ui;

    /// <param name="ui">Oberflaechen-Thread: nur der hat eine Eingabe-Warteschlange,
    /// die AttachThreadInput fuer "zurueck zu AutoPointer" braucht.</param>
    public BrowserAusgabe(SynchronizationContext? ui) => _ui = ui;

    public void Oeffne(IReadOnlyList<Vergleich> vergleiche, Einstellungen e, IntPtr autoPointer)
    {
        BrowserOeffner.Oeffne(vergleiche.Select(v => v.Url).ToList(), e.Browser);
        if (e.ZurueckZuAutoPointer && autoPointer != IntPtr.Zero && _ui != null)
            Task.Delay(900).ContinueWith(_ => _ui.Post(__ => BrowserOeffner.ZurueckZu(autoPointer), null));
    }
}
