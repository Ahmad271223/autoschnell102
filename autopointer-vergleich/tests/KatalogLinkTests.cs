using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

public class KatalogTests
{
    [Theory]
    [InlineData("VW Passat Variant", "VW", "Passat Variant")]
    [InlineData("Bentley Bentayga", "Bentley", "Bentayga")]
    [InlineData("Land Rover Range Rover Evoque", "Land Rover", "Range Rover Evoque")]
    [InlineData("Mercedes-Benz C 200", "Mercedes-Benz", "C 200")]
    [InlineData("Alfa Romeo Giulia", "Alfa Romeo", "Giulia")]
    [InlineData("Rolls-Royce Ghost", "Rolls-Royce", "Ghost")]
    [InlineData("BMW 320", "BMW", "320")]
    [InlineData("Bentlev Bentayga", "Bentley", "Bentayga")]   // Lesefehler in der Marke
    public void Marke_und_Modell_trennen(string text, string marke, string modell)
    {
        var t = Kat.TeileMarkeModell(text);
        Assert.NotNull(t);
        Assert.Equal((marke, modell), t!.Value);
    }

    [Fact]
    public void Unbekannte_Marke() => Assert.Null(Kat.TeileMarkeModell("Quatschmarke X1"));

    [Theory]
    [InlineData("Bentley", "Bentayga", "3100", "16")]
    [InlineData("VW", "Passat Variant", "25200", "63")]
    [InlineData("Mercedes-Benz", "C 200", "17200", "18")]
    public void Mobile_ids_wie_im_Backend(string marke, string modell, string markeId, string modellId)
    {
        var m = Kat.MobileMarkeFinden(marke)!;
        Assert.Equal(markeId, m.Id);
        var mod = Kat.MobileModellFinden(m, modell, null)!;
        Assert.Equal(modellId, mod.Id);
        Assert.False(mod.Unscharf);
    }

    [Fact]
    public void Lesefehler_im_Modell_wird_unscharf_aufgeloest()
    {
        var m = Kat.MobileMarkeFinden("Bentley")!;
        var mod = Kat.MobileModellFinden(m, "Bentavga", null)!;
        Assert.Equal("16", mod.Id);
        Assert.True(mod.Unscharf);
    }

    [Fact]
    public void Generisches_Modell_kommt_aus_dem_Titel()
    {
        var m = Kat.MobileMarkeFinden("VW")!;
        var mod = Kat.MobileModellFinden(m, "Andere", "VW Golf 1.4 TSI Highline")!;
        Assert.Equal("Golf", mod.Name);
    }

    [Fact]
    public void AutoScout_kein_falsches_Modell_RP435()
    {
        // M340i darf nicht zum M3 werden (Backend-Regel RP-435)
        var bmw = Kat.AutoScoutMarkeFinden("BMW")!;
        var mod = Kat.AutoScoutModellFinden(bmw, "M340i", null);
        Assert.True(mod == null || mod.Name != "M3");
    }

    [Fact]
    public void Wortgrenzen_wie_im_Backend()
    {
        Assert.Equal(new HashSet<int> { 1, 4, 5 }, Katalog.Wortgrenzen("E 220 d"));
        Assert.Equal(new HashSet<int> { 1, 4, 5 }, Katalog.Wortgrenzen("M340i"));
        Assert.Equal(new HashSet<int> { 3, 11, 16 }, Katalog.Wortgrenzen("CLA Shooting Brake"));
    }
}

public class ZuordnerTests
{
    [Fact]
    public void Schluessel_wie_gewuenscht()
    {
        var f = Bentley();
        Zuordner.Zuordnen(f, Kat);
        Assert.Equal("Bentley Bentayga | 03/2017 | 84975 km | 320 kW", f.Schluessel);
    }

    [Fact]
    public void An_den_Server_geht_was_AutoPointer_zeigt()
    {
        var f = Passat();
        var z = Zuordner.Zuordnen(f, Kat);
        Assert.True(z.MarkeErkannt);
        Assert.Equal(("VW", "Passat Variant"), (f.MarkeText, f.ModellText));
        Assert.Equal(("Volkswagen", "Passat Variant"), (f.Marke, f.Modell));
    }

