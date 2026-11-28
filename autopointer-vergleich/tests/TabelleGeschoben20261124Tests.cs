using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Serverpruefung 24.11.2026 (1.5.20, Lesebilder Honda Jazz / Dacia Dokker): die Technik-Tabelle in AutoPointer war
/// nach unten geschoben — zu sehen nur Farbe, Klimatisierung, Hubraum, Tueren …, nicht Marke/Modell, Erstzulassung und
/// Kilometerstand. Bisher: "fehlt: Erstzulassung, Kilometerstand – Detailbereich groesser ziehen" und ein Lesebild, das
/// nichts zeigt. Jetzt: die oberen Zeilen werden gezaehlt (<see cref="Fahrzeug.ObenFelder"/>), der Hinweis sagt "nach oben
/// schieben", kein Lesebild.</summary>
[Collection("Protokolldateien")]
public class TabelleGeschoben20261124Tests
{
    public TabelleGeschoben20261124Tests() => Protokoll.DateiAktiv = false;

    private static OcrZeile Z(string text, double x, double y, double b = 300, double h = 14) => new(text, x, y, b, h);

    // die Zeilen des Lesebilds Honda Jazz (06.11.2026): nur der untere Teil der Tabelle
    private static List<OcrZeile> Unterer_Teil() => new()
    {
        Z("Farbe:", 10, 10, 120), Z("Silber", 200, 10, 120),
        Z("Klimatisierung:", 10, 32, 120), Z("Klimaanlage", 200, 32, 120),
        Z("Hubraum:", 10, 54, 120), Z("1.339 cm³", 200, 54, 120),
        Z("Türen:", 10, 76, 120), Z("4/5", 200, 76, 120),
        Z("Umweltplakette:", 10, 98, 120), Z("4 (Grün)", 200, 98, 120),
    };

    [Fact]
    public void Obere_Zeilen_werden_gezaehlt()
    {
        var unten = DetailLeser.Auswerten(Unterer_Teil(), Array.Empty<OcrZeile>(), 0);
        Assert.True(unten.TabellenFelder >= 2);
        Assert.Equal(0, unten.ObenFelder);
        Assert.True(DetailLeser.TabelleNachUntenGeschoben(unten));

        var oben = new List<OcrZeile>
        {
            Z("Marke, Modell:", 10, 10, 120), Z("Honda Jazz", 200, 10, 120),
            Z("Erstzulassung:", 10, 32, 120), Z("05/2011", 200, 32, 120),
            Z("Kilometerstand:", 10, 54, 120), Z("98.000 km", 200, 54, 120),
            Z("Farbe:", 10, 76, 120), Z("Silber", 200, 76, 120),
        };
        var ganz = DetailLeser.Auswerten(oben, Array.Empty<OcrZeile>(), 0);
        Assert.Equal(3, ganz.ObenFelder);
        Assert.False(DetailLeser.TabelleNachUntenGeschoben(ganz));
        // zwei Durchlaeufe: das Maximum — sah einer die oberen Zeilen, ist die Tabelle nicht verschoben
        Assert.Equal(3, DetailLeser.Ergaenzen(unten, ganz).ObenFelder);
        Assert.False(DetailLeser.TabelleNachUntenGeschoben(unten));

        // gar keine Tabelle (0/1 Zeilen) ist "nicht zu sehen", nicht "verschoben"; ungezaehlt (null) nie
        var leer = DetailLeser.Auswerten(Array.Empty<OcrZeile>(), Array.Empty<OcrZeile>(), 0);
        Assert.False(DetailLeser.TabelleNachUntenGeschoben(leer));
        Assert.False(DetailLeser.TabelleNachUntenGeschoben(new Fahrzeug { TabellenFelder = 5 }));
        Assert.True(DetailLeser.IstObenFeld(Feld.Kilometer) && DetailLeser.IstObenFeld(Feld.MarkeModell));
        Assert.False(DetailLeser.IstObenFeld(Feld.Farbe) || DetailLeser.IstObenFeld(Feld.InseratId));
    }

    [Fact]   // der Titel springt fuer Marke/Modell ein (1.5.18) — Erstzulassung/Kilometer fehlen trotzdem, also "verschoben"
    public void Titel_als_Marke_aendert_nichts_am_Befund()
    {
        var f = DetailLeser.Auswerten(Unterer_Teil(), Array.Empty<OcrZeile>(), 0);
        f.Titel = "Honda Jazz 1.4 Elegance";
        Assert.True(DetailLeser.MarkeModellAusTitel(f));
        Assert.Equal(new[] { "Erstzulassung", "Kilometerstand" }, DetailLeser.Fehlend(f));
        Assert.True(DetailLeser.TabelleNachUntenGeschoben(f));
    }
}
