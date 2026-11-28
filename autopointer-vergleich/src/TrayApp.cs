using System.Diagnostics;
using System.Net.NetworkInformation;
using Microsoft.Win32;

namespace AutoPointerVergleich;

/// <summary>Steuerfenster mit Knoepfen (seit 03.10.2026, Wunsch Ahmad) plus Symbol im Infobereich.</summary>
internal sealed class TrayApp : ApplicationContext
{
    private const int HotkeyId = 0x4150;
    /// <summary>Ein zweiter Programmstart setzt dieses Signal — die laufende Instanz zeigt ihr Fenster.</summary>
    internal const string ZeigenSignalName = @"Local\AutoSchnell.AutoPointerVergleich.Zeigen";
    /// <summary>Pruefung 08.10.2026 (1.5.9, B): eine neuere Version setzt dieses Signal — die laufende beendet sich sauber
    /// (ohne Rueckfrage), damit die neue starten und die feste Kopie ersetzen kann.</summary>
    internal const string BeendenSignalName = @"Local\AutoSchnell.AutoPointerVergleich.Beenden";
    /// <summary>Der Name des Programms in Fenstertiteln, Rueckfragen und Sprechblasen (1.5.9, E — nicht mehr
    /// "AutoPointer-Vergleich"; AutoPointer ist das fremde Programm, aus dem gelesen wird).</summary>
    internal const string Name = "AutoSchnell Vergleich";

    private readonly NotifyIcon _symbol;
    private readonly Icon _iconAktiv = Symbole.Icon(Symbole.Aktiv);
    private readonly Icon _iconPause = Symbole.Icon(Symbole.Pause);
    private readonly Icon _iconWarten = Symbole.Icon(Symbole.Warten);
    private readonly Icon _iconFehler = Symbole.Icon(Symbole.Fehler);
    private readonly string? _serverUeberschrieben;
    private AutoSchnellDienst _dienst = null!;
    private VerbindenForm? _verbindenForm;
    private readonly SynchronizationContext _ui;
    private readonly CancellationTokenSource _ende = new();
    private readonly HotkeyFenster _hotkey;
    private readonly bool _probelauf;
    private Einstellungen _einstellungen;
    private Ueberwacher? _ueberwacher;
    private EinstellungenForm? _einstellungenForm;
    private long _letzteSprechblase = long.MinValue / 2;      // A10: monoton
    /// <summary>Paket 2 (A9): Texterkennung fehlte beim Start (Fehlertext) — wird jede Minute erneut versucht.</summary>
    private string? _ocrFehlt;
    /// <summary>Paket 2 (A8): /status meldete 402/403 — Text des Servers; null = Lizenz in Ordnung.</summary>
    private string? _lizenzSperre;
    /// <summary>Paket 2 (A8): voruebergehende Fehler bei /status hintereinander (1 → 1 min, 2 → 2 min, dann 5 min).</summary>
    private int _lizenzFehler;
    /// <summary>Paket 2 (A8): wann die naechste Lizenzpruefung faellig ist (TickCount64, ms).</summary>
    private long _naechsteLizenz;
    private bool _lizenzLaeuft;
    private readonly SteuerFenster _fenster;
    private readonly Leiste _leiste;
    private IntPtr _leistenHandle;          // fuer den Lese-Thread (kein Zugriff auf das Steuerelement dort)
    private AutoPointerQuelle? _quelle;
    /// <summary>Wunsch Ahmad 06.10.2026: letzter Mausklick in AutoPointer — nur danach wird verglichen.</summary>
    private Klicks? _klicks;
    private readonly EventWaitHandle _zeigenSignal;
    private readonly EventWaitHandle _beendenSignal;
    /// <summary>1.5.9 (B): die eigene Version fuer einen zweiten Start (geteilter Speicher; null = nicht moeglich).</summary>
    private readonly IDisposable? _versionVeroeffentlicht;
    private string? _letzteMeldung;
    /// <summary>Pruefung 08.10.2026 (1.5.9, C): die letzten Hinweise (mit Uhrzeit, ganz) fuer "Status und Hilfe" —
    /// Sprechblasen verschwinden und sind kurz, hier steht die ganze Erklaerung.</summary>
    private readonly LinkedList<string> _hinweise = new();
    internal const int HinweiseImFenster = 4;
    /// <summary>Stand der Zwischenablage, als das letzte Auto dran war (Nr. 11).</summary>
    private uint _zwischenablageStand;
    private bool _updateGemeldet;
    private bool _systemcheckLaeuft;
    private bool _beendet;

    public TrayApp(bool probelauf, string? server = null, bool minimiert = false)
    {
        _probelauf = probelauf;
        _serverUeberschrieben = server;
        _ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        _einstellungen = Einstellungen.Laden();
        if (!string.IsNullOrWhiteSpace(server))
        {
            // Nr. 14: auch die Adresse von der Befehlszeile nur https://…auto-schnellkauf.de oder der eigene Rechner.
            // Paket 2 (A12): gilt nur im Speicher — beim Speichern bleibt der gespeicherte Server stehen.
            if (Einstellungen.SichererServer(server) is { } sicher) _einstellungen.ServerUeberschreiben(sicher);
            else Protokoll.Schreibe($"--server {server} ignoriert – nur https://…auto-schnellkauf.de oder der eigene Rechner.");
        }
        DienstErstellen();

        // 1.5.8 (Wunsch Ahmad 08.10.2026): bedient wird ueber die Leiste — am Symbol nur noch Zeigen und Beenden
        var menue = new ContextMenuStrip();
        var oeffnen = new ToolStripMenuItem("Leiste und Status zeigen", null, (_, _) => FensterZeigen());
        oeffnen.Font = new Font(oeffnen.Font, FontStyle.Bold);
        menue.Items.Add(oeffnen);
        menue.Items.Add(new ToolStripSeparator());
        menue.Items.Add("Beenden", null, (_, _) => BeendenFragen());

        _symbol = new NotifyIcon
        {
            Icon = _iconWarten,
            Text = Name,
            ContextMenuStrip = menue,
            Visible = true,
        };
        _symbol.DoubleClick += (_, _) => FensterZeigen();

        _hotkey = new HotkeyFenster(id => { if (id == DiktatHotkeyId) DiktatUmschalten(); else AutomatikUmschalten(); });
        HotkeyAnwenden();
        // 24.11.2026: Strg+Alt+D = Diktat (immer, unabhaengig vom Kuerzel fuer die Automatik)
        if (!_hotkey.Anmelden(DiktatHotkeyId, Native.MOD_CONTROL | Native.MOD_ALT, (uint)Keys.D))
            Protokoll.Schreibe("Strg+Alt+D ist schon von einem anderen Programm belegt – Diktat nur über den Knopf in der Leiste.");
        AutomatikAnzeigen();

        _fenster = new SteuerFenster(ZustandFuersFenster);
        _fenster.Verbinden += () => VerbindenZeigen(null);
        _fenster.Trennen += async () => await TrennenAsync();
        _fenster.EinstellungenOeffnen += EinstellungenZeigen;
        _fenster.SystemcheckOeffnen += async () => await SystemcheckZeigenAsync();
        _fenster.Beenden += BeendenFragen;
        _leiste = new Leiste(ZustandFuersFenster, () => _quelle?.Hauptfenster ?? IntPtr.Zero);
        _leiste.Aktivieren += () => AutomatikSetzen(true);
        _leiste.Stoppen += () => AutomatikSetzen(false);
        _leiste.JetztVergleichen += VergleichenStarten;
        _leiste.VertragOeffnen += async () => await VertragOeffnenAsync();
        _leiste.Diktieren += DiktatUmschalten;
        _leiste.DauerlaufUmschalten += DauerlaufUmschalten;
        _leiste.DiktatZuruecknehmen += DiktatZuruecknehmen;
        _leiste.FensterOeffnen += FensterZeigen;
        _leiste.LetztenOeffnen += () => _ueberwacher?.LetztenErneutOeffnen();
        _leiste.EinstellungenOeffnen += EinstellungenZeigen;
        _leiste.SystemcheckOeffnen += async () => await SystemcheckZeigenAsync();
        _leiste.Verbinden += () => VerbindenZeigen(null);
        _leiste.Trennen += async () => await TrennenAsync();
        _leiste.EckeGewechselt += ecke => { _einstellungen.LeisteEcke = ecke; Speichern(_einstellungen); };
        _leiste.PositionGeaendert += stelle =>
        {
            _einstellungen.LeisteX = stelle?.X;
            _einstellungen.LeisteY = stelle?.Y;
            Speichern(_einstellungen);
        };
        _leiste.Beenden += BeendenFragen;
        _leiste.UpdateOeffnen += UpdateOeffnen;
        AutoPointerFenster.EigeneFenster = () => new[] { _leistenHandle };
        // Nr. 10: liegt die Leiste ueber der AutoPointer-Tabelle, wird sie fuer das Bildschirm-Abbild kurz unsichtbar
        AutoPointerFenster.EigeneAusblenden = unsichtbar => _ui.Send(_ =>
        {
            if (!_leiste.IsDisposed && _leiste.Visible) _leiste.Opacity = unsichtbar ? 0 : 1;
        }, null);
        LeisteAnwenden();
        // 1.5.8: es startet nur die Leiste; "Status und Hilfe" ueber "Mehr" bzw. das Symbol im Infobereich
        _ = minimiert;

        Protokoll.KlartextUmstellen();     // alte Klartext-Protokolle verschluesseln (03.10.2026)
        Protokoll.AufraeumenBeiTageswechsel();
        _ = DiktatAufwaermenAsync();
        Protokoll.Schreibe($"{Name} {Application.ProductVersion} gestartet{(probelauf ? " – PROBELAUF (öffnet keinen Browser)" : "")}."
                           + $" Server: {_einstellungen.Server}" + (_einstellungen.ServerNurImSpeicher ? " (nur für diesen Lauf, --server)" : ""));
        VerbindungAnzeigen();
        Starten();
        // Lizenz beim Start pruefen bzw. zum Verbinden auffordern (erst, wenn die Nachrichtenschleife laeuft)
        _naechsteLizenz = Environment.TickCount64 + LizenzTakt(TimeSpan.FromMinutes(15));
        _ui.Post(async _ => await LizenzPruefenAsync(beimStart: true), null);
        var token = _ende.Token;
        _zeigenSignal = new EventWaitHandle(false, EventResetMode.AutoReset, ZeigenSignalName);
        // Pruefung 08.10.2026 (1.5.9, B): ein zweiter Start sieht hier, welche Version laeuft, und kann sie per Signal
        // beenden (vorher holte er nur das Fenster nach vorne — eine neue Version startete nie, die feste Kopie blieb alt)
        _beendenSignal = new EventWaitHandle(false, EventResetMode.AutoReset, BeendenSignalName);
        _versionVeroeffentlicht = LaufendesProgramm.Veroeffentlichen(Application.ProductVersion.Split('+')[0], Environment.ProcessId);
        Task.Run(() =>
        {
            var warten = new[] { _zeigenSignal, _beendenSignal, token.WaitHandle };
            while (true)
            {
                int i = WaitHandle.WaitAny(warten);
                if (i == 0) _ui.Post(_ => FensterZeigen(), null);
                else if (i == 1)
                {
                    _ui.Post(_ =>
                    {
                        Protokoll.Schreibe("Eine neuere Version wird gestartet – dieses Programm beendet sich.");
                        Beenden();
                    }, null);
                    break;
                }
                else break;
            }
        }, token);
        // Paket 2 (A8/A13): alle 15 s nachsehen, ob die Lizenzpruefung faellig ist (15 min, nach Fehlern 1/2/5 min)
        // und ob der Tag gewechselt hat (Protokoll aufraeumen)
        Task.Run(async () =>
        {
            while (!token.IsCancellationRequested)
            {
                try { await Task.Delay(TimeSpan.FromSeconds(15), token); }
                catch (OperationCanceledException) { break; }
                if (Environment.TickCount64 >= Interlocked.Read(ref _naechsteLizenz))
                {
                    Interlocked.Exchange(ref _naechsteLizenz, Environment.TickCount64 + LizenzTakt(TimeSpan.FromMinutes(15)));
                    _ui.Post(async _ => await LizenzPruefenAsync(beimStart: false), null);
                }
                try { Protokoll.AufraeumenBeiTageswechsel(); }
                catch (Exception ex) { Protokoll.SchreibeGedrosselt("aufraeumen", "Protokoll nicht aufgeräumt: " + ex.Message, TimeSpan.FromHours(1)); }
            }
        }, token);
        // Paket 2 (A8): nach dem Aufwachen aus dem Standby und bei Netzwechsel die Lizenz sofort neu pruefen
        // (nach kurzer Pause: direkt nach dem Aufwachen ist das Netz oft noch nicht da)
        SystemEvents.PowerModeChanged += PowerModeGeaendert;
        NetworkChange.NetworkAvailabilityChanged += NetzGeaendert;
    }

