using System.Drawing;
using System.Drawing.Imaging;
using System.Windows.Forms;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Dauerlauf (Wunsch Ahmad 27.11.2026): Listenzeilen lesen, vorsortieren, Gegenpruefungen. Die Listenbilder
/// werden hier nachgezeichnet, wie AutoPointer sie am 27.11.2026 zeigte (Tahoma, Zeile am Stueck gezeichnet, graue
/// Trennlinien, Spaltenstriche im Kopf, rote/graue Plakette).</summary>
public class DauerlaufTests
{
    private static readonly TextFormatFlags Flags = TextFormatFlags.NoPadding | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine;

    private static ListenZeile Zeile(string art = "PKW, Gebraucht, Limousine, 4/5 Türen", string daten = "EZ: 03/2009, 167.000 Km, Benzin, 55 kW (75 PS), Schaltgetriebe, ...",
                                     int? preis = 3400, bool? privat = true, string titel = "Mercedes c180", string portal = "Kleinanzeigen",
                                     string zeit = "27.11.2026 16:48:25", bool titelGenau = true)
        => new(0, 147, titel, art, daten, "", preis, privat, portal, zeit, titelGenau);

    [Fact]
    public void Zeile_gibt_Zustand_Jahr_Km_PS_und_Kraftstoff_her()
    {
        var z = Zeile();
        Assert.Equal("Gebraucht", z.Zustand);
        Assert.Equal(2009, z.Jahr);
        Assert.Equal(167000, z.Km);
        Assert.Equal(75, z.Ps);
        Assert.Equal("Benzin", z.Kraftstoff);
        Assert.True(z.Lesbar);
        // Neuwagen ohne Erstzulassung; abgeschnittenes Feld zaehlt nicht als Kraftstoff
        var neu = Zeile(art: "PKW, Neu, SUV/Geländewagen/Pickup", daten: "9 Km, Hybrid (Benzin/Elektro), Euro 6d, 110 kW (150 PS), Automa...");
        Assert.Equal("Neu", neu.Zustand);
        Assert.Null(neu.Jahr);
        Assert.Equal(9, neu.Km);
        Assert.Equal("Hybrid (Benzin/Elektro)", neu.Kraftstoff);
        Assert.Equal("", Zeile(daten: "EZ: 01/2002, 180.000 Km, Ben...").Kraftstoff);
        Assert.False(Zeile(zeit: "").Lesbar);
    }

    [Fact]
    public void Vorauswahl_sortiert_nur_aus_was_sicher_nicht_passt()
    {
        var r = DauerlaufRegeln.Aus("privat;unfall;preis=500-30000;km=250000;jahr=2005");
        Assert.Null(Vorauswahl.Grund(Zeile(), r));
        Assert.Equal("Händler", Vorauswahl.Grund(Zeile(privat: false), r));
        Assert.Null(Vorauswahl.Grund(Zeile(privat: null), r));                       // Plakette nicht erkannt: kommt weiter
        Assert.Equal("Unfallwagen", Vorauswahl.Grund(Zeile(art: "PKW, Unfallwagen, Van/Kleinbus, 4/5 Türen"), r));
        Assert.StartsWith("Titel", Vorauswahl.Grund(Zeile(titel: "Dacia Lodgy an Bastler, neue Winterreifen"), r));
        Assert.StartsWith("Titel", Vorauswahl.Grund(Zeile(titel: "Opel Combo CNG NICHT FAHRBEREIT!"), r));
        Assert.Null(Vorauswahl.Grund(Zeile(titel: "Golf 6 unfallfrei, Scheckheft"), r));
        Assert.Contains("über", Vorauswahl.Grund(Zeile(preis: 34500), r));
        Assert.Contains("unter", Vorauswahl.Grund(Zeile(preis: 1), r));
        Assert.Null(Vorauswahl.Grund(Zeile(preis: null), r));                        // Preis nicht gelesen: kommt weiter
        Assert.Contains("km", Vorauswahl.Grund(Zeile(daten: "EZ: 10/2016, 253.000 Km, Diesel"), r));
        Assert.Contains("Erstzulassung", Vorauswahl.Grund(Zeile(daten: "EZ: 06/1999, 161.322 Km, Benzin"), r));
        Assert.Null(Vorauswahl.Grund(Zeile(privat: false, art: "PKW, Unfallwagen"), DauerlaufRegeln.Aus("haendler;mitunfall")));
        Assert.Equal("Portal Mobile.de", Vorauswahl.Grund(Zeile(portal: "Mobile.de"), DauerlaufRegeln.Aus("portal=Kleinanzeigen,AutoScout24")));
    }

