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

public class LinkBauerTests
{
    public LinkBauerTests() => Protokoll.DateiAktiv = false;

    // Referenz: backend/mobile_service.build_search_url und
    // autoscout_service.build_search_url mit denselben Regeln (03.10.2026).
    private const string BentleyMobile =
        "https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&ref=quickSearch&s=Car&vc=Car&pageNumber=1"
        + "&ms=3100%3B16%3B%3B%3B&fr=2017%3A2017&ml=70000%3A100000&pw=316%3A324&ft=DIESEL&tr=AUTOMATIC_GEAR&dam=0&cn=DE&sb=p&od=up";
    private const string BentleyAutoScout =
        "https://www.autoscout24.de/lst/bentley?atype=C&cy=D&cat=ma11mo21181&fregfrom=2017&fregto=2017&kmfrom=70000&kmto=100000"
        + "&powerfrom=316&powerto=324&powertype=kw&fuel=D&gear=A&damaged_listing=exclude&ocs_listing=include&sort=price&desc=0&ustate=N,U";
    private const string PassatMobile =
        "https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&ref=quickSearch&s=Car&vc=Car&pageNumber=1"
        + "&ms=25200%3B63%3B%3B%3B&fr=2006%3A2006&ml=230000%3A260000&pw=121%3A129&ft=DIESEL&tr=AUTOMATIC_GEAR&dam=0&cn=DE&sb=p&od=up";

    /// <summary>Regeln, mit denen die Referenz-Links aus dem Backend erzeugt wurden
    /// (exaktes EZ-Jahr, km-Bereich, Leistung ±5 PS).</summary>
    private static Einstellungen AlteRegeln() => new()
    {
        KmModus = KmModus.Bereich, KmSpanne = 15000, EzModus = EzModus.ExaktesJahr, LeistungModus = LeistungModus.PlusMinus,
    };

    private static (List<Vergleich> Links, List<string> Hinweise) Baue(Fahrzeug f, Einstellungen? e = null)
    {
        var z = LinkBauer.Zuordnen(f, Kat);
        var h = new List<string>();
        return (LinkBauer.Bauen(f, z, e ?? new Einstellungen(), h), h);
    }

    [Fact]
    public void Bentley_beide_Links_identisch_mit_Backend()
    {
        var (links, hinweise) = Baue(Bentley(), AlteRegeln());
        Assert.Equal(2, links.Count);
        Assert.Equal(("mobile.de", BentleyMobile), (links[0].Portal, links[0].Url));
        Assert.Equal(("AutoScout24", BentleyAutoScout), (links[1].Portal, links[1].Url));
        Assert.Empty(hinweise);
    }

    [Fact]
    public void Passat_mobile_Link_identisch_mit_Backend()
    {
        var (links, _) = Baue(Passat(), AlteRegeln());
        Assert.Equal(PassatMobile, links[0].Url);
    }

    [Fact]
    public void Nur_ein_Portal()
    {
        var (links, _) = Baue(Bentley(), new Einstellungen { MobileDe = false });
        Assert.Single(links);
        Assert.Equal("AutoScout24", links[0].Portal);
    }

    [Theory]
    [InlineData(84975, 15000, 70000, 100000)]
    [InlineData(244000, 15000, 230000, 260000)]
    [InlineData(5000, 15000, 0, 20000)]
    [InlineData(84975, 10000, 75000, 95000)]
    public void Kilometerbereich_gerundet(int km, int spanne, int von, int bis) =>
        Assert.Equal(((int?)von, (int?)bis), LinkBauer.KmGrenzen(km, new Einstellungen { KmModus = KmModus.Bereich, KmSpanne = spanne }));

