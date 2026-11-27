using System.Drawing;
using System.Drawing.Imaging;

namespace AutoPointerVergleich;

/// <summary>Liest die Tabelle „Technische Daten“ und die Liste „Ausstattung“ der Detailansicht mit dem Schrift-Abgleich
/// (<see cref="SchriftLeser"/>) — Buchstabe fuer Buchstabe, ohne Texterkennung (Wunsch Ahmad 27.11.2026: der Kaufvertrag
/// soll mit ALLEN Daten aus AutoPointer vorausgefuellt sein; „es darf keine Lesefehler geben“).</summary>
/// <remarks>Gemessen am 27.11.2026 (AutoPointer 3.9.9.9341, 125 %): die Tabelle ist gestreift (Zeilen abwechselnd
/// F9F9F9 und FFFFFF), getrennt durch Linien in E5E5E5; links die Bezeichnung (Tahoma 14 px), rechts der Wert (fett).
/// Die Ausstattung steht in drei Spalten auf Weiss, ohne Linien. Was nicht genau aufgeht, fehlt im Ergebnis — nie geraten.</remarks>
internal static class TabellenLeser
{
    private static int _pxName, _pxWert, _pxAusstattung;

    private static bool Linie(int farbe)
    {
        int r = (farbe >> 16) & 0xFF, g = (farbe >> 8) & 0xFF, b = farbe & 0xFF;
        return Math.Abs(r - g) <= 3 && Math.Abs(g - b) <= 3 && g >= 0xD8 && g <= 0xEE;
    }

    /// <summary>Zeilen (oben einschliesslich, unten ausschliesslich) zwischen den waagerechten Trennlinien.</summary>
    internal static List<(int Oben, int Unten)> Zeilen(int[] p, int b, int h)
    {
        var linien = new List<int>();
        for (int y = 0; y < h; y++)
        {
            bool linie = true;
            for (int x = 4; x < b - 24 && linie; x += 5) linie = Linie(p[y * b + x]);
            if (linie) linien.Add(y);
        }
        var zeilen = new List<(int, int)>();
        int oben = 0;
        foreach (int y in linien)
        {
            if (y - oben >= 10) zeilen.Add((oben, y));
            oben = y + 1;
        }
        if (h - oben >= 10) zeilen.Add((oben, h));
        return zeilen;
    }

    /// <summary>Zusammenhaengende Stuecke Schrift in einer Zeile: [von, bis) je Stueck; eine Luecke ab
    /// <paramref name="luecke"/> Spalten trennt zwei Stuecke.</summary>
    internal static List<(int Von, int Bis)> Stuecke(int[] p, int b, int oben, int unten, int grund, int luecke, int x0 = 0, int x1 = -1)
    {
        if (x1 < 0) x1 = b;
        var liste = new List<(int, int)>();
        int von = -1, leer = 0;
        for (int x = x0; x <= x1; x++)
        {
            bool tinte = false;
            if (x < x1)
                // der senkrechte Trennstrich zwischen Bezeichnung und Wert (E5E5E5) ist keine Schrift
                for (int y = oben; y < unten && !tinte; y++) tinte = p[y * b + x] != grund && !Linie(p[y * b + x]);
            if (tinte)
            {
                if (von < 0) von = x;
                leer = 0;
            }
            else if (von >= 0 && (++leer >= luecke || x == x1))
            {
                liste.Add((von, x - leer + 1));
                von = -1;
                leer = 0;
            }
        }
        return liste;
    }

    private static string Lies(Bitmap bild, int x0, int x1, int oben, int unten, bool fett, int grund, ref int px)
    {
        x0 = Math.Max(0, x0 - 4);
        x1 = Math.Min(bild.Width, x1 + 6);
        if (x1 - x0 < 4 || unten - oben < 6) return "";
        using var stueck = bild.Clone(new Rectangle(x0, oben, x1 - x0, unten - oben), PixelFormat.Format32bppRgb);
        var l = SchriftLeser.LiesFeld(stueck, amStueck: true, fett: fett, nurPx: px, grund: grund);
        if (l is not { Luecken: 0, Zeilen.Count: 1 }) return "";
        px = l.Px;
        return l.Text;
    }