    private static long LizenzTakt(TimeSpan t) => (long)t.TotalMilliseconds;

    private void PowerModeGeaendert(object? sender, PowerModeChangedEventArgs e)
    {
        if (e.Mode == PowerModes.Resume) LizenzBaldPruefen(TimeSpan.FromSeconds(8), "Aufwachen aus dem Standby");
    }

    private void NetzGeaendert(object? sender, NetworkAvailabilityEventArgs e)
    {
        if (e.IsAvailable) LizenzBaldPruefen(TimeSpan.FromSeconds(4), "Netzwerk wieder da");
    }

    /// <summary>Lizenzpruefung in Kuerze anstossen (ausserhalb des 15-min-Takts). Laeuft im Hintergrund, meldet sich
    /// ueber den Oberflaechen-Thread.</summary>
    private void LizenzBaldPruefen(TimeSpan verzoegerung, string anlass)
    {
        if (_ende.IsCancellationRequested) return;
        Protokoll.Schreibe($"{anlass} – Lizenz wird in {verzoegerung.TotalSeconds:0} s neu geprüft.");
        Task.Delay(verzoegerung, _ende.Token).ContinueWith(t =>
        {
            if (t.IsCanceled) return;
            Interlocked.Exchange(ref _naechsteLizenz, Environment.TickCount64 + LizenzTakt(TimeSpan.FromMinutes(15)));
            _ui.Post(async _ => await LizenzPruefenAsync(beimStart: false), null);
        }, TaskScheduler.Default);
    }

