using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>1.5.11 (Wunsch Ahmad 08.10.2026 abends): "Vertrag" mit Erweiterung — erst Inserat oeffnen und lesen lassen,
/// nie Apify. Pruefung 09.10.2026 (Vertragsweg, 1.5.15, Befund Ahmad "Kaufvertrag erstellen aus den Programmen heraus haengt
/// haeufig"): der Weg endet nie stumm — jeder Ausgang ist ein <see cref="LesungsStand"/>; danach fragt das Programm die
/// App bis 20 s, WAS sie mit dem Ziel gemacht hat (<see cref="AppStartWeg"/>).</summary>
public class VertragsWegTests
{
    private sealed class Lauf
    {
        public readonly Queue<InseratStand> Antworten = new();
        public int Fragen, Geoeffnet;
        public readonly List<(string Text, bool Fehler)> Meldungen = new();
        public long Uhr;
        public Task<InseratStand> Gelesen(string _)
        {
            Fragen++;
            return Task.FromResult(Antworten.Count > 0 ? Antworten.Dequeue() : new InseratStand(false));
        }
        public Task Warte(TimeSpan t) { Uhr += (long)t.TotalMilliseconds; return Task.CompletedTask; }
        public Task<LesungsStand> Starte() => VertragsWeg.InseratBereitAsync("https://suchen.mobile.de/fahrzeuge/details.html?id=1",
            Gelesen, () => Geoeffnet++, (t, f) => Meldungen.Add((t, f)), Warte, () => Uhr);
        public void Antwort(params bool?[] gelesen)
        {
            foreach (var g in gelesen) Antworten.Enqueue(new InseratStand(g));
        }
    }

    [Fact]
    public async Task Schon_gelesen_oeffnet_kein_Inserat()
    {
        var l = new Lauf();
        l.Antwort(true);
        Assert.Equal(LesungsStand.Gelesen, await l.Starte());
        Assert.Equal(0, l.Geoeffnet);
        Assert.Empty(l.Meldungen);
    }

    [Fact]
    public async Task Nicht_gelesen_oeffnet_das_Inserat_und_wartet_auf_die_Lesung()
    {
        var l = new Lauf();
        l.Antwort(false, null, false, true);       // ein "nicht pruefbar" MITTEN im Warten bricht nicht ab
        Assert.Equal(LesungsStand.Gelesen, await l.Starte());
        Assert.Equal(1, l.Geoeffnet);
        Assert.Equal(4, l.Fragen);
        Assert.Equal(VertragsWeg.WirdGelesen, Assert.Single(l.Meldungen).Text);
    }

    [Fact]   // Pruefung 09.10.2026 (3d): nach der Wartezeit kein stummes Ende — Hinweis und "NichtGelesen" (die App liest auf Knopfdruck)
    public async Task Kommt_die_Lesung_nicht_Hinweis_und_NichtGelesen()
    {
        var l = new Lauf();
        Assert.Equal(LesungsStand.NichtGelesen, await l.Starte());
        Assert.Equal(1, l.Geoeffnet);
        Assert.Equal((VertragsWeg.NichtGelesen, true), l.Meldungen[^1]);
        Assert.True(l.Uhr >= VertragsWeg.WarteMs);
        Assert.True(VertragsWeg.NichtGelesen.Length <= 150 && VertragsWeg.WirdGelesen.Length <= 150 && VertragsWeg.NichtPruefbar.Length <= 150);
    }

    [Fact]   // Pruefung 09.10.2026 (3e): 40 s statt 25, Takt 1,5 s
    public void Wartezeit_40_Sekunden_im_Takt_von_anderthalb()
    {
        Assert.Equal(40_000, VertragsWeg.WarteMs);
        Assert.Equal(1500, VertragsWeg.TaktMs);
    }

