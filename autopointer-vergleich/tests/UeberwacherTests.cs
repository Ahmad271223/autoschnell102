using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

/// <summary>Ablauf: Wartezeit, kein Doppel-Oeffnen, schnelles Wechseln, Mindestabstand.</summary>
public class UeberwacherTests
{
    private sealed class Attrappe : IAnsichtQuelle
    {
        public Lage Lage = Lage.KeineDetails;
        public ulong Summe;
        public Func<Fahrzeug>? Fahrzeug;
        public Action? WaehrendDesLesens;
        public int Lesungen;
        public IntPtr Hauptfenster => IntPtr.Zero;
        public QuellenZustand Pruefe() => new(Lage, Lage == Lage.Details ? Summe : 0);

        public Task<Lesung?> LiesAsync()
        {
            Lesungen++;
            var f = Fahrzeug!();
            WaehrendDesLesens?.Invoke();
            return Task.FromResult<Lesung?>(new Lesung(f, f.MarkeModellText.Length == 0 && f.Kilometer == null, "roh"));
        }

        public void Zeige(Func<Fahrzeug> f, ulong summe)
        {
            Lage = Lage.Details;
            Fahrzeug = f;
            Summe = summe;
        }
    }

    private sealed class Browser : IOeffner
    {
        public readonly List<IReadOnlyList<Vergleich>> Aufrufe = new();
        public void Oeffne(IReadOnlyList<Vergleich> v, Einstellungen e, IntPtr ap) => Aufrufe.Add(v);
    }

    private readonly Attrappe _q = new();
    private readonly Browser _b = new();
    private readonly Einstellungen _e = new();
    private readonly List<string> _meldungen = new();
    private DateTime _jetzt = new(2026, 10, 3, 12, 0, 0);
    private readonly List<TimeSpan> _gewartet = new();
    private readonly Ueberwacher _u;

    public UeberwacherTests()
    {
        Protokoll.DateiAktiv = false;
        _u = new Ueberwacher(_q, Kat, () => _e, _b, () => _jetzt, t =>
        {
            _gewartet.Add(t);
            _jetzt += t;
            return Task.CompletedTask;
        });
        _u.Meldung += (t, _) => _meldungen.Add(t);
        _u.Neustart();
    }

    private async Task Tick(int ms = 250)
    {
        await _u.TickAsync();
        _jetzt = _jetzt.AddMilliseconds(ms);
    }

    /// <summary>Fahrzeug anzeigen und so lange ticken, bis die Wartezeit um ist.</summary>
    private async Task Anklicken(Func<Fahrzeug> f, ulong summe)
    {
        _q.Zeige(f, summe);
        for (int i = 0; i < 4; i++) await Tick();
    }

    private static Fahrzeug Golf() => DetailLeser.Auswerten(new[]
    {
        Z("Marke, Modell:", 12, 4), Z("VW Golf", 212, 4),
        Z("Erstzulassung:", 12, 26), Z("05/2019", 212, 26),
        Z("Kilometerstand:", 12, 48), Z("61.000 km", 212, 48),
        Z("Leistung:", 12, 70), Z("110 kW (150 PS)", 212, 70),
    }, Array.Empty<OcrZeile>(), 0);

    private async Task Start()
    {
        await Tick();          // AutoPointer zeigt noch nichts -> kein Startzustand
    }

    [Fact]
    public async Task Anklicken_oeffnet_beide_Vergleiche_erst_nach_der_Wartezeit()
    {
        await Start();
        _q.Zeige(Bentley, 100);
        await Tick(100);   // Aenderung bemerkt
        await Tick(100);   // 100 ms stabil - noch zu frueh (Wartezeit 400)
        Assert.Empty(_b.Aufrufe);
        await Tick(250);
        await Tick(250);
        Assert.Single(_b.Aufrufe);
        Assert.Equal(new[] { "mobile.de", "AutoScout24" }, _b.Aufrufe[0].Select(v => v.Portal));
    }