    [Fact]
    public void Kopf_muss_Uhrzeit_Portal_Preis_und_Titel_der_Zeile_zeigen()
    {
        var z = Zeile(titel: "Mini Clubman Estate mit H-Kennzeichen", preis: 10450, zeit: "27.11.2026 17:20:24");
        var passt = new Dauerlauf.KopfDaten("27.11.2026 17:20:24 - Inserat von Kleinanzeigen", "Mini Clubman Estate mit H-Kennzeichen", 10450);
        Assert.Null(Dauerlauf.KopfWiderspruch(z, passt));
        // gemessen 27.11.2026: zwei Inserate derselben Sekunde, der Klick traf den Nachbarn
        Assert.NotNull(Dauerlauf.KopfWiderspruch(z, passt with { Titel = "Mercedes CLC Sport Coupé", Preis = 5500 }));
        Assert.NotNull(Dauerlauf.KopfWiderspruch(z, passt with { Titel = "Mercedes CLC Sport Coupé", Preis = null }));
        Assert.NotNull(Dauerlauf.KopfWiderspruch(z, passt with { Zeile = "27.11.2026 17:20:25 - Inserat von Kleinanzeigen" }));
        Assert.NotNull(Dauerlauf.KopfWiderspruch(z, passt with { Zeile = "27.11.2026 17:20:24 - Inserat von Mobile.de" }));
        Assert.NotNull(Dauerlauf.KopfWiderspruch(z, passt with { Zeile = "" }));                 // nicht lesbar = nicht bestaetigt
        // abgeschnittener Listentitel: der Kopf zeigt ihn ganz
        var lang = Zeile(titel: "Audi A3 Sportback 2.0 TDI DPF 6 Gang Bi-Xenon Anhäng...");
        Assert.Null(Dauerlauf.KopfWiderspruch(lang, new("27.11.2026 16:48:25 - Inserat von Kleinanzeigen", "Audi A3 Sportback 2.0 TDI DPF 6 Gang Bi-Xenon Anhängerkupplung", null)));
        // beide abgeschnitten, der Kopf frueher (gemessen 27.11.2026, mobile.de): kein Widerspruch — ein anderer Titel schon
        var fiesta = Zeile(titel: "Ford Fiesta 1,25 60kW Ambiente Ambiente", portal: "Mobile.de");
        Assert.Null(Dauerlauf.KopfWiderspruch(fiesta, new("27.11.2026 16:48:25 - Inserat von Mobile.de", "Ford Fiesta 1,25 60kW Ambient...", null)));
        Assert.NotNull(Dauerlauf.KopfWiderspruch(fiesta, new("27.11.2026 16:48:25 - Inserat von Mobile.de", "Ford Focus 1,6 Titanium Turn...", null)));
        var f2 = new Fahrzeug { Titel = "Ford Fiesta 1,25 60kW Ambient" };
        Dauerlauf.Genau(f2, new("x", "Ford Fiesta 1,25 60kW Ambient...", 3800), fiesta);
        Assert.Equal("Ford Fiesta 1,25 60kW Ambiente Ambiente", f2.Titel);            // der laengere exakte Titel gilt
        // im Kopf nur ZWEI Punkte sichtbar (gemessen 27.11.2026 abends): weiter dasselbe Auto
        var kuga = Zeile(titel: "Ford Kuga ST-LINE Kuga 2.5 Duratec PHEV", portal: "AutoScout24");
        Assert.Null(Dauerlauf.KopfWiderspruch(kuga, new("27.11.2026 16:48:25 - Inserat von AutoScout24", "Ford Kuga ST-LINE Kuga 2.5 Duratec ..", null)));
        Assert.Equal("Opel Corsa-B (SWING) 44 kW/6", Dauerlauf.OhnePunkte("Opel Corsa-B (SWING) 44 kW/6.."));
        Assert.Equal("Seat Leon St.", Dauerlauf.OhnePunkte("Seat Leon St."));
        Assert.True(Dauerlauf.TabelleReicht(new Fahrzeug { MarkeModellText = "Andere", Kilometer = 1, Merkmale = Enumerable.Range(0, 4).Select(i => new KeyValuePair<string, string>("a" + i, "b")).ToList() }));
        Assert.False(Dauerlauf.TabelleReicht(new Fahrzeug { MarkeModellText = "Andere", Kilometer = 1 }));
        // Titel mit Luecken (Emoji) wird nicht verglichen
        Assert.Null(Dauerlauf.KopfWiderspruch(Zeile(titel: "Suzuki Alto", titelGenau: false), new("27.11.2026 16:48:25 - Inserat von Kleinanzeigen", "☺Suzuki Alto", null)));
    }

