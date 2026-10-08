using System.Net;
using System.Text;
using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

/// <summary>Pruefbericht 03.10.2026 (18 Punkte): alles, was sich im Programm ohne echten AutoPointer pruefen laesst.</summary>
[Collection("Protokolldateien")]
public class PruefberichtTests
{
    // ------------------------------------------------------------------ Attrappen
    private sealed class Quelle : IAnsichtQuelle
    {
        public Func<Fahrzeug>? Fahrzeug;
        public ulong Summe;
        public IntPtr Hauptfenster => IntPtr.Zero;
        public QuellenZustand Pruefe() => new(Fahrzeug == null ? Lage.KeineDetails : Lage.Details, Fahrzeug == null ? 0 : Summe);
        public Task<Lesung?> LiesAsync() => Task.FromResult<Lesung?>(new Lesung(Fahrzeug!(), false, "roh"));
    }

    private sealed class Server : IVergleichsDienst
    {
        public bool Verbunden => true;
        public bool MarkeUnbekannt;
        public Task<bool?> VorgangSelbstAsync(string vorgangId) => Task.FromResult<bool?>(false);
        public Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf)
        {
            if (MarkeUnbekannt)
                return Task.FromResult(new VergleichAntwort(Array.Empty<Vergleich>(), new[] { "Marke unbekannt" }, "inland",
                                                            MarkeErkannt: false));
            string id = $"{f.MarkeModellText.Replace(' ', '-')}-{f.InseratKennung ?? "ohne"}";
            return Task.FromResult(new VergleichAntwort(new[] { new Vergleich("mobile.de", $"https://suchen.mobile.de/{id}") },
                                                        Array.Empty<string>(), "inland"));
        }
    }

    private sealed class Browser : IOeffner
    {
        public readonly List<IReadOnlyList<Vergleich>> Aufrufe = new();
        public void Oeffne(IReadOnlyList<Vergleich> v, Einstellungen e, IntPtr ap, BrowserWahl browser) => Aufrufe.Add(v);
    }

    private readonly Quelle _q = new();
    private readonly Server _s = new();
    private readonly Browser _b = new();
    private readonly List<string> _meldungen = new();
    private DateTime _jetzt = new(2026, 10, 3, 12, 0, 0);
    private readonly Ueberwacher _u;

    public PruefberichtTests()
    {
        Protokoll.DateiAktiv = false;
        _u = new Ueberwacher(_q, () => new Einstellungen(), _b, _s, () => _jetzt, t => { _jetzt += t; return Task.CompletedTask; });
        _u.Meldung += h => _meldungen.Add(h.Text);
        _u.Neustart();
    }

    private async Task Anklicken(Func<Fahrzeug> f, ulong summe)
    {
        _q.Fahrzeug = f;
        _q.Summe = summe;
        for (int i = 0; i < 4; i++)
        {
            await _u.TickAsync();
            _jetzt = _jetzt.AddMilliseconds(250);
        }
    }

    private static Fahrzeug Neuwagen(string? id) => new()
    {
        MarkeModellText = "VW Golf", EzJahr = 2026, EzMonat = 10, Kilometer = 0, Kw = 110, Ps = 150,
        Quelle = "mobile.de", InseratId = id,
    };

    private async Task Start()
    {
        _q.Fahrzeug = null;
        await _u.TickAsync();
    }

    // ------------------------------------------------------------------ Nr. 1
    [Fact]
    public async Task Nr1_zwei_Neuwagen_mit_gleichen_Daten_aber_anderer_Inserat_ID_sind_zwei_Autos()
    {
        await Start();
        await Anklicken(() => Neuwagen("440123456"), 1);
        await Anklicken(() => Neuwagen("440999999"), 2);
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Contains("440999999", _b.Aufrufe[1][0].Url);
    }

    [Fact]
    public async Task Nr1_gleiche_Inserat_ID_bleibt_dasselbe_Auto_auch_wenn_ein_Wert_anders_gelesen_wird()
    {
        await Start();
        await Anklicken(() => Neuwagen("440123456"), 1);
        await Anklicken(() => { var f = Neuwagen("440123456"); f.Kilometer = 10; return f; }, 2);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Nr1_ohne_Inserat_ID_entscheiden_die_Daten_wie_bisher()
    {
        await Start();
        await Anklicken(() => Neuwagen("440123456"), 1);
        await Anklicken(() => Neuwagen(null), 2);            // ID diesmal nicht lesbar, Daten gleich
        Assert.Single(_b.Aufrufe);
        Assert.Null(Neuwagen("1").InseratKennung);           // zu kurz fuer eine Kennung
        Assert.Equal("440123456", Neuwagen(" 440-123 456 ").InseratKennung);
        var hash = Neuwagen(null);
        hash.HashId = "8f0c2c4e-1d2b-4c1a-9a7e-3b5f6d7e8f90";
        Assert.Equal("8f0c2c4e1d2b4c1a9a7e3b5f6d7e8f90", hash.InseratKennung);
    }

    // ------------------------------------------------------------------ Nr. 2
    [Fact]
    public async Task Nr2_neues_Auto_ohne_Links_loescht_die_Links_des_vorigen()
    {
        await Start();
        await Anklicken(() => Neuwagen("440123456"), 1);
        Assert.Single(_u.LetzteVergleiche);
        _s.MarkeUnbekannt = true;
        await Anklicken(() => { var f = Neuwagen("440777777"); f.MarkeModellText = "Quatsch Modell"; return f; }, 2);
        Assert.Empty(_u.LetzteVergleiche);
        Assert.Equal("Quatsch Modell", _u.LetztesFahrzeug!.MarkeModellText);
        _u.LetztenErneutOeffnen();
        Assert.Single(_b.Aufrufe);                            // NICHT noch einmal die Links des Golfs
        Assert.Contains(_meldungen, m => m.Contains("zuletzt angeklickte Auto"));
    }

    [Fact]
    public async Task Nr2_Auto_beim_Start_hat_noch_keinen_Vergleich_und_kein_Inserat()
    {
        _q.Fahrzeug = () => Neuwagen("440123456");           // schon angezeigt, bevor die Automatik startet
        _q.Summe = 5;
        for (int i = 0; i < 4; i++) { await _u.TickAsync(); _jetzt = _jetzt.AddMilliseconds(250); }
        Assert.Empty(_b.Aufrufe);
        Assert.NotNull(_u.LetztesFahrzeug);
        Assert.Empty(_u.LetzteVergleiche);
        Assert.Null(_u.LetzteInseratUrl);
    }

    // ------------------------------------------------------------------ Nr. 9
    [Theory]
    [InlineData("170 kW (150 PS)")]       // kW falsch gelesen (110 -> 170)
    [InlineData("110 kW (250 PS)")]       // PS falsch gelesen
    public void Nr9_widerspruechliche_Leistung_ist_unbekannt_statt_falsch(string text)
    {
        var (kw, ps, unsicher) = DetailLeser.LeistungGeprueft(text);
        Assert.Null(kw);
        Assert.Null(ps);
        Assert.True(unsicher);
    }

    [Fact]
    public void Nr9_stimmige_Leistung_bleibt_und_der_zweite_Durchgang_kann_klaeren()
    {
        Assert.Equal(((int?)110, (int?)150, false), DetailLeser.LeistungGeprueft("110 kW (150 PS)"));
        var a = new Fahrzeug { LeistungUnsicher = true };
        var b = new Fahrzeug { Kw = 110, Ps = 150 };
        DetailLeser.Ergaenzen(a, b);
        Assert.Equal(110, a.Kw);
        Assert.False(a.LeistungUnsicher);
        var c = DetailLeser.Ergaenzen(new Fahrzeug { LeistungUnsicher = true }, new Fahrzeug { LeistungUnsicher = true });
        Assert.True(c.LeistungUnsicher);
    }

    [Fact]
    public void Nr9_Hinweise_bei_unsicherer_Leistung_und_ungewoehnlich_vielen_Kilometern()
    {
        var jetzt = new DateTime(2026, 10, 3);
        var f = new Fahrzeug { MarkeModellText = "VW Golf", EzJahr = 2025, EzMonat = 10, Kilometer = 300_000, LeistungUnsicher = true };
        var h = Ueberwacher.PlausibilitaetsHinweise(f, jetzt);
        Assert.Contains(h, x => x.Contains("Leistung nicht sicher"));
        Assert.Contains(h, x => x.Contains("Kilometerstand ungewöhnlich hoch"));
        Assert.Empty(Ueberwacher.PlausibilitaetsHinweise(
            new Fahrzeug { EzJahr = 2019, EzMonat = 5, Kilometer = 61_000, Kw = 110 }, jetzt));
    }

    // ------------------------------------------------------------------ Nr. 7
    [Theory]
    [InlineData(@"C:\Program Files (x86)\AutoPointer\neu.exe", true)]
    [InlineData(@"C:\Program Files\vitdev\AP\start.exe", true)]
    [InlineData(@"C:\Windows\notepad.exe", false)]
    [InlineData(null, false)]
    public void Nr7_AutoPointer_auch_am_Installationsordner_erkannt(string? pfad, bool erwartet) =>
        Assert.Equal(erwartet, AutoPointerFenster.IstAutoPointerPfad(pfad));

    // ------------------------------------------------------------------ Nr. 11
    [Fact]
    public void Nr11_Inserat_Adresse_aus_der_Zwischenablage_nur_passend_zum_Portal()
    {
        string autoscout = "https://www.autoscout24.de/angebote/vw-golf-benzin-8f0c2c4e-1d2b-4c1a-9a7e-3b5f6d7e8f90";
        Assert.Equal(autoscout, TrayApp.InseratAdresse("Schau mal: " + autoscout + " .", "AutoScout24"));
        Assert.Null(TrayApp.InseratAdresse("https://suchen.mobile.de/fahrzeuge/details.html?id=123456789", "AutoScout24"));
        Assert.Null(TrayApp.InseratAdresse("https://www.autoscout24.de/lst/vw/golf", "AutoScout24"));      // Suche, kein Inserat
        Assert.Null(TrayApp.InseratAdresse("https://autoscout24.de.boese.example/angebote/x", "AutoScout24"));
        Assert.Null(TrayApp.InseratAdresse("http://www.autoscout24.de/angebote/x", "AutoScout24"));        // kein https
        Assert.Equal("https://suchen.mobile.de/fahrzeuge/details.html?id=123456789",
                     TrayApp.InseratAdresse("https://suchen.mobile.de/fahrzeuge/details.html?id=123456789", "mobile.de"));
        Assert.Null(TrayApp.InseratAdresse("", "mobile.de"));
    }

    // ------------------------------------------------------------------ Nr. 13
    [Fact]
    public void Nr13_App_nur_mit_genauem_Namen_und_ohne_Raten_bei_mehreren()
    {
        const string chrome = @"C:\Program Files\Google\Chrome\Application\chrome_proxy.exe";
        const string server = "https://app.auto-schnellkauf.de";
        Assert.NotNull(AutoSchnellApp.AusVerknuepfung(chrome, "--app-id=abcdefghijklmnopabcdefghijklmnop", "AutoSchnell (Profil 2).lnk", server));
        Assert.Null(AutoSchnellApp.AusVerknuepfung(chrome, "--app-id=abcdefghijklmnopabcdefghijklmnop", "AutoSchnell Test.lnk", server));
        Assert.Null(AutoSchnellApp.AusVerknuepfung(chrome, "--app-id=abcdefghijklmnopabcdefghijklmnop", "AutoSchnellAlt.lnk", server));

        var nameA = new AutoSchnellApp.Verknuepfung(chrome, "Default", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        var nameB = new AutoSchnellApp.Verknuepfung(chrome, "Profile 1", "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
        var sicher = new AutoSchnellApp.Verknuepfung(chrome, "Default", "cccccccccccccccccccccccccccccccc", PerAdresse: true);
        Assert.Null(AutoSchnellApp.Auswaehlen(new[] { nameA, nameB }));              // zwei verschiedene: nicht raten
        Assert.Equal(nameA, AutoSchnellApp.Auswaehlen(new[] { nameA, nameA with { Programm = "x" } }));
        Assert.Equal(sicher, AutoSchnellApp.Auswaehlen(new[] { nameA, nameB, sicher }));
        Assert.Null(AutoSchnellApp.Auswaehlen(Array.Empty<AutoSchnellApp.Verknuepfung>()));
    }

    // ------------------------------------------------------------------ Nr. 14
    [Theory]
    [InlineData("https://app.auto-schnellkauf.de", "https://app.auto-schnellkauf.de")]
    [InlineData("https://kfz-mueller.auto-schnellkauf.de/", "https://kfz-mueller.auto-schnellkauf.de")]
    [InlineData("http://127.0.0.1:9", "http://127.0.0.1:9")]
    [InlineData("http://localhost:8002/", "http://localhost:8002")]
    [InlineData("http://app.auto-schnellkauf.de", null)]
    [InlineData("https://boese.example", null)]
    [InlineData("https://auto-schnellkauf.de.boese.example", null)]
    [InlineData("https://nutzer:pw@app.auto-schnellkauf.de", null)]
    [InlineData("ftp://app.auto-schnellkauf.de", null)]
    [InlineData("", null)]
    public void Nr14_Server_nur_https_AutoSchnell_oder_der_eigene_Rechner(string adresse, string? erwartet) =>
        Assert.Equal(erwartet, Einstellungen.SichererServer(adresse));

    [Fact]
    public void Nr14_unsichere_Adresse_in_den_Einstellungen_faellt_auf_den_Standard()
    {
        var e = new Einstellungen { Server = "http://boese.example" }.Bereinigt();
        Assert.Equal(Einstellungen.StandardServer, e.Server);
    }

    // ------------------------------------------------------------------ Nr. 15, 17, 4, 12 (Server-Antworten)
    private sealed class Http : HttpMessageHandler
    {
        public Func<HttpRequestMessage, Task<HttpResponseMessage>> Antwort = _ => Task.FromResult(Json("{}"));
        /// <summary>Wie ein haengender Server: wartet, bricht aber ab, wenn der Aufrufer abbricht (wie das echte Netz).</summary>
        public TimeSpan Verzoegerung = TimeSpan.Zero;
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken t)
        {
            if (Verzoegerung > TimeSpan.Zero) await Task.Delay(Verzoegerung, t);
            return await Antwort(r);
        }
    }

    private static HttpResponseMessage Json(string text, int status = 200) =>
        new((HttpStatusCode)status) { Content = new StringContent(text, Encoding.UTF8, "application/json") };

    [Fact]
    public async Task Nr15_nur_Links_zu_mobile_de_und_AutoScout24_werden_geoeffnet()
    {
        var h = new Http
        {
            Antwort = _ => Task.FromResult(Json("""
                {"links":[{"portal":"mobile.de","url":"https://suchen.mobile.de/fahrzeuge/search.html?ms=1"},
                          {"portal":"AutoScout24","url":"https://www.autoscout24.de/lst/vw"},
                          {"portal":"fremd","url":"https://boese.example/phish"},
                          {"portal":"trick","url":"https://mobile.de.boese.example/x"}],
                 "inserat_url":"https://boese.example/inserat","hinweise":[],"profil":"inland"}
                """)),
        };
        var d = new AutoSchnellDienst("https://app.example.test", () => "k", h);
        var a = await d.VergleichAsync(Passat(), false);
        Assert.Equal(new[] { "mobile.de", "AutoScout24" }, a.Links.Select(l => l.Portal));
        Assert.Null(a.InseratUrl);
        Assert.True(AutoSchnellDienst.ErlaubteAdresse("https://www.kleinanzeigen.de/s-anzeige/1", AutoSchnellDienst.InseratSeiten));
        Assert.False(AutoSchnellDienst.ErlaubteAdresse("https://www.kleinanzeigen.de/s-anzeige/1", AutoSchnellDienst.VergleichsSeiten));
    }

    [Fact]
    public async Task Nr17_haengt_der_Server_ist_nach_der_Frist_Schluss_mit_klarer_Meldung()
    {
        var alt = AutoSchnellDienst.VergleichFrist;
        AutoSchnellDienst.VergleichFrist = TimeSpan.FromMilliseconds(200);
        try
        {
            var h = new Http { Verzoegerung = TimeSpan.FromSeconds(5) };
            var d = new AutoSchnellDienst("https://app.example.test", () => "k", h);
            var start = DateTime.Now;
            var ex = await Assert.ThrowsAsync<DienstFehler>(() => d.VergleichAsync(Passat(), false));
            Assert.True((DateTime.Now - start).TotalSeconds < 3, "Frist greift");
            Assert.Contains("antwortet gerade nicht", ex.Message);
            Assert.True(ex.KeineVerbindung);
        }
        finally { AutoSchnellDienst.VergleichFrist = alt; }
    }

    [Fact]
    public async Task Nr4_Status_nennt_die_angebotene_Version()
    {
        var h = new Http
        {
            Antwort = _ => Task.FromResult(Json("""
                {"konto":"10002","name":"","firma":"Test","pc_name":"PC","abo_bis":null,
                 "aktuelle_version":"1.6.0","programm_name":"Vergleichs-Programm"}
                """)),
        };
        var s = await new AutoSchnellDienst("https://app.example.test", () => "k", h).StatusAsync();
        Assert.Equal("1.6.0", s.AktuelleVersion);
        Assert.Equal("Vergleichs-Programm", s.ProgrammName);
        Assert.True(AutoSchnellDienst.NeuereVersion("1.6.0", "1.5.0"));
        Assert.True(AutoSchnellDienst.NeuereVersion("1.5.1", "1.5.0+abc"));
        Assert.False(AutoSchnellDienst.NeuereVersion("1.5.0", "1.5.0"));
        Assert.False(AutoSchnellDienst.NeuereVersion("1.4.9", "1.5.0"));
        Assert.False(AutoSchnellDienst.NeuereVersion(null, "1.5.0"));
        Assert.False(AutoSchnellDienst.NeuereVersion("quatsch", "1.5.0"));
    }

    [Fact]
    public async Task Nr12_App_Start_wird_beim_Server_nachgefragt()
    {
        string? pfad = null;
        var h = new Http
        {
            Antwort = r =>
            {
                pfad = r.RequestUri!.AbsolutePath;
                return Task.FromResult(Json(r.RequestUri.AbsolutePath.EndsWith("/abc123") ? """{"bestaetigt":true}""" : """{"bestaetigt":false}"""));
            },
        };
        var d = new AutoSchnellDienst("https://app.example.test", () => "k", h);
        Assert.True(await d.AppStartBestaetigtAsync("abc123"));
        Assert.Equal("/api/werkzeuge/autopointer-vergleich/app-start/abc123", pfad);
        Assert.False(await d.AppStartBestaetigtAsync("anders"));
        h.Antwort = _ => Task.FromResult(Json("{}", 500));
        Assert.False(await d.AppStartBestaetigtAsync("abc123"));            // Fehler zaehlen als "nicht bestaetigt"
    }

    // ------------------------------------------------------------------ Nr. 6/8
    [Fact]
    public void Nr6_8_Systemcheck_sagt_was_fehlt()
    {
        var gut = new Systemcheck.Befund
        {
            Windows = new Version(10, 0, 26200), OcrSprache = "de-DE", ServerErreichbar = true,
            Server = "https://app.auto-schnellkauf.de", Verbunden = true,
            Status = new StatusAntwort("10002", "Max", "Firma", "PC", "2026-12-31T00:00:00", "1.5.0"),
            EigeneVersion = "1.5.0", AutoPointerLaeuft = true, FahrzeugAngezeigt = true,
            Gelesen = Neuwagen("440123456"),
            Probe = new VergleichAntwort(new[] { new Vergleich("mobile.de", "https://suchen.mobile.de/x") }, Array.Empty<string>(), "inland"),
            BrowserGefunden = true, AppInstalliert = true,
        };
        var punkte = Systemcheck.Auswerten(gut);
        Assert.All(punkte, p => Assert.Equal(PruefStufe.Ok, p.Stufe));
        Assert.Contains(punkte, p => p.Name == "Probe-Lesung" && p.Text.Contains("Inserat-ID erkannt"));
        Assert.Contains(punkte, p => p.Name == "Probe-Vergleich" && p.Text.Contains("mobile.de"));
        Assert.False(Systemcheck.SchwererFehler(punkte));

        var ohneOcr = Systemcheck.Auswerten(gut with { OcrSprache = null, OcrFehler = "Texterkennung fehlt", Gelesen = null, Probe = null });
        Assert.Contains(ohneOcr, p => p.Name == "Texterkennung" && p.Stufe == PruefStufe.Fehler && p.Text == "Texterkennung fehlt");
        Assert.True(Systemcheck.SchwererFehler(ohneOcr));

        var apZu = Systemcheck.Auswerten(gut with { AutoPointerLaeuft = false, FahrzeugAngezeigt = false, Gelesen = null, Probe = null });
        Assert.Contains(apZu, p => p.Name == "AutoPointer" && p.Stufe == PruefStufe.Hinweis);
        Assert.False(Systemcheck.SchwererFehler(apZu));          // AutoPointer zu ist kein Grund, den Check aufzudraengen

        var update = Systemcheck.Auswerten(gut with { Status = gut.Status! with { AktuelleVersion = "1.6.0" } });
        Assert.Contains(update, p => p.Name == "Programmversion" && p.Stufe == PruefStufe.Hinweis && p.Text.Contains("1.6.0"));

        var getrennt = Systemcheck.Auswerten(gut with { Verbunden = false, Status = null });
        Assert.Contains(getrennt, p => p.Name == "Verbindung" && p.Stufe == PruefStufe.Fehler);
        Assert.Contains("✖", Systemcheck.Text(getrennt));
    }
}
