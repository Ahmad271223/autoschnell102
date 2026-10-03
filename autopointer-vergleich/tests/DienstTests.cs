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

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage r, CancellationToken t)
        {
            Letzte = r;
            Inhalt = r.Content == null ? null : await r.Content.ReadAsStringAsync(t);
            if (Ausnahme != null) throw Ausnahme;
            return Antwort(r);
        }
    }

    private static HttpResponseMessage Json(int status, string text) =>
        new((HttpStatusCode)status) { Content = new StringContent(text, Encoding.UTF8, "application/json") };

    private static (AutoSchnellDienst, Attrappe) Dienst(string? schluessel = "geheim")
    {
        var a = new Attrappe();
        return (new AutoSchnellDienst("https://app.example.test/", () => schluessel, a), a);
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
             "vorab":{"status":"laeuft","hinweis":""}}
            """);
        var f = Passat();
        Zuordner.Zuordnen(f, Kat);
        var antwort = await d.VergleichAsync(f, probelauf: false);

        Assert.Equal(HttpMethod.Post, a.Letzte!.Method);
        Assert.Equal("https://app.example.test/api/werkzeuge/autopointer-vergleich/vergleich", a.Letzte.RequestUri!.ToString());
        Assert.Equal("geheim", a.Letzte.Headers.GetValues("X-Werkzeug-Schluessel").Single());
        using var doc = JsonDocument.Parse(a.Inhalt!);
        var fz = doc.RootElement.GetProperty("fahrzeug");
        Assert.Equal("VW", fz.GetProperty("marke").GetString());
        Assert.Equal("Passat Variant", fz.GetProperty("modell").GetString());
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
        Assert.Contains("Keine Verbindung", ex.Message);
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
}