    /// <summary>„Technische Daten“: Bezeichnung (ohne Doppelpunkt) → Wert, in der Reihenfolge der Tabelle. Zeilen, die
    /// nicht genau aufgehen (angeschnitten, verdeckt), fehlen.</summary>
    internal static List<KeyValuePair<string, string>> Technik(Bitmap bild)
    {
        int b = bild.Width, h = bild.Height;
        var p = ListenLeser.Punkte(bild);
        var raus = new List<KeyValuePair<string, string>>();
        foreach (var (oben, unten) in Zeilen(p, b, h))
        {
            int grund = p[(oben + 1) * b + 2];
            // rechts kann ein Rollbalken stehen (die letzten ~22 Spalten): was dort beginnt, ist kein Text
            var st = Stuecke(p, b, oben, unten, grund, 14).Where(x => x.Von < b - 26).ToList();
            if (st.Count < 2) continue;
            string name = Lies(bild, st[0].Von, st[0].Bis, oben, unten, false, grund, ref _pxName).TrimEnd(':').Trim();
            string wert = Lies(bild, st[1].Von, st[^1].Bis, oben, unten, true, grund, ref _pxWert).Trim();
            if (name.Length > 0 && wert.Length > 0) raus.Add(new(name, wert));
        }
        return raus;
    }

    /// <summary>„Ausstattung“: alle Eintraege (zeilenweise von links nach rechts), ohne Doppelte.</summary>
    internal static List<string> Ausstattung(Bitmap bild)
    {
        const int weiss = 0xFFFFFF;
        int b = bild.Width;
        var p = ListenLeser.Punkte(bild);
        var raus = new List<string>();
        foreach (var (von, bis) in ListenLeser.Baender(p, b, 0, b, 0, bild.Height))
        {
            int oben = Math.Max(0, von - 6), unten = Math.Min(bild.Height, bis + 5);
            foreach (var (x0, x1) in Stuecke(p, b, von, bis, weiss, 18))
            {
                string s = Lies(bild, x0, x1, oben, unten, false, weiss, ref _pxAusstattung).Trim();
                if (s.Length > 1 && !raus.Contains(s)) raus.Add(s);
            }
        }
        return raus;
    }

    // ---- Werte aus den Merkmalen ----------------------------------------------------------

    internal static string? Wert(IEnumerable<KeyValuePair<string, string>> merkmale, params string[] namen)
    {
        foreach (var m in merkmale)
            if (namen.Any(n => m.Key.Equals(n, StringComparison.OrdinalIgnoreCase)) && m.Value.Trim().Length > 0) return m.Value.Trim();
        return null;
    }

    private static int? Zahl(string? text, string muster)
    {
        if (text == null) return null;
        var m = System.Text.RegularExpressions.Regex.Match(text, muster);
        return m.Success && int.TryParse(m.Groups[1].Value.Replace(".", ""), out int n) ? n : null;
    }

    /// <summary>Was die exakt gelesene Tabelle hergibt, ersetzt die Werte der Texterkennung (nur vorhandene Zeilen).
    /// (rein, fuer Tests)</summary>
    internal static void Uebernehmen(Fahrzeug f, IReadOnlyList<KeyValuePair<string, string>> m)
    {
        if (Wert(m, "Marke, Modell") is { } mm) { f.MarkeModellText = mm; f.MarkeModellAusTitel = false; }
        if (Zahl(Wert(m, "Preis"), @"^(\d{1,3}(?:\.\d{3})*|\d+) EUR") is { } preis) f.Preis = preis;
        if (Zahl(Wert(m, "Kilometerstand"), @"^(\d{1,3}(?:\.\d{3})*|\d+) km") is { } km) f.Kilometer = km;
        if (Wert(m, "Erstzulassung") is { } ez && System.Text.RegularExpressions.Regex.Match(ez, @"^(\d{1,2})/(\d{4})$") is { Success: true } e)
        {
            f.EzMonat = int.Parse(e.Groups[1].Value);
            f.EzJahr = int.Parse(e.Groups[2].Value);
        }
        if (Wert(m, "Leistung") is { } leistung)
        {
            if (Zahl(leistung, @"(\d+) kW") is { } kw) f.Kw = kw;
            if (Zahl(leistung, @"\((\d+) PS\)") is { } ps) f.Ps = ps;
            f.LeistungUnsicher = false;
        }
        if (Wert(m, "Kraftstoff") is { } kraftstoff) f.Kraftstoff = kraftstoff;
        if (Wert(m, "Getriebeart", "Getriebe") is { } getriebe) f.Getriebe = getriebe;
        if (Wert(m, "Kategorie") is { } kategorie) f.Kategorie = kategorie;
        if (Wert(m, "Zustand") is { } zustand) f.Zustand = zustand;
        if (Wert(m, "Türen") is { } tueren) f.Tueren = tueren;
        if (Wert(m, "Inserat-ID") is { } nummer) { f.InseratId = nummer; f.InseratIdUnsicher = false; f.AlternativenInseratId.Clear(); }
        if (Wert(m, "Hash-ID") is { } hash && !hash.EndsWith("...", StringComparison.Ordinal)) f.HashId = hash;
    }
}
