using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>1.5.11 (Wunsch Ahmad 08.10.2026 abends): "Vertrag" mit Erweiterung — erst Inserat oeffnen und lesen lassen,
/// nie Apify.</summary>
public class VertragsWegTests
{
    private sealed class Lauf
    {
        public readonly Queue<bool?> Antworten = new();
        public int Fragen, Geoeffnet;
        public readonly List<(string Text, bool Fehler)> Meldungen = new();
        public long Uhr;
        public Task<bool?> Gelesen(string _) { Fragen++; return Task.FromResult(Antworten.Count > 0 ? Antworten.Dequeue() : false); }
        public Task Warte(TimeSpan t) { Uhr += (long)t.TotalMilliseconds; return Task.CompletedTask; }
        public Task<bool> Starte() => VertragsWeg.InseratBereitAsync("https://suchen.mobile.de/fahrzeuge/details.html?id=1",
            Gelesen, () => Geoeffnet++, (t, f) => Meldungen.Add((t, f)), Warte, () => Uhr);
    }

    [Fact]
    public async Task Schon_gelesen_oeffnet_kein_Inserat()
    {
        var l = new Lauf();
        l.Antworten.Enqueue(true);
        Assert.True(await l.Starte());
        Assert.Equal(0, l.Geoeffnet);
        Assert.Empty(l.Meldungen);
    }

    [Fact]
    public async Task Nicht_gelesen_oeffnet_das_Inserat_und_wartet_auf_die_Lesung()
    {
        var l = new Lauf();
        foreach (var a in new bool?[] { false, null, false, true }) l.Antworten.Enqueue(a);
        Assert.True(await l.Starte());
        Assert.Equal(1, l.Geoeffnet);
        Assert.Equal(4, l.Fragen);
        Assert.Equal(VertragsWeg.WirdGelesen, Assert.Single(l.Meldungen).Text);
    }

    [Fact]
    public async Task Kommt_die_Lesung_nicht_nur_ein_Hinweis()
    {
        var l = new Lauf();
        Assert.False(await l.Starte());
        Assert.Equal(1, l.Geoeffnet);
        Assert.Equal((VertragsWeg.NichtGelesen, true), l.Meldungen[^1]);
        Assert.True(l.Uhr >= VertragsWeg.WarteMs);
        Assert.True(VertragsWeg.NichtGelesen.Length <= 150 && VertragsWeg.WirdGelesen.Length <= 150);
    }
}
