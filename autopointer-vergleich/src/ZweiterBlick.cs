using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;

namespace AutoPointerVergleich;

/// <summary>Befund Ahmad 09.10.2026 (1.5.12): die Texterkennung verliest immer wieder genau die Werte, an denen der
/// Vergleich haengt — "Hyundai i30" als "Hyundai IBO", "VW T-Roc" als "VWT- Ro c" (Marke nicht gefunden), "Elektro" als
/// "EIektro", die Inserat-ID mit Buchstaben oder gar nicht. Der Server repariert vieles, aber am besten hilft ein zweiter,
/// unabhaengiger Blick: die WERTE der Zeilen "Marke, Modell", "Kraftstoff" und "Inserat-ID" werden aus DEMSELBEN Abbild
/// (kein neues Abbild, AutoPointer bleibt unberuehrt) einzeln ausgeschnitten und anders aufbereitet noch einmal gelesen.
/// Was dabei anders herauskommt, geht als <c>fahrzeug.alternativen</c> mit — der Server probiert es, wenn er den
/// Hauptwert nicht erkennt.</summary>
/// <remarks>
/// Kosten: alle Ausschnitte stehen untereinander in EINEM Sammelbild je Aufbereitung — zwei Texterkennungen insgesamt
/// (also hoechstens zwei je Feld), mit eigener Engine gleichzeitig. Die Lage der Zeilen kommt aus dem ersten Durchgang
/// (<see cref="DetailLeser.WertBereiche"/>); hat er keine der drei Zeilen gefunden, entfaellt der zweite Blick ganz.
/// Er darf nie haengen und nie den Vergleich kosten: hoechstens <see cref="TextErkennung.Frist"/> fuer alles, jeder
/// Fehler landet nur im Protokoll und der Vergleich geht ohne Alternativen raus.
/// </remarks>
internal static class ZweiterBlick
{
    /// <summary>1.5.16 (Wunsch Ahmad 13.10.2026): auch die Zeile "Hash-ID" (AutoScout) — ihre Lesungen gehen in die
    /// Mehrheit der Kennung (<see cref="InseratNummer"/>).</summary>
    internal static readonly Feld[] Felder = { Feld.MarkeModell, Feld.Kraftstoff, Feld.InseratId, Feld.HashId };
    /// <summary>Die Kennungs-Zeilen: nur sie bekommen die dritte Aufbereitung (<see cref="Aufbereitung.SchwarzWeiss"/>).</summary>
    internal static readonly Feld[] Kennungen = { Feld.InseratId, Feld.HashId };
    internal const int MaxJeFeld = 3;
    internal const int MaxLaenge = 160;

    /// <summary>Wie der Ausschnitt fuer die Texterkennung aufbereitet wird (der erste Durchgang: ganze Tabelle, Zoom 3,
    /// Farbe wie angezeigt).</summary>
    internal enum Aufbereitung
    {
        /// <summary>Zoom 5 (bei 96 dpi), Farbe wie angezeigt — Rand in der Hintergrundfarbe der Zeile.</summary>
        Gross,
        /// <summary>Zoom 4, Graustufen, Kontrast gestreckt: Hintergrund weiss, Schrift schwarz — auch bei farbig
        /// markierter Zeile oder heller Schrift auf dunklem Grund.</summary>
        Kontrast,
        /// <summary>1.5.16 (Wunsch Ahmad 13.10.2026, Inserat-Nummer sicher lesen): Zoom 6, Schwarz/Weiss mit Schwellwert
        /// (Kontrast gestreckt, dann jedes Pixel ganz weiss oder ganz schwarz) — nur fuer die Kennungs-Zeilen
        /// (<see cref="Kennungen"/>), als dritte, unabhaengige Lesung fuer die Mehrheit. Allein las sie in der Messung vom
        /// 09.10. schlechter als Gross/Kontrast; hier zaehlt, dass ihre Fehler ANDERE sind.</summary>
        SchwarzWeiss,
    }

