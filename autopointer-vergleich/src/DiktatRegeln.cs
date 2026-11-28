using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>
/// Regeln fuer das Sprachdiktat (Wunsch Ahmad 24.11.2026), ohne KI: Sprachbefehle ("Komma", "Punkt",
/// "neue Zeile" …) werden Zeichen, Fachwoerter richtig geschrieben (AHK, TÜV, HU …).
/// Dieselben Regeln stehen in frontend/src/lib/diktatRegeln.js — bei Aenderungen beide anpassen.
/// Am PC wird ein Zeilenumbruch beim Eintippen zum Leerzeichen (Diktat.FuerEingabe).
/// </summary>
internal static class DiktatRegeln
{
    // Reihenfolge wichtig: "Doppelpunkt" vor "Punkt".
    private static readonly (string Muster, string Zeichen)[] Befehle =
    {
        (@"(neue|nächste|naechste)\s+zeile|neuer\s+absatz|absatz", "\n"),
        ("doppelpunkt", ":"),
        ("fragezeichen", "?"),
        ("ausrufezeichen", "!"),
        ("bindestrich", "-"),
        ("komma", ","),
        ("punkt", "."),
    };

    private static readonly (Regex Muster, string Ersatz)[] Befehlsregeln = Befehle
        .Select(b => (new Regex(@"[\s,.;:]*(?<![\p{L}\p{N}-])(?:" + b.Muster + @")(?![\p{L}\p{N}])[\s,.;:!?]*",
                                RegexOptions.IgnoreCase | RegexOptions.CultureInvariant),
                      b.Zeichen is "\n" or "-" ? b.Zeichen : b.Zeichen + " "))
        .ToArray();

    public static string Sprachbefehle(string? text)
    {
        string t = text ?? "";
        foreach (var (muster, ersatz) in Befehlsregeln) t = muster.Replace(t, ersatz);
        return Aufraeumen(t);
    }

    private static string Aufraeumen(string t)
    {
        t = Regex.Replace(t, @"[ \t]+\n", "\n");
        t = Regex.Replace(t, @"\n[ \t]+", "\n");
        t = Regex.Replace(t, @"[ \t]+([,.:;!?])", "$1");
        t = Regex.Replace(t, @"([,.:;!?])(?=[,.:;!?])", "");
        t = Regex.Replace(t, @"[ \t]{2,}", " ");
        // Grossschreibung nach Satzende und am Zeilenanfang
        t = Regex.Replace(t, @"(^|[.!?]\s+|\n)(\p{Ll})", m => m.Groups[1].Value + m.Groups[2].Value.ToUpperInvariant());
        return t.Trim();
    }

    private static readonly (Regex Muster, string Ersatz)[] Fachwoerter =
    {
        (new Regex(@"\bA\.?\s?H\.?\s?K\b\.?"), "AHK"),
        (new Regex(@"\bT\.?\s?Ü\.?\s?V\b\.?"), "TÜV"),
        (new Regex(@"\bTüv\b", RegexOptions.IgnoreCase), "TÜV"),
        (new Regex(@"\bH(?:\.\s?U\.|\s?U(?![\p{L}\p{N}]))"), "HU"),
        (new Regex(@"\bA(?:\.\s?U\.|\s?U(?![\p{L}\p{N}]))"), "AU"),
        (new Regex(@"\bA\.?\s?B\.?\s?S\b"), "ABS"),
        (new Regex(@"\bE\.?\s?S\.?\s?P\b"), "ESP"),
        (new Regex(@"\bP\.?\s?S\b(?=[\s,.!?]|$)"), "PS"),
        // Whisper hoert "Scheckheft" oft als "Checkheft" (Messung 24.11.2026)
        (new Regex(@"\bs?checkheft\s*gepflegt\b", RegexOptions.IgnoreCase), "scheckheftgepflegt"),
        (new Regex(@"\bcheckheft\b", RegexOptions.IgnoreCase), "Scheckheft"),
        (new Regex(@"\bzahn\s+riemen\b", RegexOptions.IgnoreCase), "Zahnriemen"),
        (new Regex(@"\bsteuer\s+kette\b", RegexOptions.IgnoreCase), "Steuerkette"),
        (new Regex(@"\bstein\s+schlag\b", RegexOptions.IgnoreCase), "Steinschlag"),
        (new Regex(@"\bstoss\s*stange\b", RegexOptions.IgnoreCase), "Stoßstange"),
        (new Regex(@"\bkotflugel\b", RegexOptions.IgnoreCase), "Kotflügel"),
    };

    public static string FachwoerterSchreiben(string? text)
    {
        string t = text ?? "";
        foreach (var (muster, ersatz) in Fachwoerter) t = muster.Replace(t, ersatz);
        return t;
    }

    /// <summary>Was jedes Diktat durchlaeuft, bevor es eingetippt wird.</summary>
    public static string Nachbearbeiten(string? text) => Sprachbefehle(FachwoerterSchreiben(text));
}
