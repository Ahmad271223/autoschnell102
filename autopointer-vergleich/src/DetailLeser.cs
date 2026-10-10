using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

internal enum Feld
{
    MarkeModell, Preis, Zustand, Kategorie, Erstzulassung, Kilometer, Leistung, Getriebe, Kraftstoff,
    Farbe, Herstellerfarbe, Klimatisierung, Interieur, Tueren, Umweltplakette, InseratId, Hubraum,
    Schadstoffklasse, Sitzplaetze, HashId,
    /// <summary>Wunsch Ahmad 09.10.2026 (1.5.14, Elektroautos): Zeilen "Antrieb", "Antriebsart", "Motor", "Energie",
    /// "Energieträger", "Elektroantrieb" — nur ein Rueckfall fuer den Kraftstoff, wenn die Zeile "Kraftstoff" fehlt
    /// (<see cref="DetailLeser.KraftstoffAusAntrieb"/>): "Antrieb: Allrad" wird nie Kraftstoff.</summary>
    Antrieb,
}

/// <summary>1.5.12: eine erkannte Zeile der Tabelle "Technische Daten" mit der Lage ihres Werts im Bild (Pixel im
/// unskalierten Abbild wie <see cref="OcrZeile"/>).</summary>
/// <param name="Wert">gelesener Wert (getrimmt, sonst roh), "" = keiner gelesen.</param>
/// <param name="WertX">wo der gelesene Wert beginnt — geschaetzt, wenn Bezeichnung und Wert als ein Stueck gelesen wurden.</param>
/// <param name="Spalte">wo die Wertspalte beginnt (aus allen Reihen, die Werte stehen linksbuendig); null = unbekannt.</param>
/// <param name="LinksMin">rechtes Ende der Bezeichnung, wenn sie ein eigenes Stueck ist (sonst 0).</param>
/// <param name="Rechts">rechtes Ende des gelesenen Werts; null = kein Wert gelesen.</param>
/// <param name="VorherUnten">Unterkante der Reihe darueber (null = erste Reihe).</param>
/// <param name="DanachOben">Oberkante der Reihe darunter (null = letzte Reihe).</param>
/// <param name="Bezeichnung">1.5.14: die gelesene Bezeichnung (ohne Doppelpunkt) — fuer <see cref="Feld.Antrieb"/>
/// entscheidet sie mit ("Elektroantrieb").</param>
internal sealed record TabellenZeile(Feld Feld, string Wert, double WertX, double? Spalte, double LinksMin,
                                     double? Rechts, double Oben, double Unten, double? VorherUnten, double? DanachOben,
                                     string Bezeichnung = "");

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
        // 1.5.14 (Wunsch Ahmad 09.10.2026, Opel Mokka-e ohne Kraftstoff): der Antrieb unter anderer Beschriftung
        ("antrieb", Feld.Antrieb), ("antriebsart", Feld.Antrieb), ("motor", Feld.Antrieb), ("energie", Feld.Antrieb),
        ("energietrager", Feld.Antrieb), ("elektroantrieb", Feld.Antrieb),
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

    /// <summary>Bezeichnung -> Wert aus der Tabelle "Technische Daten". Fuer <see cref="Feld.Antrieb"/> steht hier schon
    /// der daraus abgeleitete Kraftstoff (<see cref="KraftstoffAusAntrieb"/>) — oder nichts.</summary>
    public static Dictionary<Feld, string> Tabelle(IReadOnlyList<OcrZeile> zeilen)
    {
        var ergebnis = new Dictionary<Feld, string>();
        foreach (var z in Zeilen(zeilen))
        {
            if (z.Wert.Length == 0 || ergebnis.ContainsKey(z.Feld)) continue;
            string? wert = z.Feld == Feld.Antrieb ? KraftstoffAusAntrieb(z.Bezeichnung, z.Wert) : z.Wert;
            if (!string.IsNullOrEmpty(wert)) ergebnis[z.Feld] = wert;
        }
        return ergebnis;
    }

    // ---- Wunsch Ahmad 09.10.2026 (1.5.14): "Elektroantrieb — Elektro ist Kraftstoff Elektro, nicht 'nicht erkannt'" ----

    /// <summary>Kraftstoff-Woerter, die in einer Antriebs-Zeile zaehlen (Teilwort, in Leseform — "EIektro"/"E1ektro"
    /// wie "Elektro").</summary>
    private static readonly string[] KraftstoffWoerter =
        { "elektr", "strom", "benzin", "diesel", "hybrid", "gas", "lpg", "cng", "wasserstoff" };

    /// <summary>Leseform wie <see cref="Fahrzeug.Schluessel"/>: i/l/1 und o/0 gleich (typische Verwechslungen).</summary>
    private static string Leseform(string norm) =>
        new(norm.Select(c => c switch { 'i' or 'l' => '1', 'o' => '0', _ => c }).ToArray());

    /// <summary>Enthaelt der Wert ein Kraftstoff-Wort (auch in Leseform)? "Allrad", "Front", "1.6 TSI" nicht. (rein, fuer Tests)</summary>
    internal static bool IstKraftstoffWort(string? wert)
    {
        string lese = Leseform(FahrzeugCodes.Norm(wert));
        return lese.Length > 0 && KraftstoffWoerter.Any(w => lese.Contains(Leseform(w)));
    }

    /// <summary>Kraftstoff aus einer Antriebs-Zeile ("Antrieb", "Antriebsart", "Motor", "Energie", "Energieträger",
    /// "Elektroantrieb") — NUR ein Rueckfall, wenn die Zeile "Kraftstoff" fehlt (<see cref="Auswerten"/>):
    /// (1) der Wert zaehlt nur, wenn er ein Kraftstoff-Wort enthaelt ("Antrieb: Elektro" ja, "Antrieb: Allrad" nein);
    /// (2) steht "elektro" schon in der Bezeichnung ("Elektroantrieb: Ja"), ist es "Elektro" — ausser der Wert verneint.
    /// null = kein Kraftstoff. (rein, fuer Tests)</summary>
    internal static string? KraftstoffAusAntrieb(string bezeichnung, string? wert)
    {
        if (string.IsNullOrWhiteSpace(wert)) return null;
        string b = FahrzeugCodes.Norm(bezeichnung);
        if (b.Contains("elektro"))
        {
            string w = FahrzeugCodes.Norm(wert);
            bool verneint = w.StartsWith("nein") || w.StartsWith("kein") || w.StartsWith("nicht") || w.StartsWith("ohne");
            return verneint ? null : "Elektro";
        }
        return IstKraftstoffWort(wert) ? Sauber(wert) : null;
    }

    /// <summary>Eindeutige Elektro-Modelle im Titel bzw. in "Marke, Modell" (Liste Ahmad 09.10.2026): Mokka-e, e-tron,
    /// EQA/EQB/EQC/EQE/EQS, ID.3/4/5/7, e-Golf, e-up, Zoe, Leaf, Tesla, Taycan, i3, iX, i4, EV6, Ioniq 5/6, e-208,
    /// e-2008, Corsa-e, e-Niro, MX-30, Enyaq, Born, Spring — als ganze Woerter ("i30" und "ix35" sind keine).</summary>
    private static readonly Regex ElektroMuster = new(
        @"(?<![\w-])(?:mokka[\s-]?e|corsa[\s-]?e|e-tron|eq[abces]|id\.?\s?[3457]|e-golf|e-up!?|zoe|leaf|tesla|taycan"
        + @"|i3s?|ix\d?|i4|ev6|ioniq\s?[56]|e-20{1,2}8|e-niro|mx-30|enyaq|born|spring)(?![\w-])",
        RegexOptions.IgnoreCase | RegexOptions.Compiled);

    /// <summary>Traegt der Text ein eindeutiges Elektro-Kennzeichen? Bleibt der Kraftstoff leer, geht dann "Elektro" als
    /// ALTERNATIVE mit (<c>alternativen.kraftstoff</c>) — nicht als sicherer Wert: der Server nimmt Alternativen nur,
    /// wenn der Hauptwert fehlt. (rein, fuer Tests)</summary>
    internal static bool ElektroKennzeichen(string? text) =>
        !string.IsNullOrWhiteSpace(text) && ElektroMuster.IsMatch(text);

    /// <summary>Befund Ahmad 09.10.2026 (1.5.12, zweiter Blick): jede erkannte Zeile der Tabelle samt Lage des Werts im
    /// Bild — dieselbe Zuordnung Bezeichnung -> Wert wie <see cref="Tabelle"/> (die nimmt genau diese Zeilen), damit der
    /// zweite Blick denselben Wert noch einmal liest und nicht den einer Nachbarzeile. Auch Zeilen OHNE gelesenen Wert
    /// ("Inserat-ID:" und daneben nichts Lesbares) kommen mit — gerade die soll der zweite Blick noch lesen.</summary>
    internal static List<TabellenZeile> Zeilen(IReadOnlyList<OcrZeile> zeilen)
    {
        var liste = new List<TabellenZeile>();
        if (zeilen.Count == 0) return liste;

        var reihen = InReihen(zeilen);
        // Spaltengrenze: wo in mehrteiligen Reihen der Wert beginnt.
        var wertStarts = reihen.Where(r => r.Count >= 2).Select(r => r[1].X).OrderBy(x => x).ToList();
        double grenze = wertStarts.Count > 0 ? wertStarts[wertStarts.Count / 2] - 8 : double.MaxValue;

        double? spalte = grenze != double.MaxValue ? grenze + 8 : null;
        for (int i = 0; i < reihen.Count; i++)
        {
            var reihe = reihen[i];
            string bezeichnung, wert;
            double wertX, linksMin = 0;
            if (reihe.Count >= 2 && reihe[0].X < grenze)
            {
                bezeichnung = reihe[0].Text;
                wert = string.Join(" ", reihe.Skip(1).Select(z => z.Text));
                wertX = reihe[1].X;
                linksMin = reihe[0].Rechts;
                int dp = bezeichnung.IndexOf(':');
                if (dp >= 0 && dp < bezeichnung.Length - 1)
                {
                    wert = bezeichnung[(dp + 1)..].Trim() + " " + wert;
                    bezeichnung = bezeichnung[..dp];
                    wertX = WertBeginn(reihe[0], dp, grenze);
                    linksMin = 0;               // die Bezeichnung endet irgendwo in reihe[0] — nicht genau bekannt
                }
            }
            else
            {
                // hier ist reihe[0] das einzige Stueck (mehrteilige Reihen links der Grenze nimmt der Zweig oben)
                string text = string.Join(" ", reihe.Select(z => z.Text));
                int dp = text.IndexOf(':');
                if (dp <= 0 || reihe[0].X >= grenze) continue;   // nur Wert oder nur Bezeichnung
                bezeichnung = text[..dp];
                wert = text[(dp + 1)..];
                wertX = WertBeginn(reihe[0], dp, grenze);
            }
            bezeichnung = bezeichnung.Trim().TrimEnd(':', ' ');
            var feld = BezeichnungErkennen(bezeichnung);
            if (feld == null) continue;
            liste.Add(new TabellenZeile(feld.Value, wert.Trim(), wertX, spalte, linksMin,
                                        wert.Trim().Length > 0 ? reihe.Max(z => z.Rechts) : null,
                                        reihe.Min(z => z.Y), reihe.Max(z => z.Y + z.Hoehe),
                                        i > 0 ? reihen[i - 1].Max(z => z.Y + z.Hoehe) : null,
                                        i + 1 < reihen.Count ? reihen[i + 1].Min(z => z.Y) : null,
                                        bezeichnung));
        }
        return liste;
    }

    /// <summary>Wo beginnt der Wert, wenn die Texterkennung Bezeichnung und Wert als EIN Stueck gelesen hat
    /// ("Kraftstoff: Diesel")? Ist die Wertspalte aus anderen Reihen bekannt (<paramref name="grenze"/>), dort; sonst
    /// nach dem Anteil der Zeichen bis zum Doppelpunkt geschaetzt.</summary>
    private static double WertBeginn(OcrZeile z, int dp, double grenze)
    {
        bool ohneWert = z.Text[(dp + 1)..].Trim().Length == 0;
        if (grenze != double.MaxValue)
        {
            double spalte = grenze + 8;
            if (spalte > z.X && (ohneWert || spalte < z.Rechts)) return spalte;
        }
        if (ohneWert) return z.Rechts;
        return z.X + z.Breite * (dp + 1) / z.Text.Length;
    }

    /// <summary>1.5.12: so weit (in Zeilenhoehen) reicht der Ausschnitt rechts ueber das Gelesene hinaus. Gemessen
    /// 09.10.2026 (1.080 gezeichnete Tabellen): genauso viele Treffer wie bis zum Tabellenrand (939 statt 938), aber
    /// ein Viertel weniger Zeit (+59 statt +76 ms je Lesung) — das Sammelbild wird schmaler.</summary>
    internal const double RechtsZeilen = 8;

    /// <summary>1.5.12: Ausschnitt (Pixel im Abbild) um den Wert einer Zeile. Waagrecht ab der Wertspalte (etwas Rand
    /// davor, nie in die Bezeichnung) bis <see cref="RechtsZeilen"/> Zeilenhoehen hinter das Gelesene (ohne gelesenen
    /// Wert bis zum Rand der Tabelle) — NICHT nur so weit, wie der erste Durchgang gelesen hat: der verschluckt gern ein
    /// ganzes Wort ("Golf" statt "VW Golf", "BMW" statt "BMW X1"), und genau das soll der zweite Blick noch sehen
    /// (gemessen: zu knappe Ausschnitte lasen "/ Polo" und "BMW X"). Senkrecht die Reihe mit etwas Rand, aber nie ueber
    /// die halbe Luecke zur Nachbarreihe hinaus (sonst liest er deren Wert mit). null, wenn nichts uebrig bleibt.
    /// (rein, fuer Tests)</summary>
    internal static System.Drawing.Rectangle? WertAusschnitt(TabellenZeile z, int bildBreite, int bildHoehe)
    {
        double h = Math.Max(4, z.Unten - z.Oben);
        double randX = Math.Max(4, h * 0.5), randY = Math.Max(3, h * 0.3);
        double links = Math.Max(Math.Min(z.WertX, z.Spalte ?? z.WertX) - randX, z.LinksMin + 1);
        double rechts = bildBreite - 1;                 // die aeusserste Pixelspalte (Rahmen der Tabelle) nicht
        if (z.Rechts is { } gelesenBis) rechts = Math.Min(rechts, gelesenBis + RechtsZeilen * h);
        double oben = z.Oben - randY, unten = z.Unten + randY;
        if (z.VorherUnten is { } vu) oben = Math.Max(oben, (vu + z.Oben) / 2);
        if (z.DanachOben is { } d) unten = Math.Min(unten, (z.Unten + d) / 2);
        int x1 = Math.Max(0, (int)Math.Floor(links)), y1 = Math.Max(0, (int)Math.Floor(oben));
        int x2 = Math.Min(bildBreite, (int)Math.Ceiling(rechts)), y2 = Math.Min(bildHoehe, (int)Math.Ceiling(unten));
        if (x2 - x1 < 4 || y2 - y1 < 4) return null;
        return new System.Drawing.Rectangle(x1, y1, x2 - x1, y2 - y1);
    }

    /// <summary>1.5.12: Ausschnitte der Werte von <paramref name="felder"/> — je Feld die Zeile, die auch
    /// <see cref="Tabelle"/> nimmt (die erste mit Wert), sonst die erste ohne gelesenen Wert. (rein, fuer Tests)</summary>
    internal static Dictionary<Feld, System.Drawing.Rectangle> WertBereiche(IReadOnlyList<OcrZeile> zeilen, int bildBreite,
                                                                            int bildHoehe, IReadOnlyCollection<Feld> felder)
    {
        var ergebnis = new Dictionary<Feld, System.Drawing.Rectangle>();
        var mitWert = new HashSet<Feld>();
        foreach (var z in Zeilen(zeilen))
        {
            if (!felder.Contains(z.Feld) || mitWert.Contains(z.Feld)) continue;
            if (ergebnis.ContainsKey(z.Feld) && z.Wert.Length == 0) continue;
            if (WertAusschnitt(z, bildBreite, bildHoehe) is not { } r) continue;
            ergebnis[z.Feld] = r;
            if (z.Wert.Length > 0) mitWert.Add(z.Feld);
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
        var (kw, ps, _) = LeistungGeprueft(s);
        return (kw, ps);
    }

    /// <summary>Wie <see cref="Leistung"/>, aber: widersprechen sich gelesene kW und PS (Pruefbericht 03.10.2026,
    /// Nr. 9), bleibt die Leistung unbekannt und <c>Unsicher</c> ist true — vorher gewann still der kW-Wert, auch
    /// wenn genau der falsch gelesen war (110 kW -> 170 kW). Der zweite Lesedurchgang kann sie dann noch klaeren.</summary>
    public static (int? Kw, int? Ps, bool Unsicher) LeistungGeprueft(string? s)
    {
        var (kw, ps) = LeistungRoh(s);
        if (kw == null && ps == null) return (null, null, false);
        if (kw != null && ps != null && Math.Abs(FahrzeugCodes.KwZuPs(kw.Value) - ps.Value) > 3
            && Math.Abs(FahrzeugCodes.PsZuKw(ps.Value) - kw.Value) > 3)
            return (null, null, true);
        kw ??= ps != null ? FahrzeugCodes.PsZuKw(ps.Value) : null;
        ps ??= kw != null ? FahrzeugCodes.KwZuPs(kw.Value) : null;
        if (kw is < 5 or > 1500) return (null, null, false);
        return (kw, ps, false);
    }

    private static (int? Kw, int? Ps) LeistungRoh(string? s)
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

    /// <summary>Inserat-ID wie gelesen -> nur Buchstaben, Ziffern, Bindestrich; leer = null. (Auch fuer den zweiten
    /// Blick, 1.5.12 — dieselbe Regel wie fuer den ersten.)</summary>
    internal static string? InseratIdSauber(string? s)
    {
        if (s == null) return null;
        string sauber = Regex.Replace(s, @"[^A-Za-z0-9\-]", "");
        return sauber.Length > 0 ? sauber : null;
    }

    internal static string? Sauber(string? s)
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
            // 1.5.14: fehlt die Zeile "Kraftstoff" (Elektroautos, Opel Mokka-e), der Rueckfall aus der Antriebs-Zeile
            Kraftstoff = Sauber(tab.GetValueOrDefault(Feld.Kraftstoff)) ?? Sauber(tab.GetValueOrDefault(Feld.Antrieb)),
            Tueren = Sauber(tab.GetValueOrDefault(Feld.Tueren)),
            Preis = Betrag(tab.GetValueOrDefault(Feld.Preis)),
        };
        (f.EzMonat, f.EzJahr) = Erstzulassung(tab.GetValueOrDefault(Feld.Erstzulassung));
        (f.Kw, f.Ps, f.LeistungUnsicher) = LeistungGeprueft(tab.GetValueOrDefault(Feld.Leistung));
        if (tab.TryGetValue(Feld.InseratId, out var id)) f.InseratId = InseratIdSauber(id);
        f.HashId = HashId(tab.GetValueOrDefault(Feld.HashId));
        var (quelle, titel, preis) = Kopf(kopf, kopfBreite);
        f.Quelle = quelle;
        f.Titel = titel;
        f.Preis ??= preis;
        // 1.5.14 (Wunsch Ahmad 09.10.2026): kein Kraftstoff gelesen, aber Titel/Modell sagen eindeutig "Elektroauto" —
        // als Alternative mitschicken (der Server nimmt sie nur, wenn der Hauptwert fehlt), nie als sicheren Wert
        if (f.Kraftstoff == null && (ElektroKennzeichen(f.Titel) || ElektroKennzeichen(f.MarkeModellText)))
            f.AlternativenKraftstoff.Add("Elektro");
        NeuwagenErgaenzen(f, DateTime.Now);
        return f;
    }

    private static readonly Regex Neu = new(@"^\s*neu(wagen|fahrzeug)?\s*$", RegexOptions.IgnoreCase | RegexOptions.Compiled);

    /// <summary>Befund 03.10.2026 (BYD Dolphin, mobile.de): Neuwagen haben in AutoPointer keine Zeilen
    /// "Erstzulassung" und "Kilometerstand" — dann gilt dieses Jahr und 0 km, sonst oeffnete sich nichts.</summary>
    internal static void NeuwagenErgaenzen(Fahrzeug f, DateTime heute)
    {
        if (f.Zustand == null || !Neu.IsMatch(f.Zustand)) return;
        if (f.EzJahr == null) { f.EzJahr = heute.Year; f.EzMonat = null; }
        f.Kilometer ??= 0;
    }

    /// <summary>Fuehrt zwei Lesungen zusammen: fehlende Felder der ersten werden
    /// aus der zweiten ergaenzt (zweiter Durchlauf mit anderem Zoom).</summary>
    public static Fahrzeug Ergaenzen(Fahrzeug a, Fahrzeug b)
    {
        if (a.MarkeModellText.Length == 0) a.MarkeModellText = b.MarkeModellText;
        if (a.EzJahr == null) { a.EzJahr = b.EzJahr; a.EzMonat = b.EzMonat; }
        a.Kilometer ??= b.Kilometer;
        if (a.Kw == null) { a.Kw = b.Kw; a.Ps = b.Ps; }
        // Nr. 9: unsicher bleibt die Leistung nur, wenn KEIN Durchgang einen stimmigen Wert lieferte
        a.LeistungUnsicher = a.Kw == null && (a.LeistungUnsicher || b.LeistungUnsicher);
        a.Kraftstoff ??= b.Kraftstoff;
        // 1.5.14: die Elektro-Alternative des anderen Durchlaufs behalten — aber nie den Hauptwert doppeln
        foreach (var alt in b.AlternativenKraftstoff)
            if (!a.AlternativenKraftstoff.Contains(alt, StringComparer.Ordinal)) a.AlternativenKraftstoff.Add(alt);
        a.AlternativenKraftstoff.RemoveAll(alt => string.Equals(alt, a.Kraftstoff, StringComparison.Ordinal));
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
