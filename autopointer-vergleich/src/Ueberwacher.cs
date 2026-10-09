namespace AutoPointerVergleich;

internal enum Lage { KeinAutoPointer, KeineDetails, Details }

internal readonly record struct QuellenZustand(Lage Lage, ulong Summe);

/// <param name="Weg">"Bildschirm" (nur kopiert, was AutoPointer ohnehin zeigt) oder
/// "PrintWindow" (AutoPointer hat die Tabelle extra in ein Bild gezeichnet).</param>
/// <param name="Bild">1.5.13 (Wunsch Ahmad 09.10.2026): genau das Bild, das die Texterkennung gelesen hat (Kopf ueber Technik,
/// PNG, 24 Bit, ≤ 1,5 MB) — geht an AutoSchnell, wenn das Auto nicht erkannt wird. null: kein Bild (Tests, Einstellung aus,
/// Kodieren fehlgeschlagen). Lebt nur mit dieser Lesung — nach dem Verarbeiten ist es weg, es wird nie je Auto gesammelt.</param>
internal sealed record Lesung(Fahrzeug Fahrzeug, bool Leer, string Rohtext, string Weg = "", byte[]? Bild = null);

/// <summary>Pruefung 08.10.2026 (1.5.9, C): eine Meldung an den Sucher. Windows schneidet Sprechblasen bei ~255 Zeichen
/// ab — die langen Anleitungen (Inserat-ID ≈ 400, Hash-ID ≈ 360 Zeichen) verloren genau ihre Anweisungen. Deshalb kommt
/// in die Sprechblase nur <paramref name="Text"/> (hoechstens <see cref="MaxZeichen"/> Zeichen); die ganze Erklaerung
/// steht in <paramref name="Ausfuehrlich"/> und bleibt unter "Letzte Hinweise" im Fenster "Status und Hilfe".</summary>
/// <param name="Sprechblase">false = nur ins Fenster (und Protokoll), keine Sprechblase — z. B. ein Hinweis, der in
/// diesem Programmlauf schon einmal als Sprechblase kam.</param>
internal sealed record Hinweis(string Text, bool Fehler, string? Ausfuehrlich = null, bool Sprechblase = true)
{
    internal const int MaxZeichen = 150;

    /// <summary>Auf hoechstens <paramref name="max"/> Zeichen kuerzen — am Wortende, mit "…". (rein, fuer Tests)</summary>
    internal static string Kuerzen(string text, int max = MaxZeichen)
    {
        text = text.Trim();
        if (text.Length <= max) return text;
        int ende = text.LastIndexOf(' ', max - 1);
        if (ende < max / 2) ende = max - 1;
        return text[..ende].TrimEnd(' ', ',', ';', ':', '–', '-') + "…";
    }
}

/// <summary>Woher die Detailansicht kommt - echt: <see cref="AutoPointerQuelle"/>,
/// in den Tests eine Attrappe.</summary>
internal interface IAnsichtQuelle
{
    IntPtr Hauptfenster { get; }
    /// <summary>Pruefung 09.10.2026 (Befund 3): liegt gerade ein Fenster von AutoPointer vorne? Nur dann zeigt der
    /// Bildschirm im Rechteck der Tabellen auch AutoPointer — sonst wuerde der Browser "gelesen".</summary>
    bool ImVordergrund { get; }
    QuellenZustand Pruefe();
    Task<Lesung?> LiesAsync();
}

internal interface IOeffner
{
    /// <param name="browser">1.5.9 (A): in diesem Browser oeffnen — meist <see cref="Einstellungen.Browser"/>, fuer die
    /// Vorgangsseite bei "Standardbrowser" der Browser, in dem die Erweiterung verbunden ist.</param>
    void Oeffne(IReadOnlyList<Vergleich> vergleiche, Einstellungen e, IntPtr autoPointer, BrowserWahl browser);
}

