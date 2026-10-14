using System.Collections.Concurrent;
using System.Drawing;
using System.Drawing.Imaging;
using Xunit;
using Xunit.Abstractions;
using static AutoPointerVergleich.Tests.Fixtures;

namespace AutoPointerVergleich.Tests;

/// <summary>Befund Ahmad 09.10.2026 (1.5.12): zweiter Blick auf die Werte von "Marke, Modell", "Kraftstoff" und
/// "Inserat-ID" ("Hyundai IBO", "VWT- Ro c", "EIektro", Inserat-ID mit Buchstaben). Die reinen Teile (Ausschnitt aus der
/// Lage der Zeilen, Saeubern/Doppelte/Grenzen, Zuordnung im Sammelbild) laufen immer; die Teile mit der echten
/// Windows-Texterkennung nur, wenn sie auf dem PC vorhanden ist (sonst still uebersprungen, wie in der CI).</summary>
[Collection("Protokolldateien")]   // aendert TextErkennung.Frist — nicht parallel zu TexterkennungTests
public class ZweiterBlickTests
{
    private readonly ITestOutputHelper _aus;

    public ZweiterBlickTests(ITestOutputHelper aus)
    {
        _aus = aus;
        Protokoll.DateiAktiv = false;
    }

    // ---- Ausschnitte aus der Lage der Zeilen (rein) ---------------------------------------------------------------

    [Fact]   // echte Lesung vom 03.10.2026 (Passat): Werte bei x≈212, Zeilen 22 px auseinander
    public void Ausschnitte_aus_der_Lage_der_Passat_Zeilen()
    {
        var b = DetailLeser.WertBereiche(PassatTechnik, 420, 340, ZweiterBlick.Felder);
        Assert.Equal(new[] { Feld.Kraftstoff, Feld.InseratId, Feld.MarkeModell }.OrderBy(f => f), b.Keys.OrderBy(f => f));
        // Marke/Modell: Wert y=5..18, Bezeichnung endet bei x=132, Reihe "Preis" beginnt bei y=27; Wertspalte bei 212
        Assert.Equal(new Rectangle(204, 0, 215, 23), b[Feld.MarkeModell]);
        // Kraftstoff: y=180..193, darueber endet "Getriebeart" bei 171, darunter beginnt "Farbe" bei 202
        Assert.Equal(new Rectangle(205, 176, 214, 21), b[Feld.Kraftstoff]);
        // Inserat-ID: letzte Reihe (y=313..326), darueber endet "Umweltplakette" bei 304
        Assert.Equal(new Rectangle(205, 309, 214, 21), b[Feld.InseratId]);
        foreach (var r in b.Values)
        {
            Assert.True(r.Left > 132, "nie in die Bezeichnung");
            Assert.Equal(419, r.Right);                       // bis zum Rand der Tabelle, ohne die Rahmenspalte
        }
        // die Bereiche dreier Nachbarreihen ueberlappen nie mit deren Text
        Assert.True(b[Feld.MarkeModell].Bottom <= 27);
        Assert.True(b[Feld.Kraftstoff].Top >= 171 && b[Feld.Kraftstoff].Bottom <= 202);
        Assert.True(b[Feld.InseratId].Top >= 304);
    }

    [Fact]   // gemessen 09.10.: der erste Durchgang verschluckt gern ein Wort ("Golf" statt "VW Golf", "BMW" statt "BMW X1")
    public void Ausschnitt_beginnt_an_der_Wertspalte_und_reicht_bis_zum_Rand()
    {
        var zeilen = PassatTechnik.Where(z => z.Text != "VW Passat Variant").Append(Z("Passat Variant", 236, 5, 96)).ToArray();
        var r = DetailLeser.WertBereiche(zeilen, 420, 340, ZweiterBlick.Felder)[Feld.MarkeModell];
        Assert.True(r.Left <= 212 - 4, r.ToString());         // "VW " (ab 212) bleibt im Ausschnitt
        Assert.Equal(419, r.Right);                          // und rechts davon auch, was nicht gelesen wurde
        Assert.Equal("Passat Variant", DetailLeser.Tabelle(zeilen)[Feld.MarkeModell]);   // der erste Durchgang bleibt

        // breite Tabelle: rechts hoechstens 8 Zeilenhoehen hinter das Gelesene (Wert endet bei 332, Reihe 14 px hoch);
        // eine Zeile ohne gelesenen Wert reicht bis zum Rand
        var ohneId = zeilen.Where(z => z.Text != "3529712138").ToArray();
        var breit = DetailLeser.WertBereiche(ohneId, 800, 340, ZweiterBlick.Felder);
        Assert.Equal(332 + (int)DetailLeser.RechtsZeilen * 14, breit[Feld.MarkeModell].Right);
        Assert.Equal(799, breit[Feld.InseratId].Right);
    }

