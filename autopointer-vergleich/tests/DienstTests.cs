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

    [Fact]   // Befund Ahmad 09.10.2026 (1.5.12): was der zweite Blick anders las, geht als fahrzeug.alternativen mit
    public async Task Vergleich_schickt_Alternativen_des_zweiten_Blicks()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """{"links":[],"hinweise":[],"profil":"inland"}""");
        var f = Passat();
        f.AlternativenMarkeModell = new() { "VW Passat Variant 2", "VW Passat Varlant" };
        f.AlternativenKraftstoff = new() { "DieseI" };
        f.AlternativenInseratId = new();                                 // leer: Schluessel faellt weg
        await d.VergleichAsync(f, probelauf: false);

        using var doc = JsonDocument.Parse(a.Inhalt!);
        var fz = doc.RootElement.GetProperty("fahrzeug");
        var alt = fz.GetProperty("alternativen");
        Assert.Equal(JsonValueKind.Object, alt.ValueKind);
        Assert.Equal(new[] { "VW Passat Variant 2", "VW Passat Varlant" },
                     alt.GetProperty("marke_modell_text").EnumerateArray().Select(x => x.GetString()));
        Assert.Equal(new[] { "DieseI" }, alt.GetProperty("kraftstoff").EnumerateArray().Select(x => x.GetString()));
        Assert.False(alt.TryGetProperty("inserat_id", out _));
        Assert.Equal(new[] { "marke_modell_text", "kraftstoff" }, alt.EnumerateObject().Select(p => p.Name));
        // die Hauptwerte bleiben, wie sie sind
        Assert.Equal("VW Passat Variant", fz.GetProperty("marke_modell_text").GetString());
        Assert.Equal("Diesel", fz.GetProperty("kraftstoff").GetString());
    }

    [Fact]   // 1.5.12: nichts Abweichendes gelesen -> kein "alternativen" (auch kein null, kein leeres Objekt)
    public async Task Vergleich_ohne_Alternativen_laesst_das_Feld_weg()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """{"links":[],"hinweise":[],"profil":"inland"}""");
        var f = Passat();
        f.AlternativenKraftstoff = new() { "", "   " };                    // nur Leeres zaehlt nicht
        await d.VergleichAsync(f, probelauf: false);
        using var doc = JsonDocument.Parse(a.Inhalt!);
        Assert.False(doc.RootElement.GetProperty("fahrzeug").TryGetProperty("alternativen", out _));
        Assert.DoesNotContain("alternativen", a.Inhalt);
    }

    [Fact]   // 1.5.12: hoechstens 3 je Feld, je hoechstens 160 Zeichen, keine Doppelten
    public void Alternativen_in_der_Nutzlast_begrenzt()
    {
        var f = Passat();
        f.AlternativenMarkeModell = new() { new string('x', 200), "a", "a", "b", "c", "d" };
        var alt = Assert.IsType<Dictionary<string, List<string>>>(AutoSchnellDienst.Nutzlast(f)["alternativen"]);
        var mm = alt["marke_modell_text"];
        Assert.Equal(3, mm.Count);
        Assert.Equal(160, mm[0].Length);
        Assert.Equal(new[] { "a", "b" }, mm.Skip(1));
        Assert.Single(alt);
    }

    [Fact]   // 1.5.8 (Wunsch Ahmad 08.10.2026): Vorgangsnummer und "die Erweiterung oeffnet"
    public async Task Vergleich_liest_Vorgangsnummer_und_fragt_nach()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """
            {"links":[{"portal":"mobile.de","url":"https://suchen.mobile.de/x?ms=1"}],"hinweise":[],"profil":"inland",
             "vorgang_id":"0f8c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b","ueber_helfer":true}
            """);
        var antwort = await d.VergleichAsync(Passat(), probelauf: false);
        Assert.Equal("0f8c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b", antwort.VorgangId);
        Assert.True(antwort.UeberHelfer);

        // kaputte Nummer -> kein Vorgang, und dann auch nie "ueber die Erweiterung"
        a.Antwort = _ => Json(200, """{"links":[],"hinweise":[],"profil":"inland","vorgang_id":"../x","ueber_helfer":true}""");
        var kaputt = await d.VergleichAsync(Passat(), probelauf: false);
        Assert.Null(kaputt.VorgangId);
        Assert.False(kaputt.UeberHelfer);
    }

    [Theory]   // Pruefung 08.10.2026 (1.5.9, A): helfer_browser = der Browser, in dem die Erweiterung verbunden ist
    [InlineData("\"chrome\"", "chrome")]
    [InlineData("\"Edge\"", "edge")]
    [InlineData("\"\"", "")]
    [InlineData("\"firefox\"", "")]
    [InlineData("null", "")]
    public async Task Vergleich_liest_den_Browser_der_Erweiterung(string wert, string erwartet)
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, $$"""
            {"links":[{"portal":"mobile.de","url":"https://suchen.mobile.de/x?ms=1"}],"hinweise":[],"profil":"inland",
             "vorgang_id":"0f8c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b","ueber_helfer":true,"helfer_browser":{{wert}}}
            """);
        var antwort = await d.VergleichAsync(Passat(), probelauf: false);
        Assert.Equal(erwartet, antwort.HelferBrowser);
    }

    private const string VorgangNr = "0f8c1a2b-3c4d-4e5f-8a9b-0c1d2e3f4a5b";

    [Fact]   // 1.5.11 (Wunsch Ahmad 08.10.2026 abends): "Vertrag" fragt, ob das Inserat schon gelesen ist — nur Lesen
    public async Task Inserat_gelesen_fragt_den_Server()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """{"gelesen":true,"quelle":"browser"}""");
        const string url = "https://suchen.mobile.de/fahrzeuge/details.html?id=487654321";
        Assert.True(await d.InseratGelesenAsync(url));
        Assert.Equal(HttpMethod.Get, a.Letzte!.Method);
        Assert.Equal("https://app.example.test/api/werkzeuge/autopointer-vergleich/inserat-gelesen?url="
                     + Uri.EscapeDataString(url), a.Letzte.RequestUri!.AbsoluteUri);
        a.Antwort = _ => Json(200, """{"gelesen":false}""");
        Assert.False(await d.InseratGelesenAsync(url));
        a.Antwort = _ => Json(500, """{"detail":"x"}""");
        Assert.Null(await d.InseratGelesenAsync(url));
    }

    [Fact]
    public async Task Vergleich_liest_hat_helfer()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """{"links":[],"hinweise":[],"profil":"inland","hat_helfer":true}""");
        Assert.True((await d.VergleichAsync(Passat(), probelauf: false)).HatHelfer);
        a.Antwort = _ => Json(200, """{"links":[],"hinweise":[],"profil":"inland"}""");
        Assert.False((await d.VergleichAsync(Passat(), probelauf: false)).HatHelfer);
    }

    [Fact]   // Pruefung 08.10.2026 (1.5.9, A): POST …/vorgang/<id>/selbst — der Server entscheidet, wer oeffnet
    public async Task Vorgang_selbst_beanspruchen()
    {
        var (d, a) = Dienst();
        a.Antwort = _ => Json(200, """{"selbst":true}""");
        Assert.True(await d.VorgangSelbstAsync(VorgangNr));
        Assert.Equal(HttpMethod.Post, a.Letzte!.Method);
        Assert.Equal($"https://app.example.test/api/werkzeuge/autopointer-vergleich/vorgang/{VorgangNr}/selbst",
                     a.Letzte.RequestUri!.ToString());
        Assert.Equal("geheim", a.Letzte.Headers.GetValues("X-Werkzeug-Schluessel").Single());
        Assert.Null(a.Inhalt);                                              // kein Inhalt
        a.Antwort = _ => Json(200, """{"selbst":false}""");
        Assert.False(await d.VorgangSelbstAsync(VorgangNr));                // die Erweiterung hat ihn
        a.Antwort = _ => Json(404, """{"detail":"Vorgang nicht gefunden oder abgelaufen."}""");
        Assert.False(await d.VorgangSelbstAsync(VorgangNr));                // unbekannt/abgelaufen: nichts tun
        a.Antwort = _ => Json(401, """{"detail":"Dieses Programm ist nicht (mehr) verbunden"}""");
        Assert.False(await d.VorgangSelbstAsync(VorgangNr));                // nicht oeffnen (Vorgangsseite ist offen)
    }

    [Fact]   // 1.5.9 (A): Netzfehler/5xx -> nach 1,5 s genau ein zweiter Versuch, dann null (= nicht oeffnen, Hinweis)
    public async Task Vorgang_selbst_bei_Netzfehler_einmal_nach_1_5_s_wiederholt()
    {
        var pausen = new List<TimeSpan>();
        var a = new Attrappe();
        var d = new AutoSchnellDienst("https://app.example.test/", () => "geheim", a, t => { pausen.Add(t); return Task.CompletedTask; });
        a.Antwort = _ => a.Aufrufe == 1 ? Json(503, "<html>Service Unavailable</html>") : Json(200, """{"selbst":true}""");
        Assert.True(await d.VorgangSelbstAsync(VorgangNr));
        Assert.Equal(2, a.Aufrufe);
        Assert.Equal(new[] { TimeSpan.FromMilliseconds(1500) }, pausen);

        a.Aufrufe = 0;
        pausen.Clear();
        a.Antwort = _ => Json(502, "");
        Assert.Null(await d.VorgangSelbstAsync(VorgangNr));
        Assert.Equal(2, a.Aufrufe);                                          // kein dritter (auch kein interner) Versuch
        Assert.Single(pausen);

        a.Aufrufe = 0;
        a.Ausnahme = new HttpRequestException("weg");
        Assert.Null(await d.VorgangSelbstAsync(VorgangNr));
        Assert.Equal(2, a.Aufrufe);
    }

    [Theory]
    [InlineData(402, """{"detail":"Kein aktives AutoSchnell-Abo – das Programm ist gesperrt."}""", "Kein aktives AutoSchnell-Abo")]
    [InlineData(401, """{"detail":"Dieses Programm ist nicht (mehr) verbunden"}""", "nicht (mehr) verbunden")]
    [InlineData(403, """{"detail":"Für dein Konto nicht freigeschaltet."}""", "nicht freigeschaltet")]
    [InlineData(403, "{}", "nicht freigeschaltet")]
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
        Assert.Equal(status == 403, ex.Gesperrt);
    }

    [Theory]   // Pruefung 08.10.2026 (1.5.9, G): 403 OHNE JSON kommt von Cloudflare/Firewall — voruebergehend, keine Sperre
    [InlineData("<html><title>Attention Required! | Cloudflare</title></html>")]
    [InlineData("")]
    [InlineData("Forbidden")]
    public async Task Cloudflare_403_ohne_JSON_ist_voruebergehend(string text)
    {
        var (d, a) = Dienst();
        a.Antwort = _ => new HttpResponseMessage(HttpStatusCode.Forbidden) { Content = new StringContent(text) };
        var ex = await Assert.ThrowsAsync<DienstFehler>(() => d.VergleichAsync(Passat(), false));
        Assert.Equal(403, ex.Status);
        Assert.True(ex.OhneJson);
        Assert.False(ex.Gesperrt);
        Assert.True(ex.Voruebergehend);
        Assert.Contains("kurz nicht erreichbar", ex.Message);
        Assert.DoesNotContain("freigeschaltet", ex.Message);
        Assert.Equal(1, a.Aufrufe);                     // in SendeAsync nicht wiederholt (das macht der Ueberwacher in 5 s)
        Assert.False(AutoSchnellDienst.IstJsonObjekt(text));
        Assert.True(AutoSchnellDienst.IstJsonObjekt("""{"detail":"x"}"""));
    }

    [Fact]   // 1.5.9 (G): die Meldungen versprechen keinen Wiederholversuch mehr — das sagt der Aufrufer, wenn es stimmt
    public void Meldungen_versprechen_nichts()
    {
        foreach (int status in new[] { 403, 502, 503, 504, 522 })
            Assert.DoesNotContain("erneut versucht", AutoSchnellDienst.Meldung("<html></html>", status));
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