/// <param name="TexterkennungFehlt">Paket 2 (A9): Windows-Texterkennung (Sprachpaket) fehlt — wird jede Minute
/// erneut versucht; bis dahin liest das Programm nichts.</param>
internal enum Status { Pause, KeinAutoPointer, Bereit, Aktiv, NichtVerbunden, Gesperrt, TexterkennungFehlt }

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
    /// <summary>Pruefung 05.10.2026 (Paket 2, A10): Zeitabstaende (Wartezeit, Mindestabstand) laufen ueber diese
    /// monotone Uhr in Millisekunden (Environment.TickCount64) — nicht ueber DateTime.Now, das beim Stellen der
    /// Uhr (Zeitabgleich, Sommerzeit) springt. Tests, die nur eine DateTime-Uhr geben, bekommen sie daraus.</summary>
    private readonly Func<long> _takt;
    private readonly Func<TimeSpan, Task> _warte;
    private readonly SemaphoreSlim _einzeln = new(1, 1);

    private ulong _summe;
    private long _seit;
    private volatile bool _offen;
    private string? _letzterSchluessel;
    /// <summary>Inserat-Kennung des zuletzt gemerkten Autos (Pruefbericht 03.10.2026, Nr. 1).</summary>
    private string? _letzteKennung;
    private long? _letzteOeffnung;
    private bool _basis = true;
    private bool _ersterTick = true;
    private ulong _gemeldeteSumme;
    private Status? _status;
    private bool _gesperrt;
    /// <summary>Server meldete 401 — bis zum Neu-Verbinden (Neustart) keine Vergleiche.</summary>
    private bool _verloren;
    /// <summary>Paket 1: Lesefehler hintereinander fuer denselben Inhalt (GDI, Texterkennung) — hoechstens
    /// <see cref="LeseVersuche"/> Versuche mit wachsendem Abstand, dann erst wieder bei einer Aenderung.</summary>
    private int _lesefehler;
    internal const int LeseVersuche = 3;
    /// <summary>Pruefung 05.10.2026 (Paket 2, A11): Neustart()/NachVerbinden() kommen vom Oberflaechen-Thread, waehrend
    /// der Takt-Thread liest. Vorher setzten sie die Felder mitten im Lesen zurueck — das gerade gelesene Auto wurde
    /// verworfen und danach nie verglichen (_offen war schon false). Jetzt merken sie nur einen Wunsch, den der
    /// naechste Durchlauf unter der Sperre anwendet. 0 = nichts, 1 = Neustart, 2 = NachVerbinden.</summary>
    private int _angefordert;
    /// <summary>Wunsch Ahmad 06.10.2026 ("nur die Autos vergleichen, die man anklickt"): Zeitpunkt des letzten
    /// Mausklicks in AutoPointer (gleiche Uhr wie <see cref="_takt"/>, siehe Klicks.cs). Eine Aenderung der
    /// Detailansicht wird nur verglichen, wenn hoechstens <see cref="KlickVorlaufMs"/> davor (oder danach) geklickt
    /// wurde — zeigt AutoPointer von selbst ein anderes Auto (Live-Liste rutscht weiter), bleibt es stehen, bis der
    /// Sucher es anklickt. null = keine Klick-Pruefung (aeltere Tests).</summary>
    private readonly Func<long>? _letzterKlick;
    internal const int KlickVorlaufMs = 5000;
    /// <summary>Die angezeigte Aenderung kam nicht vom Sucher — wartet auf seinen Klick.</summary>
    private bool _ungeklickt;
    /// <summary>NachVerbinden() zaehlt wie ein Klick: das gerade angezeigte Auto wird verglichen.</summary>
    private long _klickErsatz = long.MinValue / 2;
    /// <summary>Pruefung 08.10.2026 (1.5.9, H): ohne Klick uebergangene Aenderungen (Pfeiltasten, AutoPointer blaettert
    /// selbst) waren still — der Sucher wartete auf Tabs, die nie kamen. Einmal je Programmlauf sagen, wie es geht.</summary>
    private bool _mausHinweisGezeigt;
    internal const string MausHinweis = "Automatisch geht es nur per Mausklick – für dieses Auto „Vergleichen“ drücken.";
    /// <summary>Pruefung 09.10.2026 (Befund 3): "Vergleichen", aber vorne liegt ein anderes Fenster (Browser) — nicht lesen,
    /// sonst stuende der Browser-Inhalt im Abbild ("Fahrzeug nicht erkannt", Lesebild mit fremdem Inhalt an AutoSchnell).</summary>
    internal const string AutoPointerNichtVorne = "AutoPointer liegt nicht vorne – bitte AutoPointer anklicken und erneut „Vergleichen“ drücken.";
    /// <summary>Pruefung 09.10.2026 (Befund 2b/3): ein minimiertes AutoPointer wird nie nach vorne geholt (Windows stellt es
    /// nicht wieder her, Tasten gingen ins Leere) — der Sucher oeffnet es selbst.</summary>
    internal const string AutoPointerMinimiert = "AutoPointer ist minimiert – bitte AutoPointer öffnen und erneut „Vergleichen“ drücken.";

    public bool Probelauf { get; set; }
    /// <summary>Paket 2 (A8): die Lizenzpruefung (/status) meldete 402/403 — bis sie wieder gut ist, wird nichts
    /// gelesen (Status "Gesperrt"). Setzt TrayApp.</summary>
    public volatile bool LizenzGesperrt;
    public Fahrzeug? LetztesFahrzeug { get; private set; }
    /// <summary>Pruefung 08.10.2026 (1.5.9, D): das letzte Auto wurde beim Start nur gemerkt (kein Server-Aufruf) — "Vertrag"
    /// sagte dann faelschlich, die Inserat-ID sei nicht zu sehen. Jetzt: "erst „Vergleichen“ drücken".</summary>
    public bool LetztesNurGemerkt { get; private set; }
    internal const string NochNichtVerglichen = "Dieses Auto ist noch nicht verglichen – erst „Vergleichen“ drücken.";
    public IReadOnlyList<Vergleich> LetzteVergleiche { get; private set; } = Array.Empty<Vergleich>();
    /// <summary>Pruefung 08.10.2026 (1.5.9, A): so lange hat die Erweiterung Zeit, den Vorgang zu uebernehmen, bevor das
    /// Programm ihn beim Server fuer sich beansprucht (vorher 3 s — zu knapp, wenn der Browser erst startet). Die frueheren
    /// "30 Minuten gleich direkt" (HelferAusfallMs) sind entfallen: ob die Erweiterung zum Zug kommt, entscheidet der Server.</summary>
    internal const int VorgangWarteMs = 5000;
    /// <summary>1.5.9 (A): der neueste Vorgang, dessen Seite geoeffnet wurde — null, sobald ein anderes Auto dran ist. Nur
    /// fuer ihn oeffnet das Programm nach der Wartezeit selbst; ein alter wird nur noch beansprucht (damit eine spaete
    /// Erweiterung ihn nicht mehr oeffnet) und protokolliert — vorher sprangen die Tabs eines alten Autos auf, waehrend
    /// der Sucher schon das naechste angeklickt hatte.</summary>
    private volatile string? _aktuellerVorgang;
    /// <summary>1.5.11 (Wunsch Ahmad 08.10.2026 abends): laut letzter Server-Antwort hat das Konto die Browser-Erweiterung —
    /// "Vertrag" oeffnet dann erst das Inserat (die Erweiterung liest es), nie einen Apify-Abruf.</summary>
    public bool HatHelfer { get; private set; }
    /// <summary>1.5.11: der Browser der Erweiterung ("chrome"/"edge"/"").</summary>
    public string HelferBrowser { get; private set; } = "";
    /// <summary>1.5.10: in welchem Browser die letzten Vergleiche aufgingen ("Letzten Vergleich" nimmt denselben).</summary>
    private BrowserWahl? _letzterBrowser;
    internal const string VorgangNichtErreichbar = "AutoSchnell antwortet gerade nicht – für dieses Auto „Vergleichen“ drücken.";
    internal const string ErweiterungNichtUebernommen = "Die Browser-Erweiterung hat nicht übernommen – Vergleiche direkt geöffnet.";
    /// <summary>Die laufende Nachfrage beim Server (fuer Tests).</summary>
    internal Task? LetzteVorgangsPruefung { get; private set; }
    /// <summary>Pruefung 08.10.2026 (1.5.9, G): nach einem voruebergehenden Fehler (kein Netz, Zeitueberschreitung, 5xx,
    /// Cloudflare) wird das Auto genau einmal nach <see cref="WiederholMs"/> erneut verglichen — wenn AutoPointer es dann
    /// noch zeigt. Vorher stand "wird gleich erneut versucht" da, aber nichts geschah. null = kein Versuch offen.</summary>
    private long? _wiederholenAb;
    private bool _wiederholErzwungen;
    internal const int WiederholMs = 5000;
    /// <summary>1.5.9 (C): der lange Inserat-ID-/Hash-ID-Hinweis kommt hoechstens einmal je Programmlauf als Sprechblase.</summary>
    private bool _inseratHinweisGezeigt;
    /// <summary>Original-Inserat des zuletzt verglichenen Autos (fuer "Vertrag": in AutoSchnell oeffnen).</summary>
    public string? LetzteInseratUrl { get; private set; }
    // ---- Wunsch Ahmad 09.10.2026 (1.5.13): Bild der Anzeige bei nicht erkannten Autos an AutoSchnell -----------------
    /// <summary>Hoechstens so viele Lesebilder je Programmlauf und Tag — darueber nur noch eine Protokollzeile (der Server
    /// hat seine eigene Grenze, 429). Der Zaehler beginnt beim Datumswechsel neu.</summary>
    internal const int LesebilderJeTag = 30;
    private int _lesebilderHeute;
    private DateTime _lesebilderTag;
    /// <summary>Fuer welche Autos schon ein Bild ging (Inserat-Kennung; ohne sie der angezeigte Inhalt samt gelesenen
    /// Daten) — nie zweimal dasselbe Auto, auch nicht aus einem anderen Grund oder nach "Vergleichen". Nur die letzten
    /// <see cref="LesebilderGemerkt"/>, damit die Liste bei einem wochenlang laufenden Programm nicht waechst.</summary>
    private readonly HashSet<string> _lesebildGesendet = new();
    private readonly Queue<string> _lesebildReihe = new();
    internal const int LesebilderGemerkt = 500;
    /// <summary>Der laufende Versand (fuer Tests) — die Vergleiche warten nie darauf.</summary>
    internal Task? LetzterLesebildVersand { get; private set; }
    public Status Status => _status ?? Status.KeinAutoPointer;

    /// <summary>Statuswechsel (fuer das Symbol im Infobereich).</summary>
    public event Action<Status>? StatusGeaendert;
    /// <summary>Meldung fuer den Nutzer (Sprechblase kurz, ausfuehrlich im Fenster — 1.5.9, C).</summary>
    public event Action<Hinweis>? Meldung;
    /// <summary>Der Server kennt den Schluessel nicht mehr (anderer PC, getrennt) -> neu verbinden.</summary>
    public event Action<string>? VerbindungVerloren;
    /// <summary>Ein neues Auto ist jetzt "das letzte" (Nr. 11: ab hier zaehlt eine neu kopierte Inserat-Adresse).</summary>
    public event Action<Fahrzeug>? FahrzeugGewechselt;

    /// <param name="uhr">Datum/Uhrzeit fuer Plausibilitaet (EZ in der Zukunft) — Tests stellen sie.</param>
    /// <param name="takt">Monotone Uhr in ms fuer Abstaende; fehlt sie, kommt sie aus <paramref name="uhr"/> (Tests)
    /// bzw. ist Environment.TickCount64.</param>
    /// <param name="letzterKlick">Letzter Mausklick in AutoPointer (Klicks.LetzterKlick, Uhr wie <paramref name="takt"/>).</param>
    public Ueberwacher(IAnsichtQuelle quelle, Func<Einstellungen> einstellungen, IOeffner oeffner,
                       IVergleichsDienst dienst, Func<DateTime>? uhr = null, Func<TimeSpan, Task>? warte = null,
                       Func<long>? takt = null, Func<long>? letzterKlick = null)
    {
        _letzterKlick = letzterKlick;
        _quelle = quelle;
        _einstellungen = einstellungen;
        _oeffner = oeffner;
        _dienst = dienst;
        _uhr = uhr ?? (() => DateTime.Now);
        _takt = takt ?? (uhr != null ? () => uhr().Ticks / TimeSpan.TicksPerMillisecond : () => Environment.TickCount64);
        _warte = warte ?? (t => Task.Delay(t));
    }

    /// <summary>Beim Start und beim Wiedereinschalten: Ein Fahrzeug, das gerade
    /// schon angezeigt wird, gilt als gesehen und oeffnet keine Tabs - erst das
    /// naechste angeklickte. (Wird beim naechsten Durchlauf unter der Sperre angewendet, A11.)</summary>
    public void Neustart() => Interlocked.Exchange(ref _angefordert, 1);

    /// <summary>Befund 04.10.2026 (Mercedes nach dem Neuverbinden nicht geoeffnet): nach dem (Neu-)Verbinden wird das
    /// gerade angezeigte Auto verglichen — nicht wie beim Programmstart als "schon gesehen" behandelt, und auch
    /// dann, wenn es vor dem Trennen schon dran war. (Angewendet beim naechsten Durchlauf unter der Sperre, A11.)</summary>
    public void NachVerbinden() => Interlocked.Exchange(ref _angefordert, 2);

    /// <summary>Paket 3 (F2): solange eine Aenderung "offen" ist (Anzeige hat sich geaendert, Wartezeit laeuft), fragt
    /// die Takt-Schleife alle 100 ms statt 250 ms nach — der Vergleich geht bis zu 150 ms frueher auf. Sonst 250 ms
    /// (Pruefsumme kostet sonst unnoetig CPU).</summary>
    public bool KurzerTakt => _offen;

    /// <summary>Angeforderten Neustart/NachVerbinden anwenden — nur unter <see cref="_einzeln"/>.</summary>
    private void AnforderungAnwenden()
    {
        int a = Interlocked.Exchange(ref _angefordert, 0);
        if (a == 0) return;
        _basis = true;
        _ersterTick = true;
        _verloren = false;
        _gesperrt = false;
        _offen = false;
        _ungeklickt = false;
        _wiederholenAb = null;
        _summe = 0;
        if (a == 2)
        {
            _basis = false;
            _letzterSchluessel = null;
            _letzteKennung = null;
            _klickErsatz = _takt();
        }
    }

    /// <summary>Hat der Sucher die Aenderung von <paramref name="seit"/> ausgeloest (oder danach in AutoPointer
    /// geklickt)? Ohne Klick-Pruefung immer ja.</summary>
    private bool VomSucher(long seit)
    {
        if (_letzterKlick == null) return true;
        long klick = Math.Max(_letzterKlick(), _klickErsatz);
        return klick >= seit - KlickVorlaufMs;
    }

    public async Task TickAsync()
    {
        // A11: auch die fruehen Ausstiege (Pause, nicht verbunden) unter der Sperre, damit ein angeforderter
        // Neustart/NachVerbinden sicher angewendet wird (z. B. _verloren zuruecksetzen) und nie mitten im Lesen greift
        if (!await _einzeln.WaitAsync(0)) return;
        try
        {
            AnforderungAnwenden();
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
            if (LizenzGesperrt)
            {
                // A8: /status sagte 402/403 — nichts lesen, bis die Lizenzpruefung wieder gut ist
                SetzeStatus(Status.Gesperrt);
                _offen = false;
                _wiederholenAb = null;
                return;
            }
            var z = _quelle.Pruefe();
            bool vorne = z.Lage == Lage.Details && _quelle.ImVordergrund;
            if (_ersterTick)
            {
                _ersterTick = false;
                // Befund 3 (09.10.2026): liegt AutoPointer beim Start hinter dem Browser, ist unbekannt, was es zeigt —
                // dann zaehlt das erste Auto, das vorne zu sehen ist, wie ein neu angeklicktes (vorher las der erste Takt
                // den Browser-Inhalt und verbrauchte damit den Startzustand)
                if (z.Lage != Lage.Details || !vorne) _basis = false;
            }
            if (z.Lage != Lage.Details)
            {
                SetzeStatus(z.Lage == Lage.KeinAutoPointer ? Status.KeinAutoPointer : Status.Bereit);
                _summe = 0;
                _offen = false;
                _ungeklickt = false;
                _wiederholenAb = null;
                return;
            }
            SetzeStatus(_gesperrt ? Status.Gesperrt : Status.Aktiv);
            // Pruefung 09.10.2026 (Befund 3): solange ein anderes Fenster (Browser) vor AutoPointer liegt, wird weder eine
            // Aenderung gewertet noch gelesen — das Abbild zeigte sonst den Browser ("Fahrzeug nicht erkannt", Lesebild
            // mit fremdem Inhalt). Sobald AutoPointer wieder vorne ist, geht es an derselben Stelle weiter.
            if (!vorne) return;
            long jetzt = _takt();
            if (z.Summe != _summe)
            {
                _summe = z.Summe;
                _seit = jetzt;
                _offen = true;
                _ungeklickt = false;
                _wiederholenAb = null;          // 1.5.9 (G): ein anderes Auto — der Wiederholversuch fuers alte entfaellt
                // Paket 3 (F4): schon jetzt (vor Wartezeit und Lesen) die Verbindung zum Server vorwaermen
                try { _dienst.Vorwaermen(); } catch (Exception) { }
                return;
            }
            if (_ungeklickt)
            {
                // Wunsch Ahmad 06.10.2026: AutoPointer hat von selbst ein anderes Auto gezeigt — erst ein Klick des
                // Suchers (auch auf genau dieses Auto) gibt es frei; dann wie gewohnt warten und lesen
                if (!VomSucher(_seit)) return;
                _ungeklickt = false;
                _offen = true;
                _seit = jetzt;
                return;
            }
            // Pruefung 08.10.2026 (1.5.9, G): der eine Wiederholversuch nach einem voruebergehenden Fehler. Die Anzeige ist
            // unveraendert (sonst waere er oben schon verworfen); kein neuer Klick noetig — der Sucher hatte ja geklickt.
            bool wiederholung = false;
            if (_wiederholenAb is { } ab)
            {
                if (jetzt < ab) return;
                _wiederholenAb = null;
                wiederholung = true;
                Protokoll.Schreibe("Neuer Versuch für das angezeigte Auto.");
            }
            else
            {
                if (!_offen || jetzt - _seit < e.WartezeitMs) return;
                if (!_basis && !VomSucher(_seit))
                {
                    // Beim Start (_basis) wird nur gemerkt, nie geoeffnet — das darf ohne Klick passieren
                    _offen = false;
                    _ungeklickt = true;
                    Protokoll.SchreibeGedrosselt("ungeklickt", "AutoPointer zeigt ein anderes Auto, ohne dass in AutoPointer "
                                                 + "geklickt wurde – kein Vergleich (erst beim Anklicken).", TimeSpan.FromMinutes(5));
                    // 1.5.9 (H): einmal je Programmlauf sagen, warum nichts aufgeht (Pfeiltasten, Live-Liste)
                    if (!_mausHinweisGezeigt)
                    {
                        _mausHinweisGezeigt = true;
                        Melde(MausHinweis, false);
                    }
                    return;
                }
            }

            Lesung? lesung;
            try
            {
                lesung = await _quelle.LiesAsync();
                _lesefehler = 0;
            }
            catch (Exception ex)
            {
                // Pruefung 05.10.2026 (Paket 1): vorher blieb _offen stehen — derselbe Inhalt wurde alle 250 ms neu
                // gelesen (volle Texterkennung) und jeder Fehler samt Stapel protokolliert. Jetzt: 3 Versuche mit
                // 2/4/6 s Abstand, dann ist dieser Inhalt erledigt (naechste Aenderung oder "Vergleichen").
                // 1.5.9 (J): eine haengende Texterkennung zaehlt nach 10 s ebenso (TimeoutException).
                _lesefehler++;
                Protokoll.Schreibe($"Lesen fehlgeschlagen ({_lesefehler}. Versuch): {ex.GetType().Name}: {ex.Message}");
                if (_lesefehler >= LeseVersuche || wiederholung)
                {
                    _offen = false;
                    _lesefehler = 0;
                    MeldeEinmal("Die Anzeige konnte nicht gelesen werden – das Auto noch einmal anklicken oder „Vergleichen“ drücken.");
                }
                else
                {
                    _seit = _takt() + 2000 * _lesefehler;
                }
                return;
            }
            var nach = _quelle.Pruefe();
            if (nach.Lage != Lage.Details || nach.Summe != _summe)
            {
                // Waehrend des Lesens hat AutoPointer weitergeblaettert: verwerfen,
                // sonst koennten Werte zweier Fahrzeuge gemischt werden.
                _summe = nach.Summe;
                _seit = _takt();
                _offen = true;                  // 1.5.9: auch nach einem Wiederholversuch eine neue Aenderung
                return;
            }
            _offen = false;
            if (lesung == null) return;
            await VerarbeiteAsync(lesung, e, erzwungen: wiederholung && _wiederholErzwungen, wiederholung);
        }
        finally { _einzeln.Release(); }
    }

    /// <summary>Knopf "Vergleichen": sofort lesen und oeffnen - auch bei Pause und auch, wenn es dasselbe Fahrzeug ist.
    /// (Dass AutoPointer dafuer vorne liegt, sorgt TrayApp — 1.5.9, I.)</summary>
    public async Task JetztVergleichenAsync()
    {
        // Pruefung 05.10.2026 (Paket 1): laeuft gerade ein Lesen (Doppelklick auf "Vergleichen", Knopf waehrend die
        // Automatik liest), nicht anstellen — sonst gingen dieselben Vergleiche zweimal auf
        if (!await _einzeln.WaitAsync(0))
        {
            Protokoll.Schreibe("„Vergleichen“: es läuft schon ein Lesen – nicht doppelt.");
            return;
        }
        try
        {
            AnforderungAnwenden();
            _wiederholenAb = null;              // 1.5.9 (G): der Knopf ersetzt einen offenen Wiederholversuch
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
                // 1.5.9 (E): seit 1.5.8 verbindet man ueber die Leiste, nicht mehr per Rechtsklick aufs Symbol
                Melde("Nicht mit AutoSchnell verbunden – auf der Leiste „NICHT VERBUNDEN“ anklicken und den Code eingeben.", true);
                return;
            }
            if (!_quelle.ImVordergrund)
            {
                // Pruefung 09.10.2026 (Befund 3, letzte Sicherung — TrayApp prueft es schon vor dem Aufruf): liegt kein
                // AutoPointer-Fenster vorne, wird NICHT gelesen — und damit entsteht auch nie ein Lesebild mit fremdem Inhalt
                Protokoll.Schreibe("„Vergleichen“: AutoPointer liegt nicht vorne – nicht gelesen.");
                Melde(AutoPointerNichtVorne, true);
                return;
            }
            var lesung = await _quelle.LiesAsync();
            if (lesung == null) return;
            _summe = _quelle.Pruefe().Summe;
            _offen = false;
            _ungeklickt = false;
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
                : LetztesNurGemerkt ? NochNichtVerglichen
                : "Für das zuletzt angeklickte Auto gibt es keinen Vergleich – „Vergleichen“ drücken.", false);
            return;
        }
        Protokoll.Schreibe("Letzten Vergleich erneut geöffnet.");
        Oeffne(LetzteVergleiche, _einstellungen(), _letzterBrowser);
    }

    /// <param name="wiederholung">1.5.9 (G): der eine Wiederholversuch nach einem voruebergehenden Fehler — schlaegt er
    /// auch fehl, wird nicht noch einmal versucht.</param>
    private async Task VerarbeiteAsync(Lesung lesung, Einstellungen e, bool erzwungen, bool wiederholung = false)
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
            _aktuellerVorgang = null;           // 1.5.9 (A): ein anderes (unlesbares) Auto ist dran
            Protokoll.Schreibe($"Fahrzeug konnte nicht eindeutig erkannt werden – fehlt: {string.Join(", ", fehlt)}\n"
                               + $"Gelesen: {lesung.Rohtext}");
            MeldeEinmal("Fahrzeug konnte nicht eindeutig erkannt werden (fehlt: " + string.Join(", ", fehlt) + ")."
                        + (lesung.Weg == "Bildschirm" ? " Detailbereich in AutoPointer größer ziehen." : ""));
            // 1.5.13 (Wunsch Ahmad 09.10.2026): das gelesene Bild an AutoSchnell — hier sind alle Durchgaenge (zweiter
            // Durchlauf, zweiter Blick) schon durch; einmal je angezeigtem Inhalt, wie die Meldung darueber
            LesebildSenden(lesung, Lesebilder.PflichtfeldFehlt, e, fehlt);
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
        // 1.5.9 (A): ab hier ist ein anderes Auto (oder "Vergleichen") dran — ein noch offener Vorgang ist nicht mehr
        // der neueste und oeffnet nichts mehr
        _aktuellerVorgang = null;
        bool basis = _basis && !erzwungen;
        _basis = false;
        if (basis)
        {
            Merken(f);
            LetztesFahrzeug = f;
            LetztesNurGemerkt = true;
            // Nr. 2: zu diesem Auto gibt es noch keinen Vergleich — keine Links/Inserat eines frueheren Autos
            LetzteVergleiche = Array.Empty<Vergleich>();
            LetzteInseratUrl = null;
            FahrzeugGewechselt?.Invoke(f);
            Protokoll.Schreibe("Fahrzeug war beim Start schon angezeigt – nicht automatisch geöffnet (Knopf „Vergleichen“).");
            return;
        }
        VergleichAntwort antwort;
        try { antwort = await _dienst.VergleichAsync(f, Probelauf); }
        catch (DienstFehler ex)
        {
            // Schluessel bleibt der alte: nach Behebung (Abo, Verbindung) vergleicht
            // derselbe Klick bzw. "Vergleichen" erneut.
            Protokoll.Schreibe("AutoSchnell: " + ex.Message);
            if (ex.NichtVerbunden)
            {
                _verloren = true;
                SetzeStatus(Status.NichtVerbunden);
                VerbindungVerloren?.Invoke(ex.Message);
                Melde(ex.Message, true);
                return;
            }
            if (ex.KeinAbo || ex.Gesperrt)
            {
                // 1.5.9 (G): nur ein echtes 403 von AutoSchnell sperrt — eines von Cloudflare (ohne JSON) ist voruebergehend
                _gesperrt = true;
                SetzeStatus(Status.Gesperrt);
                Melde(ex.Message, true);
                return;
            }
            // Pruefung 08.10.2026 (1.5.9, G): voruebergehend (kein Netz, Zeitueberschreitung, 5xx, Cloudflare, Antwort zu
            // einer frueheren Verbindung) -> genau ein Wiederholversuch in 5 s, wenn dann noch dasselbe Auto angezeigt
            // wird. Der Text sagt nur, was wirklich passiert. (Bei Pause laeuft der Takt nicht — dann kein Versprechen.)
            bool voruebergehend = ex.Voruebergehend || ex.Veraltet;
            if (voruebergehend && !wiederholung && e.AutomatikAktiv)
            {
                _wiederholenAb = _takt() + WiederholMs;
                _wiederholErzwungen = erzwungen;
                Protokoll.Schreibe($"Neuer Versuch in {WiederholMs / 1000} s, wenn AutoPointer dann noch dasselbe Auto zeigt.");
                Melde(ex.Message + " Neuer Versuch in 5 Sekunden.", true);
            }
            else Melde(ex.Message + (voruebergehend ? " Für dieses Auto später „Vergleichen“ drücken." : ""), true);
            return;
        }
        _wiederholenAb = null;
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
        LetztesNurGemerkt = false;
        LetzteInseratUrl = antwort.InseratUrl;
        // 1.5.11: hat das Konto die Erweiterung (und in welchem Browser)? "Vertrag" oeffnet dann erst das Inserat
        HatHelfer = antwort.HatHelfer;
        HelferBrowser = antwort.HelferBrowser;
        // Pruefbericht 03.10.2026 (Nr. 2): ab hier gehoeren die "letzten Vergleiche" zu DIESEM Auto — auch wenn
        // es keine gibt. Vorher blieben die Links des vorigen Autos stehen ("erneut oeffnen" zeigte das falsche Auto).
        LetzteVergleiche = Array.Empty<Vergleich>();
        FahrzeugGewechselt?.Invoke(f);
        if (!antwort.MarkeErkannt)
        {
            MeldeEinmal($"Marke in „{f.MarkeModellText}“ nicht erkannt – kein Vergleich.");
            LesebildSenden(lesung, Lesebilder.MarkeUnbekannt, e, vorgangId: antwort.VorgangId);
            return;
        }
        // 1.5.13: Marke bekannt, aber der Server fand kein Modell — das Bild zeigt, was wirklich dastand (Katalog/Reparaturen)
        if (!antwort.ModellGefunden) LesebildSenden(lesung, Lesebilder.ModellUnbekannt, e, vorgangId: antwort.VorgangId);
        foreach (var v in antwort.Links) Protokoll.Schreibe($"{v.Portal} URL (Regeln {antwort.Profil}): {v.Url}");
        foreach (var h in antwort.Hinweise) Protokoll.Schreibe("Hinweis: " + h);
        Protokoll.Schreibe(antwort.InseratUrl != null
            ? $"Inserat: {antwort.InseratUrl} – für den Kaufvertrag vorab ausgelesen: {antwort.VorabStatus}"
            : "Inserat-Adresse unbekannt – für den Kaufvertrag von Hand einfügen.");
        // Hinweise fuer den Kaufvertrag (Wunsch Ahmad 03.10.2026). 1.5.9 (C): die lange Anleitung (Inserat-ID, Hash-ID)
        // steht ganz im Fenster; die Sprechblase bekommt die Kurzform, und die nur einmal je Programmlauf.
        string? inseratLang = null, inseratKurz = null, vorabHinweis = null;
        if (antwort.InseratUrl == null && (f.Quelle ?? "").Contains("AutoScout", StringComparison.OrdinalIgnoreCase))
            (inseratLang, inseratKurz) = (KeinLinkHinweis, KeinLinkKurz);
        // Befund Ahmad 08.10.2026 (Liste: bei vielen mobile.de- und einigen Kleinanzeigen-Autos kein "Inserat öffnen"):
        // die Zeile "Inserat-ID" steht ganz unten in der Tabelle — ist der Detailbereich zu niedrig, liegt sie
        // ausserhalb des Bildschirms und wird nicht gelesen. Gleich sagen, wie es geht (sonst merkt man es erst beim
        // Kaufvertrag).
        else if (antwort.InseratUrl == null && f.Quelle is "mobile.de" or "Kleinanzeigen")
            (inseratLang, inseratKurz) = (KeineNummerHinweis, KeineNummerKurz);
        else if (antwort.VorabStatus is "limit" or "fehler" && antwort.VorabHinweis.Length > 0)
            vorabHinweis = "Für den Kaufvertrag nicht vorab ausgelesen: " + antwort.VorabHinweis;
        // 1.5.13: Inserat-ID (mobile.de/Kleinanzeigen) bzw. Hash-ID (AutoScout) nicht gelesen — das Bild zeigt, ob die Zeile
        // fehlte oder nur falsch gelesen wurde (nicht noch einmal, wenn fuer dieses Auto schon ein Bild ging)
        if (inseratLang != null) LesebildSenden(lesung, Lesebilder.InseratIdFehlt, e, vorgangId: antwort.VorgangId);
        // 1.5.8 (Wunsch Ahmad 08.10.2026): die Portalwahl steht in AutoSchnell, der Server schickt nur deren Links —
        // hier nur noch die beiden bekannten Vergleichsportale (alles andere ignorieren)
        var links = antwort.Links.Where(l => l.Portal is "mobile.de" or "AutoScout24").ToList();
        // Wunsch Ahmad 07.10.2026: hat das Konto den Browser-Helfer, oeffnen wir das Inserat als Tab mit — der Helfer
        // liest es dort fuer den Kaufvertrag, ganz ohne Apify.
        // 1.5.10 (Befund Ahmad 08.10.2026 abends, "oeffnet eher das Inserat statt der Vergleiche"): das Inserat ZUERST,
        // die Vergleiche danach — der zuletzt geoeffnete Tab liegt vorne, und vorne gehoert der Vergleich hin (das Auto
        // selbst sieht der Sucher ja schon in AutoPointer). Der Helfer liest das Inserat auch im Hintergrund.
        if (antwort.InseratImBrowser && antwort.InseratUrl != null && links.Count > 0)
            links.Insert(0, new Vergleich("Inserat", antwort.InseratUrl));
        LetzteVergleiche = links;
        if (links.Count == 0)
        {
            MeldeEinmal($"{f.Marke} {f.Modell}: kein Vergleich geöffnet – "
                        + (antwort.Hinweise.FirstOrDefault() ?? "keine Links (Portalwahl in AutoSchnell prüfen)."));
            // 1.5.9 (C): die Vertrags-Hinweise nur ins Fenster — die Sprechblase gehoert "kein Vergleich" (vorher
            // verdraengte der Vertrags-Hinweis sie oft, Sprechblasen kommen hoechstens alle 3 s)
            var still = new[] { inseratLang, vorabHinweis }.OfType<string>().ToList();
            if (still.Count > 0) Melde(still[0], false, string.Join("\n", still), blase: false);
            return;
        }

        if (_letzteOeffnung is { } letzte)
        {
            // A10: monoton gemessen und auf den Mindestabstand begrenzt — ein Sprung der Uhr (oder eine Test-Uhr,
            // die zurueckgestellt wird) fuehrt nie zu einer Wartezeit laenger als MindestabstandMs
            long abstand = Math.Clamp(letzte + e.MindestabstandMs - _takt(), 0, e.MindestabstandMs);
            if (abstand > 0) await _warte(TimeSpan.FromMilliseconds(abstand));
        }
        // 1.5.8 (Wunsch Ahmad 08.10.2026, Vorgangsnummer): hat das Konto die Browser-Erweiterung, oeffnet das Programm
        // nur die Vorgangsseite — die Erweiterung holt sich den Vorgang und oeffnet Vergleiche + Inserat selbst (sie kennt
        // dann ihre Tabs, nichts wird doppelt geoeffnet oder erraten).
        // Pruefung 08.10.2026 (1.5.9, A): die Seite geht im Browser der Erweiterung auf (bei "Standardbrowser"; sonst sah
        // sie die Seite nie), nach 5 s beansprucht das Programm den Vorgang beim Server (VorgangPruefenAsync) — erst dann,
        // und nur wenn ihn niemand hat, oeffnet es selbst.
        if (antwort.UeberHelfer && antwort.VorgangId is { } vorgang && !Probelauf)
        {
            var browser = BrowserFuer(e.Browser, antwort.HelferBrowser);
            _aktuellerVorgang = vorgang;
            Oeffne(new[] { new Vergleich("Vorgang", $"{e.Server.TrimEnd('/')}/app/vorgang/{vorgang}") }, e, browser);
            LetzteVorgangsPruefung = VorgangPruefenAsync(vorgang, links, e, browser);
        }
        else
        {
            // 1.5.10: auch der direkte Weg geht in den Browser der Erweiterung (bei "Standardbrowser") — sonst las sie das
            // Inserat nicht (Kaufvertrag ohne Daten) und die Vergleichsseiten bekamen keine Ampel
            _letzterBrowser = BrowserFuer(e.Browser, antwort.HelferBrowser);
            Oeffne(links, e, _letzterBrowser);
        }
        _letzteOeffnung = _takt();
        var hinweise = antwort.Hinweise.Where(h => h.Contains("kein mobile.de-Vergleich") || h.Contains("kein AutoScout24-Vergleich")).ToList();
        var plausi = PlausibilitaetsHinweise(f, _uhr());
        if (antwort.Melden != null)
        {
            // Seit 04.10.2026 prueft der Server EZ/km selbst und laesst den falschen Filter weg — dann seine Hinweise
            // statt der eigenen Kilometer-Meldung (sonst doppelt); was das Lesen betrifft (Leistung), bleibt
            hinweise.InsertRange(0, antwort.Melden);
            plausi = plausi.Where(h => !h.StartsWith("Kilometerstand ungewöhnlich hoch", StringComparison.Ordinal)).ToList();
        }
        hinweise.AddRange(plausi);
        if (vorabHinweis != null) hinweise.Add(vorabHinweis);
        MeldeHinweise(hinweise, inseratLang, inseratKurz);
    }

    /// <summary>Pruefung 08.10.2026 (1.5.9, C): alle Hinweise zu einem Auto als EINE Meldung — in der Sprechblase der erste
    /// (gekuerzt) und "+N weitere", ganz im Fenster "Status und Hilfe". Der lange Inserat-ID-/Hash-ID-Hinweis kommt als
    /// Sprechblase (Kurzform) hoechstens einmal je Programmlauf; danach steht er nur noch im Fenster und im Protokoll.</summary>
    private void MeldeHinweise(List<string> hinweise, string? inseratLang, string? inseratKurz)
    {
        var kurz = new List<string>(hinweise);
        var lang = new List<string>(hinweise);
        if (inseratLang != null)
        {
            lang.Add(inseratLang);
            if (!_inseratHinweisGezeigt)
            {
                _inseratHinweisGezeigt = true;
                kurz.Add(inseratKurz ?? inseratLang);
            }
            else Protokoll.Schreibe("Hinweis (nur im Fenster): " + inseratLang);
        }
        if (lang.Count == 0) return;
        string ganz = string.Join("\n", lang);
        if (kurz.Count == 0)
        {
            Melde(lang[0], false, ganz, blase: false);
            return;
        }
        string text = kurz[0];
        if (kurz.Count > 1)
        {
            string rest = $" (+{kurz.Count - 1} weitere im Fenster „Status und Hilfe“)";
            text = Hinweis.Kuerzen(text, Hinweis.MaxZeichen - rest.Length) + rest;
        }
        Melde(text, false, ganz);
    }

    /// <summary>Pruefung 08.10.2026 (1.5.9, A): in welchem Browser geht die Vorgangsseite auf? Wer Edge oder Chrome
    /// eingestellt hat, bekommt den; bei "Standardbrowser" der Browser, in dem die Erweiterung verbunden ist (helfer_browser
    /// vom Server) — sonst landete die Seite z. B. in Firefox, die Erweiterung in Chrome sah sie nie, und nach der
    /// Wartezeit ging alles direkt auf. Fehlt der Browser, nimmt BrowserOeffner den Standardbrowser. (rein, fuer Tests)</summary>
    internal static BrowserWahl BrowserFuer(BrowserWahl eingestellt, string? helferBrowser) =>
        eingestellt != BrowserWahl.Standard ? eingestellt
        : helferBrowser == "chrome" ? BrowserWahl.Chrome
        : helferBrowser == "edge" ? BrowserWahl.Edge
        : BrowserWahl.Standard;

    /// <summary>Pruefung 08.10.2026 (1.5.9, A): nach <see cref="VorgangWarteMs"/> den Vorgang beim Server fuer das Programm
    /// beanspruchen (POST …/selbst) — eindeutig: entweder die Erweiterung oeffnet oder das Programm, nie beide.
    /// true + noch der neueste Vorgang -> selbst oeffnen (im selben Browser wie die Vorgangsseite); true, aber inzwischen
    /// ein anderes Auto -> nichts (nur Protokoll); false -> die Erweiterung hat ihn; null (AutoSchnell nicht erreichbar,
    /// auch nach dem zweiten Versuch) -> NICHT oeffnen (sonst womoeglich doppelt), kurzer Hinweis.</summary>
    private async Task VorgangPruefenAsync(string vorgang, IReadOnlyList<Vergleich> links, Einstellungen e, BrowserWahl browser)
    {
        try
        {
            await _warte(TimeSpan.FromMilliseconds(VorgangWarteMs));
            // auch fuer ein inzwischen altes Auto beanspruchen: dann kann eine spaete Erweiterung es nicht mehr oeffnen
            bool? selbst = await _dienst.VorgangSelbstAsync(vorgang);
            bool aktuell = _aktuellerVorgang == vorgang;
            if (selbst == false)
            {
                Protokoll.Schreibe($"Vorgang {vorgang}: die Browser-Erweiterung hat übernommen (oder er ist abgelaufen) – das Programm öffnet nichts.");
                return;
            }
            if (selbst == null)
            {
                Protokoll.Schreibe($"Vorgang {vorgang}: AutoSchnell nicht erreichbar – Vergleiche NICHT selbst geöffnet (sonst womöglich doppelt).");
                if (aktuell) Melde(VorgangNichtErreichbar, true);
                return;
            }
            if (!aktuell)
            {
                Protokoll.Schreibe($"Vorgang {vorgang}: inzwischen ist ein anderes Auto dran – die alten Vergleiche werden nicht mehr geöffnet.");
                return;
            }
            Protokoll.Schreibe($"Vorgang {vorgang}: die Browser-Erweiterung hat nicht übernommen – Vergleiche direkt geöffnet.");
            Oeffne(links, e, browser);
            Melde(ErweiterungNichtUebernommen, false);
        }
        catch (Exception ex) { Protokoll.Schreibe("Vorgang prüfen: " + ex.Message); }
    }

    private void Merken(Fahrzeug f)
    {
        _letzterSchluessel = f.Schluessel;
        _letzteKennung = f.InseratKennung;
    }

    /// <summary>Wunsch Ahmad 09.10.2026 (1.5.13, "wenn er etwas ausliest und nicht erkennt: automatisch ein Screenshot des
    /// nicht Erkannten"): das gelesene Bild an AutoSchnell schicken — im Hintergrund (die Vergleiche warten nicht), je Auto
    /// genau einmal (Inserat-Kennung; ohne sie der angezeigte Inhalt samt gelesenen Daten — ein anderer Ausschnitt desselben
    /// Autos ohne Kennung ist dann ein neues Bild, das darf es: er zeigt ja etwas anderes), hoechstens
    /// <see cref="LesebilderJeTag"/> je Tag, nie im Probelauf, nie bei abgeschalteter Einstellung. Jeder Fehler ist nur eine
    /// Protokollzeile. Laeuft unter <see cref="_einzeln"/> (aus VerarbeiteAsync) — Zaehler und Liste brauchen keine Sperre.</summary>
    private void LesebildSenden(Lesung lesung, string grund, Einstellungen e, IReadOnlyList<string>? fehlt = null,
                                string? vorgangId = null)
    {
        if (!e.LesebilderSenden) return;
        var f = lesung.Fahrzeug;
        string schluessel = f.InseratKennung ?? $"{_summe}|{f.Schluessel}";
        if (_lesebildGesendet.Contains(schluessel)) return;
        if (Probelauf)
        {
            Protokoll.Schreibe($"Probelauf: Lesebild ({grund}) NICHT gesendet.");
            return;
        }
        if (lesung.Bild == null)
        {
            Protokoll.Schreibe($"Lesebild ({grund}): zu dieser Lesung gibt es kein Bild – nichts gesendet.");
            return;
        }
        var heute = _uhr().Date;
        if (heute != _lesebilderTag)
        {
            _lesebilderTag = heute;
            _lesebilderHeute = 0;
        }
        if (_lesebilderHeute >= LesebilderJeTag)
        {
            Protokoll.Schreibe($"Lesebild ({grund}) nicht gesendet – heute schon {LesebilderJeTag} Bilder (Tagesgrenze des Programms).");
            return;
        }
        _lesebilderHeute++;
        _lesebildGesendet.Add(schluessel);
        _lesebildReihe.Enqueue(schluessel);
        while (_lesebildReihe.Count > LesebilderGemerkt) _lesebildGesendet.Remove(_lesebildReihe.Dequeue());
        int nummer = _lesebilderHeute;
        var bild = new Lesebild(grund, lesung.Bild, lesung.Rohtext, f, fehlt, vorgangId);
        // Task.Run: auch der Aufbau der Anfrage (Base64 von bis zu 1,5 MB, JSON) kostet den Vergleich keine Zeit
        LetzterLesebildVersand = Task.Run(async () =>
        {
            try
            {
                if (await _dienst.LesebildSendenAsync(bild))
                    Protokoll.Schreibe($"Lesebild gesendet ({grund}): {f.MarkeModellText} · {bild.Bild.Length / 1024} KB – heute {nummer} von {LesebilderJeTag}.");
            }
            catch (Exception ex) { Protokoll.Schreibe($"Lesebild ({grund}) nicht gesendet: {ex.Message}"); }
        });
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

    // Pruefung 08.10.2026 (1.5.9, C/E): die langen Anleitungen stehen ganz im Fenster "Status und Hilfe" (Letzte Hinweise);
    // in der Sprechblase nur die Kurzform (Windows schnitt sie bei ~255 Zeichen ab — genau die Anweisungen fehlten).
    // Der Knopf heisst "Vertrag", nicht "Kaufvertrag".
    internal const string KeineNummerHinweis =
        "Inserat-Nummer nicht gelesen: in AutoPointer ist die Zeile „Inserat-ID“ (ganz unten in der Tabelle) nicht zu sehen. Detailbereich in AutoPointer höher ziehen, bis „Inserat-ID“ sichtbar ist – dann klappen „Inserat öffnen“ und der Kaufvertrag von selbst. Für dieses Auto: in AutoPointer „Seite öffnen“, im Browser die Adresse kopieren (Strg+L, dann Strg+C) und hier noch einmal „Vertrag“ drücken.";
    internal const string KeineNummerKurz =
        "Inserat-ID nicht gelesen – Detailbereich in AutoPointer höher ziehen. Anleitung: „Mehr ▾“ → „Status und Hilfe“.";

    /// <summary>Der passende Hinweis, wenn fuer den Kaufvertrag die Inserat-Adresse fehlt.</summary>
    internal static string LinkHinweisFuer(Fahrzeug f) =>
        (f.Quelle ?? "").Contains("AutoScout", StringComparison.OrdinalIgnoreCase) ? KeinLinkHinweis : KeineNummerHinweis;

    internal const string KeinLinkHinweis =
        "AutoScout-Inserat: die Kennung (Hash-ID) ist in AutoPointer nicht vollständig sichtbar. Für den Kaufvertrag: in AutoPointer „Seite öffnen“, im Browser die Adresse kopieren (Strg+L, dann Strg+C) und hier noch einmal „Vertrag“ drücken – das Programm übernimmt die kopierte Adresse. Tipp: Detailbereich in AutoPointer breiter ziehen – dann klappt es automatisch.";
    internal const string KeinLinkKurz =
        "AutoScout-Kennung (Hash-ID) nicht lesbar – Detailbereich in AutoPointer breiter ziehen. Anleitung: „Mehr ▾“ → „Status und Hilfe“.";

    /// <summary>1.5.9 (C): Sprechblase, wenn "Vertrag" gedrueckt wird und die Inserat-Adresse fehlt (lang: <see cref="LinkHinweisFuer"/>).</summary>
    internal const string VertragOhneAdresse =
        "Inserat-Adresse fehlt: in AutoPointer „Seite öffnen“, im Browser die Adresse kopieren (Strg+L, Strg+C), dann noch einmal „Vertrag“.";

    private void Oeffne(IReadOnlyList<Vergleich> links, Einstellungen e, BrowserWahl? browser = null)
    {
        if (Probelauf)
        {
            Protokoll.Schreibe($"Probelauf: {links.Count} Vergleich(e) NICHT geöffnet.");
            return;
        }
        try
        {
            _oeffner.Oeffne(links, e, _quelle.Hauptfenster, browser ?? e.Browser);
            Protokoll.Schreibe($"Vergleiche geöffnet ({string.Join(" + ", links.Select(l => l.Portal))}"
                               + (browser is { } b && b != e.Browser ? $", im Browser der Erweiterung: {b}" : "") + ").");
        }
        catch (Exception ex)
        {
            // 1.5.9 (E): keine englische .NET-Meldung in der Sprechblase — die steht im Protokoll
            Protokoll.Schreibe("Browser konnte nicht geöffnet werden: " + ex);
            Melde("Der Browser ließ sich nicht öffnen – in den Einstellungen einen anderen Browser wählen.", true);
        }
    }

    /// <summary>Fehlermeldung nur einmal je angezeigtem Inhalt - kein Dauerfeuer.</summary>
    private void MeldeEinmal(string text)
    {
        if (_gemeldeteSumme == _summe && _summe != 0) return;
        _gemeldeteSumme = _summe;
        Melde(text, true);
    }

    /// <summary>1.5.9 (C): die Sprechblase bekommt hoechstens <see cref="Hinweis.MaxZeichen"/> Zeichen; ist der Text
    /// laenger (z. B. ein Text des Servers), steht er ganz im Fenster.</summary>
    private void Melde(string text, bool fehler, string? ausfuehrlich = null, bool blase = true)
    {
        string kurz = Hinweis.Kuerzen(text);
        if (ausfuehrlich == null && kurz != text.Trim()) ausfuehrlich = text.Trim();
        Meldung?.Invoke(new Hinweis(kurz, fehler, ausfuehrlich != kurz ? ausfuehrlich : null, blase));
    }

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
            Status.TexterkennungFehlt => "Windows-Texterkennung fehlt – es wird nichts gelesen.",
            _ => "AutoPointer-Detailansicht erkannt.",
        });
        StatusGeaendert?.Invoke(s);
    }
}