    [Fact]
    public void Bezeichnung_und_Wert_als_ein_Stueck()
    {
        // Wertspalte aus den anderen Reihen bekannt -> dort
        var zeilen = PassatTechnik.Where(z => z.Text is not ("Kraftstoff:" or "Diesel"))
            .Append(Z("Kraftstoff: Diesel", 12, 180, 260)).ToArray();
        Assert.Equal("Diesel", DetailLeser.Tabelle(zeilen)[Feld.Kraftstoff]);
        var r = DetailLeser.WertBereiche(zeilen, 420, 340, ZweiterBlick.Felder)[Feld.Kraftstoff];
        Assert.Equal(205, r.Left);
        // keine Spalte bekannt (alles in einem Stueck gelesen) -> nach der Lage des Doppelpunkts geschaetzt
        var allein = new[] { Z("Kraftstoff: Diesel", 12, 10, 180) };
        var s = DetailLeser.WertBereiche(allein, 300, 40, ZweiterBlick.Felder)[Feld.Kraftstoff];
        Assert.InRange(s.Left, 105, 125);                    // Doppelpunkt nach 11 von 18 Zeichen: 12 + 180·11/18 = 122
    }

    [Fact]   // "Inserat-ID:" gelesen, daneben nichts — gerade dann soll der zweite Blick hinsehen
    public void Zeile_ohne_gelesenen_Wert_bekommt_trotzdem_einen_Ausschnitt()
    {
        var zeilen = PassatTechnik.Where(z => z.Text != "3529712138").ToArray();
        Assert.False(DetailLeser.Tabelle(zeilen).ContainsKey(Feld.InseratId));     // der erste Durchgang: keine ID
        Assert.Null(DetailLeser.Auswerten(zeilen, PassatKopf, 846).InseratId);
        var r = DetailLeser.WertBereiche(zeilen, 420, 340, ZweiterBlick.Felder)[Feld.InseratId];
        Assert.Equal(new Rectangle(205, 309, 214, 21), r);
    }

    [Fact]
    public void Gibt_es_zweimal_dieselbe_Zeile_gilt_wie_im_ersten_Durchgang_die_erste_mit_Wert()
    {
        var zeilen = new[]
        {
            Z("Marke, Modell:", 12, 4), Z("Kraftstoff:", 12, 26),                 // erste Kraftstoff-Zeile ohne Wert
            Z("VW Golf", 212, 4), Z("Kraftstoff:", 12, 48), Z("Benzin", 212, 48),
        };
        Assert.Equal("Benzin", DetailLeser.Tabelle(zeilen)[Feld.Kraftstoff]);
        var r = DetailLeser.WertBereiche(zeilen, 420, 80, ZweiterBlick.Felder)[Feld.Kraftstoff];
        Assert.True(r.Top > 39 && r.Bottom >= 61, r.ToString());
    }

    [Fact]
    public void Ausschnitt_bleibt_im_Bild()
    {
        var z = new TabellenZeile(Feld.InseratId, "", 416, null, 414, null, 2, 15, null, null);
        Assert.Null(DetailLeser.WertAusschnitt(z, 418, 20));     // zwischen Bezeichnung und Rand bleibt nichts uebrig
        var oben = new TabellenZeile(Feld.MarkeModell, "VW", 10, 10, 0, 30, -3, 9, null, null);
        var r = DetailLeser.WertAusschnitt(oben, 100, 12)!.Value;
        Assert.True(r.Top >= 0 && r.Bottom <= 12 && r.Left >= 0 && r.Right <= 100);
    }