    /// <summary>Reihenfolge der Aufbereitungen — die erste ist die sicherere (sie liefert z. B. die Inserat-ID, wenn der
    /// erste Durchgang keine hatte). Gemessen 09.10.2026 (<c>ZweiterBlickMessung</c>, 1.080 gezeichnete Tabellen): allein
    /// richtig Marke/Modell Gross x5 872 / Kontrast x4 848, Inserat-ID 1.069 / 1.060; als Paar zum ersten Durchgang
    /// gehoeren sie zu den besten (Schwellwert schwarz/weiss und Zoom 6 lasen schlechter — seit 1.5.16 trotzdem als
    /// DRITTE Lesung nur fuer die Kennungen, siehe <see cref="Aufbereitung.SchwarzWeiss"/>).</summary>
    internal static readonly Aufbereitung[] Reihenfolge = { Aufbereitung.Gross, Aufbereitung.Kontrast, Aufbereitung.SchwarzWeiss };

    /// <summary>Welche Felder in der Aufbereitung gelesen werden: Schwarz/Weiss nur die Kennungen, sonst alle.</summary>
    internal static bool Liest(Aufbereitung art, Feld feld) => art != Aufbereitung.SchwarzWeiss || Kennungen.Contains(feld);

    /// <summary>Vergroesserung je Aufbereitung (bei 96 dpi; bei hoeherer Skalierung entsprechend weniger, wie im
    /// ersten Durchgang).</summary>
    internal static double Zoom(Aufbereitung art, uint dpi)
    {
        double basis = art switch { Aufbereitung.Gross => 5.0, Aufbereitung.SchwarzWeiss => 6.0, _ => 4.0 };
        return Math.Clamp(basis * 96 / (dpi == 0 ? 96 : dpi), 2.0, basis);
    }

    /// <summary>Ein Feld im Sammelbild: senkrechter Streifen [Oben, Unten) in Pixeln des Sammelbilds.</summary>
    internal sealed record Platz(Feld Feld, double Oben, double Unten);

    /// <summary>Zweiter Blick fuer <paramref name="f"/> (setzt die Alternativen und — 1.5.16 — die Kennung per Mehrheit).
    /// Liefert den Text fuers Protokoll/Rohtext oder null (nichts gelesen, uebersprungen oder fehlgeschlagen — dann nur
    /// protokolliert; die Mehrheit der Kennung bildet der Aufrufer dann aus den Durchgaengen allein). Wirft nie.</summary>
    /// <param name="zeilen2">Zeilen eines zweiten Durchgangs (anderer Zoom) — nur fuer Felder, die der erste nicht fand.</param>
    /// <param name="ocr2">eigene Engine fuer die zweite Aufbereitung (gleichzeitig); null = nacheinander auf <paramref name="ocr"/>.</param>
    /// <param name="ocr3">1.5.16: eigene Engine fuer die dritte Aufbereitung (Schwarz/Weiss der Kennungen); null = auf
    /// einer der anderen nacheinander.</param>
    internal static async Task<string?> AnwendenAsync(Fahrzeug f, Bitmap technik, IReadOnlyList<OcrZeile> zeilen,
                                                      IReadOnlyList<OcrZeile>? zeilen2, uint dpi, TextErkennung ocr,
                                                      TextErkennung? ocr2, TextErkennung? ocr3 = null)
    {
        try
        {
            var bereiche = DetailLeser.WertBereiche(zeilen, technik.Width, technik.Height, Felder);
            if (zeilen2 != null)
                foreach (var (feld, r) in DetailLeser.WertBereiche(zeilen2, technik.Width, technik.Height, Felder))
                    bereiche.TryAdd(feld, r);
            if (bereiche.Count == 0) return null;          // keine der Zeilen gefunden: kein zweiter Blick
            var liste = Felder.Where(bereiche.ContainsKey).Select(x => (x, bereiche[x])).ToList();
            var gelesen = await LesenAsync(technik, liste, dpi, ocr, ocr2, ocr3);
            Uebernehmen(f, gelesen);
            return Rohtext(gelesen);
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe($"Zweiter Blick fehlgeschlagen – Vergleich ohne Alternativen: {ex.GetType().Name}: {ex.Message}");
            return null;
        }
    }