/// <summary>Pruefung 09.10.2026 (Befund 4/5): die Fenster-Zugriffe der Quelle — echt Win32 (<see cref="Echt"/>), in Tests
/// Attrappen, damit sich Drossel und Handle-Pruefung ohne AutoPointer pruefen lassen.</summary>
internal sealed record FensterZugriff(Func<IntPtr> FindeHauptfenster, Func<IntPtr, bool> IstHauptfenster,
                                      Func<IntPtr, DetailAnsicht?> FindeDetails, Func<DetailAnsicht, bool> NochGueltig,
                                      Func<IntPtr, bool> ImVordergrund, Func<IntPtr, ulong> Pruefsumme)
{
    public static FensterZugriff Echt { get; } = new(AutoPointerFenster.FindeHauptfenster, AutoPointerFenster.IstHauptfenster,
                                                    AutoPointerFenster.FindeDetails, AutoPointerFenster.NochGueltig,
                                                    AutoPointerFenster.ImVordergrund, AutoPointerFenster.Pruefsumme);
}

/// <summary>Die echte Quelle: AutoPointer-Fenster, PrintWindow, Windows-OCR.</summary>
internal sealed class AutoPointerQuelle : IAnsichtQuelle
{
    private readonly TextErkennung? _ocr;
    private readonly Func<Einstellungen> _einstellungen;
    private readonly FensterZugriff _fenster;
    private readonly Func<long> _takt;
    private DetailAnsicht? _ansicht;
    private IntPtr _haupt;
    private long _letzteSuche = long.MinValue / 2;      // A10: monoton (TickCount64)
    private long _letzteDetailSuche = long.MinValue / 2;
    /// <summary>Die Suche nach dem Hauptfenster (EnumWindows ueber alle Fenster) hoechstens alle 2 s.</summary>
    internal const int HauptSucheMs = 2000;
    /// <summary>Pruefung 09.10.2026 (Befund 5): die Suche nach der Detailansicht (EnumChildWindows ueber 500–1.500
    /// Kindfenster von AutoPointer) hoechstens jede Sekunde, solange AutoPointer kein Auto zeigt — vorher lief sie alle
    /// 250 ms ungedrosselt (nur die Hauptfenster-Suche war gedrosselt).</summary>
    internal const int DetailSucheMs = 1000;