    [Fact]
    public void Kilometerbereich_enthaelt_immer_das_Fahrzeug()
    {
        for (int km = 0; km < 400_000; km += 1237)
            foreach (int spanne in new[] { 5000, 7500, 15000, 30000 })
            {
                var (von, bis) = LinkBauer.KmGrenzen(km, new Einstellungen { KmModus = KmModus.Bereich, KmSpanne = spanne });
                Assert.True(von <= km && bis >= km, $"{km} ± {spanne}: {von}–{bis}");
            }
    }

    [Fact]
    public void Kilometer_ohne_Runden_und_bis_Modus()
    {
        Assert.Equal(((int?)69975, (int?)99975), LinkBauer.KmGrenzen(84975, new Einstellungen { KmModus = KmModus.Bereich, KmSpanne = 15000, KmRunden = false }));
        Assert.Equal(((int?)null, (int?)99975), LinkBauer.KmGrenzen(84975, new Einstellungen { KmModus = KmModus.BisPlus, KmSpanne = 15000 }));
    }

    [Fact]
    public void Erstzulassung_Modi()
    {
        Assert.Equal(((int?)2017, (int?)2017), LinkBauer.EzGrenzen(2017, new Einstellungen { EzModus = EzModus.ExaktesJahr }));
        Assert.Equal(((int?)2016, (int?)2018), LinkBauer.EzGrenzen(2017, new Einstellungen { EzModus = EzModus.PlusMinus }));
        Assert.Equal(((int?)2016, (int?)null), LinkBauer.EzGrenzen(2017, new Einstellungen { EzModus = EzModus.AbJahr }));
        var (links, _) = Baue(Bentley(), new Einstellungen { EzModus = EzModus.PlusMinus });
        Assert.Contains("fr=2016%3A2018", links[0].Url);
        Assert.Contains("fregfrom=2016&fregto=2018", links[1].Url);
    }

    [Fact]
    public void Unbekanntes_Modell_oeffnet_keine_Suche_nur_nach_Marke()
    {
        var f = Bentley();
        f.MarkeModellText = "Bentley Gibtsnichtmodell";
        var (links, hinweise) = Baue(f);
        Assert.Empty(links);
        Assert.Contains(hinweise, h => h.Contains("kein mobile.de-Vergleich"));
        Assert.Contains(hinweise, h => h.Contains("kein AutoScout24-Vergleich"));

        var (nurMarke, _) = Baue(f, new Einstellungen { OhneModellNurMarke = true });
        Assert.Equal(2, nurMarke.Count);
        Assert.Contains("ms=3100%3B%3B%3B%3B", nurMarke[0].Url);
        Assert.Contains("cat=ma11&", nurMarke[1].Url);
    }

    [Fact]
    public void Fehlende_Merkmale_trotzdem_Suche_mit_Hinweis()
    {
        var f = Bentley();
        f.Kraftstoff = null;
        f.Kw = null;
        f.Ps = null;
        var (links, hinweise) = Baue(f);
        Assert.Equal(2, links.Count);
        Assert.DoesNotContain("ft=", links[0].Url);
        Assert.DoesNotContain("pw=", links[0].Url);
        Assert.Contains(hinweise, h => h.StartsWith("Kraftstoff nicht gelesen"));
        Assert.Contains(hinweise, h => h.StartsWith("Leistung nicht gelesen"));
    }

    [Fact]
    public void Filter_abschaltbar()
    {
        var e = new Einstellungen
        {
            LeistungModus = LeistungModus.Aus, KraftstoffFiltern = false, GetriebeFiltern = false,
            UnfallwagenAusblenden = false, NurDeutschland = false, KmModus = KmModus.Aus, EzModus = EzModus.Aus,
        };
        var (links, _) = Baue(Bentley(), e);
        Assert.Equal("https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&ref=quickSearch&s=Car&vc=Car&pageNumber=1&ms=3100%3B16%3B%3B%3B&sb=p&od=up", links[0].Url);
        Assert.Equal("https://www.autoscout24.de/lst/bentley?atype=C&cat=ma11mo21181&ocs_listing=include&sort=price&desc=0&ustate=N,U", links[1].Url);
    }

