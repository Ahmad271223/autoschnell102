using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

internal enum Feld
{
    MarkeModell, Preis, Zustand, Kategorie, Erstzulassung, Kilometer, Leistung, Getriebe, Kraftstoff,
    Farbe, Herstellerfarbe, Klimatisierung, Interieur, Tueren, Umweltplakette, InseratId, Hubraum,
    Schadstoffklasse, Sitzplaetze, HashId,
}

/// <summary>Macht aus den erkannten Textzeilen der beiden Tabellen ein Fahrzeug.</summary>
/// <remarks>Die Tabelle "Technische Daten" hat links die Bezeichnung
/// ("Kilometerstand:"), rechts den Wert. Zeilen werden ueber die Hoehe
/// zusammengefuehrt, Bezeichnungen unscharf verglichen (die Texterkennung liest
/// z. B. "Kibmeterstand" oder "Getriebeatt").</remarks>
internal static class DetailLeser
{
    private static readonly (string Norm, Feld Feld)[] Bezeichnungen =
    {
        ("markemodell", Feld.MarkeModell), ("preis", Feld.Preis), ("zustand", Feld.Zustand),
        ("kategorie", Feld.Kategorie), ("erstzulassung", Feld.Erstzulassung),
        ("kilometerstand", Feld.Kilometer), ("leistung", Feld.Leistung),
        ("getriebeart", Feld.Getriebe), ("getriebe", Feld.Getriebe),
        ("kraftstoff", Feld.Kraftstoff), ("kraftstoffart", Feld.Kraftstoff),
        ("farbe", Feld.Farbe), ("herstellerfarbe", Feld.Herstellerfarbe),
        ("klimatisierung", Feld.Klimatisierung), ("interieur", Feld.Interieur),
        ("turen", Feld.Tueren), ("tueren", Feld.Tueren), ("umweltplakette", Feld.Umweltplakette),
        ("inseratid", Feld.InseratId), ("hubraum", Feld.Hubraum),
        ("schadstoffklasse", Feld.Schadstoffklasse), ("sitzplatze", Feld.Sitzplaetze),
        ("hashid", Feld.HashId),
    };

    public static Feld? BezeichnungErkennen(string text)
    {
        string n = FahrzeugCodes.Norm(text);
        if (n.Length < 3) return null;
        Feld? bestes = null;
        int besterAbstand = int.MaxValue;
        bool mehrdeutig = false;
        foreach (var (norm, feld) in Bezeichnungen)
        {
            int d = FahrzeugCodes.Abstand(n, norm);
            if (d > Math.Max(1, norm.Length / 4)) continue;
            if (d < besterAbstand)
            {
                besterAbstand = d;
                bestes = feld;
                mehrdeutig = false;
            }
            else if (d == besterAbstand && bestes != feld) mehrdeutig = true;
        }
        return mehrdeutig ? null : bestes;
    }

    /// <summary>Bezeichnung -> Wert aus der Tabelle "Technische Daten".</summary>
    public static Dictionary<Feld, string> Tabelle(IReadOnlyList<OcrZeile> zeilen)
    {
        var ergebnis = new Dictionary<Feld, string>();
        if (zeilen.Count == 0) return ergebnis;

        var reihen = InReihen(zeilen);
        // Spaltengrenze: wo in mehrteiligen Reihen der Wert beginnt.
        var wertStarts = reihen.Where(r => r.Count >= 2).Select(r => r[1].X).OrderBy(x => x).ToList();
        double grenze = wertStarts.Count > 0 ? wertStarts[wertStarts.Count / 2] - 8 : double.MaxValue;

        foreach (var reihe in reihen)
        {
            string bezeichnung, wert;
            if (reihe.Count >= 2 && reihe[0].X < grenze)
            {
                bezeichnung = reihe[0].Text;
                wert = string.Join(" ", reihe.Skip(1).Select(z => z.Text));
                int dp = bezeichnung.IndexOf(':');
                if (dp >= 0 && dp < bezeichnung.Length - 1)
                {
                    wert = bezeichnung[(dp + 1)..].Trim() + " " + wert;
                    bezeichnung = bezeichnung[..dp];
                }
            }
            else
            {
                string text = string.Join(" ", reihe.Select(z => z.Text));
                int dp = text.IndexOf(':');
                if (dp <= 0 || reihe[0].X >= grenze) continue;   // nur Wert oder nur Bezeichnung
                bezeichnung = text[..dp];
                wert = text[(dp + 1)..];
            }
            wert = wert.Trim();
            if (wert.Length == 0) continue;
            var feld = BezeichnungErkennen(bezeichnung.TrimEnd(':', ' '));
            if (feld != null && !ergebnis.ContainsKey(feld.Value)) ergebnis[feld.Value] = wert;
        }
        return ergebnis;
    }

