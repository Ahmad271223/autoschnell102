using System.Reflection;
using Whisper.net;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Sprachdiktat am PC (Wunsch Ahmad 24.11.2026): Knopf "Diktat" / Strg+Alt+D, Erkennung mit Whisper auf dem PC.</summary>
public class DiktatTests
{
    [Fact]
    public void Pcm_wird_zu_float_und_hoert_an_der_Grenze_auf()
    {
        var ziel = new List<float>();
        byte[] puffer = { 0x00, 0x00, 0xFF, 0x7F, 0x00, 0x80, 0x00, 0x40 };   // 0, 32767, -32768, 16384
        Diktat.PcmAnhaengen(ziel, puffer, puffer.Length, max: 3);
        Assert.Equal(3, ziel.Count);
        Assert.Equal(0f, ziel[0]);
        Assert.InRange(ziel[1], 0.999f, 1f);
        Assert.Equal(-1f, ziel[2]);
    }

    [Fact]
    public void Whisper_Platzhalter_fallen_weg()
    {
        Assert.Equal("Kratzer an der Stoßstange. Delle hinten links.",
            Diktat.TextAufbereiten(new[] { " Kratzer an der Stoßstange.", " [Musik] ", " Delle hinten links. (Stille)" }));
        Assert.Equal("", Diktat.TextAufbereiten(new[] { "[Musik]", "  " }));
    }

    [Fact]   // ein Enter wuerde in einzeiligen Feldern das Formular abschicken
    public void Eingabe_hat_keine_Zeilenumbrueche()
    {
        Assert.Equal("Zeile eins Zeile zwei", Diktat.FuerEingabe(" Zeile eins\r\nZeile  zwei\n"));
    }

    [Fact]   // SendInput lehnt falsche Strukturgroessen ab (x64: 40 Bytes)
    public void Input_Struktur_hat_die_Windows_Groesse()
    {
        Assert.Equal(IntPtr.Size == 8 ? 40 : 28, Diktat.InputGroesse);
    }

    [Fact]   // die EXE bleibt eine Datei: die whisper.cpp-DLLs stecken als Ressource darin
    public void Whisper_Bibliotheken_stecken_in_der_Exe()
    {
        var namen = typeof(Diktat).Assembly.GetManifestResourceNames().Where(n => n.StartsWith("diktat/")).ToList();
        Assert.Contains("diktat/whisper.dll", namen);
        Assert.Contains("diktat/ggml-whisper.dll", namen);
        Assert.Contains("diktat/ggml-base-whisper.dll", namen);
        Assert.Contains("diktat/ggml-cpu-whisper.dll", namen);
        // Grafikkarte (Vulkan): 0,3-0,8 s statt 10-17 s je Diktat (Messung 24.11.2026)
        Assert.Contains("diktat-vulkan/ggml-vulkan-whisper.dll", typeof(Diktat).Assembly.GetManifestResourceNames());
    }

    [Fact]   // entpacken + laden klappt ohne lose runtimes-Ordner neben der EXE
    public void Whisper_Laufzeit_laesst_sich_aus_der_Exe_laden()
    {
        string ordner = Path.Combine(Path.GetTempPath(), "autoschnell-diktat-test-" + Guid.NewGuid().ToString("N"));
        var d = new Diktat(ordner);
        d.LaufzeitBereitstellen();
        Assert.True(File.Exists(Path.Combine(ordner, "laufzeit-1.9.1", "runtimes", "win-x64", "whisper.dll")));
        string info = WhisperFactory.GetRuntimeInfo();
        Assert.False(string.IsNullOrWhiteSpace(info));
    }

    [Fact]
    public void Ohne_Modell_ist_nichts_bereit_und_ohne_Aufnahme_kommt_nichts()
    {
        var d = new Diktat(Path.Combine(Path.GetTempPath(), "autoschnell-diktat-leer-" + Guid.NewGuid().ToString("N")));
        Assert.False(d.ModellVorhanden);
        Assert.False(d.NimmtAuf);
        Assert.Empty(d.AufnahmeStoppen());
    }

    [Fact]   // kurze Aufnahme (Fehlklick) -> leerer Text, ohne das Modell zu brauchen
    public async Task Zu_kurze_Aufnahme_ergibt_leeren_Text()
    {
        var d = new Diktat(Path.Combine(Path.GetTempPath(), "autoschnell-diktat-kurz-" + Guid.NewGuid().ToString("N")));
        Assert.Equal("", await d.ErkennenAsync(new float[4000], CancellationToken.None));
    }

