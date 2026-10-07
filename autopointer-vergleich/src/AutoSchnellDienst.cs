using System.Net.Http.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Win32;

namespace AutoPointerVergleich;

/// <summary>Fehler vom AutoSchnell-Server (Status) oder keine Verbindung (Status 0).</summary>
internal sealed class DienstFehler : Exception
{
    public int Status { get; }
    public DienstFehler(int status, string meldung) : base(meldung) => Status = status;

    /// <summary>Pruefung 05.10.2026 (Paket 2, A11): die 401 kam zu einem Schluessel, der inzwischen ersetzt wurde
    /// (waehrend der Anfrage neu verbunden) — dann gilt sie nicht als "Verbindung verloren".</summary>
    public bool Veraltet { get; init; }

    /// <summary>401: Schluessel ungueltig (anderer PC verbunden, getrennt, Konto gesperrt).</summary>
    public bool NichtVerbunden => Status == 401 && !Veraltet;
    /// <summary>402: kein aktives Abo.</summary>
    public bool KeinAbo => Status == 402;
    public bool KeineVerbindung => Status == 0;
    /// <summary>Paket 2 (A8): voruebergehend — keine Verbindung, Server ueberlastet (5xx), zu viele Anfragen (429).
    /// Lizenzpruefung: still bleiben und spaeter erneut versuchen, kein rotes Symbol.</summary>
    public bool Voruebergehend => Status == 0 || Status == 429 || Status >= 500;
}

internal sealed record VerbindenAntwort(string Schluessel, string Konto, string Name, string Firma);
/// <param name="AktuelleVersion">Pruefbericht 03.10.2026 (Nr. 4): die Version, die AutoSchnell gerade zum
/// Herunterladen anbietet (null bei einem Server ohne diese Angabe) — ist sie neuer, sagt das Programm es.</param>
internal sealed record StatusAntwort(string Konto, string Name, string Firma, string PcName, string? AboBis,
                                     string? AktuelleVersion = null, string? ProgrammName = null);
internal sealed record Vergleich(string Portal, string Url);

/// <param name="ErkanntMarke">Seit 1.4.0: Marke/Modell erkennt der SERVER (Katalognamen zur Anzeige);
/// null bei einem Server ohne Erkennung.</param>
internal sealed record VergleichAntwort(IReadOnlyList<Vergleich> Links, IReadOnlyList<string> Hinweise, string Profil,
                                        string? InseratUrl = null, string VorabStatus = "", string VorabHinweis = "",
                                       string? ErkanntMarke = null, string? ErkanntModell = null, bool MarkeErkannt = true,
                                       IReadOnlyList<string>? Melden = null, bool InseratImBrowser = false,
                                       string? VorgangId = null, bool UeberHelfer = false);

/// <summary>Was der Ueberwacher vom Server braucht (in Tests eine Attrappe).</summary>
internal interface IVergleichsDienst
{
    bool Verbunden { get; }
    Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf);
    /// <summary>Paket 3 (F4): Verbindung vorwaermen, sobald eine Aenderung in AutoPointer erkannt ist — die Anfrage
    /// trifft dann auf eine offene Verbindung (kein DNS/TLS-Aufbau mehr auf dem kritischen Weg). Darf nichts tun.</summary>
    void Vorwaermen() { }
    /// <summary>1.5.8 (Vorgangsnummer): hat die Browser-Erweiterung den Vorgang uebernommen? null = nicht pruefbar.</summary>
    Task<bool?> VorgangUebernommenAsync(string vorgangId) => Task.FromResult<bool?>(null);
}

