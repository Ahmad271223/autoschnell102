using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Win32;

namespace AutoPointerVergleich;

internal enum KmModus { Bereich, BisPlus, Aus }
internal enum EzModus { ExaktesJahr, PlusMinus, AbJahr, Aus }
/// <summary>PlusMinus = ± PS (tolerance_ps), AbMinus = ab −PS nach oben offen (min_ps).</summary>
internal enum LeistungModus { PlusMinus, AbMinus, Aus }
internal enum BrowserWahl { Standard, Edge, Chrome }
internal enum Sortierung { PreisAufsteigend, KilometerAufsteigend, ErstzulassungAbsteigend, Relevanz }

/// <summary>Einstellungen, gespeichert unter %APPDATA%\AutoSchnell\AutoPointer-Vergleich.</summary>
internal sealed class Einstellungen
{
    public bool AutomatikAktiv { get; set; } = true;
    public bool MobileDe { get; set; } = true;
    public bool AutoScout24 { get; set; } = true;

    // Standardregeln nach Ahmads Vorgabe (03.10.2026, Polo 10/2005, 128.000 km, 75 PS):
    // fr=2004: / ml=:148000 / pw=51: — EZ ab Vorjahr, km bis +20.000, Leistung ab −5 PS
    // (Backend: older_exact 1, plus 20000, min_ps 5).
    public KmModus KmModus { get; set; } = KmModus.BisPlus;
    public int KmSpanne { get; set; } = 20000;
    /// <summary>Nur bei "Bereich": Grenzen auf volle 5.000 km runden (84.975 ± 15.000 -> 70.000–100.000).</summary>
    public bool KmRunden { get; set; } = true;

    public EzModus EzModus { get; set; } = EzModus.AbJahr;
    public int EzJahre { get; set; } = 1;

    public LeistungModus LeistungModus { get; set; } = LeistungModus.AbMinus;
    public int LeistungTolerantPs { get; set; } = 5;

    /// <summary>Stand der Standardregeln. Gespeicherte Einstellungen mit aelterem Stand
    /// bekommen beim Laden die neuen Suchregeln (Portale, Browser usw. bleiben).</summary>
    public int RegelFassung { get; set; }
    public const int AktuelleRegelFassung = 2;
    public bool KraftstoffFiltern { get; set; } = true;
    public bool GetriebeFiltern { get; set; } = true;
    public bool UnfallwagenAusblenden { get; set; } = true;
    public bool NurDeutschland { get; set; } = true;
    public Sortierung Sortierung { get; set; } = Sortierung.PreisAufsteigend;
    /// <summary>Modell im Katalog nicht gefunden: trotzdem nur nach Marke suchen?
    /// Aus = lieber kein Vergleich als eine Suche "nur Bentley".</summary>
    public bool OhneModellNurMarke { get; set; } = false;

    public BrowserWahl Browser { get; set; } = BrowserWahl.Standard;
    public bool ZurueckZuAutoPointer { get; set; } = false;

    /// <summary>So lange muss die Detailansicht unveraendert sein, bevor gelesen wird.</summary>
    public int WartezeitMs { get; set; } = 400;
    /// <summary>Hoechstens ein neuer Vergleich je Zeitraum.</summary>
    public int MindestabstandMs { get; set; } = 500;
    public bool HinweiseAnzeigen { get; set; } = true;
    public bool TastenkuerzelAktiv { get; set; } = true;
    public bool ErkennungsbilderSpeichern { get; set; } = false;

    [JsonIgnore] public bool MitWindowsStarten { get; set; }

    public static string Ordner => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "AutoSchnell", "AutoPointer-Vergleich");
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
        if (File.Exists(Datei) && e.RegelFassung < AktuelleRegelFassung)
        {
            e.RegelnAufStandard();
            Protokoll.Schreibe("Suchregeln auf den neuen Standard gesetzt (EZ ab Vorjahr, km bis +20.000, Leistung ab −5 PS).");
        }
        e.RegelFassung = AktuelleRegelFassung;
        e.MitWindowsStarten = Autostart.IstAn();
        return e.Bereinigt();
    }

    /// <summary>Nur die Suchregeln zuruecksetzen.</summary>
    public void RegelnAufStandard()
    {
        var s = new Einstellungen();
        KmModus = s.KmModus;
        KmSpanne = s.KmSpanne;
        KmRunden = s.KmRunden;
        EzModus = s.EzModus;
        EzJahre = s.EzJahre;
        LeistungModus = s.LeistungModus;
        LeistungTolerantPs = s.LeistungTolerantPs;
    }

    public void Speichern()
    {
        RegelFassung = AktuelleRegelFassung;
        Directory.CreateDirectory(Ordner);
        File.WriteAllText(Datei, JsonSerializer.Serialize(this, Json));
        Autostart.Setzen(MitWindowsStarten);
    }

    public Einstellungen Kopie() =>
        JsonSerializer.Deserialize<Einstellungen>(JsonSerializer.Serialize(this, Json), Json)!.Mit(m => m.MitWindowsStarten = MitWindowsStarten);

    private Einstellungen Mit(Action<Einstellungen> a) { a(this); return this; }

    public Einstellungen Bereinigt()
    {
        KmSpanne = Math.Clamp(KmSpanne, 0, 500_000);
        EzJahre = Math.Clamp(EzJahre, 0, 20);
        LeistungTolerantPs = Math.Clamp(LeistungTolerantPs, 0, 200);
        WartezeitMs = Math.Clamp(WartezeitMs, 100, 5000);
        MindestabstandMs = Math.Clamp(MindestabstandMs, 0, 60_000);
        return this;
    }
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
