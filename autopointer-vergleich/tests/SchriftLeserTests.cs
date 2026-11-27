using System.Drawing;
using System.Drawing.Imaging;
using System.Windows.Forms;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>1.5.22 (Wunsch Ahmad 27.11.2026, "es darf keine Lesefehler geben"): der Schrift-Abgleich. Die Tests zeichnen
/// Text mit derselben Windows-Schrift wie AutoPointer (Tahoma, GDI) und verlangen ihn Zeichen fuer Zeichen zurueck —
/// einmal als ganze Zeile, einmal Wort fuer Wort gezeichnet (so zeichnet AutoPointer).</summary>
public class SchriftLeserTests
{
    private static readonly TextFormatFlags Flags = TextFormatFlags.NoPadding | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine;

    /// <summary>Linker Rand im Testbild — der Leser muss die Stiftstelle selbst finden.</summary>
    private const int RandLinks = 5;

    private static Bitmap Zeichne(int px, bool wortweise, params string[] zeilen)
    {
        using var schrift = new Font(SchriftLeser.SchriftName, px, FontStyle.Regular, GraphicsUnit.Pixel);
        var gross = new Size(4000, 200);
        int hoehe = TextRenderer.MeasureText("Xg", schrift, gross, Flags).Height;
        int breite = zeilen.Max(z => TextRenderer.MeasureText(z.Length == 0 ? " " : z, schrift, gross, Flags).Width) + 12 + RandLinks;
        var bild = new Bitmap(breite, hoehe * zeilen.Length, PixelFormat.Format32bppRgb);
        using var g = Graphics.FromImage(bild);
        g.Clear(Color.White);
        int leer = TextRenderer.MeasureText("x x", schrift, gross, Flags).Width - 2 * TextRenderer.MeasureText("x", schrift, gross, Flags).Width;
        for (int i = 0; i < zeilen.Length; i++)
        {
            if (!wortweise)
            {
                TextRenderer.DrawText(g, zeilen[i], schrift, new Point(RandLinks, i * hoehe), Color.Black, Color.White, Flags);
                continue;
            }
            int x = RandLinks;
            foreach (string wort in zeilen[i].Split(' '))
            {
                if (wort.Length > 0)
                {
                    // jedes Wort fuer sich an seiner Stiftstelle, wie ein HTML-Feld es zeichnet
                    int b = TextRenderer.MeasureText(wort, schrift, gross, Flags).Width;
                    // ab der Stiftstelle des Worts: der linke Ueberhang wird abgeschnitten, der rechte bleibt (wie in AutoPointer gemessen)
                    TextRenderer.DrawText(g, wort, schrift, new Rectangle(x, i * hoehe, b + 6, hoehe), Color.Black, Color.White, Flags);
                    x += TextRenderer.MeasureText(wort, schrift, gross, Flags).Width;
                }
                x += leer;
            }
        }
        return bild;
    }

    /// <summary>Was der Leser an den ersten Zeilen sieht — nur fuer die Fehlermeldung.</summary>
    private static string Diagnose(Bitmap bild, int px)
    {
        var leser = SchriftLeser.Fuer(px);
        int abstand = SchriftLeser.Zeilenabstand(BeschreibungGanz.LeereZeilen(bild));
        var sb = new System.Text.StringBuilder($"Zeilenabstand {abstand}, Schrifthöhe {leser.Hoehe}, "
                                               + $"probierte Größen {string.Join("/", SchriftLeser.GroessenFuer(abstand))}");
        for (int i = 0; i * leser.Hoehe < bild.Height; i++)
            sb.Append($"\n  Zeile {i}: „{leser.ZeileBei(bild, i * leser.Hoehe)?.Text}“");
        return sb.ToString();
    }

    private static readonly string[] Text =
    {
        "BMW X1 23d X Drive.Allradantrieb.Das Auto wurde komplett beim BMW Händler Scheckheft",
        "gepflegt. Alles funktioniert einwandfrei, 1. Hand, TÜV 03/2027 – Preis: 6.990 € VB!",
        "",
        "Steuerkette macht Geräusche & muss erneuert werden (Bastlerfahrzeug); „Export“ möglich?",
        "Füße, Öl, Ärger, groß: äöüÄÖÜß 0123456789 km/h @ 100% * #1 + 2 = 3 'ja' \"nein\" [x] {y} <z>",
        "Illinois lIl1| rn m nn vv w cl d ijJ fft ftt tt ff fi fl Tf Ty AV Wa To r. r, f. f,",
    };

    [Theory]
    [InlineData(12, true)]
    [InlineData(14, true)]
    [InlineData(18, true)]
    [InlineData(11, true)]
    [InlineData(13, true)]
    [InlineData(16, true)]
    public void Liest_gezeichneten_Text_Zeichen_fuer_Zeichen(int px, bool wortweise)
    {
        using var bild = Zeichne(px, wortweise, Text);
        var l = SchriftLeser.LiesFeld(bild);
        Assert.True(l != null, Diagnose(bild, px));
        Assert.Equal(Text, l!.Zeilen.Select(z => z.Text).ToArray());
        Assert.True(l.Exakt);
        Assert.Equal(0, l.Luecken);
    }