    [Fact]
    public async Task Gleiches_Fahrzeug_oeffnet_nicht_erneut_auch_wenn_sich_die_Anzeige_aendert()
    {
        await Start();
        await Anklicken(Bentley, 100);
        await Anklicken(Bentley, 101);   // z. B. "Neu"-Markierung weg, Bild nachgeladen
        await Anklicken(Bentley, 102);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Schnelles_Wechseln_A_B_C_jeweils_eigener_Vergleich()
    {
        await Start();
        await Anklicken(Bentley, 1);
        await Anklicken(Passat, 2);
        await Anklicken(Golf, 3);
        Assert.Equal(3, _b.Aufrufe.Count);
        Assert.Contains("ms=3100%3B16", _b.Aufrufe[0][0].Url);
        Assert.Contains("ms=25200%3B63", _b.Aufrufe[1][0].Url);
        Assert.Contains("fr=2019%3A2019", _b.Aufrufe[2][0].Url);
    }

    [Fact]
    public async Task Zwischenstand_kuerzer_als_Wartezeit_wird_uebersprungen()
    {
        await Start();
        _q.Zeige(Bentley, 1);
        await Tick(100);
        _q.Zeige(Passat, 2);          // nach 100 ms schon weiter
        await Tick(100);
        await Tick(250);
        await Tick(250);
        await Tick(250);
        Assert.Single(_b.Aufrufe);
        Assert.Contains("ms=25200%3B63", _b.Aufrufe[0][0].Url);
        Assert.Equal(1, _q.Lesungen);
    }

    [Fact]
    public async Task Aenderung_waehrend_des_Lesens_wird_verworfen_keine_Mischdaten()
    {
        await Start();
        _q.Zeige(Bentley, 1);
        _q.WaehrendDesLesens = () => { _q.Fahrzeug = Passat; _q.Summe = 2; _q.WaehrendDesLesens = null; };
        await Tick(250);
        await Tick(250);
        await Tick(250);   // liest Bentley, aber Anzeige ist inzwischen Passat -> verwerfen
        Assert.Empty(_b.Aufrufe);
        await Tick(250);
        await Tick(250);
        await Tick(250);
        Assert.Single(_b.Aufrufe);
        Assert.Contains("ms=25200%3B63", _b.Aufrufe[0][0].Url);
    }

    [Fact]
    public async Task Unvollstaendig_erkannt_oeffnet_nichts_und_meldet_einmal()
    {
        await Start();
        Fahrzeug OhneEz()
        {
            var f = Bentley();
            f.EzJahr = null;
            f.EzMonat = null;
            return f;
        }
        await Anklicken(OhneEz, 5);
        await Anklicken(OhneEz, 5);
        Assert.Empty(_b.Aufrufe);
        Assert.Single(_meldungen, m => m.StartsWith("Fahrzeug konnte nicht eindeutig erkannt werden"));
    }

    [Fact]
    public async Task Beim_Start_angezeigtes_Fahrzeug_oeffnet_nicht_das_naechste_schon()
    {
        _q.Zeige(Bentley, 1);       // schon da, bevor das Programm startet
        for (int i = 0; i < 5; i++) await Tick();
        Assert.Empty(_b.Aufrufe);
        Assert.NotNull(_u.LetztesFahrzeug);
        await Anklicken(Passat, 2);
        Assert.Single(_b.Aufrufe);
    }

    [Fact]
    public async Task Automatik_aus_oeffnet_nichts()
    {
        await Start();
        _e.AutomatikAktiv = false;
        await Anklicken(Bentley, 1);
        Assert.Empty(_b.Aufrufe);
        Assert.Equal(Status.Pause, _u.Status);
        Assert.Equal(0, _q.Lesungen);
    }

    [Fact]
    public async Task Jetzt_vergleichen_oeffnet_auch_dasselbe_Fahrzeug()
    {
        await Start();
        await Anklicken(Bentley, 1);
        await _u.JetztVergleichenAsync();
        Assert.Equal(2, _b.Aufrufe.Count);
    }

    [Fact]
    public async Task Mindestabstand_zwischen_zwei_Vergleichen()
    {
        await Start();
        _e.WartezeitMs = 100;
        _e.MindestabstandMs = 2000;
        await Anklicken(Bentley, 1);
        await Anklicken(Passat, 2);
        Assert.Equal(2, _b.Aufrufe.Count);
        Assert.Single(_gewartet);
        Assert.True(_gewartet[0] > TimeSpan.FromMilliseconds(500));
    }

    [Fact]
    public async Task Ohne_AutoPointer_kein_Lesen()
    {
        _q.Lage = Lage.KeinAutoPointer;
        await Tick();
        await Tick();
        Assert.Equal(Status.KeinAutoPointer, _u.Status);
        Assert.Equal(0, _q.Lesungen);
    }

    [Fact]
    public async Task Modell_unbekannt_meldet_statt_nur_Marke_zu_suchen()
    {
        await Start();
        Fahrzeug Unbekannt()
        {
            var f = Bentley();
            f.MarkeModellText = "Bentley Gibtsnicht";
            return f;
        }
        await Anklicken(Unbekannt, 9);
        Assert.Empty(_b.Aufrufe);
        Assert.Contains(_meldungen, m => m.Contains("kein Vergleich geöffnet"));
    }
}
