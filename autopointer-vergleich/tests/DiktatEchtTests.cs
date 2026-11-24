using System.Diagnostics;
using NAudio.Wave;
using Xunit;
using Xunit.Abstractions;

namespace AutoPointerVergleich.Tests;

/// <summary>
/// Echte Erkennung mit dem Sprachmodell (574 MB) — laeuft nur, wenn DIKTAT_WAV_ORDNER auf einen Ordner mit
/// 16-kHz-Mono-WAVs zeigt und das Modell unter %LOCALAPPDATA%\AutoSchnell\Diktat liegt (sonst leer bestanden).
/// Zu jeder datei.wav darf eine datei.txt mit Woertern liegen, die im Ergebnis vorkommen muessen (eins je Zeile).
/// </summary>
public class DiktatEchtTests
{
    private readonly ITestOutputHelper _aus;
    public DiktatEchtTests(ITestOutputHelper aus) => _aus = aus;

    [Fact]
    public async Task Deutsche_Sprache_wird_erkannt()
    {
        string? ordner = Environment.GetEnvironmentVariable("DIKTAT_WAV_ORDNER");
        if (string.IsNullOrEmpty(ordner) || !Directory.Exists(ordner)) return;
        using var d = new Diktat();
        Assert.True(d.ModellVorhanden, "Sprachmodell fehlt unter " + d.ModellPfad);
        // Messung Fachwoerter-Vorabtext (24.11.2026): DIKTAT_PROMPT="AHK, TÜV, HU, …"
        d.Hinweistext = Environment.GetEnvironmentVariable("DIKTAT_PROMPT");
        _aus.WriteLine("Vorabtext: " + (d.Hinweistext ?? "(keiner)"));

        var laden = Stopwatch.StartNew();
        d.Vorbereiten();
        _aus.WriteLine($"Modell laden: {laden.Elapsed.TotalSeconds:F1} s");
        int runden = int.TryParse(Environment.GetEnvironmentVariable("DIKTAT_RUNDEN"), out var r) ? r : 1;
        foreach (var wav in Enumerable.Repeat(Directory.GetFiles(ordner, "*.wav").OrderBy(x => x), runden).SelectMany(x => x))
        {
            var proben = new List<float>();
            using (var leser = new WaveFileReader(wav))
            {
                Assert.Equal(16000, leser.WaveFormat.SampleRate);
                Assert.Equal(1, leser.WaveFormat.Channels);
                var bytes = new byte[leser.Length];
                int n = leser.Read(bytes, 0, bytes.Length);
                Diktat.PcmAnhaengen(proben, bytes, n, int.MaxValue);
            }
            var uhr = Stopwatch.StartNew();
            string roh = await d.ErkennenAsync(proben.ToArray(), CancellationToken.None);
            uhr.Stop();
            string text = DiktatRegeln.Nachbearbeiten(roh);
            if (text != roh) _aus.WriteLine($"  roh: \"{roh}\"");
            double sek = proben.Count / 16000.0;
            _aus.WriteLine($"{Path.GetFileName(wav)}: {sek:F1} s Ton, {uhr.Elapsed.TotalSeconds:F1} s Erkennung → \"{text}\"");

            string erwartet = Path.ChangeExtension(wav, ".txt");
            if (File.Exists(erwartet))
                foreach (var wort in File.ReadAllLines(erwartet).Where(w => w.Trim().Length > 0))
                    Assert.Contains(wort.Trim(), text, StringComparison.OrdinalIgnoreCase);
        }
    }
}
