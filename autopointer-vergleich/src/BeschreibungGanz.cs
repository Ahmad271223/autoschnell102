using System.Drawing;
using System.Drawing.Imaging;

namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 27.11.2026 (1.5.22, „Beschreibung aus AutoPointer holen" — fuer mobile.de, wo die Inseratseite
/// im Browser 3–20 s braucht): die GANZE Beschreibung lesen, nicht nur den sichtbaren Anfang.</summary>
/// <remarks>
/// Gemessen am 27.11.2026 (AutoPointer Premium 3.9.9.9341): das Textfeld unter „Beschreibung" (THTMLStaticText) gibt
/// seinen Text weder ueber WM_GETTEXT noch ueber die Bedienhilfen her — aber es ist immer so hoch wie der ganze Text
/// und liegt in einem Rollbereich (TcxScrollBox). Im Reiter „Übersicht" liegt es oft ganz unter dem sichtbaren Rand
/// (dann las das Programm bisher GAR NICHTS davon). Deshalb: den Rollbereich in Rasten nach unten rollen
/// (WM_MOUSEWHEEL per PostMessage — dieselbe Nachricht wie das Mausrad, keine Maus, kein Fokus), nach jeder Raste den
/// sichtbaren Streifen des Feldes abfotografieren (BitBlt, wie alle Lesebilder) und an seiner echten Stelle in EIN Bild
/// setzen; Lage und Hoehe meldet Windows genau. Danach wieder ganz nach oben rollen — die Detailansicht steht dann wie
/// vorher, die Pruefsumme der Tabellen stimmt wieder.
/// Gerollt wird NUR, wenn AutoPointer vorne liegt, der Bereich am Anfang steht (hat der Sucher selbst gerollt, bleibt
/// seine Stelle unberuehrt) und die eigene Leiste nicht davor liegt. Sonst gilt, was ohne Rollen sichtbar ist.
/// </remarks>
internal static class BeschreibungGanz
{
    internal const string RollbereichKlasse = "TcxScrollBox";
    /// <summary>So viele Rasten hoechstens nach unten (eine Raste ≈ 30–40 Bildpunkte: reicht fuer sehr lange Texte).</summary>
    internal const int MaxRasten = 60;
    /// <summary>So viele Zeichen gehen hoechstens an den Server (dort max. 8000).</summary>
    internal const int MaxZeichen = 6000;
    /// <summary>Hoechste Stueckhoehe fuer die Texterkennung (Bildpunkte im Original).</summary>
    internal const int StueckHoehe = 1400;

    /// <summary>Vergroesserung vor der Texterkennung (bei 96 dpi) — wie bei den Tabellen (3): die Schrift ist klein.</summary>
    internal static double Vergroesserung { get; set; } = 3.0;

    /// <summary>Nur fuer "--einmal --ganz --bilder": das zusammengesetzte Feldbild hier ablegen (zum Messen).</summary>
    internal static string? BildSpeichern { get; set; }

    /// <summary>Nur fuer "--einmal --ganz": Lage und Entscheidung ins Protokoll.</summary>
    internal static bool Diagnose { get; set; }

    // ---- rein (fuer Tests) ---------------------------------------------------

    /// <summary>Der Teil des Feldes, der im Rollbereich zu sehen ist: erste Zeile (ab Feld-Oberkante) und Hoehe.
    /// null = nichts zu sehen.</summary>
    internal static (int Von, int Hoehe)? Streifen(int feldOben, int feldHoehe, int sichtOben, int sichtUnten)
    {
        int oben = Math.Max(feldOben, sichtOben);
        int unten = Math.Min(feldOben + feldHoehe, sichtUnten);
        return unten > oben ? (oben - feldOben, unten - oben) : null;
    }

    /// <summary>Wie viele Rasten auf einmal? So viele, dass keine Zeile des Feldes ungesehen oben hinausrollt:
    /// liegt das Feld noch unter dem Rand, hoechstens bis seine Oberkante oben ankommt; ist es schon zu sehen,
    /// hoechstens eine Sichthoehe (etwas weniger, damit sich die Streifen ueberlappen). Und nie weiter als noetig.
    /// <paramref name="jeRaste"/> = gemessener Weg einer Raste in Bildpunkten; 0 = noch unbekannt (dann eine Raste).</summary>
    internal static int Rasten(int feldOben, int feldUnten, int sichtOben, int sichtUnten, int jeRaste)
    {
        if (jeRaste <= 0) return 1;
        int erlaubt = feldOben >= sichtUnten ? feldOben - sichtOben : (sichtUnten - sichtOben) - 12;
        int noetig = feldUnten - sichtUnten;
        int n = Math.Min(erlaubt / jeRaste, (noetig + jeRaste - 1) / jeRaste);
        return Math.Clamp(n, 1, 30);
    }

    /// <summary>Welche Zeilen des Feldes schon fotografiert sind.</summary>
    internal sealed class Abdeckung
    {
        private readonly bool[] _zeilen;
        public Abdeckung(int hoehe) => _zeilen = new bool[Math.Max(0, hoehe)];
        public void Merke(int von, int hoehe)
        {
            for (int y = Math.Max(0, von); y < Math.Min(_zeilen.Length, von + hoehe); y++) _zeilen[y] = true;
        }
        public bool Vollstaendig => Array.TrueForAll(_zeilen, z => z);
        public int Fehlend => _zeilen.Count(z => !z);
        /// <summary>Erste fotografierte Zeile und Zeile hinter der letzten (0,0 = nichts).</summary>
        public (int Von, int Bis) Bereich()
        {
            int von = Array.IndexOf(_zeilen, true);
            return von < 0 ? (0, 0) : (von, Array.LastIndexOf(_zeilen, true) + 1);
        }
    }

    /// <summary>Ein hohes Bild in Stuecke fuer die Texterkennung teilen — geschnitten wird in einer LEEREN Zeile
    /// (zwischen zwei Textzeilen), hoechstens <paramref name="suche"/> Zeilen ueber der Grenze; gibt es dort keine,
    /// hart an der Grenze. <paramref name="leer"/>[y] = Zeile y enthaelt keine Schrift.</summary>
    internal static List<(int Von, int Hoehe)> Stuecke(bool[] leer, int maxHoehe, int suche = 80)
    {
        var liste = new List<(int, int)>();
        int von = 0;
        while (von < leer.Length)
        {
            int bis = Math.Min(leer.Length, von + maxHoehe);
            if (bis < leer.Length)
                for (int y = bis; y > Math.Max(von + 1, bis - suche); y--)
                    if (leer[y - 1]) { bis = y; break; }
            liste.Add((von, bis - von));
            von = bis;
        }
        return liste;
    }

    /// <summary>Erkannte Zeilen zu EINEM Text, je Zeile ein Zeilenumbruch (die Schadenspruefung des Servers urteilt
    /// je Satzteil — eine Verneinung darf nicht in die naechste Zeile reichen), hoechstens <see cref="MaxZeichen"/>.</summary>
    internal static string? Zusammen(IEnumerable<string> zeilen)
    {
        string text = string.Join("\n",zeilen.Select(z => z.Trim()).Where(z => z.Length > 0));
        return text.Length == 0 ? null : text.Length > MaxZeichen ? text[..MaxZeichen] : text;
    }

    // ---- Fenster ---------------------------------------------------------------

    /// <summary>Der Rollbereich, in dem das Feld liegt (hoechstens 6 Ebenen hoch), oder Zero.</summary>
    internal static IntPtr Rollbereich(IntPtr feld)
    {
        IntPtr h = feld;
        for (int i = 0; i < 6 && h != IntPtr.Zero; i++)
        {
            h = Native.GetParent(h);
            if (h != IntPtr.Zero && Native.Klasse(h) == RollbereichKlasse) return h;
        }
        return IntPtr.Zero;
    }

    private sealed record Lage(Native.RECT Feld, int SichtOben, int SichtUnten);

    private static Lage? Miss(IntPtr feld, IntPtr box) => Native.ImDpiKontext(feld, () =>
    {
        if (!Native.GetWindowRect(feld, out var f) || !Native.GetClientRect(box, out var c)) return null;
        var p = new Native.POINT { X = 0, Y = 0 };
        if (!Native.ClientToScreen(box, ref p)) return null;
        return new Lage(f, p.Y, p.Y + c.Height);
    });

    /// <summary>Steht der Rollbereich am Anfang? (Kein Kindfenster ragt oben hinaus.)</summary>
    private static bool AmAnfang(IntPtr box, int sichtOben) => Native.ImDpiKontext(box, () =>
    {
        foreach (var k in Native.Kinder(box))
            if (Native.IsWindowVisible(k) && Native.GetWindowRect(k, out var r) && r.Top < sichtOben - 1) return false;
        return true;
    });

    private static void Fotografiere(IntPtr feld, Bitmap ziel, int von, int hoehe) => Native.ImDpiKontext(feld, () =>
    {
        using var g = Graphics.FromImage(ziel);
        IntPtr zielDc = g.GetHdc();
        IntPtr quelle = Native.GetDC(feld);
        try { Native.BitBlt(zielDc, 0, von, ziel.Width, hoehe, quelle, 0, von, Native.SRCCOPY); }
        finally
        {
            Native.ReleaseDC(feld, quelle);
            g.ReleaseHdc(zielDc);
        }
        return 0;
    });

    /// <summary>Womit gerollt wird, wenn nicht das echte Rad: WM_MOUSEWHEEL (gemessen 27.11.2026: wirkt, solange der
    /// Fokus nicht in der Liste liegt — CM_MOUSEWHEEL wirkte in der Nachmessung gar nicht).</summary>
    internal static uint RadNachricht { get; set; } = Native.WM_MOUSEWHEEL;
    /// <summary>Dauerlauf: mit dem echten Mausrad rollen (das Programm fuehrt dort ohnehin die Maus).</summary>
    internal static bool EchtesRad { get; set; }

    private static void Rolle(IntPtr box, int rasten, Lage l)
    {
        // WM_MOUSEWHEEL: oberes Wort = Weg (120 je Raste, negativ = nach unten), lParam = Bildschirmpunkt im Bereich
        int x = l.Feld.Left + 20, y = (l.SichtOben + l.SichtUnten) / 2;
        if (EchtesRad)
        {
            // Dauerlauf: das echte Mausrad, wie es ein Mensch dreht — der Zeiger steht dafuer schon ueber der Detailansicht
            // (Dauerlauf.MausAufDetails). Geschickte Nachrichten wirkten nicht, sobald vorher in die Liste geklickt wurde
            // (gemessen 27.11.2026 abends: weder WM_MOUSEWHEEL noch CM_MOUSEWHEEL).
            // Jede Raste als EIGENES Ereignis: ein einzelnes Ereignis mit grossem Weg bewegte die Ansicht nur um eine
            // Raste (gemessen: „60 Rasten“ fuer 62 Zeilen, ~2 s, und das Zurueckrollen kam nicht oben an).
            uint weg = unchecked((uint)(Math.Sign(rasten) * 120));
            for (int i = 0; i < Math.Abs(rasten); i++) Native.mouse_event(Native.MOUSEEVENTF_WHEEL, 0, 0, weg, UIntPtr.Zero);
            return;
        }
        // Befund 27.11.2026 (Dauerlauf): WM_MOUSEWHEEL reicht eine Delphi-Anwendung an das Feld mit dem FOKUS weiter — nach
        // einem Klick in die Liste rollte so die Liste statt der Detailansicht. CM_MOUSEWHEEL ist die Nachricht, die das
        // Formular selbst an das Zielfeld schickt (gleicher Aufbau): sie trifft genau diesen Rollbereich.
        Native.PostMessage(box, RadNachricht, new IntPtr(rasten * 120 * 65536L), new IntPtr(((long)(y & 0xFFFF) << 16) | (uint)(x & 0xFFFF)));
    }

    /// <summary>Warten, bis das Feld sich nach einer Raste bewegt hat (hoechstens 300 ms). false = steht.</summary>
    /// <summary>Befund 27.11.2026 abends: mit ueber 2.000 Inseraten in der Liste antwortet AutoPointer traeger — nach 300 ms
    /// galt die Raste als wirkungslos, die Beschreibung blieb angeschnitten. Jetzt bis zu 1,2 s (je 15 ms).</summary>
    internal const int BewegtVersuche = 80;

    private static async Task<bool> BewegtAsync(IntPtr feld, IntPtr box, int altOben, int versuche = BewegtVersuche)
    {
        for (int i = 0; i < versuche; i++)
        {
            await Task.Delay(15);
            var l = Miss(feld, box);
            if (l == null) return false;
            if (l.Feld.Top != altOben) return true;
        }
        return false;
    }

    /// <summary>Das ganze Feld als EIN Bild (weiss, wo nichts zu sehen war) samt Abdeckung. null = nicht moeglich
    /// (kein Rollbereich, AutoPointer nicht vorne, Leiste davor, vom Sucher gerollt und nichts zu sehen).</summary>
    /// <param name="anderesFeld">Dauerlauf 27.11.2026: statt der Beschreibung ein anderes Feld desselben Rollbereichs
    /// ganz abbilden (Tabelle „Technische Daten“, Gitter „Ausstattung“).</param>
    internal static async Task<(Bitmap Bild, Abdeckung Gesehen, int Rasten)?> AbbildAsync(DetailAnsicht ansicht, IntPtr? anderesFeld = null)
    {
        IntPtr feld = anderesFeld ?? ansicht.Beschreibung;
        if (feld == IntPtr.Zero || !Native.IsWindow(feld)) return null;
        IntPtr box = Rollbereich(feld);
        if (box == IntPtr.Zero || !Native.IsWindowVisible(box)) return null;
        if (!AutoPointerFenster.ImVordergrund(ansicht.Hauptfenster) || AutoPointerFenster.Verdeckt(box)) return null;
        var l = Miss(feld, box);
        if (l == null || l.Feld.Width <= 20 || l.Feld.Height <= 4 || l.Feld.Height > 20000) return null;

        bool rollen = l.Feld.Bottom > l.SichtUnten && AmAnfang(box, l.SichtOben);
        if (Diagnose)
        {
            Protokoll.Schreibe($"Beschreibung: Feld {l.Feld.Left},{l.Feld.Top} {l.Feld.Width}x{l.Feld.Height}, sichtbar {l.SichtOben}–{l.SichtUnten}, rollen={rollen}");
            Native.ImDpiKontext(box, () =>
            {
                foreach (var k in Native.Kinder(box))
                    if (Native.IsWindowVisible(k) && Native.GetWindowRect(k, out var r) && r.Top < l.SichtOben - 1)
                        Protokoll.Schreibe($"  ragt oben hinaus: {Native.Klasse(k)} oben={r.Top} höhe={r.Height} eltern={Native.Klasse(Native.GetParent(k))}");
                return 0;
            });
        }
        var bild = new Bitmap(l.Feld.Width, l.Feld.Height, PixelFormat.Format32bppRgb);
        using (var g = Graphics.FromImage(bild)) g.Clear(Color.White);
        var gesehen = new Abdeckung(l.Feld.Height);
        int rasten = 0, startOben = l.Feld.Top, jeRaste = 0;
        try
        {
            while (true)
            {
                if (Streifen(l.Feld.Top, l.Feld.Height, l.SichtOben, l.SichtUnten) is { } s)
                {
                    Fotografiere(feld, bild, s.Von, s.Hoehe);
                    gesehen.Merke(s.Von, s.Hoehe);
                }
                if (!rollen || gesehen.Vollstaendig || l.Feld.Bottom <= l.SichtUnten || rasten >= MaxRasten) break;
                // ein anderes Fenster ist nach vorne gekommen: nicht weiter rollen, nichts Fremdes fotografieren
                if (!AutoPointerFenster.ImVordergrund(ansicht.Hauptfenster)) break;
                int schritt = Rasten(l.Feld.Top, l.Feld.Bottom, l.SichtOben, l.SichtUnten, jeRaste);
                Rolle(box, -schritt, l);
                rasten += schritt;
                if (!await BewegtAsync(feld, box, l.Feld.Top)) break;
                await Task.Delay(45);                       // AutoPointer zeichnet nach dem Rollen neu
                var neu = Miss(feld, box);
                if (neu == null || neu.Feld.Height != l.Feld.Height) break;      // anderes Auto: abbrechen
                if (jeRaste == 0 && schritt == 1) jeRaste = l.Feld.Top - neu.Feld.Top;      // die erste Raste misst den Weg
                l = neu;
            }
        }
        finally
        {
            // Zurueck an den Anfang — gerollt wurde nur von dort. Eine Raste mehr schadet nicht (oben ist Schluss).
            if (rasten > 0)
            {
                var jetzt = Miss(feld, box) ?? l;
                Rolle(box, rasten + 1, jetzt);
                for (int i = 0; i < 40; i++)
                {
                    await Task.Delay(15);
                    if (Miss(feld, box) is not { } m || m.Feld.Top >= startOben) break;
                }
                await Task.Delay(60);                       // neu gezeichnet, bevor der Ueberwacher die Pruefsumme bildet
            }
        }
        return (bild, gesehen, rasten);
    }

    /// <summary>Dauerlauf (27.11.2026 abends): MEHRERE Felder desselben Rollbereichs in EINEM Durchgang ganz abbilden
    /// (Technische Daten, Ausstattung, Beschreibung) — einmal nach unten, einmal zurueck. Vorher wurde jedes Feld
    /// einzeln gerollt; das Zurueckrollen war beim naechsten Feld noch nicht fertig, die Beschreibung fehlte dann
    /// (gemessen an mobile.de/AutoScout24), und es kostete dreimal die Zeit.
    /// Ergebnis je Feld in der Reihenfolge von <paramref name="felder"/>; null = Feld fehlt oder aenderte beim Aufnehmen
    /// seine Hoehe (noch am Laden). Ganz null = nicht moeglich (AutoPointer nicht vorne, verdeckt, kein Rollbereich).</summary>
    internal static async Task<(Bitmap Bild, Abdeckung Gesehen, int Rasten)?[]?> AbbildMehrereAsync(DetailAnsicht ansicht, IReadOnlyList<IntPtr> felder)
    {
        var da = felder.Select(f => f != IntPtr.Zero && Native.IsWindow(f)).ToArray();
        IntPtr erstes = felder.Where((f, i) => da[i]).FirstOrDefault();
        if (erstes == IntPtr.Zero) return null;
        IntPtr box = Rollbereich(erstes);
        if (box == IntPtr.Zero || !Native.IsWindowVisible(box)) return null;
        if (!AutoPointerFenster.ImVordergrund(ansicht.Hauptfenster) || AutoPointerFenster.Verdeckt(box)) return null;

        var bilder = new Bitmap?[felder.Count];
        var gesehen = new Abdeckung?[felder.Count];
        IntPtr leit = IntPtr.Zero;                 // das oberste Feld: an ihm wird die Bewegung gemessen
        int leitOben = int.MaxValue, sichtOben = 0;
        for (int i = 0; i < felder.Count; i++)
        {
            if (!da[i] || Rollbereich(felder[i]) != box || Miss(felder[i], box) is not { } l) { da[i] = false; continue; }
            if (l.Feld.Width <= 20 || l.Feld.Height <= 4 || l.Feld.Height > 20000) { da[i] = false; continue; }
            bilder[i] = new Bitmap(l.Feld.Width, l.Feld.Height, PixelFormat.Format32bppRgb);
            using (var g = Graphics.FromImage(bilder[i]!)) g.Clear(Color.White);
            gesehen[i] = new Abdeckung(l.Feld.Height);
            sichtOben = l.SichtOben;
            if (l.Feld.Top < leitOben) { leitOben = l.Feld.Top; leit = felder[i]; }
        }
        if (leit == IntPtr.Zero) return null;
        bool rollen = AmAnfang(box, sichtOben);
        int rasten = 0, jeRaste = 0, seiten = 0;
        bool blaettern = EchtesRad && SeitenweiseBlaettern;
        try
        {
            while (true)
            {
                int unten = int.MinValue, sichtUnten = 0, sichtHoehe = 0;
                bool fertig = true;
                for (int i = 0; i < felder.Count; i++)
                {
                    if (!da[i]) continue;
                    var l = Miss(felder[i], box);
                    if (l == null || l.Feld.Height != bilder[i]!.Height)
                    {
                        // das Feld ist weg oder gewachsen (laedt noch): nicht weiter aufnehmen
                        da[i] = false;
                        bilder[i]!.Dispose();
                        bilder[i] = null;
                        continue;
                    }
                    if (Streifen(l.Feld.Top, l.Feld.Height, l.SichtOben, l.SichtUnten) is { } s)
                    {
                        Fotografiere(felder[i], bilder[i]!, s.Von, s.Hoehe);
                        gesehen[i]!.Merke(s.Von, s.Hoehe);
                    }
                    sichtUnten = l.SichtUnten;
                    sichtHoehe = l.SichtUnten - l.SichtOben;
                    if (!gesehen[i]!.Vollstaendig)
                    {
                        fertig = false;
                        unten = Math.Max(unten, l.Feld.Bottom);
                    }
                }
                if (fertig || !rollen || unten <= sichtUnten || rasten >= MaxRastenMehrere) break;
                if (!AutoPointerFenster.ImVordergrund(ansicht.Hauptfenster)) break;
                if (Miss(leit, box) is not { } vor) break;
                // Seitenweise: ein Klick in die Rollleiste unter dem Schieber blaettert eine ganze Sichthoehe (gemessen
                // 27.11.2026 abends: eine Mausrad-Raste bewegt nur ~11 Bildpunkte — 70 Rasten und ~3 s je Auto mit
                // langer Ausstattung). Bewegt der Klick nichts mehr (Schieber liegt darunter), weiter mit dem Rad.
                if (blaettern && unten - sichtUnten > 100)
                {
                    LeistenKlick(box, nachUnten: true);
                    if (await BewegtAsync(leit, box, vor.Feld.Top, 20))
                    {
                        await RuhigAsync(leit, box);
                        seiten++;
                        if (seiten > 40) break;
                        continue;
                    }
                    blaettern = false;
                    MausInDieMitte(box);
                }
                // fast eine ganze Sichthoehe weiter (die Felder liegen untereinander: so bleibt keine Zeile ungesehen)
                int schritt = jeRaste <= 0 ? 1
                    : Math.Clamp(Math.Min((sichtHoehe - 12) / jeRaste, (unten - sichtUnten + jeRaste - 1) / jeRaste), 1, 40);
                Rolle(box, -schritt, vor);
                rasten += schritt;
                if (!await BewegtAsync(leit, box, vor.Feld.Top)) break;
                int oben = await RuhigAsync(leit, box);
                if (jeRaste == 0 && schritt == 1) jeRaste = vor.Feld.Top - oben;
                if (jeRaste <= 0 && schritt == 1) break;          // keine messbare Bewegung
            }
        }
        finally
        {
            // zurueck an den Anfang: erst seitenweise (Klick ueber dem Schieber), den Rest mit dem Rad
            for (int i = 0; i < seiten + 1 && seiten > 0; i++)
            {
                if (Miss(leit, box) is not { } jetzt || jetzt.Feld.Top >= leitOben) break;
                LeistenKlick(box, nachUnten: false);
                if (!await BewegtAsync(leit, box, jetzt.Feld.Top, 20)) break;
                await RuhigAsync(leit, box);
            }
            if (seiten > 0) MausInDieMitte(box);
            if (rasten > 0 || (seiten > 0 && (Miss(leit, box)?.Feld.Top ?? leitOben) < leitOben))
            {
                Rolle(box, rasten + 2 + (seiten > 0 ? 6 : 0), Miss(leit, box) ?? new Lage(default, 0, 0));
                for (int i = 0; i < 100; i++)                     // bis zu 1,5 s: erst weiter, wenn die Ansicht wieder oben steht
                {
                    await Task.Delay(15);
                    if (Miss(leit, box) is not { } m || m.Feld.Top >= leitOben) break;
                }
                await Task.Delay(40);
            }
        }
        var raus = new (Bitmap, Abdeckung, int)?[felder.Count];
        for (int i = 0; i < felder.Count; i++)
            if (bilder[i] != null) raus[i] = (bilder[i]!, gesehen[i]!, rasten);
        return raus;
    }

    internal const int MaxRastenMehrere = 200;

    /// <summary>Dauerlauf: mit Klicks in die Rollleiste seitenweise blaettern (schneller als das Rad). Nur mit <see cref="EchtesRad"/>.</summary>
    internal static bool SeitenweiseBlaettern { get; set; } = true;

    /// <summary>Echter Mausklick in die senkrechte Rollleiste des Rollbereichs: unter dem Schieber (eine Seite weiter)
    /// bzw. ueber ihm (eine Seite zurueck). Die Leiste ist die rechte Kante des Bereichs, die Pfeile je ~21 Bildpunkte.</summary>
    private static void LeistenKlick(IntPtr box, bool nachUnten) => Native.ImDpiKontext(box, () =>
    {
        if (!Native.GetWindowRect(box, out var r)) return 0;
        Native.SetCursorPos(r.Right - 10, nachUnten ? r.Bottom - 36 : r.Top + 36);
        Thread.Sleep(12);
        Native.mouse_event(Native.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
        Native.mouse_event(Native.MOUSEEVENTF_LEFTUP, 0, 0, 0, UIntPtr.Zero);
        return 0;
    });

    private static void MausInDieMitte(IntPtr box) => Native.ImDpiKontext(box, () =>
    {
        if (Native.GetWindowRect(box, out var r)) Native.SetCursorPos((r.Left + r.Right) / 2, r.Top + Math.Min(r.Height / 2, 120));
        return 0;
    });

    /// <summary>Warten, bis das Feld nach dem Rollen stillsteht (zweimal dieselbe Lage im Abstand von 20 ms, hoechstens
    /// 600 ms) — mitten in der Bewegung fotografiert, passen Lage und Bild nicht zusammen. Zurueck: die Oberkante.</summary>
    private static async Task<int> RuhigAsync(IntPtr feld, IntPtr box)
    {
        int letzte = int.MinValue, gleich = 0;
        for (int i = 0; i < 30; i++)
        {
            await Task.Delay(20);
            int oben = Miss(feld, box)?.Feld.Top ?? letzte;
            gleich = oben == letzte ? gleich + 1 : 0;
            letzte = oben;
            if (gleich >= 2) break;
        }
        await Task.Delay(30);                       // AutoPointer zeichnet nach dem Rollen neu
        return letzte;
    }

    /// <summary>Zeile ohne Schrift? (alle Bildpunkte hell)</summary>
    internal static unsafe bool[] LeereZeilen(Bitmap bild)
    {
        var leer = new bool[bild.Height];
        var daten = bild.LockBits(new Rectangle(0, 0, bild.Width, bild.Height), ImageLockMode.ReadOnly, PixelFormat.Format32bppRgb);
        try
        {
            for (int y = 0; y < daten.Height; y++)
            {
                uint* zeile = (uint*)((byte*)daten.Scan0 + (long)y * daten.Stride);
                bool hell = true;
                for (int x = 0; x < daten.Width && hell; x++)
                {
                    uint p = zeile[x];
                    hell = (p & 0xFF) > 200 && ((p >> 8) & 0xFF) > 200 && ((p >> 16) & 0xFF) > 200;
                }
                leer[y] = hell;
            }
        }
        finally { bild.UnlockBits(daten); }
        return leer;
    }

    /// <summary>Die gelesene Beschreibung.</summary>
    /// <param name="Exakt">true = jeder Buchstabe mit der echten Schrift abgeglichen, nichts blieb offen, nichts gekuerzt
    /// (<see cref="SchriftLeser"/>). false = Texterkennung oder Abgleich mit Luecken — kann Lesefehler enthalten.</param>
    internal sealed record Gelesen(string Text, bool Exakt);

    /// <summary>Schrift-Abgleich an (Standard). Aus nur zum Messen ("--einmal --ganz --ohne-abgleich").</summary>
    internal static bool Abgleich { get; set; } = true;

    /// <summary>Der Abgleich zaehlt, wenn hoechstens jedes zwanzigste Wort nicht aufging (einzelne Symbole/Emoji) —
    /// sonst passt die Schrift nicht und die Texterkennung liest. (rein, fuer Tests)</summary>
    internal static bool Brauchbar(SchriftLeser.Lesung l)
    {
        int woerter = l.Zeilen.Sum(z => z.Text.Split(' ', StringSplitOptions.RemoveEmptyEntries).Length);
        return woerter > 0 && l.Luecken * 20 <= woerter;
    }

    private static string? Kuerzen(string text)
    {
        text = text.Trim();
        return text.Length == 0 ? null : text.Length > MaxZeichen ? text[..MaxZeichen] : text;
    }

    /// <summary>Die ganze Beschreibung lesen. null = nichts gelesen (dann bleibt es beim sichtbaren Anfang).</summary>
    /// <param name="fertig">Dauerlauf: das Feldbild ist schon aufgenommen (<see cref="AbbildMehrereAsync"/>).</param>
    internal static async Task<Gelesen?> LiesAsync(TextErkennung ocr, DetailAnsicht ansicht, (Bitmap Bild, Abdeckung Gesehen, int Rasten)? fertig = null)
    {
        try
        {
            var start = Environment.TickCount64;
            var ergebnis = fertig ?? await AbbildAsync(ansicht);
            if (ergebnis is not { } e) return null;
            using var bild = e.Bild;
            var (von, bis) = e.Gesehen.Bereich();
            if (bis - von < 6) return null;
            if (BildSpeichern is { } pfad)
            {
                using var ausschnitt = bild.Clone(new Rectangle(0, von, bild.Width, bis - von), PixelFormat.Format32bppRgb);
                ausschnitt.Save(pfad, ImageFormat.Png);
            }
            // "Es darf keine Lesefehler geben" (Ahmad 27.11.2026): zuerst der Schrift-Abgleich — Buchstabe fuer
            // Buchstabe gegen die echte Schrift. Geht alles auf, ist das der Text, der dort steht (kein Raten).
            // Nur wenn zu viel nicht aufgeht (andere Schrift, viele Symbole), liest die Texterkennung wie bisher.
            if (Abgleich)
            {
                using var feld = bild.Clone(new Rectangle(0, von, bild.Width, bis - von), PixelFormat.Format32bppRgb);
                var abgleich = await Task.Run(() => SchriftLeser.LiesFeld(feld));
                if (abgleich is { Zeilen.Count: 0 }) return null;           // leeres Feld
                if (abgleich != null && Brauchbar(abgleich))
                {
                    string? genau = Kuerzen(abgleich.Text);
                    Protokoll.Schreibe($"Beschreibung abgeglichen: {genau?.Length ?? 0} Zeichen, {abgleich.Zeilen.Count} Zeilen, "
                                       + $"{abgleich.Luecken} Lücken, Schrift {abgleich.Px} px, {e.Rasten} Rasten gerollt, "
                                       + $"{e.Gesehen.Fehlend} Zeilen nicht gesehen, {Environment.TickCount64 - start} ms.");
                    return genau == null ? null : new Gelesen(genau, abgleich.Exakt && e.Gesehen.Fehlend == 0
                                                                     && genau.Length == abgleich.Text.Length);
                }
            }
            uint dpi = Native.GetDpiForWindow(ansicht.Beschreibung);
            double faktor = Math.Clamp(Vergroesserung * 96 / (dpi == 0 ? 96 : dpi), 1.0, Vergroesserung);
            var zeilen = new List<string>();
            var leer = LeereZeilen(bild);
            foreach (var (sv, sh) in Stuecke(leer[von..bis], StueckHoehe))
            {
                using var stueck = bild.Clone(new Rectangle(0, von + sv, bild.Width, sh), PixelFormat.Format32bppRgb);
                zeilen.AddRange((await ocr.LiesAsync(stueck, faktor)).Select(z => z.Text));
            }
            string? text = Zusammen(zeilen);
            Protokoll.Schreibe($"Beschreibung ganz gelesen: {text?.Length ?? 0} Zeichen, Feldhöhe {bild.Height}, "
                               + $"{e.Rasten} Rasten gerollt, {e.Gesehen.Fehlend} Zeilen nicht gesehen, "
                               + $"{Environment.TickCount64 - start} ms.");
            return text == null ? null : new Gelesen(text, false);
        }
        catch (Exception ex)
        {
            Protokoll.Schreibe("Beschreibung nicht ganz gelesen: " + ex.Message);
            return null;
        }
    }
}
