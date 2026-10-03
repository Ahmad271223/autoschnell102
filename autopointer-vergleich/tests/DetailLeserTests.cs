using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

[Collection("Protokolldateien")]   // setzen Protokoll.DateiAktiv — nicht parallel zu TresorTests
public class DetailLeserTests
{
    [Fact]   // Befund 03.10.2026: BYD Dolphin (mobile.de, Neuwagen) — AutoPointer zeigt weder EZ noch km
    public void Neuwagen_ohne_Erstzulassung_und_Kilometer()
    {
        var f = new Fahrzeug { MarkeModellText = "BYD DOLPHIN", Zustand = "Neu" };
        DetailLeser.NeuwagenErgaenzen(f, new DateTime(2026, 10, 3));
        Assert.Equal((2026, (int?)null, 0), (f.EzJahr!.Value, f.EzMonat, f.Kilometer!.Value));
        Assert.Empty(DetailLeser.Fehlend(f));

        var gebraucht = new Fahrzeug { MarkeModellText = "BYD DOLPHIN", Zustand = "Gebraucht" };
        DetailLeser.NeuwagenErgaenzen(gebraucht, new DateTime(2026, 10, 3));
        Assert.Contains("Erstzulassung", DetailLeser.Fehlend(gebraucht));

        var mitEz = new Fahrzeug { MarkeModellText = "BYD DOLPHIN", Zustand = "Neuwagen", EzJahr = 2025, EzMonat = 12, Kilometer = 10 };
        DetailLeser.NeuwagenErgaenzen(mitEz, new DateTime(2026, 10, 3));
        Assert.Equal((2025, (int?)12, 10), (mitEz.EzJahr!.Value, mitEz.EzMonat, mitEz.Kilometer!.Value));
    }

    [Fact]
    public void AutoPointer_zeichnen_lassen_ist_standardmaessig_aus() =>
        Assert.False(new Einstellungen().AutoPointerZeichnenLassen);

    public DetailLeserTests() => Protokoll.DateiAktiv = false;

    [Fact]
    public void Passat_alle_Felder_aus_echter_Texterkennung()
    {
        var f = Passat();
        Assert.Equal("VW Passat Variant", f.MarkeModellText);
        Assert.Equal(10, f.EzMonat);
        Assert.Equal(2006, f.EzJahr);
        Assert.Equal(244000, f.Kilometer);
        Assert.Equal(125, f.Kw);
        Assert.Equal(170, f.Ps);
        Assert.Equal("Automatik", f.Getriebe);
        Assert.Equal("Diesel", f.Kraftstoff);
        Assert.Equal(1500, f.Preis);
        Assert.Equal("Unfallwagen", f.Zustand);
        Assert.True(f.Unfallwagen);
        Assert.Equal("3529712138", f.InseratId);
        Assert.Equal("4/5", f.Tueren);
        Assert.Equal("Kleinanzeigen", f.Quelle);
        Assert.Equal("VW Passat B6 - Bastlerfahrzeug", f.Titel);
        Assert.Empty(DetailLeser.Fehlend(f));
    }

    [Fact]
    public void Bentley_mit_Lesefehlern_in_den_Bezeichnungen()
    {
        var f = Bentley();
        Assert.Equal("Bentley Bentayga", f.MarkeModellText);
        Assert.Equal((3, 2017), (f.EzMonat!.Value, f.EzJahr!.Value));
        Assert.Equal(84975, f.Kilometer);
        Assert.Equal(320, f.Kw);
        Assert.Equal(435, f.Ps);
        Assert.Equal(99900, f.Preis);
        Assert.Equal("mobile.de", f.Quelle);
        Assert.Equal("Bentley Bentayga V8 Diesel, 1. Hand, Mulliner, Nai", f.Titel);
        Assert.False(f.Unfallwagen);
    }

