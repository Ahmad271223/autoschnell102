namespace AutoPointerVergleich;

/// <summary>Was aus der AutoPointer-Detailansicht gelesen wurde.</summary>
internal sealed class Fahrzeug
{
    /// <summary>Feld "Marke, Modell" wie angezeigt, z. B. "Bentley Bentayga".</summary>
    public string MarkeModellText { get; set; } = "";
    public string? Marke { get; set; }
    public string? Modell { get; set; }
    /// <summary>Marke/Modell wie in AutoPointer (getrennt), so gehen sie an den Server.</summary>
    public string? MarkeText { get; set; }
    public string? ModellText { get; set; }
    /// <summary>Inserat-Titel aus dem Kopf (oft mit Motor/Ausstattung).</summary>
    public string? Titel { get; set; }
    public string? Quelle { get; set; }
    public string? InseratId { get; set; }
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

    public bool Unfallwagen => (Zustand ?? "").Contains("unfall", StringComparison.OrdinalIgnoreCase);

    public string KraftstoffCode => FahrzeugCodes.KraftstoffCode(Kraftstoff) ?? "";
    public string GetriebeCode => FahrzeugCodes.GetriebeCode(Getriebe) ?? "";

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

    /// <summary>Kennung "Bentley Bentayga | 03/2017 | 84975 km | 320 kW": bleibt sie
    /// gleich, ist es dasselbe Fahrzeug und es wird nichts neu geoeffnet.</summary>
    public string Schluessel =>
        $"{(Marke != null ? $"{Marke} {Modell}".Trim() : MarkeModellText)} | {EzText} | {Kilometer} km | {Kw} kW";

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