    /// <summary>Zweiter Programmstart: laufende Instanz zeigt ihr Fenster statt "laeuft bereits".</summary>
    internal static bool ZeigenAnfordern()
    {
        try
        {
            using var signal = EventWaitHandle.OpenExisting(ZeigenSignalName);
            Native.AllowSetForegroundWindow(Native.ASFW_ANY);
            return signal.Set();
        }
        catch (WaitHandleCannotBeOpenedException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    private void FensterZeigen()
    {
        LeisteAnwenden();                                   // falls die Leiste verdeckt/verschoben war: wieder da
        if (!_fenster.IsDisposed) _fenster.Zeigen();
    }

    /// <summary>Wunsch Ahmad 03.10.2026: kleine Leiste immer im Vordergrund, unten links oder rechts.
    /// Seit 1.5.8 immer an (sie ist die eine Bedienung).</summary>
    private void LeisteAnwenden()
    {
        _fenster.NurAusblenden(true);
        if (_leiste.IsDisposed) return;
        _leiste.EckeSetzen(_einstellungen.LeisteEcke);
        _leiste.PositionSetzen(_einstellungen.LeisteX is int x && _einstellungen.LeisteY is int y ? new Point(x, y) : null);
        if (!_leiste.Visible) _leiste.Show();
        _leistenHandle = _leiste.Handle;
    }

    private FensterZustand ZustandFuersFenster()
    {
        var f = _ueberwacher?.LetztesFahrzeug;
        string? auto = f == null ? null
            : $"{(f.Marke != null ? $"{f.Marke} {f.Modell}".Trim() : f.MarkeModellText)} · EZ {f.EzText}"
              + (f.Kilometer != null ? $" · {f.Kilometer:N0} km" : "");
        return new FensterZustand(AktuellerStatus(), _einstellungen.AutomatikAktiv,
            _dienst.Verbunden, _einstellungen.VerbundenAls ?? "", auto,
            !string.IsNullOrEmpty(_ueberwacher?.LetzteInseratUrl), _letzteMeldung, _probelauf, _lizenzSperre,
            _hinweise.ToList(), _ueberwacher?.LetztesNurGemerkt == true, _vertragLaeuft,
            _dauerlaufAn || _einstellungen.DauerlaufEingeschaltet
                ? DauerlaufStand(_dlNeu, _dlAussortiert, _dlGelesen, _dlTreffer, _dauerlaufAn ? _dlHindernis : "") : null,
            DauerlaufWartet: !_dauerlaufAn && _einstellungen.DauerlaufEingeschaltet);
    }

    /// <summary>Status fuer Symbol, Fenster und Leiste: Lizenzsperre (A8) und fehlende Texterkennung (A9) gehen vor.</summary>
    private Status AktuellerStatus()
    {
        if (_lizenzSperre != null && _dienst.Verbunden) return Status.Gesperrt;
        if (_ueberwacher == null && _ocrFehlt != null) return Status.TexterkennungFehlt;
        return _ueberwacher?.Status ?? Status.KeinAutoPointer;
    }

    /// <summary>Pruefung 05.10.2026 (Paket 3, F5): "Vergleichen" (Leiste, Fenster, Menue) rechnete im Oberflaechen-Thread
    /// — Bildschirm-Abbild, Hochskalieren und Auswertung froren Fenster und Leiste fuer 0,3-0,5 s ein. Jetzt im
    /// Hintergrund; Fehler kommen als Sprechblase.</summary>
    private void VergleichenStarten()
    {
        var u = _ueberwacher;
        if (u == null)
        {
            Sprechblase(_ocrFehlt != null ? "Texterkennung fehlt – es kann nichts gelesen werden." : "Noch nicht bereit.", true, erzwingen: true);
            return;
        }
        // Pruefung 08.10.2026 (1.5.9, I): der Knopf las den Bildschirm auch, wenn der Browser AutoPointer verdeckte (die
        // Automatik prueft das, der Knopf nicht) — dann wurde der Browser "gelesen". Jetzt AutoPointer erst nach vorne
        // holen (hier im Oberflaechen-Thread: AttachThreadInput braucht seine Eingabe-Warteschlange), kurz warten, lesen.
        // Pruefung 09.10.2026 (Befund 3): ein minimiertes AutoPointer wird nicht angefasst (Hinweis, nicht lesen); und nach
        // dem Warten wird GEPRUEFT, ob AutoPointer wirklich vorne liegt — scheitert SetForegroundWindow (Windows verweigert,
        // Modal-Dialog), kam vorher der Browser-Inhalt ins Abbild ("nicht erkannt", Lesebild mit fremdem Inhalt).
        IntPtr haupt = _quelle?.Hauptfenster ?? IntPtr.Zero;
        bool geholt = false;
        if (haupt != IntPtr.Zero)
        {
            if (Native.IsIconic(haupt))
            {
                Protokoll.Schreibe("„Vergleichen“: AutoPointer ist minimiert – nicht gelesen.");
                Sprechblase(Ueberwacher.AutoPointerMinimiert, true, erzwingen: true);
                return;
            }
            if (!AutoPointerFenster.ImVordergrund(haupt))
            {
                geholt = true;                                   // versucht — danach kurz warten und nachsehen
                try
                {
                    if (!BrowserOeffner.ZurueckZu(haupt)) Protokoll.Schreibe("„Vergleichen“: AutoPointer ließ sich nicht nach vorne holen.");
                }
                catch (Exception ex) { Protokoll.Schreibe("AutoPointer nicht nach vorne geholt: " + ex.Message); }
            }
        }
        _ = Task.Run(async () =>
        {
            try
            {
                if (geholt) await Task.Delay(VorneWarteMs);
                if (haupt != IntPtr.Zero && !AutoPointerFenster.ImVordergrund(haupt))
                {
                    Protokoll.Schreibe("„Vergleichen“: AutoPointer liegt nicht vorne – nicht gelesen.");
                    _ui.Post(_ => Sprechblase(Ueberwacher.AutoPointerNichtVorne, true, erzwingen: true), null);
                    return;
                }
                await u.JetztVergleichenAsync();
            }
            catch (Exception ex)
            {
                // 1.5.9 (E): die .NET-Meldung (oft englisch) nur ins Protokoll
                Protokoll.Schreibe("„Vergleichen“ fehlgeschlagen: " + ex);
                _ui.Post(_ => Sprechblase(ex is TimeoutException
                    ? "Die Texterkennung hängt gerade – bitte gleich noch einmal „Vergleichen“ drücken."
                    : "Vergleichen hat nicht geklappt – bitte noch einmal „Vergleichen“ drücken.", true, erzwingen: true), null);
            }
        });
    }

    /// <summary>1.5.9 (I): so lange nach dem Nach-vorne-Holen warten, bis AutoPointer neu gezeichnet ist.</summary>
    internal const int VorneWarteMs = 300;

    /// <summary>Wunsch Ahmad 03.10.2026: das zuletzt angeklickte Auto in AutoSchnell oeffnen — der Server hat
    /// es schon ausgelesen, der Vergleich steht sofort mit Fotos da, "Kaufvertrag erstellen" ohne Link-Einfuegen.
    /// Paket 3 (F3): die Suche nach der App-Verknuepfung laeuft im Hintergrund (vorher fror die Oberflaeche bei jedem
    /// Klick, solange alle Verknuepfungen per COM gelesen wurden).</summary>
    private bool _vertragLaeuft;
    /// <summary>Pruefung 09.10.2026 (Vertragsweg, P1): ein zweiter Klick, waehrend der erste noch laeuft (Lesung abwarten,
    /// App suchen, App-Start pruefen), endete stumm — der Sucher drueckte weiter und glaubte, es haenge.</summary>
    internal const string VertragLaeuftSchon = "Kaufvertrag wird gerade geöffnet – bitte kurz warten.";

    private async Task VertragOeffnenAsync()
    {
        if (_vertragLaeuft)
        {
            Sprechblase(VertragLaeuftSchon, false, erzwingen: true, merken: false);
            return;
        }
        _vertragLaeuft = true;
        if (!_leiste.IsDisposed) _leiste.Aktualisieren();          // Knopf sofort "Vertrag …", nicht erst im Sekundentakt
        try { await VertragOeffnenInternAsync(); }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Kaufvertrag nicht geöffnet: " + ex);
            Sprechblase("Der Vertrag ließ sich nicht öffnen – bitte noch einmal „Vertrag“ drücken.", true, erzwingen: true);
        }
        finally
        {
            _vertragLaeuft = false;
            if (!_leiste.IsDisposed) _leiste.Aktualisieren();
        }
    }

    private async Task VertragOeffnenInternAsync()
    {
        string? url = _ueberwacher?.LetzteInseratUrl;
        var fahrzeug = _ueberwacher?.LetztesFahrzeug;
        if (string.IsNullOrEmpty(url) && fahrzeug != null)
        {
            // Nr. 11: AutoScout zeigt die Kennung abgeschnitten — hat der Sucher die Adresse seit dem Anklicken
            // kopiert ("Seite öffnen", Strg+L, Strg+C), nimmt das Programm sie.
            url = InseratAusZwischenablage(fahrzeug);
            if (url != null) Protokoll.Schreibe("Kaufvertrag: Inserat-Adresse aus der Zwischenablage übernommen: " + url);
        }
        if (string.IsNullOrEmpty(url))
        {
            if (fahrzeug == null)
                Sprechblase("Noch kein Auto verglichen – erst in AutoPointer ein Inserat anklicken.", true, erzwingen: true);
            // Pruefung 08.10.2026 (1.5.9, D): das Auto vom Programmstart wurde nur gemerkt (kein Server-Aufruf, also auch
            // keine Inserat-Adresse) — nicht faelschlich "Inserat-ID nicht zu sehen" sagen
            else if (_ueberwacher?.LetztesNurGemerkt == true)
                Sprechblase(Ueberwacher.NochNichtVerglichen, true, erzwingen: true);
            // 1.5.9 (C): kurz in die Sprechblase, die ganze Anleitung ins Fenster
            else Sprechblase(Ueberwacher.VertragOhneAdresse, true, erzwingen: true, ausfuehrlich: Ueberwacher.LinkHinweisFuer(fahrzeug));
            return;
        }
        // 1.5.11 (Wunsch Ahmad 08.10.2026 abends, "Vertrag: Inserat oeffnen und lesen, kein Apify"): hat das Konto die
        // Erweiterung und liegt das Inserat noch nicht gelesen vor, erst das Inserat in IHREM Browser oeffnen und warten,
        // bis sie es gelesen hat — sonst haette die App es ueber Apify geholt.
        // Pruefung 09.10.2026 (Vertragsweg): der Weg endet nie stumm — ohne Lesung geht der Kaufvertrag trotzdem auf, die
        // App liest dann nur auf Knopfdruck ("&lesung=fehlt", Ahmads Regel: Apify nur, wenn jemand bewusst einfuegt/klickt).
        bool lesungFehlt = false;
        if (_ueberwacher?.HatHelfer == true)
        {
            // 3a: das Inserat zum Lesen in den Browser der ERWEITERUNG — nur dort liest sie es
            var browser = Ueberwacher.BrowserFuerLesung(_einstellungen.Browser, _ueberwacher.HelferBrowser);
            string inserat = url;
            var stand = await VertragsWeg.InseratBereitAsync(url, _dienst.InseratGelesenAsync,
                () =>
                {
                    Protokoll.Schreibe("Kaufvertrag: Inserat zum Lesen geöffnet: " + inserat);
                    try { BrowserOeffner.Oeffne(new[] { inserat }, browser); }
                    catch (Exception ex) { Protokoll.Schreibe("Inserat nicht geöffnet: " + ex); }
                },
                (text, fehler) => Sprechblase(text, fehler, erzwingen: true),
                t => Task.Delay(t), () => Environment.TickCount64);
            if (stand == LesungsStand.Gesperrt) return;            // kein Vertrag ohne Abo — der Servertext kam schon
            lesungFehlt = stand == LesungsStand.NichtGelesen;
        }
        // Nr. 12: Kennung des Starts — die App meldet sie beim Uebernehmen an AutoSchnell zurueck
        string start = Guid.NewGuid().ToString("N");
        string ziel = $"{_einstellungen.Server}/app/vergleich?url={Uri.EscapeDataString(url)}&start={start}"
                      + (lesungFehlt ? "&lesung=fehlt" : "");
        Protokoll.Schreibe("Kaufvertrag: öffne " + ziel);
        // Wunsch Ahmad 03.10.2026: zuerst die installierte AutoSchnell-App (offenes Fenster oder neu starten),
        // nur ohne App im Browser. Pruefung 09.10.2026 (Vertragsweg, 4a): die App des Browsers, in dem die Erweiterung
        // verbunden ist — dort ist der Sucher angemeldet.
        var app = await AutoSchnellApp.OeffnenAsync(ziel, _einstellungen.Server, _ueberwacher?.HelferBrowser);
        if (app != null)
        {
            // P1: der Knopf bleibt "Vertrag …", bis die App sich gemeldet hat (oder der Browser aufging) — vorher lief die
            // Pruefung nebenher und ein zweiter Klick startete die App ein zweites Mal
            await AppStartPruefenAsync(start, ziel, app);
            return;
        }
        ImBrowserOeffnen(ziel);
    }

    /// <summary>Pruefung 09.10.2026 (Vertragsweg, P6): die Webseite im Browser der Erweiterung/App (bei "Standardbrowser") —
    /// vorher landete der Kaufvertrag z. B. in Firefox, waehrend die Anmeldung in Chrome lag.</summary>
    private void ImBrowserOeffnen(string ziel)
    {
        try { BrowserOeffner.Oeffne(new[] { ziel }, Ueberwacher.BrowserFuer(_einstellungen.Browser, _ueberwacher?.HelferBrowser)); }
        catch (Exception ex)
        {
            // 1.5.9 (E): keine englische .NET-Meldung in der Sprechblase
            Protokoll.Schreibe("Browser konnte nicht geöffnet werden: " + ex);
            Sprechblase("Der Browser ließ sich nicht öffnen – in den Einstellungen einen anderen Browser wählen.", true, erzwingen: true);
        }
    }

    /// <summary>Pruefung 08.10.2026 (1.5.9, B): "Mehr ▾" → "Update auf … verfügbar …" — die Seite "Programme" in AutoSchnell
    /// (dorthin verwies schon die einmalige Sprechblase), dort steht der Download.</summary>
    private void UpdateOeffnen()
    {
        Protokoll.Schreibe("Update: öffne die Seite „Programme“ in AutoSchnell.");
        ImBrowserOeffnen($"{_einstellungen.Server.TrimEnd('/')}/app/programme");
    }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 12): Kommt von der App keine Rueckmeldung (Fenster nicht nach vorne gekommen,
    /// falsche App), oeffnet das Programm den Kaufvertrag im Browser. Pruefung 09.10.2026 (Vertragsweg, P5): 20 s statt 10,
    /// und die App sagt, was sie tut (<see cref="AppStartWeg"/>) — "nachgefragt" (etwas ungespeichert), "anmeldung" und
    /// "abo" sind eine Meldung an den Sucher, KEIN Browser (vorher ging dann nach 10 s zusaetzlich die Webseite auf, und
    /// der Sucher hatte den Vertrag zweimal). Nur ohne jede Meldung: Browser der Erweiterung, Suche verwerfen (4c) und
    /// dieselbe App nach dem zweiten Fehlschlag hintereinander ueberspringen.</summary>
    private async Task AppStartPruefenAsync(string start, string ziel, AutoSchnellApp.Verknuepfung app)
    {
        if (!_dienst.Verbunden) return;
        string? zustand = await AppStartWeg.WartenAsync(() => _dienst.AppStartBestaetigtAsync(start),
                                                        t => Task.Delay(t), () => Environment.TickCount64);
        if (zustand != null)
        {
            AutoSchnellApp.StartGeglueckt();
            Protokoll.Schreibe("AutoSchnell-App hat sich gemeldet: " + zustand + ".");
            Sprechblase(AppStartWeg.Meldung(zustand), zustand == AppStartWeg.Abo, erzwingen: true, merken: zustand != AppStartWeg.Offen);
            return;
        }
        bool uebersprungen = AutoSchnellApp.StartFehlgeschlagen(app);
        Protokoll.Schreibe("AutoSchnell-App hat sich nicht gemeldet – Kaufvertrag im Browser geöffnet"
                           + (uebersprungen ? $" (App {app.AppId} in {Path.GetFileName(app.Programm)} wird ab jetzt übersprungen)." : "."));
        Sprechblase(AppStartWeg.NichtGemeldet, false, erzwingen: true);
        ImBrowserOeffnen(ziel);
    }

    /// <summary>Nr. 11: Inserat-Adresse aus der Zwischenablage — nur, wenn seit dem Anklicken dieses Autos etwas
    /// kopiert wurde, nur Inserat-Seiten (mobile.de, AutoScout24, Kleinanzeigen) und nur das Portal dieses Autos.</summary>
    private string? InseratAusZwischenablage(Fahrzeug f)
    {
        try
        {
            if (Native.GetClipboardSequenceNumber() == _zwischenablageStand) return null;
            return InseratAdresse(Clipboard.ContainsText() ? Clipboard.GetText() : null, f.Quelle);
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Zwischenablage nicht lesbar: " + ex.Message);
            return null;
        }
    }

    /// <summary>Erste Inserat-Adresse im Text, passend zum Portal (rein, fuer Tests).</summary>
    internal static string? InseratAdresse(string? text, string? quelle)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        string q = (quelle ?? "").ToLowerInvariant();
        string? portal = q.Contains("autoscout") ? "autoscout24.de" : q.Contains("mobile") ? "mobile.de"
            : q.Contains("kleinanzeigen") || q.Contains("ebay") ? "kleinanzeigen.de" : null;
        foreach (System.Text.RegularExpressions.Match m in
                 System.Text.RegularExpressions.Regex.Matches(text, @"https://[^\s""'<>]+"))
        {
            string url = m.Value.TrimEnd('.', ',', ';', ')');
            if (!AutoSchnellDienst.ErlaubteAdresse(url, portal != null ? new[] { portal } : AutoSchnellDienst.InseratSeiten))
                continue;
            string pfad = new Uri(url).AbsolutePath.ToLowerInvariant();
            bool inserat = pfad.Contains("/angebote/") || pfad.Contains("/s-anzeige/") || pfad.Contains("/auto-inserat/")
                           || (pfad.Contains("details.html") && url.Contains("id=", StringComparison.OrdinalIgnoreCase));
            if (inserat) return url;
        }
        return null;
    }

