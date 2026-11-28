using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 27.11.2026 (Dauerlauf): was der Haendler ueberhaupt haben will — geprueft schon an der
/// Listenzeile, damit nur passende Autos angeklickt und ganz gelesen werden (gemessen: 1,3 neue Autos je Sekunde,
/// ein ganz gelesenes Auto kostet etwa 1,3 s). 0 bzw. leer = keine Grenze.</summary>
internal sealed class DauerlaufRegeln
{
    public bool NurPrivat { get; set; } = true;
    /// <summary>Unfallwagen (Zustand in der Liste) und Titel wie „an Bastler“, „Motorschaden“ gar nicht erst oeffnen.</summary>
    public bool OhneUnfall { get; set; } = true;
    public int PreisVon { get; set; }
    public int PreisBis { get; set; }
    public int KmBis { get; set; }
    public int JahrAb { get; set; }
    /// <summary>Nur diese Portale (wie in der Spalte „Datenquelle“: Mobile.de, Kleinanzeigen …); leer = alle.</summary>
    public List<string> Portale { get; set; } = new();

    /// <summary>Die Regeln als Text (Gegenstueck zu <see cref="Aus"/>) — so stehen sie in den Einstellungen. (rein, fuer Tests)</summary>
    internal string AlsText()
    {
        var teile = new List<string> { NurPrivat ? "privat" : "haendler", OhneUnfall ? "unfall" : "mitunfall" };
        if (PreisVon > 0 || PreisBis > 0) teile.Add($"preis={PreisVon}-{PreisBis}");
        if (KmBis > 0) teile.Add($"km={KmBis}");
        if (JahrAb > 0) teile.Add($"jahr={JahrAb}");
        if (Portale.Count > 0) teile.Add("portal=" + string.Join(",", Portale));
        return string.Join(";", teile);
    }

    /// <summary>Kurzform fuer die Befehlszeile: „privat;unfall;preis=500-30000;km=250000;jahr=2008;portal=Kleinanzeigen“;
    /// „haendler“ laesst auch Haendler zu, „mitunfall“ auch Unfallwagen. (rein, fuer Tests)</summary>
    internal static DauerlaufRegeln Aus(string? text)
    {
        var r = new DauerlaufRegeln();
        foreach (string teil in (text ?? "").Split(';', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var kv = teil.Split('=', 2);
            string k = kv[0].ToLowerInvariant(), v = kv.Length > 1 ? kv[1] : "";
            static int Z(string s) => int.TryParse(s.Replace(".", ""), out int n) ? n : 0;
            switch (k)
            {
                case "privat": r.NurPrivat = true; break;
                case "haendler": r.NurPrivat = false; break;
                case "unfall": r.OhneUnfall = true; break;
                case "mitunfall": r.OhneUnfall = false; break;
                case "preis":
                    var p = v.Split('-', 2);
                    r.PreisVon = p.Length > 1 ? Z(p[0]) : 0;
                    r.PreisBis = Z(p[^1]);
                    break;
                case "km": r.KmBis = Z(v); break;
                case "jahr": r.JahrAb = Z(v); break;
                case "portal": r.Portale.AddRange(v.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)); break;
            }
        }
        return r;
    }
}

internal static class Vorauswahl
{
    /// <summary>Titel, die einen schweren Schaden ansagen. „unfallfrei“ ist keiner.</summary>
    private static readonly Regex SchwerImTitel = new(
        @"bastler|motorschaden|getriebeschaden|totalschaden|unfall(?!frei)(wagen|schaden|auto|fahrzeug)?|nicht fahrbereit|zum ausschlachten",
        RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);

    /// <summary>null = das Auto kommt weiter (wird angeklickt und ganz gelesen); sonst der Grund, warum nicht.
    /// Was in der Zeile nicht sicher gelesen wurde (null/leer), sortiert nie aus. (rein, fuer Tests)</summary>
    internal static string? Grund(ListenZeile z, DauerlaufRegeln r)
    {
        if (r.Portale.Count > 0 && !r.Portale.Contains(z.Portal, StringComparer.OrdinalIgnoreCase)) return $"Portal {z.Portal}";
        if (r.NurPrivat && z.Privat == false) return "Händler";
        if (r.OhneUnfall)
        {
            if (z.Art.Split(", ").Contains("Unfallwagen")) return "Unfallwagen";
            var m = SchwerImTitel.Match(z.Titel);
            if (m.Success) return $"Titel: „{m.Value}“";
        }
        if (z.Preis is { } preis)
        {
            if (r.PreisBis > 0 && preis > r.PreisBis) return $"Preis über {r.PreisBis:N0} €";
            if (r.PreisVon > 0 && preis < r.PreisVon) return $"Preis unter {r.PreisVon:N0} €";
        }
        if (r.KmBis > 0 && z.Km is { } km && km > r.KmBis) return $"über {r.KmBis:N0} km";
        if (r.JahrAb > 0 && z.Jahr is { } jahr && jahr < r.JahrAb) return $"Erstzulassung vor {r.JahrAb}";
        return null;
    }
}