    [Fact]   // Pruefung 09.10.2026 (3c): AutoSchnell antwortet auf die ERSTE Frage nicht -> nicht 40 s warten, Kaufvertrag trotzdem
    public async Task Server_antwortet_nicht_dann_sofort_weiter_ohne_Inserat_zu_oeffnen()
    {
        var l = new Lauf();
        l.Antwort((bool?)null);
        Assert.Equal(LesungsStand.NichtPruefbar, await l.Starte());
        Assert.Equal(0, l.Geoeffnet);
        Assert.Equal(1, l.Fragen);
        Assert.Equal(0, l.Uhr);
        Assert.Equal((VertragsWeg.NichtPruefbar, true), Assert.Single(l.Meldungen));
    }

    [Fact]   // Pruefung 09.10.2026 (3f): 402/403 vom Server -> Servertext als Sprechblase, kein Vertrag (auch mitten im Warten)
    public async Task Sperre_des_Servers_bricht_ab_mit_dem_Servertext()
    {
        var l = new Lauf();
        l.Antworten.Enqueue(new InseratStand(null, "Für dieses Konto ist kein Sucher-Abo aktiv."));
        Assert.Equal(LesungsStand.Gesperrt, await l.Starte());
        Assert.Equal(0, l.Geoeffnet);
        Assert.Equal(("Für dieses Konto ist kein Sucher-Abo aktiv.", true), Assert.Single(l.Meldungen));

        var m = new Lauf();
        m.Antwort(false, false);
        m.Antworten.Enqueue(new InseratStand(null, "Gesperrt."));
        Assert.Equal(LesungsStand.Gesperrt, await m.Starte());
        Assert.Equal(1, m.Geoeffnet);
        Assert.Equal(("Gesperrt.", true), m.Meldungen[^1]);
        Assert.True(m.Uhr < VertragsWeg.WarteMs);
    }

    // ------------------------------------------------------------------ 3a: Inserat zum Lesen im Browser der Erweiterung
    [Fact]
    public void Inserat_zum_Lesen_geht_in_den_Browser_der_Erweiterung()
    {
        Assert.Equal(BrowserWahl.Chrome, Ueberwacher.BrowserFuerLesung(BrowserWahl.Edge, "chrome"));      // Einstellung Edge, Helfer in Chrome
        Assert.Equal(BrowserWahl.Edge, Ueberwacher.BrowserFuerLesung(BrowserWahl.Standard, "edge"));
        Assert.Equal(BrowserWahl.Edge, Ueberwacher.BrowserFuerLesung(BrowserWahl.Edge, ""));              // Helfer unbekannt: Einstellung
        Assert.Equal(BrowserWahl.Standard, Ueberwacher.BrowserFuerLesung(BrowserWahl.Standard, null));
        // die Vergleichs-Links bleiben bei der Einstellung, wenn eine gesetzt ist
        Assert.Equal(BrowserWahl.Edge, Ueberwacher.BrowserFuer(BrowserWahl.Edge, "chrome"));
    }

    // ------------------------------------------------------------------ P5: was die App mit dem Ziel gemacht hat
    private sealed class AppLauf
    {
        public readonly Queue<AppStartAntwort> Antworten = new();
        public int Fragen;
        public long Uhr;
        public Task<AppStartAntwort> Frage()
        {
            Fragen++;
            return Task.FromResult(Antworten.Count > 0 ? Antworten.Dequeue() : new AppStartAntwort(false));
        }
        public Task Warte(TimeSpan t) { Uhr += (long)t.TotalMilliseconds; return Task.CompletedTask; }
        public Task<string?> Starte() => AppStartWeg.WartenAsync(Frage, Warte, () => Uhr);
    }

    [Fact]
    public void App_Zustand_gemeldet_oder_weiter_warten()
    {
        Assert.Null(AppStartWeg.Gemeldet(new AppStartAntwort(false)));                               // noch nichts
        Assert.Null(AppStartWeg.Gemeldet(new AppStartAntwort(false, AppStartWeg.Offen)));
        Assert.Equal(AppStartWeg.Offen, AppStartWeg.Gemeldet(new AppStartAntwort(true)));            // aelterer Server: nur bestaetigt
        Assert.Equal(AppStartWeg.Offen, AppStartWeg.Gemeldet(new AppStartAntwort(true, AppStartWeg.Offen)));
        // ein Zustand ausser "offen" zaehlt auch ohne "bestaetigt" — die App hat das Ziel ja bekommen
        Assert.Equal(AppStartWeg.Nachgefragt, AppStartWeg.Gemeldet(new AppStartAntwort(false, AppStartWeg.Nachgefragt)));
        Assert.Equal(AppStartWeg.Anmeldung, AppStartWeg.Gemeldet(new AppStartAntwort(true, AppStartWeg.Anmeldung)));
        Assert.Equal(AppStartWeg.Abo, AppStartWeg.Gemeldet(new AppStartAntwort(false, AppStartWeg.Abo)));
    }