/// <summary>Verbindung zu AutoSchnell (Wunsch Ahmad 03.10.2026): das Programm arbeitet nur
/// verbunden. Ein 6-stelliger Code aus der App ergibt einen Programm-Schluessel (ein PC je
/// Konto); jeder Vergleich geht ueber den Server — der prueft Abo und Freigabe und baut die
/// Links mit den Vergleichsregeln der Firma.</summary>
internal sealed class AutoSchnellDienst : IVergleichsDienst
{
    public const string Werkzeug = "autopointer-vergleich";
    private const string SchluesselKopf = "X-Werkzeug-Schluessel";
    /// <summary>Pruefbericht 03.10.2026 (Nr. 17): so lange darf ein Vergleich hoechstens dauern. Der Server antwortet
    /// sonst in Sekundenbruchteilen (das Inserat liest er im Hintergrund) — haengt er, ist nach 8 s Schluss und das
    /// naechste angeklickte Auto kommt dran, statt 15 s lang alles zu blockieren.</summary>
    internal static TimeSpan VergleichFrist { get; set; } = TimeSpan.FromSeconds(8);

    /// <summary>Pruefbericht 03.10.2026 (Nr. 15): nur diese Seiten oeffnet das Programm — Vergleiche bei mobile.de
    /// und AutoScout24, Inserate zusaetzlich bei Kleinanzeigen. Alles andere (auch von einem falsch eingestellten
    /// oder fremden Server) wird verworfen.</summary>
    internal static readonly string[] VergleichsSeiten = { "mobile.de", "autoscout24.de" };
    internal static readonly string[] InseratSeiten = { "mobile.de", "autoscout24.de", "kleinanzeigen.de" };

    internal static bool ErlaubteAdresse(string? url, IEnumerable<string> seiten) =>
        Uri.TryCreate(url, UriKind.Absolute, out var u) && u.Scheme == Uri.UriSchemeHttps
        && string.IsNullOrEmpty(u.UserInfo)
        && seiten.Any(h => u.Host.Equals(h, StringComparison.OrdinalIgnoreCase)
                           || u.Host.EndsWith("." + h, StringComparison.OrdinalIgnoreCase));

    private readonly HttpClient _http;
    private readonly Func<string?> _schluessel;
    private readonly Func<TimeSpan, Task> _warte;

    public string Server { get; }

    /// <summary>Pruefung 05.10.2026 (Paket 2, A3): so lange wartet der eine Wiederholversuch bei Netzfehlern/5xx.</summary>
    internal static readonly TimeSpan WiederholPause = TimeSpan.FromSeconds(1);

    /// <param name="warte">Wartezeit vor dem Wiederholversuch (Tests: sofort).</param>
    public AutoSchnellDienst(string server, Func<string?> schluessel, HttpMessageHandler? handler = null,
                             Func<TimeSpan, Task>? warte = null)
    {
        Server = server.TrimEnd('/');
        _schluessel = schluessel;
        _warte = warte ?? (t => Task.Delay(t));
        // Paket 3 (F4): offene Verbindungen 5 Minuten behalten (Standard 1 min) — zwischen zwei angeklickten Autos
        // liegen oft Minuten, danach kostete jede Anfrage wieder DNS + TLS-Aufbau (0,2-0,5 s)
        _http = handler == null
            ? new HttpClient(new SocketsHttpHandler { PooledConnectionIdleTimeout = TimeSpan.FromMinutes(5) })
            : new HttpClient(handler);
        _http.Timeout = TimeSpan.FromSeconds(15);
        _http.DefaultRequestHeaders.UserAgent.ParseAdd($"AutoSchnell-Vergleich/{Application.ProductVersion.Split('+')[0]}");
    }

    public bool Verbunden => !string.IsNullOrEmpty(_schluessel());

    private long _zuletztVorgewaermt = long.MinValue / 2;
    private int _vorwaermenLaeuft;

    /// <summary>Paket 3 (F4): GET /api/health im Hintergrund, hoechstens alle 60 s, Fehler egal — danach steht die
    /// Verbindung im Pool, wenn /vergleich kommt.</summary>
    public void Vorwaermen()
    {
        long jetzt = Environment.TickCount64;
        if (jetzt - _zuletztVorgewaermt < 60_000) return;
        if (Interlocked.Exchange(ref _vorwaermenLaeuft, 1) == 1) return;
        _zuletztVorgewaermt = jetzt;
        _ = Task.Run(async () =>
        {
            try
            {
                using var abbruch = new CancellationTokenSource(TimeSpan.FromSeconds(5));
                using var a = new HttpRequestMessage(HttpMethod.Get, Server + "/api/health");
                using var r = await _http.SendAsync(a, HttpCompletionOption.ResponseHeadersRead, abbruch.Token);
            }
            catch (Exception) { /* nur vorwaermen — der echte Aufruf meldet Fehler */ }
            finally { Interlocked.Exchange(ref _vorwaermenLaeuft, 0); }
        });
    }

