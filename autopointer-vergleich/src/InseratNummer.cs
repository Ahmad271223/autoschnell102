using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>Von welchem Portal das Inserat stammt — nur fuer die Stellenzahl der Inserat-Nummer. Wie
/// <c>backend/werkzeuge.quelle_code</c> (Systemcheck 12.10.2026: die Portal-Spalte ist in AutoPointer oft abgeschnitten,
/// "Mo...", "Klei...", "Aut..." — ein eindeutiger Anfang reicht).</summary>
internal enum Portal { Unbekannt, Mobile, Kleinanzeigen, AutoScout }

/// <summary>Ergebnis der Mehrfachlesung einer Kennung (Inserat-ID oder Hash-ID).</summary>
/// <param name="Wert">Hauptwert: die sichere Nummer, sonst die plausibelste Lesung; null = keine plausible Lesung.</param>
/// <param name="Sicher">Mindestens <see cref="InseratNummer.MindestGleich"/> Lesungen Zeichen fuer Zeichen gleich UND die
/// Stellenzahl passt zum Portal.</param>
/// <param name="Alternativen">Alle weiteren, voneinander verschiedenen Lesungen (ohne den Hauptwert), hoechstens
/// <see cref="ZweiterBlick.MaxJeFeld"/>.</param>
/// <param name="Lesungen">Wie viele brauchbare Lesungen es gab (0 = die Zeile wurde nie gelesen: "fehlt", nicht "unsicher").</param>
internal sealed record NummernLesung(string? Wert, bool Sicher, List<string> Alternativen, int Lesungen)
{
    /// <summary>Es gab Lesungen, aber keine sichere Nummer — der Server bekommt <c>inserat_id_unsicher: true</c>.</summary>
    public bool Unsicher => !Sicher && Lesungen > 0;
}

/// <summary>Wunsch Ahmad 13.10.2026 (1.5.16, Inserat-Nummer sicher lesen; Systemcheck 12.10.2026: beim Kunden fehlt die
/// 14-stellige mobile.de-Nummer sehr oft, und eine falsch gelesene Ziffer, die trotzdem eine Ziffer ist, fiel nicht auf —
/// fremdes Auto im Kaufvertrag). Die Nummer wird aus mehreren Aufbereitungen desselben Abbilds gelesen (erster Durchgang,
/// zweiter Durchlauf, drei Aufbereitungen des zweiten Blicks); sie gilt nur als SICHER, wenn mindestens zwei Lesungen
/// Zeichen fuer Zeichen gleich sind und die Stellenzahl zum Portal passt (B1/B2 aus <c>docs/inserat-id-sicher-lesen.md</c>).
/// Sonst geht die plausibelste Lesung als Hauptwert mit, alle anderen als Alternativen, und <c>fahrzeug.inserat_id_unsicher</c>
/// sagt dem Server, dass er die Nummer nicht als sicher behandeln darf. Alles hier ist rein (fuer Tests).</summary>
internal static class InseratNummer
{
    /// <summary>So viele Lesungen muessen Zeichen fuer Zeichen gleich sein.</summary>
    internal const int MindestGleich = 2;

    /// <summary>Stellenzahl je Portal (B1): mobile.de genau 9 (aeltere) oder 14 (aktuelle, z. B. 50342132635392), Kleinanzeigen
    /// 9–11; AutoScout kennt die Inserat-ID nicht (dort zaehlt die Hash-ID) — wie der Server 6–20; unbekanntes Portal
    /// (Quelle nicht gelesen): 9–14, was beide Portale abdeckt.</summary>
    internal static bool StellenzahlPasst(string nummer, Portal portal)
    {
        if (nummer.Length == 0 || !nummer.All(c => c is >= '0' and <= '9')) return false;
        int n = nummer.Length;
        return portal switch
        {
            Portal.Mobile => n is 9 or 14,
            Portal.Kleinanzeigen => n is >= 9 and <= 11,
            Portal.AutoScout => n is >= 6 and <= 20,
            _ => n is >= 9 and <= 14,
        };
    }

