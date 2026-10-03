using System.Globalization;

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

/// <summary>Baut die Suchlinks. Parameter wie in backend/mobile_service.build_search_url
/// und backend/autoscout_service.build_search_url (kompakte mobile.de-Parameter
/// ms/fr/ml/pw/ft/tr; AutoScout cat=ma..mo.., fregfrom ... ).</summary>
internal static class LinkBauer
{
    public static Zuordnung Zuordnen(Fahrzeug f, Katalog k)
    {
        var teile = k.TeileMarkeModell(f.MarkeModellText);
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
        return z;
    }

    public static List<Vergleich> Bauen(Fahrzeug f, Zuordnung z, Einstellungen e, List<string> hinweise)
    {
        var links = new List<Vergleich>();
        if (!z.MarkeErkannt)
        {
            hinweise.Add($"Marke in „{f.MarkeModellText}“ nicht erkannt – kein Vergleich.");
            return links;
        }
        if (e.MobileDe)
        {
            string? url = Mobile(f, z, e, hinweise);
            if (url != null) links.Add(new Vergleich("mobile.de", url));
        }
        if (e.AutoScout24)
        {
            string? url = AutoScout(f, z, e, hinweise);
            if (url != null) links.Add(new Vergleich("AutoScout24", url));
        }
        FilterHinweise(f, e, hinweise);
        return links;
    }

    // ---- Bereiche ------------------------------------------------------------

    internal static (int? Von, int? Bis) KmGrenzen(int km, Einstellungen e)
    {
        switch (e.KmModus)
        {
            case KmModus.Bereich:
                int von = Math.Max(0, km - e.KmSpanne), bis = km + e.KmSpanne;
                if (e.KmRunden && e.KmSpanne >= 5000)
                {
                    int rv = (int)Math.Round(von / 5000.0, MidpointRounding.AwayFromZero) * 5000;
                    int rb = (int)Math.Round(bis / 5000.0, MidpointRounding.AwayFromZero) * 5000;
                    von = rv <= km ? rv : von / 5000 * 5000;
                    bis = rb >= km ? rb : (bis + 4999) / 5000 * 5000;
                }
                return (von, bis);
            case KmModus.BisPlus:
                return (null, km + e.KmSpanne);
            default:
                return (null, null);
        }
    }

    internal static (int? Von, int? Bis) EzGrenzen(int jahr, Einstellungen e) => e.EzModus switch
    {
        EzModus.ExaktesJahr => (jahr, jahr),
        EzModus.PlusMinus => (jahr - e.EzJahre, jahr + e.EzJahre),
        EzModus.AbJahr => (jahr - e.EzJahre, null),
        _ => (null, null),
    };

    /// <summary>Leistung in kW wie im Backend: tolerance_ps (± PS) bzw. min_ps (ab −PS,
    /// nach oben offen: Bis = null).</summary>
    internal static (int Von, int? Bis)? KwGrenzen(Fahrzeug f, Einstellungen e)
    {
        if (e.LeistungModus == LeistungModus.Aus || f.Kw == null) return null;
        int ps = f.Ps ?? FahrzeugCodes.KwZuPs(f.Kw.Value);
        int tol = e.LeistungTolerantPs;
        if (e.LeistungModus == LeistungModus.AbMinus)
            return (tol == 0 ? f.Kw.Value : FahrzeugCodes.PsZuKw(Math.Max(1, ps - tol)), null);
        if (tol == 0) return (f.Kw.Value, f.Kw.Value);
        return (FahrzeugCodes.PsZuKw(Math.Max(1, ps - tol)), FahrzeugCodes.PsZuKw(ps + tol));
    }

    private static string S(int? v) => v?.ToString(CultureInfo.InvariantCulture) ?? "";

    // ---- mobile.de -----------------------------------------------------------

    private static string? Mobile(Fahrzeug f, Zuordnung z, Einstellungen e, List<string> hinweise)
    {
        if (z.MobileMarke == null)
        {
            hinweise.Add($"Marke „{z.MarkeText}“ gibt es im mobile.de-Katalog nicht – kein mobile.de-Vergleich.");
            return null;
        }
        if (z.MobileModell == null && !e.OhneModellNurMarke)
        {
            hinweise.Add($"Modell „{z.ModellText}“ im mobile.de-Katalog nicht gefunden – kein mobile.de-Vergleich (sonst würde nur nach „{z.MobileMarke.Name}“ gesucht).");
            return null;
        }
        if (z.MobileModell?.Unscharf == true)
            hinweise.Add($"mobile.de: „{z.ModellText}“ unscharf als „{z.MobileModell.Name}“ erkannt.");

        var p = new List<(string, string)>
        {
            ("isSearchRequest", "true"), ("ref", "quickSearch"), ("s", "Car"), ("vc", "Car"), ("pageNumber", "1"),
            ("ms", z.MobileModell != null ? $"{z.MobileMarke.Id};{z.MobileModell.Id};;;" : $"{z.MobileMarke.Id};;;;"),
        };
        if (f.EzJahr != null)
        {
            var (von, bis) = EzGrenzen(f.EzJahr.Value, e);
            if (von != null || bis != null) p.Add(("fr", $"{S(von)}:{S(bis)}"));
        }
        if (f.Kilometer != null)
        {
            var (von, bis) = KmGrenzen(f.Kilometer.Value, e);
            if (von != null || bis != null) p.Add(("ml", $"{S(von)}:{S(bis)}"));
        }
        if (KwGrenzen(f, e) is { } kw) p.Add(("pw", $"{kw.Von}:{S(kw.Bis)}"));
        if (e.KraftstoffFiltern && f.KraftstoffCode.Length > 0) p.Add(("ft", f.KraftstoffCode));
        if (e.GetriebeFiltern && f.GetriebeCode.Length > 0) p.Add(("tr", f.GetriebeCode));
        if (e.UnfallwagenAusblenden) p.Add(("dam", "0"));
        if (e.NurDeutschland) p.Add(("cn", "DE"));
        p.AddRange(e.Sortierung switch
        {
            Sortierung.KilometerAufsteigend => new[] { ("sb", "ml"), ("od", "up") },
            Sortierung.ErstzulassungAbsteigend => new[] { ("sb", "fr"), ("od", "down") },
            Sortierung.Relevanz => new[] { ("sb", "rel") },
            _ => new[] { ("sb", "p"), ("od", "up") },
        });
        return "https://suchen.mobile.de/fahrzeuge/search.html?" + string.Join("&",
            p.Select(x => $"{x.Item1}={Uri.EscapeDataString(x.Item2)}"));
    }