    // ---- Saeubern, Doppelte, Grenzen (rein) -----------------------------------------------------------------------

    [Theory]
    [InlineData("MarkeModell", "  VW   T-Roc  ", "VW T-Roc")]
    [InlineData("MarkeModell", "Mercedes-Benz Andere ...", "Mercedes-Benz Andere")]    // wie Sauber: Punkte am Ende weg
    [InlineData("Kraftstoff", "ff: Diesel", "Diesel")]                                // Rest der Bezeichnung mitgelesen
    [InlineData("Kraftstoff", "   ", null)]
    [InlineData("Kraftstoff", null, null)]
    [InlineData("InseratId", "35 2971 2138.", "3529712138")]                           // wie der erste Durchgang
    [InlineData("InseratId", "ID: 3529-712 138", "3529-712138")]
    [InlineData("InseratId", "…", null)]
    public void Saeubern_wie_der_Hauptwert(string feld, string? roh, string? erwartet) =>
        Assert.Equal(erwartet, ZweiterBlick.Saeubern(Enum.Parse<Feld>(feld), roh));

    [Fact]
    public void Saeubern_kuerzt_auf_160_Zeichen() =>
        Assert.Equal(ZweiterBlick.MaxLaenge, ZweiterBlick.Saeubern(Feld.MarkeModell, new string('A', 400))!.Length);

    [Fact]
    public void Nur_Abweichendes_ohne_Doppelte_hoechstens_drei()
    {
        Assert.Equal(new[] { "Hyundai i30" },
            ZweiterBlick.Alternativen(Feld.MarkeModell, "Hyundai IBO", new[] { "Hyundai i30", "Hyundai IBO", " Hyundai  i30 ", null }));
        // Gross-/Kleinschreibung zaehlt: "EIektro" (grosses I) ist nicht "Elektro"
        Assert.Equal(new[] { "Elektro", "eIektro" },
            ZweiterBlick.Alternativen(Feld.Kraftstoff, "EIektro", new[] { "Elektro", "EIektro", "eIektro" }));
        Assert.Equal(new[] { "a", "b", "c" }, ZweiterBlick.Alternativen(Feld.Kraftstoff, null, new[] { "a", "b", "a", "c", "d" }));
        Assert.Empty(ZweiterBlick.Alternativen(Feld.InseratId, "3529712138", new[] { "3529712138", "3529 712138", "" }));
        // ohne Hauptwert ist jede Lesung eine Alternative
        Assert.Equal(new[] { "VW T-Roc" }, ZweiterBlick.Alternativen(Feld.MarkeModell, "", new[] { "VW T-Roc" }));
    }

    [Fact]   // fehlt die Inserat-ID aus dem ersten Durchgang, nimmt das Programm die erste Lesung des zweiten Blicks
    public void Inserat_ID_aus_dem_zweiten_Blick_nur_wenn_der_erste_keine_hatte()
    {
        var f = Passat();
        f.InseratId = null;
        f.HashId = "ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d";
        ZweiterBlick.Uebernehmen(f, new Dictionary<Feld, List<string?>>
        {
            [Feld.InseratId] = new() { "3529712138", "35297I2138" },
            [Feld.MarkeModell] = new() { "VW Passat Variant", "VW Passat Varlant" },
            [Feld.Kraftstoff] = new() { null, "Diesel" },
        });
        Assert.Equal("3529712138", f.InseratId);
        Assert.Equal(new[] { "35297I2138" }, f.AlternativenInseratId);              // die genommene ist keine Alternative
        Assert.Equal("ee31ae2a-9d2f-4c62-b078-cc6af83d3f1d", f.HashId);             // Hash-ID-Regel unveraendert
        Assert.Equal(new[] { "VW Passat Varlant" }, f.AlternativenMarkeModell);
        Assert.Empty(f.AlternativenKraftstoff);
        Assert.Equal("VW Passat Variant", f.MarkeModellText);                       // Hauptwerte bleiben

        var g = Passat();                                                           // ID gelesen: bleibt
        ZweiterBlick.Uebernehmen(g, new Dictionary<Feld, List<string?>> { [Feld.InseratId] = new() { "3529712I38" } });
        Assert.Equal("3529712138", g.InseratId);
        Assert.Equal(new[] { "3529712I38" }, g.AlternativenInseratId);
    }