    [Fact]
    public void Tabelle_darf_der_Zeile_nicht_widersprechen()
    {
        var z = Zeile();
        Assert.Null(Dauerlauf.Widerspruch(z, new Fahrzeug { Preis = 3400, Kilometer = 167000, EzJahr = 2009 }));
        Assert.Null(Dauerlauf.Widerspruch(z, new Fahrzeug()));                       // nichts gelesen: kein Widerspruch (der Kopf entscheidet)
        Assert.NotNull(Dauerlauf.Widerspruch(z, new Fahrzeug { Preis = 3400, Kilometer = 102000, EzJahr = 2009 }));   // Tabelle noch vom vorigen Auto
        Assert.NotNull(Dauerlauf.Widerspruch(z, new Fahrzeug { Preis = 5500 }));
        Assert.NotNull(Dauerlauf.Widerspruch(z, new Fahrzeug { EzJahr = 1996 }));
    }

    [Fact]
    public void Exakter_Kopf_geht_vor_der_Texterkennung_und_Treffer_braucht_Pflichtfelder()
    {
        var f = new Fahrzeug { MarkeModellText = "Andere", Titel = "Audi Q5 3xSIine", Preis = 26990, EzJahr = 2017, Kilometer = 115000 };
        Dauerlauf.Genau(f, new Dauerlauf.KopfDaten("27.11.2026 17:58:17 - Inserat von Kleinanzeigen", "Audi Q5 3xSline", 26999));
        Assert.Equal("Audi Q5 3xSline", f.Titel);
        Assert.Equal(26999, f.Preis);
        Dauerlauf.Genau(f, new Dauerlauf.KopfDaten("x", "", null));              // nichts gelesen: nichts ueberschrieben
        Assert.Equal("Audi Q5 3xSline", f.Titel);
        Assert.Equal(26999, f.Preis);
        Assert.True(AutoSchnellDienst.TrefferMeldbar(f));
        Assert.False(AutoSchnellDienst.TrefferMeldbar(new Fahrzeug { MarkeModellText = "VW Golf", Kilometer = 1 }));
        Assert.False(AutoSchnellDienst.TrefferMeldbar(new Fahrzeug { EzJahr = 2017, Kilometer = 1 }));
    }

