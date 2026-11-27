using System.Drawing;
using System.Drawing.Imaging;
using System.Text;
using System.Windows.Forms;

namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 27.11.2026 ("es darf keine Lesefehler geben"): die Beschreibung aus AutoPointer NICHT mit der
/// Texterkennung lesen, sondern Buchstabe fuer Buchstabe mit der echten Schrift abgleichen.</summary>
/// <remarks>
/// Gemessen am 27.11.2026: die Windows-Texterkennung liest das Feld mit rund 3 % Zeichenfehlern (beste
/// Bildaufbereitung 2,2 %) — die Schrift ist zu klein. Aber AutoPointer zeichnet den Text mit einer festen
/// Windows-Schrift (Tahoma, GDI): derselbe Text, auf DIESEM Rechner mit derselben Schrift nachgezeichnet, ergibt bis
/// auf einzelne Randpunkte dasselbe Bild. Also: jedes Zeichen einmal selbst zeichnen (Vorlage) und die Zeile von links
/// nach rechts damit belegen — ein Zeichen gilt nur, wenn seine Bildpunkte genau passen; am Ende muss die ganze Zeile
/// aufgehen. Das ist kein Raten: eine aufgegangene Zeile ist der Text, der dort steht.
/// Was nicht aufgeht (unbekanntes Zeichen, Emoji, andere Schriftart), wird als Luecke (<see cref="Luecke"/>) markiert;
/// die Lesung gilt dann nicht als exakt, und der Aufrufer nimmt dafuer die Texterkennung.
/// Die Vorlagen entstehen auf dem Rechner des Suchers mit dessen Schriftglaettung — so passen sie zu dem, was sein
/// AutoPointer zeichnet. Die Schriftgroesse wird aus dem Zeilenabstand bestimmt und an den ersten Zeilen geprueft.
/// </remarks>
internal sealed class SchriftLeser
{
    /// <summary>Platzhalter fuer ein Stueck, das nicht aufging.</summary>
    internal const char Luecke = '▯';      // ▯

    internal const string SchriftName = "Tahoma";

    /// <summary>Alle Zeichen, die abgeglichen werden (deutsche Inserate: Latin-1, Satz- und Sonderzeichen, Symbole).</summary>
    internal static readonly string Zeichen = BaueZeichen();

    private static string BaueZeichen()
    {
        var sb = new StringBuilder();
        for (char c = '!'; c <= '~'; c++) sb.Append(c);
        for (char c = '¡'; c <= 'ÿ'; c++) if (c != '­') sb.Append(c);
        sb.Append("ŒœŠšŸŽžƒ€‚„…†‡‰‹›‘’“”•–—™ˆ˜ĞğİıŞşĆćČčĐđŁłŃńŚśŹźŻżĄąĘęŘřŤťŮůŐőŰű");
        sb.Append("●○■□▪▫◆◇★☆►▶◄▲▼→←↑↓⇒✓✔✗✘☑☐☒♦♥♠♣☎✆✉⚠≈≤≥≠·‧※№℃");
        return new string(sb.ToString().Distinct().ToArray());
    }

    private sealed class Vorlage
    {
        public char Zeichen;
        public int Vorschub;                 // so weit rueckt der Stift
        public int Links, Rechts;            // Tinten-Spalten [Links, Rechts) relativ zum Stift (Links kann < 0 sein)
        public int[] Punkte = Array.Empty<int>();    // RGB je Punkt, Spalte fuer Spalte: (Rechts-Links) * Hoehe
        public int Tinte;                    // Anzahl nicht weisser Punkte
    }

    /// <summary>So viele Bildpunkte duerfen in den beiden Randspalten eines Zeichens abweichen (Mischung mit dem
    /// Nachbarzeichen; gemessen 1–2 je Stelle).</summary>
    internal const int RandErlaubt = 4;
    private const int Rand = 6;             // Platz links/rechts beim Zeichnen der Vorlagen (Ueberhang)
    /// <summary>Hintergrundfarbe, auf der der Text steht (RGB). _grund im Beschreibungsfeld und in der Liste; die Tabelle
    /// „Technische Daten“ ist gestreift (F9F9F9 / FFFFFF) — die Schriftglaettung mischt die Raender mit DIESEM Grund,
    /// darum gibt es je Grundfarbe eigene Vorlagen (gemessen 27.11.2026).</summary>
    private readonly int _grund;

