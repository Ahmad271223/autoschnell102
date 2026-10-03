namespace AutoPointerVergleich.Tests;

/// <summary>Textzeilen, wie sie die Windows-Texterkennung aus AutoPointer liefert
/// (Passat: echte Ausgabe vom 03.10.2026, Zoom x3; Bentley: nach dem Screenshot
/// mit typischen Lesefehlern in den Bezeichnungen).</summary>
internal static class Fixtures
{
    public static OcrZeile Z(string text, double x, double y, double breite = 120, double hoehe = 13) =>
        new(text, x, y, breite, hoehe);

    public static readonly OcrZeile[] PassatTechnik =
    {
        Z("Marke, Model:", 12, 4), Z("Preis:", 12, 27), Z("Zustand:", 12, 48), Z("Kategorie:", 12, 71),
        Z("Erstzulassung:", 12, 92), Z("Khmeterstand:", 12, 114), Z("Leistung:", 12, 137), Z("Getriebeart:", 12, 158),
        Z("Kraftstoff:", 12, 180), Z("Farbe:", 12, 202), Z("Kimatisierung:", 12, 224), Z("Interieur:", 11, 247),
        Z("Türen:", 11, 269), Z("Umwettplakette", 12, 290), Z("Inserat-ID:", 11, 313),
        Z("VW Passat Variant", 211, 5), Z("1.500 EUR", 213, 27), Z("Unfallwagen", 212, 48),
        Z("Kombi/Lieferwagen", 212, 70), Z("10/2006", 213, 92), Z("244.000 km", 212, 115),
        Z("125 kW (170 PS)", 213, 137), Z("Automatik", 211, 158), Z("Diesel", 212, 180), Z("Schwarz", 212, 202),
        Z("Klimaanlage", 213, 224), Z("Stoff", 212, 246), Z("4/5", 212, 268), Z("4 (Grün)", 212, 291),
        Z("3529712138", 212, 313),
    };

    public static readonly OcrZeile[] PassatKopf =
    {
        Z("03.10.2026 12:07:54 - Inserat von Kleinanzeigen", 13, 27, 400, 14),
        Z("VW Passat B6 - Bastlerfahrzeug", 72, 54, 330, 20),
        Z("Neu", 25, 56, 30, 14),
        Z("1.500 EUR", 14, 93, 110, 18),
        Z("kleinanzeigen", 698, 55, 120, 18),
    };

    public static readonly OcrZeile[] BentleyTechnik =
    {
        Z("Marke, Modell:", 12, 4), Z("Preis:", 12, 26), Z("Zustand:", 12, 48), Z("Kategorie:", 12, 70),
        Z("Erstzulassung:", 12, 92), Z("Kibmeterstand:", 12, 114), Z("Leistung:", 12, 136), Z("Getriebeatt:", 12, 158),
        Z("Kraftstoff:", 12, 180), Z("Farbe:", 12, 202), Z("Herstellerfarbe:", 12, 224), Z("Omatisierung:", 12, 246),
        Z("Bentley Bentayga", 212, 4), Z("99.900 EUR", 212, 26), Z("Gebraucht", 212, 48),
        Z("SUV/Geländewagen/Pickup", 212, 70), Z("03/2017", 212, 92), Z("84.975 km", 212, 114),
        Z("320 kW (435 PS)", 212, 136), Z("Automatik", 212, 158), Z("Diesel", 212, 180),
        Z("Beige metallic", 212, 202), Z("L999", 212, 224), Z("Klimaautomatik", 212, 246),
    };

    public static readonly OcrZeile[] BentleyKopf =
    {
        Z("03.10.2026 11:55:00 - Inserat von Mobile.de", 13, 27, 400, 14),
        Z("Neu", 25, 56, 30, 14),
        Z("Bentley Bentayga V8 Diesel, 1. Hand, Mulliner, Nai...", 72, 54, 520, 20),
        Z("99.900 EUR", 14, 93, 110, 18),
        Z("mobile.de", 690, 50, 140, 20),
    };

    private static Katalog? _katalog;
    public static Katalog Kat => _katalog ??= Katalog.Laden();

    public static Fahrzeug Bentley() => DetailLeser.Auswerten(BentleyTechnik, BentleyKopf, 846);
    public static Fahrzeug Passat() => DetailLeser.Auswerten(PassatTechnik, PassatKopf, 846);
}
