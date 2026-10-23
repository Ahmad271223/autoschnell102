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

    [Fact]   // Pruefung 05.10.2026 (Paket 2, A12): --server gilt nur fuer diesen Lauf — nie in die Datei
    public void Server_von_der_Befehlszeile_wird_nie_gespeichert()
    {
        string ordner = Path.Combine(Path.GetTempPath(), "as-einst-" + Guid.NewGuid().ToString("N")[..8]);
        string? vorher = Environment.GetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN");
        bool dateiVorher = Protokoll.DateiAktiv;
        Environment.SetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN", ordner);
        Protokoll.DateiAktiv = false;
        try
        {
            bool autostart = Autostart.IstAn();          // Setzen(an == IstAn) aendert nichts an der Registrierung
            var e = new Einstellungen { MitWindowsStarten = autostart };
            e.ServerUeberschreiben("http://127.0.0.1:9");
            Assert.Equal("http://127.0.0.1:9", e.Server);
            Assert.True(e.ServerNurImSpeicher);
            e.SchluesselSetzen("test-schluessel", "Test (10002-1)");
            e.WartezeitMs = 650;
            e.Speichern();

            var geladen = Einstellungen.Laden();
            Assert.Equal(Einstellungen.StandardServer, geladen.Server);       // der gespeicherte Server bleibt
            Assert.Equal(650, geladen.WartezeitMs);
            Assert.False(geladen.ServerNurImSpeicher);
            // der Schluessel gehoert zum Testserver — fuer den gespeicherten Server gilt er nicht
            Assert.Equal("http://127.0.0.1:9", geladen.SchluesselServer);
            Assert.Null(geladen.Schluessel());
            geladen.ServerUeberschreiben("http://127.0.0.1:9");
            Assert.Equal("test-schluessel", geladen.Schluessel());
            // im Speicher bleibt der Testserver auch nach dem Speichern
            Assert.Equal("http://127.0.0.1:9", e.Server);
        }
        finally
        {
            Environment.SetEnvironmentVariable("AUTOSCHNELL_VERGLEICH_DATEN", vorher);
            Protokoll.DateiAktiv = dateiVorher;
            try { Directory.Delete(ordner, true); } catch (IOException) { }
        }
    }

    [Fact]   // Pruefung 08.10.2026 (1.5.9, F): Autostart nach dem ersten Verbinden — ausser der Sucher hat ihn selbst umgestellt
    public void Autostart_nach_dem_Verbinden_nur_ohne_eigene_Wahl()
    {
        var e = new Einstellungen { MitWindowsStarten = false };
        Assert.True(e.AutostartNachVerbinden());
        e.MitWindowsStarten = true;
        Assert.False(e.AutostartNachVerbinden());                    // ist schon an
        e = new Einstellungen { MitWindowsStarten = false, AutostartSelbstGewaehlt = true };
        Assert.False(e.AutostartNachVerbinden());                    // bewusst aus: bleibt aus
        // die eigene Wahl ueberlebt Speichern/Laden (Kopie = JSON hin und zurueck)
        Assert.True(e.Kopie().AutostartSelbstGewaehlt);
        Assert.False(new Einstellungen().Kopie().AutostartSelbstGewaehlt);
    }

    [Fact]   // 1.5.9 (F): nur wer den Haken wirklich umstellt, hat "selbst gewaehlt"
    public void Einstellungsfenster_merkt_eigene_Autostart_Wahl()
    {
        Exception? fehler = null;
        var t = new Thread(() =>
        {
            try
            {
                var ziel = new Einstellungen { MitWindowsStarten = false };
                using (var f = new EinstellungenForm(ziel)) f.AnwendenAuf(ziel);            // nichts umgestellt
                Assert.False(ziel.AutostartSelbstGewaehlt);
                var anders = new Einstellungen { MitWindowsStarten = true };                // Dialog zeigte "an"
                using (var f = new EinstellungenForm(anders)) f.AnwendenAuf(ziel);           // -> ziel war "aus"
                Assert.True(ziel.AutostartSelbstGewaehlt);
                Assert.True(ziel.MitWindowsStarten);
            }
            catch (Exception ex) { fehler = ex; }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (fehler != null) throw fehler;
    }

    [Fact]   // Paket 2 (A13): Erkennungsbilder hoechstens ~200 MB — die aeltesten fliegen zuerst
    public void Erkennungsbilder_ueber_dem_Limit_aelteste_zuerst()
    {
        var t0 = new DateTime(2026, 10, 5, 8, 0, 0);
        var dateien = new List<(string Pfad, long Groesse, DateTime Zeit)>
        {
            ("a", 60, t0), ("b", 60, t0.AddMinutes(1)), ("c", 60, t0.AddMinutes(2)), ("d", 60, t0.AddMinutes(3)),
        };
        Assert.Equal(new[] { "b", "a" }, Protokoll.UeberDemLimit(dateien, 150));   // d + c = 120 bleiben
        Assert.Empty(Protokoll.UeberDemLimit(dateien, 240));
        Assert.Equal(new[] { "c", "b", "a" }, Protokoll.UeberDemLimit(dateien, 100));
        Assert.Equal(200L * 1024 * 1024, Protokoll.BilderMaxBytes);
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
            Assert.Single(zeilen, z => z.Contains("Fehler X"));
            Protokoll.SchreibeGedrosselt(schluessel, "Fehler X", TimeSpan.Zero);
            Assert.Contains(zeilen, z => z.Contains("4-mal dieselbe Meldung unterdrückt"));
        }
        finally
        {
            Protokoll.NeueZeile -= zeilen.Add;
            Protokoll.DateiAktiv = dateiVorher;
        }
    }

    [Fact]   // Wunsch Ahmad 09.10.2026 (1.5.13): Lesebilder an AutoSchnell — Standard AN, auch fuer eine alte einstellungen.json ohne das Feld
    public void Lesebilder_senden_ist_standardmaessig_an_und_ueberlebt_die_Kopie()
    {
        Assert.True(new Einstellungen().LesebilderSenden);
        var alt = System.Text.Json.JsonSerializer.Deserialize<Einstellungen>("""{"WartezeitMs": 400}""")!;
        Assert.True(alt.LesebilderSenden);
        var e = new Einstellungen { LesebilderSenden = false };
        Assert.False(e.Kopie().LesebilderSenden);          // Kopie = JSON hin und zurueck, wie Speichern/Laden
        Assert.True(new Einstellungen().Kopie().LesebilderSenden);
    }

    [Fact]   // 1.5.13: der Haken im Einstellungsfenster kommt in den laufenden Einstellungen an (AnwendenAuf, vgl. A11)
    public void Einstellungsfenster_uebernimmt_Lesebilder_senden()
    {
        Exception? fehler = null;
        var t = new Thread(() =>
        {
            try
            {
                var ziel = new Einstellungen();
                using (var f = new EinstellungenForm(new Einstellungen { LesebilderSenden = false })) f.AnwendenAuf(ziel);
                Assert.False(ziel.LesebilderSenden);
                using (var f = new EinstellungenForm(new Einstellungen())) f.AnwendenAuf(ziel);
                Assert.True(ziel.LesebilderSenden);
            }
            catch (Exception ex) { fehler = ex; }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (fehler != null) throw fehler;
    }
}