    /// <summary>Alle Aufbereitungen lesen (je ein Sammelbild, je eine Texterkennung; Schwarz/Weiss nur, wenn eine
    /// Kennungs-Zeile dabei ist). Ergebnis je Feld: die rohen Lesungen in <see cref="Reihenfolge"/> (null = in dieser
    /// Aufbereitung nichts gelesen oder das Feld gehoert nicht zu ihr).</summary>
    /// <param name="ocr3">1.5.16: dritte Engine; Bilder werden reihum auf die vorhandenen Engines verteilt — jede Engine
    /// liest ihre Bilder nacheinander, die Engines untereinander gleichzeitig.</param>
    internal static async Task<Dictionary<Feld, List<string?>>> LesenAsync(Bitmap technik, IReadOnlyList<(Feld Feld, Rectangle Bereich)> bereiche,
                                                                          uint dpi, TextErkennung ocr, TextErkennung? ocr2,
                                                                          TextErkennung? ocr3 = null)
    {
        // Bilder hier (synchron, auf diesem Faden) aus dem Abbild bauen — GDI+-Bitmaps duerfen nicht gleichzeitig
        // gelesen werden; danach arbeitet jede Engine nur noch auf ihrem eigenen Sammelbild
        var bilder = new List<(Aufbereitung Art, Bitmap Bild, List<Platz> Plaetze)>();
        try
        {
            foreach (var art in Reihenfolge)
            {
                var dazu = bereiche.Where(b => Liest(art, b.Feld)).ToList();
                if (dazu.Count == 0) continue;
                var (bild, plaetze) = Sammelbild(technik, dazu, Zoom(art, dpi), art);
                bilder.Add((art, bild, plaetze));
            }
        }
        catch
        {
            foreach (var b in bilder) b.Bild.Dispose();
            throw;
        }

        // Pruefung 05.10.2026 (Paket 3, F1) / 1.5.16: je Engine ein eigener Faden (auch das Hochskalieren laeuft so
        // nebeneinander); Bild i geht an Engine i mod n. Task.WhenAll wartet auf ALLE, auch wenn eine wirft — sonst
        // wuerde einer noch laufenden Engine ihr Bild unter ihr weg freigegeben.
        var engines = new[] { ocr, ocr2, ocr3 }.OfType<TextErkennung>().Distinct().ToList();
        async Task<List<OcrZeile>[]> Alle()
        {
            var ergebnisse = new List<OcrZeile>[bilder.Count];
            async Task Nacheinander(int start)
            {
                for (int i = start; i < bilder.Count; i += engines.Count)
                    ergebnisse[i] = await engines[start].LiesAsync(bilder[i].Bild, 1.0);
            }
            var auftraege = new List<Task>();
            for (int e = 0; e < engines.Count && e < bilder.Count; e++)
            {
                int start = e;
                auftraege.Add(e == 0 ? Nacheinander(0) : Task.Run(() => Nacheinander(start)));
            }
            await Task.WhenAll(auftraege);
            return ergebnisse;
        }

        void Freigeben() { foreach (var b in bilder) b.Bild.Dispose(); }
        bool haengt = false;
        List<OcrZeile>[] ergebnisse;
        try
        {
            // hoechstens so lange wie eine Texterkennung — haengt sie, gilt der zweite Blick als fehlgeschlagen; die
            // Bilder werden erst freigegeben, wenn sie doch noch fertig wird.
            // Pruefung 09.10.2026 (Befund 9): dass eine haengende Engine keinen neuen Auftrag mehr bekommt, sichert
            // TextErkennung.LiesAsync je Engine (Haengewache) — hier wirft sie dann sofort, der zweite Blick entfaellt.
            ergebnisse = await TextErkennung.MitFrist(Alle(), TextErkennung.Frist, Freigeben);
        }
        catch (TimeoutException) { haengt = true; throw; }
        finally { if (!haengt) Freigeben(); }

        // je Feld genau ein Eintrag je Aufbereitung in Reihenfolge (null, wo die Aufbereitung das Feld nicht liest)
        var gelesen = bereiche.ToDictionary(b => b.Feld, _ => new List<string?>());
        foreach (var art in Reihenfolge)
        {
            int i = bilder.FindIndex(b => b.Art == art);
            var je = i < 0 ? new Dictionary<Feld, string>() : Zuordnen(ergebnisse[i], bilder[i].Plaetze);
            foreach (var feld in gelesen.Keys) gelesen[feld].Add(je.GetValueOrDefault(feld));
        }
        return gelesen;
    }

