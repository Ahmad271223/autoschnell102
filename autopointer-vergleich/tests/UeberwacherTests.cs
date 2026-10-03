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
        public readonly List<Fahrzeug> Anfragen = new();

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
            return Task.FromResult(new VergleichAntwort(new[]
            {
                new Vergleich("mobile.de", $"https://suchen.mobile.de/{id}"),
                new Vergleich("AutoScout24", $"https://www.autoscout24.de/{id}"),
            }, Array.Empty<string>(), "inland", InseratUrl, InseratUrl != null ? "laeuft" : "kein_link",
               ErkanntMarke: "Erkannt", ErkanntModell: f.MarkeModellText));
        }
    }

    private sealed class Browser : IOeffner
    {
        public readonly List<IReadOnlyList<Vergleich>> Aufrufe = new();
        public void Oeffne(IReadOnlyList<Vergleich> v, Einstellungen e, IntPtr ap) => Aufrufe.Add(v);
    }

    private readonly Attrappe _q = new();
    private readonly Browser _b = new();
    private readonly Server _server = new();
    private readonly List<string> _verloren = new();
    private readonly Einstellungen _e = new();
    private readonly List<string> _meldungen = new();
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
        _u.Meldung += (t, _) => _meldungen.Add(t);
        _u.VerbindungVerloren += m => _verloren.Add(m);
        _u.Neustart();
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
        Assert.Contains(_meldungen, m => m.Contains("Hash-ID") && m.Contains("selbst kopieren"));
    }

    [Fact]
    public async Task Nur_die_eingestellten_Portale_oeffnen()
    {
        await Start();
        _e.MobileDe = false;
        await Anklicken(Bentley, 1);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(new[] { "AutoScout24" }, _b.Aufrufe[0].Select(v => v.Portal));
    }

    [Fact]   // Befund 03.10.2026: AutoPointer moeglichst nicht selbst zeichnen lassen (PrintWindow)
    public void Bildschirm_reicht_nur_mit_Pflichtfeldern_und_Inserat_ID()
    {
        Assert.True(AutoPointerQuelle.Reicht(Passat()));     // alle Zeilen bis zur Inserat-ID sichtbar
        Assert.False(AutoPointerQuelle.Reicht(Bentley()));   // Tabelle endet vor der Inserat-ID -> PrintWindow
        var f = Passat();
        f.Kilometer = null;                                  // Pflichtfeld fehlt -> PrintWindow
        Assert.False(AutoPointerQuelle.Reicht(f));
    }
}
