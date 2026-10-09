using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Win32;

namespace AutoPointerVergleich;

internal enum BrowserWahl { Standard, Edge, Chrome }

/// <summary>Einstellungen, gespeichert unter %APPDATA%\AutoSchnell\AutoPointer-Vergleich.
/// Die Suchregeln stehen seit dem 03.10.2026 NICHT mehr hier: der Server baut die Links
/// mit den Vergleichsregeln der Firma aus AutoSchnell (Einstellungen → Vergleich).</summary>
internal sealed class Einstellungen
{
    public const string StandardServer = "https://app.auto-schnellkauf.de";

    public bool AutomatikAktiv { get; set; } = true;
    // 1.5.8 (Wunsch Ahmad 08.10.2026): die Portalwahl (mobile.de / AutoScout24) gibt es nur noch in AutoSchnell —
    // der Server schickt nur die Links der gewaehlten Portale. Alte Werte in einstellungen.json werden ignoriert.

    public BrowserWahl Browser { get; set; } = BrowserWahl.Standard;
    public bool ZurueckZuAutoPointer { get; set; } = false;

    /// <summary>So lange muss die Detailansicht unveraendert sein, bevor gelesen wird.</summary>
    public int WartezeitMs { get; set; } = 400;
    /// <summary>Hoechstens ein neuer Vergleich je Zeitraum.</summary>
    public int MindestabstandMs { get; set; } = 500;
    public bool HinweiseAnzeigen { get; set; } = true;
    public bool TastenkuerzelAktiv { get; set; } = true;
    public bool ErkennungsbilderSpeichern { get; set; } = false;
    /// <summary>Wunsch Ahmad 09.10.2026 (1.5.13): bei einem nicht erkannten Auto das gelesene Bild der Anzeige an AutoSchnell
    /// schicken (einmal je Auto, hoechstens 30 am Tag, nie im Probelauf) — damit man dort sieht, was auf dem Bildschirm
    /// stand. Standard AN (Wunsch Ahmad); eine alte einstellungen.json ohne das Feld bekommt denselben Standard.</summary>
    public bool LesebilderSenden { get; set; } = true;

    // 1.5.8: die kleine Leiste ist immer da (EINE Bedienung statt Fenster + Leiste + Menue am Symbol);
    // ein altes "LeisteAnzeigen": false in einstellungen.json wird ignoriert.
    /// <summary>Ecke der Leiste: "links" oder "rechts" (jeweils unten). Links verdeckt die Detailansicht von
    /// AutoPointer nicht (die steht rechts).</summary>
    public string LeisteEcke { get; set; } = Leiste.Links;
    /// <summary>Frei verschobene Leiste (Griff-Punkt, Wunsch Ahmad 04.10.2026): linke obere Ecke in
    /// Bildschirmpunkten; null = feste Ecke (LeisteEcke).</summary>
    public int? LeisteX { get; set; }
    public int? LeisteY { get; set; }

    // ---- Verbindung zu AutoSchnell ------------------------------------------
    public string Server { get; set; } = StandardServer;
    /// <summary>Programm-Schluessel, mit Windows-DPAPI fuer DIESES Windows-Konto verschluesselt.</summary>
    public string? SchluesselGeschuetzt { get; set; }
    /// <summary>Server, fuer den der Schluessel gilt.</summary>
    public string? SchluesselServer { get; set; }
    /// <summary>Anzeige "Max Sucher (10002-1) · Autohaus …".</summary>
    public string? VerbundenAls { get; set; }

    [JsonIgnore] public bool MitWindowsStarten { get; set; }
    /// <summary>Pruefung 08.10.2026 (1.5.9, F): hat der Sucher "mit Windows starten" je selbst umgestellt? Dann schaltet
    /// das Programm den Autostart nie von sich aus ein (ein bewusstes "aus" bleibt aus).</summary>
    public bool AutostartSelbstGewaehlt { get; set; }

    /// <summary>1.5.9 (F): nach dem (ersten) Verbinden den Autostart einschalten? Nur, wenn er aus ist und der Sucher ihn
    /// nie selbst umgestellt hat. (rein, fuer Tests)</summary>
    public bool AutostartNachVerbinden() => !MitWindowsStarten && !AutostartSelbstGewaehlt;