    private string Url(string pfad) => $"{Server}/api/werkzeuge/{Werkzeug}/{pfad}";

    /// <summary>Baut die Anfrage NEU je Versuch (eine HttpRequestMessage laesst sich nur einmal senden) und merkt den
    /// Schluessel, mit dem sie rausging (A11: eine 401 zaehlt nur, wenn er noch der aktuelle ist).</summary>
    private HttpRequestMessage Anfrage(HttpMethod methode, string pfad, out string? gesendet, object? inhalt = null)
    {
        var a = new HttpRequestMessage(methode, Url(pfad));
        gesendet = _schluessel();
        if (!string.IsNullOrEmpty(gesendet)) a.Headers.Add(SchluesselKopf, gesendet);
        if (inhalt != null) a.Content = JsonContent.Create(inhalt, options: Json);
        return a;
    }

    internal static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNameCaseInsensitive = true,
    };

    /// <summary>Pruefung 05.10.2026 (Paket 2, A3): lohnt bei diesem Status GENAU EIN Wiederholversuch? Nur, wenn
    /// der Server die Anfrage sicher nicht verarbeitet hat — Gateway/Ueberlast (502/503/504) und Cloudflare (52x).
    /// Nie bei 4xx und nie bei 500 (Serverfehler mitten in der Verarbeitung). (rein, fuer Tests)</summary>
    internal static bool Wiederholbar(int status) => status is 502 or 503 or 504 or (>= 520 and <= 529);

    private async Task<JsonElement> SendeAsync(HttpMethod methode, string pfad, object? inhalt = null, TimeSpan? frist = null)
    {
        for (int versuch = 1; ; versuch++)
        {
            string? gesendet;
            HttpResponseMessage antwort;
            using var abbruch = frist is { } f ? new CancellationTokenSource(f) : null;
            try { antwort = await _http.SendAsync(Anfrage(methode, pfad, out gesendet, inhalt), abbruch?.Token ?? CancellationToken.None); }
            catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException or OperationCanceledException)
            {
                // A14: die Ursache ins Protokoll (Typ, Meldung, innere Ausnahme) — "Keine Verbindung" allein half nicht weiter
                Protokoll.Schreibe($"Netzfehler ({pfad}, {versuch}. Versuch): {Ursache(ex)}");
                // Zeitueberschreitung NIE wiederholen: /vergleich startet serverseitig den Vorab-Abruf und schreibt einen
                // Eintrag — eine Wiederholung zaehlte doppelt. Nur ein echter Verbindungsfehler (kein Antwortbeginn) einmal.
                if (ex is HttpRequestException && versuch == 1)
                {
                    await _warte(WiederholPause);
                    continue;
                }
                if (abbruch?.IsCancellationRequested == true)
                    throw new DienstFehler(0, "AutoSchnell antwortet gerade nicht – beim nächsten Auto wird es erneut versucht.");
                throw new DienstFehler(0, "Keine Verbindung zu AutoSchnell – bitte Internet prüfen.");
            }
            using (antwort)
            {
                string text = await antwort.Content.ReadAsStringAsync();
                if (antwort.IsSuccessStatusCode)
                {
                    try { return JsonDocument.Parse(text).RootElement.Clone(); }
                    catch (JsonException) { throw new DienstFehler(0, "Unerwartete Antwort von AutoSchnell."); }
                }
                int status = (int)antwort.StatusCode;
                if (Wiederholbar(status) && versuch == 1)
                {
                    Protokoll.Schreibe($"AutoSchnell antwortet mit {status} ({pfad}) – ein Wiederholversuch in {WiederholPause.TotalSeconds:0} s.");
                    await _warte(WiederholPause);
                    continue;
                }
                // A11: 401 zu einem Schluessel, der inzwischen ersetzt wurde (waehrend der Anfrage neu verbunden)?
                bool veraltet = status == 401 && !string.IsNullOrEmpty(gesendet) && gesendet != _schluessel();
                throw new DienstFehler(status, veraltet
                    ? "Antwort gehört zu einer früheren Verbindung – bitte noch einmal versuchen."
                    : Meldung(text, status)) { Veraltet = veraltet };
            }
        }
    }

    /// <summary>Typ + Meldung der Ausnahme samt innerer Ausnahmen (A14), z. B.
    /// "HttpRequestException: Name konnte nicht aufgelöst werden → SocketException: …".</summary>
    internal static string Ursache(Exception ex)
    {
        var teile = new List<string>();
        for (Exception? e = ex; e != null && teile.Count < 4; e = e.InnerException)
            teile.Add($"{e.GetType().Name}: {e.Message}");
        return string.Join(" → ", teile);
    }

    /// <summary>"Max Muster (10002-1) · Firma". Chef-Konten haben oft keinen Namen — dann
    /// "Konto 10002 · Firma" statt einer Zeile, die mit Leerzeichen beginnt.</summary>
    internal static string KontoText(string? name, string? konto, string? firma)
    {
        name = name?.Trim();
        konto = konto?.Trim();
        string wer = (string.IsNullOrEmpty(name), string.IsNullOrEmpty(konto)) switch
        {
            (false, false) => $"{name} ({konto})",
            (false, true) => name!,
            (true, false) => $"Konto {konto}",
            _ => "Konto",
        };
        return string.IsNullOrWhiteSpace(firma) ? wer : $"{wer} · {firma.Trim()}";
    }

    internal static string Meldung(string text, int status)
    {
        try
        {
            using var doc = JsonDocument.Parse(text);
            if (doc.RootElement.TryGetProperty("detail", out var d))
            {
                // Standard-404 von FastAPI: die Route gibt es auf diesem Server (noch) nicht
                if (status == 404 && d.ValueKind == JsonValueKind.String && d.GetString() == "Not Found")
                    return "AutoSchnell kennt dieses Programm noch nicht – der Server ist noch nicht aktualisiert. Bitte später erneut versuchen.";
                if (d.ValueKind == JsonValueKind.String) return d.GetString() ?? "";
                if (d.ValueKind == JsonValueKind.Array) return "Ungültige Fahrzeugdaten.";
            }
        }
        catch (JsonException) { }
        return status switch
        {
            401 => "Programm nicht verbunden – bitte mit einem Code aus AutoSchnell verbinden.",
            402 => "Kein aktives AutoSchnell-Abo – das Programm ist gesperrt.",
            403 => "Für dein Konto nicht freigeschaltet.",
            429 => "Zu viele Anfragen – bitte kurz warten.",
            // Paket 2 (A3): Gateway/Ueberlast/Cloudflare — kein Fehler des Suchers, geht gleich wieder
            502 or 503 or 504 or (>= 520 and <= 529) => "AutoSchnell ist kurz nicht erreichbar – wird gleich erneut versucht.",
            _ => $"AutoSchnell antwortet mit Fehler {status}.",
        };
    }

    public async Task<VerbindenAntwort> VerbindenAsync(string code, string pcName, string pcKennung)
    {
        var e = await SendeAsync(HttpMethod.Post, "verbinden", new { code, pc_name = pcName, pc_kennung = pcKennung });
        return new VerbindenAntwort(Text(e, "schluessel"), Text(e, "konto"), Text(e, "name"), Text(e, "firma"));
    }

    public async Task<StatusAntwort> StatusAsync()
    {
        var e = await SendeAsync(HttpMethod.Get, "status");
        return new StatusAntwort(Text(e, "konto"), Text(e, "name"), Text(e, "firma"), Text(e, "pc_name"),
                                 e.TryGetProperty("abo_bis", out var b) && b.ValueKind == JsonValueKind.String ? b.GetString() : null,
                                 Text(e, "aktuelle_version") is { Length: > 0 } av ? av : null,
                                 Text(e, "programm_name") is { Length: > 0 } pn ? pn : null);
    }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 12): hat die AutoSchnell-App das Auto wirklich uebernommen? Die
    /// Web-App meldet den Start (Kennung im Link) an den Server; ohne Meldung oeffnet das Programm den Browser.
    /// Fehler zaehlen als "nicht bestaetigt".</summary>
    /// <summary>1.5.8: hat die Erweiterung den Vorgang uebernommen (GET vorgang/&lt;id&gt;)? null = nicht pruefbar.</summary>
    public async Task<bool?> VorgangUebernommenAsync(string vorgangId)
    {
        try
        {
            var e = await SendeAsync(HttpMethod.Get, "vorgang/" + Uri.EscapeDataString(vorgangId),
                                     frist: TimeSpan.FromSeconds(4));
            return e.TryGetProperty("uebernommen", out var u) && u.ValueKind == JsonValueKind.True;
        }
        catch (DienstFehler) { return null; }
    }

    public async Task<bool> AppStartBestaetigtAsync(string startKennung)
    {
        try
        {
            var e = await SendeAsync(HttpMethod.Get, "app-start/" + Uri.EscapeDataString(startKennung),
                                     frist: TimeSpan.FromSeconds(4));
            return e.TryGetProperty("bestaetigt", out var b) && b.ValueKind == JsonValueKind.True;
        }
        catch (DienstFehler) { return false; }
    }

    /// <summary>Ist <paramref name="angeboten"/> neuer als <paramref name="eigene"/>? ("1.5.0" vs "1.4.2"; Zusaetze
    /// wie "+abc" zaehlen nicht). Unlesbares -> false.</summary>
    internal static bool NeuereVersion(string? angeboten, string? eigene)
    {
        static Version? V(string? s) =>
            Version.TryParse((s ?? "").Split('+', '-')[0].Trim().TrimStart('v', 'V'), out var v) ? v : null;
        var a = V(angeboten);
        var e = V(eigene);
        return a != null && e != null && a > e;
    }

    public async Task AbmeldenAsync()
    {
        try { await SendeAsync(HttpMethod.Post, "abmelden"); }
        catch (DienstFehler) { /* lokal wird trotzdem getrennt */ }
    }

    public async Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf)
    {
        var e = await SendeAsync(HttpMethod.Post, "vergleich", new { fahrzeug = Nutzlast(f), probelauf }, VergleichFrist);
        var links = new List<Vergleich>();
        if (e.TryGetProperty("links", out var l) && l.ValueKind == JsonValueKind.Array)
            foreach (var x in l.EnumerateArray())
            {
                string url = Text(x, "url");
                if (ErlaubteAdresse(url, VergleichsSeiten))
                    links.Add(new Vergleich(Text(x, "portal"), url));
                else if (url.Length > 0)
                    Protokoll.Schreibe("Link verworfen (keine mobile.de-/AutoScout24-Adresse): " + url);
            }
        var hinweise = new List<string>();
        if (e.TryGetProperty("hinweise", out var h) && h.ValueKind == JsonValueKind.Array)
            hinweise.AddRange(h.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.String).Select(x => x.GetString()!));
        string? inseratUrl = Text(e, "inserat_url") is { Length: > 0 } iu && ErlaubteAdresse(iu, InseratSeiten) ? iu : null;
        string vorabStatus = "", vorabHinweis = "";
        if (e.TryGetProperty("vorab", out var vorab) && vorab.ValueKind == JsonValueKind.Object)
        {
            vorabStatus = Text(vorab, "status");
            vorabHinweis = Text(vorab, "hinweis");
        }
        string? marke = null, modell = null;
        bool markeErkannt = true;
        if (e.TryGetProperty("fahrzeug", out var fz) && fz.ValueKind == JsonValueKind.Object)
        {
            marke = Text(fz, "marke") is { Length: > 0 } m ? m : null;
            modell = Text(fz, "modell");
            markeErkannt = !fz.TryGetProperty("erkannt", out var ek) || ek.ValueKind != JsonValueKind.False;
        }
        // Seit 04.10.2026: was der Server dem Sucher sofort zeigen will (unplausible EZ/km, Modell aus der Beschreibung);
        // null = Server ohne diese Angabe
        List<string>? melden = null;
        if (e.TryGetProperty("melden", out var md) && md.ValueKind == JsonValueKind.Array)
            melden = md.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.String).Select(x => x.GetString()!).ToList();
        // Wunsch Ahmad 07.10.2026: hat das Konto den Browser-Helfer, liest der das Inserat — wir oeffnen es als Tab mit
        bool imBrowser = e.TryGetProperty("inserat_im_browser", out var ib) && ib.ValueKind == JsonValueKind.True;
        // 1.5.8 (Wunsch Ahmad 08.10.2026): Vorgangsnummer; ueber_helfer = die Erweiterung oeffnet die Tabs
        string? vorgang = Text(e, "vorgang_id") is { Length: 36 } vg && Guid.TryParse(vg, out _) ? vg : null;
        bool ueberHelfer = e.TryGetProperty("ueber_helfer", out var uh) && uh.ValueKind == JsonValueKind.True;
        return new VergleichAntwort(links, hinweise, Text(e, "profil"), inseratUrl, vorabStatus, vorabHinweis,
                                    marke, modell, markeErkannt, melden, imBrowser && inseratUrl != null,
                                    vorgang, ueberHelfer && vorgang != null);
    }

    /// <summary>Fahrzeug -> Anfrage an /vergleich (Feldnamen wie routes/werkzeuge.FahrzeugIn).</summary>
    internal static Dictionary<string, object?> Nutzlast(Fahrzeug f)
    {
        static string K(string? s, int n) => (s ?? "").Trim() is var t && t.Length > n ? t[..n] : (s ?? "").Trim();
        // Seit 1.4.0 (Wunsch Ahmad 03.10.2026): das Programm erkennt nichts mehr selbst — es schickt, was
        // AutoPointer zeigt (roh=true), der Server erkennt Marke und Modell. marke/modell (erstes Wort / Rest)
        // nur, damit ein Server ohne Erkennung weiter antwortet.
        string text = (f.MarkeModellText ?? "").Trim();
        int leer = text.IndexOf(' ');
        return new Dictionary<string, object?>
        {
            ["marke"] = K(leer > 0 ? text[..leer] : text, 60),
            ["modell"] = K(leer > 0 ? text[(leer + 1)..] : "", 80),
            ["roh"] = true,
            ["marke_modell_text"] = K(f.MarkeModellText, 160),
            ["titel"] = K(f.Titel, 300),
            ["ez_monat"] = f.EzMonat,
            ["ez_jahr"] = f.EzJahr,
            ["kilometer"] = f.Kilometer,
            ["kw"] = f.Kw,
            ["ps"] = f.Ps,
            ["kraftstoff"] = K(f.Kraftstoff, 60),
            ["getriebe"] = K(f.Getriebe, 60),
            ["tueren"] = K(f.Tueren, 20),
            ["preis"] = f.Preis,
            ["zustand"] = K(f.Zustand, 60),
            ["kategorie"] = K(f.Kategorie, 80),
            ["quelle"] = K(f.Quelle, 40),
            ["inserat_id"] = K(f.InseratId, 60),
            ["hash_id"] = K(f.HashId, 60),
            ["beschreibung"] = K(f.BeschreibungText, 1500),
        };
    }

    private static string Text(JsonElement e, string name) =>
        e.ValueKind == JsonValueKind.Object && e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString() ?? "" : "";

    /// <summary>Name und Kennung dieses PCs (die Kennung ist ein Streuwert, kein Klartext).</summary>
    public static (string Name, string Kennung) PcAngaben()
    {
        string guid = "";
        try
        {
            using var k = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64)
                .OpenSubKey(@"SOFTWARE\Microsoft\Cryptography");
            guid = k?.GetValue("MachineGuid") as string ?? "";
        }
        catch (Exception) { }
        var roh = Encoding.UTF8.GetBytes($"{guid}|{Environment.MachineName}|{Environment.UserName}");
        return (Environment.MachineName, Convert.ToHexString(SHA256.HashData(roh))[..32].ToLowerInvariant());
    }
}