    [Fact]
    public void Zeilen_des_Sammelbilds_werden_ihrem_Feld_zugeordnet()
    {
        var plaetze = new[]
        {
            new ZweiterBlick.Platz(Feld.MarkeModell, 0, 100), new ZweiterBlick.Platz(Feld.Kraftstoff, 100, 200),
            new ZweiterBlick.Platz(Feld.InseratId, 200, 300),
        };
        var je = ZweiterBlick.Zuordnen(new[]
        {
            Z("T-Roc", 120, 40, 80, 40), Z("VW", 40, 42, 60, 40),          // zwei Stuecke einer Zeile: nach x
            Z("Elektro", 40, 140, 150, 40), Z("  ", 40, 240, 10, 40),       // leeres Stueck zaehlt nicht
            Z("Rest", 40, 305, 50, 40),                                     // ausserhalb aller Streifen
        }, plaetze);
        Assert.Equal("VW T-Roc", je[Feld.MarkeModell]);
        Assert.Equal("Elektro", je[Feld.Kraftstoff]);
        Assert.False(je.ContainsKey(Feld.InseratId));
    }

    // ---- Sammelbild (GDI+, ohne Texterkennung) --------------------------------------------------------------------

    [Fact]
    public void Sammelbild_stapelt_die_Ausschnitte_und_dreht_helle_Schrift_auf_dunkler_Markierung_um()
    {
        using var technik = new Bitmap(300, 70, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(technik))
        {
            g.Clear(Color.White);
            g.FillRectangle(Brushes.Black, 100, 8, 40, 6);                          // "Schrift" schwarz auf weiss
            using var blau = new SolidBrush(Color.FromArgb(0x31, 0x6A, 0xC5));      // markierte Zeile: weiss auf blau
            g.FillRectangle(blau, 0, 40, 300, 25);
            g.FillRectangle(Brushes.White, 100, 48, 40, 6);
        }
        var bereiche = new List<(Feld, Rectangle)> { (Feld.MarkeModell, new Rectangle(90, 0, 200, 22)), (Feld.Kraftstoff, new Rectangle(90, 42, 200, 21)) };
        foreach (var art in ZweiterBlick.Reihenfolge)
        {
            double zoom = ZweiterBlick.Zoom(art, 96);
            var (bild, plaetze) = ZweiterBlick.Sammelbild(technik, bereiche, zoom, art);
            using (bild)
            {
                Assert.Equal(new[] { Feld.MarkeModell, Feld.Kraftstoff }, plaetze.Select(p => p.Feld));
                Assert.Equal(0, plaetze[0].Oben);
                Assert.Equal(plaetze[0].Unten, plaetze[1].Oben);                  // lueckenlos untereinander
                Assert.Equal(bild.Height, plaetze[1].Unten);
                Assert.True(bild.Width >= 200 * zoom && bild.Height >= 43 * zoom);
                // Mitte des zweiten "Schriftzugs" im Sammelbild
                int rand = (int)Math.Ceiling(8 * zoom);
                var mitte = bild.GetPixel(rand + (int)(30 * zoom), (int)plaetze[1].Oben + rand + (int)(9 * zoom));
                var ecke = bild.GetPixel(2, (int)plaetze[1].Oben + 2);
                if (art == ZweiterBlick.Aufbereitung.Kontrast)
                {
                    Assert.True(mitte.R < 60 && mitte.R == mitte.G && mitte.G == mitte.B, mitte.ToString());   // dunkel, grau
                    Assert.Equal(Color.White.ToArgb(), ecke.ToArgb());             // Hintergrund weiss
                }
                else
                {
                    Assert.True(mitte.R > 200 && mitte.G > 200, mitte.ToString());  // Farbe wie angezeigt
                    Assert.Equal(Color.FromArgb(0x31, 0x6A, 0xC5).ToArgb(), ecke.ToArgb());   // Rand in Zeilenfarbe
                }
            }
        }
        Assert.Equal(new[] { 5.0, 4.0 }, ZweiterBlick.Reihenfolge.Select(a => ZweiterBlick.Zoom(a, 96)));
        Assert.Equal(new[] { 2.5, 2.0 }, ZweiterBlick.Reihenfolge.Select(a => ZweiterBlick.Zoom(a, 192)));
    }