    public static string Ordner =>
        Environment.GetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN") is { Length: > 0 } eigener
            ? eigener
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "AutoSchnell", "AutoPointer-Vergleich");
    private static string Datei => Path.Combine(Ordner, "einstellungen.json");

    private static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter() },
    };

    private static string Sicherung => Datei + ".bak";

    public static Einstellungen Laden()
    {
        // Pruefung 05.10.2026 (Paket 1): eine abgeschnittene Datei (Absturz/Stromausfall beim Speichern) kostete den
        // Programm-Schluessel — jetzt zaehlt dann die Sicherung vom letzten Speichern (.bak)
        var e = Lies(Datei) ?? Lies(Sicherung) ?? new Einstellungen();
        e.MitWindowsStarten = Autostart.IstAn();
        return e.Bereinigt();
    }

    private static Einstellungen? Lies(string pfad)
    {
        try
        {
            if (!File.Exists(pfad)) return null;
            var e = JsonSerializer.Deserialize<Einstellungen>(File.ReadAllText(pfad), Json);
            if (e == null) Protokoll.Schreibe($"Einstellungen leer ({Path.GetFileName(pfad)}).");
            return e;
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe($"Einstellungen nicht lesbar ({Path.GetFileName(pfad)}: {ex.Message}).");
            return null;
        }
    }

    /// <summary>Pruefung 05.10.2026 (Paket 2, A12): der Server von der Befehlszeile (--server, Tests) gilt nur im
    /// Speicher — beim Speichern bleibt der gespeicherte Server stehen. Vorher landete der Testserver in der Datei
    /// und das Programm haengte sich beim naechsten normalen Start an den falschen Server.</summary>
    private string? _serverGespeichert;
    [JsonIgnore] public bool ServerNurImSpeicher => _serverGespeichert != null;

    public void ServerUeberschreiben(string server)
    {
        _serverGespeichert ??= Server;
        Server = server;
    }

    public void Speichern()
    {
        Directory.CreateDirectory(Ordner);
        // Paket 1: erst vollstaendig in eine neue Datei, dann in einem Zug tauschen (die alte wird zur .bak) —
        // File.WriteAllText leerte die Datei zuerst; ein Absturz dazwischen hinterliess eine leere Datei
        string neu = Datei + ".neu";
        var zuSchreiben = this;
        if (_serverGespeichert != null)
        {
            zuSchreiben = Kopie();
            zuSchreiben.Server = _serverGespeichert;       // A12: --server nie in die Datei
        }
        File.WriteAllText(neu, JsonSerializer.Serialize(zuSchreiben, Json));
        if (File.Exists(Datei)) File.Replace(neu, Datei, Sicherung);
        else File.Move(neu, Datei, overwrite: true);
        Autostart.Setzen(MitWindowsStarten);
    }

    public Einstellungen Kopie() =>
        JsonSerializer.Deserialize<Einstellungen>(JsonSerializer.Serialize(this, Json), Json)!.Mit(m => m.MitWindowsStarten = MitWindowsStarten);

    private Einstellungen Mit(Action<Einstellungen> a) { a(this); return this; }

    public Einstellungen Bereinigt()
    {
        WartezeitMs = Math.Clamp(WartezeitMs, 100, 5000);
        MindestabstandMs = Math.Clamp(MindestabstandMs, 0, 60_000);
        Server = SichererServer(Server) ?? StandardServer;
        LeisteEcke = LeisteEcke == Leiste.Rechts ? Leiste.Rechts : Leiste.Links;
        return this;
    }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 14): der Programm-Schluessel geht mit JEDER Anfrage an den Server —
    /// deshalb nur https und nur AutoSchnell (auto-schnellkauf.de samt Unteradressen). http bzw. eine andere
    /// Adresse nur fuer den eigenen Rechner (Tests: 127.0.0.1, localhost). Sonst null.</summary>
    internal static string? SichererServer(string? adresse)
    {
        if (string.IsNullOrWhiteSpace(adresse) || !Uri.TryCreate(adresse.Trim(), UriKind.Absolute, out var u)) return null;
        if (!string.IsNullOrEmpty(u.UserInfo) || u.Scheme is not ("https" or "http")) return null;
        bool lokal = u.IsLoopback;
        bool autoschnell = u.Host.Equals("auto-schnellkauf.de", StringComparison.OrdinalIgnoreCase)
                           || u.Host.EndsWith(".auto-schnellkauf.de", StringComparison.OrdinalIgnoreCase);
        if (!lokal && !(autoschnell && u.Scheme == "https")) return null;
        return adresse.Trim().TrimEnd('/');
    }

    /// <summary>Pruefung 05.10.2026 (Paket 3, F6): Schluessel() wurde ~6-mal je Sekunde aufgerufen (Verbunden im Takt,
    /// Leiste, Fenster) und entschluesselte JEDES Mal per DPAPI. Jetzt wird das Ergebnis je gespeichertem Schluessel
    /// (SchluesselGeschuetzt + Server + SchluesselServer) behalten; SchluesselSetzen leert den Zwischenspeicher.
    /// Ein unveraenderliches Paar, damit der Takt-Thread und der Oberflaechen-Thread ohne Sperre lesen koennen.</summary>
    private sealed record SchluesselCache(string Kennung, string? Wert);
    private SchluesselCache? _schluesselCache;

    /// <summary>Programm-Schluessel fuer den aktuellen Server (oder null).</summary>
    public string? Schluessel()
    {
        if (string.IsNullOrEmpty(SchluesselGeschuetzt) || !string.Equals(SchluesselServer, Server, StringComparison.OrdinalIgnoreCase))
            return null;
        string kennung = $"{SchluesselGeschuetzt}|{Server}|{SchluesselServer}";
        var cache = _schluesselCache;
        if (cache != null && cache.Kennung == kennung) return cache.Wert;
        string? wert;
        try
        {
            var roh = ProtectedData.Unprotect(Convert.FromBase64String(SchluesselGeschuetzt), Zusatz, DataProtectionScope.CurrentUser);
            wert = Encoding.UTF8.GetString(roh);
        }
        catch (Exception) { wert = null; }       // anderes Windows-Konto / kaputt -> neu verbinden
        _schluesselCache = new SchluesselCache(kennung, wert);
        return wert;
    }

    public void SchluesselSetzen(string? schluessel, string? verbundenAls)
    {
        _schluesselCache = null;
        if (string.IsNullOrEmpty(schluessel))
        {
            SchluesselGeschuetzt = null;
            SchluesselServer = null;
            VerbundenAls = null;
            return;
        }
        var geschuetzt = ProtectedData.Protect(Encoding.UTF8.GetBytes(schluessel), Zusatz, DataProtectionScope.CurrentUser);
        SchluesselGeschuetzt = Convert.ToBase64String(geschuetzt);
        SchluesselServer = Server;
        VerbundenAls = verbundenAls;
    }

    private static readonly byte[] Zusatz = Encoding.UTF8.GetBytes("AutoSchnell.AutoPointerVergleich.v1");
}