    private static List<List<OcrZeile>> InReihen(IReadOnlyList<OcrZeile> zeilen)
    {
        var hoehen = zeilen.Select(z => z.Hoehe).OrderBy(h => h).ToList();
        double mh = Math.Max(4, hoehen[hoehen.Count / 2]);
        var reihen = new List<(double MitteY, List<OcrZeile> Zeilen)>();
        foreach (var z in zeilen.OrderBy(z => z.MitteY))
        {
            int i = reihen.FindIndex(r => Math.Abs(r.MitteY - z.MitteY) < mh * 0.6);
            if (i < 0) reihen.Add((z.MitteY, new List<OcrZeile> { z }));
            else reihen[i].Zeilen.Add(z);
        }
        return reihen.OrderBy(r => r.MitteY).Select(r => r.Zeilen.OrderBy(z => z.X).ToList()).ToList();
    }

    // ---- Werte --------------------------------------------------------------

    private static readonly Regex EinheitAbtrennen = new(@"(\d)(km|kw|kW|KW|PS|ps|EUR|€)", RegexOptions.Compiled);

    private static string Zahlentext(string s) => FahrzeugCodes.ZiffernReparieren(EinheitAbtrennen.Replace(s, "$1 $2"));

    public static (int? Monat, int? Jahr) Erstzulassung(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return (null, null);
        string t = Zahlentext(s);
        int maxJahr = DateTime.Now.Year + 1;
        var m = Regex.Match(t, @"(\d{1,2})\s*[/.\-]\s*((?:19|20)\d{2})");
        if (m.Success)
        {
            int monat = int.Parse(m.Groups[1].Value), jahr = int.Parse(m.Groups[2].Value);
            if (jahr >= 1950 && jahr <= maxJahr) return (monat is >= 1 and <= 12 ? monat : null, jahr);
        }
        m = Regex.Match(t, @"(?<!\d)((?:19|20)\d{2})(?!\d)");
        if (m.Success)
        {
            int jahr = int.Parse(m.Groups[1].Value);
            if (jahr >= 1950 && jahr <= maxJahr) return (null, jahr);
        }
        return (null, null);
    }

    public static int? Kilometer(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        var m = Regex.Match(Zahlentext(s), @"\d{1,3}(?:[.,' ]\d{3})+|\d+");
        if (!m.Success) return null;
        string ziffern = new(m.Value.Where(char.IsDigit).ToArray());
        if (ziffern.Length == 0 || ziffern.Length > 7) return null;
        int km = int.Parse(ziffern);
        return km <= 2_500_000 ? km : null;
    }

    public static (int? Kw, int? Ps) Leistung(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return (null, null);
        string t = Zahlentext(s);
        int? kw = null, ps = null;
        var mk = Regex.Match(t, @"(\d{1,4})\s*k\s*[wWvV]", RegexOptions.IgnoreCase);
        if (mk.Success) kw = int.Parse(mk.Groups[1].Value);
        var mp = Regex.Match(t, @"(\d{1,4})\s*P\s*[S5]", RegexOptions.IgnoreCase);
        if (mp.Success) ps = int.Parse(mp.Groups[1].Value);
        if (kw == null && ps == null)
        {
            // Einheiten nicht lesbar: AutoPointer schreibt "X kW (Y PS)"
            var zahlen = Regex.Matches(t, @"\d{2,4}").Select(x => int.Parse(x.Value)).ToList();
            if (zahlen.Count >= 2) { kw = zahlen[0]; ps = zahlen[1]; }
            else if (zahlen.Count == 1) kw = zahlen[0];
        }
        if (kw != null && ps != null && Math.Abs(FahrzeugCodes.KwZuPs(kw.Value) - ps.Value) > 3)
        {
            // Widerspruch -> der Wert, der zum anderen passt, gewinnt; sonst kW.
            if (Math.Abs(FahrzeugCodes.PsZuKw(ps.Value) - kw.Value) > 3) ps = FahrzeugCodes.KwZuPs(kw.Value);
        }
        kw ??= ps != null ? FahrzeugCodes.PsZuKw(ps.Value) : null;
        ps ??= kw != null ? FahrzeugCodes.KwZuPs(kw.Value) : null;
        if (kw is < 5 or > 1500) return (null, null);
        return (kw, ps);
    }