    [Fact]   // dieselben Regeln wie frontend/src/lib/diktatRegeln.test.js
    public void Sprachbefehle_und_Fachwoerter_wie_in_der_App()
    {
        Assert.Equal("Kratzer, Delle.", DiktatRegeln.Sprachbefehle("Kratzer Komma Delle Punkt"));
        Assert.Equal("Kratzer, Delle.", DiktatRegeln.Sprachbefehle("Kratzer, Komma, Delle, Punkt."));
        Assert.Equal("Kratzer hinten\nDelle vorne", DiktatRegeln.Sprachbefehle("Kratzer hinten neue Zeile Delle vorne"));
        Assert.Equal("Achtung: zwei Schlüssel", DiktatRegeln.Sprachbefehle("Achtung Doppelpunkt zwei Schlüssel"));
        Assert.Equal("Alles gut. Zwei Schlüssel.", DiktatRegeln.Sprachbefehle("alles gut Punkt zwei Schlüssel Punkt"));
        Assert.Equal("Druckpunkt der Kupplung hoch", DiktatRegeln.Sprachbefehle("Druckpunkt der Kupplung hoch"));
        Assert.Equal("Kommata", DiktatRegeln.Sprachbefehle("Kommata"));
        Assert.Equal("AHK abnehmbar, TÜV neu, HU bis 2027", DiktatRegeln.FachwoerterSchreiben("A H K abnehmbar, Tüv neu, H.U. bis 2027"));
        Assert.Equal("Zahnriemen neu, Steinschlag", DiktatRegeln.FachwoerterSchreiben("Zahn Riemen neu, Stein Schlag"));
        Assert.Equal("Hupe geht, Hut ab", DiktatRegeln.FachwoerterSchreiben("Hupe geht, Hut ab"));
        Assert.Equal("Das Auto ist scheckheftgepflegt, Scheckheft liegt bei",
            DiktatRegeln.FachwoerterSchreiben("Das Auto ist checkheft gepflegt, Checkheft liegt bei"));
        Assert.Equal("TÜV neu, AHK", DiktatRegeln.Nachbearbeiten("Tüv neu Komma A H K"));
        // am PC: "neue Zeile" wird beim Eintippen ein Leerzeichen (Enter wuerde Formulare abschicken)
        Assert.Equal("Kratzer Delle", Diktat.FuerEingabe(DiktatRegeln.Nachbearbeiten("Kratzer neue Zeile Delle")));
    }

    [Fact]
    public void Pegel_wird_aus_dem_Ton_berechnet()
    {
        Assert.Equal(-160f, Diktat.PegelDb(new byte[200], 200));
        var laut = new byte[200];
        for (int i = 0; i < 200; i += 2) BitConverter.GetBytes((short)(i % 4 == 0 ? 16384 : -16384)).CopyTo(laut, i);
        Assert.InRange(Diktat.PegelDb(laut, 200), -6.1f, -5.9f);          // halbe Aussteuerung = -6 dB
        Assert.Equal("▮▮▮▯▯", Diktat.PegelBalken(0.6));
        Assert.Equal("▯▯▯▯▯", Diktat.PegelBalken(-1));
    }

    [Fact]   // Auto-Stopp erst, wenn gesprochen wurde; Grundrauschen zaehlt nicht als Sprache
    public void Stille_zaehlt_erst_nach_dem_Sprechen()
    {
        var m = new Diktat.StilleMesser();
        long t = 0;
        for (int i = 0; i < 30; i++, t += 100) m.Messen(-55f, t);         // 3 s leises Rauschen
        Assert.Equal(-1, m.StilleMs(t));
        for (int i = 0; i < 10; i++, t += 100) m.Messen(-20f, t);         // 1 s Sprache
        Assert.Equal(100, m.StilleMs(t));
        for (int i = 0; i < 21; i++, t += 100) m.Messen(-54f, t);         // 2,1 s Pause
        Assert.True(m.StilleMs(t) >= Diktat.AutoStoppMs);
        m.Messen(-22f, t);                                                 // weiter gesprochen
        Assert.Equal(0, m.StilleMs(t));
    }

    [Fact]   // im lauten Raum: Rauschen bei -35 dB ist keine Sprache
    public void Lautes_Rauschen_ist_keine_Sprache()
    {
        var m = new Diktat.StilleMesser();
        for (long t = 0; t < 3000; t += 100) m.Messen(-35f + (t % 300 == 0 ? 2f : 0f), t);
        Assert.Equal(-1, m.StilleMs(3000));
    }

    [Fact]
    public void Leiste_zeigt_den_Diktat_Zustand()
    {
        Exception? fehler = null;
        var t = new Thread(() =>
        {
            try
            {
                var zustand = new FensterZustand(Status.Aktiv, true, true, "Konto 10002", null, false, null, false);
                using var l = new Leiste(() => zustand, () => IntPtr.Zero);
                bool geklickt = false;
                l.Diktieren += () => geklickt = true;
                l.Show();
                Application.DoEvents();
                Assert.Equal("Diktat", l.DiktatText);
                l.DiktatAnzeigen("Stopp 0:07", aufnahme: true);
                Assert.Equal("Stopp 0:07", l.DiktatText);
                l.DiktatAnzeigen(null, aufnahme: false);
                Assert.Equal("Diktat", l.DiktatText);
                // Rückgängig-Knopf: erst nach einem Diktat sichtbar
                Assert.False(l.DiktatZuruecknehmenSichtbar);
                bool zurueck = false;
                l.DiktatZuruecknehmen += () => zurueck = true;
                l.DiktatZuruecknehmenZeigen(true);
                Application.DoEvents();
                Assert.True(l.DiktatZuruecknehmenSichtbar);
                var zk = typeof(Leiste).GetField("_zurueck", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(l) as Button;
                zk!.PerformClick();
                Assert.True(zurueck);
                l.DiktatZuruecknehmenZeigen(false);
                Assert.False(l.DiktatZuruecknehmenSichtbar);
                var knopf = typeof(Leiste).GetField("_diktat", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(l) as Button;
                knopf!.PerformClick();
                Assert.True(geklickt);
            }
            catch (Exception ex) { fehler = ex; }
        });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (fehler != null) throw fehler;
    }
}
