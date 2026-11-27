using System.Drawing;
using System.Drawing.Imaging;
using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>Eine Zeile der Inserat-Liste in AutoPointer, so wie sie dort steht (Wunsch Ahmad 27.11.2026, Dauerlauf).</summary>
/// <param name="Oben">Lage der Zeile im Listenbild (Bildpunkte).</param>
/// <param name="Titel">Ueberschrift des Inserats (fett; ohne die Marke „Neu“).</param>
/// <param name="Art">z. B. „PKW, Gebraucht, Kombi/Lieferwagen, 4/5 Türen, 5 Sitze“.</param>
/// <param name="Daten">z. B. „EZ: 12/2017, 147.672 Km, Diesel, Euro 6, 140 kW (190 PS), Auto...“ (am Ende abgeschnitten).</param>
/// <param name="Preis">null = nicht sicher gelesen (z. B. gruen als „reduziert“ gezeichnet).</param>
/// <param name="Privat">true = Plakette „Privat“, false = „Händler“, null = keine erkannt.</param>
/// <param name="TitelGenau">Der Titel ging im Schrift-Abgleich ganz auf (sonst fehlen Stuecke, z. B. Emoji).</param>
/// <param name="Anbieter">Name des Verkaeufers aus der Spalte „Anbieter“ ("" = keiner oder nicht genau lesbar).</param>
/// <param name="Ort">z. B. „D-56457 Westerburg“ (neben der Plakette).</param>
/// <param name="Zeit">z. B. „27.11.2026 16:44:44“ — wann AutoPointer das Inserat bekam.</param>
internal sealed record ListenZeile(int Oben, int Unten, string Titel, string Art, string Daten, string Ausstattung,
                                   int? Preis, bool? Privat, string Portal, string Zeit, bool TitelGenau = false,
                                   string Anbieter = "", IReadOnlyList<string>? Telefone = null, int? EntfernungKm = null,
                                   string Ort = "")
{
    /// <summary>Telefonnummern aus der Spalte „Anbieter“ (nie null).</summary>
    public IReadOnlyList<string> Nummern => Telefone ?? Array.Empty<string>();

    /// <summary>Genug gelesen, um das Auto wiederzuerkennen und vorzusortieren?</summary>
    public bool Lesbar => Zeit.Length > 0 && Portal.Length > 0 && (Art.Length > 0 || Daten.Length > 0);

    /// <summary>Woran dieselbe Zeile wiedererkannt wird (sie rutscht in der Liste nach unten).</summary>
    public string Kennung => string.Join("|", Portal, Zeit, Titel, Art, Daten, Preis);

    /// <summary>Zweites Feld der Art-Zeile: „Gebraucht“, „Unfallwagen“, „Neu“, „Vorführwagen“ …</summary>
    public string Zustand
    {
        get
        {
            var t = Art.Split(", ");
            return t.Length > 1 ? t[1] : "";
        }
    }

    public int? Jahr => Zahl(@"EZ: \d\d/(\d{4})");
    public int? Km => Zahl(@"(?:^|, )(\d{1,3}(?:\.\d{3})*|\d+) Km");
    public int? Ps => Zahl(@"\((\d+) PS\)");

    /// <summary>Das Feld nach dem Kilometerstand; "" wenn es fehlt oder abgeschnitten ist.</summary>
    public string Kraftstoff
    {
        get
        {
            var t = Daten.Split(", ");
            int i = Array.FindIndex(t, s => s.EndsWith(" Km", StringComparison.Ordinal));
            return i >= 0 && i + 1 < t.Length && !t[i + 1].EndsWith("...", StringComparison.Ordinal) ? t[i + 1] : "";
        }
    }

    private int? Zahl(string muster)
    {
        var m = Regex.Match(Daten, muster);
        return m.Success && int.TryParse(m.Groups[1].Value.Replace(".", ""), out int n) ? n : null;
    }
}

