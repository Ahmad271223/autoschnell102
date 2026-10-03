using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>Getriebe-/Kraftstoff-Codes fuer beide Portale - 1:1 nach
/// backend/fahrzeug_codes.py (Befund 17.09.2026: nie Rohwerte in Filter-Links,
/// mobile.de und AutoScout24 ignorieren unbekannte Werte still).</summary>
internal static class FahrzeugCodes
{
    public static readonly IReadOnlyDictionary<string, string> AutoScoutGetriebe = new Dictionary<string, string>
    {
        ["AUTOMATIC_GEAR"] = "A", ["MANUAL_GEAR"] = "M", ["SEMIAUTOMATIC_GEAR"] = "S",
    };

    public static readonly IReadOnlyDictionary<string, string> AutoScoutKraftstoff = new Dictionary<string, string>
    {
        ["PETROL"] = "B", ["DIESEL"] = "D", ["ELECTRICITY"] = "E", ["HYBRID"] = "2",
        ["HYBRID_DIESEL"] = "3", ["LPG"] = "L", ["CNG"] = "C", ["HYDROGENIUM"] = "H",
    };

    private static readonly string[] KraftstoffCodes =
        { "PETROL", "DIESEL", "ELECTRICITY", "HYBRID", "HYBRID_DIESEL", "LPG", "CNG", "HYDROGENIUM", "ETHANOL", "OTHER" };

    /// <summary>Kleinbuchstaben ohne Akzente, nur a-z und 0-9 (wie _norm im Backend).</summary>
    public static string Norm(string? wert)
    {
        if (string.IsNullOrEmpty(wert)) return "";
        var sb = new StringBuilder(wert.Length);
        foreach (char c in wert.Normalize(NormalizationForm.FormKD))
        {
            if (CharUnicodeInfo.GetUnicodeCategory(c) == UnicodeCategory.NonSpacingMark) continue;
            char k = char.ToLowerInvariant(c);
            if (k is >= 'a' and <= 'z' or >= '0' and <= '9') sb.Append(k);
        }
        return sb.ToString();
    }

    public static string? GetriebeCode(string? wert)
    {
        string n = Norm(wert);
        if (n.Length == 0) return null;
        // Reihenfolge wichtig: "Halbautomatik" enthaelt "auto".
        if (n.Contains("halbauto") || n.StartsWith("semi")) return "SEMIAUTOMATIC_GEAR";
        if (n.Contains("auto") || n.Contains("doppelkupplung") || n.Contains("dsg") || n.Contains("cvt")
            || n.Contains("stufenlos") || n.Contains("tiptronic")) return "AUTOMATIC_GEAR";
        if (n.Contains("schalt") || n.Contains("manu")) return "MANUAL_GEAR";
        return null;
    }

    public static string? KraftstoffCode(string? wert)
    {
        string roh = (wert ?? "").Trim();
        if (KraftstoffCodes.Contains(roh.ToUpperInvariant())) return roh.ToUpperInvariant();
        string n = Norm(roh);
        if (n.Length == 0) return null;
        bool elektrisch = n.Contains("elektr") || n.Contains("electr") || n.Contains("strom");
        bool verbrenner = n.Contains("benzin") || n.Contains("petrol") || n.Contains("gasoline") || n.Contains("diesel");
        // Reihenfolge wichtig: "Hybrid (Diesel/Elektro)" enthaelt "diesel" und "elektro".
        if (n.Contains("hybrid") || n.Contains("plugin") || (elektrisch && verbrenner))
            return n.Contains("diesel") ? "HYBRID_DIESEL" : "HYBRID";
        if (n.Contains("wasserstoff") || n.Contains("hydrogen")) return "HYDROGENIUM";
        if (n.Contains("lpg") || n.Contains("autogas") || n.Contains("flussiggas")) return "LPG";
        if (n.Contains("cng") || n.Contains("erdgas") || n.Contains("naturalgas")) return "CNG";
        if (n.Contains("ethanol") || n.Contains("e85")) return "ETHANOL";
        if (elektrisch) return "ELECTRICITY";
        if (n.Contains("diesel")) return "DIESEL";
        if (n.Contains("benzin") || n.Contains("petrol") || n.Contains("gasoline") || n.StartsWith("super")) return "PETROL";
        if (n is "andere" or "sonstige" or "sonstiges" or "other" or "others") return "OTHER";
        return null;
    }

    // Umrechnung wie autoscout_service.kw_to_ps / ps_to_kw
    public static int KwZuPs(int kw) => (int)Math.Round(kw * 1.359621617, MidpointRounding.ToEven);
    public static int PsZuKw(int ps) => (int)Math.Round(ps / 1.359621617, MidpointRounding.ToEven);

    /// <summary>Levenshtein-Abstand - fuer Lesefehler der Texterkennung.</summary>
    public static int Abstand(string a, string b)
    {
        if (a.Length == 0) return b.Length;
        if (b.Length == 0) return a.Length;
        var vorher = new int[b.Length + 1];
        var jetzt = new int[b.Length + 1];
        for (int j = 0; j <= b.Length; j++) vorher[j] = j;
        for (int i = 1; i <= a.Length; i++)
        {
            jetzt[0] = i;
            for (int j = 1; j <= b.Length; j++)
            {
                int kosten = a[i - 1] == b[j - 1] ? 0 : 1;
                jetzt[j] = Math.Min(Math.Min(jetzt[j - 1] + 1, vorher[j] + 1), vorher[j - 1] + kosten);
            }
            (vorher, jetzt) = (jetzt, vorher);
        }
        return vorher[b.Length];
    }

    private static readonly Regex Token = new(@"\S+", RegexOptions.Compiled);

    /// <summary>Typische Verwechslungen der Texterkennung in Zahlen reparieren
    /// ("1O/2006" -> "10/2006", "l25 kW" -> "125 kW") - nur in Woertern, die
    /// ueberwiegend aus Ziffern bestehen.</summary>
    public static string ZiffernReparieren(string text) => Token.Replace(text, m =>
    {
        string t = m.Value;
        int ziffern = t.Count(char.IsDigit);
        int buchstaben = t.Count(char.IsLetter);
        if (ziffern == 0 || ziffern < buchstaben * 2 || t.Length < 2) return t;
        var sb = new StringBuilder(t.Length);
        foreach (char c in t)
            sb.Append(c switch
            {
                'O' or 'o' or 'D' => '0',
                'l' or 'I' or '|' or '!' => '1',
                'S' or 's' => '5',
                'B' => '8',
                _ => c,
            });
        return sb.ToString();
    });
}