    private readonly List<Vorlage> _vorlagen = new();
    private readonly int _leerzeichen;
    /// <summary>So weit ragt die Tinte eines Zeichens hoechstens LINKS ueber seine Stiftstelle hinaus.</summary>
    private readonly int _maxLinks;
    /// <summary>true = die Zeile wurde als EINE Zeichenkette gezeichnet (Listen und Tabellen in AutoPointer, gemessen
    /// 27.11.2026): der linke Ueberhang am Wortanfang bleibt stehen. false = Wort fuer Wort (Beschreibungsfeld): er wird
    /// abgeschnitten.</summary>
    private readonly bool _amStueck;
    private readonly FontStyle _stil;
    private readonly Color _grundFarbe;
    /// <summary>Zeilenhoehe der Schrift = Zeilenabstand im Feld.</summary>
    public int Hoehe { get; }
    public int Px { get; }


    private SchriftLeser(int px, bool amStueck = false, bool fett = false, int grund = 0xFFFFFF)
    {
        Px = px;
        _amStueck = amStueck;
        _grund = grund & 0xFFFFFF;
        _grundFarbe = Color.FromArgb(255, (_grund >> 16) & 0xFF, (_grund >> 8) & 0xFF, _grund & 0xFF);
        _stil = fett ? FontStyle.Bold : FontStyle.Regular;
        var schrift = _schrift = new GdiSchrift(SchriftName, px, fett);
        Hoehe = schrift.Miss("Xg").Height;
        int x = schrift.Miss("x").Width;
        _leerzeichen = schrift.Miss("x x").Width - 2 * x;
        foreach (char c in Zeichen)
        {
            string s = c.ToString();
            int vorschub = schrift.Miss("x" + s + "x").Width - 2 * x;
            if (vorschub <= 0) continue;
            int b = vorschub + 2 * Rand;
            using var bild = new Bitmap(b, Hoehe, PixelFormat.Format32bppRgb);
            using (var g = Graphics.FromImage(bild))
            {
                g.Clear(_grundFarbe);
                schrift.Zeichne(g, s, Rand, 0, _grund);
            }
            var p = Lies(bild);
            int links = -1, rechts = -1, tinte = 0;
            for (int sx = 0; sx < b; sx++)
                for (int y = 0; y < Hoehe; y++)
                    if (p[y * b + sx] != _grund)
                    {
                        if (links < 0) links = sx;
                        rechts = sx + 1;
                        tinte++;
                    }
            if (links < 0) continue;                       // zeichnet nichts (Zeichen fehlt in der Schrift)
            var v = new Vorlage { Zeichen = c, Vorschub = vorschub, Links = links - Rand, Rechts = rechts - Rand, Tinte = tinte };
            v.Punkte = new int[(rechts - links) * Hoehe];
            for (int sx = links; sx < rechts; sx++)
                for (int y = 0; y < Hoehe; y++)
                    v.Punkte[(sx - links) * Hoehe + y] = p[y * b + sx];
            _vorlagen.Add(v);
        }
        // "fehlendes Zeichen" (Kaestchen) sieht fuer viele Zeichen gleich aus — solche Doppelgaenger entfernen, sonst
        // wuerde ein Kaestchen als irgendein Zeichen gelesen
        var doppelt = _vorlagen.GroupBy(v => (v.Vorschub, v.Links, string.Join(",", v.Punkte)))
                               .Where(g => g.Count() > 3).SelectMany(g => g).ToHashSet();
        _vorlagen.RemoveAll(doppelt.Contains);
        // mehr Tinte zuerst: "m" vor "r"+"n", "ü" vor "u"
        _vorlagen.Sort((a, b2) => b2.Tinte.CompareTo(a.Tinte));
        foreach (var v in _vorlagen) _nachZeichen[v.Zeichen] = v;
        _maxLinks = _vorlagen.Count == 0 ? 0 : Math.Max(0, _vorlagen.Max(v => -v.Links));
    }

    private static readonly Dictionary<(int, bool, bool, int), SchriftLeser> _merker = new();
    private static readonly Dictionary<int, int> _hoehen = new();

    /// <summary>Leser fuer eine Schriftgroesse (Vorlagen einmal je Programmlauf gezeichnet, ~20 ms).</summary>
    internal static SchriftLeser Fuer(int px, bool amStueck = false, bool fett = false, int grund = 0xFFFFFF)
    {
        lock (_merker)
        {
            if (!_merker.TryGetValue((px, amStueck, fett, grund), out var l)) _merker[(px, amStueck, fett, grund)] = l = new SchriftLeser(px, amStueck, fett, grund);
            return l;
        }
    }