    [Theory]
    [InlineData("Kibmeterstand", "Kilometer")]
    [InlineData("Khmeterstand:", "Kilometer")]
    [InlineData("Getriebeatt", "Getriebe")]
    [InlineData("Marke, Model", "MarkeModell")]
    [InlineData("Omatisierung", "Klimatisierung")]
    [InlineData("Umwettplakette", "Umweltplakette")]
    [InlineData("Herstellerfarbe", "Herstellerfarbe")]
    [InlineData("Farbe", "Farbe")]
    [InlineData("Inserat-ID", "InseratId")]
    [InlineData("Türen", "Tueren")]
    [InlineData("preis", "Preis")]
    public void Bezeichnungen_unscharf(string text, string erwartet) =>
        Assert.Equal(Enum.Parse<Feld>(erwartet), DetailLeser.BezeichnungErkennen(text));

    [Theory]
    [InlineData("Beschreibung")]
    [InlineData("xy")]
    [InlineData("Ausstattung")]
    public void Fremde_Bezeichnungen_werden_ignoriert(string text) =>
        Assert.Null(DetailLeser.BezeichnungErkennen(text));

    [Theory]
    [InlineData("03/2017", 3, 2017)]
    [InlineData("1O/2006", 10, 2006)]
    [InlineData("3/2017", 3, 2017)]
    [InlineData("2017", null, 2017)]
    [InlineData("13/2017", null, 2017)]
    public void Erstzulassung(string text, int? monat, int jahr) =>
        Assert.Equal((monat, (int?)jahr), DetailLeser.Erstzulassung(text));

    [Theory]
    [InlineData("84.975 km", 84975)]
    [InlineData("244.000 km", 244000)]
    [InlineData("5 km", 5)]
    [InlineData("84975km", 84975)]
    [InlineData("l2.500 km", 12500)]
    public void Kilometer(string text, int km) => Assert.Equal(km, DetailLeser.Kilometer(text));

    [Theory]
    [InlineData("320 kW (435 PS)", 320, 435)]
    [InlineData("125 kW (170 PS)", 125, 170)]
    [InlineData("125kW (170PS)", 125, 170)]
    [InlineData("435 PS", 320, 435)]
    [InlineData("320 kVV (435 P5)", 320, 435)]
    [InlineData("320 (435)", 320, 435)]
    public void Leistung(string text, int kw, int ps) => Assert.Equal(((int?)kw, (int?)ps), DetailLeser.Leistung(text));

    [Theory]
    [InlineData("03.10.2026 13:20:00 - Inserat von Mobie.de", "mobile.de")]
    [InlineData("03.10.2026 13:20:00 - Inserat von Mobile.de", "mobile.de")]
    [InlineData("03.10.2026 13:20:00 - Inserat von AutoScout24", "AutoScout24")]
    [InlineData("03.10.2026 13:20:00 - Inserat von Kkinanzeigen", "Kleinanzeigen")]
    public void Quelle_auch_mit_Lesefehlern(string zeile, string quelle) =>
        Assert.Equal(quelle, DetailLeser.Kopf(new[] { Z(zeile, 13, 27, 400, 14) }, 846).Quelle);

    // Hash-ID = AutoScout-Kennung fuer den Inserat-Link (Wunsch Ahmad 03.10.2026, live: Opel Mokka X)
    [Theory]
    [InlineData("ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d", "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d")]
    [InlineData("EE31AE2A-9D2F-4C62-B078-CC6AF83D3F1D", "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d")]
    [InlineData("ee31ae2a-9d2f-4c62-bO78-cc6af83d3fld", "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d")]   // O statt 0, l statt 1
    [InlineData("ee31ae2a-9d2f- 4c62-b078-cc6af83d3f1d", "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d")]
    [InlineData("9bcc72cb-be50-4cc4-804d-0d...", null)]                                         // abgeschnitten
    [InlineData("9bcc72cb-be50-4cc4-804d-0d…", null)]
    [InlineData("ee31ae2a-9d2f-4c62-b078-cc6af83d3f", null)]                                    // zu kurz
    [InlineData("", null)]
    public void Hash_ID_nur_vollstaendig(string text, string? erwartet) =>
        Assert.Equal(erwartet, DetailLeser.HashId(text));

