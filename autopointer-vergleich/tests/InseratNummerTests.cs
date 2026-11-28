using System.Text.Json;
using Xunit;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 13.10.2026 (1.5.16, Inserat-Nummer sicher lesen; Systemcheck 12.10.2026: 14-stellige mobile.de-
/// Nummer fehlt oft, eine falsch gelesene Ziffer fiel nicht auf). Die reinen Teile: Normalisieren, Stellenzahl je Portal
/// (B1), Mehrheit ueber mehrere Lesungen (B2), Hash-ID, Uebernahme ins Fahrzeug, Nutzlast an /vergleich, Zeilenabstand (B6).
/// Ohne Texterkennung, ohne Bilder.</summary>
public class InseratNummerTests
{
    public InseratNummerTests() => Protokoll.DateiAktiv = false;

    // ---- Normalisieren -------------------------------------------------------------------------------------------

    [Theory]
    [InlineData("50342132635392", "50342132635392")]
    [InlineData("5034 2132 635392", "50342132635392")]                  // Leerzeichen raus
    [InlineData("Inserat-ID: 5O342l32635392", "50342132635392")]       // nur die Zelle rechts der Beschriftung; O->0, l->1
    [InlineData("ID: 3529712I38", "3529712138")]
    [InlineData("35297|2138", "3529712138")]                           // | -> 1
    [InlineData("S0342132635392", "50342132635392")]                   // S -> 5
    [InlineData("Z0342132635392", "20342132635392")]                   // Z -> 2
    [InlineData("3529712B38", "3529712B38")]                           // B bleibt (8 oder 3?) — nie plausibel
    [InlineData("…", null)]
    [InlineData("   ", null)]
    [InlineData(null, null)]
    public void Normalisieren_wie_der_Server(string? roh, string? erwartet) =>
        Assert.Equal(erwartet, InseratNummer.Normalisieren(roh));

    // ---- Portal + Stellenzahl (B1) ---------------------------------------------------------------------------------

    [Theory]   // (Portal ist internal — wie bei Feld in ZweiterBlickTests als Name)
    [InlineData("mobile.de", "Mobile")]
    [InlineData("Mobile.de", "Mobile")]
    [InlineData("Mo...", "Mobile")]                                    // Systemcheck 12.10.: Spalte abgeschnitten
    [InlineData("Kleinanzeigen", "Kleinanzeigen")]
    [InlineData("Klei...", "Kleinanzeigen")]
    [InlineData("AutoScout24", "AutoScout")]
    [InlineData("Aut...", "AutoScout")]
    [InlineData("", "Unbekannt")]
    [InlineData(null, "Unbekannt")]
    [InlineData("Sonstwas", "Unbekannt")]
    public void Portal_aus_der_Quelle(string? quelle, string erwartet) =>
        Assert.Equal(Enum.Parse<Portal>(erwartet), InseratNummer.PortalVon(quelle));

    [Theory]
    [InlineData("503421326", "Mobile", true)]                          // 9 (aeltere)
    [InlineData("50342132635392", "Mobile", true)]                     // 14 (aktuelle)
    [InlineData("3529712138", "Mobile", false)]                        // 10: sicher falsch gelesen
    [InlineData("5034213263539", "Mobile", false)]                     // 13
    [InlineData("503421326353921", "Mobile", false)]                   // 15
    [InlineData("3529712138", "Kleinanzeigen", true)]                  // 10
    [InlineData("352971213", "Kleinanzeigen", true)]                   // 9
    [InlineData("35297121388", "Kleinanzeigen", true)]                 // 11
    [InlineData("35297121", "Kleinanzeigen", false)]                   // 8
    [InlineData("352971213888", "Kleinanzeigen", false)]               // 12
    [InlineData("3529712138", "Unbekannt", true)]                      // unbekannt: 9-14
    [InlineData("50342132635392", "Unbekannt", true)]
    [InlineData("35297121", "Unbekannt", false)]
    [InlineData("123456", "AutoScout", true)]                          // AutoScout: die Inserat-ID zaehlt nicht fuer den Link — wie der Server 6-20
    [InlineData("3529712B38", "Kleinanzeigen", false)]                 // Buchstabe
    [InlineData("", "Mobile", false)]
    public void Stellenzahl_je_Portal(string nummer, string portal, bool passt) =>
        Assert.Equal(passt, InseratNummer.StellenzahlPasst(nummer, Enum.Parse<Portal>(portal)));

    // ---- Mehrheit (B2) -------------------------------------------------------------------------------------------

    [Fact]
    public void Zwei_gleiche_Lesungen_sind_sicher()
    {
        var m = InseratNummer.Mehrheit(new[] { "50342132635392", "5O342132635392", "50342132635382" }, Portal.Mobile);
        Assert.Equal("50342132635392", m.Wert);
        Assert.True(m.Sicher);
        Assert.False(m.Unsicher);
        Assert.Equal(new[] { "50342132635382" }, m.Alternativen);       // die abweichende bleibt als Alternative
        Assert.Equal(3, m.Lesungen);
        // bei zwei Paaren gewinnt das haeufigere (3 zu 2: sicher)
        var drei = InseratNummer.Mehrheit(new[] { "503421326", "503421327", "503421326", "503421327", "503421326" }, Portal.Mobile);
        Assert.Equal("503421326", drei.Wert);
        Assert.True(drei.Sicher);
    }

    [Fact]
    public void Gleichstand_ist_keine_Mehrheit()
    {
        // 1.5.17 (Pruefung 23.10.2026, externer Bericht zu 1.5.16): A, B, A, B — vorher gewann die fruehere Lesung als
        // "sicher". Zwei plausible Lesungen mit gleich vielen Treffern widersprechen sich: unsicher, die fruehere bleibt
        // Hauptwert, die andere Alternative — der Server bekommt inserat_id_unsicher und prueft das Inserat gegen den Bildschirm.
        var m = InseratNummer.Mehrheit(new[] { "503421327", "503421326", "503421327", "503421326" }, Portal.Mobile);
        Assert.Equal("503421327", m.Wert);
        Assert.False(m.Sicher);
        Assert.True(m.Unsicher);
        Assert.Equal(new[] { "503421326" }, m.Alternativen);
        // Gleichstand zaehlt nur unter PLAUSIBLEN Lesungen: zweimal richtig gegen zweimal falsche Stellenzahl bleibt sicher
        var k = InseratNummer.Mehrheit(new[] { "5034213263539", "50342132635392", "5034213263539", "50342132635392" }, Portal.Mobile);
        Assert.Equal("50342132635392", k.Wert);
        Assert.True(k.Sicher);
        // dasselbe fuer die Hash-ID (AutoScout): bei Gleichstand kein sicherer Wert -> Uebernehmen setzt keine Hash-ID
        const string a = "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d", b = "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1e";
        var h = InseratNummer.HashMehrheit(new[] { a, b, a, b });
        Assert.False(h.Sicher);
        Assert.True(h.Unsicher);
        Assert.Equal(a, h.Wert);
    }

    [Fact]
    public void Alle_verschieden_ist_unsicher_mit_Alternativen()
    {
        var m = InseratNummer.Mehrheit(new[] { "50342132635392", "50342132635382", "50342132635392 " + "1", "5034213263539B" }, Portal.Mobile);
        Assert.False(m.Sicher);
        Assert.True(m.Unsicher);
        Assert.Equal("50342132635392", m.Wert);                        // die erste plausible (14 Stellen) ist der Hauptwert
        Assert.Equal(new[] { "50342132635382", "503421326353921", "5034213263539B" }, m.Alternativen);
        // hoechstens drei Alternativen
        var viele = InseratNummer.Mehrheit(new[] { "503421326", "503421327", "503421328", "503421329", "503421320" }, Portal.Mobile);
        Assert.Equal(3, viele.Alternativen.Count);
        Assert.Equal("503421326", viele.Wert);
    }

    [Fact]
    public void Falsche_Stellenzahl_ist_nie_sicher()
    {
        // zweimal gleich gelesen, aber 10 Stellen bei mobile.de: unsicher, kein Hauptwert — die Lesung bleibt als Alternative
        var m = InseratNummer.Mehrheit(new[] { "3529712138", "3529712138" }, Portal.Mobile);
        Assert.False(m.Sicher);
        Assert.True(m.Unsicher);
        Assert.Null(m.Wert);
        Assert.Equal(new[] { "3529712138" }, m.Alternativen);
        // dieselben Lesungen bei Kleinanzeigen: sicher
        var k = InseratNummer.Mehrheit(new[] { "3529712138", "3529712138" }, Portal.Kleinanzeigen);
        Assert.True(k.Sicher);
        Assert.Equal("3529712138", k.Wert);
        Assert.Empty(k.Alternativen);
        // 13 und 15 Stellen (ein Zeichen verschluckt / doppelt) sind nie plausibel: Hauptwert leer
        var n = InseratNummer.Mehrheit(new[] { "5034213263539", "503421326353922" }, Portal.Mobile);
        Assert.Null(n.Wert);
        Assert.True(n.Unsicher);
        Assert.Equal(2, n.Alternativen.Count);
    }

    [Fact]
    public void Eine_Lesung_ist_unsicher_keine_Lesung_ist_fehlt()
    {
        var eine = InseratNummer.Mehrheit(new[] { "50342132635392" }, Portal.Mobile);
        Assert.Equal("50342132635392", eine.Wert);
        Assert.False(eine.Sicher);
        Assert.True(eine.Unsicher);
        var keine = InseratNummer.Mehrheit(new string?[] { null, "", "  " }, Portal.Mobile);
        Assert.Null(keine.Wert);
        Assert.False(keine.Sicher);
        Assert.False(keine.Unsicher);                                   // nichts gelesen = "fehlt", nicht "unsicher"
        Assert.Equal(0, keine.Lesungen);
    }

    [Fact]
    public void Hash_ID_per_Mehrheit()
    {
        const string uuid = "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d";
        var sicher = InseratNummer.HashMehrheit(new[] { uuid, "Hash-ID: EE31AE2A-9D2F-4C62-BO78-CC6AF83D3Fld", "9bcc72cb-be50-4cc4-804d-0d..." });
        Assert.True(sicher.Sicher);
        Assert.Equal(uuid, sicher.Wert);
        Assert.Equal(new[] { "9bcc72cb-be50-4cc4-804d-0d..." }, sicher.Alternativen);
        var eine = InseratNummer.HashMehrheit(new[] { uuid, null });
        Assert.False(eine.Sicher);
        Assert.True(eine.Unsicher);
        Assert.Equal(uuid, eine.Wert);
        var abgeschnitten = InseratNummer.HashMehrheit(new[] { "9bcc72cb-be50-4cc4-804d-0d...", "9bcc72cb-be50-4cc4-804d-0d..." });
        Assert.False(abgeschnitten.Sicher);                             // zweimal gleich, aber keine vollstaendige UUID
        Assert.Null(abgeschnitten.Wert);
    }

    // ---- ins Fahrzeug --------------------------------------------------------------------------------------------

    [Fact]   // mobile.de: die Inserat-ID entscheidet; der zweite Blick legt seine Lesungen zu denen der Durchgaenge
    public void Uebernehmen_mobile_Inserat_ID()
    {
        var f = new Fahrzeug { Quelle = "mobile.de" };
        f.InseratIdLesungen.Add("50342132635392");                     // erster Durchgang
        InseratNummer.Uebernehmen(f, new[] { "5O342132635392", null, "50342132635382" }, Array.Empty<string?>());
        Assert.Equal("50342132635392", f.InseratId);
        Assert.False(f.InseratIdUnsicher);
        Assert.Equal(new[] { "50342132635382" }, f.AlternativenInseratId);
        Assert.Null(f.HashId);

        var u = new Fahrzeug { Quelle = "Mo..." };
        u.InseratIdLesungen.Add("50342132635392");
        InseratNummer.Uebernehmen(u, new[] { "50342132635382" }, Array.Empty<string?>());
        Assert.Equal("50342132635392", u.InseratId);
        Assert.True(u.InseratIdUnsicher);
        Assert.Equal(new[] { "50342132635382" }, u.AlternativenInseratId);
    }

    [Fact]   // AutoScout: die Hash-ID entscheidet ueber "unsicher" (die Inserat-ID zaehlt dort nicht fuer den Link); Hash nur sicher
    public void Uebernehmen_AutoScout_Hash_ID()
    {
        const string uuid = "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d";
        var f = new Fahrzeug { Quelle = "AutoScout24" };
        f.InseratIdLesungen.Add("440123456");
        f.HashIdLesungen.Add(uuid);
        InseratNummer.Uebernehmen(f, new[] { "440123457" }, new[] { "EE31AE2A-9D2F-4C62-B078-CC6AF83D3F1D", null, null });
        Assert.Equal(uuid, f.HashId);
        Assert.False(f.InseratIdUnsicher);                              // Hash zweimal gleich — die uneinige Inserat-ID stoert nicht
        Assert.Equal("440123456", f.InseratId);

        var g = new Fahrzeug { Quelle = "AutoScout24" };
        g.InseratIdLesungen.Add("440123456");
        g.HashIdLesungen.Add(uuid);
        InseratNummer.Uebernehmen(g, new[] { "440123456" }, new[] { "9bcc72cb-be50-4cc4-804d-0d...", null, null });
        Assert.Null(g.HashId);                                          // nur einmal vollstaendig: lieber keine als eine falsche
        Assert.True(g.InseratIdUnsicher);
        Assert.Equal("440123456", g.InseratId);                         // die Inserat-ID bleibt (Kennung fuer "gleiches Auto")

        var h = new Fahrzeug { Quelle = "AutoScout24" };                 // gar keine Hash-Zeile gelesen: "fehlt", nicht "unsicher"
        h.InseratIdLesungen.Add("440123456");
        InseratNummer.Uebernehmen(h, new[] { "440123456" }, new string?[] { null, null, null });
        Assert.Null(h.HashId);
        Assert.False(h.InseratIdUnsicher);
    }

    [Fact]   // die Durchgaenge sammeln ihre Lesungen, Ergaenzen fuehrt sie zusammen
    public void Auswerten_und_Ergaenzen_sammeln_die_Lesungen()
    {
        var a = Passat();
        Assert.Equal(new[] { "3529712138" }, a.InseratIdLesungen);
        Assert.Empty(a.HashIdLesungen);
        var zeilen = PassatTechnik.Select(z => z.Text == "3529712138" ? z with { Text = "35297I2138" } : z)
            .Concat(new[] { Z("Hash-ID:", 11, 335), Z("ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d", 212, 335) }).ToArray();
        var b = DetailLeser.Auswerten(zeilen, PassatKopf, 846);
        Assert.Equal(new[] { "35297I2138" }, b.InseratIdLesungen);
        Assert.Equal(new[] { "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d" }, b.HashIdLesungen);
        DetailLeser.Ergaenzen(a, b);
        Assert.Equal(new[] { "3529712138", "35297I2138" }, a.InseratIdLesungen);
        Assert.Equal(new[] { "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d" }, a.HashIdLesungen);
        // ohne zweiten Blick: die beiden Durchgaenge allein reichen fuer "sicher"
        InseratNummer.Uebernehmen(a, Array.Empty<string?>(), Array.Empty<string?>());
        Assert.Equal("3529712138", a.InseratId);
        Assert.False(a.InseratIdUnsicher);
    }

    // ---- Nutzlast an /vergleich ----------------------------------------------------------------------------------

    [Fact]
    public void Nutzlast_traegt_inserat_id_unsicher_nur_bei_true()
    {
        var f = Passat();
        Assert.False(AutoSchnellDienst.Nutzlast(f).ContainsKey("inserat_id_unsicher"));
        f.InseratIdUnsicher = true;
        f.AlternativenInseratId = new() { "3529712188" };
        var n = AutoSchnellDienst.Nutzlast(f);
        Assert.Equal(true, n["inserat_id_unsicher"]);
        string json = JsonSerializer.Serialize(n, AutoSchnellDienst.Json);
        using var doc = JsonDocument.Parse(json);
        Assert.True(doc.RootElement.GetProperty("inserat_id_unsicher").GetBoolean());
        Assert.Equal("3529712138", doc.RootElement.GetProperty("inserat_id").GetString());
        Assert.Equal(new[] { "3529712188" }, doc.RootElement.GetProperty("alternativen").GetProperty("inserat_id").EnumerateArray().Select(x => x.GetString()));
        // ohne plausible Lesung: inserat_id leer, Alternativen und das Feld gehen mit
        f.InseratId = null;
        var leer = JsonSerializer.Serialize(AutoSchnellDienst.Nutzlast(f), AutoSchnellDienst.Json);
        using var d2 = JsonDocument.Parse(leer);
        Assert.Equal("", d2.RootElement.GetProperty("inserat_id").GetString());
        Assert.True(d2.RootElement.GetProperty("inserat_id_unsicher").GetBoolean());
    }

    [Fact]
    public void Beschreibung_nennt_die_unsichere_Nummer()
    {
        var f = Passat();
        Assert.Contains(f.Beschreibung(), z => z.Contains("Inserat-ID: 3529712138") && !z.Contains("unsicher"));
        f.InseratIdUnsicher = true;
        Assert.Contains(f.Beschreibung(), z => z.Contains("Inserat-ID: 3529712138 (unsicher gelesen)"));
        f.InseratId = null;
        Assert.Contains(f.Beschreibung(), z => z.Contains("Inserat-ID: unsicher gelesen (keine plausible Lesung)"));
    }

    // ---- Zeilenabstand (B6) --------------------------------------------------------------------------------------

    [Fact]
    public void Zeilenabstand_der_Technik_Tabelle()
    {
        Assert.Equal(22, DetailLeser.Zeilenabstand(PassatTechnik));                 // echte Lesung: 22 px je Reihe
        // Tabelle auf 60 % verkleinert (kleine Schrift in AutoPointer): 13 px
        var klein = PassatTechnik.Select(z => z with { X = z.X * 0.6, Y = z.Y * 0.6, Breite = z.Breite * 0.6, Hoehe = z.Hoehe * 0.6 }).ToArray();
        double abstand = DetailLeser.Zeilenabstand(klein)!.Value;
        Assert.InRange(abstand, 12, 14);
        Assert.True(abstand < DetailLeser.SchriftKleinPx);
        // weniger als drei Reihen (nur "Marke, Modell" und "Preis"): kein verlaesslicher Wert
        Assert.Null(DetailLeser.Zeilenabstand(PassatTechnik.Take(2).Concat(PassatTechnik.Skip(15).Take(2)).ToArray()));
        Assert.Null(DetailLeser.Zeilenabstand(Array.Empty<OcrZeile>()));
    }
}