    /// <summary>Alle Ausschnitte untereinander in einem Bild, jeder mit Rand in seiner Hintergrundfarbe ueber die ganze
    /// Breite (keine Kanten neben der Schrift, die als "I" oder "|" gelesen werden koennten), vergroessert um
    /// <paramref name="faktor"/>. Liefert das Bild und wo jedes Feld darin steht.</summary>
    internal static (Bitmap Bild, List<Platz> Plaetze) Sammelbild(Bitmap technik, IReadOnlyList<(Feld Feld, Rectangle Bereich)> bereiche,
                                                                  double faktor, Aufbereitung art)
    {
        int rand = (int)Math.Ceiling(8 * faktor);
        var teile = new List<(Feld Feld, Bitmap Bild, Color Hintergrund)>();
        try
        {
            var ganz = new Rectangle(0, 0, technik.Width, technik.Height);
            foreach (var (feld, bereich) in bereiche)
            {
                var r = Rectangle.Intersect(bereich, ganz);
                if (r.Width < 1 || r.Height < 1) continue;
                var (bild, hintergrund) = Aufbereiten(technik, r, faktor, art);
                teile.Add((feld, bild, hintergrund));
            }
            if (teile.Count == 0) throw new InvalidOperationException("Kein Ausschnitt im Bild.");
            int breite = teile.Max(t => t.Bild.Width) + 2 * rand;
            int hoehe = teile.Sum(t => t.Bild.Height + 2 * rand);
            var sammel = new Bitmap(breite, hoehe, PixelFormat.Format32bppArgb);
            var plaetze = new List<Platz>();
            using (var g = Graphics.FromImage(sammel))
            {
                g.Clear(Color.White);
                int y = 0;
                foreach (var (feld, bild, hintergrund) in teile)
                {
                    int streifen = bild.Height + 2 * rand;
                    using (var pinsel = new SolidBrush(hintergrund)) g.FillRectangle(pinsel, 0, y, breite, streifen);
                    g.DrawImage(bild, new Rectangle(rand, y + rand, bild.Width, bild.Height));
                    plaetze.Add(new Platz(feld, y, y + streifen));
                    y += streifen;
                }
            }
            return (sammel, plaetze);
        }
        finally
        {
            foreach (var t in teile) t.Bild.Dispose();
        }
    }

    /// <summary>Einen Ausschnitt vergroessern (bikubisch, ohne dunkle Raender) und je nach <paramref name="art"/>
    /// aufbereiten. Liefert auch die Hintergrundfarbe (haeufigste Farbe am Rand des Ausschnitts).</summary>
    private static (Bitmap Bild, Color Hintergrund) Aufbereiten(Bitmap technik, Rectangle r, double faktor, Aufbereitung art)
    {
        Color hintergrund = Randfarbe(technik, r);
        int b = Math.Max(1, (int)Math.Round(r.Width * faktor)), h = Math.Max(1, (int)Math.Round(r.Height * faktor));
        var gross = new Bitmap(b, h, PixelFormat.Format32bppArgb);
        try
        {
            using (var g = Graphics.FromImage(gross))
            using (var attr = new ImageAttributes())
            {
                // TileFlipXY: am Rand des Ausschnitts nicht mit Schwarz/Transparenz mischen (sonst dunkle Kanten)
                attr.SetWrapMode(WrapMode.TileFlipXY);
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                g.DrawImage(technik, new Rectangle(0, 0, b, h), r.X, r.Y, r.Width, r.Height, GraphicsUnit.Pixel, attr);
            }
            if (art is Aufbereitung.Kontrast or Aufbereitung.SchwarzWeiss)
            {
                KontrastStrecken(gross, Helligkeit(hintergrund), schwellwert: art == Aufbereitung.SchwarzWeiss);
                hintergrund = Color.White;
            }
            return (gross, hintergrund);
        }
        catch
        {
            gross.Dispose();
            throw;
        }
    }

    private static int Helligkeit(Color c) => (c.R * 299 + c.G * 587 + c.B * 114 + 500) / 1000;
    private static int Helligkeit(byte r, byte g, byte b) => (r * 299 + g * 587 + b * 114 + 500) / 1000;

