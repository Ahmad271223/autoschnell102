using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

public class DetailLeserTests
{
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
        Assert.Equal("AUTOMATIC_GEAR", f.GetriebeCode);
        Assert.Equal("DIESEL", f.KraftstoffCode);
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
        Zuordner.Zuordnen(f, Kat);
        Assert.Equal("B6 - Bastlerfahrzeug", f.Variante);
        var b = Bentley();
        Zuordner.Zuordnen(b, Kat);
        Assert.Equal("V8 Diesel, 1. Hand, Mulliner, Nai", b.Variante);
    }
}
