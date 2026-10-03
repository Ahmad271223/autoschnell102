namespace AutoPointerVergleich;

internal enum Lage { KeinAutoPointer, KeineDetails, Details }

internal readonly record struct QuellenZustand(Lage Lage, ulong Summe);

internal sealed record Lesung(Fahrzeug Fahrzeug, bool Leer, string Rohtext);

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

internal enum Status { Pause, KeinAutoPointer, Bereit, Aktiv }

/// <summary>Der Ablauf: Aenderung in der Detailansicht bemerken -> warten, bis sie
/// stillsteht -> lesen -> nur bei einem wirklich anderen Fahrzeug die
/// Vergleiche oeffnen.</summary>
internal sealed class Ueberwacher
{
    private readonly IAnsichtQuelle _quelle;
    private readonly Katalog _katalog;
    private readonly Func<Einstellungen> _einstellungen;
    private readonly IOeffner _oeffner;
    private readonly Func<DateTime> _uhr;
    private readonly Func<TimeSpan, Task> _warte;
    private readonly SemaphoreSlim _einzeln = new(1, 1);

    private ulong _summe;
    private DateTime _seit;
    private bool _offen;
    private string? _letzterSchluessel;
    private DateTime _letzteOeffnung = DateTime.MinValue;
    private bool _basis = true;
    private bool _ersterTick = true;
    private ulong _gemeldeteSumme;
    private Status? _status;

    public bool Probelauf { get; set; }
    public Fahrzeug? LetztesFahrzeug { get; private set; }
    public IReadOnlyList<Vergleich> LetzteVergleiche { get; private set; } = Array.Empty<Vergleich>();
    public Status Status => _status ?? Status.KeinAutoPointer;

    /// <summary>Statuswechsel (fuer das Symbol im Infobereich).</summary>
    public event Action<Status>? StatusGeaendert;
    /// <summary>Kurze Meldung fuer den Nutzer (Sprechblase). bool = Fehler.</summary>
    public event Action<string, bool>? Meldung;

    public Ueberwacher(IAnsichtQuelle quelle, Katalog katalog, Func<Einstellungen> einstellungen, IOeffner oeffner,
                       Func<DateTime>? uhr = null, Func<TimeSpan, Task>? warte = null)
    {
        _quelle = quelle;
        _katalog = katalog;
        _einstellungen = einstellungen;
        _oeffner = oeffner;
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
            SetzeStatus(Status.Aktiv);
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
            Melde("Noch kein Vergleich vorhanden.", false);
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
            MeldeEinmal("Fahrzeug konnte nicht eindeutig erkannt werden (fehlt: " + string.Join(", ", fehlt) + ").");
            return;
        }

        var zuordnung = LinkBauer.Zuordnen(f, _katalog);
        string schluessel = f.Schluessel;
        if (!erzwungen && schluessel == _letzterSchluessel)
        {
            Protokoll.Schreibe($"Gleiches Fahrzeug ({schluessel}) – kein neuer Vergleich.");
            return;
        }

        Protokoll.Schreibe("Fahrzeug erkannt\n" + string.Join("\n", f.Beschreibung()));
        var hinweise = new List<string>();
        var links = LinkBauer.Bauen(f, zuordnung, e, hinweise);
        foreach (var v in links) Protokoll.Schreibe($"{v.Portal} URL erstellt: {v.Url}");
        foreach (var h in hinweise) Protokoll.Schreibe("Hinweis: " + h);

        bool basis = _basis && !erzwungen;
        _basis = false;
        _letzterSchluessel = schluessel;
        LetztesFahrzeug = f;
        if (links.Count > 0) LetzteVergleiche = links;

        if (basis)
        {
            Protokoll.Schreibe("Fahrzeug war beim Start schon angezeigt – nicht automatisch geöffnet "
                               + "(Menü „Aktuelles Fahrzeug jetzt vergleichen“).");
            return;
        }
        if (links.Count == 0)
        {
            MeldeEinmal($"{f.Marke} {f.Modell}: kein Vergleich geöffnet – " + (hinweise.FirstOrDefault() ?? "kein Portal aktiv."));
            return;
        }

        var abstand = _letzteOeffnung.AddMilliseconds(e.MindestabstandMs) - _uhr();
        if (abstand > TimeSpan.Zero) await _warte(abstand);
        Oeffne(links, e);
        _letzteOeffnung = _uhr();
        var fehlendePortale = hinweise.Where(h => h.Contains("kein mobile.de-Vergleich") || h.Contains("kein AutoScout24-Vergleich")).ToList();
        if (fehlendePortale.Count > 0) Melde(string.Join("\n", fehlendePortale), false);
    }

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
        using var technik = AutoPointerFenster.Fotografiere(ansicht.TechnikTabelle);
        using var kopf = AutoPointerFenster.Fotografiere(ansicht.KopfTabelle);
        if (technik == null) return null;
        return await LiesBilderAsync(_ocr, technik, kopf, Native.GetDpiForWindow(ansicht.TechnikTabelle), _einstellungen().ErkennungsbilderSpeichern);
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
        if (DetailLeser.Fehlend(f).Count > 0 || DetailLeser.Unvollstaendig(f).Count > 0)
        {
            var zt2 = await ocr.LiesAsync(technik, faktor * 2 / 3);
            var f2 = DetailLeser.Auswerten(zt2, zk, kopf?.Width ?? 0);
            f = DetailLeser.Ergaenzen(f, f2);
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
            technik.Save(basis + "-technik.png");
            kopf?.Save(basis + "-kopf.png");
            File.WriteAllText(basis + "-text.txt", roh);
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