    /// <summary>Haeufigste Farbe auf dem aeussersten Pixelring des Ausschnitts — der Zeilenhintergrund (die Schrift
    /// beruehrt den Rand kaum, der Ausschnitt hat Rand).</summary>
    internal static Color Randfarbe(Bitmap bild, Rectangle r)
    {
        var zaehler = new Dictionary<int, int>();
        void Zaehle(int x, int y)
        {
            int argb = bild.GetPixel(x, y).ToArgb() | unchecked((int)0xFF000000);
            zaehler[argb] = zaehler.GetValueOrDefault(argb) + 1;
        }
        for (int x = r.Left; x < r.Right; x++) { Zaehle(x, r.Top); Zaehle(x, r.Bottom - 1); }
        for (int y = r.Top + 1; y < r.Bottom - 1; y++) { Zaehle(r.Left, y); Zaehle(r.Right - 1, y); }
        return zaehler.Count == 0 ? Color.White : Color.FromArgb(zaehler.MaxBy(k => k.Value).Key);
    }

    /// <summary>Graustufen, Hintergrund (<paramref name="hintergrund"/> = dessen Helligkeit) -> weiss, Schrift -> schwarz,
    /// dazwischen linear. Die Schrift ist das Ende der Helligkeiten (2 %/98 %), das am weitesten vom Hintergrund
    /// liegt — helle Schrift auf dunkler Markierung wird so umgedreht.</summary>
    /// <param name="schwellwert">1.5.16 (Schwarz/Weiss): statt linear jedes Pixel ganz schwarz (naeher an der Schrift) oder
    /// ganz weiss (naeher am Hintergrund) — Mitte der gestreckten Spanne.</param>
    internal static void KontrastStrecken(Bitmap bild, int hintergrund, bool schwellwert = false)
    {
        var daten = bild.LockBits(new Rectangle(0, 0, bild.Width, bild.Height), ImageLockMode.ReadWrite, PixelFormat.Format32bppArgb);
        try
        {
            int n = daten.Stride * bild.Height;
            var puffer = new byte[n];
            System.Runtime.InteropServices.Marshal.Copy(daten.Scan0, puffer, 0, n);
            var hist = new int[256];
            int pixel = 0;
            for (int y = 0; y < bild.Height; y++)
                for (int x = 0, i = y * daten.Stride; x < bild.Width; x++, i += 4, pixel++)
                    hist[Helligkeit(puffer[i + 2], puffer[i + 1], puffer[i])]++;
            int Quantil(double q)
            {
                long ziel = (long)Math.Ceiling(pixel * q), summe = 0;
                for (int v = 0; v < 256; v++) { summe += hist[v]; if (summe >= ziel) return v; }
                return 255;
            }
            int dunkel = Quantil(0.02), hell = Quantil(0.98);
            int schrift = Math.Abs(hintergrund - dunkel) >= Math.Abs(hell - hintergrund) ? dunkel : hell;
            double spanne = hintergrund - schrift;
            for (int y = 0; y < bild.Height; y++)
                for (int x = 0, i = y * daten.Stride; x < bild.Width; x++, i += 4)
                {
                    byte v;
                    if (Math.Abs(spanne) < 8) v = 255;     // kein Kontrast: nichts zu lesen
                    else
                    {
                        double t = (Helligkeit(puffer[i + 2], puffer[i + 1], puffer[i]) - schrift) / spanne;
                        v = schwellwert ? (byte)(t < 0.5 ? 0 : 255) : (byte)Math.Round(Math.Clamp(t, 0, 1) * 255);
                    }
                    puffer[i] = puffer[i + 1] = puffer[i + 2] = v;
                    puffer[i + 3] = 255;
                }
            System.Runtime.InteropServices.Marshal.Copy(puffer, 0, daten.Scan0, n);
        }
        finally { bild.UnlockBits(daten); }
    }

    /// <summary>Gelesene Zeilen des Sammelbilds -> Text je Feld (nach der Mitte der Zeile; ein Streifen enthaelt nur
    /// eine Tabellenzeile, mehrere Stuecke davon also von links nach rechts). (rein, fuer Tests)</summary>
    internal static Dictionary<Feld, string> Zuordnen(IReadOnlyList<OcrZeile> zeilen, IReadOnlyList<Platz> plaetze)
    {
        var ergebnis = new Dictionary<Feld, string>();
        foreach (var platz in plaetze)
        {
            var stuecke = zeilen.Where(z => z.MitteY >= platz.Oben && z.MitteY < platz.Unten && z.Text.Trim().Length > 0)
                .OrderBy(z => z.X).Select(z => z.Text.Trim()).ToList();
            if (stuecke.Count > 0) ergebnis[platz.Feld] = string.Join(" ", stuecke);
        }
        return ergebnis;
    }