    private void DienstErstellen() =>
        _dienst = new AutoSchnellDienst(_einstellungen.Server, () => _einstellungen.Schluessel());

    private void VerbindungAnzeigen()
    {
        // 1.5.8: die Verbindung zeigen Leiste ("Mehr") und "Status und Hilfe" (jede Sekunde); am Symbol der Tooltip
        _symbol.Text = _dienst.Verbunden ? "AutoSchnell Vergleich – verbunden" : "AutoSchnell Vergleich – nicht verbunden";
        if (!_leiste.IsDisposed) _leiste.Aktualisieren();
    }

    /// <summary>Beim Start und alle 15 Minuten: gilt der Schluessel noch, ist das Abo aktiv?
    /// Pruefung 05.10.2026 (Paket 2, A8): voruebergehende Fehler (kein Netz, 5xx, 429) bleiben STILL — kein rotes
    /// Symbol, keine Sprechblase alle 15 Minuten, auch nicht beim Autostart ohne Netz; stattdessen nach 1/2/5 Minuten
    /// erneut. 402/403 werden zum Zustand "Gesperrt" (Leiste/Fenster zeigen den Server-Text, der Ueberwacher liest
    /// nichts). 401 loescht wie bisher den Schluessel (anderer PC verbunden) — sofern er noch der aktuelle ist.</summary>
    private async Task LizenzPruefenAsync(bool beimStart)
    {
        if (!_dienst.Verbunden)
        {
            // Befund 7 (09.10.2026): beim Start ohne Nutzerklick — der Dialog kommt, wird aber nicht aktiv erzwungen
            if (beimStart) VerbindenZeigen(null, aktivieren: false);
            return;
        }
        if (_lizenzLaeuft) return;
        _lizenzLaeuft = true;
        try
        {
            var s = await _dienst.StatusAsync();
            _lizenzFehler = 0;
            LizenzSperreSetzen(null);
            string als = AutoSchnellDienst.KontoText(s.Name, s.Konto, s.Firma);
            if (als != _einstellungen.VerbundenAls)
            {
                _einstellungen.VerbundenAls = als;
                Speichern(_einstellungen);
            }
            Protokoll.Schreibe($"Lizenz ok: {als} · PC {s.PcName}" + (s.AboBis != null ? $" · Abo bis {s.AboBis[..Math.Min(10, s.AboBis.Length)]}" : ""));
            // Pruefbericht 03.10.2026 (Nr. 4): AutoSchnell bietet eine neuere Version an -> einmal je Programmstart sagen.
            // Pruefung 08.10.2026 (1.5.9, B): die Sprechblase verschwand und war dann weg — jetzt steht dauerhaft ein
            // Eintrag "Update auf … verfügbar …" in "Mehr ▾" (oeffnet die Seite "Programme" mit dem Download).
            string eigene = Application.ProductVersion.Split('+')[0];
            bool neuer = AutoSchnellDienst.NeuereVersion(s.AktuelleVersion, eigene);
            if (!_leiste.IsDisposed) _leiste.UpdateSetzen(neuer ? s.AktuelleVersion : null);
            if (!_updateGemeldet && neuer)
            {
                _updateGemeldet = true;
                Protokoll.Schreibe($"Neue Version {s.AktuelleVersion} verfügbar (installiert: {eigene}).");
                Sprechblase(UpdateText(s.AktuelleVersion!), false, erzwingen: true,
                            ausfuehrlich: $"Neue Version {s.AktuelleVersion} verfügbar (installiert: {eigene}). Auf der Leiste "
                                          + $"„Mehr ▾“ → „Update auf {s.AktuelleVersion} verfügbar …“ öffnet in AutoSchnell die Seite "
                                          + $"„{s.ProgrammName ?? "Programme"}“: dort herunterladen und die Datei starten – sie ersetzt "
                                          + "das laufende Programm.");
            }
            VerbindungAnzeigen();
            StatusAnzeigen(AktuellerStatus());
        }
        catch (DienstFehler ex)
        {
            Protokoll.Schreibe("Lizenzprüfung: " + ex.Message);
            if (ex.NichtVerbunden) VerbindungVerloren(ex.Message);
            // 1.5.9 (G): nur ein echtes 403 von AutoSchnell sperrt — eines ohne JSON (Cloudflare) ist voruebergehend
            else if (ex.KeinAbo || ex.Gesperrt)
            {
                _lizenzFehler = 0;
                LizenzSperreSetzen(ex.Message);
                StatusAnzeigen(AktuellerStatus());
                if (beimStart) Sprechblase(ex.Message, true, erzwingen: true);
            }
            else if (ex.Voruebergehend || ex.Veraltet)
            {
                // still: in 1, 2, dann alle 5 Minuten erneut — das Symbol bleibt, wie es ist
                _lizenzFehler++;
                var pause = TimeSpan.FromMinutes(_lizenzFehler switch { 1 => 1, 2 => 2, _ => 5 });
                Interlocked.Exchange(ref _naechsteLizenz, Environment.TickCount64 + LizenzTakt(pause));
                Protokoll.Schreibe($"Lizenzprüfung vorübergehend nicht möglich ({_lizenzFehler}. Mal) – erneut in {pause.TotalMinutes:0} min.");
            }
            else
            {
                _symbol.Icon = _iconFehler;
                Sprechblase(ex.Message, true, erzwingen: true);
            }
        }
        finally { _lizenzLaeuft = false; }
    }

    /// <summary>1.5.9 (B/C): die Sprechblase zur neuen Version — kurz, mit dem Weg zum dauerhaften Menue-Eintrag. (rein, fuer Tests)</summary>
    internal static string UpdateText(string version) =>
        $"Neue Version {version} verfügbar – auf der Leiste „Mehr ▾“ → „Update auf {version} verfügbar …“.";

    private void LizenzSperreSetzen(string? grund)
    {
        if (_lizenzSperre == grund) return;
        _lizenzSperre = grund;
        if (_ueberwacher != null) _ueberwacher.LizenzGesperrt = grund != null;
        Protokoll.Schreibe(grund != null ? "Gesperrt (Lizenzprüfung): " + grund : "Sperre aufgehoben (Lizenzprüfung ok).");
        _fenster.Aktualisieren();
    }

    /// <summary>Befund 7 (09.10.2026): der Grund des letzten Verbindungsverlusts — steht im Verbinden-Dialog, sobald der
    /// Sucher ihn selbst oeffnet (Leiste "NICHT VERBUNDEN" bzw. "Mehr ▾" → "Mit AutoSchnell verbinden …").</summary>
    private string? _verlustHinweis;

    /// <summary>Pruefung 09.10.2026 (Befund 7): die Verbindung ging UNAUFGEFORDERT verloren (401 aus dem Lese-Takt oder der
    /// 15-min-Lizenzpruefung — ein anderer PC hat sich mit dem Konto verbunden). Vorher sprang hier sofort der
    /// Verbinden-Dialog auf (TopMost, aktiviert) und unterbrach das Tippen in AutoPointer. Jetzt: nur Sprechblase,
    /// Leisten-Status "NICHT VERBUNDEN" (Klick = verbinden) und rotes Symbol — der Dialog kommt erst auf Klick.</summary>
    private void VerbindungVerloren(string meldung)
    {
        _einstellungen.SchluesselSetzen(null, null);
        Speichern(_einstellungen);
        LizenzSperreSetzen(null);
        VerbindungAnzeigen();
        StatusAnzeigen(Status.NichtVerbunden);
        _verlustHinweis = meldung;
        if (_verbindenForm is { IsDisposed: false }) return;       // der Dialog ist schon offen
        Sprechblase(VerlustText(meldung), true, erzwingen: true, ausfuehrlich: meldung + " " + VerlustAnleitung);
    }

    internal const string VerlustAnleitung = "Zum Neuverbinden auf der Leiste „NICHT VERBUNDEN“ anklicken.";

    /// <summary>Befund 7: Sprechblase zum Verbindungsverlust — die Meldung des Servers (gekuerzt) plus der Weg zum Dialog,
    /// zusammen hoechstens <see cref="Hinweis.MaxZeichen"/>. (rein, fuer Tests)</summary>
    internal static string VerlustText(string meldung)
    {
        string rest = " " + VerlustAnleitung;
        return Hinweis.Kuerzen(meldung.Trim(), Hinweis.MaxZeichen - rest.Length) + rest;
    }

