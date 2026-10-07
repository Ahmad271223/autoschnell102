using System.Net;
using System.Text;
using System.Text.Json;
using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

/// <summary>Verbindung zu AutoSchnell (Wunsch Ahmad 03.10.2026): Code -> Schluessel, Lizenz,
/// Vergleich ueber den Server — hier mit einer HTTP-Attrappe statt echtem Netz.</summary>
public class DienstTests
{
    private sealed class Attrappe : HttpMessageHandler
    {
        public HttpRequestMessage? Letzte;
        public string? Inhalt;
        public Func<HttpRequestMessage, HttpResponseMessage> Antwort = _ => Json(200, "{}");
        public Exception? Ausnahme;
        public int Aufrufe;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken t)
        {
            Aufrufe++;
            Letzte = r;
            Inhalt = r.Content == null ? null : await r.Content.ReadAsStringAsync(t);
            if (Ausnahme != null) throw Ausnahme;
            return Antwort(r);
        }
    }

    private static HttpResponseMessage Json(int status, string text) =>
        new((HttpStatusCode)status) { Content = new StringContent(text, Encoding.UTF8, "application/json") };

    private static (AutoSchnellDienst, Attrappe) Dienst(string? schluessel = "geheim") => DienstMit(() => schluessel);

    private static (AutoSchnellDienst, Attrappe) DienstMit(Func<string?> schluessel)
    {
        var a = new Attrappe();
        // Wiederholversuch ohne echte Sekunde Pause (Paket 2, A3)
        return (new AutoSchnellDienst("https://app.example.test/", schluessel, a, _ => Task.CompletedTask), a);
    }

    [Fact]
    public async Task Vergleich_schickt_Schluessel_und_Fahrzeugdaten()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """
            {"links":[{"portal":"mobile.de","url":"https://suchen.mobile.de/x?ms=1"},
                      {"portal":"AutoScout24","url":"https://www.autoscout24.de/lst/vw"},
                      {"portal":"boese","url":"javascript:alert(1)"}],
             "hinweise":["Getriebe nicht gelesen"],"profil":"inland",
             "inserat_url":"https://www.kleinanzeigen.de/s-anzeige/3529712138",
             "vorab":{"status":"laeuft","hinweis":""},
             "fahrzeug":{"marke":"Volkswagen","modell":"Passat Variant","erkannt":true},
             "melden":["Erstzulassung 04/2026 passt nicht zu 165.000 km – ohne Erstzulassungs-Filter gesucht. Bitte prüfen."],
             "inserat_im_browser":true}
            """);
        var f = Passat();
        var antwort = await d.VergleichAsync(f, probelauf: false);

        Assert.Equal(HttpMethod.Post, a.Letzte!.Method);
        Assert.Equal("https://app.example.test/api/werkzeuge/autopointer-vergleich/vergleich", a.Letzte.RequestUri!.ToString());
        Assert.Equal("geheim", a.Letzte.Headers.GetValues("X-Werkzeug-Schluessel").Single());
        using var doc = JsonDocument.Parse(a.Inhalt!);
        var fz = doc.RootElement.GetProperty("fahrzeug");
        Assert.Equal("VW", fz.GetProperty("marke").GetString());
        Assert.Equal("Passat Variant", fz.GetProperty("modell").GetString());
        // seit 1.4.0: nur Rohtext — erkannt wird auf dem Server
        Assert.True(fz.GetProperty("roh").GetBoolean());
        Assert.Equal("VW Passat Variant", fz.GetProperty("marke_modell_text").GetString());
        Assert.Equal(("Volkswagen", "Passat Variant", true), (antwort.ErkanntMarke, antwort.ErkanntModell, antwort.MarkeErkannt));
        Assert.Contains("passt nicht zu 165.000 km", Assert.Single(antwort.Melden!));
        Assert.True(antwort.InseratImBrowser);                       // 07.10.2026: Helfer liest das Inserat
        Assert.Equal(2006, fz.GetProperty("ez_jahr").GetInt32());
        Assert.Equal(10, fz.GetProperty("ez_monat").GetInt32());
        Assert.Equal(244000, fz.GetProperty("kilometer").GetInt32());
        Assert.Equal(125, fz.GetProperty("kw").GetInt32());
        Assert.Equal("Diesel", fz.GetProperty("kraftstoff").GetString());
        Assert.Equal("3529712138", fz.GetProperty("inserat_id").GetString());
        Assert.False(doc.RootElement.GetProperty("probelauf").GetBoolean());

        // nur https-Links werden geoeffnet
        Assert.Equal(new[] { "mobile.de", "AutoScout24" }, antwort.Links.Select(l => l.Portal));
        Assert.Equal(new[] { "Getriebe nicht gelesen" }, antwort.Hinweise);
        Assert.Equal("inland", antwort.Profil);
        Assert.Equal("https://www.kleinanzeigen.de/s-anzeige/3529712138", antwort.InseratUrl);
        Assert.Equal("laeuft", antwort.VorabStatus);
        Assert.True(fz.TryGetProperty("hash_id", out _));
    }

    [Theory]
    [InlineData(402, """{"detail":"Kein aktives AutoSchnell-Abo – das Programm ist gesperrt."}""", "Kein aktives AutoSchnell-Abo")]
    [InlineData(401, """{"detail":"Dieses Programm ist nicht (mehr) verbunden"}""", "nicht (mehr) verbunden")]
    [InlineData(403, "kaputt", "nicht freigeschaltet")]
    [InlineData(422, """{"detail":[{"msg":"x"}]}""", "Ungültige Fahrzeugdaten")]
    [InlineData(404, """{"detail":"Not Found"}""", "Server ist noch nicht aktualisiert")]
    [InlineData(404, """{"detail":"Code ungültig oder abgelaufen"}""", "Code ungültig")]
    public async Task Fehler_vom_Server_werden_lesbar(int status, string text, string erwartet)
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(status, text);
        var ex = await Assert.ThrowsAsync<DienstFehler>(() => d.VergleichAsync(Passat(), false));
        Assert.Equal(status, ex.Status);
        Assert.Contains(erwartet, ex.Message);
        Assert.Equal(status == 401, ex.NichtVerbunden);
        Assert.Equal(status == 402, ex.KeinAbo);
    }

    [Fact]
    public async Task Kein_Netz_ist_Status_0()
    {
        var (d, a) = Dienst();
        a.Ausnahme = new HttpRequestException("weg");
        var ex = await Assert.ThrowsAsync<DienstFehler>(() => d.StatusAsync());
        Assert.True(ex.KeineVerbindung);
        Assert.True(ex.Voruebergehend);
        Assert.Contains("Keine Verbindung", ex.Message);
        Assert.Equal(2, a.Aufrufe);                     // Paket 2 (A3): genau ein Wiederholversuch
    }

    [Fact]   // Pruefung 05.10.2026 (Paket 2, A3): ein kurzer Netzaussetzer kostet keinen Vergleich mehr
    public async Task Verbindungsfehler_wird_genau_einmal_wiederholt()
    {
        var (d, a) = Dienst();
        a.Antwort = _ =>
        {
            if (a.Aufrufe == 1) throw new HttpRequestException("Verbindung zurückgesetzt", new System.Net.Sockets.SocketException(10054));
            return Json(200, """{"ok":true,"konto":"10002-1","name":"Max","firma":"AH","pc_name":"PC"}""");
        };
        var s = await d.StatusAsync();
        Assert.Equal("Max", s.Name);
        Assert.Equal(2, a.Aufrufe);
    }

    [Theory]   // Gateway/Ueberlast/Cloudflare: einmal wiederholen, dann eine verstaendliche Meldung
    [InlineData(502)]
    [InlineData(503)]
    [InlineData(504)]
    [InlineData(522)]
    public async Task Gateway_Fehler_wird_einmal_wiederholt_dann_verstaendlich_gemeldet(int status)
    {
        var (d, a) = Dienst();
        a.Antwort = _ => new HttpResponseMessage((HttpStatusCode)status) { Content = new StringContent("<html>Bad Gateway</html>") };
        var ex = await Assert.ThrowsAsync<DienstFehler>(() => d.VergleichAsync(Passat(), false));
        Assert.Equal(status, ex.Status);
        Assert.True(ex.Voruebergehend);
        Assert.Contains("kurz nicht erreichbar", ex.Message);
        Assert.Equal(2, a.Aufrufe);
        // beim zweiten Mal klappt es
        var (d2, a2) = Dienst();
        a2.Antwort = _ => a2.Aufrufe == 1 ? Json(status, "") : Json(200, """{"links":[],"hinweise":[],"profil":"inland"}""");
        var antwort = await d2.VergleichAsync(Passat(), false);
        Assert.Equal("inland", antwort.Profil);
        Assert.Equal(2, a2.Aufrufe);
    }

    [Fact]   // NIE wiederholen: Zeitueberschreitung (/vergleich zaehlt serverseitig schon), 4xx, 500
    public async Task Zeitueberschreitung_4xx_und_500_werden_nicht_wiederholt()
    {
        var (d, a) = Dienst();
        a.Ausnahme = new TaskCanceledException("Zeit um");
        var ex = await Assert.ThrowsAsync<DienstFehler>(() => d.VergleichAsync(Passat(), false));
        Assert.True(ex.KeineVerbindung);
        Assert.Equal(1, a.Aufrufe);

        foreach (int status in new[] { 400, 404, 429, 500 })
        {
            var (d2, a2) = Dienst();
            a2.Antwort = _ => Json(status, "{}");
            var ex2 = await Assert.ThrowsAsync<DienstFehler>(() => d2.VergleichAsync(Passat(), false));
            Assert.Equal(status, ex2.Status);
            Assert.Equal(1, a2.Aufrufe);
        }
        Assert.True(AutoSchnellDienst.Wiederholbar(503));
        Assert.True(AutoSchnellDienst.Wiederholbar(529));
        Assert.False(AutoSchnellDienst.Wiederholbar(500));
        Assert.False(AutoSchnellDienst.Wiederholbar(530));
        Assert.False(AutoSchnellDienst.Wiederholbar(401));
    }

    [Fact]   // Paket 2 (A11): waehrend der Anfrage neu verbunden — die 401 zum ALTEN Schluessel wirft nicht raus
    public async Task Alte_401_zaehlt_nicht_als_Verbindung_verloren()
    {
        string schluessel = "alt";
        var (d, a) = DienstMit(() => schluessel);
        a.Antwort = _ => { schluessel = "neu"; return Json(401, """{"detail":"Dieses Programm ist nicht (mehr) verbunden"}"""); };
        var ex = await Assert.ThrowsAsync<DienstFehler>(() => d.VergleichAsync(Passat(), false));
        Assert.Equal(401, ex.Status);
        Assert.True(ex.Veraltet);
        Assert.False(ex.NichtVerbunden);
        Assert.Equal("alt", a.Letzte!.Headers.GetValues("X-Werkzeug-Schluessel").Single());
        // derselbe Schluessel -> wie gehabt "nicht verbunden"
        var (d2, a2) = Dienst("gleich");
        a2.Antwort = _ => Json(401, "{}");
        var ex2 = await Assert.ThrowsAsync<DienstFehler>(() => d2.StatusAsync());
        Assert.True(ex2.NichtVerbunden);
        Assert.False(ex2.Voruebergehend);
    }

    [Fact]   // A14: die Ursache eines Netzfehlers ist im Protokoll nachvollziehbar
    public void Ursache_nennt_Typ_Meldung_und_innere_Ausnahme()
    {
        var ex = new HttpRequestException("Name nicht aufgelöst", new System.Net.Sockets.SocketException(11001));
        string u = AutoSchnellDienst.Ursache(ex);
        Assert.StartsWith("HttpRequestException: Name nicht aufgelöst → SocketException: ", u);
        Assert.False(new DienstFehler(402, "x").Voruebergehend);
        Assert.False(new DienstFehler(403, "x").Voruebergehend);
        Assert.True(new DienstFehler(429, "x").Voruebergehend);
    }

    [Fact]
    public async Task Verbinden_schickt_Code_und_PC()
    {
        var (d, a) = Dienst(schluessel: null);
        Assert.False(d.Verbunden);
        a.Antwort = _ => Json(200, """{"schluessel":"neu","konto":"10002-1","name":"Max Sucher","firma":"Autohaus"}""");
        var r = await d.VerbindenAsync("482913", "BUERO-PC", "abc");
        Assert.Equal(new VerbindenAntwort("neu", "10002-1", "Max Sucher", "Autohaus"), r);
        Assert.Equal("https://app.example.test/api/werkzeuge/autopointer-vergleich/verbinden", a.Letzte!.RequestUri!.ToString());
        Assert.False(a.Letzte.Headers.Contains("X-Werkzeug-Schluessel"));
        using var doc = JsonDocument.Parse(a.Inhalt!);
        Assert.Equal("482913", doc.RootElement.GetProperty("code").GetString());
        Assert.Equal("BUERO-PC", doc.RootElement.GetProperty("pc_name").GetString());
    }

    [Fact]
    public async Task Status_liest_Konto()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """{"ok":true,"konto":"10002-1","name":"Max","firma":"AH","pc_name":"PC","abo_bis":"2026-12-31T00:00:00+00:00"}""");
        var s = await d.StatusAsync();
        Assert.Equal(("10002-1", "Max", "AH", "PC"), (s.Konto, s.Name, s.Firma, s.PcName));
        Assert.StartsWith("2026-12-31", s.AboBis);
        Assert.Equal(HttpMethod.Get, a.Letzte!.Method);
    }

    [Fact]
    public void PC_Kennung_ist_ein_Streuwert()
    {
        var (name, kennung) = AutoSchnellDienst.PcAngaben();
        Assert.Equal(Environment.MachineName, name);
        Assert.Matches("^[0-9a-f]{32}$", kennung);
        Assert.Equal(kennung, AutoSchnellDienst.PcAngaben().Kennung);
    }

    [Fact]
    public void Schluessel_wird_verschluesselt_und_gilt_nur_fuer_seinen_Server()
    {
        var e = new Einstellungen { Server = "https://app.example.test" };
        Assert.Null(e.Schluessel());
        e.SchluesselSetzen("streng-geheim", "Max (10002-1) · AH");
        Assert.DoesNotContain("streng-geheim", e.SchluesselGeschuetzt);
        Assert.Equal("streng-geheim", e.Schluessel());
        e.Server = "https://anderer.example.test";
        Assert.Null(e.Schluessel());                 // fuer einen anderen Server: neu verbinden
        e.Server = "https://app.example.test";
        e.SchluesselSetzen(null, null);
        Assert.Null(e.Schluessel());
        Assert.Null(e.VerbundenAls);
    }

    [Fact]   // Pruefung 05.10.2026 (Paket 3, F6): Schluessel() entschluesselt nicht mehr bei jedem Aufruf (DPAPI ~6x/s)
    public void Schluessel_Zwischenspeicher_folgt_dem_gespeicherten_Wert()
    {
        var e = new Einstellungen { Server = "https://app.example.test" };
        e.SchluesselSetzen("erster", "Max");
        Assert.Equal("erster", e.Schluessel());
        Assert.Equal("erster", e.Schluessel());
        // ein anderer gespeicherter Wert (z. B. aus der Datei nachgeladen) -> neu entschluesselt, nicht der alte
        var andere = new Einstellungen { Server = "https://app.example.test" };
        andere.SchluesselSetzen("zweiter", "Max");
        e.SchluesselGeschuetzt = andere.SchluesselGeschuetzt;
        Assert.Equal("zweiter", e.Schluessel());
        // kaputter Wert -> null, und nach SchluesselSetzen wieder frisch
        e.SchluesselGeschuetzt = Convert.ToBase64String(new byte[] { 1, 2, 3 });
        Assert.Null(e.Schluessel());
        e.SchluesselSetzen("dritter", "Max");
        Assert.Equal("dritter", e.Schluessel());
        e.Server = "https://anderer.example.test";
        Assert.Null(e.Schluessel());
        e.Server = "https://app.example.test";
        Assert.Equal("dritter", e.Schluessel());
        // 2.000 Aufrufe (≈ 6 je Sekunde ueber 5 Minuten) muessen aus dem Zwischenspeicher kommen
        var uhr = System.Diagnostics.Stopwatch.StartNew();
        for (int i = 0; i < 2000; i++) e.Schluessel();
        Assert.True(uhr.ElapsedMilliseconds < 500, $"{uhr.ElapsedMilliseconds} ms fuer 2.000 Aufrufe");
    }

    [Fact]   // Paket 3 (F4): Vorwaermen schickt GET /api/health, hoechstens einmal je Minute, Fehler egal
    public async Task Vorwaermen_ruft_health_hoechstens_einmal_je_Minute()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """{"ok":true}""");
        d.Vorwaermen();
        d.Vorwaermen();
        d.Vorwaermen();
        for (int i = 0; i < 100 && a.Aufrufe == 0; i++) await Task.Delay(20);
        await Task.Delay(50);
        Assert.Equal(1, a.Aufrufe);
        Assert.Equal("https://app.example.test/api/health", a.Letzte!.RequestUri!.ToString());
        Assert.Equal(HttpMethod.Get, a.Letzte.Method);
        // Fehler beim Vorwaermen stoeren nicht
        var (d2, a2) = Dienst();
        a2.Ausnahme = new HttpRequestException("weg");
        d2.Vorwaermen();
        for (int i = 0; i < 100 && a2.Aufrufe == 0; i++) await Task.Delay(20);
        Assert.Equal(1, a2.Aufrufe);
    }

    [Theory]   // Befund 03.10.2026: Chef 10002 ohne Namen -> "Verbunden:  (10002) · Norden Autoankauf"
    [InlineData("Max Muster", "10002-1", "AH", "Max Muster (10002-1) · AH")]
    [InlineData("", "10002", "Norden Autoankauf", "Konto 10002 · Norden Autoankauf")]
    [InlineData(null, "10002", "", "Konto 10002")]
    [InlineData(" Max ", "", "AH", "Max · AH")]
    public void Kontozeile_ohne_leeren_Namen(string? name, string konto, string firma, string erwartet) =>
        Assert.Equal(erwartet, AutoSchnellDienst.KontoText(name, konto, firma));
}
