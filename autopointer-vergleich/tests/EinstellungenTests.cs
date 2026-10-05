using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Pruefung 05.10.2026 (Paket 1): Einstellungen (mit dem Programm-Schluessel) ueberleben einen Absturz
/// beim Speichern — vorher leerte File.WriteAllText die Datei zuerst.</summary>
[Collection("Protokolldateien")]   // AUTOSCHNELL_VERGLEICH_DATEN wird umgebogen — nicht parallel zu TresorTests
public class EinstellungenTests
{
    [Fact]
    public void Speichern_tauscht_in_einem_Zug_und_haelt_eine_Sicherung()
    {
        string ordner = Path.Combine(Path.GetTempPath(), "as-einst-" + Guid.NewGuid().ToString("N")[..8]);
        string? vorher = Environment.GetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN");
        bool dateiVorher = Protokoll.DateiAktiv;
        Environment.SetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN", ordner);
        Protokoll.DateiAktiv = false;
        try
        {
            bool autostart = Autostart.IstAn();          // Setzen(an == IstAn) aendert nichts an der Registrierung
            var e = new Einstellungen { WartezeitMs = 777, MitWindowsStarten = autostart };
            e.SchluesselSetzen("streng-geheim", "Max (10002-1)");
            e.Speichern();
            string datei = Path.Combine(ordner, "einstellungen.json");
            Assert.True(File.Exists(datei));
            Assert.False(File.Exists(datei + ".neu"));
            Assert.Equal(777, Einstellungen.Laden().WartezeitMs);

            e.WartezeitMs = 888;
            e.Speichern();                                // die erste Fassung wird zur Sicherung
            Assert.True(File.Exists(datei + ".bak"));
            Assert.Equal(888, Einstellungen.Laden().WartezeitMs);

            // Absturz mitten im Schreiben: Datei abgeschnitten -> die Sicherung zaehlt, der Schluessel bleibt
            File.WriteAllText(datei, "{\"WartezeitMs\": 8");
            var geladen = Einstellungen.Laden();
            Assert.Equal(777, geladen.WartezeitMs);
            Assert.Equal("streng-geheim", geladen.Schluessel());

            // beides kaputt -> Standardwerte, kein Absturz
            File.WriteAllText(datei + ".bak", "");
            Assert.Equal(new Einstellungen().WartezeitMs, Einstellungen.Laden().WartezeitMs);
        }
        finally
        {
            Environment.SetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN", vorher);
            Protokoll.DateiAktiv = dateiVorher;
            try { Directory.Delete(ordner, true); } catch (IOException) { }
        }
    }

    [Fact]
    public void Gedrosseltes_Protokoll_zaehlt_Wiederholungen()
    {
        var zeilen = new List<string>();
        Protokoll.NeueZeile += zeilen.Add;
        bool dateiVorher = Protokoll.DateiAktiv;
        Protokoll.DateiAktiv = false;
        try
        {
            string schluessel = "test:" + Guid.NewGuid().ToString("N");
            for (int i = 0; i < 5; i++) Protokoll.SchreibeGedrosselt(schluessel, "Fehler X", TimeSpan.FromMinutes(1));
            Assert.Single(zeilen.Where(z => z.Contains("Fehler X")));
            Protokoll.SchreibeGedrosselt(schluessel, "Fehler X", TimeSpan.Zero);
            Assert.Contains(zeilen, z => z.Contains("4-mal dieselbe Meldung unterdrückt"));
        }
        finally
        {
            Protokoll.NeueZeile -= zeilen.Add;
            Protokoll.DateiAktiv = dateiVorher;
        }
    }
}