    public AutoPointerQuelle(TextErkennung ocr, Func<Einstellungen> einstellungen)
        : this(ocr, einstellungen, FensterZugriff.Echt, null) { }

    /// <param name="ocr">null nur in Tests — dann liest <see cref="LiesAsync"/> nichts.</param>
    /// <param name="takt">Monotone Uhr in ms (Tests); sonst Environment.TickCount64.</param>
    internal AutoPointerQuelle(TextErkennung? ocr, Func<Einstellungen> einstellungen, FensterZugriff fenster, Func<long>? takt)
    {
        _ocr = ocr;
        _einstellungen = einstellungen;
        _fenster = fenster;
        _takt = takt ?? (() => Environment.TickCount64);
    }

    public IntPtr Hauptfenster => _haupt;

    /// <summary>Befund 3 (09.10.2026): liegt ein Fenster von AutoPointer vorne? (Fuer den Takt und "Vergleichen".)</summary>
    public bool ImVordergrund => _haupt != IntPtr.Zero && _fenster.ImVordergrund(_haupt);

    public QuellenZustand Pruefe()
    {
        if (_ansicht == null || !_fenster.NochGueltig(_ansicht))
        {
            _ansicht = null;
            // Befund 4 (09.10.2026): auch das Hauptfenster-Handle kann nach einem AutoPointer-Neustart ein fremdes Fenster
            // sein (IsWindow sagt dann noch "ja") — es muss weiter ein TMainForm sein, sonst wird neu gesucht
            if (!_fenster.IstHauptfenster(_haupt))
            {
                _haupt = IntPtr.Zero;
                if (_takt() - _letzteSuche < HauptSucheMs) return new QuellenZustand(Lage.KeinAutoPointer, 0);
                _letzteSuche = _takt();
                _haupt = _fenster.FindeHauptfenster();
                if (_haupt == IntPtr.Zero) return new QuellenZustand(Lage.KeinAutoPointer, 0);
            }
            if (_takt() - _letzteDetailSuche < DetailSucheMs) return new QuellenZustand(Lage.KeineDetails, 0);
            _letzteDetailSuche = _takt();
            _ansicht = _fenster.FindeDetails(_haupt);
            if (_ansicht == null) return new QuellenZustand(Lage.KeineDetails, 0);
        }
        // Ein anderes Fahrzeug kann nur erscheinen, wenn der Sucher in AutoPointer
        // klickt - dann ist es vorne. Liegt ein anderes Fenster (Browser) davor,
        // gilt die letzte Pruefsumme: kostet nichts und verdeckte Pixel loesen
        // kein erneutes Lesen aus.
        if (_letzteSumme != 0 && !_fenster.ImVordergrund(_haupt)) return new QuellenZustand(Lage.Details, _letzteSumme);
        ulong summe = _fenster.Pruefsumme(_ansicht.TechnikTabelle);
        summe = (summe * 31) ^ _fenster.Pruefsumme(_ansicht.KopfTabelle);
        _letzteSumme = summe == 0 ? 1 : summe;
        return new QuellenZustand(Lage.Details, _letzteSumme);
    }