    public static int? Betrag(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        var m = Regex.Match(Zahlentext(s), @"\d{1,3}(?:[.' ]\d{3})+|\d+");
        if (!m.Success) return null;
        string ziffern = new(m.Value.Where(char.IsDigit).ToArray());
        return ziffern.Length is > 0 and <= 9 ? int.Parse(ziffern) : null;
    }

    private static readonly Regex Uuid = new(
        @"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", RegexOptions.Compiled);

    /// <summary>Hash-ID (AutoScout-Kennung, 36 Zeichen). Nur vollstaendig: zeigt AutoPointer sie in
    /// schmaler Ansicht abgeschnitten ("9bcc72cb-…-0d..."), gibt es keine — dann muss der Sucher den
    /// Link selbst kopieren. Typische Lesefehler (O/o statt 0, l/I statt 1) werden repariert.</summary>
    public static string? HashId(string? s)
    {
        if (string.IsNullOrWhiteSpace(s) || s.Contains("...") || s.Contains('…')) return null;
        var sb = new System.Text.StringBuilder();
        foreach (char c in s)
        {
            if (char.IsWhiteSpace(c)) continue;
            sb.Append(c switch
            {
                'O' or 'o' => '0',
                'l' or 'I' or '|' => '1',
                '–' or '—' => '-',
                _ => char.ToLowerInvariant(c),
            });
        }
        string h = sb.ToString();
        return Uuid.IsMatch(h) ? h : null;
    }

    private static string? Sauber(string? s)
    {
        if (string.IsNullOrWhiteSpace(s)) return null;
        s = Regex.Replace(s, @"\s+", " ").Trim().TrimEnd('.', '…').Trim();
        return s.Length > 0 ? s : null;
    }

    // ---- Kopf: Quelle, Titel, Preis ------------------------------------------

    public static (string? Quelle, string? Titel, int? Preis) Kopf(IReadOnlyList<OcrZeile> zeilen, double breite)
    {
        string? quelle = null, titel = null;
        int? preis = null;
        double quelleY = double.MinValue;
        var sortiert = zeilen.OrderBy(z => z.Y).ToList();
        foreach (var z in sortiert)
        {
            var m = Regex.Match(z.Text, @"[Il1]nserat\s+v[o0]n\s+(.+)$", RegexOptions.IgnoreCase);
            if (!m.Success) continue;
            quelle = QuelleName(m.Groups[1].Value);
            quelleY = z.Y + z.Hoehe / 2;
            break;
        }
        foreach (var z in sortiert)
        {
            if (z.MitteY <= quelleY) continue;
            if (breite > 0 && z.X > breite * 0.7) continue;          // Portal-Logo rechts
            string t = z.Text.Trim();
            if (Regex.IsMatch(t, @"^[\d.,' ]+\s*(EUR|€)\s*$", RegexOptions.IgnoreCase))
            {
                preis ??= Betrag(t);
                continue;
            }
            if (t.Length < 4 || FahrzeugCodes.Norm(t) == "neu") continue;
            titel ??= Sauber(t);
        }
        return (quelle, titel, preis);
    }

    private static string QuelleName(string roh)
    {
        string n = FahrzeugCodes.Norm(roh);
        if (n.Contains("mobi")) return "mobile.de";          // auch Lesefehler wie "Mobie.de"
        if (n.Contains("scout")) return "AutoScout24";
        if (n.Contains("anzeigen") || n.StartsWith("klein") || n.StartsWith("kkin")) return "Kleinanzeigen";
        return roh.Trim();
    }