    /// <param name="aktivieren">Befund 7: true auf Nutzerklick (der Dialog darf aktiv werden); false ohne Anlass vom Nutzer
    /// (Programmstart) — dann nimmt er niemandem den Fokus.</param>
    private void VerbindenZeigen(string? hinweis, bool aktivieren = true)
    {
        if (_verbindenForm is { IsDisposed: false }) { if (aktivieren) _verbindenForm.Activate(); return; }
        hinweis ??= _verlustHinweis;
        _verlustHinweis = null;
        using var f = new VerbindenForm(_dienst, hinweis, aktivieren);
        _verbindenForm = f;
        try
        {
            if (f.ShowDialog() != DialogResult.OK || f.Ergebnis == null) return;
            var r = f.Ergebnis;
            string als = AutoSchnellDienst.KontoText(r.Name, r.Konto, r.Firma);
            _einstellungen.SchluesselSetzen(r.Schluessel, als);
            // Pruefung 08.10.2026 (1.5.9, F): nach dem ersten Verbinden mit Windows starten — sonst oeffnete am naechsten
            // Morgen nichts mehr, und niemand wusste warum. Ein bewusstes "aus" (einmal selbst umgestellt) bleibt aus.
            if (_einstellungen.AutostartNachVerbinden())
            {
                _einstellungen.MitWindowsStarten = true;
                Protokoll.Schreibe("Autostart nach dem ersten Verbinden eingeschaltet.");
            }
            Speichern(_einstellungen);
            Protokoll.Schreibe($"Mit AutoSchnell verbunden: {als}");
            _lizenzFehler = 0;
            LizenzSperreSetzen(null);           // der Code gibt es nur mit aktivem Abo
            VerbindungAnzeigen();
            _ueberwacher?.NachVerbinden();      // das gerade angezeigte Auto gleich vergleichen
            StatusAnzeigen(AktuellerStatus());
            Sprechblase($"Verbunden als {als}. In AutoPointer ein Auto anklicken – die Vergleiche öffnen sich.", false, erzwingen: true);
        }
        finally { _verbindenForm = null; }
    }