    private ulong _letzteSumme;

    public async Task<Lesung?> LiesAsync()
    {
        var ansicht = _ansicht;
        if (ansicht == null || _ocr == null) return null;
        var e = _einstellungen();
        return await LiesAnsichtAsync(_ocr, ansicht, e.ErkennungsbilderSpeichern, lesebild: e.LesebilderSenden);
    }

    /// <summary>Befund 03.10.2026: AutoPointer zeigte eine "Zugriffsverletzung" (aprun.exe). Es stuerzt
    /// nachweislich auch ohne uns ab (24.09.), aber PrintWindow laesst AutoPointer selbst zeichnen —
    /// deshalb zuerst nur den Bildschirminhalt kopieren (keine Nachricht an AutoPointer). PrintWindow
    /// nur noch, wenn darin Pflichtzeilen oder die Inserat-ID fehlen (weggescrollt, schmale Ansicht).</summary>
    /// <remarks>Zweiter Befund 03.10.2026 abends: dieselbe Zugriffsverletzung (Offset 16B050B) noch einmal —
    /// PrintWindow nur noch, wenn der Sucher es in den Einstellungen ausdruecklich erlaubt
    /// (<paramref name="zeichnenErlaubt"/>). Sonst gilt, was auf dem Bildschirm steht.</remarks>
    /// <param name="lesebild">1.5.13: das gelesene Bild als PNG mitgeben (<see cref="Lesung.Bild"/>) — aus, wenn der Sucher
    /// das Senden abgeschaltet hat (dann auch kein Kodieren).</param>
    internal static async Task<Lesung?> LiesAnsichtAsync(TextErkennung ocr, DetailAnsicht ansicht, bool bilderSpeichern,
                                                         Action<System.Drawing.Bitmap, System.Drawing.Bitmap?>? bilder = null,
                                                         bool lesebild = true)
    {
        // Beschreibung GLEICHZEITIG mit der Tabelle lesen (eigene Texterkennung) — sonst +0,15-0,2 s je Auto
        var beschreibung = BeschreibungLesenAsync(WeitereErkennung(0) ?? ocr, ansicht);
        var lesung = await LiesTabellenAsync(ocr, ansicht, bilderSpeichern, bilder, lesebild);
        string? text = await beschreibung;
        if (lesung != null) lesung.Fahrzeug.BeschreibungText = text;
        return lesung;
    }