/// <summary>Eintrag unter HKCU\...\Run - nur wenn der Nutzer "Mit Windows starten" anhakt.</summary>
internal static class Autostart
{
    private const string Schluessel = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string Name = "AutoSchnell AutoPointer-Vergleich";

    public static bool IstAn()
    {
        try
        {
            using var k = Registry.CurrentUser.OpenSubKey(Schluessel);
            return k?.GetValue(Name) is string;
        }
        catch { return false; }
    }

    /// <summary>Pruefung 04.10.2026: Wert des Autostart-Eintrags fuer diese Programmdatei.</summary>
    internal static string Wert(string pfad) => $"\"{pfad}\" --autostart";

    /// <summary>Muss ein vorhandener Autostart-Eintrag auf die laufende Datei umgestellt werden? (rein, fuer Tests)</summary>
    internal static bool MussNachziehen(string? eintrag, string? pfad) =>
        eintrag != null && !string.IsNullOrEmpty(pfad)
        && !string.Equals(eintrag.Trim(), Wert(pfad), StringComparison.OrdinalIgnoreCase);

    /// <summary>Pruefung 04.10.2026: Wer ein Update an anderer Stelle speichert ("AutoSchnell-Vergleich (1).exe" in
    /// Downloads), bekam beim naechsten Windows-Start wieder die ALTE Datei — der Eintrag zeigte auf deren Pfad.
    /// Ist der Autostart an, zeigt er beim Programmstart auf die richtige Datei. Aus bleibt aus.
    /// Pruefung 05.10.2026 (Paket 2, A6): "richtig" ist die Kopie am festen Platz (<see cref="Installation"/>), sobald
    /// es sie gibt; sonst die laufende Datei — nie eine aus Temp/Downloads (dann bleibt der Eintrag, wie er ist).</summary>
    public static void PfadNachziehen()
    {
        try
        {
            using var k = Registry.CurrentUser.OpenSubKey(Schluessel, writable: true);
            if (k == null || k.GetValue(Name) is not string eintrag) return;
            string? pfad = Installation.AutostartZiel();
            if (pfad == null)
            {
                Protokoll.Schreibe("Autostart nicht nachgezogen: das Programm läuft aus einem temporären Ordner und die "
                                   + "feste Kopie fehlt (" + Environment.ProcessPath + ").");
                return;
            }
            if (!MussNachziehen(eintrag, pfad)) return;
            k.SetValue(Name, Wert(pfad));
            Protokoll.Schreibe("Autostart zeigt jetzt auf diese Programmdatei: " + pfad);
        }
        catch (Exception ex) { Protokoll.Schreibe("Autostart nicht nachgezogen: " + ex.Message); }
    }

    public static void Setzen(bool an)
    {
        if (an == IstAn()) return;
        using var k = Registry.CurrentUser.OpenSubKey(Schluessel, writable: true);
        if (k == null) return;
        // Paket 2 (A6): auf die feste Kopie, wenn es sie gibt — sonst wie bisher auf die laufende Datei
        if (an) k.SetValue(Name, Wert(Installation.AutostartZiel() ?? Environment.ProcessPath ?? ""));
        else k.DeleteValue(Name, throwOnMissingValue: false);
    }
}