    [Fact]
    public void Technische_Daten_und_Ausstattung_werden_exakt_gelesen()
    {
        // gestreifte Tabelle wie in AutoPointer: Zeilen abwechselnd F9F9F9 / FFFFFF, Linien E5E5E5, Wert fett, Rollbalken rechts
        var zeilen = new[] { ("Marke, Modell:", "Ford Fiesta"), ("Preis:", "3.800 EUR"), ("Erstzulassung:", "04/2009"), ("Kilometerstand:", "86.400 km"),
                             ("Leistung:", "60 kW (82 PS)"), ("Getriebeart:", "Schaltgetriebe"), ("Kraftstoff:", "Benzin"), ("Türen:", "4/5"),
                             ("Inserat-ID:", "458518054055136") };
        using var bild = new Bitmap(612, zeilen.Length * 22, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild))
        {
            using var linie = new Pen(Color.FromArgb(0xE5, 0xE5, 0xE5));
            using var normal = new Font(SchriftLeser.SchriftName, 14, FontStyle.Regular, GraphicsUnit.Pixel);
            using var fett = new Font(SchriftLeser.SchriftName, 14, FontStyle.Bold, GraphicsUnit.Pixel);
            for (int i = 0; i < zeilen.Length; i++)
            {
                var grund = i % 2 == 0 ? Color.FromArgb(0xF9, 0xF9, 0xF9) : Color.White;
                using var pinsel = new SolidBrush(grund);
                g.FillRectangle(pinsel, 0, i * 22, 612, 21);
                TextRenderer.DrawText(g, zeilen[i].Item1, normal, new Point(12, i * 22 + 2), Color.Black, grund, Flags);
                TextRenderer.DrawText(g, zeilen[i].Item2, fett, new Point(212, i * 22 + 2), Color.Black, grund, Flags);
                g.DrawLine(linie, 0, i * 22 + 21, 611, i * 22 + 21);
                g.DrawLine(linie, 199, i * 22, 199, i * 22 + 20);
            }
            g.FillRectangle(Brushes.Gainsboro, 592, 0, 20, bild.Height);
        }
        var m = TabellenLeser.Technik(bild);
        Assert.Equal(zeilen.Select(z => z.Item1.TrimEnd(':')).ToArray(), m.Select(x => x.Key).ToArray());
        Assert.Equal(zeilen.Select(z => z.Item2).ToArray(), m.Select(x => x.Value).ToArray());

        var f = new Fahrzeug { MarkeModellText = "Ford Fiasta", Preis = 3300, Kilometer = 86, InseratId = "45851B054055136", InseratIdUnsicher = true };
        TabellenLeser.Uebernehmen(f, m);                                  // exakt gelesen geht vor der Texterkennung
        Assert.Equal("Ford Fiesta", f.MarkeModellText);
        Assert.Equal(3800, f.Preis);
        Assert.Equal(86400, f.Kilometer);
        Assert.Equal((4, 2009), (f.EzMonat, f.EzJahr));
        Assert.Equal((60, 82), (f.Kw, f.Ps));
        Assert.Equal("Benzin", f.Kraftstoff);
        Assert.Equal("Schaltgetriebe", f.Getriebe);
        Assert.Equal("458518054055136", f.InseratId);
        Assert.False(f.InseratIdUnsicher);
        TabellenLeser.Uebernehmen(f, new List<KeyValuePair<string, string>>());   // nichts gelesen: nichts ueberschrieben
        Assert.Equal("Benzin", f.Kraftstoff);