    private async Task TrennenAsync()
    {
        if (MessageBox.Show("Verbindung zu AutoSchnell trennen? Danach öffnet das Programm keine Vergleiche mehr, "
                            + "bis es wieder mit einem Code verbunden wird.", Name,
                            MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
        await _dienst.AbmeldenAsync();
        _einstellungen.SchluesselSetzen(null, null);
        Speichern(_einstellungen);
        LizenzSperreSetzen(null);
        Protokoll.Schreibe("Verbindung zu AutoSchnell getrennt.");
        VerbindungAnzeigen();
        StatusAnzeigen(Status.NichtVerbunden);
    }

    private void Starten()
    {
        var ocr = TextErkennung.Erstelle(out string fehler);
        if (ocr == null)
        {
            Protokoll.Schreibe(fehler);
            bool erstesMal = _ocrFehlt == null;
            _ocrFehlt = fehler;
            StatusAnzeigen(AktuellerStatus());
            _fenster.Aktualisieren();
            if (erstesMal)
            {
                // 1.5.9 (C): kurz in die Sprechblase, die Anleitung ganz ins Fenster (zeigt der Systemcheck auch)
                Sprechblase("Windows-Texterkennung fehlt – es wird nichts gelesen. Anleitung: „Mehr ▾“ → „Status und Hilfe“.",
                            true, erzwingen: true, ausfuehrlich: fehler);
                // Nr. 8: ohne Texterkennung geht nichts — gleich den Systemcheck zeigen (sagt, was fehlt und was zu tun ist)
                _ui.Post(async _ => await SystemcheckZeigenAsync(), null);
            }
            // Pruefung 05.10.2026 (Paket 2, A9): eigener Zustand "Texterkennung fehlt" in Leiste und Fenster, jede
            // Minute erneut versuchen (Sprachpaket nachinstalliert) — danach laeuft alles ohne Programm-Neustart
            Task.Delay(TimeSpan.FromSeconds(60), _ende.Token).ContinueWith(t =>
            {
                if (!t.IsCanceled) _ui.Post(_ => { if (_ueberwacher == null) Starten(); }, null);
            }, TaskScheduler.Default);
            return;
        }
        if (_ocrFehlt != null)
        {
            Protokoll.Schreibe("Texterkennung jetzt verfügbar – das Programm läuft normal weiter.");
            Sprechblase("Texterkennung jetzt verfügbar – das Programm läuft normal weiter.", false, erzwingen: true);
            _ocrFehlt = null;
        }
        Protokoll.Schreibe($"Texterkennung: {ocr.Sprache}");
        _ocr = ocr;
        var quelle = new AutoPointerQuelle(ocr, () => _einstellungen);
        _quelle = quelle;
        _klicks = new Klicks(() => quelle.Hauptfenster);
        var klicks = _klicks;
        _ueberwacher = new Ueberwacher(quelle, () => _einstellungen, new BrowserAusgabe(_ui),
                                       new DienstVermittler(() => _dienst),
                                       letzterKlick: () => klicks.LetzterKlick) { Probelauf = _probelauf, LizenzGesperrt = _lizenzSperre != null };
        _ueberwacher.StatusGeaendert += s => _ui.Post(_ => StatusAnzeigen(AktuellerStatus()), null);
        _ueberwacher.Meldung += h => _ui.Post(_ => Sprechblase(h.Text, h.Fehler, ausfuehrlich: h.Ausfuehrlich, blase: h.Sprechblase), null);
        _ueberwacher.VerbindungVerloren += m => _ui.Post(_ => VerbindungVerloren(m), null);
        _ueberwacher.FahrzeugGewechselt += _ => _zwischenablageStand = Native.GetClipboardSequenceNumber();
        _ueberwacher.Neustart();

        var token = _ende.Token;
        Task.Run(async () =>
        {
            while (!token.IsCancellationRequested)
            {
                // Dauerlauf (27.11.2026): solange er laeuft, ruht die Klick-Automatik — sonst loesten die Klicks des
                // Dauerlaufs fuer jedes Auto einen Vergleich samt Browser-Tabs aus
                if (_dauerlaufAn)
                {
                    bool tat = false;
                    try { tat = await DauerlaufSchrittAsync(); }
                    catch (Exception ex)
                    {
                        Protokoll.SchreibeGedrosselt("dauerlauf:" + ex.GetType().Name, "Dauerlauf: " + ex, TimeSpan.FromSeconds(60));
                    }
                    try { await Task.Delay(tat ? 20 : 150, token); }
                    catch (OperationCanceledException) { break; }
                    continue;
                }
                // 28.11.2026: eingeschaltet, aber wartend — sobald der PC ruht, von selbst weitermachen
                if (_einstellungen.DauerlaufEingeschaltet && !_dlStartet && DauerlaufKannWeiter())
                {
                    _dlStartet = true;
                    _ui.Post(_ => { try { DauerlaufLos(null); } finally { _dlStartet = false; } }, null);
                }
                try { await _ueberwacher.TickAsync(); }
                catch (Exception ex)
                {
                    // Paket 1: derselbe Fehler alle 250 ms wuerde das Protokoll fluten — je Fehlerart alle 60 s
                    Protokoll.SchreibeGedrosselt("takt:" + ex.GetType().Name + ":" + ex.Message, "Fehler: " + ex,
                                                 TimeSpan.FromSeconds(60));
                }
                // Paket 3 (F2): solange eine Aenderung offen ist, alle 100 ms nachsehen (Vergleich bis 150 ms frueher)
                try { await Task.Delay(_ueberwacher.KurzerTakt ? 100 : 250, token); }
                catch (OperationCanceledException) { break; }
            }
        }, token);
    }

    // ---- Dauerlauf (Wunsch Ahmad 27.11.2026) ------------------------------------------------------------------
    // Der PC laeuft unbeaufsichtigt: das Programm liest die Liste mit, sortiert vor, klickt passende Autos selbst an,
    // liest sie ganz und legt sie in die Trefferliste in AutoSchnell. Einmal im Regelfenster eingeschaltet, bleibt er
    // an (28.11.2026): bewegt jemand die Maus, WARTET er und macht nach DauerlaufRuheS Sekunden ohne Maus und Tastatur
    // von selbst weiter — auch nach einem Neustart des Programms. Ganz aus: Klick aufs blaue Feld oder Menue.
    private TextErkennung? _ocr;
    private Dauerlauf? _dauerlauf;
    private volatile bool _dauerlaufAn, _dlStartet;
    /// <summary>So lange muessen Maus und Tastatur ruhen, bevor der wartende Dauerlauf von selbst weitermacht.</summary>
    internal const int DauerlaufRuheS = 30;
    private int _dlNeu, _dlAussortiert, _dlGelesen, _dlTreffer;
    private volatile string _dlHindernis = "";
    private Native.POINT? _dlMaus;
    private long _dlAnzeige, _dlStart;
    internal const int DauerlaufSchonfristMs = 4000;

    /// <summary>So weit darf der Zeiger zwischen zwei Schritten wandern, ohne dass es als „jemand benutzt die Maus“ gilt.</summary>
    internal const int DauerlaufMausSpiel = 10;
    internal const string DauerlaufMausText = "Dauerlauf wartet – die Maus wurde bewegt. Er macht von selbst weiter, sobald der PC 30 s nicht benutzt wird. Ganz aus: Klick auf das Feld „DAUERLAUF“.";
    internal const string DauerlaufServerAlt = "Dauerlauf angehalten – AutoSchnell kennt die Trefferliste noch nicht (Server-Update fehlt).";

    /// <summary>Der kurze Stand fuer Leiste und Tooltip. (rein, fuer Tests)</summary>
    internal static string DauerlaufStand(int neu, int aussortiert, int gelesen, int treffer, string hindernis) =>
        $"{neu} neu · {aussortiert} aussortiert · {gelesen} gelesen · {treffer} in der Trefferliste"
        + (string.IsNullOrEmpty(hindernis) ? "" : $" – wartet: {hindernis}");

    /// <summary>Hat jemand die Maus bewegt? Verglichen wird, wo das Programm den Zeiger nach seinem letzten Schritt
    /// gelassen hat, mit dem, wo er jetzt steht. (rein, fuer Tests)</summary>
    internal static bool MausBewegt(Native.POINT? gelassen, Native.POINT jetzt) =>
        gelassen is { } g && (Math.Abs(g.X - jetzt.X) > DauerlaufMausSpiel || Math.Abs(g.Y - jetzt.Y) > DauerlaufMausSpiel);

    private void DauerlaufUmschalten()
    {
        if (_dauerlaufAn || _einstellungen.DauerlaufEingeschaltet)
        {
            bool lief = _dauerlaufAn;
            _einstellungen.DauerlaufEingeschaltet = false;
            Speichern(_einstellungen);
            if (lief) DauerlaufStoppen("Dauerlauf ausgeschaltet.", false);
            else
            {
                Protokoll.Schreibe("Dauerlauf ausgeschaltet (wartete).");
                Sprechblase("Dauerlauf ausgeschaltet.", false, erzwingen: true);
                StatusAnzeigen(AktuellerStatus());
                _fenster.Aktualisieren();
                _leiste.Aktualisieren();
            }
            return;
        }
        if (!_dienst.Verbunden || _lizenzSperre != null)
        {
            Sprechblase("Der Dauerlauf geht nur, wenn das Programm mit AutoSchnell verbunden ist und das Abo läuft.", true, erzwingen: true);
            return;
        }
        if (_ocr == null || _ueberwacher == null)
        {
            Sprechblase("Noch nicht bereit – bitte gleich noch einmal versuchen.", true, erzwingen: true);
            return;
        }
        IntPtr haupt = _quelle?.Hauptfenster ?? IntPtr.Zero;
        if (haupt == IntPtr.Zero || Native.IsIconic(haupt))
        {
            Sprechblase("Für den Dauerlauf muss AutoPointer offen und sichtbar sein (nicht minimiert).", true, erzwingen: true);
            return;
        }
        using var form = new DauerlaufForm(DauerlaufRegeln.Aus(_einstellungen.DauerlaufRegeln));
        if (form.ShowDialog() != DialogResult.OK) return;
        _einstellungen.DauerlaufRegeln = form.Regeln.AlsText();
        _einstellungen.DauerlaufEingeschaltet = true;
        Speichern(_einstellungen);
        _dlNeu = _dlAussortiert = _dlGelesen = _dlTreffer = 0;
        _dauerlauf = null;
        DauerlaufLos(haupt);
    }

    /// <summary>Darf der eingeschaltete, wartende Dauerlauf jetzt von selbst weitermachen? Nur wenn Maus und Tastatur
    /// <see cref="DauerlaufRuheS"/> Sekunden ruhen (niemand arbeitet am PC) und alles da ist.</summary>
    private bool DauerlaufKannWeiter()
    {
        if (_dauerlaufAn || !_dienst.Verbunden || _lizenzSperre != null || _ocr == null || _ueberwacher == null) return false;
        if (!RuheReicht(Native.RuheMs())) return false;
        IntPtr haupt = _quelle?.Hauptfenster ?? IntPtr.Zero;
        return haupt != IntPtr.Zero && !Native.IsIconic(haupt);
    }

    /// <summary>(rein, fuer Tests)</summary>
    internal static bool RuheReicht(long ruheMs) => ruheMs >= DauerlaufRuheS * 1000L;

    /// <summary>Startet bzw. setzt fort (UI-Faden). haupt = null: von selbst nach der Ruhezeit — dann still, ohne Sprechblase.</summary>
    private void DauerlaufLos(IntPtr? hauptVomStart)
    {
        if (_dauerlaufAn || !_einstellungen.DauerlaufEingeschaltet || _ocr == null) return;
        bool vonSelbst = hauptVomStart == null;
        if (vonSelbst && !DauerlaufKannWeiter()) return;
        IntPtr haupt = hauptVomStart ?? _quelle?.Hauptfenster ?? IntPtr.Zero;
        if (haupt == IntPtr.Zero) return;
        var regeln = DauerlaufRegeln.Aus(_einstellungen.DauerlaufRegeln);
        _dlHindernis = "";
        _dlMaus = null;
        _dlStart = Environment.TickCount64;
        // beim Fortsetzen dasselbe Objekt: es weiss, welche Autos schon gelesen sind
        _dauerlauf ??= new Dauerlauf(_ocr, () => regeln, DauerlaufEreignis);
        try { if (!AutoPointerFenster.ImVordergrund(haupt)) BrowserOeffner.ZurueckZu(haupt); }
        catch (Exception ex) { Protokoll.Schreibe("Dauerlauf: AutoPointer nicht nach vorne geholt: " + ex.Message); }
        _dauerlaufAn = true;
        Protokoll.Schreibe((vonSelbst ? "Dauerlauf macht weiter (" : "Dauerlauf AN (") + regeln.AlsText() + ").");
        if (!vonSelbst)
            Sprechblase("Dauerlauf läuft – das Programm führt jetzt die Maus. Wird der PC benutzt, wartet er und macht danach von selbst weiter.",
                        false, erzwingen: true, merken: false);
        StatusAnzeigen(AktuellerStatus());
        _fenster.Aktualisieren();
        _leiste.Aktualisieren();
    }

    /// <summary>Haelt den Dauerlauf an (aus jedem Faden aufrufbar).</summary>
    /// <param name="wartet">true = nur unterbrochen (bleibt eingeschaltet, macht von selbst weiter); false = ganz aus.</param>
    private void DauerlaufStoppen(string meldung, bool fehler, bool wartet = false)
    {
        if (!_dauerlaufAn) return;
        _dauerlaufAn = false;
        if (!wartet && _einstellungen.DauerlaufEingeschaltet)
        {
            _einstellungen.DauerlaufEingeschaltet = false;
            _ui.Post(_ => Speichern(_einstellungen), null);
        }
        BeschreibungGanz.EchtesRad = false;          // der normale Betrieb nimmt dem Sucher nie die Maus
        Protokoll.Schreibe($"Dauerlauf AUS: {meldung} ({DauerlaufStand(_dlNeu, _dlAussortiert, _dlGelesen, _dlTreffer, "")})");
        _ui.Post(_ =>
        {
            // das gerade angezeigte Auto gilt als gesehen — sonst oeffnete die Automatik dafuer sofort einen Vergleich
            _ueberwacher?.Neustart();
            Sprechblase(meldung, fehler, erzwingen: true);
            StatusAnzeigen(AktuellerStatus());
            _fenster.Aktualisieren();
            _leiste.Aktualisieren();
        }, null);
    }

    private async Task<bool> DauerlaufSchrittAsync()
    {
        var lauf = _dauerlauf;
        if (lauf == null) return false;
        if (!_dienst.Verbunden || _lizenzSperre != null)
        {
            DauerlaufStoppen("Dauerlauf wartet – das Programm ist nicht mehr verbunden.", true, wartet: true);
            return false;
        }
        // die ersten Sekunden nach dem Start laesst der Sucher die Maus erst los
        if (Environment.TickCount64 - _dlStart > DauerlaufSchonfristMs && Native.GetCursorPos(out var jetzt) && MausBewegt(_dlMaus, jetzt))
        {
            DauerlaufStoppen(DauerlaufMausText, false, wartet: true);
            return false;
        }
        bool tat = await lauf.SchrittAsync();
        _dlMaus = Native.GetCursorPos(out var danach) ? danach : null;
        _dlNeu = lauf.Neu;
        _dlAussortiert = lauf.Aussortiert;
        _dlGelesen = lauf.Gelesen;
        bool geaendert = _dlHindernis != lauf.Hindernis;
        _dlHindernis = lauf.Hindernis;
        // Leiste hoechstens alle 2 s auffrischen (und sofort, wenn sich aendert, worauf gewartet wird)
        if (geaendert || Environment.TickCount64 - _dlAnzeige > 2000)
        {
            _dlAnzeige = Environment.TickCount64;
            _ui.Post(_ => _leiste.Aktualisieren(), null);
        }
        return tat;
    }

    private void DauerlaufEreignis(Dauerlauf.Ereignis e)
    {
        if (e.Art != "gelesen" || e.Fahrzeug is not { } f) return;
        if (_probelauf || !AutoSchnellDienst.TrefferMeldbar(f)) return;
        var zeile = e.Zeile;
        _ = Task.Run(async () =>
        {
            try
            {
                await _dienst.TrefferMeldenAsync(f, zeile);
                Interlocked.Increment(ref _dlTreffer);
            }
            catch (DienstFehler ex) when (ex.Status == 404)
            {
                DauerlaufStoppen(DauerlaufServerAlt, true);
            }
            catch (DienstFehler ex) when (ex.Status is 401 or 402 or 403)
            {
                DauerlaufStoppen("Dauerlauf angehalten – " + ex.Message, true);
            }
            catch (Exception ex)
            {
                Protokoll.SchreibeGedrosselt("dauerlauf-senden", "Dauerlauf: Treffer nicht gesendet: " + ex.Message, TimeSpan.FromSeconds(60));
            }
        });
    }

    private void AutomatikUmschalten() => AutomatikSetzen(!_einstellungen.AutomatikAktiv);

    private void AutomatikSetzen(bool an)
    {
        if (_einstellungen.AutomatikAktiv == an) { _fenster.Aktualisieren(); return; }
        _einstellungen.AutomatikAktiv = an;
        Speichern(_einstellungen);
        if (_einstellungen.AutomatikAktiv) _ueberwacher?.Neustart();
        AutomatikAnzeigen();
        Protokoll.Schreibe(_einstellungen.AutomatikAktiv ? "Automatik AN." : "Automatik AUS.");
        Sprechblase(_einstellungen.AutomatikAktiv ? "Automatik AN – Vergleiche öffnen sich beim Anklicken." : "Automatik AUS – es öffnet sich nichts.",
                    false, erzwingen: true, merken: false);
        _fenster.Aktualisieren();
    }

    private void AutomatikAnzeigen()
    {
        StatusAnzeigen(AktuellerStatus());
    }

    private void StatusAnzeigen(Status s)
    {
        if (!_einstellungen.AutomatikAktiv) s = Status.Pause;
        _symbol.Icon = s switch
        {
            Status.Pause => _iconPause,
            Status.KeinAutoPointer => _iconWarten,
            Status.NichtVerbunden or Status.Gesperrt or Status.TexterkennungFehlt => _iconFehler,
            _ => _iconAktiv,
        };
        string text = s switch
        {
            Status.Pause => "Automatik aus",
            Status.KeinAutoPointer => "AutoPointer nicht gefunden",
            Status.Bereit => "bereit – kein Fahrzeug angezeigt",
            Status.NichtVerbunden => "nicht mit AutoSchnell verbunden",
            Status.Gesperrt => "gesperrt: " + (_lizenzSperre ?? "Abo/Freigabe"),
            Status.TexterkennungFehlt => "Windows-Texterkennung fehlt",
            _ => "aktiv",
        };
        var letztes = _ueberwacher?.LetztesFahrzeug;
        string zeile = $"{Name}{(_probelauf ? " (Probelauf)" : "")}: {text}";
        if (letztes != null) zeile += $"\nZuletzt: {letztes.Marke} {letztes.Modell} {letztes.EzText}";
        _symbol.Text = zeile.Length > 127 ? zeile[..127] : zeile;
    }

    /// <param name="ausfuehrlich">1.5.9 (C): die ganze Erklaerung fuer "Letzte Hinweise" im Fenster (die Sprechblase
    /// bekommt hoechstens 150 Zeichen — Windows schneidet bei ~255 ab, und die Anweisungen standen am Ende).</param>
    /// <param name="blase">false = nur ins Fenster, keine Sprechblase.</param>
    /// <param name="merken">false = nicht unter "Letzte Hinweise" (reine Bestaetigungen wie "Automatik AN").</param>
    private void Sprechblase(string text, bool fehler, bool erzwingen = false, string? ausfuehrlich = null,
                             bool blase = true, bool merken = true)
    {
        string kurz = Hinweis.Kuerzen(text);
        if (ausfuehrlich == null && kurz != text.Trim()) ausfuehrlich = text.Trim();
        _letzteMeldung = $"{DateTime.Now:HH:mm} {kurz}";      // bleibt im Fenster stehen, die Sprechblase verschwindet
        if (merken) HinweisMerken(ausfuehrlich ?? kurz);
        StatusAnzeigen(AktuellerStatus());
        if (!_fenster.IsDisposed && _fenster.Visible) _fenster.Aktualisieren();
        if (!blase) return;
        if (!erzwingen && !_einstellungen.HinweiseAnzeigen) return;
        if (!erzwingen && Environment.TickCount64 - _letzteSprechblase < 3000) return;    // A10: monoton
        _letzteSprechblase = Environment.TickCount64;
        _symbol.ShowBalloonTip(4000, Name, kurz, fehler ? ToolTipIcon.Warning : ToolTipIcon.Info);
    }

    /// <summary>1.5.9 (C): die letzten <see cref="HinweiseImFenster"/> Hinweise mit Uhrzeit, der neueste zuerst; derselbe
    /// Text gleich noch einmal ersetzt den alten (nur neue Uhrzeit).</summary>
    private void HinweisMerken(string text)
    {
        string eintrag = $"{DateTime.Now:HH:mm} {text}";
        if (_hinweise.First is { } erster && erster.Value[(Math.Min(6, erster.Value.Length))..] == text) _hinweise.RemoveFirst();
        _hinweise.AddFirst(eintrag);
        while (_hinweise.Count > HinweiseImFenster) _hinweise.RemoveLast();
    }

    private void EinstellungenZeigen()
    {
        if (_einstellungenForm != null) { _einstellungenForm.Activate(); return; }
        using var f = new EinstellungenForm(_einstellungen);
        _einstellungenForm = f;
        try
        {
            if (f.ShowDialog() != DialogResult.OK) return;
            bool warAn = _einstellungen.AutomatikAktiv;
            // Paket 2 (A11): nur die Dialogfelder uebernehmen — Schluessel/VerbundenAls/Server koennen sich
            // waehrend des offenen Dialogs geaendert haben (Lizenzpruefung, 401). 1.5.9 (F): merkt sich auch, ob der
            // Sucher "mit Windows starten" selbst umgestellt hat.
            f.AnwendenAuf(_einstellungen);
            Speichern(_einstellungen);
            if (!warAn && _einstellungen.AutomatikAktiv) _ueberwacher?.Neustart();
            HotkeyAnwenden();
            AutomatikAnzeigen();
            LeisteAnwenden();
            Protokoll.Schreibe("Einstellungen gespeichert.");
        }
        finally { _einstellungenForm = null; }
    }

    private void Speichern(Einstellungen neu)
    {
        try { neu.Speichern(); }
        catch (Exception ex)
        {
            // 1.5.9 (E): die .NET-Meldung nur ins Protokoll
            Protokoll.Schreibe("Einstellungen nicht gespeichert: " + ex);
            Sprechblase("Einstellungen konnten nicht gespeichert werden – bitte noch einmal versuchen.", true, erzwingen: true);
        }
        _einstellungen = neu;
    }

    private void HotkeyAnwenden()
    {
        _hotkey.Abmelden(HotkeyId);
        if (_einstellungen.TastenkuerzelAktiv && !_hotkey.Anmelden(HotkeyId, Native.MOD_CONTROL | Native.MOD_ALT, (uint)Keys.P))
            Protokoll.Schreibe("Strg+Alt+P ist schon von einem anderen Programm belegt – Umschalten nur über das Symbol.");
    }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 6/8): Systemcheck mit Probe-Lesung und Probe-Vergleich.</summary>
    private async Task SystemcheckZeigenAsync()
    {
        if (_systemcheckLaeuft) return;
        _systemcheckLaeuft = true;
        try
        {
            Sprechblase("Systemcheck läuft …", false, erzwingen: true, merken: false);
            var punkte = await Systemcheck.PruefenAsync(_einstellungen, _dienst);
            string text = Systemcheck.Text(punkte);
            Protokoll.Schreibe("Systemcheck:\n" + text);
            bool fehler = punkte.Any(p => p.Stufe == PruefStufe.Fehler);
            MessageBox.Show(text + "\n\n(Strg+C kopiert diesen Text.)",
                fehler ? "Systemcheck – bitte beheben" : "Systemcheck – alles bereit",
                MessageBoxButtons.OK, fehler ? MessageBoxIcon.Warning : MessageBoxIcon.Information);
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Systemcheck fehlgeschlagen: " + ex);
            Sprechblase("Der Systemcheck hat nicht geklappt – bitte noch einmal versuchen.", true, erzwingen: true);
        }
        finally { _systemcheckLaeuft = false; }
    }

    /// <summary>Pruefung 08.10.2026 (1.5.9, F): "Beenden" fragt erst — wer das Programm aus Versehen beendet, bekam sonst
    /// einfach keine Vergleiche mehr und wusste nicht, wie es wieder angeht.</summary>
    internal const string BeendenFrage =
        "Danach öffnen sich keine Vergleiche mehr. Wieder starten: Startmenü → AutoSchnell Vergleich.";

    private bool _frageOffen;

    private void BeendenFragen()
    {
        if (_beendet || _frageOffen) return;
        _frageOffen = true;
        try
        {
            var beenden = new TaskDialogButton("Beenden");
            var abbrechen = new TaskDialogButton("Abbrechen");
            var seite = new TaskDialogPage
            {
                Caption = Name,
                Heading = $"{Name} beenden?",
                Text = BeendenFrage,
                Icon = TaskDialogIcon.Warning,
                Buttons = { beenden, abbrechen },
                DefaultButton = abbrechen,
                AllowCancel = true,
            };
            TaskDialogButton antwort;
            try { antwort = TaskDialog.ShowDialog(seite, TaskDialogStartupLocation.CenterScreen); }
            catch (Exception ex)
            {
                // ohne TaskDialog (sehr alte Darstellung): dieselbe Frage als einfache Meldung
                Protokoll.Schreibe("Rückfrage beim Beenden ohne TaskDialog: " + ex.Message);
                antwort = MessageBox.Show(BeendenFrage + "\n\nJetzt beenden?", Name, MessageBoxButtons.OKCancel,
                                          MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2) == DialogResult.OK ? beenden : abbrechen;
            }
            if (antwort != beenden)
            {
                Protokoll.Schreibe("Beenden abgebrochen.");
                return;
            }
        }
        finally { _frageOffen = false; }
        Beenden();
    }

    // ---- Sprachdiktat (Wunsch Ahmad 24.11.2026) ----
    // Knopf "Diktat" in der Leiste oder Strg+Alt+D: aufnehmen, nochmal = auf diesem PC erkennen (Whisper) und in das
    // Fenster tippen, das beim Start vorne war. Ist inzwischen ein anderes Fenster vorne, landet der Text in der
    // Zwischenablage. Ins Protokoll kommt nur die Laenge, nie der gesprochene Text.
    private const int DiktatHotkeyId = 0x4151;
    private readonly Diktat _diktat = new();
    private bool _diktatBeschaeftigt;
    private IntPtr _diktatZiel;
    private long _diktatStart;
    private System.Windows.Forms.Timer? _diktatUhr;
    // Rückgängig (24.11.2026): 8 s nach dem Eintippen Knopf "↶" — loescht per Ruecktaste genau das Eingetippte,
    // aber nur, wenn dasselbe Fenster noch vorne ist.
    private const int RueckgaengigMs = 8000;
    private int _diktatLaenge;
    private IntPtr _diktatGetipptIn;
    private System.Windows.Forms.Timer? _diktatZurueckUhr;

    private void DiktatZuruecknehmenBeenden()
    {
        _diktatZurueckUhr?.Stop();
        _diktatLaenge = 0;
        _diktatGetipptIn = IntPtr.Zero;
        if (!_leiste.IsDisposed) _leiste.DiktatZuruecknehmenZeigen(false);
    }

    private void DiktatZuruecknehmen()
    {
        int laenge = _diktatLaenge;
        IntPtr ziel = _diktatGetipptIn;
        DiktatZuruecknehmenBeenden();
        if (laenge <= 0) return;
        if (ziel == IntPtr.Zero || Native.GetForegroundWindow() != ziel)
        {
            Sprechblase("Rückgängig geht nur, solange das Fenster mit dem Text vorne ist.", false, erzwingen: true);
            return;
        }
        Diktat.Loeschen(laenge);
        Protokoll.Schreibe($"Diktat: {laenge} Zeichen zurückgenommen.");
    }

    private void DiktatUmschalten()
    {
        if (_beendet || _diktatBeschaeftigt) return;
        if (_diktat.NimmtAuf) { _ = DiktatBeendenAsync(); return; }
        DiktatZuruecknehmenBeenden();
        if (!_dienst.Verbunden || _lizenzSperre != null)
        {
            Sprechblase("Diktat geht nur, wenn das Programm mit AutoSchnell verbunden ist und das Abo läuft.", true, erzwingen: true);
            return;
        }
        if (!Diktat.HatMikrofon)
        {
            Sprechblase("Kein Mikrofon gefunden – bitte Mikrofon oder Headset anschließen.", true, erzwingen: true);
            return;
        }
        if (!_diktat.ModellVorhanden) { _ = DiktatModellLadenAsync(); return; }
        _diktatZiel = Native.GetForegroundWindow();
        try { _diktat.AufnahmeStarten(); }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Diktat: Mikrofon ließ sich nicht öffnen: " + ex.Message);
            Sprechblase("Das Mikrofon lässt sich nicht öffnen – in Windows unter Datenschutz → Mikrofon erlauben.", true, erzwingen: true);
            return;
        }
        _diktatStart = Environment.TickCount64;
        _ = Task.Run(() => { try { _diktat.Vorbereiten(); } catch (Exception) { /* Fehler kommt beim Erkennen */ } });
        if (_diktatUhr == null)
        {
            _diktatUhr = new System.Windows.Forms.Timer { Interval = 200 };
            _diktatUhr.Tick += (_, _) => DiktatUhrTick();
        }
        _diktatUhr.Start();
        DiktatUhrTick();
    }

