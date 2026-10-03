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

    /// <summary>401: Schluessel ungueltig (anderer PC verbunden, getrennt, Konto gesperrt).</summary>
    public bool NichtVerbunden => Status == 401;
    /// <summary>402: kein aktives Abo.</summary>
    public bool KeinAbo => Status == 402;
    public bool KeineVerbindung => Status == 0;
}

internal sealed record VerbindenAntwort(string Schluessel, string Konto, string Name, string Firma);
internal sealed record StatusAntwort(string Konto, string Name, string Firma, string PcName, string? AboBis);
internal sealed record VergleichAntwort(IReadOnlyList<Vergleich> Links, IReadOnlyList<string> Hinweise, string Profil,
                                        string? InseratUrl = null, string VorabStatus = "", string VorabHinweis = "");

/// <summary>Was der Ueberwacher vom Server braucht (in Tests eine Attrappe).</summary>
internal interface IVergleichsDienst
{
    bool Verbunden { get; }
    Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf);
}

/// <summary>Verbindung zu AutoSchnell (Wunsch Ahmad 03.10.2026): das Programm arbeitet nur
/// verbunden. Ein 6-stelliger Code aus der App ergibt einen Programm-Schluessel (ein PC je
/// Konto); jeder Vergleich geht ueber den Server — der prueft Abo und Freigabe und baut die
/// Links mit den Vergleichsregeln der Firma.</summary>
internal sealed class AutoSchnellDienst : IVergleichsDienst
{
    public const string Werkzeug = "autopointer-vergleich";
    private const string SchluesselKopf = "X-Werkzeug-Schluessel";

    private readonly HttpClient _http;
    private readonly Func<string?> _schluessel;

    public string Server { get; }

    public AutoSchnellDienst(string server, Func<string?> schluessel, HttpMessageHandler? handler = null)
    {
        Server = server.TrimEnd('/');
        _schluessel = schluessel;
        _http = handler == null ? new HttpClient() : new HttpClient(handler);
        _http.Timeout = TimeSpan.FromSeconds(15);
        _http.DefaultRequestHeaders.UserAgent.ParseAdd($"AutoSchnell-Vergleich/{Application.ProductVersion.Split('+')[0]}");
    }

    public bool Verbunden => !string.IsNullOrEmpty(_schluessel());

    private string Url(string pfad) => $"{Server}/api/werkzeuge/{Werkzeug}/{pfad}";

    private HttpRequestMessage Anfrage(HttpMethod methode, string pfad, object? inhalt = null)
    {
        var a = new HttpRequestMessage(methode, Url(pfad));
        var s = _schluessel();
        if (!string.IsNullOrEmpty(s)) a.Headers.Add(SchluesselKopf, s);
        if (inhalt != null) a.Content = JsonContent.Create(inhalt, options: Json);
        return a;
    }

    internal static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        PropertyNameCaseInsensitive = true,
    };

    private async Task<JsonElement> SendeAsync(HttpRequestMessage anfrage)
    {
        HttpResponseMessage antwort;
        try { antwort = await _http.SendAsync(anfrage); }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
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
            throw new DienstFehler((int)antwort.StatusCode, Meldung(text, (int)antwort.StatusCode));
        }
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
            _ => $"AutoSchnell antwortet mit Fehler {status}.",
        };
    }

    public async Task<VerbindenAntwort> VerbindenAsync(string code, string pcName, string pcKennung)
    {
        var e = await SendeAsync(Anfrage(HttpMethod.Post, "verbinden",
            new { code, pc_name = pcName, pc_kennung = pcKennung }));
        return new VerbindenAntwort(Text(e, "schluessel"), Text(e, "konto"), Text(e, "name"), Text(e, "firma"));
    }

    public async Task<StatusAntwort> StatusAsync()
    {
        var e = await SendeAsync(Anfrage(HttpMethod.Get, "status"));
        return new StatusAntwort(Text(e, "konto"), Text(e, "name"), Text(e, "firma"), Text(e, "pc_name"),
                                 e.TryGetProperty("abo_bis", out var b) && b.ValueKind == JsonValueKind.String ? b.GetString() : null);
    }

    public async Task AbmeldenAsync()
    {
        try { await SendeAsync(Anfrage(HttpMethod.Post, "abmelden")); }
        catch (DienstFehler) { /* lokal wird trotzdem getrennt */ }
    }

    public async Task<VergleichAntwort> VergleichAsync(Fahrzeug f, bool probelauf)
    {
        var e = await SendeAsync(Anfrage(HttpMethod.Post, "vergleich", new { fahrzeug = Nutzlast(f), probelauf }));
        var links = new List<Vergleich>();
        if (e.TryGetProperty("links", out var l) && l.ValueKind == JsonValueKind.Array)
            foreach (var x in l.EnumerateArray())
            {
                string url = Text(x, "url");
                if (Uri.TryCreate(url, UriKind.Absolute, out var u) && u.Scheme == Uri.UriSchemeHttps)
                    links.Add(new Vergleich(Text(x, "portal"), url));
            }
        var hinweise = new List<string>();
        if (e.TryGetProperty("hinweise", out var h) && h.ValueKind == JsonValueKind.Array)
            hinweise.AddRange(h.EnumerateArray().Where(x => x.ValueKind == JsonValueKind.String).Select(x => x.GetString()!));
        string? inseratUrl = Text(e, "inserat_url") is { Length: > 0 } iu
                             && Uri.TryCreate(iu, UriKind.Absolute, out var iuri) && iuri.Scheme == Uri.UriSchemeHttps ? iu : null;
        string vorabStatus = "", vorabHinweis = "";
        if (e.TryGetProperty("vorab", out var vorab) && vorab.ValueKind == JsonValueKind.Object)
        {
            vorabStatus = Text(vorab, "status");
            vorabHinweis = Text(vorab, "hinweis");
        }
        return new VergleichAntwort(links, hinweise, Text(e, "profil"), inseratUrl, vorabStatus, vorabHinweis);
    }

    /// <summary>Fahrzeug -> Anfrage an /vergleich (Feldnamen wie routes/werkzeuge.FahrzeugIn).</summary>
    internal static Dictionary<string, object?> Nutzlast(Fahrzeug f)
    {
        static string K(string? s, int n) => (s ?? "").Trim() is var t && t.Length > n ? t[..n] : (s ?? "").Trim();
        return new Dictionary<string, object?>
        {
            ["marke"] = K(f.MarkeText ?? f.Marke, 60),
            ["modell"] = K(f.ModellText ?? f.Modell, 80),
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