    /// <summary>Portal aus dem Quelle-Text, wie AutoPointer ihn zeigt (auch abgeschnitten: "Mo...", "Klei...", "Aut...").</summary>
    internal static Portal PortalVon(string? quelle)
    {
        string q = Regex.Replace((quelle ?? "").ToLowerInvariant(), "[^a-z0-9]", "");
        if (q.Length == 0) return Portal.Unbekannt;
        if (q.Contains("mobile") || q.StartsWith("mo")) return Portal.Mobile;
        if (q.Contains("anzeigen") || q.StartsWith("kl")) return Portal.Kleinanzeigen;
        if (q.Contains("scout") || q.StartsWith("au")) return Portal.AutoScout;
        return Portal.Unbekannt;
    }

    /// <summary>Eine Lesung der Zeile "Inserat-ID" normalisieren: nur die Zelle rechts der Beschriftung (nach dem letzten
    /// Doppelpunkt), Leerzeichen und Sonderzeichen raus (<see cref="DetailLeser.InseratIdSauber"/>), und die eindeutigen
    /// Verwechslungen der Texterkennung wie <c>backend/werkzeuge.inserat_nummer</c>: O/o→0, I/l/i/|→1, S/s→5, Z/z→2.
    /// Mehrdeutige Zeichen (B = 8 oder 3, G = 6 oder 9) bleiben stehen — so eine Lesung ist nie plausibel. null = leer.</summary>
    internal static string? Normalisieren(string? roh)
    {
        if (string.IsNullOrWhiteSpace(roh)) return null;
        int dp = roh.LastIndexOf(':');
        if (dp >= 0) roh = roh[(dp + 1)..];
        roh = roh.Replace('|', '1');                   // faellt sonst in InseratIdSauber weg
        string? sauber = DetailLeser.InseratIdSauber(roh);
        if (sauber == null) return null;
        var sb = new System.Text.StringBuilder(sauber.Length);
        foreach (char c in sauber)
            sb.Append(c switch
            {
                'O' or 'o' => '0',
                'I' or 'l' or 'i' => '1',
                'S' or 's' => '5',
                'Z' or 'z' => '2',
                _ => c,
            });
        return sb.ToString();
    }

    /// <summary>Mehrheit ueber alle Lesungen der Inserat-ID (B2). Sicher = eine normalisierte Lesung kommt mindestens
    /// <see cref="MindestGleich"/>-mal vor und ihre Stellenzahl passt (bei mehreren die haeufigste; Gleichstand zweier
    /// plausibler Lesungen ist seit 1.5.17 KEINE Mehrheit, sondern unsicher).
    /// Sonst Hauptwert = die erste plausible Lesung (Stellenzahl passt) oder null; alle anderen verschiedenen Lesungen
    /// werden Alternativen (Reihenfolge wie gelesen).</summary>
    internal static NummernLesung Mehrheit(IEnumerable<string?> lesungen, Portal portal) =>
        Mehrheit(lesungen.Select(Normalisieren), n => StellenzahlPasst(n, portal));

    /// <summary>Dieselbe Mehrheit fuer die Hash-ID (AutoScout, UUID): normalisiert wie <see cref="DetailLeser.HashId"/>
    /// (O→0, l/I/|→1, klein, ohne Leerzeichen) — nur vollstaendige UUIDs sind plausibel. Auch hier gilt: lieber keine
    /// Hash-ID als eine falsche, deshalb nimmt <see cref="Uebernehmen"/> nur eine SICHERE als Hauptwert.</summary>
    internal static NummernLesung HashMehrheit(IEnumerable<string?> lesungen) =>
        Mehrheit(lesungen.Select(HashNormalisieren), n => DetailLeser.HashId(n) != null);