    // ---- Ablauf: ueberspringen, Fehler, Frist ---------------------------------------------------------------------

    private static readonly string[] DreiZeilen = { "Marke, Model:", "VW Passat Variant", "Kraftstoff:", "Diesel", "Inserat-ID:", "3529712138" };

    [Fact]   // keine der drei Zeilen gefunden -> kein zweiter Blick (die Texterkennung wird gar nicht erst gefragt)
    public async Task Ohne_die_drei_Zeilen_kein_zweiter_Blick()
    {
        var ohne = PassatTechnik.Where(z => !DreiZeilen.Contains(z.Text)).ToArray();
        Assert.Empty(DetailLeser.WertBereiche(ohne, 420, 340, ZweiterBlick.Felder));
        var zeilen = new ConcurrentQueue<string>();
        Protokoll.NeueZeile += zeilen.Enqueue;
        try
        {
            using var bild = new Bitmap(420, 340);
            var f = Passat();
            Assert.Null(await ZweiterBlick.AnwendenAsync(f, bild, ohne, null, 96, null!, null));
            Assert.DoesNotContain(zeilen, z => z.Contains("Zweiter Blick"));
            Assert.Empty(f.AlternativenMarkeModell);
            Assert.Equal("3529712138", f.InseratId);
        }
        finally { Protokoll.NeueZeile -= zeilen.Enqueue; }
    }

    [Fact]   // was auch immer schiefgeht: nur ins Protokoll, der Vergleich geht ohne Alternativen raus
    public async Task Fehler_im_zweiten_Blick_kostet_den_Vergleich_nicht()
    {
        var zeilen = new ConcurrentQueue<string>();
        Protokoll.NeueZeile += zeilen.Enqueue;
        try
        {
            using var bild = new Bitmap(420, 340);
            var f = Passat();
            // keine Texterkennung (null) -> Ausnahme mitten im zweiten Blick
            Assert.Null(await ZweiterBlick.AnwendenAsync(f, bild, PassatTechnik, null, 96, null!, null));
            Assert.Contains(zeilen, z => z.Contains("Zweiter Blick fehlgeschlagen"));
            Assert.Empty(f.AlternativenMarkeModell);
            Assert.Empty(f.AlternativenKraftstoff);
            Assert.Empty(f.AlternativenInseratId);
            Assert.Equal(("VW Passat Variant", "Diesel", "3529712138"), (f.MarkeModellText, f.Kraftstoff, f.InseratId));
            Assert.DoesNotContain("alternativen", AutoSchnellDienst.Nutzlast(f).Keys);
        }
        finally { Protokoll.NeueZeile -= zeilen.Enqueue; }
    }

    /// <summary>Gezeichnete Technik-Tabelle wie in AutoPointer (Bezeichnung links, Wert rechts, 22 px je Zeile) —
    /// wie in <see cref="TexterkennungTests"/>.</summary>
    private static Bitmap Tabelle(params (string Bezeichnung, string Wert)[] zeilen)
    {
        var bmp = new Bitmap(420, 22 * zeilen.Length + 10, PixelFormat.Format32bppRgb);
        using var g = Graphics.FromImage(bmp);
        g.Clear(Color.White);
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        using var schrift = new Font("Segoe UI", 9f);
        for (int i = 0; i < zeilen.Length; i++)
        {
            g.DrawString(zeilen[i].Bezeichnung, schrift, Brushes.Black, 12, 4 + 22 * i);
            g.DrawString(zeilen[i].Wert, schrift, Brushes.Black, 212, 4 + 22 * i);
        }
        return bmp;
    }

    private static Bitmap Golf() => Tabelle(("Marke, Modell:", "VW Golf"), ("Erstzulassung:", "05/2019"),
                                            ("Kilometerstand:", "61.000 km"), ("Leistung:", "110 kW (150 PS)"),
                                            ("Kraftstoff:", "Elektro"), ("Inserat-ID:", "3529712138"));