    [Fact]
    public async Task App_meldet_sich_dann_ihr_Zustand_und_Ende()
    {
        var l = new AppLauf();
        l.Antworten.Enqueue(new AppStartAntwort(false));
        l.Antworten.Enqueue(new AppStartAntwort(false, AppStartWeg.Nachgefragt));
        Assert.Equal(AppStartWeg.Nachgefragt, await l.Starte());
        Assert.Equal(2, l.Fragen);
        Assert.Equal(2 * AppStartWeg.TaktMs, l.Uhr);
    }

    [Fact]   // 20 s statt 10 — dann null (Browser)
    public async Task App_meldet_sich_nicht_dann_null_nach_20_Sekunden()
    {
        var l = new AppLauf();
        Assert.Null(await l.Starte());
        Assert.Equal(20_000, AppStartWeg.WarteMs);
        Assert.True(l.Uhr >= AppStartWeg.WarteMs && l.Uhr < AppStartWeg.WarteMs + AppStartWeg.TaktMs);
        Assert.True(l.Fragen >= 25);
    }

    [Fact]
    public void Meldung_je_Zustand_kurz_und_deutsch()
    {
        Assert.Equal("Kaufvertrag in der AutoSchnell-App geöffnet.", AppStartWeg.Meldung(AppStartWeg.Offen));
        Assert.Contains("„Hier öffnen“", AppStartWeg.Meldung(AppStartWeg.Nachgefragt));
        Assert.Contains("anmelden", AppStartWeg.Meldung(AppStartWeg.Anmeldung));
        Assert.Contains("Sucher-Abo", AppStartWeg.Meldung(AppStartWeg.Abo));
        Assert.Contains("im Browser geöffnet", AppStartWeg.NichtGemeldet);
        foreach (var z in new[] { AppStartWeg.Offen, AppStartWeg.Nachgefragt, AppStartWeg.Anmeldung, AppStartWeg.Abo })
            Assert.True(AppStartWeg.Meldung(z).Length <= 150, z);
    }

    [Fact]   // nur bekannte Zustaende; alles andere (aelterer Server, Unbekanntes) gilt als "offen"
    public void Zustand_aus_der_Serverantwort()
    {
        Assert.Equal(AppStartWeg.Nachgefragt, AutoSchnellDienst.AppStartZustand(" Nachgefragt "));
        Assert.Equal(AppStartWeg.Anmeldung, AutoSchnellDienst.AppStartZustand("anmeldung"));
        Assert.Equal(AppStartWeg.Abo, AutoSchnellDienst.AppStartZustand("abo"));
        Assert.Equal(AppStartWeg.Offen, AutoSchnellDienst.AppStartZustand("offen"));
        Assert.Equal(AppStartWeg.Offen, AutoSchnellDienst.AppStartZustand(""));
        Assert.Equal(AppStartWeg.Offen, AutoSchnellDienst.AppStartZustand(null));
        Assert.Equal(AppStartWeg.Offen, AutoSchnellDienst.AppStartZustand("quatsch"));
    }

    // ------------------------------------------------------------------ P1: der Knopf sagt, dass es laeuft
    [Fact]
    public void Leiste_zeigt_Vertrag_laeuft()
    {
        var ruhe = new FensterZustand(Status.Aktiv, true, true, "x", "VW Golf · EZ 2020", true, null, false);
        Assert.Equal("Vertrag", Leiste.VertragText(ruhe));
        Assert.Equal("Vertrag …", Leiste.VertragText(ruhe with { VertragLaeuft = true }));
        Assert.True(TrayApp.VertragLaeuftSchon.Length <= 150);
    }
}