    // Ahmads Vorgabe 03.10.2026 (VW Polo aus Kleinanzeigen, 10/2005, 128.000 km, 55 kW/75 PS):
    // genau dieser mobile.de-Link; der AutoScout-Link ist der des Backends mit denselben Regeln
    // (older_exact 1, plus 20000, min_ps 5) — AutoScout schreibt ihn beim Oeffnen um in
    // /lst/volkswagen/polo/ft_benzin/tr_schaltgetriebe?fregfrom=2004&kmto=148000&powerfrom=51...
    private const string PoloMobileSoll =
        "https://suchen.mobile.de/fahrzeuge/search.html?isSearchRequest=true&ref=quickSearch&s=Car&vc=Car&pageNumber=1"
        + "&ms=25200%3B27%3B%3B%3B&fr=2004%3A&ml=%3A148000&pw=51%3A&ft=PETROL&tr=MANUAL_GEAR&dam=0&cn=DE&sb=p&od=up";
    private const string PoloAutoScoutSoll =
        "https://www.autoscout24.de/lst/volkswagen?atype=C&cy=D&cat=ma74mo2090&fregfrom=2004&kmto=148000&powerfrom=51"
        + "&powertype=kw&fuel=B&gear=M&damaged_listing=exclude&ocs_listing=include&sort=price&desc=0&ustate=N,U";

    private static Fahrzeug Polo() => new()
    {
        MarkeModellText = "VW Polo", EzMonat = 10, EzJahr = 2005, Kilometer = 128000, Kw = 55, Ps = 75,
        Kraftstoff = "Benzin", Getriebe = "Schaltgetriebe", Zustand = "Gebraucht",
    };

    [Fact]
    public void Standardregeln_ergeben_genau_Ahmads_Links()
    {
        var (links, hinweise) = Baue(Polo());
        Assert.Equal(PoloMobileSoll, links[0].Url);
        Assert.Equal(PoloAutoScoutSoll, links[1].Url);
        Assert.Empty(hinweise);
    }

    [Fact]
    public void Leistung_Modi()
    {
        var f = Polo();
        Assert.Equal((51, (int?)null), LinkBauer.KwGrenzen(f, new Einstellungen())!.Value);
        Assert.Equal((51, (int?)59), LinkBauer.KwGrenzen(f, new Einstellungen { LeistungModus = LeistungModus.PlusMinus })!.Value);
        Assert.Equal((55, (int?)null), LinkBauer.KwGrenzen(f, new Einstellungen { LeistungTolerantPs = 0 })!.Value);
        Assert.Equal((55, (int?)55), LinkBauer.KwGrenzen(f, new Einstellungen { LeistungModus = LeistungModus.PlusMinus, LeistungTolerantPs = 0 })!.Value);
        Assert.Null(LinkBauer.KwGrenzen(f, new Einstellungen { LeistungModus = LeistungModus.Aus }));
    }

    [Fact]
    public void Alte_gespeicherte_Regeln_werden_auf_den_Standard_gesetzt()
    {
        var e = AlteRegeln();
        e.MobileDe = false;
        e.Browser = BrowserWahl.Edge;
        e.RegelnAufStandard();
        Assert.Equal((KmModus.BisPlus, 20000, EzModus.AbJahr, 1, LeistungModus.AbMinus, 5),
                     (e.KmModus, e.KmSpanne, e.EzModus, e.EzJahre, e.LeistungModus, e.LeistungTolerantPs));
        Assert.False(e.MobileDe);                      // Portale/Browser bleiben
        Assert.Equal(BrowserWahl.Edge, e.Browser);
    }

    [Fact]
    public void Schluessel_wie_gewuenscht()
    {
        var f = Bentley();
        LinkBauer.Zuordnen(f, Kat);
        Assert.Equal("Bentley Bentayga | 03/2017 | 84975 km | 320 kW", f.Schluessel);
    }
}