    [Theory]
    [InlineData(11, "BMW")]
    [InlineData(11, "gepflegt.")]
    [InlineData(11, "Steuerkette")]
    [InlineData(16, "BMW")]
    [InlineData(16, "Steuerkette")]
    [InlineData(11, "Hand, TÜV")]
    [InlineData(11, "TÜV 03/2027")]
    [InlineData(11, "03/2027 –")]
    [InlineData(11, "– Preis:")]
    [InlineData(11, "1. Hand,")]
    [InlineData(11, "Hand,")]
    [InlineData(11, "TÜV")]
    [InlineData(11, "03/2027")]
    [InlineData(11, "–")]
    [InlineData(11, "Preis:")]
    [InlineData(12, "TÜV 03/2027 – Preis: 6.990 € VB!")]
    [InlineData(15, "TÜV 03/2027 – Preis: 6.990 € VB!")]
    public void Einzelnes_Wort(int px, string wort)
    {
        using var bild = Zeichne(px, true, wort);
        var leser = SchriftLeser.Fuer(px);
        string spur = leser.SpurBei(bild, 0);
        Assert.True(leser.ZeileBei(bild, 0)?.Text == wort, spur);
    }

    [Fact]
    public void Leeres_Feld_ergibt_keine_Zeilen()
    {
        using var bild = new Bitmap(300, 23, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild)) g.Clear(Color.White);
        var l = SchriftLeser.LiesFeld(bild);
        Assert.NotNull(l);
        Assert.Empty(l!.Zeilen);
        Assert.False(l.Exakt);
    }

    [Theory]
    [InlineData(11)]
    [InlineData(14)]
    [InlineData(17)]
    public void Eine_einzige_Zeile_wird_ohne_Zeilenabstand_gelesen(int px)
    {
        using var bild = Zeichne(px, true, "Bei Fragen einfach anrufen, VB 4.500 €");
        var l = SchriftLeser.LiesFeld(bild);
        Assert.True(l is { Exakt: true }, Diagnose(bild, px));
        Assert.Equal("Bei Fragen einfach anrufen, VB 4.500 €", l!.Text);
    }

    [Fact]
    public void Fremde_Schrift_geht_nicht_auf_und_gilt_nicht_als_exakt()
    {
        using var schrift = new Font("Times New Roman", 15, FontStyle.Italic, GraphicsUnit.Pixel);
        using var bild = new Bitmap(500, 40, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild))
        {
            g.Clear(Color.White);
            TextRenderer.DrawText(g, "Motorschaden, nicht fahrbereit", schrift, new Point(0, 0), Color.Black, Color.White, Flags);
            TextRenderer.DrawText(g, "Getriebe defekt und Unfall", schrift, new Point(0, 20), Color.Black, Color.White, Flags);
        }
        var l = SchriftLeser.LiesFeld(bild);
        Assert.True(l == null || !l.Exakt);
        Assert.True(l == null || !BeschreibungGanz.Brauchbar(l));
    }

    [Fact]
    public void Ein_unbekanntes_Zeichen_wird_zur_Luecke_der_Rest_bleibt_genau()
    {
        // ein Kringel mitten im Text, den keine Vorlage kennt
        using var bild = Zeichne(14, true, "Sehr gepflegt und sauber", "Motor xxxx einwandfrei und TÜV neu bis 2028");
        using (var g = Graphics.FromImage(bild))
        {
            using var schrift = new Font(SchriftLeser.SchriftName, 14, FontStyle.Regular, GraphicsUnit.Pixel);
            int h = TextRenderer.MeasureText("Xg", schrift, new Size(4000, 200), Flags).Height;
            int x0 = RandLinks + TextRenderer.MeasureText("Motor ", schrift, new Size(4000, 200), Flags).Width;
            int b = TextRenderer.MeasureText("xxxx", schrift, new Size(4000, 200), Flags).Width;
            g.FillRectangle(Brushes.White, x0, h, b, h);
            g.DrawEllipse(Pens.Black, x0 + 2, h + 3, b - 5, h - 7);
        }
        var l = SchriftLeser.LiesFeld(bild);
        Assert.NotNull(l);
        Assert.False(l!.Exakt);
        Assert.Equal(1, l.Luecken);
        Assert.Equal("Sehr gepflegt und sauber", l.Zeilen[0].Text);
        Assert.Equal($"Motor {SchriftLeser.Luecke} einwandfrei und TÜV neu bis 2028", l.Zeilen[1].Text);
        Assert.False(BeschreibungGanz.Brauchbar(l));         // 1 Luecke auf 12 Woerter: zu viel (hoechstens jedes zwanzigste)
    }

    [Fact]
    public void Zeilenabstand_ist_der_haeufigste_Abstand_der_Zeilenoberkanten()
    {
        var leer = Enumerable.Repeat(true, 100).ToArray();
        foreach (int oben in new[] { 3, 20, 37, 71 })              // 17, 17, 34 (eine Leerzeile dazwischen)
            for (int y = oben; y < oben + 11; y++) leer[y] = false;
        Assert.Equal(17, SchriftLeser.Zeilenabstand(leer));
        Assert.Equal(0, SchriftLeser.Zeilenabstand(Enumerable.Repeat(true, 50).ToArray()));
    }

    [Fact]
    public void Nutzlast_meldet_exakt_nur_wenn_abgeglichen()
    {
        var f = new Fahrzeug { MarkeModellText = "BMW X5", BeschreibungVoll = "Steuerkette rasselt", BeschreibungExakt = true };
        Assert.Equal(true, AutoSchnellDienst.Nutzlast(f)["beschreibung_exakt"]);
        f.BeschreibungExakt = false;
        Assert.False(AutoSchnellDienst.Nutzlast(f).ContainsKey("beschreibung_exakt"));
        Assert.False(AutoSchnellDienst.Nutzlast(new Fahrzeug { MarkeModellText = "BMW", BeschreibungExakt = true }).ContainsKey("beschreibung_exakt"));
    }
}