    /// <summary>Pruefung 05.10.2026 (Paket 3, F1): weitere Texterkennungen fuer parallele Durchgaenge — 0: Beschreibung,
    /// 1: Kopf-Tabelle, 2: zweiter Durchgang der Technik-Tabelle. Eine OcrEngine arbeitet nur einen Auftrag auf einmal
    /// ab; mit eigener Engine laufen die Durchgaenge auf mehreren Kernen gleichzeitig. Einmal erzeugt, dann behalten;
    /// null, wenn das Erzeugen fehlschlaegt (dann liest die Haupt-Engine nacheinander wie bisher).</summary>
    private static readonly TextErkennung?[] _weitere = new TextErkennung?[3];
    private static readonly bool[] _weitereVersucht = new bool[3];
    private static readonly object _weitereSperre = new();

    private static TextErkennung? WeitereErkennung(int i)
    {
        lock (_weitereSperre)
        {
            if (_weitereVersucht[i]) return _weitere[i];
            _weitereVersucht[i] = true;
            _weitere[i] = TextErkennung.Erstelle(out _);
            return _weitere[i];
        }
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
                                                         Action<System.Drawing.Bitmap, System.Drawing.Bitmap?>? bilder,
                                                         bool lesebild)
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
                // Pruefung 09.10.2026 (Befund 8): wirft das Kopf-Abbild, gibt Abbilder() das Technik-Bild wieder frei —
                // vorher war es dann erzeugt, aber noch in keinem using (Bitmap-Leck je Fehlversuch)
                (technikBild, kopfBild) = AutoPointerFenster.Abbilder(() => AutoPointerFenster.Abbild(ansicht.TechnikTabelle),
                                                                      () => AutoPointerFenster.Abbild(ansicht.KopfTabelle));
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
                var sicht = await LiesBilderAsync(ocr, technik, kopf, dpi, bilderSpeichern, WeitereErkennung(1), WeitereErkennung(2),
                                                  lesebild: lesebild);
                bilder?.Invoke(technik, kopf);
                return sicht with { Weg = "Bildschirm" };
            }
        }
        return null;
    }

    /// <summary>Zwei Durchlaeufe: Zoom x3 (bei 96 dpi), fehlende Felder aus einem
    /// zweiten Durchlauf mit x2 ergaenzt.
    /// Pruefung 05.10.2026 (Paket 3, F1): Technik-Tabelle, Kopf-Tabelle und der zweite Durchgang liefen nacheinander
    /// (3 x Texterkennung ≈ 0,3-0,5 s). Mit <paramref name="ocrKopf"/> und <paramref name="ocrZweiter"/> (eigene
    /// Engines) laufen alle drei GLEICHZEITIG; der zweite Durchgang wird immer schon gestartet (er wird fast immer
    /// gebraucht: Hash-ID, fehlende Felder) und nur dann verwendet, wenn die bisherige Regel es verlangt. Die Regel
    /// "Hash-ID nur, wenn beide Durchgaenge gleich lesen" bleibt. Ohne eigene Engines (null, Tests) wie bisher
    /// nacheinander auf <paramref name="ocr"/>.</summary>
    /// <param name="zweiterBlick">Befund Ahmad 09.10.2026 (1.5.12): danach die Werte von Marke/Modell, Kraftstoff und
    /// Inserat-ID einzeln noch einmal lesen (<see cref="ZweiterBlick"/>) — false nur fuer Tests/Messungen.</param>
    /// <param name="lesebild">1.5.13 (Wunsch Ahmad 09.10.2026): genau diese Bilder (Kopf ueber Technik) als PNG in
    /// <see cref="Lesung.Bild"/> mitgeben — fuer das Bild an AutoSchnell, wenn das Auto nicht erkannt wird. Kein neues Abbild.</param>
    internal static async Task<Lesung> LiesBilderAsync(TextErkennung ocr, System.Drawing.Bitmap technik,
                                                       System.Drawing.Bitmap? kopf, uint dpi, bool bilderSpeichern,
                                                       TextErkennung? ocrKopf = null, TextErkennung? ocrZweiter = null,
                                                       bool zweiterBlick = true, bool lesebild = false)
    {
        // 1.5.13: das Bild fuer AutoSchnell JETZT zusammensetzen — gleich gehoeren die Abbilder der Texterkennung (GDI+ erlaubt
        // kein gleichzeitiges Lesen desselben Bildes) — und nebenher als PNG kodieren; gebraucht wird es nur, wenn das Auto
        // nicht erkannt wird, kostet das Lesen so aber keine Zeit
        Task<byte[]?>? png = lesebild ? Lesebilder.PngImHintergrund(technik, kopf) : null;
        double faktor = Math.Clamp(3.0 * 96 / (dpi == 0 ? 96 : dpi), 1.5, 3.0);
        var leer = new List<OcrZeile>();
        List<OcrZeile> zt, zk;
        Task<List<OcrZeile>>? zweiter = null;
        if (ocrKopf != null && ocrZweiter != null)
        {
            // parallel: jede Engine ein eigenes Bild (GDI+-Bitmaps duerfen nicht gleichzeitig gelesen werden -> Kopie
            // fuer den zweiten Durchgang) und ein eigener Thread (auch das Hochskalieren vor der Erkennung laeuft so
            // nebeneinander). LiesAsync liest das Bild synchron ein, bevor es wartet — die Kopie darf danach weg.
            using var technikKopie = new System.Drawing.Bitmap(technik);
            var t2 = kopf != null ? Task.Run(() => ocrKopf.LiesAsync(kopf, faktor)) : Task.FromResult(leer);
            zweiter = Task.Run(() => ocrZweiter.LiesAsync(technikKopie, faktor * 2 / 3));
            zt = await ocr.LiesAsync(technik, faktor);
            zk = await t2;
            try { await zweiter; }
            catch (Exception ex) { Protokoll.Schreibe("2. Durchlauf fehlgeschlagen: " + ex.Message); zweiter = null; }
        }
        else
        {
            zt = await ocr.LiesAsync(technik, faktor);
            zk = kopf != null ? await ocr.LiesAsync(kopf, faktor) : leer;
        }
        var f = DetailLeser.Auswerten(zt, zk, kopf?.Width ?? 0);
        string roh = Rohtext(zt, zk);
        List<OcrZeile>? zt2 = null;
        if (DetailLeser.Fehlend(f).Count > 0 || DetailLeser.Unvollstaendig(f).Count > 0 || f.HashId != null)
        {
            zt2 = zweiter != null ? zweiter.Result : await ocr.LiesAsync(technik, faktor * 2 / 3);
            var f2 = DetailLeser.Auswerten(zt2, zk, kopf?.Width ?? 0);
            // Hash-ID (fuer den AutoScout-Link) nur, wenn beide Durchlaeufe genau dasselbe lesen
            string? hash = f.HashId != null && f.HashId == f2.HashId ? f.HashId : null;
            f = DetailLeser.Ergaenzen(f, f2);
            f.HashId = hash;
            roh += "  ||  2. Durchlauf: " + Rohtext(zt2, Array.Empty<OcrZeile>());
        }
        // 1.5.12 (Befund Ahmad 09.10.2026): zweiter Blick auf Marke/Modell, Kraftstoff, Inserat-ID — aus demselben Abbild,
        // hoechstens 10 s, Fehler nur ins Protokoll. Fehlt eine Pflichtangabe, geht ohnehin nichts an den Server: dann nicht.
        if (zweiterBlick && DetailLeser.Fehlend(f).Count == 0)
        {
            // Lage der Zeilen aus dem ersten Durchgang; was er nicht fand, aus dem zweiten (falls schon gelesen)
            string? blick = await ZweiterBlick.AnwendenAsync(f, technik, zt, zt2 ?? zweiter?.Result, dpi, ocr, ocrZweiter);
            if (blick != null) roh += "  ||  2. Blick: " + blick;
        }
        if (bilderSpeichern) Speichere(technik, kopf, roh);
        // 1.5.13: das PNG ist laengst fertig (lief neben der Texterkennung); Fehler hat PngImHintergrund schon protokolliert
        byte[]? bild = png != null ? await png : null;
        return new Lesung(f, zt.Count == 0, roh, Bild: bild);
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

    public void Oeffne(IReadOnlyList<Vergleich> vergleiche, Einstellungen e, IntPtr autoPointer, BrowserWahl browser)
    {
        BrowserOeffner.Oeffne(vergleiche.Select(v => v.Url).ToList(), browser);
        if (e.ZurueckZuAutoPointer && autoPointer != IntPtr.Zero && _ui != null)
            Task.Delay(900).ContinueWith(_ => _ui.Post(__ =>
            {
                // Pruefung 09.10.2026 (Befund 2): liegt AutoPointer schon vorne, tut ZurueckZu nichts; minimiert oder
                // haengend ebenso — dann nur eine (gedrosselte) Protokollzeile, kein Hinweis an den Sucher
                try
                {
                    if (!BrowserOeffner.ZurueckZu(autoPointer))
                        Protokoll.SchreibeGedrosselt("zurueck", "Zurück zu AutoPointer: nicht nach vorne geholt (minimiert, beschäftigt oder von Windows verweigert).",
                                                     TimeSpan.FromMinutes(5));
                }
                catch (Exception ex) { Protokoll.SchreibeGedrosselt("zurueck:fehler", "Zurück zu AutoPointer: " + ex.Message, TimeSpan.FromMinutes(5)); }
            }, null));
    }
}

