using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 04.11.2026 (1.5.18): zwoelf Lesebilder nicht erkannter Anzeigen ausgewertet (Messung
/// <see cref="LesebilderMessung"/>). Daraus: Hash-ID mit S statt 5 und Punkten/Kreisen statt Bindestrich, Erstzulassung mit
/// dem Schraegstrich als "1", "Irserat von", das Portal-Logo im Titel, Marke/Modell aus dem Titel, wenn die Zeile nicht zu
/// sehen ist, Tabelle gar nicht sichtbar, Hash-ID abgeschnitten.</summary>
[Collection("Protokolldateien")]
public class Lesebilder20261104Tests
{
    public Lesebilder20261104Tests() => Protokoll.DateiAktiv = false;

    private static OcrZeile Z(string text, double x, double y, double b = 300, double h = 14) => new(text, x, y, b, h);

    [Theory]   // Lesungen aus dem Lesebild Kia Stonic (AutoScout24), echte Kennung f3ccc645-6ca4-49f7-baa9-aa5ad8c55e3d
    [InlineData("f3ccc645-6ca4-49f7-baa9-aa5ad8cSSe3d")]
    [InlineData("f3ccc64S.6ca4-49f7-baa9-aaSad8cSSe3d")]
    [InlineData("f3ccc645-6ca4-49f7- baa9•aa5ad8c55e3d")]
    [InlineData("f3ccc645•6ca4• 49f7-baa9-aa5ad8c5Se3d")]
    [InlineData("f3ccc645-6ca4-49f7-baa9.aa5ad8cSSe3d")]
    [InlineData("f3ccc6456ca449f7baa9aa5ad8c55e3d")]          // alle Trennzeichen verschluckt: 32 Hex-Zeichen
    public void Hash_ID_mit_S_statt_5_und_falschen_Trennzeichen(string lesung) =>
        Assert.Equal("f3ccc645-6ca4-49f7-baa9-aa5ad8c55e3d", DetailLeser.HashId(lesung));

    [Theory]
    [InlineData("f3ccc645-6ca4-49f7-baa9-aa5ad8cGGe3d")]      // G: 6 oder 9? nicht eindeutig
    [InlineData("f3ccc64-56ca4-49f7-baa9-aa5ad8c55e3d")]      // fuenf Gruppen, aber falsch geschnitten
    [InlineData("f20948e6-bdcd-446b-b750-fdcc2f8")]           // abgeschnitten (Mercedes GLE)
    [InlineData("9e26ebe6-44e7-4e12-97a5-a...")]              // abgeschnitten mit „…“ (VW Passat)
    public void Hash_ID_bleibt_leer_wenn_nicht_eindeutig_oder_abgeschnitten(string lesung) =>
        Assert.Null(DetailLeser.HashId(lesung));

    [Fact]   // Mehrheit: aus den fuenf Lesungen des Kia Stonic wird eine SICHERE Hash-ID (vorher: keine -> kein AutoScout-Link)
    public void Kia_Stonic_Hash_ID_ist_jetzt_sicher()
    {
        var f = new Fahrzeug { Quelle = "AutoScout24" };
        f.HashIdLesungen.Add("f3ccc645-6ca4-49f7-baa9-aa5ad8cSSe3d");
        f.HashIdLesungen.Add("f3ccc64S.6ca4-49f7-baa9-aaSad8cSSe3d");
        InseratNummer.Uebernehmen(f, Array.Empty<string?>(), new[] { "f3ccc645-6ca4-49f7- baa9•aa5ad8c55e3d",
            "f3ccc645•6ca4• 49f7-baa9-aa5ad8c5Se3d", "f3ccc645-6ca4-49f7-baa9.aa5ad8cSSe3d" });
        Assert.Equal("f3ccc645-6ca4-49f7-baa9-aa5ad8c55e3d", f.HashId);
        Assert.False(f.InseratIdUnsicher);
        Assert.False(f.HashAbgeschnitten);
    }

    [Fact]   // Mercedes GLE / VW Passat: die Kennung ist abgeschnitten -> eigener Hinweis statt "nicht lesbar"
    public void Abgeschnittene_Hash_ID_wird_erkannt()
    {
        var gle = new Fahrzeug { Quelle = "AutoScout24" };
        gle.HashIdLesungen.Add("f20948e6-bdcd-446b-b750-fdcc2f8");
        InseratNummer.Uebernehmen(gle, Array.Empty<string?>(), new[] { "f20948e6-bdcd- 446b- b750-fdcc2f8•",
            "f20948e6-bdcd-446b-b750-fdcc2f8:" });
        Assert.Null(gle.HashId);
        Assert.True(gle.HashAbgeschnitten);
        var passat = new Fahrzeug { Quelle = "AutoScout24" };
        passat.HashIdLesungen.Add("9e26ebe6-44e7-4e12-97a5-a...");
        InseratNummer.Uebernehmen(passat, Array.Empty<string?>(), Array.Empty<string?>());
        Assert.True(passat.HashAbgeschnitten);
        // eine ganze, sichere Kennung ist nie "abgeschnitten"; ein Doppelpunkt am Ende kostet keine Lesung mehr
        var ganz = new Fahrzeug { Quelle = "AutoScout24" };
        ganz.HashIdLesungen.Add("f3ccc645-6ca4-49f7-baa9-aa5ad8c55e3d:");
        InseratNummer.Uebernehmen(ganz, Array.Empty<string?>(), new[] { "f3ccc645-6ca4-49f7-baa9-aa5ad8c55e3d" });
        Assert.Equal("f3ccc645-6ca4-49f7-baa9-aa5ad8c55e3d", ganz.HashId);
        Assert.False(ganz.HashAbgeschnitten);
        Assert.Equal("3529712138", InseratNummer.Normalisieren("Inserat-ID: 3529712138:"));
    }

    [Theory]   // Hyundai i30: der Schraegstrich als "1" gelesen
    [InlineData("0712019", 7, 2019)]
    [InlineData("1112019", 11, 2019)]
    [InlineData("07l2019", 7, 2019)]
    [InlineData("07/2019", 7, 2019)]
    [InlineData("2019", null, 2019)]
    public void Erstzulassung_mit_Schraegstrich_als_Eins(string text, int? monat, int jahr) =>
        Assert.Equal((monat, (int?)jahr), DetailLeser.Erstzulassung(text));

    [Fact]
    public void Erstzulassung_sieben_Ziffern_nur_wenn_eindeutig()
    {
        Assert.Equal((null, null), DetailLeser.Erstzulassung("1312019"));        // Monat 13 gibt es nicht
        Assert.Equal((null, null), DetailLeser.Erstzulassung("0712099"));        // Jahr in der Zukunft
    }

    [Fact]   // BMW 530d (mobile.de): "Irserat von Mobie.de" — vorher wurde die Datumszeile zum Titel
    public void Kopf_mit_Irserat_und_Logo_im_Titel()
    {
        var k = DetailLeser.Kopf(new[] { Z("23.10.2026 19:33:08 - Irserat von Mobie.de", 10, 10),
                                         Z("BMW 530d - F10", 40, 40), Z("6.100 EUR", 10, 70) }, 480);
        Assert.Equal(("mobile.de", "BMW 530d - F10", (int?)6100), k);
        // ohne erkannte Quelle-Zeile ist die Datumszeile trotzdem kein Titel
        var ohne = DetailLeser.Kopf(new[] { Z("23.10.2026 19:33:08 - Xyz", 10, 10), Z("BMW 530d - F10", 40, 40) }, 480);
        Assert.Equal("BMW 530d - F10", ohne.Titel);
        // Hyundai i30 / Mercedes GLE: das Logo rechts in derselben Zeile
        Assert.Equal("Hyundai i30 1...", DetailLeser.OhneLogo("Hyundai i30 1...amobile.de"));
        Assert.Equal("Mercedes-Benz GLE ...", DetailLeser.OhneLogo("Mercedes-Benz GLE ... Auto Scout24"));
        Assert.Equal("Ford Ka Trend", DetailLeser.OhneLogo("Ford Ka Trend kleinanzeigen"));
        Assert.Equal("VW Polo 1.0 TSI", DetailLeser.OhneLogo("VW Polo 1.0 TSI"));
        var kopf = DetailLeser.Kopf(new[] { Z("28.10.2026 - Inserat von Mo...", 10, 10),
                                            Z("Hyundai i30 1...amobile.de", 40, 40) }, 480);
        Assert.Equal(("mobile.de", "Hyundai i30 1"), (kopf.Quelle, kopf.Titel));
        Assert.Equal("mobile.de", DetailLeser.Kopf(new[] { Z("- Inserat von Mobk.de", 10, 10) }, 480).Quelle);
        Assert.Equal("Kleinanzeigen", DetailLeser.Kopf(new[] { Z("- Inserat von Klei...", 10, 10) }, 480).Quelle);
        Assert.Equal("AutoScout24", DetailLeser.Kopf(new[] { Z("- Inserat von Aut…", 10, 10) }, 480).Quelle);
    }

    [Fact]   // BMW 530d (Reiter „Übersicht“, Tabelle gescrollt): die Zeile „Marke, Modell“ fehlt, der Titel nicht
    public void Marke_Modell_aus_dem_Titel_wenn_die_Zeile_fehlt()
    {
        var f = new Fahrzeug { Titel = "BMW 530d - F10", EzJahr = 2010, Kilometer = 381000 };
        Assert.True(DetailLeser.MarkeModellAusTitel(f));
        Assert.Equal("BMW 530d - F10", f.MarkeModellText);
        Assert.True(f.MarkeModellAusTitel);
        Assert.Empty(DetailLeser.Fehlend(f));
        // die Zeile geht immer vor
        var mitZeile = new Fahrzeug { MarkeModellText = "BMW 530", Titel = "BMW 530d - F10" };
        Assert.False(DetailLeser.MarkeModellAusTitel(mitZeile));
        Assert.Equal("BMW 530", mitZeile.MarkeModellText);
        // kein brauchbarer Titel: bleibt leer
        foreach (var t in new[] { null, "", "12", "23.10.2026 19:33:08 - Irserat" })
        {
            var g = new Fahrzeug { Titel = t };
            Assert.False(DetailLeser.MarkeModellAusTitel(g));
            Assert.Equal("", g.MarkeModellText);
        }
        Assert.True(DetailLeser.MarkeModellAusTitel(new Fahrzeug { Titel = "Hyundai i30 1...amobile.de" }));
    }

    [Theory]   // verwaschene Bezeichnung, eindeutiger Wert (Lesebilder Hyundai i30 / BMW 530d / Ford Ka)
    [InlineData("ErstzUa$9.ng•.", "0712019", "Erstzulassung")]
    [InlineData("Erstzdasst-ng.•", "10/2017", "Erstzulassung")]
    [InlineData("Kbmeterstand", "201.000 km", "Kilometer")]
    [InlineData("\"Iometerstard•.", "90.000 km", "Kilometer")]
    public void Verwaschene_Bezeichnung_mit_eindeutigem_Wert(string bezeichnung, string wert, string feld) =>   // Feld ist internal
        Assert.Equal(Enum.Parse<Feld>(feld), DetailLeser.LockerErkannt(bezeichnung, wert));

    [Theory]   // nie: andere Zeilen mit aehnlichem Wert, oder eine Bezeichnung, die gar nicht danach aussieht
    [InlineData("HU", "bis 10/2028")]
    [InlineData("Hauptuntersuchung", "10/2028")]
    [InlineData("Reichweite", "450 km")]
    [InlineData("Verbrauch innerorts", "6,5 Liter/100 km")]
    [InlineData("CO2-Emissionen", "126 g/km")]
    [InlineData("Baujahr", "2018")]
    [InlineData("Kbmeterstand", "ca. 200.000")]
    public void Lockere_Erkennung_greift_nicht_bei_anderen_Zeilen(string bezeichnung, string wert) =>
        Assert.Null(DetailLeser.LockerErkannt(bezeichnung, wert));

    [Fact]   // eine lesbare Bezeichnung geht immer vor der lockeren Erkennung
    public void Lesbare_Bezeichnung_geht_vor()
    {
        var technik = new[] { Z("K1ometerstd•.:", 10, 10, 120), Z("999.000 km", 200, 10, 120),   // nur locker erkannt
                              Z("Kilometerstand:", 10, 32, 120), Z("61.000 km", 200, 32, 120),
                              Z("ErstzUa$9.ng:", 10, 54, 120), Z("0712019", 200, 54, 120) };
        var f = DetailLeser.Auswerten(technik, Array.Empty<OcrZeile>(), 0);
        Assert.Equal(61000, f.Kilometer);
        Assert.Equal((7, 2019), (f.EzMonat, f.EzJahr));
    }

    [Fact]   // Bezeichnung gar nicht gelesen, nur der Wert rechts: genau einer mit eindeutiger Form zaehlt
    public void Wert_ohne_Bezeichnung_nur_wenn_eindeutig()
    {
        var technik = new List<OcrZeile> { Z("Marke, Modell:", 10, 10, 120), Z("BMW 530d", 200, 10, 120),
                                           Z("Zustand:", 10, 32, 120), Z("Unfallwagen", 200, 32, 120),
                                           Z("12/2010", 200, 54, 120), Z("381.000 km", 200, 76, 120),
                                           Z("HU:", 10, 98, 120), Z("bis 03/2028", 200, 98, 120),
                                           Z("7,4 Liter/100 km", 200, 120, 120) };
        var f = DetailLeser.Auswerten(technik, Array.Empty<OcrZeile>(), 0);
        Assert.Equal((12, 2010), (f.EzMonat, f.EzJahr));
        Assert.Equal(381000, f.Kilometer);
        // zwei Werte mit km-Form ohne Bezeichnung (Reichweite + Kilometerstand): nichts geraten
        technik.Add(Z("450 km", 200, 142, 120));
        Assert.Null(DetailLeser.Auswerten(technik, Array.Empty<OcrZeile>(), 0).Kilometer);
        // eine lesbare Zeile geht immer vor
        technik.Add(Z("Kilometerstand:", 10, 164, 120));
        technik.Add(Z("61.000 km", 200, 164, 120));
        Assert.Equal(61000, DetailLeser.Auswerten(technik, Array.Empty<OcrZeile>(), 0).Kilometer);
    }

    [Fact]   // gezaehlte Tabellenzeilen: beide Durchlaeufe, das Maximum
    public void Tabellenzeilen_werden_gezaehlt()
    {
        var technik = new[] { Z("Marke, Modell:", 10, 10, 120), Z("VW Golf", 200, 10, 120),
                              Z("Erstzulassung:", 10, 32, 120), Z("05/2019", 200, 32, 120),
                              Z("Kilometerstand:", 10, 54, 120), Z("61.000 km", 200, 54, 120) };
        var f = DetailLeser.Auswerten(technik, Array.Empty<OcrZeile>(), 0);
        Assert.Equal(3, f.TabellenFelder);
        var leer = DetailLeser.Auswerten(Array.Empty<OcrZeile>(), Array.Empty<OcrZeile>(), 0);
        Assert.Equal(0, leer.TabellenFelder);
        Assert.Equal(3, DetailLeser.Ergaenzen(leer, f).TabellenFelder);
    }

    [Fact]   // Lesebild „Hash-ID abgeschnitten“: Grund inserat_id_fehlt mit dem Vermerk
    public void Lesebild_Nutzlast_mit_Hash_Vermerk()
    {
        var b = new Lesebild(Lesebilder.InseratIdFehlt, new byte[] { 1 }, "roh", null, new[] { Lesebilder.HashAbgeschnittenText });
        var n = Lesebilder.Nutzlast(b);
        Assert.Equal("inserat_id_fehlt", n["grund"]);
        Assert.Equal(new[] { "Hash-ID abgeschnitten" }, (IEnumerable<string>)n["fehlt"]!);
        Assert.False(Lesebilder.Nutzlast(new Lesebild(Lesebilder.InseratIdFehlt, new byte[] { 1 }, "roh")).ContainsKey("fehlt"));
    }
}
