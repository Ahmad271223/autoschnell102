using System.Drawing;
using System.Drawing.Imaging;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>1.5.22 (Wunsch Ahmad 27.11.2026): die ganze Beschreibung aus AutoPointer — die reinen Teile von
/// <see cref="BeschreibungGanz"/> (Streifen, Abdeckung, Stuecke, Text) und das neue Feld an den Server.</summary>
public class BeschreibungGanzTests
{
    [Fact]
    public void Streifen_Feld_ganz_sichtbar()
        => Assert.Equal((0, 318), BeschreibungGanz.Streifen(feldOben: 548, feldHoehe: 318, sichtOben: 176, sichtUnten: 886));

    [Fact]
    public void Streifen_Feld_ragt_unten_hinaus()
        => Assert.Equal((0, 241), BeschreibungGanz.Streifen(feldOben: 645, feldHoehe: 318, sichtOben: 176, sichtUnten: 886));

    [Fact]
    public void Streifen_Feld_ragt_oben_hinaus()
        => Assert.Equal((76, 242), BeschreibungGanz.Streifen(feldOben: 100, feldHoehe: 318, sichtOben: 176, sichtUnten: 886));

    /// <summary>Gemessen am 27.11.2026 im Reiter „Übersicht": das Feld lag bei 645, der Rollbereich endete bei 589 —
    /// von der Beschreibung war nichts zu sehen.</summary>
    [Fact]
    public void Streifen_Feld_ganz_unter_dem_Rand()
        => Assert.Null(BeschreibungGanz.Streifen(feldOben: 645, feldHoehe: 318, sichtOben: 176, sichtUnten: 589));

    /// <summary>Gemessen am 27.11.2026 (mobile.de, lange Tabelle): Feld 1171..1806, sichtbar 220..1107.</summary>
    [Fact]
    public void Rasten_erste_Raste_misst_den_Weg()
        => Assert.Equal(1, BeschreibungGanz.Rasten(feldOben: 1171, feldUnten: 1806, sichtOben: 220, sichtUnten: 1107, jeRaste: 0));

    [Fact]
    public void Rasten_nie_weiter_als_noetig()
        // noetig: 1728 - 1107 = 621 Bildpunkte = 8 Rasten zu 78; erlaubt waeren (1093 - 220) / 78 = 11
        => Assert.Equal(8, BeschreibungGanz.Rasten(feldOben: 1093, feldUnten: 1728, sichtOben: 220, sichtUnten: 1107, jeRaste: 78));

    [Fact]
    public void Rasten_Feld_unter_dem_Rand_rollt_hoechstens_bis_die_Oberkante_oben_ankommt()
        // Feld 2000 hoch ganz unter dem Rand: Oberkante darf von 1200 bis 220 = 980 Bildpunkte = 12 Rasten zu 78
        => Assert.Equal(12, BeschreibungGanz.Rasten(feldOben: 1200, feldUnten: 3200, sichtOben: 220, sichtUnten: 1107, jeRaste: 78));

    [Fact]
    public void Rasten_sichtbares_Feld_rollt_weniger_als_eine_Sichthoehe()
    {
        // Sichthoehe 887, 12 Bildpunkte Ueberlappung: 875 / 78 = 11 Rasten = 858 Bildpunkte < 887 — keine Zeile ungesehen
        int n = BeschreibungGanz.Rasten(feldOben: 300, feldUnten: 4000, sichtOben: 220, sichtUnten: 1107, jeRaste: 78);
        Assert.Equal(11, n);
        Assert.True(n * 78 < 1107 - 220);
    }

    [Fact]
    public void Rasten_mindestens_eine()
        => Assert.Equal(1, BeschreibungGanz.Rasten(feldOben: 1100, feldUnten: 1120, sichtOben: 220, sichtUnten: 1107, jeRaste: 2000));

    [Fact]
    public void Abdeckung_wird_erst_mit_allen_Streifen_vollstaendig()
    {
        var a = new BeschreibungGanz.Abdeckung(318);
        Assert.Equal((0, 0), a.Bereich());
        a.Merke(0, 120);
        Assert.False(a.Vollstaendig);
        Assert.Equal(198, a.Fehlend);
        a.Merke(97, 150);                    // ueberlappt den ersten Streifen
        Assert.Equal((0, 247), a.Bereich());
        a.Merke(240, 500);                   // ragt ueber das Feld hinaus: wird begrenzt
        Assert.True(a.Vollstaendig);
        Assert.Equal((0, 318), a.Bereich());
    }

    [Fact]
    public void Stuecke_schneiden_in_einer_leeren_Zeile()
    {
        // 3000 Zeilen, Schrift ueberall — nur die Zeilen 1380..1389 sind leer (zwischen zwei Textzeilen)
        var leer = new bool[3000];
        for (int y = 1380; y < 1390; y++) leer[y] = true;
        var s = BeschreibungGanz.Stuecke(leer, 1400);
        Assert.Equal((0, 1390), s[0]);                       // Schnitt hinter der letzten leeren Zeile, nicht bei 1400
        Assert.Equal((1390, 1400), s[1]);                    // keine leere Zeile in Reichweite: hart an der Grenze
        Assert.Equal((2790, 210), s[2]);
        Assert.Equal(3000, s.Sum(x => x.Hoehe));
    }

    [Fact]
    public void Stuecke_kurzes_Bild_bleibt_ganz()
        => Assert.Equal(new[] { (0, 318) }, BeschreibungGanz.Stuecke(new bool[318], 1400));

    [Fact]
    public void Zusammen_zieht_Zeilen_zusammen_und_kuerzt()
    {
        Assert.Null(BeschreibungGanz.Zusammen(new[] { " ", "" }));
        Assert.Equal("Steuerkette macht Geräusche\nBastlerfahrzeug",
                     BeschreibungGanz.Zusammen(new[] { " Steuerkette macht Geräusche ", "", "Bastlerfahrzeug" }));
        Assert.Equal(BeschreibungGanz.MaxZeichen, BeschreibungGanz.Zusammen(new[] { new string('x', 9000) })!.Length);
    }

    [Fact]
    public void LeereZeilen_erkennt_Schrift()
    {
        using var bild = new Bitmap(40, 6, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild)) g.Clear(Color.White);
        bild.SetPixel(17, 2, Color.Black);
        bild.SetPixel(3, 4, Color.FromArgb(240, 240, 240));          // heller Hintergrund zaehlt nicht als Schrift
        Assert.Equal(new[] { true, true, false, true, true, true }, BeschreibungGanz.LeereZeilen(bild));
    }

    [Fact]
    public void Nutzlast_schickt_die_ganze_Beschreibung_in_eigenem_Feld()
    {
        var f = new Fahrzeug { MarkeModellText = "BMW X5", BeschreibungText = "Anfang", BeschreibungVoll = new string('a', 7000) };
        var n = AutoSchnellDienst.Nutzlast(f);
        Assert.Equal("Anfang", n["beschreibung"]);
        Assert.Equal(BeschreibungGanz.MaxZeichen, ((string)n["beschreibung_voll"]!).Length);
    }

    [Fact]
    public void Nutzlast_ohne_ganze_Beschreibung_bleibt_wie_bisher()
    {
        var n = AutoSchnellDienst.Nutzlast(new Fahrzeug { MarkeModellText = "BMW X5", BeschreibungText = "Anfang" });
        Assert.False(n.ContainsKey("beschreibung_voll"));
    }

    [Fact]
    public void Einstellung_ist_im_Standard_an()
        => Assert.True(new Einstellungen().BeschreibungGanzLesen);
}
