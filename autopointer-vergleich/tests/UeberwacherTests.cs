using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

/// <summary>Ablauf: Wartezeit, kein Doppel-Oeffnen, schnelles Wechseln, Mindestabstand.</summary>
[Collection("Protokolldateien")]   // setzen Protokoll.DateiAktiv — nicht parallel zu TresorTests
public class UeberwacherTests
{
    private sealed class Attrappe : IAnsichtQuelle
    {
        public Lage Lage = Lage.KeineDetails;
        public ulong Summe;
        public Func<Fahrzeug>? Fahrzeug;
        public Action? WaehrendDesLesens;
        public int Lesungen;
        public IntPtr Hauptfenster => IntPtr.Zero;
        public QuellenZustand Pruefe() => new(Lage, Lage == Lage.Details ? Summe : 0);

        public Task<Lesung?> LiesAsync()
        {
            Lesungen++;
            var f = Fahrzeug!();
            WaehrendDesLesens?.Invoke();
            return Task.FromResult<Lesung?>(new Lesung(f, f.MarkeModellText.Length == 0 && f.Kilometer == null, "roh"));
        }

        public void Zeige(Func<Fahrzeug> f, ulong summe)
        {
            Lage = Lage.Details;
            Fahrzeug = f;
            Summe = summe;
        }
    }

    /// <summary>Server-Attrappe: Link je Fahrzeug, Fehler auf Wunsch.</summary>
    private sealed class Server : IVergleichsDienst
    {
        public bool Verbunden { get; set; } = true;
        public DienstFehler? Fehler;
        public string? InseratUrl;
        public bool InseratImBrowser;
        public List<string>? Melden;
        /// <summary>1.5.8: wie der echte Server — nur die Links der in AutoSchnell gewaehlten Portale (null = beide).</summary>
        public string[]? NurPortale;
        /// <summary>1.5.8: Konto mit Browser-Erweiterung — der Server sagt ueber_helfer + Vorgangsnummer.</summary>
        public bool UeberHelfer;
        /// <summary>1.5.9: in welchem Browser die Erweiterung verbunden ist ("chrome", "edge", "").</summary>
        public string HelferBrowser = "";
        /// <summary>1.5.9: Antwort auf POST …/selbst — true = das Programm oeffnet, false = die Erweiterung hat ihn,
        /// null = nicht erreichbar. <see cref="SelbstAntwort"/> geht vor (Antwort spaeter, je Vorgang).</summary>
        public bool? Selbst = false;
        public Func<string, Task<bool?>>? SelbstAntwort;
        public readonly List<string> Nachgefragt = new();
        /// <summary>Erster Vorgang; jeder weitere bekommt eine eigene Nummer (wie beim echten Server).</summary>
        public const string Vorgang = "11111111-2222-3333-4444-555555555555";
        public readonly List<string> Vorgaenge = new();
        public Task<bool?> VorgangSelbstAsync(string vorgangId)
        {
            Nachgefragt.Add(vorgangId);
            return SelbstAntwort?.Invoke(vorgangId) ?? Task.FromResult(Selbst);
        }
        public readonly List<Fahrzeug> Anfragen = new();
        public int Vorgewaermt;
        public void Vorwaermen() => Vorgewaermt++;