    [Fact]
    public void Lesefehler_im_Modell_wird_fuer_den_Server_korrigiert()
    {
        var f = Bentley();
        f.MarkeModellText = "Bentley Bentavga";
        Zuordner.Zuordnen(f, Kat);
        Assert.Equal("Bentayga", f.ModellText);
    }

    [Theory]   // Befund 03.10.2026 (Kleinanzeigen, AutoPointer zeigt "VW weitere VW")
    [InlineData("VW weitere VW", "VW Beetle Cabrio 1.2 TSI", "Beetle")]
    [InlineData("VW Andere", "VW Beetle Cabrio 1.2 TSI", "Beetle")]
    [InlineData("VW weitere VW", "Schoenes Cabrio", "weitere VW")]   // nichts im Titel: so lassen, Server sagt warum
    // Befund 03.10.2026: Kleinanzeigen-Kategorie statt Modell, Modellwoerter verstreut in der Ueberschrift
    [InlineData("VW VW-Busse", "T5 Bulli multivan", "T5 Multivan")]
    [InlineData("VW VW-Busse", "Suche einen 7 sitzer", "VW-Busse")]   // nichts Brauchbares: Server sagt warum
    public void Platzhalter_Modell_kommt_aus_dem_Titel(string markeModell, string titel, string erwartet)
    {
        var f = Passat();
        f.MarkeModellText = markeModell;
        f.Titel = titel;
        Zuordner.Zuordnen(f, Kat);
        Assert.Equal(("VW", erwartet), (f.MarkeText, f.ModellText));
    }

    [Theory]   // Befund 03.10.2026: AutoPointer zeigt nur "Andere" — Marke UND Modell aus der Ueberschrift
    [InlineData("Andere", "Ford Mondeo Turnier 2.0 TDCi Diesel, ...", "Ford", "Mondeo")]
    [InlineData("Andere", "Ford Mondeo Turnier 2.0 TDCi Diesel, …", "Ford", "Mondeo")]
    [InlineData("Sonstige", "BMW 320d Touring", "BMW", "320")]
    public void Ohne_Marke_kommt_alles_aus_dem_Titel(string feld, string titel, string marke, string modell)
    {
        var f = Passat();
        f.MarkeModellText = feld;
        f.Titel = titel;
        var z = Zuordner.Zuordnen(f, Kat);
        Assert.True(z.MarkeErkannt);
        Assert.Equal(marke, f.MarkeText);
        Assert.StartsWith(modell, f.ModellText);
    }

    [Fact]
    public void Titelabgleich_nimmt_nie_Sammelnamen_und_bevorzugt_mehr_Woerter()
    {
        Assert.Equal("T5 Multivan", Katalog.AusTitel(new[] { "T5 (Alle)", "T5 andere", "Multivan", "T5 Multivan" }, "VW T5 Bulli Multivan"));
        Assert.Null(Katalog.AusTitel(new[] { "T5 (Alle)", "T5 andere" }, "T5 andere Ausstattung alle"));
        Assert.Equal("Beetle", Katalog.AusTitel(new[] { "Beetle", "New Beetle" }, "VW Beetle Cabrio 1.2 TSI"));
        Assert.Equal("New Beetle", Katalog.AusTitel(new[] { "Beetle", "New Beetle" }, "VW New Beetle 1.6"));
    }

    [Fact]
    public void Unbekannte_Marke_wird_erkannt()
    {
        var f = Bentley();
        f.MarkeModellText = "Quatschmarke X1";
        f.Titel = "Quatschmarke X1 Sport";
        Assert.False(Zuordner.Zuordnen(f, Kat).MarkeErkannt);
        // steht die Marke in der Ueberschrift, kommt sie von dort (Befund 03.10.2026: Feld "Andere")
        f = Bentley();
        f.MarkeModellText = "Quatschmarke X1";
        Assert.True(Zuordner.Zuordnen(f, Kat).MarkeErkannt);
        Assert.Equal("Bentley", f.MarkeText);
    }
}
