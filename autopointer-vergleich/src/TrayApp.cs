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

    private readonly NotifyIcon _symbol;
    private readonly ToolStripMenuItem _automatik;
    private readonly Icon _iconAktiv = Symbole.Icon(Symbole.Aktiv);
    private readonly Icon _iconPause = Symbole.Icon(Symbole.Pause);
    private readonly Icon _iconWarten = Symbole.Icon(Symbole.Warten);
    private readonly Icon _iconFehler = Symbole.Icon(Symbole.Fehler);
    private readonly ToolStripMenuItem _verbindungsZeile;
    private readonly ToolStripMenuItem _trennen;
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
    private readonly ToolStripMenuItem _leisteMenue;
    private AutoPointerQuelle? _quelle;
    /// <summary>Wunsch Ahmad 06.10.2026: letzter Mausklick in AutoPointer — nur danach wird verglichen.</summary>
    private Klicks? _klicks;
    private readonly EventWaitHandle _zeigenSignal;
    private string? _letzteMeldung;
    /// <summary>Stand der Zwischenablage, als das letzte Auto dran war (Nr. 11).</summary>
    private uint _zwischenablageStand;
    private bool _updateGemeldet;
    private bool _systemcheckLaeuft;

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

        var menue = new ContextMenuStrip();
        var oeffnen = new ToolStripMenuItem("Fenster öffnen", null, (_, _) => FensterZeigen());
        oeffnen.Font = new Font(oeffnen.Font, FontStyle.Bold);
        menue.Items.Add(oeffnen);
        _leisteMenue = new ToolStripMenuItem("Kleine Leiste anzeigen", null, (_, _) => LeisteUmschalten());
        menue.Items.Add(_leisteMenue);
        menue.Items.Add(new ToolStripSeparator());
        _verbindungsZeile = new ToolStripMenuItem("Nicht verbunden") { Enabled = false };
        menue.Items.Add(_verbindungsZeile);
        menue.Items.Add("Mit AutoSchnell verbinden …", null, (_, _) => VerbindenZeigen(null));
        _trennen = new ToolStripMenuItem("Verbindung trennen", null, async (_, _) => await TrennenAsync());
        menue.Items.Add(_trennen);
        menue.Items.Add(new ToolStripSeparator());
        _automatik = new ToolStripMenuItem("Automatik aktiv", null, (_, _) => AutomatikUmschalten()) { CheckOnClick = false };
        menue.Items.Add(_automatik);
        menue.Items.Add("Aktuelles Fahrzeug jetzt vergleichen", null, (_, _) => VergleichenStarten());
        menue.Items.Add("Letzten Vergleich erneut öffnen", null, (_, _) => _ueberwacher?.LetztenErneutOeffnen());
        menue.Items.Add("Kaufvertrag: Auto in AutoSchnell öffnen", null, async (_, _) => await VertragOeffnenAsync());
        menue.Items.Add(new ToolStripSeparator());
        menue.Items.Add("Einstellungen …", null, (_, _) => EinstellungenZeigen());
        menue.Items.Add("Systemcheck: läuft alles? …", null, async (_, _) => await SystemcheckZeigenAsync());
        menue.Items.Add(new ToolStripSeparator());
        menue.Items.Add("Beenden", null, (_, _) => Beenden());

        _symbol = new NotifyIcon
        {
            Icon = _iconWarten,
            Text = "AutoPointer-Vergleich",
            ContextMenuStrip = menue,
            Visible = true,
        };
        _symbol.DoubleClick += (_, _) => FensterZeigen();

        _hotkey = new HotkeyFenster(() => AutomatikUmschalten());
        HotkeyAnwenden();
        AutomatikAnzeigen();

        _fenster = new SteuerFenster(ZustandFuersFenster);
        _fenster.Aktivieren += () => AutomatikSetzen(true);
        _fenster.Stoppen += () => AutomatikSetzen(false);
        _fenster.JetztVergleichen += VergleichenStarten;
        _fenster.LetztenOeffnen += () => _ueberwacher?.LetztenErneutOeffnen();
        _fenster.VertragOeffnen += async () => await VertragOeffnenAsync();
        _fenster.Verbinden += () => VerbindenZeigen(null);
        _fenster.Trennen += async () => await TrennenAsync();
        _fenster.EinstellungenOeffnen += EinstellungenZeigen;
        _fenster.SystemcheckOeffnen += async () => await SystemcheckZeigenAsync();
        _fenster.Beenden += Beenden;
        _leiste = new Leiste(ZustandFuersFenster, () => _quelle?.Hauptfenster ?? IntPtr.Zero);
        _leiste.Aktivieren += () => AutomatikSetzen(true);
        _leiste.Stoppen += () => AutomatikSetzen(false);
        _leiste.JetztVergleichen += VergleichenStarten;
        _leiste.VertragOeffnen += async () => await VertragOeffnenAsync();
        _leiste.FensterOeffnen += FensterZeigen;
        _leiste.EckeGewechselt += ecke => { _einstellungen.LeisteEcke = ecke; Speichern(_einstellungen); };
        _leiste.PositionGeaendert += stelle =>
        {
            _einstellungen.LeisteX = stelle?.X;
            _einstellungen.LeisteY = stelle?.Y;
            Speichern(_einstellungen);
        };
        _leiste.Ausblenden += () => { _einstellungen.LeisteAnzeigen = false; Speichern(_einstellungen); LeisteAnwenden(); FensterZeigen(); };
        _leiste.Beenden += Beenden;
        AutoPointerFenster.EigeneFenster = () => new[] { _leistenHandle };
        // Nr. 10: liegt die Leiste ueber der AutoPointer-Tabelle, wird sie fuer das Bildschirm-Abbild kurz unsichtbar
        AutoPointerFenster.EigeneAusblenden = unsichtbar => _ui.Send(_ =>
        {
            if (!_leiste.IsDisposed && _leiste.Visible) _leiste.Opacity = unsichtbar ? 0 : 1;
        }, null);
        LeisteAnwenden();
        // Mit Leiste startet nur die Leiste (das grosse Fenster per Klick auf ☰); ohne Leiste das Fenster
        if (!_einstellungen.LeisteAnzeigen)
        {
            if (minimiert) _fenster.WindowState = FormWindowState.Minimized;   // Start mit Windows: nur in der Taskleiste
            _fenster.Show();
        }

        Protokoll.KlartextUmstellen();     // alte Klartext-Protokolle verschluesseln (03.10.2026)
        Protokoll.AufraeumenBeiTageswechsel();
        Protokoll.Schreibe($"AutoPointer-Vergleich {Application.ProductVersion} gestartet{(probelauf ? " – PROBELAUF (öffnet keinen Browser)" : "")}."
                           + $" Server: {_einstellungen.Server}" + (_einstellungen.ServerNurImSpeicher ? " (nur für diesen Lauf, --server)" : ""));
        VerbindungAnzeigen();
        Starten();
        // Lizenz beim Start pruefen bzw. zum Verbinden auffordern (erst, wenn die Nachrichtenschleife laeuft)
        _naechsteLizenz = Environment.TickCount64 + LizenzTakt(TimeSpan.FromMinutes(15));
        _ui.Post(async _ => await LizenzPruefenAsync(beimStart: true), null);
        var token = _ende.Token;
        _zeigenSignal = new EventWaitHandle(false, EventResetMode.AutoReset, ZeigenSignalName);
        Task.Run(() =>
        {
            var warten = new[] { _zeigenSignal, token.WaitHandle };
            while (WaitHandle.WaitAny(warten) == 0) _ui.Post(_ => FensterZeigen(), null);
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
        if (!_fenster.IsDisposed) _fenster.Zeigen();
    }

    /// <summary>Wunsch Ahmad 03.10.2026: kleine Leiste immer im Vordergrund, unten links oder rechts.</summary>
    private void LeisteAnwenden()
    {
        bool an = _einstellungen.LeisteAnzeigen;
        _leisteMenue.Checked = an;
        _fenster.NurAusblenden(an);
        if (_leiste.IsDisposed) return;
        if (an)
        {
            _leiste.EckeSetzen(_einstellungen.LeisteEcke);
            _leiste.PositionSetzen(_einstellungen.LeisteX is int x && _einstellungen.LeisteY is int y ? new Point(x, y) : null);
            if (!_leiste.Visible) _leiste.Show();
            _leistenHandle = _leiste.Handle;
        }
        else
        {
            _leiste.Hide();
            _leistenHandle = IntPtr.Zero;
        }
    }

    private void LeisteUmschalten()
    {
        _einstellungen.LeisteAnzeigen = !_einstellungen.LeisteAnzeigen;
        Speichern(_einstellungen);
        LeisteAnwenden();
        if (!_einstellungen.LeisteAnzeigen) FensterZeigen();
    }

    private FensterZustand ZustandFuersFenster()
    {
        var f = _ueberwacher?.LetztesFahrzeug;
        string? auto = f == null ? null
            : $"{(f.Marke != null ? $"{f.Marke} {f.Modell}".Trim() : f.MarkeModellText)} · EZ {f.EzText}"
              + (f.Kilometer != null ? $" · {f.Kilometer:N0} km" : "");
        return new FensterZustand(AktuellerStatus(), _einstellungen.AutomatikAktiv,
            _dienst.Verbunden, _einstellungen.VerbundenAls ?? "", auto,
            !string.IsNullOrEmpty(_ueberwacher?.LetzteInseratUrl),
            _ueberwacher?.VertragBereit == true,
            _letzteMeldung, _probelauf, _lizenzSperre);
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
        _ = Task.Run(async () =>
        {
            try { await u.JetztVergleichenAsync(); }
            catch (Exception ex)
            {
                Protokoll.Schreibe("„Vergleichen“ fehlgeschlagen: " + ex);
                _ui.Post(_ => Sprechblase("Vergleichen fehlgeschlagen: " + ex.Message, true, erzwingen: true), null);
            }
        });
    }

    /// <summary>Wunsch Ahmad 03.10.2026: das zuletzt angeklickte Auto in AutoSchnell oeffnen — der Server hat
    /// es schon ausgelesen, der Vergleich steht sofort mit Fotos da, "Kaufvertrag erstellen" ohne Link-Einfuegen.
    /// Paket 3 (F3): die Suche nach der App-Verknuepfung laeuft im Hintergrund (vorher fror die Oberflaeche bei jedem
    /// Klick, solange alle Verknuepfungen per COM gelesen wurden).</summary>
    private bool _vertragLaeuft;

    private async Task VertragOeffnenAsync()
    {
        if (_vertragLaeuft) return;
        _vertragLaeuft = true;
        try { await VertragOeffnenInternAsync(); }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Kaufvertrag nicht geöffnet: " + ex);
            Sprechblase("Kaufvertrag nicht geöffnet: " + ex.Message, true, erzwingen: true);
        }
        finally { _vertragLaeuft = false; }
    }

    private async Task VertragOeffnenInternAsync()
    {
        var u = _ueberwacher;
        if (u == null || !u.VertragBereit)
        {
            Sprechblase("Das aktuelle Fahrzeug wird noch geprüft – der Kaufvertrag wird erst freigegeben, wenn Fahrzeug und Inserat sicher zusammengehören.",
                        false, erzwingen: true);
            return;
        }
        string? url = u.LetzteInseratUrl;
        var fahrzeug = u.LetztesFahrzeug;
        if (string.IsNullOrEmpty(url) && fahrzeug != null)
        {
            // Nr. 11: AutoScout zeigt die Kennung abgeschnitten — hat der Sucher die Adresse seit dem Anklicken
            // kopiert ("Seite öffnen", Strg+L, Strg+C), nimmt das Programm sie.
            url = InseratAusZwischenablage(fahrzeug);
            if (url != null) Protokoll.Schreibe("Kaufvertrag: Inserat-Adresse aus der Zwischenablage übernommen: " + url);
        }
        if (string.IsNullOrEmpty(url))
        {
            Sprechblase(fahrzeug == null
                ? "Noch kein Auto verglichen – erst in AutoPointer ein Inserat anklicken."
                : Ueberwacher.KeinLinkHinweis, true, erzwingen: true);
            return;
        }
        // Nr. 12: Kennung des Starts — die App meldet sie beim Uebernehmen an AutoSchnell zurueck
        string start = Guid.NewGuid().ToString("N");
        string ziel = $"{_einstellungen.Server}/app/vergleich?url={Uri.EscapeDataString(url)}&start={start}";
        Protokoll.Schreibe("Kaufvertrag: öffne " + ziel);
        // Wunsch Ahmad 03.10.2026: zuerst die installierte AutoSchnell-App (offenes Fenster oder neu starten),
        // nur ohne App im Browser
        if (await AutoSchnellApp.OeffnenAsync(ziel, _einstellungen.Server))
        {
            _ = AppStartPruefenAsync(start, ziel);
            return;
        }
        ImBrowserOeffnen(ziel);
    }

    private void ImBrowserOeffnen(string ziel)
    {
        try { BrowserOeffner.Oeffne(new[] { ziel }, _einstellungen.Browser); }
        catch (Exception ex) { Sprechblase("Browser konnte nicht geöffnet werden: " + ex.Message, true, erzwingen: true); }
    }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 12): Kommt von der App binnen 10 Sekunden keine Rueckmeldung (Fenster
    /// nicht nach vorne gekommen, falsche App, nicht angemeldet), oeffnet das Programm den Kaufvertrag im Browser.</summary>
    private async Task AppStartPruefenAsync(string start, string ziel)
    {
        if (!_dienst.Verbunden) return;
        long bis = Environment.TickCount64 + 10_000;     // A10: monoton
        while (Environment.TickCount64 < bis)
        {
            await Task.Delay(700);
            if (await _dienst.AppStartBestaetigtAsync(start))
            {
                Protokoll.Schreibe("AutoSchnell-App hat das Auto übernommen.");
                return;
            }
        }
        Protokoll.Schreibe("AutoSchnell-App hat sich nicht gemeldet – Kaufvertrag im Browser geöffnet.");
        Sprechblase("Die AutoSchnell-App hat nicht reagiert – der Kaufvertrag ist im Browser geöffnet.", false, erzwingen: true);
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
        bool verbunden = _dienst.Verbunden;
        _verbindungsZeile.Text = verbunden ? $"Verbunden: {_einstellungen.VerbundenAls}" : "Nicht mit AutoSchnell verbunden";
        _trennen.Enabled = verbunden;
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
            if (beimStart) VerbindenZeigen(null);
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
            // Pruefbericht 03.10.2026 (Nr. 4): AutoSchnell bietet eine neuere Version an -> einmal je Programmstart sagen
            string eigene = Application.ProductVersion.Split('+')[0];
            if (!_updateGemeldet && AutoSchnellDienst.NeuereVersion(s.AktuelleVersion, eigene))
            {
                _updateGemeldet = true;
                Protokoll.Schreibe($"Neue Version {s.AktuelleVersion} verfügbar (installiert: {eigene}).");
                Sprechblase($"Neue Version {s.AktuelleVersion} verfügbar – in AutoSchnell unter „{s.ProgrammName ?? "Programme"}“ "
                            + "herunterladen und starten (installiert: " + eigene + ").", false, erzwingen: true);
            }
            VerbindungAnzeigen();
            StatusAnzeigen(AktuellerStatus());
        }
        catch (DienstFehler ex)
        {
            Protokoll.Schreibe("Lizenzprüfung: " + ex.Message);
            if (ex.NichtVerbunden) VerbindungVerloren(ex.Message);
            else if (ex.KeinAbo || ex.Status == 403)
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

    private void LizenzSperreSetzen(string? grund)
    {
        if (_lizenzSperre == grund) return;
        _lizenzSperre = grund;
        if (_ueberwacher != null) _ueberwacher.LizenzGesperrt = grund != null;
        Protokoll.Schreibe(grund != null ? "Gesperrt (Lizenzprüfung): " + grund : "Sperre aufgehoben (Lizenzprüfung ok).");
        _fenster.Aktualisieren();
    }

    private void VerbindungVerloren(string meldung)
    {
        _einstellungen.SchluesselSetzen(null, null);
        Speichern(_einstellungen);
        LizenzSperreSetzen(null);
        VerbindungAnzeigen();
        StatusAnzeigen(Status.NichtVerbunden);
        VerbindenZeigen(meldung);
    }

    private void VerbindenZeigen(string? hinweis)
    {
        if (_verbindenForm is { IsDisposed: false }) { _verbindenForm.Activate(); return; }
        using var f = new VerbindenForm(_dienst, hinweis);
        _verbindenForm = f;
        try
        {
            if (f.ShowDialog() != DialogResult.OK || f.Ergebnis == null) return;
            var r = f.Ergebnis;
            string als = AutoSchnellDienst.KontoText(r.Name, r.Konto, r.Firma);
            _einstellungen.SchluesselSetzen(r.Schluessel, als);
            Speichern(_einstellungen);
            Protokoll.Schreibe($"Mit AutoSchnell verbunden: {als}");
            _lizenzFehler = 0;
            LizenzSperreSetzen(null);           // der Code gibt es nur mit aktivem Abo
            VerbindungAnzeigen();
            _ueberwacher?.NachVerbinden();      // das gerade angezeigte Auto gleich vergleichen
            StatusAnzeigen(AktuellerStatus());
            Sprechblase($"Verbunden als {als}. Klick in AutoPointer ein Inserat an – die Vergleiche öffnen sich automatisch.", false, erzwingen: true);
        }
        finally { _verbindenForm = null; }
    }

    private async Task TrennenAsync()
    {
        if (MessageBox.Show("Verbindung zu AutoSchnell trennen? Danach öffnet das Programm keine Vergleiche mehr, "
                            + "bis es wieder mit einem Code verbunden wird.", "AutoPointer-Vergleich",
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
                Sprechblase(fehler, true, erzwingen: true);
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
        var quelle = new AutoPointerQuelle(ocr, () => _einstellungen);
        _quelle = quelle;
        _klicks = new Klicks(() => quelle.Hauptfenster);
        var klicks = _klicks;
        _ueberwacher = new Ueberwacher(quelle, () => _einstellungen, new BrowserAusgabe(_ui),
                                       new DienstVermittler(() => _dienst),
                                       letzterKlick: () => klicks.LetzterKlick) { Probelauf = _probelauf, LizenzGesperrt = _lizenzSperre != null };
        _ueberwacher.StatusGeaendert += s => _ui.Post(_ => StatusAnzeigen(AktuellerStatus()), null);
        _ueberwacher.Meldung += (t, f) => _ui.Post(_ => Sprechblase(t, f), null);
        _ueberwacher.VerbindungVerloren += m => _ui.Post(_ => VerbindungVerloren(m), null);
        _ueberwacher.FahrzeugGewechselt += _ => _zwischenablageStand = Native.GetClipboardSequenceNumber();
        _ueberwacher.Neustart();

        var token = _ende.Token;
        Task.Run(async () =>
        {
            while (!token.IsCancellationRequested)
            {
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

    private void AutomatikUmschalten() => AutomatikSetzen(!_einstellungen.AutomatikAktiv);

    private void AutomatikSetzen(bool an)
    {
        if (_einstellungen.AutomatikAktiv == an) { _fenster.Aktualisieren(); return; }
        _einstellungen.AutomatikAktiv = an;
        Speichern(_einstellungen);
        if (_einstellungen.AutomatikAktiv) _ueberwacher?.Neustart();
        AutomatikAnzeigen();
        Protokoll.Schreibe(_einstellungen.AutomatikAktiv ? "Automatik AN." : "Automatik AUS.");
        Sprechblase(_einstellungen.AutomatikAktiv ? "Automatik AN – Vergleiche öffnen sich beim Anklicken." : "Automatik AUS – es öffnet sich nichts.", false, erzwingen: true);
        _fenster.Aktualisieren();
    }

    private void AutomatikAnzeigen()
    {
        _automatik.Checked = _einstellungen.AutomatikAktiv;
        _automatik.Text = "Automatik aktiv" + (_einstellungen.TastenkuerzelAktiv ? "\tStrg+Alt+P" : "");
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
        string zeile = $"AutoPointer-Vergleich{(_probelauf ? " (Probelauf)" : "")}: {text}";
        if (letztes != null) zeile += $"\nZuletzt: {letztes.Marke} {letztes.Modell} {letztes.EzText}";
        _symbol.Text = zeile.Length > 127 ? zeile[..127] : zeile;
    }

    private void Sprechblase(string text, bool fehler, bool erzwingen = false)
    {
        _letzteMeldung = $"{DateTime.Now:HH:mm} {text}";      // bleibt im Fenster stehen, die Sprechblase verschwindet
        StatusAnzeigen(AktuellerStatus());
        if (!erzwingen && !_einstellungen.HinweiseAnzeigen) return;
        if (!erzwingen && Environment.TickCount64 - _letzteSprechblase < 3000) return;    // A10: monoton
        _letzteSprechblase = Environment.TickCount64;
        _symbol.ShowBalloonTip(4000, "AutoPointer-Vergleich", text, fehler ? ToolTipIcon.Warning : ToolTipIcon.Info);
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
            // waehrend des offenen Dialogs geaendert haben (Lizenzpruefung, 401)
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
            Protokoll.Schreibe("Einstellungen nicht gespeichert: " + ex.Message);
            Sprechblase("Einstellungen nicht gespeichert: " + ex.Message, true, erzwingen: true);
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
            Sprechblase("Systemcheck läuft …", false, erzwingen: true);
            var punkte = await Systemcheck.PruefenAsync(_einstellungen, _dienst);
            string text = Systemcheck.Text(punkte);
            Protokoll.Schreibe("Systemcheck:\n" + text);
            bool fehler = punkte.Any(p => p.Stufe == PruefStufe.Fehler);
            MessageBox.Show(text + "\n\n(Strg+C kopiert diesen Text.)",
                fehler ? "Systemcheck – bitte beheben" : "Systemcheck – alles bereit",
                MessageBoxButtons.OK, fehler ? MessageBoxIcon.Warning : MessageBoxIcon.Information);
        }
        catch (Exception ex) { Protokoll.Schreibe("Systemcheck fehlgeschlagen: " + ex.Message); }
        finally { _systemcheckLaeuft = false; }
    }

    private void Beenden()
    {
        Protokoll.Schreibe("Beendet.");
        SystemEvents.PowerModeChanged -= PowerModeGeaendert;
        NetworkChange.NetworkAvailabilityChanged -= NetzGeaendert;
        _fenster.EndgueltigSchliessen();
        _leistenHandle = IntPtr.Zero;
        _leiste.EndgueltigSchliessen();
        _ende.Cancel();
        _klicks?.Dispose();
        _hotkey.Abmelden(HotkeyId);
        _hotkey.DestroyHandle();
        _symbol.Visible = false;
        _symbol.Dispose();
        ExitThread();
    }

    /// <summary>Unsichtbares Fenster, das die globale Tastenkombination empfaengt.</summary>
    private sealed class HotkeyFenster : NativeWindow
    {
        private readonly Action _aktion;
        private readonly HashSet<int> _angemeldet = new();

        public HotkeyFenster(Action aktion)
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
            if (m.Msg == Native.WM_HOTKEY) _aktion();
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
}