    /// <summary>Eine Hash-Lesung: nur die Zelle rechts der Beschriftung; vollstaendig -> wie <see cref="DetailLeser.HashId"/>,
    /// sonst ("9bcc72cb-…", abgeschnitten) ohne Leerzeichen und klein, damit sie als Alternative lesbar bleibt.</summary>
    internal static string? HashNormalisieren(string? roh)
    {
        if (string.IsNullOrWhiteSpace(roh)) return null;
        int dp = roh.LastIndexOf(':');
        if (dp >= 0) roh = roh[(dp + 1)..];
        if (DetailLeser.HashId(roh) is { } uuid) return uuid;
        string s = new(roh.Where(c => !char.IsWhiteSpace(c)).ToArray());
        return s.Length > 0 ? s.ToLowerInvariant() : null;
    }

    private static NummernLesung Mehrheit(IEnumerable<string?> normalisiert, Func<string, bool> plausibel)
    {
        var werte = normalisiert.OfType<string>().Where(s => s.Length > 0).ToList();
        if (werte.Count == 0) return new NummernLesung(null, false, new List<string>(), 0);
        var kandidaten = werte.GroupBy(s => s, StringComparer.Ordinal)
            .Select(g => (Wert: g.Key, Anzahl: g.Count(), Erste: werte.IndexOf(g.Key)))
            .Where(g => g.Anzahl >= MindestGleich && plausibel(g.Wert))
            .OrderByDescending(g => g.Anzahl).ThenBy(g => g.Erste)
            .ToList();
        // 1.5.17 (Pruefung 23.10.2026, externer Bericht zu 1.5.16): zwei plausible Lesungen mit gleich vielen Treffern
        // (A, B, A, B) sind KEINE Mehrheit — vorher gewann die fruehere als "sicher". Jetzt: unsicher, Hauptwert = erste
        // plausible Lesung, die andere Alternative; der Server prueft das Inserat zur Nummer gegen den Bildschirm.
        string? sicher = kandidaten.Count > 0 && (kandidaten.Count == 1 || kandidaten[0].Anzahl > kandidaten[1].Anzahl)
            ? kandidaten[0].Wert : null;
        string? wert = sicher ?? werte.FirstOrDefault(plausibel);
        var alternativen = werte.Distinct(StringComparer.Ordinal)
            .Where(w => !string.Equals(w, wert, StringComparison.Ordinal)).Take(ZweiterBlick.MaxJeFeld).ToList();
        return new NummernLesung(wert, sicher != null, alternativen, werte.Count);
    }

    /// <summary>Alle Lesungen ins Fahrzeug: Inserat-ID (Hauptwert, Alternativen) und Hash-ID (nur sicher, sonst null —
    /// wie bisher "nur wenn zweimal gleich gelesen"), dazu <see cref="Fahrzeug.InseratIdUnsicher"/>. Bei AutoScout
    /// entscheidet die Hash-ID ueber "unsicher" (die Inserat-ID zaehlt dort nicht fuer den Link), sonst die Inserat-ID.
    /// <paramref name="idLesungen"/>/<paramref name="hashLesungen"/> sind die Lesungen des zweiten Blicks; die der
    /// Durchgaenge stehen schon in <see cref="Fahrzeug.InseratIdLesungen"/>/<see cref="Fahrzeug.HashIdLesungen"/>.</summary>
    internal static void Uebernehmen(Fahrzeug f, IEnumerable<string?> idLesungen, IEnumerable<string?> hashLesungen)
    {
        var portal = PortalVon(f.Quelle);
        var id = Mehrheit(f.InseratIdLesungen.Concat(idLesungen), portal);
        var hash = HashMehrheit(f.HashIdLesungen.Concat(hashLesungen));
        f.InseratId = id.Wert;
        f.AlternativenInseratId = id.Alternativen;
        f.HashId = hash.Sicher ? hash.Wert : null;
        bool hashZaehlt = portal == Portal.AutoScout || (portal == Portal.Unbekannt && hash.Lesungen > 0);
        f.InseratIdUnsicher = hashZaehlt ? hash.Unsicher : id.Unsicher;
    }
}
