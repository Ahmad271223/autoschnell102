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