    /// <summary>Eine Lesung des zweiten Blicks saeubern — wie der Hauptwert: Inserat-ID nur Buchstaben/Ziffern/Bindestrich
    /// (<see cref="DetailLeser.InseratIdSauber"/>), sonst <see cref="DetailLeser.Sauber"/>. Ein mitgelesener Rest der
    /// Bezeichnung ("ff: Diesel") faellt weg wie beim ersten Durchgang (Wert = nach dem Doppelpunkt). Hoechstens
    /// <see cref="MaxLaenge"/> Zeichen; null = nichts Brauchbares. (rein, fuer Tests)</summary>
    internal static string? Saeubern(Feld feld, string? roh)
    {
        if (string.IsNullOrWhiteSpace(roh)) return null;
        int dp = roh.LastIndexOf(':');
        if (dp >= 0) roh = roh[(dp + 1)..];
        string? s = feld == Feld.InseratId ? DetailLeser.InseratIdSauber(roh) : DetailLeser.Sauber(roh);
        if (s != null && s.Length > MaxLaenge) s = s[..MaxLaenge].Trim();
        return string.IsNullOrEmpty(s) ? null : s;
    }

    /// <summary>Gesaeuberte Lesungen, die vom Hauptwert <paramref name="primaer"/> abweichen (Gross-/Kleinschreibung
    /// zaehlt — "EIektro" ist nicht "Elektro"), ohne Doppelte, hoechstens <see cref="MaxJeFeld"/>. (rein, fuer Tests)</summary>
    internal static List<string> Alternativen(Feld feld, string? primaer, IEnumerable<string?> gelesen)
    {
        var liste = new List<string>();
        foreach (var roh in gelesen)
        {
            var s = Saeubern(feld, roh);
            if (s == null || string.Equals(s, primaer, StringComparison.Ordinal) || liste.Contains(s, StringComparer.Ordinal)) continue;
            liste.Add(s);
            if (liste.Count == MaxJeFeld) break;
        }
        return liste;
    }

    /// <summary>Lesungen ins Fahrzeug: Alternativen je Feld. Die Kennung (Inserat-ID, Hash-ID) entscheidet seit 1.5.16
    /// die Mehrheit ueber alle Lesungen (<see cref="InseratNummer.Uebernehmen"/>): sicher nur, wenn zwei Lesungen Zeichen
    /// fuer Zeichen gleich sind und die Stellenzahl passt — sonst die plausibelste als Hauptwert, der Rest als
    /// Alternativen und <see cref="Fahrzeug.InseratIdUnsicher"/>. (rein, fuer Tests)</summary>
    internal static void Uebernehmen(Fahrzeug f, IReadOnlyDictionary<Feld, List<string?>> gelesen)
    {
        IEnumerable<string?> Je(Feld feld) => gelesen.TryGetValue(feld, out var l) ? l : Enumerable.Empty<string?>();
        f.AlternativenMarkeModell = Alternativen(Feld.MarkeModell, f.MarkeModellText, Je(Feld.MarkeModell));
        // Wunsch Ahmad 09.10.2026 (1.5.14, Elektroautos): eine Alternative, die der erste Durchgang schon kannte (Elektro-
        // Kennzeichen im Titel, DetailLeser.ElektroKennzeichen), bleibt vorne — der zweite Blick ersetzt sie nicht
        f.AlternativenKraftstoff = Alternativen(Feld.Kraftstoff, f.Kraftstoff, f.AlternativenKraftstoff.Concat(Je(Feld.Kraftstoff)));
        InseratNummer.Uebernehmen(f, Je(Feld.InseratId), Je(Feld.HashId));
    }

    private static string Rohtext(Dictionary<Feld, List<string?>> gelesen)
    {
        static string Name(Feld f) => f switch
        {
            Feld.MarkeModell => "Marke/Modell",
            Feld.InseratId => "Inserat-ID",
            Feld.HashId => "Hash-ID",
            _ => f.ToString(),
        };
        return string.Join("; ", gelesen.Select(kv => Name(kv.Key) + " " + string.Join(" / ", kv.Value.Select(v => "„" + (v ?? "") + "“"))));
    }
}
