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
    public bool MobileDe { get; set; } = true;
    public bool AutoScout24 { get; set; } = true;

    public BrowserWahl Browser { get; set; } = BrowserWahl.Standard;
    public bool ZurueckZuAutoPointer { get; set; } = false;

    /// <summary>So lange muss die Detailansicht unveraendert sein, bevor gelesen wird.</summary>
    public int WartezeitMs { get; set; } = 400;
    /// <summary>Hoechstens ein neuer Vergleich je Zeitraum.</summary>
    public int MindestabstandMs { get; set; } = 500;
    public bool HinweiseAnzeigen { get; set; } = true;
    public bool TastenkuerzelAktiv { get; set; } = true;
    public bool ErkennungsbilderSpeichern { get; set; } = false;
    /// <summary>Fehlen Zeilen auf dem Bildschirm, AutoPointer die Tabelle selbst zeichnen lassen (PrintWindow).
    /// Standard AUS: zweimal am 03.10.2026 meldete AutoPointer genau dann dieselbe "Zugriffsverletzung"
    /// (aprun.exe, Offset 16B050B).</summary>
    public bool AutoPointerZeichnenLassen { get; set; } = false;

    /// <summary>Kleine Leiste mit den wichtigsten Knoepfen, immer im Vordergrund (Wunsch Ahmad 03.10.2026).</summary>
    public bool LeisteAnzeigen { get; set; } = true;
    /// <summary>Ecke der Leiste: "links" oder "rechts" (jeweils unten). Links verdeckt die Detailansicht von
    /// AutoPointer nicht (die steht rechts).</summary>
    public string LeisteEcke { get; set; } = Leiste.Links;

    // ---- Verbindung zu AutoSchnell ------------------------------------------
    public string Server { get; set; } = StandardServer;
    /// <summary>Programm-Schluessel, mit Windows-DPAPI fuer DIESES Windows-Konto verschluesselt.</summary>
    public string? SchluesselGeschuetzt { get; set; }
    /// <summary>Server, fuer den der Schluessel gilt.</summary>
    public string? SchluesselServer { get; set; }
    /// <summary>Anzeige "Max Sucher (10002-1) · Autohaus …".</summary>
    public string? VerbundenAls { get; set; }

    [JsonIgnore] public bool MitWindowsStarten { get; set; }

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

    public static Einstellungen Laden()
    {
        Einstellungen e;
        try
        {
            e = File.Exists(Datei)
                ? JsonSerializer.Deserialize<Einstellungen>(File.ReadAllText(Datei), Json) ?? new Einstellungen()
                : new Einstellungen();
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe($"Einstellungen nicht lesbar ({ex.Message}) – Standardwerte.");
            e = new Einstellungen();
        }
        e.MitWindowsStarten = Autostart.IstAn();
        return e.Bereinigt();
    }

    public void Speichern()
    {
        Directory.CreateDirectory(Ordner);
        File.WriteAllText(Datei, JsonSerializer.Serialize(this, Json));
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

    /// <summary>Programm-Schluessel fuer den aktuellen Server (oder null).</summary>
    public string? Schluessel()
    {
        if (string.IsNullOrEmpty(SchluesselGeschuetzt) || !string.Equals(SchluesselServer, Server, StringComparison.OrdinalIgnoreCase))
            return null;
        try
        {
            var roh = ProtectedData.Unprotect(Convert.FromBase64String(SchluesselGeschuetzt), Zusatz, DataProtectionScope.CurrentUser);
            return Encoding.UTF8.GetString(roh);
        }
        catch (Exception) { return null; }       // anderes Windows-Konto / kaputt -> neu verbinden
    }

    public void SchluesselSetzen(string? schluessel, string? verbundenAls)
    {
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

    public static void Setzen(bool an)
    {
        if (an == IstAn()) return;
        using var k = Registry.CurrentUser.OpenSubKey(Schluessel, writable: true);
        if (k == null) return;
        if (an) k.SetValue(Name, $"\"{Environment.ProcessPath}\" --autostart");
        else k.DeleteValue(Name, throwOnMissingValue: false);
    }
}
