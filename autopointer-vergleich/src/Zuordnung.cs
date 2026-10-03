namespace AutoPointerVergleich;

internal sealed record Vergleich(string Portal, string Url);

/// <summary>Ergebnis der Katalog-Zuordnung fuer beide Portale.</summary>
internal sealed class Zuordnung
{
    public string MarkeText { get; init; } = "";
    public string ModellText { get; init; } = "";
    public MobileMarke? MobileMarke { get; init; }
    public ModellTreffer? MobileModell { get; init; }
    public AutoScoutMarke? AutoScoutMarke { get; init; }
    public ModellTreffer? AutoScoutModell { get; init; }
    public bool MarkeErkannt => MobileMarke != null || AutoScoutMarke != null;
}

/// <summary>Trennt "Marke, Modell" aus AutoPointer in Marke und Modell und gleicht
/// Lesefehler der Texterkennung mit den Katalogen ab. Die Suchlinks baut seit dem
/// 03.10.2026 der AutoSchnell-Server (Vergleichsregeln der Firma).</summary>
internal static class Zuordner
{
    public static Zuordnung Zuordnen(Fahrzeug f, Katalog k)
    {
        var teile = k.TeileMarkeModell(f.MarkeModellText);
        // Befund 03.10.2026: Kleinanzeigen zeigt manchmal nur "Andere" (ohne Marke) — dann Marke und Modell
        // aus der Ueberschrift ("Ford Mondeo Turnier 2.0 TDCi Diesel, ..." -> Ford Mondeo).
        bool ausTitel = false;
        if ((teile == null || Katalog.IstPlatzhalter(teile.Value.Marke)) && !string.IsNullOrWhiteSpace(f.Titel)
            && k.TeileMarkeModell(f.Titel.Trim()) is { } ausT && !Katalog.IstPlatzhalter(ausT.Marke))
        {
            teile = ausT;
            ausTitel = true;
        }
        if (teile == null) return new Zuordnung { MarkeText = f.MarkeModellText };
        var (marke, modell) = teile.Value;
        var mm = k.MobileMarkeFinden(marke);
        var am = k.AutoScoutMarkeFinden(marke);
        var z = new Zuordnung
        {
            MarkeText = marke,
            ModellText = modell,
            MobileMarke = mm,
            MobileModell = mm != null ? k.MobileModellFinden(mm, modell, f.Titel) : null,
            AutoScoutMarke = am,
            AutoScoutModell = am != null ? k.AutoScoutModellFinden(am, modell, f.Titel) : null,
        };
        f.Marke = mm?.Name ?? am?.MakeName ?? marke;
        f.Modell = z.MobileModell?.Name ?? z.AutoScoutModell?.Name ?? modell;
        // An den Server geht, was AutoPointer zeigt (wie die Inseratsdaten im App-Vergleich) —
        // nur ein Lesefehler wird durch den Katalognamen ersetzt. Befund 03.10.2026: Kleinanzeigen
        // fuehrt viele Autos als "weitere VW"; das Modell kommt dann aus dem Titel ("VW Beetle Cabrio"
        // -> Beetle), sonst lehnt der Server beide Portale ab und es oeffnet sich nichts.
        // "VW VW-Busse" (Kategorie) + Ueberschrift "T5 Bulli multivan" -> T5 Multivan: was nicht aus dem Feld
        // kam, schickt das Programm mit dem Katalognamen, den der Server sicher kennt.
        bool ersetzt = ausTitel || Katalog.IstPlatzhalter(modell)
                       || z.MobileModell?.AusTitel == true || z.AutoScoutModell?.AusTitel == true;
        f.MarkeText = marke;
        f.ModellText = z.MobileModell?.Unscharf == true ? z.MobileModell.Name
                     : z.AutoScoutModell?.Unscharf == true ? z.AutoScoutModell.Name
                     : ersetzt ? z.MobileModell?.Name ?? z.AutoScoutModell?.Name ?? modell
                     : modell;
        return z;
    }
}
