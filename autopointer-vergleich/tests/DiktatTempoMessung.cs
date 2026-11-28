using System.Diagnostics;
using NAudio.Wave;
using Whisper.net;
using Xunit;
using Xunit.Abstractions;

namespace AutoPointerVergleich.Tests;

/// <summary>Messung (nur mit DIKTAT_TEMPO=1 und DIKTAT_WAV_ORDNER): welche Whisper-Einstellung ist am PC schnell genug?
/// Ergebnis 24.11.2026 (Ryzen 7 260, Radeon 780M, large-v3-turbo q5_0): CPU 8 Threads 15,5 s, + Flash 13 s, 16 Threads
/// 10-13 s; Grafikkarte (Vulkan) 0,3-0,8 s (erstes Mal je PC ~30 s Treiber-Einrichtung). Verkuerztes Audio-Fenster
/// (audio_ctx) 2,5-8 s, aber Whisper wiederholt dann Saetze und braucht manchmal 30 s — nicht verwendet.</summary>
public class DiktatTempoMessung
{
    private readonly ITestOutputHelper _aus;
    public DiktatTempoMessung(ITestOutputHelper aus) => _aus = aus;

    [Fact]
    public async Task Varianten_messen()
    {
        string? ordner = Environment.GetEnvironmentVariable("DIKTAT_WAV_ORDNER");
        if (Environment.GetEnvironmentVariable("DIKTAT_TEMPO") != "1" || string.IsNullOrEmpty(ordner)) return;
        using var d = new Diktat();
        d.LaufzeitBereitstellen();
        _aus.WriteLine(WhisperFactory.GetRuntimeInfo());
        var dateien = Directory.GetFiles(ordner, "*.wav").OrderBy(x => x).Select(w => (Name: Path.GetFileName(w), Proben: Lesen(w))).ToList();

        foreach (var (name, flash, threads, ctx) in new[]
                 {
                     ("flash+16", true, 16, false), ("nochmal", true, 16, false),
                 })
        {
            using var fabrik = WhisperFactory.FromPath(d.ModellPfad, new WhisperFactoryOptions { UseFlashAttention = flash });
            foreach (var (datei, proben) in dateien)
            {
                var b = fabrik.CreateBuilder().WithLanguage("de").WithThreads(threads).WithNoContext();
                if (ctx) b = b.WithAudioContextSize(Math.Min(1500, (int)Math.Ceiling(proben.Length / 16000.0 * 50) + threads));
                await using var p = b.Build();
                var uhr = Stopwatch.StartNew();
                var teile = new List<string>();
                await foreach (var s in p.ProcessAsync(proben)) teile.Add(s.Text);
                _aus.WriteLine($"{name,-10} {datei}: {uhr.Elapsed.TotalSeconds:F1} s → {Diktat.TextAufbereiten(teile)}");
            }
        }
    }

    private static float[] Lesen(string wav)
    {
        var proben = new List<float>();
        using var leser = new WaveFileReader(wav);
        var bytes = new byte[leser.Length];
        int n = leser.Read(bytes, 0, bytes.Length);
        Diktat.PcmAnhaengen(proben, bytes, n, int.MaxValue);
        return proben.ToArray();
    }
}
