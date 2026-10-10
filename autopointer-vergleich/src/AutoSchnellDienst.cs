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

    /// <summary>Pruefung 08.10.2026 (1.5.9, G): die Antwort hatte keinen JSON-Inhalt — sie kam nicht von AutoSchnell,
    /// sondern von davor (Cloudflare/Firewall-Seite). Ein 403 davon heisst NICHT "gesperrt", sondern "gerade blockiert".</summary>
    public bool OhneJson { get; init; }

    /// <summary>401: Schluessel ungueltig (anderer PC verbunden, getrennt, Konto gesperrt).</summary>
    public bool NichtVerbunden => Status == 401 && !Veraltet;
    /// <summary>402: kein aktives Abo.</summary>
    public bool KeinAbo => Status == 402;
    /// <summary>1.5.9 (G): echtes 403 von AutoSchnell (mit JSON, "nicht freigeschaltet") — nur das sperrt.</summary>
    public bool Gesperrt => Status == 403 && !OhneJson;
    public bool KeineVerbindung => Status == 0;
    /// <summary>Paket 2 (A8): voruebergehend — keine Verbindung, Server ueberlastet (5xx), zu viele Anfragen (429).
    /// Lizenzpruefung: still bleiben und spaeter erneut versuchen, kein rotes Symbol.
    /// 1.5.9 (G): auch ein 403 ohne JSON (Cloudflare/Firewall) — vorher galt das Programm dann als "gesperrt".</summary>
    public bool Voruebergehend => Status == 0 || Status == 429 || Status >= 500 || (Status == 403 && OhneJson);
}

internal sealed record VerbindenAntwort(string Schluessel, string Konto, string Name, string Firma);
/// <param name="AktuelleVersion">Pruefbericht 03.10.2026 (Nr. 4): die Version, die AutoSchnell gerade zum
/// Herunterladen anbietet (null bei einem Server ohne diese Angabe) — ist sie neuer, sagt das Programm es.</param>
internal sealed record StatusAntwort(string Konto, string Name, string Firma, string PcName, string? AboBis,
                                     string? AktuelleVersion = null, string? ProgrammName = null);
internal sealed record Vergleich(string Portal, string Url);

/// <param name="ErkanntMarke">Seit 1.4.0: Marke/Modell erkennt der SERVER (Katalognamen zur Anzeige);
/// null bei einem Server ohne Erkennung.</param>
/// <param name="ModellGefunden">1.5.13: der Server kennt die Marke, konnte aber kein Modell zuordnen (fahrzeug.modell_gefunden
/// = false) — dann geht das Lesebild an AutoSchnell. Fehlt die Angabe (aelterer Server), gilt das Modell als gefunden.</param>
internal sealed record VergleichAntwort(IReadOnlyList<Vergleich> Links, IReadOnlyList<string> Hinweise, string Profil,
                                        string? InseratUrl = null, string VorabStatus = "", string VorabHinweis = "",
                                       string? ErkanntMarke = null, string? ErkanntModell = null, bool MarkeErkannt = true,
                                       IReadOnlyList<string>? Melden = null, bool InseratImBrowser = false,
                                       string? VorgangId = null, bool UeberHelfer = false, string HelferBrowser = "",
                                       bool HatHelfer = false, bool ModellGefunden = true);

/// <summary>Pruefung 09.10.2026 (Vertragsweg, 1.5.15): Antwort auf GET …/inserat-gelesen. Gelesen: true/false, null = nicht
/// pruefbar (kein Netz, 5xx, Zeitueberschreitung). Sperre: der Servertext bei 402/403 (kein Abo, gesperrt) — dann kein
/// Vertrag.</summary>
internal sealed record InseratStand(bool? Gelesen, string? Sperre = null);

/// <summary>Pruefung 09.10.2026 (Vertragsweg, 1.5.15): Antwort auf GET …/app-start/&lt;start&gt;. Zustand: "offen",
/// "nachgefragt", "anmeldung" oder "abo" (siehe <see cref="AppStartWeg"/>); aeltere Server nennen keinen — dann "offen".</summary>
internal sealed record AppStartAntwort(bool Bestaetigt, string Zustand = AppStartWeg.Offen);