    [Fact]
    public void Hash_ID_aus_der_Tabelle_und_nicht_aus_dem_zweiten_Durchlauf_ergaenzt()
    {
        var zeilen = PassatTechnik.Concat(new[] { Z("Hash-1D:", 11, 335), Z("ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d", 212, 335) }).ToArray();
        var f = DetailLeser.Auswerten(zeilen, PassatKopf, 846);
        Assert.Equal("ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d", f.HashId);
        var ohne = Passat();
        Assert.Null(DetailLeser.Ergaenzen(ohne, f).HashId);
    }

    [Fact]
    public void Leere_Erkennung_ergibt_fehlende_Pflichtfelder()
    {
        var f = DetailLeser.Auswerten(Array.Empty<OcrZeile>(), Array.Empty<OcrZeile>(), 0);
        Assert.Equal(new[] { "Marke/Modell", "Erstzulassung", "Kilometerstand" }, DetailLeser.Fehlend(f));
    }

    [Fact]
    public void Bezeichnung_und_Wert_in_einer_Zeile()
    {
        var zeilen = new[] { Z("Kilometerstand: 84.975 km", 12, 10, 300), Z("Erstzulassung: 03/2017", 12, 32, 300) };
        var tab = DetailLeser.Tabelle(zeilen);
        Assert.Equal("84.975 km", tab[Feld.Kilometer]);
        Assert.Equal("03/2017", tab[Feld.Erstzulassung]);
    }

    [Fact]
    public void Wert_ohne_Bezeichnung_wird_nicht_zugeordnet()
    {
        // x2-Durchlauf am 03.10.: "Preis:" nicht gelesen, nur "1.500 EUR"
        var zeilen = new[]
        {
            Z("Marke, Modell:", 12, 4), Z("VW Passat Variant", 212, 5),
            Z("1.500 EUR", 213, 27),
            Z("Zustand:", 12, 48), Z("Unfallwagen", 212, 48),
        };
        var tab = DetailLeser.Tabelle(zeilen);
        Assert.False(tab.ContainsKey(Feld.Preis));
        Assert.Equal("Unfallwagen", tab[Feld.Zustand]);
    }

    [Fact]
    public void Zweiter_Durchlauf_ergaenzt_fehlende_Felder()
    {
        var ohneKraftstoff = DetailLeser.Auswerten(PassatTechnik.Where(z => z.Text != "Diesel").ToArray(), PassatKopf, 846);
        Assert.Null(ohneKraftstoff.Kraftstoff);
        var f = DetailLeser.Ergaenzen(ohneKraftstoff, Passat());
        Assert.Equal("Diesel", f.Kraftstoff);
    }

    [Fact]
    public void Variante_aus_dem_Titel()
    {
        var f = Passat();
        Assert.Equal("B6 - Bastlerfahrzeug", f.Variante);
        f.Marke = "Volkswagen";                       // was der Server erkannt hat, aendert nichts daran
        f.Modell = "Passat Variant";
        Assert.Equal("B6 - Bastlerfahrzeug", f.Variante);
        Assert.Equal("V8 Diesel, 1. Hand, Mulliner, Nai", Bentley().Variante);
    }

    [Fact]   // seit 1.4.0: Kennung aus dem gelesenen Text, Lesefehler i/l/1 und o/0 zaehlen nicht als neues Auto
    public void Schluessel_aus_dem_gelesenen_Text()
    {
        var f = Bentley();
        Assert.Equal("bent1eybentayga | 03/2017 | 84975 km | 320 kW", f.Schluessel);
        var a = Bentley();
        a.MarkeModellText = "Hyundai i10";
        var b = Bentley();
        b.MarkeModellText = "HYUNDAI ilO";
        Assert.Equal(a.Schluessel, b.Schluessel);
        b.MarkeModellText = "Hyundai i20";
        Assert.NotEqual(a.Schluessel, b.Schluessel);
    }
}