    [Fact]   // haengt die Texterkennung, gilt der zweite Blick nach der Frist als fehlgeschlagen — nie ein Haenger
    public async Task Haengende_Texterkennung_im_zweiten_Blick_wird_nach_der_Frist_aufgegeben()
    {
        var ocr = TextErkennung.Erstelle(out _);
        if (ocr == null) return;                                     // keine Texterkennung auf diesem PC
        using var bild = Golf();
        var zt = await ocr.LiesAsync(bild, 3);
        var f = DetailLeser.Auswerten(zt, Array.Empty<OcrZeile>(), 0);
        var zeilen = new ConcurrentQueue<string>();
        Protokoll.NeueZeile += zeilen.Enqueue;
        var alt = TextErkennung.Frist;
        try
        {
            TextErkennung.Frist = TimeSpan.FromMilliseconds(1);      // keine Erkennung ist so schnell
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            Assert.Null(await ZweiterBlick.AnwendenAsync(f, bild, zt, null, 96, ocr, null));
            Assert.True(uhr.ElapsedMilliseconds < 5000, uhr.ElapsedMilliseconds.ToString());
            Assert.Contains(zeilen, z => z.Contains("Zweiter Blick fehlgeschlagen") && z.Contains("TimeoutException"));
            Assert.Empty(f.AlternativenMarkeModell);
        }
        finally
        {
            TextErkennung.Frist = alt;
            Protokoll.NeueZeile -= zeilen.Enqueue;
        }
    }

    // ---- mit der echten Windows-Texterkennung -------------------------------------------------------------------

    [Fact]
    public async Task Zweiter_Blick_liest_die_drei_Werte_sinnvoll()
    {
        var ocr = TextErkennung.Erstelle(out _);
        var ocr2 = TextErkennung.Erstelle(out _);
        if (ocr == null || ocr2 == null) return;                     // keine Texterkennung auf diesem PC
        using var bild = Tabelle(("Marke, Modell:", "Hyundai i30"), ("Erstzulassung:", "05/2019"),
                                 ("Kilometerstand:", "61.000 km"), ("Kraftstoff:", "Elektro"), ("Inserat-ID:", "3529712138"));
        var zt = await ocr.LiesAsync(bild, 3);
        var bereiche = DetailLeser.WertBereiche(zt, bild.Width, bild.Height, ZweiterBlick.Felder);
        Assert.Equal(3, bereiche.Count);
        var liste = ZweiterBlick.Felder.Select(f => (f, bereiche[f])).ToList();
        var gelesen = await ZweiterBlick.LesenAsync(bild, liste, 96, ocr, ocr2);
        foreach (var (feld, l) in gelesen) _aus.WriteLine($"{feld}: {string.Join(" / ", l)}");
        // je Feld genau eine Lesung je Aufbereitung, und jede Lesung ist nur der Wert (nicht die Bezeichnung,
        // nicht die Nachbarzeile)
        Assert.All(gelesen.Values, l => Assert.Equal(ZweiterBlick.Reihenfolge.Length, l.Count));
        Assert.Contains("Hyundai i30", gelesen[Feld.MarkeModell].Select(s => ZweiterBlick.Saeubern(Feld.MarkeModell, s)));
        Assert.Contains("Elektro", gelesen[Feld.Kraftstoff].Select(s => ZweiterBlick.Saeubern(Feld.Kraftstoff, s)));
        Assert.Contains("3529712138", gelesen[Feld.InseratId].Select(s => ZweiterBlick.Saeubern(Feld.InseratId, s)));
        Assert.All(gelesen.Values.SelectMany(l => l), s => Assert.DoesNotContain(":", s ?? ""));

        // im ganzen Lesen: Rohtext nennt den zweiten Blick, Alternativen weichen immer vom Hauptwert ab
        var lesung = await AutoPointerQuelle.LiesBilderAsync(ocr, bild, null, 96, false);
        Assert.Contains("2. Blick:", lesung.Rohtext);
        var f = lesung.Fahrzeug;
        Assert.DoesNotContain(f.MarkeModellText, f.AlternativenMarkeModell);
        Assert.DoesNotContain(f.Kraftstoff, f.AlternativenKraftstoff);
        Assert.DoesNotContain(f.InseratId, f.AlternativenInseratId);
    }