/// <summary>Liest die Inserat-Liste von AutoPointer aus einem Bildschirm-Abbild — mit dem Schrift-Abgleich
/// (<see cref="SchriftLeser"/>, „am Stück“), also ohne Lesefehler: was nicht genau aufgeht, bleibt leer.</summary>
/// <remarks>Gemessen am 27.11.2026 (AutoPointer 3.9.9.9341, 125 %): Daten in Tahoma 14 px, Titel fett 13 px, Preis fett
/// 14 px; Zeilen durch eine graue Linie (C8C8C8) getrennt, Spalten Beschreibung/Anbieter/Datenquelle an den hellgrauen
/// Strichen (E5E5E5) im Tabellenkopf; Plakette „Privat“ rot (D40E16), „Händler“ grau (494949); die angeklickte Zeile hat
/// grauen Grund (EAEAEA) und wird nicht gelesen.</remarks>
internal static class ListenLeser
{
    private const int Weiss = 0xFFFFFF;

    /// <summary>Das Listen-Gitter, das gerade wirklich zu sehen ist (AutoPointer hat mehrere uebereinander: Ergebnisse,
    /// Bestand, Parkplatz …): gross, links, und an seiner Mitte liegt kein anderes Fenster darueber.</summary>
    public static IntPtr FindeListe(IntPtr haupt, DetailAnsicht? details = null)
    {
        if (haupt == IntPtr.Zero) return IntPtr.Zero;
        foreach (var h in Native.Kinder(haupt))
        {
            if (h == details?.TechnikTabelle || h == details?.KopfTabelle) continue;
            if (Native.Klasse(h) != AutoPointerFenster.TabellenKlasse || !Native.IsWindowVisible(h)) continue;
            bool oben = Native.ImDpiKontext(h, () =>
            {
                if (!Native.GetWindowRect(h, out var r) || r.Width < 500 || r.Height < 300) return false;
                var mitte = new Native.POINT { X = r.Left + r.Width / 2, Y = r.Top + r.Height / 2 };
                return Native.WindowFromPoint(mitte) == h;
            });
            if (oben) return h;
        }
        return IntPtr.Zero;
    }

    internal static unsafe int[] Punkte(Bitmap bild)
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

    /// <summary>Spalten (linke Kanten) und die ganz sichtbaren Zeilen der Liste.</summary>
    internal sealed record Raster(int Beschreibung, int Anbieter, int Datenquelle, int Rechts, List<(int Oben, int Unten)> Zeilen);

    private static bool Grau(int farbe, int von, int bis)
    {
        int r = (farbe >> 16) & 0xFF, g = (farbe >> 8) & 0xFF, b = farbe & 0xFF;
        return Math.Abs(r - g) <= 4 && Math.Abs(g - b) <= 4 && g >= von && g <= bis;
    }

    /// <summary>Raster aus dem Bild: die drei Spaltenstriche im Kopf, dann die grauen Trennlinien. null = keine Liste
    /// zu erkennen (anderes Gitter, verdeckt).</summary>
    internal static Raster? FindeRaster(int[] p, int b, int h)
    {
        // Spaltenstriche: schmale hellgraue senkrechte Striche in den oberen Bildzeilen, links und rechts weiss
        var striche = new List<int>();
        int kopf = Math.Min(h, 70);
        for (int x = 2; x < b - 2; x++)
        {
            int lauf = 0, best = 0;
            for (int y = 0; y < kopf; y++)
            {
                bool strich = Grau(p[y * b + x], 0xD8, 0xF2) && p[y * b + x - 2] == Weiss && p[y * b + x + 2] == Weiss;
                lauf = strich ? lauf + 1 : 0;
                best = Math.Max(best, lauf);
            }
            if (best >= 8 && (striche.Count == 0 || x - striche[^1] > 20)) striche.Add(x);
        }
        if (striche.Count < 3) return null;
        int besch = striche[^3], anb = striche[^2], quelle = striche[^1];
        if (anb - besch < 150 || quelle - anb < 100 || b - quelle < 100) return null;

        // Trennlinien: eine Bildzeile, die von der Beschreibung bis in die Datenquelle durchgehend grau ist
        var linien = new List<int>();
        for (int y = kopf / 2; y < h; y++)
        {
            bool linie = true;
            for (int x = besch + 4; x < quelle + 60 && linie; x += 3) linie = Grau(p[y * b + x], 0xB0, 0xD8);
            if (linie && (linien.Count == 0 || y - linien[^1] > 3)) linien.Add(y);
        }
        if (linien.Count == 0) return null;
        int rechts = quelle + 60;
        while (rechts < b - 1 && Grau(p[linien[0] * b + rechts], 0xB0, 0xD8)) rechts++;

        var zeilen = new List<(int, int)>();
        // die oberste Zeile hat keine Linie ueber sich: ihre Hoehe ist die der anderen
        if (linien.Count >= 2)
        {
            int oben = linien[0] - (linien[1] - linien[0]) + 1;
            if (oben >= 20) zeilen.Add((oben, linien[0]));
        }
        for (int i = 1; i < linien.Count; i++) zeilen.Add((linien[i - 1] + 1, linien[i]));
        // nur ganze Zeilen: alle sind gleich hoch — was abweicht, ist unten angeschnitten oder keine Zeile
        if (zeilen.Count > 0)
        {
            int hoehe = zeilen.GroupBy(z => z.Item2 - z.Item1).OrderByDescending(g => g.Count()).ThenByDescending(g => g.Key).First().Key;
            zeilen.RemoveAll(z => Math.Abs(z.Item2 - z.Item1 - hoehe) > 1);
        }
        return new Raster(besch, anb, quelle, rechts, zeilen);
    }