        // Ausstattung: drei Spalten auf Weiss
        using var liste = new Bitmap(612, 70, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(liste))
        {
            g.Clear(Color.White);
            Text(g, "ABS", 4, 6, 14, false);
            Text(g, "Freisprecheinrichtung", 204, 6, 14, false);
            Text(g, "Radio", 408, 6, 14, false);
            Text(g, "Anhängerkupplung", 4, 27, 14, false);
            Text(g, "El. Fensterheber", 204, 27, 14, false);
        }
        Assert.Equal(new[] { "ABS", "Freisprecheinrichtung", "Radio", "Anhängerkupplung", "El. Fensterheber" }, TabellenLeser.Ausstattung(liste).ToArray());
    }

    [Fact]
    public void Dauerlauf_in_der_Leiste_Regeln_Stand_und_Halt_bei_Mausbewegung()
    {
        // Regeln als Text hin und zurueck (so stehen sie in den Einstellungen)
        var r = DauerlaufForm.AusWerten(privat: true, unfall: true, preisVon: 30000, preisBis: 500, km: 250000, jahr: 2008, portale: new[] { true, false, true });
        Assert.Equal((500, 30000), (r.PreisVon, r.PreisBis));                    // vertauschte Grenzen werden gedreht
        Assert.Equal(new[] { "Kleinanzeigen", "AutoScout24" }, r.Portale.ToArray());
        Assert.Equal("privat;unfall;preis=500-30000;km=250000;jahr=2008;portal=Kleinanzeigen,AutoScout24", r.AlsText());
        var zurueck = DauerlaufRegeln.Aus(r.AlsText());
        Assert.Equal(r.AlsText(), zurueck.AlsText());
        Assert.Empty(DauerlaufForm.AusWerten(false, false, 0, 0, 0, 0, new[] { true, true, true }).Portale);     // alle = keine Einschraenkung
        Assert.Empty(DauerlaufForm.AusWerten(false, false, 0, 0, 0, 0, new[] { false, false, false }).Portale);
        Assert.Equal("haendler;mitunfall", DauerlaufRegeln.Aus("haendler;mitunfall").AlsText());
        Assert.Equal("privat;unfall", new Einstellungen().DauerlaufRegeln);

        // Halt, sobald jemand die Maus bewegt: verglichen wird mit der Stelle, an der das Programm den Zeiger liess
        var gelassen = new Native.POINT { X = 900, Y = 500 };
        Assert.False(TrayApp.MausBewegt(null, gelassen));                         // erster Schritt: noch nichts zu vergleichen
        Assert.False(TrayApp.MausBewegt(gelassen, new Native.POINT { X = 905, Y = 492 }));
        Assert.True(TrayApp.MausBewegt(gelassen, new Native.POINT { X = 930, Y = 500 }));
        Assert.True(TrayApp.MausBewegt(gelassen, new Native.POINT { X = 900, Y = 489 }));

        Assert.Equal("12 neu · 7 aussortiert · 4 gelesen · 3 in der Trefferliste", TrayApp.DauerlaufStand(12, 7, 4, 3, ""));
        Assert.EndsWith("– wartet: AutoPointer liegt nicht vorne", TrayApp.DauerlaufStand(0, 0, 0, 0, "AutoPointer liegt nicht vorne"));

        // Leiste: laeuft der Dauerlauf, zeigt das Status-Feld das an
        var aus = new FensterZustand(Status.Aktiv, true, true, "Konto 10002", null, false, null, false);
        Assert.Equal("●  DAUERLAUF  ■", Leiste.StatusText(aus with { Dauerlauf = "3 gelesen" }, "AKTIV"));
        Assert.DoesNotContain("DAUERLAUF", Leiste.StatusText(aus, "AKTIV"));
    }

    // ---- Listenbild nachzeichnen ----------------------------------------------------------------

    private const int Besch = 193, Anb = 656, Quelle = 944, Breite = 1152, ZeilenHoehe = 148, ErsteOben = 24;

    private static void Text(Graphics g, string s, int x, int y, int px, bool fett)
    {
        using var schrift = new Font(SchriftLeser.SchriftName, px, fett ? FontStyle.Bold : FontStyle.Regular, GraphicsUnit.Pixel);
        TextRenderer.DrawText(g, s, schrift, new Point(x, y), Color.Black, Color.White, Flags);
    }

    private static Bitmap Liste(params (string Titel, bool Marke, string Art, string Daten, string Ausst, string Preis, bool? Privat, string Portal, string Zeit, bool Markiert)[] zeilen)
    {
        var bild = new Bitmap(Breite, ErsteOben + zeilen.Length * ZeilenHoehe + 60, PixelFormat.Format32bppRgb);
        using var g = Graphics.FromImage(bild);
        g.Clear(Color.White);
        using var strich = new Pen(Color.FromArgb(0xE5, 0xE5, 0xE5));
        using var linie = new Pen(Color.FromArgb(0xC8, 0xC8, 0xC8));
        foreach (int x in new[] { Besch, Anb, Quelle }) g.DrawLine(strich, x, 2, x, 20);
        for (int i = 0; i < zeilen.Length; i++)
        {
            var z = zeilen[i];
            int oben = ErsteOben + i * ZeilenHoehe;
            if (z.Markiert)
            {
                using var grau = new SolidBrush(Color.FromArgb(0xEA, 0xEA, 0xEA));
                g.FillRectangle(grau, 0, oben, Breite - 20, ZeilenHoehe - 1);
            }
            g.DrawLine(linie, 0, oben + ZeilenHoehe - 1, Breite - 21, oben + ZeilenHoehe - 1);
            int tx = Besch + 3;
            if (z.Marke)
            {
                using var rot = new SolidBrush(Color.FromArgb(0xD4, 0x0E, 0x16));
                g.FillRectangle(rot, tx, oben + 17, 38, 19);
                tx += 46;
            }
            if (!z.Markiert)
            {
                Text(g, z.Titel, tx, oben + 19, z.Marke ? 13 : 14, true);
                Text(g, z.Art, Besch + 3, oben + 45, 14, false);
                Text(g, z.Daten, Besch + 3, oben + 66, 14, false);
                Text(g, z.Ausst, Besch + 3, oben + 87, 14, false);
                Text(g, z.Preis, Besch + 3, oben + 114, 14, true);
                Text(g, z.Portal, Quelle + 3, oben + 26, 14, false);
                Text(g, z.Zeit, Quelle + 3, oben + 47, 14, false);
            }
            if (z.Privat is { } privat)
            {
                using var plakette = new SolidBrush(privat ? Color.FromArgb(0xD4, 0x0E, 0x16) : Color.FromArgb(0x49, 0x49, 0x49));
                g.FillRectangle(plakette, Anb + 3, oben + 113, 66, 19);
            }
        }
        return bild;
    }

    [Fact]
    public void Listenbild_wird_Zeile_fuer_Zeile_genau_gelesen()
    {
        using var bild = Liste(
            ("BMW 320D 3 Touring X Drive MSport Line Voll Service", true, "PKW, Gebraucht, Kombi/Lieferwagen, 4/5 Türen, 5 Sitze",
             "EZ: 12/2017, 147.672 Km, Diesel, Euro 6, 140 kW (190 PS), Auto...", "ABS, Airbags (Fahrer), Airbags (Beifahrer), Bordcomputer, Einparkhil...",
             "14.500 EUR", false, "Mobile.de", "27.11.2026 16:44:44", false),
            ("Dacia Lodgy an Bastler, neue Winterreifen", false, "PKW, Unfallwagen, Van/Kleinbus, 4/5 Türen",
             "EZ: 04/2014, 359.000 Km, Benzin, Euro 5, 55 kW (75 PS), Schaltg...", "ABS, Anhängerkupplung, Radio, Scheckheftgepflegt, Tempomat",
             "1.000 EUR", true, "Kleinanzeigen", "27.11.2026 16:48:36", false),
            ("Seat Exeo Weiß", true, "PKW, Unfallwagen, Kombi/Lieferwagen, 4/5 Türen", "EZ: 04/2011, 277.000 Km, Diesel, Euro 4, 125 kW (170 PS), Schal...",
             "Leichtmetallfelgen, Radio, Sitzheizung, Tempomat", "1.200 EUR", true, "Kleinanzeigen", "27.11.2026 16:48:23", true),
            ("VW Tiguan1.5 eTSI Life DSG 42%! mit LED, AHK uvm...", true, "PKW, Neu, SUV/Geländewagen/Pickup, 4/5 Türen, 5 Sitze",
             "9 Km, Hybrid (Benzin/Elektro), Euro 6d, 110 kW (150 PS), Automa...", "Keine Angaben zu den Ausstatungen vorhanden",
             "34.380 EUR *", null, "Mobile.de", "27.11.2026 16:44:57", false));
        var zeilen = ListenLeser.LiesAlle(bild);
        Assert.Equal(3, zeilen.Count);                                   // die markierte (graue) Zeile wird nicht gelesen
        var bmw = zeilen[0];
        Assert.Equal("BMW 320D 3 Touring X Drive MSport Line Voll Service", bmw.Titel);
        Assert.True(bmw.TitelGenau);
        Assert.Equal("PKW, Gebraucht, Kombi/Lieferwagen, 4/5 Türen, 5 Sitze", bmw.Art);
        Assert.Equal("EZ: 12/2017, 147.672 Km, Diesel, Euro 6, 140 kW (190 PS), Auto...", bmw.Daten);
        Assert.Equal(14500, bmw.Preis);
        Assert.False(bmw.Privat);
        Assert.Equal("Mobile.de", bmw.Portal);
        Assert.Equal("27.11.2026 16:44:44", bmw.Zeit);
        var dacia = zeilen[1];
        Assert.Equal("Dacia Lodgy an Bastler, neue Winterreifen", dacia.Titel);
        Assert.Equal("Unfallwagen", dacia.Zustand);
        Assert.Equal(359000, dacia.Km);
        Assert.True(dacia.Privat);
        Assert.Equal(1000, dacia.Preis);
        var vw = zeilen[2];
        Assert.Equal(34380, vw.Preis);
        Assert.Null(vw.Privat);
        Assert.Equal("Neu", vw.Zustand);
        Assert.Equal(9, vw.Km);
        Assert.NotEqual(bmw.Kennung, vw.Kennung);
        var n = AutoSchnellDienst.ListenNutzlast(bmw with { Anbieter = "D&M Fahrzeughandel GbR", Telefone = new[] { "+49 (0)151 56041400" }, EntfernungKm = 322, Ort = "D-61239 Ober Mörlen" });
        Assert.Equal("27.11.2026 16:44:44", n["zeit"]);
        Assert.Equal(false, n["privat"]);
        Assert.Equal("EZ: 12/2017, 147.672 Km, Diesel, Euro 6, 140 kW (190 PS), Auto...", n["daten"]);
        Assert.Equal(new List<string> { "+49 (0)151 56041400" }, n["telefone"]);
        Assert.Equal(322, n["entfernung_km"]);
        Assert.Equal("D-61239 Ober Mörlen", n["ort"]);
        Assert.Empty((List<string>)AutoSchnellDienst.ListenNutzlast(vw)["telefone"]!);
    }

    [Fact]
    public void Kein_Raster_wenn_es_keine_Liste_ist()
    {
        using var bild = new Bitmap(800, 400, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild)) g.Clear(Color.White);
        Assert.Empty(ListenLeser.LiesAlle(bild));
    }

    [Theory]
    [InlineData(14, false, "EZ: 05/2016, 200.000 Km, Diesel, 190 kW (258 PS), Automatik, Sc...")]
    [InlineData(14, false, "PKW, Gebraucht, Kombi/Lieferwagen, 4/5 Türen, 5 Sitze")]
    [InlineData(14, false, "ABS, Airbags (Fahrer), Alarmanlage, Anhängerkupplung")]
    [InlineData(14, false, "27.11.2026 17:20:24 - Inserat von Kleinanzeigen")]
    [InlineData(14, true, "14.500 EUR")]
    [InlineData(13, true, "Mercedes-Benz GLE 350 d 4Matic -Black Week-")]
    [InlineData(22, true, "Mercedes CLC Sport Coupé")]
    public void Am_Stueck_gezeichnete_Zeile_wird_genau_gelesen(int px, bool fett, string text)
    {
        using var bild = new Bitmap(700, px + 16, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild))
        {
            g.Clear(Color.White);
            Text(g, text, 4, 5, px, fett);
        }
        var l = SchriftLeser.LiesFeld(bild, amStueck: true, fett: fett);
        Assert.NotNull(l);
        Assert.Equal(0, l!.Luecken);
        Assert.Equal(text, l.Text);
        Assert.Equal(px, l.Px);
    }

    [Fact]
    public void Am_Rand_gekappter_letzter_Punkt_geht_trotzdem_auf()
    {
        // gemessen 27.11.2026: „…Automatik, Sc...“ — der dritte Punkt ist am Zellenrand abgeschnitten
        const string text = "EZ: 11/2023, 41.415 Km, Elektro, 140 kW (190 PS), Automatik, Sc...";
        using var schrift = new Font(SchriftLeser.SchriftName, 14, FontStyle.Regular, GraphicsUnit.Pixel);
        int b = TextRenderer.MeasureText(text, schrift, new Size(4000, 100), Flags).Width;
        using var bild = new Bitmap(4 + b - 2, 30, PixelFormat.Format32bppRgb);      // 2 Spalten des letzten Punkts fehlen
        using (var g = Graphics.FromImage(bild))
        {
            g.Clear(Color.White);
            Text(g, text, 4, 5, 14, false);
        }
        var l = SchriftLeser.LiesFeld(bild, amStueck: true);
        Assert.NotNull(l);
        Assert.Equal(0, l!.Luecken);
        Assert.Equal(text, l.Text);
    }
}