    /// <summary>Der erste Durchgang hat ein Wort verschluckt ("Golf" statt "VW Golf", "BMW" statt "BMW X1" — gemessen
    /// 09.10.): hier nachgestellt, indem sein Stueck gekuerzt wird. Der zweite Blick sieht ab der Wertspalte und ueber das
    /// Gelesene hinaus und liest das ganze Modell. (Woerter, die die Texterkennung sicher liest — "VW" allein laesst sie
    /// manchmal auch im sauberen Ausschnitt weg.)</summary>
    [Theory]
    [InlineData("Skoda Octavia", "Octavia", true)]      // vorne verschluckt
    [InlineData("Opel Astra", "Opel", false)]           // hinten verschluckt
    public async Task Zweiter_Blick_findet_ein_verschlucktes_Wort(string modell, string gelesen, bool vorne)
    {
        var ocr = TextErkennung.Erstelle(out _);
        if (ocr == null) return;                                     // keine Texterkennung auf diesem PC
        using var bild = Tabelle(("Marke, Modell:", modell), ("Erstzulassung:", "05/2019"), ("Kilometerstand:", "61.000 km"),
                                 ("Kraftstoff:", "Benzin"), ("Inserat-ID:", "3529712138"));
        var zt = await ocr.LiesAsync(bild, 3);
        var wert = zt.Single(z => z.X > 150 && z.Text == modell);
        double weg = wert.Breite * (modell.Length - gelesen.Length) / modell.Length;
        var verschluckt = zt.Select(z => z != wert ? z
            : vorne ? z with { Text = gelesen, X = z.X + weg, Breite = z.Breite - weg }
            : z with { Text = gelesen, Breite = z.Breite - weg }).ToList();
        var f = DetailLeser.Auswerten(verschluckt, Array.Empty<OcrZeile>(), 0);
        Assert.Equal(gelesen, f.MarkeModellText);
        _aus.WriteLine(await ZweiterBlick.AnwendenAsync(f, bild, verschluckt, null, 96, ocr, null));
        Assert.Contains(modell, f.AlternativenMarkeModell);
        Assert.Equal(gelesen, f.MarkeModellText);                    // der Hauptwert bleibt — der Server entscheidet
    }

    [Fact]   // Messung: so viel laenger dauert ein Lesen mit dem zweiten Blick (eigene Engines, wie im Programm)
    public async Task Zusatzzeit_je_Lesung()
    {
        var ocr = TextErkennung.Erstelle(out _);
        var ocr2 = TextErkennung.Erstelle(out _);
        var ocr3 = TextErkennung.Erstelle(out _);
        if (ocr == null || ocr2 == null || ocr3 == null) return;     // keine Texterkennung auf diesem PC
        using var bild = Golf();
        await AutoPointerQuelle.LiesBilderAsync(ocr, bild, null, 96, false, ocr2, ocr3);           // aufwaermen
        const int n = 10;
        var ohne = new List<long>();
        var mit = new List<long>();
        for (int i = 0; i < n; i++)
        {
            var uhr = System.Diagnostics.Stopwatch.StartNew();
            await AutoPointerQuelle.LiesBilderAsync(ocr, bild, null, 96, false, ocr2, ocr3, zweiterBlick: false);
            ohne.Add(uhr.ElapsedMilliseconds);
            uhr.Restart();
            await AutoPointerQuelle.LiesBilderAsync(ocr, bild, null, 96, false, ocr2, ocr3, zweiterBlick: true);
            mit.Add(uhr.ElapsedMilliseconds);
        }
        double o = ohne.OrderBy(x => x).ElementAt(n / 2), m = mit.OrderBy(x => x).ElementAt(n / 2);
        _aus.WriteLine($"Lesen ohne zweiten Blick {o} ms, mit {m} ms (Median aus {n}) — zusaetzlich {m - o} ms");
        Assert.True(m - o < 2000, $"zweiter Blick zu langsam: +{m - o} ms");
    }
}