    /// <summary>Hat die Zeile grauen Grund (angeklickt oder Maus darueber)? Dann stimmt der Schrift-Abgleich nicht.</summary>
    internal static bool Markiert(int[] p, int b, Raster r, (int Oben, int Unten) z)
    {
        int n = 0;
        foreach (int x in new[] { r.Beschreibung + 3, r.Anbieter - 6, r.Datenquelle - 6 })
            foreach (int y in new[] { z.Oben + 2, z.Unten - 3 })
                if (p[y * b + x] != Weiss) n++;
        return n >= 4;
    }

    /// <summary>Pruefsumme des Zeileninhalts (ohne Vorschaubild) — damit eine schon gelesene Zeile nicht noch einmal
    /// gelesen wird, wenn sie nur nach unten gerutscht ist.</summary>
    internal static ulong Summe(int[] p, int b, Raster r, (int Oben, int Unten) z)
    {
        ulong s = 14695981039346656037UL;
        for (int y = z.Oben; y < z.Unten; y++)
            for (int x = r.Beschreibung + 1; x < r.Rechts; x++)
            {
                s ^= (uint)p[y * b + x];
                s *= 1099511628211UL;
            }
        return s ^ (ulong)(z.Unten - z.Oben);
    }

    /// <summary>Zusammenhaengende Textbaender (von, bis) in einem Spaltenstueck; Baender mit hoechstens 2 leeren
    /// Bildzeilen dazwischen gehoeren zusammen (Umlautpunkte ueber Grossbuchstaben).</summary>
    internal static List<(int Von, int Bis)> Baender(int[] p, int b, int x0, int x1, int y0, int y1)
    {
        var liste = new List<(int, int)>();
        int von = -1, leer = 0;
        for (int y = y0; y <= y1; y++)
        {
            bool tinte = false;
            if (y < y1)
                for (int x = x0; x < x1 && !tinte; x++)
                {
                    int f = p[y * b + x];
                    tinte = (f & 0xFF) <= 200 || ((f >> 8) & 0xFF) <= 200 || ((f >> 16) & 0xFF) <= 200;
                }
            if (tinte)
            {
                if (von < 0) von = y;
                leer = 0;
            }
            else if (von >= 0 && (++leer > 2 || y == y1))
            {
                liste.Add((von, y - leer + 1));
                von = -1;
                leer = 0;
            }
        }
        return liste;
    }

    /// <summary>Nur zum Messen: jedes gelesene Stueck samt Ergebnis.</summary>
    internal static Action<Bitmap, string>? Probe;

    // Schriftgroessen der drei Textarten — einmal gefunden, bleiben sie (ein Fehlversuch ueber alle Groessen kostet ~0,2 s)
    private static int _pxDaten, _pxTitel, _pxTitelNeu, _pxPreis, _pxOrt;

    [ThreadStatic] private static bool _genau;