/// <summary>Was der Ueberwacher vom Server braucht (in Tests eine Attrappe).</summary>
internal interface IVergleichsDienst
{
    bool Verbunden { get; }
    Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf);
    /// <summary>Paket 3 (F4): Verbindung vorwaermen, sobald eine Aenderung in AutoPointer erkannt ist — die Anfrage
    /// trifft dann auf eine offene Verbindung (kein DNS/TLS-Aufbau mehr auf dem kritischen Weg). Darf nichts tun.</summary>
    void Vorwaermen() { }
    /// <summary>Pruefung 08.10.2026 (1.5.9, A): den Vorgang fuer das Programm beanspruchen (POST …/selbst). true = er
    /// gehoert jetzt dem Programm (die Erweiterung kann ihn nicht mehr nehmen) -> selbst oeffnen; false = die
    /// Erweiterung hat ihn schon (oder er ist unbekannt/abgelaufen) -> nichts tun; null = AutoSchnell nicht erreichbar.
    /// Bewusst OHNE Standard-Umsetzung: in 1.5.8 reichte der DienstVermittler die Nachfrage nicht weiter — es galt
    /// immer "nicht pruefbar" und alles ging doppelt auf.</summary>
    Task<bool?> VorgangSelbstAsync(string vorgangId);
    /// <summary>Wunsch Ahmad 09.10.2026 (1.5.13): das gelesene Bild eines nicht erkannten Autos an AutoSchnell (POST …/lesebild).
    /// true = angenommen; jeder Fehler ist nur eine Protokollzeile und false. Ohne Standard-Umsetzung — wie bei
    /// <see cref="VorgangSelbstAsync"/>, damit der DienstVermittler das Weiterreichen nicht vergessen kann.</summary>
    Task<bool> LesebildSendenAsync(Lesebild bild);
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

    /// <param name="wiederholen">false: kein eigener Wiederholversuch (der Aufrufer wiederholt selbst, z. B.
    /// <see cref="VorgangSelbstAsync"/> mit 1,5 s Pause).</param>
    private async Task<JsonElement> SendeAsync(HttpMethod methode, string pfad, object? inhalt = null, TimeSpan? frist = null,
                                               bool wiederholen = true)
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
                if (ex is HttpRequestException && versuch == 1 && wiederholen)
                {
                    await _warte(WiederholPause);
                    continue;
                }
                // Pruefung 08.10.2026 (1.5.9, G): die Meldungen versprechen nichts mehr ("wird gleich erneut versucht"
                // stimmte nicht) — ob und wann es erneut versucht wird, sagt der Aufrufer
                if (abbruch?.IsCancellationRequested == true)
                    throw new DienstFehler(0, "AutoSchnell antwortet gerade nicht.");
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
                if (Wiederholbar(status) && versuch == 1 && wiederholen)
                {
                    Protokoll.Schreibe($"AutoSchnell antwortet mit {status} ({pfad}) – ein Wiederholversuch in {WiederholPause.TotalSeconds:0} s.");
                    await _warte(WiederholPause);
                    continue;
                }
                // A11: 401 zu einem Schluessel, der inzwischen ersetzt wurde (waehrend der Anfrage neu verbunden)?
                bool veraltet = status == 401 && !string.IsNullOrEmpty(gesendet) && gesendet != _schluessel();
                // 1.5.9 (G): ohne JSON kam die Antwort nicht von AutoSchnell (Cloudflare-/Firewall-Seite)
                bool ohneJson = !IstJsonObjekt(text);
                if (status == 403 && ohneJson)
                    Protokoll.Schreibe($"403 ohne JSON ({pfad}) – blockiert vor AutoSchnell (Cloudflare/Firewall), keine Sperre.");
                throw new DienstFehler(status, veraltet
                    ? "Antwort gehört zu einer früheren Verbindung – bitte noch einmal versuchen."
                    : Meldung(text, status)) { Veraltet = veraltet, OhneJson = ohneJson };
            }
        }
    }

    /// <summary>Ist der Text ein JSON-Objekt (wie jede Fehlerantwort von AutoSchnell: {"detail": …})? (rein, fuer Tests)</summary>
    internal static bool IstJsonObjekt(string? text)
    {
        if (string.IsNullOrWhiteSpace(text)) return false;
        try
        {
            using var doc = JsonDocument.Parse(text);
            return doc.RootElement.ValueKind == JsonValueKind.Object;
        }
        catch (JsonException) { return false; }
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
            // 1.5.9 (G): 403 ohne JSON = Cloudflare/Firewall davor, nicht AutoSchnell — voruebergehend, keine Sperre
            403 when !IstJsonObjekt(text) => "AutoSchnell ist kurz nicht erreichbar.",
            403 => "Für dein Konto nicht freigeschaltet.",
            429 => "Zu viele Anfragen – bitte kurz warten.",
            // Paket 2 (A3): Gateway/Ueberlast/Cloudflare — kein Fehler des Suchers. Seit 1.5.9 ohne "wird gleich erneut
            // versucht": das sagt (nur, wenn es stimmt) der Aufrufer
            502 or 503 or 504 or (>= 520 and <= 529) => "AutoSchnell ist kurz nicht erreichbar.",
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

    /// <summary>1.5.9 (A): so lange wartet der eine Wiederholversuch von <see cref="VorgangSelbstAsync"/>.</summary>
    internal static readonly TimeSpan VorgangPause = TimeSpan.FromMilliseconds(1500);

    /// <summary>Pruefung 08.10.2026 (1.5.9, A — Uebergabe an die Erweiterung wird eindeutig): POST vorgang/&lt;id&gt;/selbst.
    /// Vorher fragte das Programm nur "uebernommen?" und oeffnete sonst selbst — eine Erweiterung, die einen Moment
    /// spaeter doch uebernahm, oeffnete alles ein zweites Mal. Jetzt beansprucht das Programm den Vorgang beim Server;
    /// wer zuerst kommt, oeffnet. 200 {"selbst": true} = gehoert jetzt dem Programm; {"selbst": false} = die Erweiterung
    /// hat ihn; 404 = unbekannt/abgelaufen (false). Netzfehler/5xx: nach 1,5 s genau ein zweiter Versuch, dann null
    /// (= nicht erreichbar — der Aufrufer oeffnet dann NICHT, sonst womoeglich doppelt). Andere Fehler (401, 403 …):
    /// false — die Vorgangsseite ist schon offen und zeigt die Links.</summary>
    public async Task<bool?> VorgangSelbstAsync(string vorgangId)
    {
        string pfad = "vorgang/" + Uri.EscapeDataString(vorgangId) + "/selbst";
        for (int versuch = 1; ; versuch++)
        {
            try
            {
                var e = await SendeAsync(HttpMethod.Post, pfad, frist: TimeSpan.FromSeconds(4), wiederholen: false);
                return e.TryGetProperty("selbst", out var s) && s.ValueKind == JsonValueKind.True;
            }
            catch (DienstFehler ex) when (ex.Voruebergehend || ex.Veraltet)
            {
                Protokoll.Schreibe($"Vorgang {vorgangId}: AutoSchnell nicht erreichbar ({versuch}. Versuch): {ex.Message}");
                if (versuch >= 2) return null;
                await _warte(VorgangPause);
            }
            catch (DienstFehler ex)
            {
                Protokoll.Schreibe(ex.Status == 404
                    ? $"Vorgang {vorgangId}: unbekannt oder abgelaufen – nicht selbst geöffnet."
                    : $"Vorgang {vorgangId}: AutoSchnell antwortet mit {ex.Status} – nicht selbst geöffnet.");
                return false;
            }
        }
    }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 12): hat die AutoSchnell-App das Auto wirklich uebernommen? Die
    /// Web-App meldet den Start (Kennung im Link) an den Server; ohne Meldung oeffnet das Programm den Browser.
    /// Fehler zaehlen als "nicht bestaetigt". Pruefung 09.10.2026 (Vertragsweg, 1.5.15): der Server nennt dazu einen
    /// Zustand ("offen", "nachgefragt", "anmeldung", "abo") — aeltere Server nicht (dann "offen", sobald bestaetigt).</summary>
    public async Task<AppStartAntwort> AppStartBestaetigtAsync(string startKennung)
    {
        try
        {
            var e = await SendeAsync(HttpMethod.Get, "app-start/" + Uri.EscapeDataString(startKennung),
                                     frist: TimeSpan.FromSeconds(4));
            bool bestaetigt = e.TryGetProperty("bestaetigt", out var b) && b.ValueKind == JsonValueKind.True;
            return new AppStartAntwort(bestaetigt, AppStartZustand(Text(e, "zustand")));
        }
        catch (DienstFehler) { return new AppStartAntwort(false, AppStartWeg.Offen); }
    }

    /// <summary>Nur die bekannten Zustaende; alles andere (aelterer Server, Tippfehler) gilt als "offen". (rein, fuer Tests)</summary>
    internal static string AppStartZustand(string? zustand) => zustand?.Trim().ToLowerInvariant() switch
    {
        AppStartWeg.Nachgefragt => AppStartWeg.Nachgefragt,
        AppStartWeg.Anmeldung => AppStartWeg.Anmeldung,
        AppStartWeg.Abo => AppStartWeg.Abo,
        _ => AppStartWeg.Offen,
    };

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
        bool markeErkannt = true, modellGefunden = true;
        if (e.TryGetProperty("fahrzeug", out var fz) && fz.ValueKind == JsonValueKind.Object)
        {
            marke = Text(fz, "marke") is { Length: > 0 } m ? m : null;
            modell = Text(fz, "modell");
            markeErkannt = !fz.TryGetProperty("erkannt", out var ek) || ek.ValueKind != JsonValueKind.False;
            // 1.5.13: konnte der Server das Modell zuordnen? Nur ein ausdrueckliches false zaehlt (aeltere Server ohne
            // die Angabe: gefunden) — sonst ginge nach einem Server-Update ohne das Feld jedes Auto als Lesebild raus
            modellGefunden = !fz.TryGetProperty("modell_gefunden", out var mg) || mg.ValueKind != JsonValueKind.False;
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
        // Pruefung 08.10.2026 (1.5.9, A): in welchem Browser die Erweiterung verbunden ist — dort oeffnet das Programm
        // die Vorgangsseite (bei "Standardbrowser"), sonst sieht sie die Erweiterung nie. Alles andere = unbekannt.
        string helferBrowser = Text(e, "helfer_browser").Trim().ToLowerInvariant() switch
        {
            "chrome" => "chrome",
            "edge" => "edge",
            _ => "",
        };
        // 1.5.11 (Wunsch Ahmad 08.10.2026 abends): hat das Konto die Erweiterung? Dann oeffnet "Vertrag" erst das Inserat
        bool hatHelfer = e.TryGetProperty("hat_helfer", out var hh) && hh.ValueKind == JsonValueKind.True;
        return new VergleichAntwort(links, hinweise, Text(e, "profil"), inseratUrl, vorabStatus, vorabHinweis,
                                    marke, modell, markeErkannt, melden, imBrowser && inseratUrl != null,
                                    vorgang, ueberHelfer && vorgang != null, helferBrowser, hatHelfer, modellGefunden);
    }

    /// <summary>1.5.13: so lange darf der Versand eines Lesebilds dauern (bis 1,5 MB, laeuft im Hintergrund).</summary>
    internal static readonly TimeSpan LesebildFrist = TimeSpan.FromSeconds(15);

    /// <summary>Wunsch Ahmad 09.10.2026 (1.5.13): POST lesebild — das gelesene Bild eines nicht erkannten Autos. Kein eigener
    /// Wiederholversuch (ein Bild weniger ist egal, eines doppelt waere laestig), hoechstens <see cref="LesebildFrist"/>.
    /// true = angenommen (200 {"ok": true}); jeder Fehler — 413 zu gross, 429 Tagesgrenze des Servers, 404 aelterer Server,
    /// 401, kein Netz — ist nur eine Protokollzeile und false: nie eine Meldung an den Sucher, nie Einfluss auf den
    /// Vergleich (der Aufrufer wartet nicht darauf).</summary>
    public async Task<bool> LesebildSendenAsync(Lesebild bild)
    {
        try
        {
            var e = await SendeAsync(HttpMethod.Post, "lesebild", Lesebilder.Nutzlast(bild), LesebildFrist, wiederholen: false);
            if (e.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True) return true;
            Protokoll.Schreibe($"Lesebild ({bild.Grund}): AutoSchnell hat es nicht angenommen.");
            return false;
        }
        catch (DienstFehler ex)
        {
            Protokoll.Schreibe(ex.Status switch
            {
                413 => $"Lesebild ({bild.Grund}) zu groß ({bild.Bild.Length / 1024} KB) – nicht angenommen.",
                429 => $"Lesebild ({bild.Grund}): Tagesgrenze bei AutoSchnell erreicht – heute keine weiteren Bilder.",
                404 => $"Lesebild ({bild.Grund}): dieser AutoSchnell-Server nimmt noch keine Lesebilder an (älterer Stand).",
                _ => $"Lesebild ({bild.Grund}) nicht gesendet: {ex.Message}",
            });
            return false;
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe($"Lesebild ({bild.Grund}) nicht gesendet: {Ursache(ex)}");
            return false;
        }
    }

    /// <summary>1.5.11 (Wunsch Ahmad 08.10.2026 abends, "Vertrag ohne Apify"): liegt das Inserat schon gelesen vor (Lesung
    /// der Erweiterung oder gemeinsamer Speicher)? Nur Lesen — der Server ruft dabei nichts ab. Gelesen null = nicht
    /// pruefbar. Pruefung 09.10.2026 (Vertragsweg): 402/403 von AutoSchnell (kein Abo, gesperrt) kommen als
    /// <see cref="InseratStand.Sperre"/> mit dem Servertext — dann gibt es keinen Vertrag; ein 403 ohne JSON (Cloudflare/
    /// Firewall davor) ist keine Sperre, nur "nicht pruefbar".</summary>
    public async Task<InseratStand> InseratGelesenAsync(string inseratUrl)
    {
        try
        {
            var e = await SendeAsync(HttpMethod.Get, "inserat-gelesen?url=" + Uri.EscapeDataString(inseratUrl),
                                     frist: TimeSpan.FromSeconds(4), wiederholen: false);
            return new InseratStand(e.TryGetProperty("gelesen", out var g) && g.ValueKind == JsonValueKind.True);
        }
        catch (DienstFehler ex) when (ex.Status is 402 or 403 && !ex.OhneJson)
        {
            Protokoll.Schreibe($"Inserat gelesen? AutoSchnell sperrt ({ex.Status}): {ex.Message}");
            return new InseratStand(null, ex.Message);
        }
        catch (DienstFehler ex)
        {
            Protokoll.Schreibe("Inserat gelesen? " + ex.Message);
            return new InseratStand(null);
        }
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
        var nutzlast = new Dictionary<string, object?>
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
        // Befund Ahmad 09.10.2026 (1.5.12): was der zweite Blick anders gelesen hat — nur Felder mit Abweichung, ganz
        // ohne "alternativen", wenn nichts abweicht. Aeltere Server ignorieren das unbekannte Feld (pydantic-Standard).
        var alternativen = new Dictionary<string, List<string>>();
        void Liste(string name, IEnumerable<string>? werte)
        {
            var l = (werte ?? Enumerable.Empty<string>()).Select(w => K(w, ZweiterBlick.MaxLaenge))
                .Where(w => w.Length > 0).Distinct(StringComparer.Ordinal).Take(ZweiterBlick.MaxJeFeld).ToList();
            if (l.Count > 0) alternativen[name] = l;
        }
        Liste("marke_modell_text", f.AlternativenMarkeModell);
        Liste("kraftstoff", f.AlternativenKraftstoff);
        Liste("inserat_id", f.AlternativenInseratId);
        if (alternativen.Count > 0) nutzlast["alternativen"] = alternativen;
        return nutzlast;
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