    private void DiktatUhrTick()
    {
        long sek = (Environment.TickCount64 - _diktatStart) / 1000;
        if (sek >= Diktat.MaxSekunden) { _ = DiktatBeendenAsync(); return; }
        // Auto-Stopp: 2 s Stille, nachdem schon gesprochen wurde
        if (_diktat.StilleMs >= Diktat.AutoStoppMs) { _ = DiktatBeendenAsync(); return; }
        if (!_leiste.IsDisposed)
            _leiste.DiktatAnzeigen($"Stopp {sek / 60}:{sek % 60:00} {Diktat.PegelBalken(_diktat.Pegel)}", aufnahme: true);
    }

    private async Task DiktatBeendenAsync()
    {
        _diktatUhr?.Stop();
        if (!_diktat.NimmtAuf) return;
        bool gesprochen = _diktat.StilleMs >= 0;
        var proben = _diktat.AufnahmeStoppen();
        // Messung 24.11.2026: aus 5 s Stille macht Whisper "Amen." — ohne Sprache gar nicht erst erkennen
        if (!gesprochen)
        {
            if (!_leiste.IsDisposed) _leiste.DiktatAnzeigen(null, aufnahme: false);
            Sprechblase("Nichts gehört – bitte näher am Mikrofon noch einmal sprechen.", false, erzwingen: true);
            return;
        }
        _diktatBeschaeftigt = true;
        if (!_leiste.IsDisposed) _leiste.DiktatAnzeigen("Erkennt …", aufnahme: false);
        try
        {
            var abbruch = _ende.Token;
            string text = await Task.Run(() => _diktat.ErkennenAsync(proben, abbruch), abbruch);
            if (_beendet) return;
            // Sprachbefehle ("Komma", "Punkt") und Fachwoerter (TÜV, AHK …), wie in der App
            text = Diktat.FuerEingabe(DiktatRegeln.Nachbearbeiten(text));
            if (text.Length == 0)
            {
                Sprechblase("Nichts verstanden – bitte näher am Mikrofon noch einmal sprechen.", false, erzwingen: true);
                return;
            }
            if (_diktatZiel != IntPtr.Zero && Native.GetForegroundWindow() == _diktatZiel && Diktat.Eintippen(text))
            {
                Protokoll.Schreibe($"Diktat: {text.Length} Zeichen eingefügt.");
                _diktatLaenge = text.Length;
                _diktatGetipptIn = _diktatZiel;
                if (_diktatZurueckUhr == null)
                {
                    _diktatZurueckUhr = new System.Windows.Forms.Timer { Interval = RueckgaengigMs };
                    _diktatZurueckUhr.Tick += (_, _) => DiktatZuruecknehmenBeenden();
                }
                _diktatZurueckUhr.Stop();
                _diktatZurueckUhr.Start();
                if (!_leiste.IsDisposed) _leiste.DiktatZuruecknehmenZeigen(true);
            }
            else
            {
                Clipboard.SetText(text);
                Sprechblase("Das Diktat liegt in der Zwischenablage – mit Strg+V einfügen.", false, erzwingen: true);
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Diktat fehlgeschlagen: " + ex);
            Sprechblase("Das Diktat hat nicht geklappt – bitte noch einmal versuchen.", true, erzwingen: true);
        }
        finally
        {
            _diktatBeschaeftigt = false;
            if (!_leiste.IsDisposed) _leiste.DiktatAnzeigen(null, aufnahme: false);
        }
    }

    /// <summary>Im Hintergrund: Modell in den Speicher, Grafikkarte einrichten (erstes Mal ~30 s, danach &lt;1 s).</summary>
    private async Task DiktatAufwaermenAsync()
    {
        try
        {
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            await _diktat.AufwaermenAsync(_ende.Token);
            if (_diktat.ModellVorhanden) Protokoll.Schreibe($"Diktat: bereit nach {uhr.Elapsed.TotalSeconds:F1} s.");
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) { Protokoll.Schreibe("Diktat: Vorwärmen ging nicht: " + ex.Message); }
    }

    private async Task DiktatModellLadenAsync()
    {
        if (MessageBox.Show(
                $"Für das Diktat lädt das Programm einmalig ein Sprachmodell (ca. {Diktat.ModellMB} MB) auf diesen PC.\n\n"
                + "Danach funktioniert das Diktat auch ohne Internet, und die Sprache verlässt den PC nie.\n\nJetzt laden?",
                Name, MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes) return;
        _diktatBeschaeftigt = true;
        int zuletzt = -1;
        var fortschritt = new Progress<double>(p =>
        {
            int prozent = (int)(p * 100);
            if (prozent == zuletzt || _leiste.IsDisposed) return;
            zuletzt = prozent;
            _leiste.DiktatAnzeigen($"Lädt {prozent} %", aufnahme: false);
        });
        try
        {
            if (!_leiste.IsDisposed) _leiste.DiktatAnzeigen("Lädt 0 %", aufnahme: false);
            await _diktat.ModellLadenAsync(fortschritt, _ende.Token);
            Protokoll.Schreibe("Diktat: Sprachmodell geladen.");
            _ = DiktatAufwaermenAsync();
            Sprechblase("Diktat ist bereit: ins Feld klicken, dann „Diktat“ tippen (oder Strg+Alt+D) und sprechen.", false, erzwingen: true);
        }
        catch (OperationCanceledException) { }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Diktat: Sprachmodell nicht geladen: " + ex.Message);
            Sprechblase("Das Sprachmodell konnte nicht geladen werden – bitte Internet prüfen und erneut versuchen.", true, erzwingen: true);
        }
        finally
        {
            _diktatBeschaeftigt = false;
            if (!_leiste.IsDisposed) _leiste.DiktatAnzeigen(null, aufnahme: false);
        }
    }

    /// <summary>Wirklich beenden — ohne Rueckfrage (nach "Beenden" in der Rueckfrage oder wenn eine neuere Version
    /// startet, 1.5.9 B).</summary>
    private void Beenden()
    {
        if (_beendet) return;
        _beendet = true;
        Protokoll.Schreibe("Beendet.");
        SystemEvents.PowerModeChanged -= PowerModeGeaendert;
        NetworkChange.NetworkAvailabilityChanged -= NetzGeaendert;
        _fenster.EndgueltigSchliessen();
        _leistenHandle = IntPtr.Zero;
        _leiste.EndgueltigSchliessen();
        _ende.Cancel();
        _klicks?.Dispose();
        _diktatUhr?.Stop();
        _diktatZurueckUhr?.Stop();
        _diktat.Dispose();
        _hotkey.Abmelden(HotkeyId);
        _hotkey.Abmelden(DiktatHotkeyId);
        _hotkey.DestroyHandle();
        _symbol.Visible = false;
        _symbol.Dispose();
        // 1.5.9 (B): Version und Signale freigeben — ein zweiter Start sieht dann kein laufendes Programm mehr
        try { _versionVeroeffentlicht?.Dispose(); } catch (Exception) { }
        try { _beendenSignal.Dispose(); } catch (Exception) { }
        ExitThread();
    }

    /// <summary>Unsichtbares Fenster, das die globale Tastenkombination empfaengt.</summary>
    private sealed class HotkeyFenster : NativeWindow
    {
        private readonly Action<int> _aktion;
        private readonly HashSet<int> _angemeldet = new();

        public HotkeyFenster(Action<int> aktion)
        {
            _aktion = aktion;
            CreateHandle(new CreateParams());
        }

        public bool Anmelden(int id, uint modifier, uint taste)
        {
            bool ok = Native.RegisterHotKey(Handle, id, modifier | Native.MOD_NOREPEAT, taste);
            if (ok) _angemeldet.Add(id);
            return ok;
        }

        public void Abmelden(int id)
        {
            if (_angemeldet.Remove(id)) Native.UnregisterHotKey(Handle, id);
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == Native.WM_HOTKEY) _aktion((int)m.WParam);
            base.WndProc(ref m);
        }
    }
}


/// <summary>Reicht an den jeweils aktuellen Dienst weiter (nach einem Neu-Verbinden).</summary>
internal sealed class DienstVermittler : IVergleichsDienst
{
    private readonly Func<IVergleichsDienst> _dienst;
    public DienstVermittler(Func<IVergleichsDienst> dienst) => _dienst = dienst;
    public bool Verbunden => _dienst().Verbunden;
    public Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf) => _dienst().VergleichAsync(f, probelauf);
    public void Vorwaermen() => _dienst().Vorwaermen();
    /// <summary>Pruefung 08.10.2026 (1.5.9, A): in 1.5.8 fehlte hier das Weiterreichen der Vorgangs-Nachfrage — die
    /// Standard-Umsetzung der Schnittstelle sagte immer "nicht pruefbar", und das Programm oeffnete jedes Mal selbst.</summary>
    public Task<bool?> VorgangSelbstAsync(string vorgangId) => _dienst().VorgangSelbstAsync(vorgangId);
    /// <summary>1.5.13: Lesebild an den jeweils aktuellen Dienst (nach einem Neu-Verbinden der neue Schluessel).</summary>
    public Task<bool> LesebildSendenAsync(Lesebild bild) => _dienst().LesebildSendenAsync(bild);
}
