using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>Kleine Helfer fuer das Lesen (Normalform, kW/PS, Lesefehler-Abstand). Getriebe-/Kraftstoff-Codes und
/// alles Portal-Wissen stehen seit 1.4.0 nur noch auf dem Server (backend/fahrzeug_codes.py).</summary>
internal static class FahrzeugCodes
{
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