    private static string LiesBand(Bitmap bild, int x0, int x1, List<(int Von, int Bis)> baender, int i, int y0, int y1,
                                   bool fett, ref int px, bool auchLuecken = false)
    {
        var (von, bis) = baender[i];
        int oben = Math.Max(i > 0 ? baender[i - 1].Bis + 1 : y0, von - 6);
        int unten = Math.Min(i + 1 < baender.Count ? baender[i + 1].Von - 1 : y1, bis + 5);
        if (x1 - x0 < 8 || unten - oben < 6) return "";
        using var stueck = bild.Clone(new Rectangle(x0, oben, x1 - x0, unten - oben), PixelFormat.Format32bppRgb);
        var l = SchriftLeser.LiesFeld(stueck, amStueck: true, fett: fett, nurPx: px);
        Probe?.Invoke(stueck, $"{(fett ? "fett" : "normal")} px {px}: Zeilen {l?.Zeilen.Count}, Lücken {l?.Luecken}, „{l?.Text}“");
        if (l == null || l.Zeilen.Count != 1) return "";
        _genau = l.Luecken == 0;
        if (l.Luecken == 0) px = l.Px;
        else if (!auchLuecken) return "";
        return l.Text.Replace(SchriftLeser.Luecke.ToString(), "").Trim();
    }

    private static bool Rot(int f) => ((f >> 16) & 0xFF) > 185 && ((f >> 8) & 0xFF) < 80 && (f & 0xFF) < 80;