    // ---- alles zusammen ------------------------------------------------------

    public static Fahrzeug Auswerten(IReadOnlyList<OcrZeile> technik, IReadOnlyList<OcrZeile> kopf, double kopfBreite)
    {
        var tab = Tabelle(technik);
        var f = new Fahrzeug
        {
            MarkeModellText = Sauber(tab.GetValueOrDefault(Feld.MarkeModell)) ?? "",
            Zustand = Sauber(tab.GetValueOrDefault(Feld.Zustand)),
            Kategorie = Sauber(tab.GetValueOrDefault(Feld.Kategorie)),
            Kilometer = Kilometer(tab.GetValueOrDefault(Feld.Kilometer)),
            Getriebe = Sauber(tab.GetValueOrDefault(Feld.Getriebe)),
            Kraftstoff = Sauber(tab.GetValueOrDefault(Feld.Kraftstoff)),
            Tueren = Sauber(tab.GetValueOrDefault(Feld.Tueren)),
            Preis = Betrag(tab.GetValueOrDefault(Feld.Preis)),
        };
        (f.EzMonat, f.EzJahr) = Erstzulassung(tab.GetValueOrDefault(Feld.Erstzulassung));
        (f.Kw, f.Ps) = Leistung(tab.GetValueOrDefault(Feld.Leistung));
        if (tab.TryGetValue(Feld.InseratId, out var id))
        {
            string sauber = Regex.Replace(id, @"[^A-Za-z0-9\-]", "");
            f.InseratId = sauber.Length > 0 ? sauber : null;
        }
        f.HashId = HashId(tab.GetValueOrDefault(Feld.HashId));
        var (quelle, titel, preis) = Kopf(kopf, kopfBreite);
        f.Quelle = quelle;
        f.Titel = titel;
        f.Preis ??= preis;
        return f;
    }

    /// <summary>Fuehrt zwei Lesungen zusammen: fehlende Felder der ersten werden
    /// aus der zweiten ergaenzt (zweiter Durchlauf mit anderem Zoom).</summary>
    public static Fahrzeug Ergaenzen(Fahrzeug a, Fahrzeug b)
    {
        if (a.MarkeModellText.Length == 0) a.MarkeModellText = b.MarkeModellText;
        if (a.EzJahr == null) { a.EzJahr = b.EzJahr; a.EzMonat = b.EzMonat; }
        a.Kilometer ??= b.Kilometer;
        if (a.Kw == null) { a.Kw = b.Kw; a.Ps = b.Ps; }
        a.Kraftstoff ??= b.Kraftstoff;
        a.Getriebe ??= b.Getriebe;
        a.Zustand ??= b.Zustand;
        a.Kategorie ??= b.Kategorie;
        a.Tueren ??= b.Tueren;
        a.Preis ??= b.Preis;
        a.InseratId ??= b.InseratId;
        // Hash-ID bewusst NICHT ergaenzen: sie gilt nur, wenn beide Durchlaeufe dasselbe lesen
        // (AutoPointerQuelle.LiesBilderAsync) — lieber kein Link als ein falscher.
        a.Quelle ??= b.Quelle;
        a.Titel ??= b.Titel;
        return a;
    }

    /// <summary>Was fehlt, damit ein Vergleich Sinn ergibt (Marke/Modell + EZ + km)?</summary>
    public static List<string> Fehlend(Fahrzeug f)
    {
        var fehlt = new List<string>();
        if (f.MarkeModellText.Length == 0) fehlt.Add("Marke/Modell");
        if (f.EzJahr == null) fehlt.Add("Erstzulassung");
        if (f.Kilometer == null) fehlt.Add("Kilometerstand");
        return fehlt;
    }

    /// <summary>Optionale Felder, die fuer einen genauen Vergleich fehlen.</summary>
    public static List<string> Unvollstaendig(Fahrzeug f)
    {
        var fehlt = new List<string>();
        if (f.Kw == null) fehlt.Add("Leistung");
        if (f.Kraftstoff == null) fehlt.Add("Kraftstoff");
        if (f.Getriebe == null) fehlt.Add("Getriebe");
        return fehlt;
    }
}