    /// <summary>Schriftgroessen, deren Zeilenhoehe zu <paramref name="zeilenabstand"/> passt — die naechste zuerst;
    /// 0 = Abstand unbekannt (eine einzige Zeile): alle ueblichen Groessen.</summary>
    internal static List<int> GroessenFuer(int zeilenabstand)
    {
        var liste = new List<int>();
        for (int px = 8; px <= 40; px++)
        {
            int h;
            lock (_hoehen)
            {
                if (!_hoehen.TryGetValue(px, out h))
                {
                    using var schrift = new GdiSchrift(SchriftName, px, false);
                    _hoehen[px] = h = schrift.Miss("Xg").Height;
                }
            }
            // der gemessene Abstand (zwischen den Tinten-Oberkanten) schwankt um 1–2 Punkte: Zeilen beginnen mal mit
            // Gross-, mal mit Kleinbuchstaben. Darum alle Groessen in der Naehe, die naechste zuerst.
            if (zeilenabstand <= 0 ? px is >= 9 and <= 26 : Math.Abs(h - zeilenabstand) <= 3) liste.Add(px);
        }
        if (zeilenabstand > 0)
            lock (_hoehen) liste = liste.OrderBy(px => Math.Abs(_hoehen[px] - zeilenabstand)).ThenBy(px => px).ToList();
        return liste;
    }