    /// <summary>Eine Zeile lesen. Was nicht genau aufgeht, bleibt leer bzw. null — nie geraten.</summary>
    internal static ListenZeile Lies(Bitmap bild, int[] p, Raster r, (int Oben, int Unten) z)
    {
        int b = bild.Width;
        string titel = "", art = "", daten = "", ausstattung = "";
        bool titelGenau = false;
        int? preis = null;

        // Spalte Beschreibung: oben der Titel (fett), unten der Preis (fett), dazwischen die Datenzeilen
        int bx0 = r.Beschreibung + 2, bx1 = r.Anbieter - 1;
        var baender = Baender(p, b, bx0, bx1, z.Oben, z.Unten);
        var normal = new List<string>();
        for (int i = 0; i < baender.Count; i++)
        {
            if (i == 0)
            {
                // die rote Marke „Neu“ vor dem Titel ueberspringen
                int ab = bx0;
                for (int x = bx0; x < bx0 + 70 && x < bx1; x++)
                    for (int y = baender[0].Von; y < baender[0].Bis; y++)
                        if (Rot(p[y * b + x])) { ab = x + 6; break; }
                // die Marke ist hoeher als die Schrift: das Band des Titels allein (rechts von der Marke) neu bestimmen
                int tUnten = baender.Count > 1 ? baender[1].Von - 1 : z.Unten;
                var tb = ab < bx1 ? Baender(p, b, ab, bx1, z.Oben, tUnten) : new List<(int Von, int Bis)>();
                if (tb.Count > 0)
                {
                    int gross = Enumerable.Range(0, tb.Count).OrderByDescending(k => tb[k].Bis - tb[k].Von).First();
                    titel = LiesBand(bild, ab, bx1, tb, gross, z.Oben, tUnten, fett: true,
                                     ref (ab != bx0 ? ref _pxTitelNeu : ref _pxTitel), auchLuecken: true);   // mit Marke kleiner gezeichnet
                    titelGenau = titel.Length > 0 && _genau;
                }
                if (titel.Length > 0 || baender.Count > 3) continue;
            }
            if (i == baender.Count - 1 && i > 0)
            {
                var m = Regex.Match(LiesBand(bild, bx0, bx1, baender, i, z.Oben, z.Unten, fett: true, ref _pxPreis),
                                    @"^(\d{1,3}(?:\.\d{3})*) EUR(?: \*)?$");
                if (m.Success) { preis = int.Parse(m.Groups[1].Value.Replace(".", "")); continue; }
            }
            string s = LiesBand(bild, bx0, bx1, baender, i, z.Oben, z.Unten, fett: false, ref _pxDaten);
            if (s.Length > 0) normal.Add(s);
        }
        int d = normal.FindIndex(s => s.StartsWith("EZ:", StringComparison.Ordinal) || Regex.IsMatch(s, @"\d Km(,|$)"));
        if (d >= 0)
        {
            daten = normal[d];
            if (d > 0) art = normal[d - 1];
            if (d + 1 < normal.Count) ausstattung = normal[d + 1];
        }
        else if (normal.Count > 0) art = normal[0];

        // Spalte Anbieter: nur die Plakette (rot = Privat, grau = Händler)
        int rot = 0, grau = 0;
        for (int y = z.Oben + (z.Unten - z.Oben) / 2; y < z.Unten; y++)
            for (int x = r.Anbieter + 2; x < Math.Min(r.Anbieter + 90, r.Datenquelle); x++)
            {
                int f = p[y * b + x];
                if (Rot(f)) rot++;
                else if (Grau(f, 0x44, 0x5C)) grau++;
            }
        bool? privat = rot >= 300 && rot > grau ? true : grau >= 300 && grau > rot ? false : null;

        // Spalte Anbieter, Text rechts neben den Symbolen: Name, Telefonnummern, Entfernung; in der Plakettenzeile der Ort
        // (Wunsch Ahmad 27.11.2026: Telefon-Knopf in der Trefferliste)
        string anbieter = "", ort = "";
        var telefone = new List<string>();
        int? entfernung = null;
        int ax0 = r.Anbieter + 26, ax1 = r.Datenquelle - 1;
        var zb = Baender(p, b, ax0, ax1, z.Oben, z.Unten);
        for (int i = 0; i < zb.Count; i++)
        {
            string s = LiesBand(bild, ax0, ax1, zb, i, z.Oben, z.Unten, fett: false, ref _pxDaten);
            if (s.Length == 0)
            {
                // die Plakettenzeile: der Ort steht rechts von der Plakette, in kleinerer Schrift
                int ox0 = r.Anbieter + 73;
                var ob = i == zb.Count - 1 && ox0 < ax1 ? Baender(p, b, ox0, ax1, zb[i].Von, zb[i].Bis) : new List<(int Von, int Bis)>();
                if (ob.Count > 0)
                {
                    int gross = Enumerable.Range(0, ob.Count).OrderByDescending(k => ob[k].Bis - ob[k].Von).First();
                    ort = LiesBand(bild, ox0, ax1, ob, gross, zb[i].Von - 4, zb[i].Bis + 4, fett: false, ref _pxOrt);
                }
                continue;
            }
            var m = Regex.Match(s, @"^~ ?(\d+) Km$");
            if (m.Success) entfernung = int.Parse(m.Groups[1].Value);
            else if (Regex.IsMatch(s, @"^\+?[\d ()/\-]{6,}$")) { if (telefone.Count < 3) telefone.Add(s); }
            else if (anbieter.Length == 0) anbieter = s;
        }

        // Spalte Datenquelle: Portal, darunter die Uhrzeit (das Logo darunter bleibt ungelesen)
        int qx0 = r.Datenquelle + 2, qx1 = r.Rechts;
        var qb = Baender(p, b, qx0, qx1, z.Oben, z.Unten);
        string portal = qb.Count > 0 ? LiesBand(bild, qx0, qx1, qb, 0, z.Oben, z.Unten, fett: false, ref _pxDaten) : "";
        string zeit = qb.Count > 1 ? LiesBand(bild, qx0, qx1, qb, 1, z.Oben, z.Unten, fett: false, ref _pxDaten) : "";
        if (!Regex.IsMatch(zeit, @"^\d\d\.\d\d\.\d{4} \d\d:\d\d:\d\d$")) zeit = "";

        return new ListenZeile(z.Oben, z.Unten, titel, art, daten, ausstattung, preis, privat, portal, zeit, titelGenau,
                               anbieter, telefone, entfernung, ort);
    }

    /// <summary>Alle ganz sichtbaren, nicht markierten Zeilen eines Listenbildes (fuer Tests und den Probelauf).</summary>
    internal static List<ListenZeile> LiesAlle(Bitmap bild)
    {
        var p = Punkte(bild);
        var r = FindeRaster(p, bild.Width, bild.Height);
        var liste = new List<ListenZeile>();
        if (r == null) return liste;
        foreach (var z in r.Zeilen)
            if (!Markiert(p, bild.Width, r, z)) liste.Add(Lies(bild, p, r, z));
        return liste;
    }
}