/// <summary>1.5.11 (Wunsch Ahmad 08.10.2026 abends): "Vertrag" mit Browser-Erweiterung — kein Apify. Liegt das Inserat noch
/// nicht gelesen vor, oeffnet das Programm das Inserat (die Erweiterung liest es) und wartet, bis die Lesung beim Server ist;
/// erst dann geht der Kaufvertrag auf (/mobile/compare nimmt die Lesung, ohne abzurufen). Kommt sie nicht, nur ein
/// Hinweis — nie ein Abruf. (rein, fuer Tests)</summary>
internal static class VertragsWeg
{
    internal const int WarteMs = 25_000;
    internal const int TaktMs = 1500;
    internal const string WirdGelesen = "Inserat wird geöffnet und gelesen – der Kaufvertrag öffnet sich gleich.";
    internal const string NichtGelesen = "Das Inserat ist noch nicht gelesen – im Inserat unten rechts auf „Kaufvertrag“ drücken.";

    /// <returns>true = gelesen, Kaufvertrag oeffnen; false = (noch) nicht — der Hinweis kam schon.</returns>
    internal static async Task<bool> InseratBereitAsync(string url, Func<string, Task<bool?>> gelesen, Action oeffneInserat,
                                                         Action<string, bool> melde, Func<TimeSpan, Task> warte,
                                                         Func<long> takt)
    {
        if (await gelesen(url) == true) return true;
        oeffneInserat();
        melde(WirdGelesen, false);
        long bis = takt() + WarteMs;
        while (takt() < bis)
        {
            await warte(TimeSpan.FromMilliseconds(TaktMs));
            if (await gelesen(url) == true) return true;
        }
        Protokoll.Schreibe("Kaufvertrag: Inserat nach " + WarteMs / 1000 + " s noch nicht gelesen – kein Abruf, Hinweis gezeigt.");
        melde(NichtGelesen, true);
        return false;
    }
}