    private static unsafe int[] Lies(Bitmap bild)
    {
        var p = new int[bild.Width * bild.Height];
        var d = bild.LockBits(new Rectangle(0, 0, bild.Width, bild.Height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
        try
        {
            for (int y = 0; y < d.Height; y++)
            {
                uint* z = (uint*)((byte*)d.Scan0 + (long)y * d.Stride);
                for (int x = 0; x < d.Width; x++) p[y * d.Width + x] = (int)(z[x] & 0xFFFFFF);
            }
        }
        finally { bild.UnlockBits(d); }
        return p;
    }

    /// <summary>Ergebnis einer Zeile: Text und ob sie ganz aufging.</summary>
    internal sealed record Zeile(string Text, bool Exakt);

    /// <summary>Ergebnis eines Feldes.</summary>
    /// <param name="Zeilen">Von oben nach unten, leere Zeilen (Absatz) als "".</param>
    /// <param name="Luecken">Wie viele Stuecke nicht aufgingen.</param>
    internal sealed record Lesung(List<Zeile> Zeilen, int Luecken, int Px)
    {
        public bool Exakt => Luecken == 0 && Zeilen.Count > 0;
        public string Text => string.Join("\n", Zeilen.Select(z => z.Text)).Trim('\n');
    }

    /// <summary>Zeilenabstand aus dem Bild: haeufigster Abstand zwischen den Oberkanten der Textzeilen. 0 = keine
    /// zwei Zeilen (dann hilft nur Probieren). (rein, fuer Tests)</summary>
    internal static int Zeilenabstand(bool[] leer)
    {
        var oben = new List<int>();
        for (int y = 0; y < leer.Length; y++)
            if (!leer[y] && (y == 0 || leer[y - 1])) oben.Add(y);
        var abstaende = new Dictionary<int, int>();
        for (int i = 1; i < oben.Count; i++)
        {
            int d = oben[i] - oben[i - 1];
            if (d is >= 8 and <= 60) abstaende[d] = abstaende.GetValueOrDefault(d) + 1;
        }
        return abstaende.Count == 0 ? 0 : abstaende.OrderByDescending(k => k.Value).ThenBy(k => k.Key).First().Key;
    }

    /// <summary>Das ganze Feld lesen. Probiert die zum Zeilenabstand passenden Schriftgroessen und nimmt die mit den
    /// wenigsten Luecken. null = keine Schrift passt (anderes Programm, andere Schriftart).</summary>
    internal static Lesung? LiesFeld(Bitmap feld, bool amStueck = false, bool fett = false, int nurPx = 0, int grund = 0xFFFFFF)
    {
        var leer = BeschreibungGanz.LeereZeilen(feld);
        int abstand = Zeilenabstand(leer);
        int erste = Array.IndexOf(leer, false);
        if (erste < 0) return new Lesung(new List<Zeile>(), 0, 0);        // leeres Feld: nichts zu lesen, nichts zu zeichnen
        var groessen = GroessenFuer(abstand);
        if (abstand <= 0)
        {
            // eine einzige Zeile: nur Groessen, deren Zeilenhoehe zur Tintenhoehe passt (Tinte ≈ 55–100 % der Zeile)
            int tinte = 0;
            for (int y = erste; y < leer.Length && !leer[y]; y++) tinte++;
            groessen = groessen.Where(px => { lock (_hoehen) return _hoehen[px] >= tinte && _hoehen[px] <= 2 * tinte + 2; }).ToList();
        }
        if (groessen.Count == 0) groessen = GroessenFuer(0);
        // die Groesse vom letzten Mal zuerst: sie aendert sich auf einem Rechner praktisch nie
        int merk = (amStueck ? 1 : 0) + (fett ? 2 : 0);
        int zuletzt = _letztePx[merk];
        if (zuletzt > 0) groessen = new[] { zuletzt }.Concat(groessen.Where(px => px != zuletzt)).ToList();
        // nurPx: der Aufrufer kennt die Groesse schon (Listen) — keine andere probieren (ein Fehlversuch kostet sonst ~0,2 s)
        if (nurPx > 0) groessen = new List<int> { nurPx };
        var punkte = Lies(feld);
        Lesung? beste = null;
        foreach (int px in groessen)
        {
            var l = Fuer(px, amStueck, fett, grund).Lies(punkte, feld.Width, feld.Height, leer);
            if (l == null) continue;
            if (beste == null || l.Luecken < beste.Luecken) beste = l;
            if (beste.Luecken == 0) break;
        }
        if (beste is { Luecken: 0 }) _letztePx[merk] = beste.Px;
        return beste;
    }

    private static readonly int[] _letztePx = new int[4];

    private Lesung? Lies(int[] punkte, int breite, int hoehe, bool[] leer)
    {
        // Raster: die erste Textzeile legt die Oberkante fest. Die Tinte beginnt nicht ganz oben in der Zeile
        // (Oberlaengen) — die Oberkante des Rasters wird an der ersten Zeile ausprobiert.
        int erste = Array.IndexOf(leer, false);
        if (erste < 0) return new Lesung(new List<Zeile>(), 0, Px);
        Lesung? beste = null;
        for (int versatz = 0; versatz < Hoehe && versatz <= erste + Hoehe; versatz++)
        {
            int oben = erste - versatz;
            if (oben < -2) break;
            var probe = LiesZeile(punkte, breite, hoehe, oben);
            if (probe == null || !probe.Exakt) continue;
            // Raster gefunden: alle Zeilen ab hier
            var zeilen = new List<Zeile>();
            int luecken = 0;
            for (int y = oben; y < hoehe; y += Hoehe)
            {
                var z = LiesZeile(punkte, breite, hoehe, y) ?? new Zeile("", true);
                luecken += z.Text.Count(c => c == Luecke);
                zeilen.Add(z);
            }
            while (zeilen.Count > 0 && zeilen[^1].Text.Length == 0) zeilen.RemoveAt(zeilen.Count - 1);
            var l = new Lesung(zeilen, luecken, Px);
            if (beste == null || l.Luecken < beste.Luecken) beste = l;
            if (beste.Luecken == 0) break;
        }
        return beste;
    }

    /// <summary>Eine Zeile an fester Stelle lesen (zum Messen/Pruefen).</summary>
    internal Zeile? ZeileBei(Bitmap feld, int oben) => LiesZeile(Lies(feld), feld.Width, feld.Height, oben);

    /// <summary>Nur zum Pruefen: wie weit kam die Suche im zuletzt gelesenen Wort (Text bis dort)?</summary>
    internal StringBuilder? Spur { get; set; }
    private int _spurStift;

    /// <summary>Nur zum Pruefen: ein Wort ab Spalte 0 der Zeile lesen und die Spur zurueckgeben.</summary>
    internal string SpurBei(Bitmap feld, int oben)
    {
        Spur = new StringBuilder();
        _spurStift = -1;
        var z = ZeileBei(feld, oben);
        string s = $"„{z?.Text}“ / am weitesten: „{Spur}“";
        Spur = null;
        return s;
    }

    private int Punkt(int[] punkte, int breite, int hoehe, int x, int y)
        => x < 0 || x >= breite || y < 0 || y >= hoehe ? _grund : punkte[y * breite + x];

    private bool SpalteWeiss(int[] punkte, int breite, int hoehe, int x, int oben)
    {
        for (int y = 0; y < Hoehe; y++)
            if (Punkt(punkte, breite, hoehe, x, oben + y) != _grund) return false;
        return true;
    }

    /// <summary>Grobe Vorauswahl: die INNEREN Tinten-Spalten der Vorlage muessen genau stimmen (die aeusserste Spalte
    /// je Seite mischt sich mit dem Nachbarzeichen und wird erst im Zusammenhang geprueft), und der Vorschub
    /// ausserhalb der Tinte muss weiss sein — sonst wuerde Tinte uebersprungen.</summary>
    private bool Vorauswahl(Vorlage v, int[] punkte, int breite, int hoehe, int x, int oben, int ueberhangDavor)
    {
        int spalten = v.Rechts - v.Links;
        // frei bleiben: links, was das Zeichen davor ueberdeckt (mindestens eine Spalte); rechts die letzte
        // Tinten-Spalte und alles, wohin der linke Rand des naechsten Zeichens ragen kann
        int von = x + Math.Max(v.Links + 1, ueberhangDavor), bis = x + Math.Min(v.Rechts - 1, v.Vorschub - _maxLinks);
        for (int sx = von; sx < bis; sx++)
        {
            int s = sx - x - v.Links;
            for (int y = 0; y < Hoehe; y++)
                if (Punkt(punkte, breite, hoehe, sx, oben + y) != v.Punkte[s * Hoehe + y]) return false;
        }
        for (int sx = x + Math.Max(1, ueberhangDavor); sx < x + v.Vorschub - Math.Max(1, _maxLinks); sx++)
        {
            if (sx >= x + v.Links && sx < x + v.Rechts) continue;
            if (!SpalteWeiss(punkte, breite, hoehe, sx, oben)) return false;
        }
        return true;
    }

    // ---- Zeichen im Zusammenhang (mit ihren linken Nachbarn nachgezeichnet) ----

    private readonly Dictionary<string, (int[] P, int B, int Stift)> _kontext = new();
    private readonly Dictionary<char, Vorlage> _nachZeichen = new();
    private readonly GdiSchrift _schrift;

    /// <summary>Die letzten Zeichen eines Worts so, wie Windows sie ZUSAMMEN zeichnet — dort, wo sich die Raender
    /// zweier Buchstaben ueberlagern, gilt nur dieses Bild. <c>Stift</c> = Stiftstelle des letzten Zeichens.</summary>
    private (int[] P, int B, int Stift) Kontext(string s)
    {
        if (_kontext.TryGetValue(s, out var k)) return k;
        int davor = 0;
        for (int i = 0; i < s.Length - 1; i++) davor += _nachZeichen[s[i]].Vorschub;
        int b = davor + _nachZeichen[s[^1]].Vorschub + 2 * Rand;
        using var bild = new Bitmap(b, Hoehe, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild))
        {
            g.Clear(_grundFarbe);
            _schrift.Zeichne(g, s, Rand, 0, _grund);
        }
        k = (Lies(bild), b, Rand + davor);
        if (_kontext.Count > 20000) _kontext.Clear();
        return _kontext[s] = k;
    }

    /// <summary>Abweichende Bildpunkte in den Spalten [a, b) — gezaehlt ab der Stiftstelle des letzten Zeichens von
    /// <paramref name="s"/>. Bricht ab, sobald es mehr als <paramref name="max"/> sind.</summary>
    private int Abweichung(string s, int[] punkte, int breite, int hoehe, int stift, int oben, int a, int b, int max,
                           bool wortanfang = false, int wortende = int.MaxValue)
    {
        var k = Kontext(s);
        int n = 0;
        for (int sp = a; sp < b; sp++)
        {
            int kx = k.Stift + sp;
            if (stift + sp < 0 || stift + sp >= breite) continue;      // ausserhalb des Feldes abgeschnitten: nicht pruefbar
            for (int y = 0; y < Hoehe; y++)
            {
                // AutoPointer zeichnet jedes Wort ab SEINER Stiftstelle: was links vom Wortanfang
                // ueberhaengt, wird abgeschnitten (gemessen 27.11.2026: je 2 Randpunkte bei X, A, w); rechts bleibt der Ueberhang
                int soll = (wortanfang && sp < 0) || sp >= wortende || kx < 0 || kx >= k.B ? _grund : k.P[y * k.B + kx];
                if (Punkt(punkte, breite, hoehe, stift + sp, oben + y) != soll && ++n > max) return n;
            }
        }
        return n;
    }

    // Es gilt ueberall: KEINE Abweichung. Jede Tinten-Spalte wird genau einmal gegen das Bild geprueft, das Windows
    // fuer die Zeichen im Zusammenhang zeichnet — innen beim Zeichen selbst, am rechten Rand beim Zeichen danach
    // bzw. am Wortende. Ein Zeichen, das nur "ungefaehr" passt, gibt es nicht.

    private int _ende;
    private int _grenzeStift;
    private string? _grenzeText;

    private Zeile? LiesZeile(int[] punkte, int breite, int hoehe, int oben)
    {
        // rechtes Ende der Tinte
        int ende = -1;
        for (int x = breite - 1; x >= 0 && ende < 0; x--)
            if (!SpalteWeiss(punkte, breite, hoehe, x, oben)) ende = x + 1;
        if (ende < 0) return new Zeile("", true);
        _ende = ende;

        var sb = new StringBuilder();
        var tot = new HashSet<long>();
        bool exakt = true;
        int stift = 0;
        while (stift < ende)
        {
            // die ganze restliche Zeile muss aufgehen (Tiefensuche mit Merker). Geht sie nicht auf, bleibt die
            // weiteste Wortgrenze stehen, bis zu der alles genau passte.
            int nach = Versuch(punkte, breite, hoehe, oben, stift, ende, tot, out string text);
            if (nach < 0 && !Fortschritt(stift))
            {
                // Die Stiftstelle ist hier nicht sicher (Zeilenanfang mit Rand, nach einer Luecke): ab der ersten
                // Tinten-Spalte alle Stellen probieren, an denen ein Zeichen beginnen kann
                int tinte = stift;
                while (tinte < ende && SpalteWeiss(punkte, breite, hoehe, tinte, oben)) tinte++;
                for (int p = tinte + _maxLinks; p >= Math.Max(stift, tinte - 4); p--)
                {
                    if (p == stift) continue;
                    nach = Versuch(punkte, breite, hoehe, oben, p, ende, tot, out text);
                    if (nach >= 0 || Fortschritt(tinte)) break;
                }
                if ((nach >= 0 || Fortschritt(tinte)) && sb.Length > 0 && sb[^1] != ' ') sb.Append(' ');
            }
            if (nach >= 0)
            {
                sb.Append(text);
                break;
            }
            if (Fortschritt(stift))
            {
                sb.Append(_grenzeText);          // bis hierher genau; das naechste Wort geht nicht auf
                stift = _grenzeStift;
            }
            // dieses Wort geht nicht auf: bis zur naechsten weissen Luecke ueberspringen
            exakt = false;
            int x2 = stift;
            while (x2 < ende && SpalteWeiss(punkte, breite, hoehe, x2, oben)) x2++;
            int frei = 0, noetig = Math.Max(2, _leerzeichen - 1);
            while (x2 < ende && frei < noetig)
            {
                frei = SpalteWeiss(punkte, breite, hoehe, x2, oben) ? frei + 1 : 0;
                x2++;
            }
            if (sb.Length > 0 && sb[^1] != ' ') sb.Append(' ');
            sb.Append(Luecke).Append(' ');
            stift = Math.Max(x2, stift + 1);
        }
        return new Zeile(System.Text.RegularExpressions.Regex.Replace(sb.ToString(), " {2,}", " ").Trim(), exakt);
    }

    /// <summary>Kam der letzte Versuch ueber <paramref name="ab"/> hinaus — mit mindestens einem echten Wort?</summary>
    private bool Fortschritt(int ab) => _grenzeStift > ab && !string.IsNullOrWhiteSpace(_grenzeText);

    private int Versuch(int[] punkte, int breite, int hoehe, int oben, int stift, int ende, HashSet<long> tot, out string text)
    {
        _grenzeStift = -1;
        _grenzeText = null;
        var aus = new StringBuilder();
        int nach = Suche(punkte, breite, hoehe, oben, stift, ende, aus, "", tot, 0);
        text = aus.ToString();
        return nach;
    }

    /// <summary>Passt der rechte Rand des letzten Zeichens von <paramref name="wort"/>, wenn danach nichts mehr
    /// kommt (Leerzeichen/Zeilenende)?</summary>
    private bool WortendePasst(string wort, int[] punkte, int breite, int hoehe, int stift, int oben)
    {
        if (wort.Length == 0) return true;
        var v = _nachZeichen[wort[^1]];
        // am Stueck: der letzte der drei Punkte einer abgeschnittenen Zelle ist am Zellenrand gekappt (gemessen
        // 27.11.2026) — rechts vom Ende der Tinte dieser Zeile steht dann nichts mehr
        int kappe = _amStueck && v.Zeichen == '.' && stift >= _ende ? _ende - (stift - v.Vorschub) : int.MaxValue;
        return Abweichung(wort, punkte, breite, hoehe, stift - v.Vorschub, oben,
                          Math.Min(v.Rechts - 1, v.Vorschub - _maxLinks), Math.Max(v.Rechts, v.Vorschub), 0,
                          wortanfang: wort.Length == 1 && !_amStueck, wortende: kappe) == 0;
    }

    /// <summary>Tiefensuche ueber die ganze restliche Zeile: ab <paramref name="stift"/> Zeichen oder Leerzeichen
    /// anlegen, bis das Zeilenende erreicht ist. <paramref name="wort"/> = die letzten (bis zu zwei) Zeichen des
    /// laufenden Worts ("" = Wortanfang). Zurueck: Stift am Ende, -1 = geht nicht auf.</summary>
    private int Suche(int[] punkte, int breite, int hoehe, int oben, int stift, int ende, StringBuilder aus,
                      string wort, HashSet<long> tot, int tiefe)
    {
        if (Spur != null && stift > _spurStift) { _spurStift = stift; Spur.Clear(); Spur.Append(aus).Append($" …bis Spalte {stift}"); }
        if (stift >= ende) return WortendePasst(wort, punkte, breite, hoehe, stift, oben) ? stift : -1;
        long merke = ((long)stift << 32) | (wort.Length == 0 ? 0u : wort[^1]);
        if (tot.Contains(merke) || tiefe > 900) return -1;
        Vorlage? davor = wort.Length == 0 ? null : _nachZeichen[wort[^1]];
        int ueberDavor = davor == null ? 0 : Math.Max(0, davor.Rechts - davor.Vorschub);

        // Zeichen: erst die Vorauswahl, dann genau im Zusammenhang mit den Zeichen davor
        List<(Vorlage V, string W)>? kandidaten = null;
        foreach (var v in _vorlagen)
        {
            // am Stueck: der letzte der drei Punkte einer abgeschnittenen Zelle ist am Zellenrand gekappt (gemessen
            // 27.11.2026) — was von ihm zu sehen ist, muss genau stimmen
            if (_amStueck && v.Zeichen == '.' && stift + v.Rechts > _ende && stift + v.Links < _ende)
            {
                bool passt = true;
                for (int sx = stift + v.Links; sx < _ende && passt; sx++)
                    for (int y = 0; y < Hoehe && passt; y++)
                        passt = Punkt(punkte, breite, hoehe, sx, oben + y) == v.Punkte[(sx - stift - v.Links) * Hoehe + y];
                if (passt && WortendePasst(wort, punkte, breite, hoehe, stift, oben)) (kandidaten ??= new()).Add((v, "."));
                continue;
            }
            if (!Vorauswahl(v, punkte, breite, hoehe, stift, oben, ueberDavor)) continue;
            string w = wort + v.Zeichen;
            // ab der ersten Spalte, die davor noch offen blieb, bis vor den eigenen offenen rechten Rand
            int a = davor == null ? Math.Min(v.Links, -_maxLinks)
                : Math.Min(v.Links, Math.Min(-_maxLinks, davor.Rechts - 1 - davor.Vorschub));
            int b = Math.Min(v.Rechts - 1, v.Vorschub - _maxLinks);
            // am Stueck: der linke Ueberhang steht da — nur ganz am Zeilenanfang schneidet ihn der Zellenrand ab (beides gilt)
            if (Abweichung(w, punkte, breite, hoehe, stift, oben, a, b, 0, wortanfang: davor == null && !_amStueck) == 0
                || (_amStueck && davor == null && aus.Length == 0
                    && Abweichung(w, punkte, breite, hoehe, stift, oben, a, b, 0, wortanfang: true) == 0))
                (kandidaten ??= new()).Add((v, w.Length > 2 ? w[^2..] : w));
            // am Stueck: die drei Punkte einer abgeschnittenen Zelle („Automatik, Sc...“) zeichnet AutoPointer getrennt vom
            // Text davor — der erste Punkt gilt dann wie ein Wortanfang (das Wort davor muss fuer sich aufgehen)
            else if (_amStueck && v.Zeichen == '.' && davor != null
                     && WortendePasst(wort, punkte, breite, hoehe, stift, oben)
                     && Abweichung(".", punkte, breite, hoehe, stift, oben, Math.Min(v.Links, 0), b, 0) == 0)
                (kandidaten ??= new()).Add((v, "."));
        }
        if (kandidaten != null)
            foreach (var (v, w) in kandidaten)             // _vorlagen ist nach Tinte sortiert: "m" vor "r"+"n"
            {
                int laenge = aus.Length;
                aus.Append(v.Zeichen);
                int nach = Suche(punkte, breite, hoehe, oben, stift + v.Vorschub, ende, aus, w, tot, tiefe + 1);
                if (nach >= 0) return nach;
                aus.Length = laenge;
            }

        // Leerzeichen: sein Vorschub ist weiss — bis auf den Ueberhang des Zeichens davor (links) und die Spalten,
        // in die der linke Rand des naechsten Worts ragen kann (rechts; die prueft das naechste Zeichen)
        bool weiss = true;
        // (gemessen 27.11.2026: der Ueberhang des Worts DAVOR bleibt stehen, der linke Ueberhang des naechsten Worts wird abgeschnitten)
        // am Stueck gezeichnet (Listen): der linke Ueberhang des naechsten Worts steht in den letzten Spalten des Leerzeichens —
        // die prueft das naechste Zeichen; folgt ein weiteres Leerzeichen, prueft dieses sie nach (nichts bleibt ungeprueft)
        int frei = _amStueck ? _maxLinks : 0;
        for (int x = stift + (wort.Length == 0 ? -frei : ueberDavor); x < stift + _leerzeichen - frei && weiss; x++)
            weiss = SpalteWeiss(punkte, breite, hoehe, x, oben);
        if (weiss && WortendePasst(wort, punkte, breite, hoehe, stift, oben))
        {
            int weiter = stift + _leerzeichen;
            aus.Append(' ');
            if (weiter > _grenzeStift) { _grenzeStift = weiter; _grenzeText = aus.ToString(); }
            int nach = Suche(punkte, breite, hoehe, oben, weiter, ende, aus, "", tot, tiefe + 1);
            if (nach >= 0) return nach;
            aus.Length--;
        }
        tot.Add(merke);
        return -1;
    }
}


/// <summary>Eine Windows-Schrift in GENAU der verlangten Pixelgroesse, direkt ueber GDI gezeichnet und gemessen.</summary>
/// <remarks>Befund 27.11.2026 (Probelauf am laufenden AutoPointer): ueber <c>TextRenderer</c> mit einem
/// <c>Font(…, GraphicsUnit.Pixel)</c> zeichnet Windows Forms in einem DPI-bewussten Programm bei 125 % die Schrift um
/// den Faktor 1,25 groesser (im Testlauf nicht — der ist nicht DPI-bewusst). Die Vorlagen passten dann zu nichts, und
/// im echten Programm waere jede Lesung auf die Texterkennung zurueckgefallen. Hier gilt die Pixelgroesse unabhaengig
/// von der Skalierung; Schriftglaettung wie vom System eingestellt (so zeichnet auch AutoPointer).</remarks>
internal sealed class GdiSchrift : IDisposable
{
    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    private struct SIZE { public int cx, cy; }
    [System.Runtime.InteropServices.DllImport("gdi32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern IntPtr CreateFontW(int hoehe, int breite, int neigung, int richtung, int gewicht, uint kursiv, uint unterstrichen,
                                             uint durchgestrichen, uint zeichensatz, uint genauigkeit, uint schnitt, uint guete, uint familie, string name);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")] private static extern IntPtr CreateCompatibleDC(IntPtr dc);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")] private static extern bool DeleteDC(IntPtr dc);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")] private static extern IntPtr SelectObject(IntPtr dc, IntPtr objekt);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")] private static extern bool DeleteObject(IntPtr objekt);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")] private static extern int SetBkMode(IntPtr dc, int modus);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")] private static extern uint SetBkColor(IntPtr dc, uint farbe);
    [System.Runtime.InteropServices.DllImport("gdi32.dll")] private static extern uint SetTextColor(IntPtr dc, uint farbe);
    [System.Runtime.InteropServices.DllImport("gdi32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern bool TextOutW(IntPtr dc, int x, int y, string text, int laenge);
    [System.Runtime.InteropServices.DllImport("gdi32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern bool GetTextExtentPoint32W(IntPtr dc, string text, int laenge, out SIZE groesse);

    private readonly IntPtr _schrift, _dc, _alt;
    private readonly object _sperre = new();

    public GdiSchrift(string name, int px, bool fett)
    {
        // -px = Zeichenhoehe in Bildpunkten; DEFAULT_CHARSET (1), DEFAULT_QUALITY (0)
        _schrift = CreateFontW(-px, 0, 0, 0, fett ? 700 : 400, 0, 0, 0, 1, 0, 0, 0, 0, name);
        _dc = CreateCompatibleDC(IntPtr.Zero);
        _alt = SelectObject(_dc, _schrift);
    }

    public Size Miss(string text)
    {
        lock (_sperre)
        {
            GetTextExtentPoint32W(_dc, text, text.Length, out var g);
            return new Size(g.cx, g.cy);
        }
    }

    /// <summary>Schwarz auf _grund an die Stelle (x, y) — linke obere Ecke der Zeichenzelle.</summary>
    public void Zeichne(Graphics g, string text, int x, int y, int grund = 0xFFFFFF)
    {
        IntPtr dc = g.GetHdc();
        try
        {
            IntPtr alt = SelectObject(dc, _schrift);
            SetBkMode(dc, 2);                   // OPAQUE
            // COLORREF ist 0x00BBGGRR
            SetBkColor(dc, (uint)(((grund & 0xFF) << 16) | (grund & 0xFF00) | ((grund >> 16) & 0xFF)));
            SetTextColor(dc, 0);
            TextOutW(dc, x, y, text, text.Length);
            SelectObject(dc, alt);
        }
        finally { g.ReleaseHdc(dc); }
    }

    public void Dispose()
    {
        SelectObject(_dc, _alt);
        DeleteDC(_dc);
        DeleteObject(_schrift);
    }
}