    // ---- AutoScout24 ---------------------------------------------------------

    private static string? AutoScout(Fahrzeug f, Zuordnung z, Einstellungen e, List<string> hinweise)
    {
        if (z.AutoScoutMarke == null)
        {
            hinweise.Add($"Marke „{z.MarkeText}“ gibt es im AutoScout24-Katalog nicht – kein AutoScout24-Vergleich.");
            return null;
        }
        if (z.AutoScoutModell == null && !e.OhneModellNurMarke)
        {
            hinweise.Add($"Modell „{z.ModellText}“ im AutoScout24-Katalog nicht gefunden – kein AutoScout24-Vergleich (sonst würde nur nach „{z.AutoScoutMarke.MakeName}“ gesucht).");
            return null;
        }
        if (z.AutoScoutModell?.Unscharf == true)
            hinweise.Add($"AutoScout24: „{z.ModellText}“ unscharf als „{z.AutoScoutModell.Name}“ erkannt.");

        var p = new List<(string, string)> { ("atype", "C") };
        if (e.NurDeutschland) p.Add(("cy", "D"));
        p.Add(("cat", z.AutoScoutModell != null
            ? $"ma{z.AutoScoutMarke.MakeId}mo{z.AutoScoutModell.Id}"
            : $"ma{z.AutoScoutMarke.MakeId}"));
        if (f.EzJahr != null)
        {
            var (von, bis) = EzGrenzen(f.EzJahr.Value, e);
            if (von != null) p.Add(("fregfrom", S(von)));
            if (bis != null) p.Add(("fregto", S(bis)));
        }
        if (f.Kilometer != null)
        {
            var (von, bis) = KmGrenzen(f.Kilometer.Value, e);
            if (von != null) p.Add(("kmfrom", S(von)));
            if (bis != null) p.Add(("kmto", S(bis)));
        }
        if (KwGrenzen(f, e) is { } kw)
        {
            p.Add(("powerfrom", S(kw.Von)));
            if (kw.Bis != null) p.Add(("powerto", S(kw.Bis)));
            p.Add(("powertype", "kw"));
        }
        if (e.KraftstoffFiltern && FahrzeugCodes.AutoScoutKraftstoff.TryGetValue(f.KraftstoffCode, out var fuel)) p.Add(("fuel", fuel));
        if (e.GetriebeFiltern && FahrzeugCodes.AutoScoutGetriebe.TryGetValue(f.GetriebeCode, out var gear)) p.Add(("gear", gear));
        if (e.UnfallwagenAusblenden) p.Add(("damaged_listing", "exclude"));
        p.Add(("ocs_listing", "include"));
        p.AddRange(e.Sortierung switch
        {
            Sortierung.KilometerAufsteigend => new[] { ("sort", "mileage"), ("desc", "0") },
            Sortierung.ErstzulassungAbsteigend => new[] { ("sort", "year"), ("desc", "1") },
            Sortierung.Relevanz => new[] { ("sort", "standard"), ("desc", "0") },
            _ => new[] { ("sort", "price"), ("desc", "0") },
        });
        p.Add(("ustate", "N,U"));
        return $"https://www.autoscout24.de/lst/{Katalog.Slug(z.AutoScoutMarke.MakeName)}?" + string.Join("&",
            p.Select(x => $"{x.Item1}={Uri.EscapeDataString(x.Item2).Replace("%2C", ",")}"));
    }

    /// <summary>Wie fahrzeug_codes.filter_hinweise: sagen, wenn ein gewuenschter
    /// Filter nicht in den Link kommt - nie still ohne Filter.</summary>
    private static void FilterHinweise(Fahrzeug f, Einstellungen e, List<string> hinweise)
    {
        if (e.GetriebeFiltern && f.GetriebeCode.Length == 0)
            hinweise.Add(f.Getriebe != null
                ? $"Getriebe „{f.Getriebe}“ nicht erkannt – Suche zeigt alle Getriebearten."
                : "Getriebe nicht gelesen – Suche zeigt alle Getriebearten.");
        if (e.KraftstoffFiltern)
        {
            if (f.KraftstoffCode.Length == 0)
                hinweise.Add(f.Kraftstoff != null
                    ? $"Kraftstoff „{f.Kraftstoff}“ nicht erkannt – Suche zeigt alle Kraftstoffarten."
                    : "Kraftstoff nicht gelesen – Suche zeigt alle Kraftstoffarten.");
            else if (e.AutoScout24 && !FahrzeugCodes.AutoScoutKraftstoff.ContainsKey(f.KraftstoffCode))
                hinweise.Add($"Kraftstoff „{f.Kraftstoff}“ filtert nur mobile.de – AutoScout24 zeigt alle Kraftstoffarten.");
        }
        if (e.LeistungModus != LeistungModus.Aus && f.Kw == null)
            hinweise.Add("Leistung nicht gelesen – Suche ohne Leistungsfilter.");
    }
}
