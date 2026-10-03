namespace AutoPointerVergleich;

/// <summary>Was aus der AutoPointer-Detailansicht gelesen wurde.</summary>
internal sealed class Fahrzeug
{
    /// <summary>Feld "Marke, Modell" wie angezeigt, z. B. "Bentley Bentayga".</summary>
    public string MarkeModellText { get; set; } = "";
    /// <summary>Katalognamen, wie sie der Server erkannt hat (seit 1.4.0) — nur zur Anzeige.</summary>
    public string? Marke { get; set; }
    public string? Modell { get; set; }
    /// <summary>Inserat-Titel aus dem Kopf (oft mit Motor/Ausstattung).</summary>
    public string? Titel { get; set; }
    public string? Quelle { get; set; }
    public string? InseratId { get; set; }
    /// <summary>AutoScout-Kennung (Zeile "Hash-ID"), nur wenn vollstaendig und zweimal gleich gelesen.</summary>
    public string? HashId { get; set; }
    public int? Preis { get; set; }
    public int? EzMonat { get; set; }
    public int? EzJahr { get; set; }
    public int? Kilometer { get; set; }
    public int? Kw { get; set; }
    public int? Ps { get; set; }
    public string? Kraftstoff { get; set; }
    public string? Getriebe { get; set; }
    public string? Kategorie { get; set; }
    public string? Zustand { get; set; }
    public string? Tueren { get; set; }
    /// <summary>Pruefbericht 03.10.2026 (Nr. 9): kW und PS widersprachen sich in BEIDEN Lesedurchgaengen —
    /// welcher Wert falsch gelesen ist, laesst sich nicht sagen. Dann gilt die Leistung als unbekannt (der Vergleich
    /// laeuft ohne Leistungsfilter, mit Hinweis) statt mit einem moeglicherweise falschen Wert.</summary>
    public bool LeistungUnsicher { get; set; }

    /// <summary>Pruefbericht 03.10.2026 (Nr. 1): Kennung des Inserats (Inserat-ID, sonst AutoScout-Hash-ID), klein
    /// und ohne Leer-/Sonderzeichen; null ohne Kennung. Bewusst OHNE Quelle — die steht in einer anderen Zeile
    /// und darf als Lesefehler nicht aus demselben Auto zwei machen.</summary>
    public string? InseratKennung
    {
        get
        {
            string roh = !string.IsNullOrWhiteSpace(InseratId) ? InseratId! : HashId ?? "";
            string id = new(roh.Trim().ToLowerInvariant().Where(char.IsLetterOrDigit).ToArray());
            return id.Length >= 4 ? id : null;
        }
    }

    /// <summary>Ist <paramref name="neu"/> dasselbe Auto wie das zuletzt gemerkte? Haben BEIDE eine Inserat-Kennung,
    /// entscheidet sie (zwei Neuwagen mit 0 km und gleichen Daten sind verschiedene Inserate); sonst der Schluessel
    /// aus den Fahrzeugdaten wie bisher.</summary>
    public static bool GleichesAuto(string? altSchluessel, string? altKennung, Fahrzeug neu)
    {
        if (altSchluessel == null) return false;
        string? kennung = neu.InseratKennung;
        if (kennung != null && altKennung != null) return kennung == altKennung;
        return neu.Schluessel == altSchluessel;
    }

    public bool Unfallwagen => (Zustand ?? "").Contains("unfall", StringComparison.OrdinalIgnoreCase);

    /// <summary>Zusatz aus dem Titel nach Marke/Modell ("V8 Diesel, 1. Hand ...").</summary>
    public string? Variante
    {
        get
        {
            if (string.IsNullOrWhiteSpace(Titel)) return null;
            // fuehrende Woerter weglassen, die schon in Marke/Modell stehen
            // ("VW Passat B6 - Bastler" bei "VW Passat Variant" -> "B6 - Bastler")
            var bekannt = $"{MarkeModellText} {Marke} {Modell}"
                .Split(' ', StringSplitOptions.RemoveEmptyEntries)
                .Select(FahrzeugCodes.Norm).Where(w => w.Length > 0).ToHashSet();
            var woerter = Titel.Split(' ', StringSplitOptions.RemoveEmptyEntries).ToList();
            int i = 0;
            while (i < woerter.Count && bekannt.Contains(FahrzeugCodes.Norm(woerter[i]))) i++;
            string t = string.Join(" ", woerter.Skip(i)).Trim(' ', '-', ',', '·');
            return t.Length > 0 ? t : null;
        }
    }

    /// <summary>Kennung "bent1eybentayga | 03/2017 | 84975 km | 320 kW": bleibt sie gleich, ist es dasselbe
    /// Fahrzeug und es wird nichts neu geoeffnet. Aus dem gelesenen Text (die Erkennung macht der Server) —
    /// klein, ohne Zeichen, i/l/1 und o/0 gleich, damit ein Lesefehler kein zweites Oeffnen ausloest.</summary>
    public string Schluessel => $"{Kennwort(MarkeModellText)} | {EzText} | {Kilometer} km | {Kw} kW";

    private static string Kennwort(string text) =>
        new(FahrzeugCodes.Norm(text).Select(c => c switch { 'i' or 'l' => '1', 'o' => '0', _ => c }).ToArray());

    public string EzText => EzJahr == null ? "?" : EzMonat != null ? $"{EzMonat:00}/{EzJahr}" : $"{EzJahr}";

    public IEnumerable<string> Beschreibung()
    {
        yield return $"{(Marke != null ? $"{Marke} {Modell}".Trim() : MarkeModellText)}"
                     + (Variante != null ? $"  ({Variante})" : "");
        yield return $"EZ: {EzText}   KM: {(Kilometer?.ToString("N0") ?? "?")}   Leistung: {(Kw != null ? $"{Kw} kW / {Ps} PS" : "?")}";
        yield return $"Kraftstoff: {Kraftstoff ?? "?"}   Getriebe: {Getriebe ?? "?"}   Preis: {(Preis != null ? $"{Preis:N0} €" : "?")}";
        var rest = new List<string>();
        if (Zustand != null) rest.Add($"Zustand: {Zustand}");
        if (Kategorie != null) rest.Add($"Kategorie: {Kategorie}");
        if (Quelle != null) rest.Add($"Quelle: {Quelle}");
        if (InseratId != null) rest.Add($"Inserat-ID: {InseratId}");
        if (rest.Count > 0) yield return string.Join("   ", rest);
    }
}
