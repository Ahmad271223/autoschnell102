using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 03.10.2026: was das Programm auf dem PC ablegt, ist fuer Aussenstehende verschluesselt.</summary>
[Collection("Protokolldateien")]
public class TresorTests
{
    [Fact]
    public void Zeile_hin_und_zurueck_und_kein_Klartext()
    {
        string geheim = Tresor.Zeile("Hyundai i10 · 3530444655 · https://suchen.mobile.de/x");
        Assert.DoesNotContain("Hyundai", geheim);
        Assert.DoesNotContain("mobile", geheim);
        Assert.Equal("Hyundai i10 · 3530444655 · https://suchen.mobile.de/x", Tresor.ZeileLesen(geheim));
        Assert.Null(Tresor.ZeileLesen("kein-base64!"));
        Assert.Null(Tresor.ZeileLesen(Convert.ToBase64String(new byte[] { 1, 2, 3 })));
    }

    [Fact]
    public void Protokolldatei_ist_verschluesselt_und_alter_Klartext_wird_umgestellt()
    {
        string ordner = Path.Combine(Path.GetTempPath(), "as-tresor-" + Guid.NewGuid().ToString("N")[..8]);
        string? vorher = Environment.GetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN");
        bool dateiVorher = Protokoll.DateiAktiv;
        Environment.SetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN", ordner);
        Protokoll.DateiAktiv = true;
        try
        {
            Directory.CreateDirectory(Protokoll.Ordner);
            string alt = Path.Combine(Protokoll.Ordner, $"protokoll-{DateTime.Today.AddDays(-1):yyyy-MM-dd}.log");
            File.WriteAllLines(alt, new[] { "12:00:00 Fahrzeug erkannt", "         VW Golf  (V Tour)" });
            Protokoll.KlartextUmstellen();
            Assert.False(File.Exists(alt));
            Assert.Equal(new[] { "12:00:00 Fahrzeug erkannt", "         VW Golf  (V Tour)" }, Protokoll.Tag(DateTime.Today.AddDays(-1)));

            Protokoll.Schreibe("Fahrzeug erkannt\nOpel Corsa · Inserat 3530454818");
            string heute = Path.Combine(Protokoll.Ordner, $"protokoll-{DateTime.Today:yyyy-MM-dd}.dat");
            string roh = File.ReadAllText(heute);
            Assert.DoesNotContain("Corsa", roh);
            Assert.DoesNotContain("3530454818", roh);
            Assert.Contains(Protokoll.Tag(DateTime.Today), z => z.Contains("Opel Corsa · Inserat 3530454818"));
            Assert.Equal(DateTime.Today, Protokoll.Tage()[0]);
        }
        finally
        {
            Environment.SetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN", vorher);
            Protokoll.DateiAktiv = dateiVorher;
            try { Directory.Delete(ordner, true); } catch (IOException) { }
        }
    }
}