        public Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf)
        {
            Anfragen.Add(f);
            if (Fehler != null) return Task.FromException<VergleichAntwort>(Fehler);
            int leer = f.MarkeModellText.IndexOf(' ');
            string id = (leer > 0 ? $"{f.MarkeModellText[..leer]}-{f.MarkeModellText[(leer + 1)..]}" : f.MarkeModellText)
                        .Replace(' ', '_') + $"-{f.EzJahr}";
            // wie der echte Server seit 1.4.0: unbekannte Marke -> keine Links, erkannt=false
            if (f.MarkeModellText.StartsWith("Quatsch", StringComparison.Ordinal))
                return Task.FromResult(new VergleichAntwort(Array.Empty<Vergleich>(),
                    new[] { "mobile.de kennt die Marke „Quatschmarke“ nicht – kein mobile.de-Vergleich." }, "inland",
                    InseratUrl, "kein_link", ErkanntMarke: f.MarkeModellText, ErkanntModell: "", MarkeErkannt: false));
            string? vorgang = null;
            if (UeberHelfer)
            {
                vorgang = Vorgaenge.Count == 0 ? Vorgang : $"11111111-2222-3333-4444-{Vorgaenge.Count:D12}";
                Vorgaenge.Add(vorgang);
            }
            return Task.FromResult(new VergleichAntwort(new[]
            {
                new Vergleich("mobile.de", $"https://suchen.mobile.de/{id}"),
                new Vergleich("AutoScout24", $"https://www.autoscout24.de/{id}"),
            }.Where(v => NurPortale == null || NurPortale.Contains(v.Portal)).ToArray(), Array.Empty<string>(), "inland", InseratUrl, InseratImBrowser ? "browser" : InseratUrl != null ? "laeuft" : "kein_link",
               ErkanntMarke: "Erkannt", ErkanntModell: f.MarkeModellText, Melden: Melden,
               InseratImBrowser: InseratImBrowser && InseratUrl != null,
               VorgangId: vorgang, UeberHelfer: UeberHelfer, HelferBrowser: HelferBrowser));
        }
    }

    private sealed class Browser : IOeffner
    {
        public readonly List<IReadOnlyList<Vergleich>> Aufrufe = new();
        /// <summary>1.5.9: in welchem Browser jeder Aufruf geoeffnet wurde.</summary>
        public readonly List<BrowserWahl> Wahl = new();
        public void Oeffne(IReadOnlyList<Vergleich> v, Einstellungen e, IntPtr ap, BrowserWahl browser)
        {
            Aufrufe.Add(v);
            Wahl.Add(browser);
        }
    }

    private readonly Attrappe _q = new();
    private readonly Browser _b = new();
    private readonly Server _server = new();
    private readonly List<string> _verloren = new();
    private readonly Einstellungen _e = new();
    /// <summary>Text und (falls vorhanden) die ganze Erklaerung je Meldung — die Pruefungen suchen in beidem.</summary>
    private readonly List<string> _meldungen = new();
    private readonly List<Hinweis> _hinweise = new();
    private DateTime _jetzt = new(2026, 10, 3, 12, 0, 0);
    private readonly List<TimeSpan> _gewartet = new();
    private readonly Ueberwacher _u;

    public UeberwacherTests()
    {
        Protokoll.DateiAktiv = false;
        _u = new Ueberwacher(_q, () => _e, _b, _server, () => _jetzt, t =>
        {
            _gewartet.Add(t);
            _jetzt += t;
            return Task.CompletedTask;
        });
        _u.Meldung += Gemeldet;
        _u.VerbindungVerloren += m => _verloren.Add(m);
        _u.Neustart();
    }

    private void Gemeldet(Hinweis h)
    {
        // 1.5.9 (C): keine Sprechblase ueber 150 Zeichen — Windows schneidet sonst ab
        Assert.True(h.Text.Length <= Hinweis.MaxZeichen, $"{h.Text.Length} Zeichen: {h.Text}");
        _hinweise.Add(h);
        _meldungen.Add(h.Ausfuehrlich != null ? h.Text + "\n" + h.Ausfuehrlich : h.Text);
    }

    private async Task Tick(int ms = 250)
    {
        await _u.TickAsync();
        _jetzt = _jetzt.AddMilliseconds(ms);
    }

    /// <summary>Fahrzeug anzeigen und so lange ticken, bis die Wartezeit um ist.</summary>
    private async Task Anklicken(Func<Fahrzeug> f, ulong summe)
    {
        _q.Zeige(f, summe);
        for (int i = 0; i < 4; i++) await Tick();
    }

    private static Fahrzeug Golf() => DetailLeser.Auswerten(new[]
    {
        Z("Marke, Modell:", 12, 4), Z("VW Golf", 212, 4),
        Z("Erstzulassung:", 12, 26), Z("05/2019", 212, 26),
        Z("Kilometerstand:", 12, 48), Z("61.000 km", 212, 48),
        Z("Leistung:", 12, 70), Z("110 kW (150 PS)", 212, 70),
    }, Array.Empty<OcrZeile>(), 0);

    private async Task Start()
    {
        await Tick();          // AutoPointer zeigt noch nichts -> kein Startzustand
    }

    [Fact]
    public async Task Anklicken_oeffnet_beide_Vergleiche_erst_nach_der_Wartezeit()
    {
        await Start();
        _q.Zeige(Bentley, 100);
        await Tick(100);   // Aenderung bemerkt
        await Tick(100);   // 100 ms stabil - noch zu frueh (Wartezeit 400)
        Assert.Empty(_b.Aufrufe);
        await Tick(250);
        await Tick(250);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(new[] { "mobile.de", "AutoScout24" }, _b.Aufrufe[0].Select(v => v.Portal));
    }

    [Fact]
    public async Task Gleiches_Fahrzeug_oeffnet_nicht_erneut_auch_wenn_sich_die_Anzeige_aendert()
    {
        await Start();
        await Anklicken(Bentley, 100);
        await Anklicken(Bentley, 101);   // z. B. "Neu"-Markierung weg, Bild nachgeladen
        await Anklicken(Bentley, 102);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Schnelles_Wechseln_A_B_C_jeweils_eigener_Vergleich()
    {
        await Start();
        await Anklicken(Bentley, 1);
        await Anklicken(Passat, 2);
        await Anklicken(Golf, 3);
        Assert.Equal(3, _b.Aufrufe.Count);
        Assert.Contains("Bentley-Bentayga-2017", _b.Aufrufe[0][0].Url);
        Assert.Contains("VW-Passat_Variant-2006", _b.Aufrufe[1][0].Url);
        Assert.Contains("VW-Golf-2019", _b.Aufrufe[2][0].Url);
    }

    [Fact]
    public async Task Zwischenstand_kuerzer_als_Wartezeit_wird_uebersprungen()
    {
        await Start();
        _q.Zeige(Bentley, 1);
        await Tick(100);
        _q.Zeige(Passat, 2);          // nach 100 ms schon weiter
        await Tick(100);
        await Tick(250);
        await Tick(250);
        await Tick(250);
        Assert.Single(_b.Aufrufe);
        Assert.Contains("VW-Passat_Variant-2006", _b.Aufrufe[0][0].Url);
        Assert.Equal(1, _q.Lesungen);
    }

    [Fact]
    public async Task Aenderung_waehrend_des_Lesens_wird_verworfen_keine_Mischdaten()
    {
        await Start();
        _q.Zeige(Bentley, 1);
        _q.WaehrendDesLesens = () => { _q.Fahrzeug = Passat; _q.Summe = 2; _q.WaehrendDesLesens = null; };
        await Tick(250);
        await Tick(250);
        await Tick(250);   // liest Bentley, aber Anzeige ist inzwischen Passat -> verwerfen
        Assert.Empty(_b.Aufrufe);
        await Tick(250);
        await Tick(250);
        await Tick(250);
        Assert.Single(_b.Aufrufe);
        Assert.Contains("VW-Passat_Variant-2006", _b.Aufrufe[0][0].Url);
    }

    [Fact]
    public async Task Unvollstaendig_erkannt_oeffnet_nichts_und_meldet_einmal()
    {
        await Start();
        Fahrzeug OhneEz()
        {
            var f = Bentley();
            f.EzJahr = null;
            f.EzMonat = null;
            return f;
        }
        await Anklicken(OhneEz, 5);
        await Anklicken(OhneEz, 5);
        Assert.Empty(_b.Aufrufe);
        Assert.Single(_meldungen, m => m.StartsWith("Fahrzeug konnte nicht eindeutig erkannt werden"));
    }

    [Fact]
    public async Task Beim_Start_angezeigtes_Fahrzeug_oeffnet_nicht_das_naechste_schon()
    {
        _q.Zeige(Bentley, 1);       // schon da, bevor das Programm startet
        for (int i = 0; i < 5; i++) await Tick();
        Assert.Empty(_b.Aufrufe);
        Assert.NotNull(_u.LetztesFahrzeug);
        await Anklicken(Passat, 2);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]   // Befund 04.10.2026: Mercedes stand schon da, Programm neu verbunden -> nichts ging auf
    public async Task Nach_dem_Verbinden_wird_das_angezeigte_Auto_verglichen()
    {
        _q.Zeige(Bentley, 1);       // schon da, bevor das Programm startet: wird nicht geoeffnet
        for (int i = 0; i < 5; i++) await Tick();
        Assert.Empty(_b.Aufrufe);
        _u.NachVerbinden();         // Programm (neu) mit AutoSchnell verbunden
        for (int i = 0; i < 5; i++) await Tick();
        Assert.Single(_b.Aufrufe);
        Assert.Contains("Bentley", _b.Aufrufe[0][0].Url);
        _u.NachVerbinden();         // erneut verbunden: auch dasselbe Auto noch einmal
        for (int i = 0; i < 5; i++) await Tick();
        Assert.Equal(2, _b.Aufrufe.Count);
    }

    [Fact]   // Befund 04.10.2026: unplausible EZ/km meldet der Server — das Programm zeigt es sofort (nicht nur im Protokoll)
    public async Task Hinweise_des_Servers_werden_gezeigt()
    {
        await Start();
        _server.Melden = new() { "Erstzulassung 04/2026 passt nicht zu 165.000 km – ohne Erstzulassungs-Filter gesucht. Bitte prüfen." };
        await Anklicken(Golf, 7);
        Assert.Single(_b.Aufrufe);                         // Vergleich geht trotzdem auf
        Assert.Contains(_meldungen, m => m.Contains("passt nicht zu 165.000 km"));
    }

    [Fact]
    public async Task Automatik_aus_oeffnet_nichts()
    {
        await Start();
        _e.AutomatikAktiv = false;
        await Anklicken(Bentley, 1);
        Assert.Empty(_b.Aufrufe);
        Assert.Equal(Status.Pause, _u.Status);
        Assert.Equal(0, _q.Lesungen);
    }

    [Fact]
    public async Task Jetzt_vergleichen_oeffnet_auch_dasselbe_Fahrzeug()
    {
        await Start();
        await Anklicken(Bentley, 1);
        await _u.JetztVergleichenAsync();
        Assert.Equal(2, _b.Aufrufe.Count);
    }

    [Fact]
    public async Task Mindestabstand_zwischen_zwei_Vergleichen()
    {
        await Start();
        _e.WartezeitMs = 100;
        _e.MindestabstandMs = 2000;
        await Anklicken(Bentley, 1);
        await Anklicken(Passat, 2);
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Single(_gewartet);
        Assert.True(_gewartet[0] > TimeSpan.FromMilliseconds(500));
    }

    [Fact]
    public async Task Ohne_AutoPointer_kein_Lesen()
    {
        _q.Lage = Lage.KeinAutoPointer;
        await Tick();
        await Tick();
        Assert.Equal(Status.KeinAutoPointer, _u.Status);
        Assert.Equal(0, _q.Lesungen);
    }

    [Fact]
    public async Task Unbekannte_Marke_meldet_der_Server()
    {
        // seit 1.4.0 erkennt der Server Marke und Modell — er sagt auch, wenn er die Marke nicht kennt
        await Start();
        Fahrzeug Unbekannt()
        {
            var f = Bentley();
            f.MarkeModellText = "Quatschmarke X1";
            f.Titel = "Quatschmarke X1 Sport";
            return f;
        }
        await Anklicken(Unbekannt, 9);
        Assert.Empty(_b.Aufrufe);
        Assert.Single(_server.Anfragen);
        Assert.Contains(_meldungen, m => m.Contains("nicht erkannt"));
    }

    [Fact]
    public async Task Erkannte_Namen_vom_Server_stehen_am_Fahrzeug()
    {
        await Start();
        await Anklicken(Passat, 10);
        Assert.Equal(("Erkannt", "VW Passat Variant"), (_u.LetztesFahrzeug!.Marke, _u.LetztesFahrzeug.Modell));
    }

    [Fact]
    public async Task Startzustand_fragt_den_Server_nicht()
    {
        _q.Zeige(Bentley, 1);
        for (int i = 0; i < 5; i++) await Tick();
        Assert.Empty(_server.Anfragen);
    }

    [Fact]
    public async Task Ohne_Abo_nichts_oeffnen_Meldung_und_gesperrt()
    {
        await Start();
        _server.Fehler = new DienstFehler(402, "Kein aktives AutoSchnell-Abo – das Programm ist gesperrt.");
        await Anklicken(Bentley, 1);
        Assert.Empty(_b.Aufrufe);
        Assert.Contains(_meldungen, m => m.Contains("Kein aktives AutoSchnell-Abo"));
        Assert.Equal(Status.Gesperrt, _u.Status);
        // Abo verlaengert: der naechste Vergleich klappt und entsperrt
        _server.Fehler = null;
        await Anklicken(Passat, 2);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(Status.Aktiv, _u.Status);
    }

    [Fact]
    public async Task Anderer_PC_verbunden_meldet_Verbindung_verloren()
    {
        await Start();
        _server.Fehler = new DienstFehler(401, "Dieses Programm ist nicht (mehr) verbunden");
        await Anklicken(Bentley, 1);
        Assert.Empty(_b.Aufrufe);
        Assert.Single(_verloren);
        Assert.Equal(Status.NichtVerbunden, _u.Status);
    }

    [Fact]
    public async Task Kein_Internet_dasselbe_Auto_spaeter_erneut_vergleichbar()
    {
        await Start();
        _server.Fehler = new DienstFehler(0, "Keine Verbindung zu AutoSchnell – bitte Internet prüfen.");
        await Anklicken(Bentley, 1);
        Assert.Empty(_b.Aufrufe);
        Assert.Contains(_meldungen, m => m.Contains("Keine Verbindung"));
        _server.Fehler = null;
        await _u.JetztVergleichenAsync();
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Nicht_verbunden_liest_gar_nicht()
    {
        _server.Verbunden = false;
        _q.Zeige(Bentley, 1);
        for (int i = 0; i < 4; i++) await Tick();
        Assert.Equal(0, _q.Lesungen);
        Assert.Equal(Status.NichtVerbunden, _u.Status);
    }

    [Fact]
    public async Task Kaufvertrag_Inserat_Link_wird_gemerkt()
    {
        await Start();
        _server.InseratUrl = "https://www.kleinanzeigen.de/s-anzeige/3529712138";
        await Anklicken(Passat, 1);
        Assert.Equal("https://www.kleinanzeigen.de/s-anzeige/3529712138", _u.LetzteInseratUrl);
        Assert.DoesNotContain(_meldungen, m => m.Contains("Hash-ID"));
    }

    [Fact]
    public async Task AutoScout_ohne_Hash_ID_sagt_Link_selbst_einfuegen()
    {
        await Start();
        Fahrzeug AutoScout()
        {
            var f = Bentley();
            f.Quelle = "AutoScout24";
            return f;
        }
        await Anklicken(AutoScout, 1);
        Assert.Single(_b.Aufrufe);                      // die Vergleiche kommen trotzdem
        Assert.Null(_u.LetzteInseratUrl);
        Assert.Contains(_meldungen, m => m.Contains("Hash-ID") && m.Contains("Adresse kopieren"));
    }

    [Fact]
    public async Task Lesefehler_wird_keine_Schleife_im_Takt()
    {
        // Pruefung 05.10.2026 (Paket 1): warf das Lesen eine Ausnahme (GDI, Texterkennung), blieb der Inhalt "offen"
        // und wurde alle 250 ms neu gelesen — Dauerlast und ein Protokoll, das um ~1 GB am Tag wuchs.
        await Start();
        _q.Zeige(() => throw new InvalidOperationException("GDI+ generic error"), 1);
        for (int i = 0; i < 200; i++) await Tick();               // 50 Sekunden
        Assert.Equal(Ueberwacher.LeseVersuche, _q.Lesungen);      // 3 Versuche (2/4/6 s Abstand), dann Ruhe
        Assert.Single(_meldungen, m => m.Contains("konnte nicht gelesen werden"));
        Assert.Empty(_b.Aufrufe);
        // naechstes Auto: alles wie gewohnt
        await Anklicken(Bentley, 2);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Doppelklick_auf_Vergleichen_oeffnet_nur_einmal()
    {
        // Pruefung 05.10.2026 (Paket 1): der zweite Klick stellte sich an der Sperre an und oeffnete dieselben
        // Vergleiche noch einmal ("erzwungen" uebergeht die Pruefung "gleiches Auto" absichtlich)
        await Start();
        _q.Zeige(Bentley, 1);
        Task? zweiter = null;
        _q.WaehrendDesLesens = () => { zweiter ??= _u.JetztVergleichenAsync(); };
        await _u.JetztVergleichenAsync();
        await zweiter!;
        Assert.Single(_b.Aufrufe);
        Assert.Equal(1, _q.Lesungen);
        // "Vergleichen" nach dem Lesen oeffnet weiter auch dasselbe Auto (gewollt: Tabs versehentlich geschlossen)
        _q.WaehrendDesLesens = null;
        await _u.JetztVergleichenAsync();
        Assert.Equal(2, _b.Aufrufe.Count);
    }

    [Fact]
    public async Task Inserat_wird_als_Tab_mitgeoeffnet_wenn_der_Helfer_es_liest()
    {
        // Wunsch Ahmad 07.10.2026: Konto hat den Browser-Helfer -> Server sagt inserat_im_browser, wir oeffnen das
        // Inserat als Tab mit; der Helfer liest es dort (kein Apify). 1.5.10 (Befund Ahmad 08.10. abends): das Inserat
        // ZUERST — der letzte Tab liegt vorne, und das soll ein Vergleich sein, nicht das Inserat
        await Start();
        _server.InseratUrl = "https://www.kleinanzeigen.de/s-anzeige/3529712138";
        _server.InseratImBrowser = true;
        await Anklicken(Passat, 1);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(new[] { "Inserat", "mobile.de", "AutoScout24" }, _b.Aufrufe[0].Select(v => v.Portal));
        Assert.Equal(_server.InseratUrl, _b.Aufrufe[0][0].Url);
        Assert.Equal(_server.InseratUrl, _u.LetzteInseratUrl);
        // ohne Helfer (Server sagt es nicht): wie bisher nur die Vergleiche
        _server.InseratImBrowser = false;
        await Anklicken(Bentley, 2);
        Assert.Equal(new[] { "mobile.de", "AutoScout24" }, _b.Aufrufe[1].Select(v => v.Portal));
    }

    [Fact]   // 1.5.10 (Befund Ahmad 08.10.2026 abends): ohne Vorgangsseite in den Browser der Erweiterung
    public async Task Direkter_Weg_oeffnet_im_Browser_der_Erweiterung()
    {
        await Start();
        _server.UeberHelfer = false;
        _server.HelferBrowser = "chrome";
        await Anklicken(Passat, 1);
        Assert.Equal(BrowserWahl.Chrome, _b.Wahl[0]);
        _u.LetztenErneutOeffnen();
        Assert.Equal(BrowserWahl.Chrome, _b.Wahl[1]);
        // ohne Erweiterung (kein Browser gemeldet): wie eingestellt
        _server.HelferBrowser = "";
        await Anklicken(Bentley, 2);
        Assert.Equal(BrowserWahl.Standard, _b.Wahl[2]);
    }

    [Fact]   // Befund Ahmad 08.10.2026: mobile.de/Kleinanzeigen ohne lesbare Inserat-ID -> gleich sagen, wie es geht
    public async Task Ohne_Inserat_ID_sagt_das_Programm_wie_es_geht()
    {
        await Start();
        await Anklicken(() => { var f = Bentley(); f.Quelle = "mobile.de"; f.InseratId = null; return f; }, 1);
        Assert.Contains(_meldungen, m => m.Contains("Zeile „Inserat-ID“"));
        Assert.Contains("Zeile „Inserat-ID“", Ueberwacher.LinkHinweisFuer(new Fahrzeug { Quelle = "Kleinanzeigen" }));
        Assert.Contains("Hash-ID", Ueberwacher.LinkHinweisFuer(new Fahrzeug { Quelle = "AutoScout24" }));
    }

    private const string MobileInserat = "https://suchen.mobile.de/fahrzeuge/details.html?id=440123456";

    [Fact]   // 1.5.8 (Vorgangsnummer): mit Erweiterung oeffnet das Programm nur die Vorgangsseite
    public async Task Mit_Erweiterung_oeffnet_das_Programm_nur_die_Vorgangsseite()
    {
        await Start();
        _server.UeberHelfer = true;
        _server.InseratUrl = MobileInserat;                          // sonst kaeme der Inserat-ID-Hinweis dazu
        _server.Selbst = false;                                      // die Erweiterung hat den Vorgang schon
        await Anklicken(Bentley, 1);
        Assert.Single(_b.Aufrufe);
        var v = Assert.Single(_b.Aufrufe[0]);
        Assert.Equal("Vorgang", v.Portal);
        Assert.Equal($"https://app.auto-schnellkauf.de/app/vorgang/{Server.Vorgang}", v.Url);
        await _u.LetzteVorgangsPruefung!;
        Assert.Equal(new[] { Server.Vorgang }, _server.Nachgefragt);
        Assert.Contains(TimeSpan.FromMilliseconds(Ueberwacher.VorgangWarteMs), _gewartet);   // 1.5.9: 5 s Zeit
        Assert.Equal(5000, Ueberwacher.VorgangWarteMs);
        Assert.Single(_b.Aufrufe);                                   // uebernommen: nichts doppelt
        Assert.Empty(_meldungen);
        Assert.Equal(new[] { "mobile.de", "AutoScout24" }, _u.LetzteVergleiche.Select(x => x.Portal));
    }

    [Fact]   // Pruefung 08.10.2026 (1.5.9, A): "selbst: true" und noch das neueste Auto -> das Programm oeffnet selbst
    public async Task Selbst_und_noch_aktuell_dann_oeffnet_das_Programm_im_selben_Browser()
    {
        await Start();
        _server.UeberHelfer = true;
        _server.InseratUrl = MobileInserat;                          // sonst kaeme der Inserat-ID-Hinweis dazu
        _server.HelferBrowser = "edge";
        _server.Selbst = true;
        await Anklicken(Bentley, 1);
        await _u.LetzteVorgangsPruefung!;
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Equal("Vorgang", Assert.Single(_b.Aufrufe[0]).Portal);
        Assert.Equal(new[] { "mobile.de", "AutoScout24" }, _b.Aufrufe[1].Select(x => x.Portal));
        Assert.Equal(new[] { BrowserWahl.Edge, BrowserWahl.Edge }, _b.Wahl);   // dort, wo die Vorgangsseite aufging
        var h = Assert.Single(_hinweise);
        Assert.Equal(Ueberwacher.ErweiterungNichtUebernommen, h.Text);
        Assert.False(h.Fehler);
        // die 30 Minuten "gleich direkt" (1.5.8) gibt es nicht mehr: das naechste Auto geht wieder ueber den Vorgang
        await Anklicken(Golf, 2);
        await _u.LetzteVorgangsPruefung!;
        Assert.Equal("Vorgang", Assert.Single(_b.Aufrufe[2]).Portal);
        Assert.Equal(2, _server.Nachgefragt.Count);
    }

    [Fact]   // 1.5.9 (A): "selbst: true", aber inzwischen ist ein anderes Auto dran -> die alten Tabs gehen NICHT auf
    public async Task Selbst_aber_inzwischen_ein_neueres_Auto_dann_nichts()
    {
        await Start();
        _server.UeberHelfer = true;
        _server.InseratUrl = MobileInserat;                          // sonst kaeme der Inserat-ID-Hinweis dazu
        var offen = new Dictionary<string, TaskCompletionSource<bool?>>();
        _server.SelbstAntwort = id => (offen[id] = new TaskCompletionSource<bool?>()).Task;
        await Anklicken(Bentley, 1);
        var bentley = _u.LetzteVorgangsPruefung!;
        await Anklicken(Golf, 2);                                    // der Sucher klickt weiter, bevor die 5 s um sind
        var golf = _u.LetzteVorgangsPruefung!;
        Assert.Equal(2, _b.Aufrufe.Count);                           // zwei Vorgangsseiten
        Assert.Equal(2, _server.Nachgefragt.Count);                  // beide werden beansprucht (alte: spaete Erweiterung blockiert)
        offen[_server.Vorgaenge[0]].SetResult(true);
        await bentley;
        Assert.Equal(2, _b.Aufrufe.Count);                           // Bentley ist nicht mehr der neueste: nichts
        Assert.Empty(_meldungen);
        offen[_server.Vorgaenge[1]].SetResult(true);
        await golf;
        Assert.Equal(3, _b.Aufrufe.Count);                           // Golf schon: direkt geoeffnet
        Assert.Contains("VW-Golf-2019", _b.Aufrufe[2][0].Url);
    }

    [Fact]   // 1.5.9 (A): "selbst: false" -> die Erweiterung hat ihn; null (nicht erreichbar) -> NICHT oeffnen, kurzer Hinweis
    public async Task Selbst_false_nichts_und_nicht_erreichbar_kein_Oeffnen_aber_Hinweis()
    {
        await Start();
        _server.UeberHelfer = true;
        _server.InseratUrl = MobileInserat;                          // sonst kaeme der Inserat-ID-Hinweis dazu
        _server.Selbst = false;
        await Anklicken(Bentley, 1);
        await _u.LetzteVorgangsPruefung!;
        Assert.Single(_b.Aufrufe);
        Assert.Empty(_meldungen);

        _server.Selbst = null;                                       // auch der zweite Versuch kam nicht durch
        await Anklicken(Golf, 2);
        await _u.LetzteVorgangsPruefung!;
        Assert.Equal(2, _b.Aufrufe.Count);                           // nur die Vorgangsseite — sonst womoeglich doppelt
        var h = Assert.Single(_hinweise);
        Assert.Equal(Ueberwacher.VorgangNichtErreichbar, h.Text);
        Assert.Contains("„Vergleichen“", h.Text);
    }

    [Fact]   // 1.5.9 (A): die Vorgangsseite geht im Browser der Erweiterung auf, wenn "Standardbrowser" eingestellt ist
    public async Task Vorgangsseite_im_Browser_der_Erweiterung()
    {
        await Start();
        _server.UeberHelfer = true;
        _server.HelferBrowser = "chrome";
        await Anklicken(Bentley, 1);
        Assert.Equal(BrowserWahl.Standard, _e.Browser);
        Assert.Equal(BrowserWahl.Chrome, Assert.Single(_b.Wahl));
        // eine eigene Wahl (Edge) gilt immer
        _e.Browser = BrowserWahl.Edge;
        await Anklicken(Golf, 2);
        Assert.Equal(BrowserWahl.Edge, _b.Wahl[1]);
        // ohne Erweiterung (der Server meldet keinen Browser): wie eingestellt
        _e.Browser = BrowserWahl.Standard;
        _server.UeberHelfer = false;
        _server.HelferBrowser = "";
        await Anklicken(Passat, 3);
        Assert.Equal(BrowserWahl.Standard, _b.Wahl[2]);

        Assert.Equal(BrowserWahl.Chrome, Ueberwacher.BrowserFuer(BrowserWahl.Standard, "chrome"));
        Assert.Equal(BrowserWahl.Edge, Ueberwacher.BrowserFuer(BrowserWahl.Standard, "edge"));
        Assert.Equal(BrowserWahl.Standard, Ueberwacher.BrowserFuer(BrowserWahl.Standard, ""));
        Assert.Equal(BrowserWahl.Standard, Ueberwacher.BrowserFuer(BrowserWahl.Standard, null));
        Assert.Equal(BrowserWahl.Chrome, Ueberwacher.BrowserFuer(BrowserWahl.Chrome, "edge"));
    }

    [Fact]   // 1.5.8 (Wunsch Ahmad 08.10.2026): die Portalwahl steht in AutoSchnell, der Server schickt nur deren Links
    public async Task Nur_die_Portale_aus_AutoSchnell_oeffnen()
    {
        await Start();
        _server.NurPortale = new[] { "AutoScout24" };
        await Anklicken(Bentley, 1);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(new[] { "AutoScout24" }, _b.Aufrufe[0].Select(v => v.Portal));
    }

    [Fact]   // Pruefung 05.10.2026 (Paket 2, A11): NachVerbinden() kam vom Oberflaechen-Thread mitten ins Lesen
    public async Task Neuverbinden_waehrend_des_Lesens_verliert_das_Auto_nicht()
    {
        // vorher: die Felder wurden mitten im Lesen zurueckgesetzt (_summe = 0, _offen = false) — das gelesene Auto
        // galt als "waehrend des Lesens geaendert" und wurde verworfen, danach nie mehr gelesen (nichts mehr "offen")
        await Start();
        _q.Zeige(Bentley, 1);
        _q.WaehrendDesLesens = () => { _u.NachVerbinden(); _q.WaehrendDesLesens = null; };
        for (int i = 0; i < 8; i++) await Tick();
        // das laufende Lesen wird fertig und oeffnet; der Wunsch "NachVerbinden" gilt danach (gleiches Auto noch einmal)
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Equal(2, _q.Lesungen);
    }

    [Fact]   // A11: nach einer 401 reicht NachVerbinden() auch dann, wenn der Takt gerade frueh aussteigt
    public async Task Nach_401_und_Neuverbinden_laeuft_es_weiter()
    {
        await Start();
        _server.Fehler = new DienstFehler(401, "Dieses Programm ist nicht (mehr) verbunden");
        await Anklicken(Bentley, 1);
        Assert.Equal(Status.NichtVerbunden, _u.Status);
        for (int i = 0; i < 3; i++) await Tick();          // bleibt "nicht verbunden", liest nichts
        Assert.Equal(1, _q.Lesungen);
        _server.Fehler = null;
        _u.NachVerbinden();
        for (int i = 0; i < 6; i++) await Tick();
        Assert.Single(_b.Aufrufe);
        Assert.Equal(Status.Aktiv, _u.Status);
    }

    [Fact]   // Paket 2 (A8): /status meldete 402/403 — der Ueberwacher liest nichts, bis die Sperre weg ist
    public async Task Lizenzsperre_liest_nichts()
    {
        await Start();
        _u.LizenzGesperrt = true;
        await Anklicken(Bentley, 1);
        Assert.Equal(0, _q.Lesungen);
        Assert.Equal(Status.Gesperrt, _u.Status);
        Assert.Empty(_b.Aufrufe);
        _u.LizenzGesperrt = false;                       // Abo verlaengert: die naechste Lizenzpruefung hebt auf
        for (int i = 0; i < 5; i++) await Tick();
        Assert.Single(_b.Aufrufe);
        Assert.Equal(Status.Aktiv, _u.Status);
    }

    [Fact]   // Paket 2 (A10): ein Sprung der Uhr (Zeitabgleich) fuehrt nie zu einer Wartezeit ueber dem Mindestabstand
    public async Task Mindestabstand_nie_laenger_als_eingestellt_auch_bei_Uhrsprung()
    {
        await Start();
        _e.WartezeitMs = 100;
        _e.MindestabstandMs = 2000;
        await Anklicken(Bentley, 1);
        _jetzt = _jetzt.AddHours(-1);                    // Uhr zurueckgestellt
        await Anklicken(Passat, 2);
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Single(_gewartet);
        Assert.True(_gewartet[0] <= TimeSpan.FromMilliseconds(2000), _gewartet[0].ToString());
        // Uhr weit vorgestellt: gar keine Wartezeit
        _jetzt = _jetzt.AddHours(2);
        await Anklicken(Golf, 3);
        Assert.Equal(3, _b.Aufrufe.Count);
        Assert.Single(_gewartet);
    }

    [Fact]   // Pruefung 05.10.2026 (Paket 3, F2/F4): kurzer Takt nur, solange eine Aenderung offen ist; Verbindung vorwaermen
    public async Task Kurzer_Takt_und_Vorwaermen_nur_bei_offener_Aenderung()
    {
        await Start();
        Assert.False(_u.KurzerTakt);
        Assert.Equal(0, _server.Vorgewaermt);
        _q.Zeige(Bentley, 1);
        await Tick();                                     // Aenderung bemerkt: offen, Verbindung wird vorgewaermt
        Assert.True(_u.KurzerTakt);
        Assert.Equal(1, _server.Vorgewaermt);
        await Tick();
        Assert.True(_u.KurzerTakt);
        await Tick();                                     // gelesen und geoeffnet: nicht mehr offen
        Assert.Single(_b.Aufrufe);
        Assert.False(_u.KurzerTakt);
        Assert.Equal(1, _server.Vorgewaermt);             // nur einmal je Aenderung, nicht je Takt
        for (int i = 0; i < 4; i++) await Tick();
        Assert.False(_u.KurzerTakt);
        Assert.Equal(1, _server.Vorgewaermt);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Wunsch Ahmad 06.10.2026: "das Programm vergleicht selber alle Autos auf AutoPointer und klickt sich selber
    // durch — nur die, die man anklickt". Zeigt AutoPointer von selbst ein anderes Auto (Live-Liste rutscht weiter),
    // wird erst nach einem Mausklick des Suchers in AutoPointer verglichen.
    private long _klickMs = long.MinValue / 2;
    private long JetztMs => _jetzt.Ticks / TimeSpan.TicksPerMillisecond;

    private Ueberwacher MitKlicks()
    {
        var u = new Ueberwacher(_q, () => _e, _b, _server, () => _jetzt, t => { _jetzt += t; return Task.CompletedTask; },
                                letzterKlick: () => _klickMs);
        u.Neustart();
        return u;
    }

    private async Task TickK(Ueberwacher u, int ms = 250)
    {
        await u.TickAsync();
        _jetzt = _jetzt.AddMilliseconds(ms);
    }

    private async Task AnzeigeK(Ueberwacher u, Func<Fahrzeug> f, ulong summe, bool geklickt)
    {
        if (geklickt) _klickMs = JetztMs;
        _q.Zeige(f, summe);
        for (int i = 0; i < 4; i++) await TickK(u);
    }

    [Fact]
    public async Task Ohne_Klick_zeigt_AutoPointer_ein_anderes_Auto_dann_kein_Vergleich_erst_beim_Anklicken()
    {
        var u = MitKlicks();
        await TickK(u);                                   // AutoPointer zeigt noch nichts
        await AnzeigeK(u, Bentley, 1, geklickt: true);
        Assert.Single(_b.Aufrufe);
        _jetzt = _jetzt.AddSeconds(10);
        await AnzeigeK(u, Passat, 2, geklickt: false);    // Live-Liste rutscht weiter
        await AnzeigeK(u, Golf, 3, geklickt: false);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(1, _q.Lesungen);                     // gar nicht erst gelesen
        Assert.False(u.KurzerTakt);
        // der Sucher klickt jetzt genau das angezeigte Auto an (die Anzeige aendert sich dabei nicht)
        _klickMs = JetztMs;
        for (int i = 0; i < 4; i++) await TickK(u);
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Contains("VW-Golf-2019", _b.Aufrufe[1][0].Url);
    }

    [Fact]
    public async Task Klick_kurz_vor_der_Aenderung_zaehlt_ein_alter_nicht()
    {
        var u = MitKlicks();
        await TickK(u);
        _klickMs = JetztMs - 3000;                        // AutoPointer laedt die Details etwas laenger
        _q.Zeige(Bentley, 1);
        for (int i = 0; i < 4; i++) await TickK(u);
        Assert.Single(_b.Aufrufe);
        _jetzt = _jetzt.AddSeconds(30);
        _klickMs = JetztMs - (Ueberwacher.KlickVorlaufMs + 1000);
        _q.Zeige(Passat, 2);
        for (int i = 0; i < 4; i++) await TickK(u);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Beim_Start_gemerkt_ohne_Klick_danach_nur_Angeklicktes()
    {
        _q.Zeige(Bentley, 1);                             // beim Start schon angezeigt
        var u = MitKlicks();
        for (int i = 0; i < 4; i++) await TickK(u);
        Assert.Empty(_b.Aufrufe);                         // nur gemerkt (wie bisher)
        Assert.Equal(1, _q.Lesungen);
        await AnzeigeK(u, Passat, 2, geklickt: true);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Nach_dem_Verbinden_und_per_Knopf_auch_ohne_Klick()
    {
        var u = MitKlicks();
        await TickK(u);
        u.NachVerbinden();                                // zaehlt wie ein Klick: das angezeigte Auto kommt dran
        await AnzeigeK(u, Bentley, 1, geklickt: false);
        Assert.Single(_b.Aufrufe);
        _jetzt = _jetzt.AddMinutes(1);
        await AnzeigeK(u, Passat, 2, geklickt: false);
        Assert.Single(_b.Aufrufe);
        await u.JetztVergleichenAsync();                  // "Vergleichen" geht immer
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Contains("VW-Passat_Variant-2006", _b.Aufrufe[1][0].Url);
    }

    [Fact]   // Pruefung 08.10.2026 (1.5.9, H): ohne Klick uebergangen -> einmal je Programmlauf sagen, wie es geht
    public async Task Ohne_Klick_uebergangen_einmal_je_Programmlauf_ein_Hinweis()
    {
        var u = MitKlicks();
        var hinweise = new List<Hinweis>();
        u.Meldung += hinweise.Add;
        await TickK(u);
        await AnzeigeK(u, Bentley, 1, geklickt: true);
        Assert.DoesNotContain(hinweise, h => h.Text == Ueberwacher.MausHinweis);
        _jetzt = _jetzt.AddSeconds(10);
        await AnzeigeK(u, Passat, 2, geklickt: false);    // Pfeiltaste / Live-Liste
        await AnzeigeK(u, Golf, 3, geklickt: false);
        _jetzt = _jetzt.AddSeconds(10);
        await AnzeigeK(u, Passat, 4, geklickt: false);
        var h = Assert.Single(hinweise, h => h.Text == Ueberwacher.MausHinweis);
        Assert.False(h.Fehler);
        Assert.Contains("„Vergleichen“", h.Text);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]   // Pruefung 08.10.2026 (1.5.9, D): das Auto vom Start ist nur gemerkt — nicht "Inserat-ID fehlt" sagen
    public async Task Beim_Start_gemerktes_Auto_ist_noch_nicht_verglichen()
    {
        _q.Zeige(() => { var f = Bentley(); f.Quelle = "mobile.de"; f.InseratId = null; return f; }, 1);
        for (int i = 0; i < 5; i++) await Tick();
        Assert.True(_u.LetztesNurGemerkt);
        Assert.Null(_u.LetzteInseratUrl);
        _u.LetztenErneutOeffnen();
        Assert.Equal(Ueberwacher.NochNichtVerglichen, Assert.Single(_hinweise).Text);
        Assert.DoesNotContain(_meldungen, m => m.Contains("Inserat-ID"));
        await _u.JetztVergleichenAsync();                // jetzt verglichen
        Assert.False(_u.LetztesNurGemerkt);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]   // Pruefung 08.10.2026 (1.5.9, C): die lange Inserat-ID-Anleitung hoechstens einmal je Programmlauf als Sprechblase
    public async Task Inserat_ID_Hinweis_hoechstens_einmal_als_Sprechblase_ganz_im_Fenster()
    {
        await Start();
        Fahrzeug OhneId(string modell)
        {
            var f = Bentley();
            f.MarkeModellText = modell;
            f.Quelle = "mobile.de";
            f.InseratId = null;
            return f;
        }
        await Anklicken(() => OhneId("Bentley Bentayga"), 1);
        await Anklicken(() => OhneId("Bentley Continental"), 2);
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Equal(2, _hinweise.Count);
        Assert.True(_hinweise[0].Sprechblase);
        Assert.Equal(Ueberwacher.KeineNummerKurz, _hinweise[0].Text);
        Assert.Equal(Ueberwacher.KeineNummerHinweis, _hinweise[0].Ausfuehrlich);
        Assert.False(_hinweise[1].Sprechblase);                      // beim zweiten Auto nur noch im Fenster
        Assert.Equal(Ueberwacher.KeineNummerHinweis, _hinweise[1].Ausfuehrlich);
        Assert.Contains("„Vertrag“", Ueberwacher.KeineNummerHinweis);
        Assert.DoesNotContain("„Kaufvertrag“", Ueberwacher.KeineNummerHinweis + Ueberwacher.KeinLinkHinweis);
    }

    [Fact]   // 1.5.9 (C): mehrere Hinweise -> eine kurze Sprechblase "+N weitere", alles ganz im Fenster
    public async Task Mehrere_Hinweise_eine_kurze_Sprechblase()
    {
        await Start();
        _server.Melden = new()
        {
            "Erstzulassung 04/2026 passt nicht zu 165.000 km – ohne Erstzulassungs-Filter gesucht. Bitte prüfen und in AutoPointer nachsehen, ob richtig gelesen.",
            "Modell aus der Beschreibung übernommen („C 300 e“) – bitte kurz prüfen, ob das stimmt.",
        };
        await Anklicken(Golf, 1);
        var h = Assert.Single(_hinweise);
        Assert.True(h.Text.Length <= Hinweis.MaxZeichen);
        Assert.EndsWith("(+1 weitere im Fenster „Status und Hilfe“)", h.Text);
        Assert.Contains("C 300 e", h.Ausfuehrlich);
        Assert.Contains("165.000 km", h.Ausfuehrlich);
    }

    [Fact]   // 1.5.9 (C): Kuerzen am Wortende, alle festen Sprechblasen-Texte <= 150 Zeichen
    public void Sprechblasen_hoechstens_150_Zeichen()
    {
        string lang = string.Join(" ", Enumerable.Repeat("Wort", 60));
        string k = Hinweis.Kuerzen(lang);
        Assert.True(k.Length <= Hinweis.MaxZeichen, k.Length.ToString());
        Assert.EndsWith("Wort…", k);
        Assert.Equal("kurz", Hinweis.Kuerzen("  kurz "));
        foreach (var t in new[]
                 {
                     Ueberwacher.KeineNummerKurz, Ueberwacher.KeinLinkKurz, Ueberwacher.VertragOhneAdresse,
                     Ueberwacher.MausHinweis, Ueberwacher.NochNichtVerglichen, Ueberwacher.VorgangNichtErreichbar,
                     Ueberwacher.ErweiterungNichtUebernommen, TrayApp.UpdateText("10.10.10"),
                 })
            Assert.True(t.Length <= Hinweis.MaxZeichen, $"{t.Length}: {t}");
        // die langen Anleitungen waren der Anlass (≈ 400 / 360 Zeichen) — sie bleiben ganz, aber nur fuers Fenster
        Assert.True(Ueberwacher.KeineNummerHinweis.Length > Hinweis.MaxZeichen);
    }

    // ------------------------------------------------------------------------------------------------------------
    // Pruefung 08.10.2026 (1.5.9, G): "wird gleich erneut versucht" — jetzt wirklich: ein Versuch nach 5 s
    private async Task Ticks(int anzahl)
    {
        for (int i = 0; i < anzahl; i++) await Tick();
    }

    [Fact]
    public async Task Voruebergehender_Fehler_wird_nach_5_Sekunden_einmal_wiederholt()
    {
        await Start();
        _server.Fehler = new DienstFehler(503, "AutoSchnell ist kurz nicht erreichbar.");
        await Anklicken(Bentley, 1);
        Assert.Single(_server.Anfragen);
        Assert.Contains(_meldungen, m => m.Contains("kurz nicht erreichbar") && m.Contains("Neuer Versuch in 5 Sekunden"));
        _server.Fehler = null;
        await Ticks(8);                                              // 2 s: noch nicht
        Assert.Single(_server.Anfragen);
        await Ticks(16);                                             // nach 5 s: dasselbe Auto noch einmal
        Assert.Equal(2, _server.Anfragen.Count);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(Status.Aktiv, _u.Status);
        await Ticks(30);
        Assert.Equal(2, _server.Anfragen.Count);                     // genau einmal
    }

    [Fact]
    public async Task Wiederholung_nur_solange_dasselbe_Auto_angezeigt_wird()
    {
        await Start();
        _server.Fehler = new DienstFehler(0, "AutoSchnell antwortet gerade nicht.");
        await Anklicken(Bentley, 1);
        _server.Fehler = null;
        await Anklicken(Passat, 2);                                  // anderes Auto: der Versuch fuer Bentley entfaellt
        await Ticks(30);
        Assert.Equal(2, _server.Anfragen.Count);
        Assert.Single(_b.Aufrufe);
        Assert.Contains("VW-Passat_Variant-2006", _b.Aufrufe[0][0].Url);
    }

    [Fact]
    public async Task Schlaegt_auch_der_zweite_Versuch_fehl_wird_nichts_mehr_versprochen()
    {
        await Start();
        _server.Fehler = new DienstFehler(504, "AutoSchnell ist kurz nicht erreichbar.");
        await Anklicken(Bentley, 1);
        await Ticks(24);
        Assert.Equal(2, _server.Anfragen.Count);
        Assert.Contains("später „Vergleichen“ drücken", _meldungen.Last());
        Assert.DoesNotContain("Neuer Versuch", _meldungen.Last());
        await Ticks(30);
        Assert.Equal(2, _server.Anfragen.Count);
        Assert.Empty(_b.Aufrufe);
    }

    [Fact]   // kein Wiederholversuch, wo er nichts bringt: ohne Abo, echtes 403, nicht verbunden, bei Pause
    public async Task Kein_Wiederholversuch_bei_Abo_Sperre_und_Pause()
    {
        await Start();
        _server.Fehler = new DienstFehler(403, "Für dein Konto nicht freigeschaltet.");
        await Anklicken(Bentley, 1);
        Assert.Equal(Status.Gesperrt, _u.Status);
        await Ticks(30);
        Assert.Single(_server.Anfragen);
        Assert.DoesNotContain(_meldungen, m => m.Contains("Neuer Versuch"));

        // "Vergleichen" bei Pause: der Takt laeuft nicht -> nichts versprechen
        _e.AutomatikAktiv = false;
        _server.Fehler = new DienstFehler(503, "AutoSchnell ist kurz nicht erreichbar.");
        await _u.JetztVergleichenAsync();
        Assert.Contains("später „Vergleichen“ drücken", _meldungen.Last());
    }

    [Fact]   // 1.5.9 (G): ein 403 OHNE JSON (Cloudflare/Firewall) ist voruebergehend — keine Sperre, ein Wiederholversuch
    public async Task Cloudflare_403_ohne_JSON_ist_keine_Sperre()
    {
        await Start();
        _server.Fehler = new DienstFehler(403, "AutoSchnell ist kurz nicht erreichbar.") { OhneJson = true };
        await Anklicken(Bentley, 1);
        Assert.Equal(Status.Aktiv, _u.Status);
        Assert.Contains(_meldungen, m => m.Contains("Neuer Versuch"));
        _server.Fehler = null;
        await Ticks(24);
        Assert.Single(_b.Aufrufe);
    }
}
