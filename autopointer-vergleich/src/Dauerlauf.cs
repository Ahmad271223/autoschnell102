using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 27.11.2026: der PC laeuft unbeaufsichtigt, AutoPointer zeigt laufend neue Inserate — das Programm
/// liest die Liste mit, sortiert an der Listenzeile vor (<see cref="Vorauswahl"/>) und klickt nur die passenden Autos
/// selbst an, um sie ganz zu lesen (Tabellen + ganze Beschreibung).</summary>
/// <remarks>
/// Gemessen am 27.11.2026: 1,3 neue Autos je Sekunde (bis 4 in derselben Sekunde), etwa 5 Zeilen sichtbar; nach einem
/// Klick steht die Detailansicht nach 0,27–0,51 s. Die Liste nimmt nur echte Mausklicks an (geschickte Nachrichten
/// wirken nicht) — darum braucht der Dauerlauf einen Rechner, an dem sonst niemand arbeitet, und AutoPointer vorne.
/// Sicherungen (alle am laufenden AutoPointer als noetig erwiesen):
/// - geklickt wird nur, wenn AutoPointer vorne liegt und an der Klickstelle wirklich die Liste ist; die Zeile wird
///   direkt vor dem Klick im frischen Bild gesucht und noch einmal bestaetigt (kommt im selben Moment ein neues Auto,
///   rutscht alles eine Zeile tiefer — der Klick traefe den Nachbarn);
/// - gewartet wird, bis Kopf UND Technik-Tabelle gewechselt haben (die Tabelle kommt spaeter als der Kopf — sonst
///   mischen sich Werte zweier Autos);
/// - der Kopf der Detailansicht (Uhrzeit, Portal, Titel, Preis — mit dem Schrift-Abgleich gelesen) muss vor UND nach
///   dem Lesen zur Zeile passen, ebenso Preis/Kilometer/Jahr aus der Tabelle — sonst gilt das Auto als nicht gelesen
///   und wird einmal neu versucht.
/// </remarks>
internal sealed class Dauerlauf
{
    /// <param name="Art">„aussortiert“ (Vorauswahl), „gelesen“, „verpasst“ (aus dem Bild gerutscht), „falsch“ (nach dem
    /// Klick stand ein anderes Auto da oder nichts aenderte sich).</param>
    internal sealed record Ereignis(string Art, ListenZeile Zeile, string? Grund = null, Fahrzeug? Fahrzeug = null,
                                    long KlickBisGeladenMs = 0, long GesamtMs = 0);

    private readonly TextErkennung? _ocr;
    private readonly Func<DauerlaufRegeln> _regeln;
    private readonly Action<Ereignis> _melde;
    private readonly bool _klicken;

    private IntPtr _haupt, _liste;
    private DetailAnsicht? _ansicht;
    private readonly HashSet<string> _gesehen = new();
    private readonly Queue<string> _gesehenReihe = new();
    private readonly Dictionary<ulong, ListenZeile> _gelesen = new();
    private readonly List<ListenZeile> _warten = new();
    /// <summary>Kennung des Autos, das nach einem Fehlschlag noch einmal versucht wird (seine Zeile ist dann markiert).</summary>
    private string? _nochmalZiel;
    private readonly HashSet<string> _nochmal = new();
    internal const int Gemerkt = 3000;

    /// <summary>Zaehler fuer den Probelauf.</summary>
    public int Neu { get; private set; }
    public int Aussortiert { get; private set; }
    public int Gelesen { get; private set; }
    public int Verpasst { get; private set; }
    public int Falsch { get; private set; }
    public int Wartend => _warten.Count;
    /// <summary>Warum der letzte Schritt nichts tat ("" = er lief).</summary>
    public string Hindernis { get; private set; } = "";
    /// <summary>Nur zum Messen: was der letzte Schritt im Listenbild fand.</summary>
    public string Stand { get; private set; } = "";
    /// <summary>Nur zum Messen: das naechste Listenbild hierhin speichern.</summary>
    internal static string? BildSpeichern;

    /// <param name="klicken">false = nur mitlesen und vorsortieren, nichts anklicken.</param>
    public Dauerlauf(TextErkennung? ocr, Func<DauerlaufRegeln> regeln, Action<Ereignis> melde, bool klicken = true)
    {
        _ocr = ocr;
        _regeln = regeln;
        _melde = melde;
        _klicken = klicken;
    }

    /// <summary>Ein Durchgang: Liste lesen, Neues vorsortieren, hoechstens EIN Auto anklicken und ganz lesen.
    /// true = es gab etwas zu tun (gleich wieder aufrufen), false = Ruhe (kurz warten).</summary>
    public async Task<bool> SchrittAsync()
    {
        if (!AutoPointerFenster.IstHauptfenster(_haupt))
        {
            _haupt = AutoPointerFenster.FindeHauptfenster();
            _liste = IntPtr.Zero;
            _ansicht = null;
        }
        if (_haupt == IntPtr.Zero) return Ruhe("AutoPointer läuft nicht");
        if (!AutoPointerFenster.ImVordergrund(_haupt)) return Ruhe("AutoPointer liegt nicht vorne");
        if (_ansicht != null && !AutoPointerFenster.NochGueltig(_ansicht)) _ansicht = null;
        if (_liste == IntPtr.Zero || !Native.IsWindow(_liste) || !Native.IsWindowVisible(_liste))
            _liste = ListenLeser.FindeListe(_haupt, _ansicht);
        if (_liste == IntPtr.Zero) return Ruhe("Liste nicht gefunden");
        if (AutoPointerFenster.Verdeckt(_liste)) return Ruhe("Liste verdeckt");

        var sichtbar = new HashSet<string>();
        bool neu = false;
        int markierte = 0;
        using (var bild = AutoPointerFenster.Abbild(_liste))
        {
            if (bild == null) return Ruhe("Liste nicht abzubilden");
            if (BildSpeichern is { } pfad)
            {
                BildSpeichern = null;
                try { bild.Save(pfad); } catch (Exception) { }
            }
            var p = ListenLeser.Punkte(bild);
            var r = ListenLeser.FindeRaster(p, bild.Width, bild.Height);
            if (r == null) return Ruhe("keine Listenzeilen erkannt");
            var regeln = _regeln();
            int markiert = 0, unlesbar = 0;
            foreach (var z in r.Zeilen)
            {
                if (ListenLeser.Markiert(p, bild.Width, r, z)) { markiert++; continue; }
                var zeile = Zeile(bild, p, r, z);
                if (!zeile.Lesbar) { unlesbar++; continue; }
                sichtbar.Add(zeile.Kennung);
                if (!Merke(zeile.Kennung)) continue;
                neu = true;
                Neu++;
                if (Vorauswahl.Grund(zeile, regeln) is { } grund)
                {
                    Aussortiert++;
                    _melde(new Ereignis("aussortiert", zeile, grund));
                }
                else _warten.Add(zeile);
            }
            markierte = markiert;
            Stand = $"Listenbild {bild.Width}x{bild.Height}, {r.Zeilen.Count} Zeilen, davon markiert {markiert}, nicht lesbar {unlesbar}";
        }
        Hindernis = "";
        // was wartet, aber nicht mehr zu sehen ist, ist nach unten aus dem Bild gerutscht
        // Ausnahme: das Auto, das gerade noch einmal versucht wird — seine Zeile ist nach dem Klick MARKIERT und wird
        // darum oben nicht gelesen (vorher endete jeder zweite Versuch als „verpasst“)
        foreach (var w in _warten.Where(w => !sichtbar.Contains(w.Kennung) && !(markierte > 0 && w.Kennung == _nochmalZiel)).ToList())
        {
            _warten.Remove(w);
            Verpasst++;
            _melde(new Ereignis("verpasst", w, "aus dem Bild gerutscht, bevor es dran war"));
        }
        if (!_klicken || _warten.Count == 0 || _ocr == null) return neu;

        // das aelteste zuerst: es rutscht als naechstes aus dem Bild
        var ziel = _warten[0];
        _warten.RemoveAt(0);
        await OeffneUndLiesAsync(ziel);
        return true;
    }

    /// <summary>Die Zeile aus dem Merker (Pruefsumme des Zeileninhalts) oder neu gelesen.</summary>
    private ListenZeile Zeile(Bitmap bild, int[] p, ListenLeser.Raster r, (int Oben, int Unten) z)
    {
        ulong s = ListenLeser.Summe(p, bild.Width, r, z);
        if (_gelesen.TryGetValue(s, out var zeile)) return zeile;
        if (_gelesen.Count > Gemerkt) _gelesen.Clear();
        return _gelesen[s] = ListenLeser.Lies(bild, p, r, z);
    }

    private bool Ruhe(string warum)
    {
        Hindernis = warum;
        return false;
    }

    private bool Merke(string kennung)
    {
        if (!_gesehen.Add(kennung)) return false;
        _gesehenReihe.Enqueue(kennung);
        while (_gesehenReihe.Count > Gemerkt) _gesehen.Remove(_gesehenReihe.Dequeue());
        return true;
    }

    private (ulong Kopf, ulong Technik) DetailSummen() =>
        _ansicht == null ? (0UL, 0UL)
        : (AutoPointerFenster.Pruefsumme(_ansicht.KopfTabelle), AutoPointerFenster.Pruefsumme(_ansicht.TechnikTabelle));

    internal const int LadenHoechstensMs = 1500, RuhigMs = 100;

    /// <summary>Widerspricht die Technik-Tabelle der Listenzeile? (Preis, Kilometer, Jahr — soweit beide Seiten sie
    /// sicher haben.) Dann zeigt die Tabelle noch das vorige Auto oder ein anderes. (rein, fuer Tests)</summary>
    internal static string? Widerspruch(ListenZeile z, Fahrzeug f)
    {
        if (z.Preis is { } p && f.Preis is { } fp && p != fp) return $"Preis {p} in der Liste, {fp} in der Tabelle";
        if (z.Km is { } k && f.Kilometer is { } fk && k != fk) return $"{k} km in der Liste, {fk} km in der Tabelle";
        if (z.Jahr is { } j && f.EzJahr is { } fj && j != fj) return $"Erstzulassung {j} in der Liste, {fj} in der Tabelle";
        return null;
    }

    /// <summary>Kopf der Detailansicht, mit dem Schrift-Abgleich gelesen ("" bzw. null = nicht genau lesbar).</summary>
    /// <param name="Zeile">z. B. „27.11.2026 17:20:24 - Inserat von Kleinanzeigen“.</param>
    internal sealed record KopfDaten(string Zeile, string Titel, int? Preis);

    /// <summary>Passt der Kopf zur Listenzeile? null = ja; sonst, was nicht passt. Uhrzeit und Portal MUESSEN lesbar
    /// sein und stimmen; Titel und Preis zaehlen, soweit beide Seiten sie genau haben (zwei Inserate derselben Sekunde
    /// unterscheiden sich nur darin — so gemessen am 27.11.2026). (rein, fuer Tests)</summary>
    internal static string? KopfWiderspruch(ListenZeile z, KopfDaten k)
    {
        if (k.Zeile.Length == 0) return "Kopf der Detailansicht nicht lesbar";
        if (!k.Zeile.Contains(z.Zeit, StringComparison.Ordinal) || !k.Zeile.Contains(z.Portal, StringComparison.OrdinalIgnoreCase))
            return $"Detailansicht zeigt „{k.Zeile}“";
        if (z.Preis is { } p && k.Preis is { } kp && p != kp) return $"Preis {p} in der Liste, {kp} im Kopf";
        if (z.TitelGenau && z.Titel.Length > 0 && k.Titel.Length > 0)
        {
            // lange Titel sind abgeschnitten („...“) — in der Liste UND im Kopf, an verschiedenen Stellen: der kuerzere
            // muss der Anfang des laengeren sein
            string a = OhnePunkte(z.Titel), c = OhnePunkte(k.Titel);
            if (!a.StartsWith(c, StringComparison.Ordinal) && !c.StartsWith(a, StringComparison.Ordinal))
                return $"Titel „{k.Titel}“ statt „{z.Titel}“";
        }
        return null;
    }

    private async Task OeffneUndLiesAsync(ListenZeile ziel)
    {
        var uhr = Stopwatch.StartNew();
        _ansicht ??= AutoPointerFenster.FindeDetails(_haupt);
        var vorher = DetailSummen();

        // zweiter Versuch und die Zeile ist schon die markierte: nicht klicken (ein Klick aenderte nichts), nur neu
        // lesen — der Kopf der Detailansicht muss weiter zu genau dieser Zeile passen
        bool zweiter = _nochmalZiel == ziel.Kennung;
        if (zweiter) _nochmalZiel = null;
        bool geklickt = Klicke(ziel), ohneKlick = !geklickt && zweiter;
        if (ohneKlick)
        {
            _ansicht = AutoPointerFenster.FindeDetails(_haupt) ?? _ansicht;
            if (_ansicht == null || KopfWiderspruch(ziel, Kopf(_ansicht)) is not null)
            {
                Verpasst++;
                _melde(new Ereignis("verpasst", ziel, "beim zweiten Versuch nicht mehr angezeigt"));
                return;
            }
        }
        else if (!geklickt)
        {
            Verpasst++;
            _melde(new Ereignis("verpasst", ziel, "Zeile vor dem Klick nicht wiedergefunden"));
            return;
        }

        // warten, bis Kopf UND Technik-Tabelle gewechselt haben und wieder stillstehen
        long geaendert = ohneKlick ? 0 : -1, zuletzt = -1;
        var letzte = vorher;
        while (!ohneKlick && uhr.ElapsedMilliseconds < LadenHoechstensMs)
        {
            await Task.Delay(15);
            _ansicht ??= AutoPointerFenster.FindeDetails(_haupt);
            if (_ansicht == null) continue;
            var s = DetailSummen();
            if (s != letzte)
            {
                letzte = s;
                zuletzt = uhr.ElapsedMilliseconds;
            }
            if (geaendert < 0 && s.Kopf != vorher.Kopf && s.Technik != vorher.Technik) geaendert = uhr.ElapsedMilliseconds;
            if (geaendert >= 0 && uhr.ElapsedMilliseconds - zuletzt >= RuhigMs) break;
        }
        long geladen = uhr.ElapsedMilliseconds;
        // die Felder der Detailansicht neu suchen: das Beschreibungsfeld wird je Auto neu angelegt
        if (geaendert >= 0) _ansicht = AutoPointerFenster.FindeDetails(_haupt);
        if (_ansicht == null || geaendert < 0)
        {
            Fehlschlag(ziel, _ansicht == null ? "keine Detailansicht (Reiter „Übersicht“?)" : "die Anzeige hat sich nicht geändert", geladen, uhr);
            return;
        }
        if (KopfWiderspruch(ziel, Kopf(_ansicht)) is { } falsch)
        {
            Fehlschlag(ziel, falsch, geladen, uhr);
            return;
        }

        // Gemessen 27.11.2026 abends: das Mausrad wirkt in AutoPointer dort, wo der ZEIGER steht — nach dem Klick steht er
        // ueber der Liste, dann rollt die Liste statt der Detailansicht (oder gar nichts). Darum den Zeiger vor dem Lesen
        // auf die Detailansicht setzen (ohne Klick).
        MausAufDetails();
        BeschreibungGanz.EchtesRad = !KeinEchtesRad;
        // Tempo (27.11.2026 abends, gemessen 3–6,5 s je Auto): jedes Teil nur EINMAL lesen. Frueher wurde bei einer noch
        // nicht fertig geladenen Beschreibung alles (samt Texterkennung der Tabellen) ein zweites Mal gelesen.
        // Tabelle, Ausstattung und Beschreibung in EINEM Durchgang (einmal nach unten rollen, einmal zurueck), alles
        // exakt mit dem Schrift-Abgleich. Die Texterkennung der Tabellen entfaellt im Dauerlauf. Stand die Tabelle noch
        // vom vorigen Auto da oder lud die Beschreibung noch (sie kommt als Letztes), einmal nachfassen.
        var f = new Fahrzeug { Quelle = ziel.Portal };
        for (int versuch = 0; versuch < 2; versuch++)
        {
            if (versuch > 0) await Task.Delay(150);
            await BeschreibungRuhigAsync();
            var neu = new Fahrzeug { Quelle = ziel.Portal };
            await AllesLesenAsync(neu);
            bool tabelleGut = TabelleReicht(neu) && Widerspruch(ziel, neu) == null;
            if (tabelleGut || !TabelleReicht(f))
            {
                // die bessere Beschreibung behalten, falls der zweite Durchgang sie nicht mehr exakt bekam
                if (f.BeschreibungExakt && !neu.BeschreibungExakt) { neu.BeschreibungVoll = f.BeschreibungVoll; neu.BeschreibungExakt = true; }
                f = neu;
            }
            // fertig, wenn die Beschreibung exakt ist — oder wenn ihr Feld ganz aufgenommen wurde und nichts drinsteht
            if (tabelleGut && (f.BeschreibungExakt || _beschreibungLeer)) break;
        }
        if (!TabelleReicht(f))
        {
            // Rueckfall: die Tabelle ging nicht exakt auf (andere Schrift/Skalierung) — dann wie bisher mit Texterkennung
            var lesung = await AutoPointerQuelle.LiesAnsichtAsync(_ocr!, _ansicht, bilderSpeichern: false, lesebild: false, beschreibungGanz: true);
            if (lesung == null || lesung.Leer)
            {
                Fehlschlag(ziel, "Detailansicht nicht lesbar", geladen, uhr);
                return;
            }
            f = lesung.Fahrzeug;
        }
        // 4. Zum Schluss muss weiter alles zur Listenzeile passen (AutoPointer darf nicht weitergeschaltet haben)
        var kopf = Kopf(_ansicht);
        if ((Widerspruch(ziel, f) ?? KopfWiderspruch(ziel, kopf)) is { } wider)
        {
            Fehlschlag(ziel, wider, geladen, uhr);
            return;
        }
        // Titel und Preis stehen im Kopf exakt — die gehen vor der Texterkennung
        Genau(f, kopf, ziel);
        Gelesen++;
        _melde(new Ereignis("gelesen", ziel, null, f, geladen, uhr.ElapsedMilliseconds));
    }

    /// <summary>Titel ohne die Auslassungspunkte am Ende. Gemessen 27.11.2026 abends: im Kopf sind oft nur ZWEI Punkte zu
    /// sehen (der dritte faellt ganz dem Zellenrand zum Opfer) — „Ford Kuga … Duratec ..“ galt als anderes Auto.</summary>
    internal static string OhnePunkte(string titel) => Regex.Replace(titel, @"\s*\.{2,}$", "").TrimEnd();

    /// <summary>Was der Kopf exakt hergibt, ersetzt die per Texterkennung gelesenen Werte (gemessen 27.11.2026: Titel
    /// „Audi Q5 3xSIine“, „Peugeot 308 SW 1,61 HDi Allwet“ aus der Texterkennung). (rein, fuer Tests)</summary>
    internal static void Genau(Fahrzeug f, KopfDaten kopf, ListenZeile? zeile = null)
    {
        // der Titel ist im Kopf oft frueher abgeschnitten als in der Liste: der laengere der beiden exakten gilt
        string k = OhnePunkte(kopf.Titel), l = zeile is { TitelGenau: true } ? OhnePunkte(zeile.Titel) : "";
        string titel = l.Length > k.Length ? l : k;
        if (titel.Length > 0) f.Titel = titel;
        if (kopf.Preis is { } preis) f.Preis = preis;
    }

    /// <summary>Hat die exakt gelesene Tabelle genug hergegeben, um ohne Texterkennung auszukommen? (rein, fuer Tests)</summary>
    internal static bool TabelleReicht(Fahrzeug f) =>
        f.Merkmale.Count >= 4 && !string.IsNullOrWhiteSpace(f.MarkeModellText) && (f.Kilometer != null || f.EzJahr != null || f.Preis != null);

    /// <summary>Wartet, bis das Beschreibungsfeld da ist und seine Groesse sich 60 ms lang nicht aendert (hoechstens 500 ms).
    /// Das Feld wird je Auto neu angelegt und waechst beim Laden.</summary>
    private async Task BeschreibungRuhigAsync()
    {
        (IntPtr Feld, int Hoehe) zuletzt = (IntPtr.Zero, -1);
        var uhr = Stopwatch.StartNew();
        while (uhr.ElapsedMilliseconds < 500)
        {
            _ansicht = AutoPointerFenster.FindeDetails(_haupt) ?? _ansicht;
            if (_ansicht == null) return;
            IntPtr feld = _ansicht.Beschreibung;
            int hoehe = feld != IntPtr.Zero && Native.GetWindowRect(feld, out var r) ? r.Height : -1;
            if (feld != IntPtr.Zero && (feld, hoehe) == zuletzt) return;
            zuletzt = (feld, hoehe);
            await Task.Delay(60);
        }
    }

    /// <summary>Nur zum Messen: nicht mit dem echten Mausrad rollen.</summary>
    internal static bool KeinEchtesRad;

    private void MausAufDetails()
    {
        if (_ansicht == null) return;
        IntPtr box = BeschreibungGanz.Rollbereich(_ansicht.Beschreibung != IntPtr.Zero ? _ansicht.Beschreibung : _ansicht.TechnikTabelle);
        if (box == IntPtr.Zero) return;
        Native.ImDpiKontext(box, () =>
        {
            if (!Native.GetClientRect(box, out var r)) return 0;
            var q = new Native.POINT { X = r.Width / 2, Y = Math.Min(r.Height / 2, 120) };
            if (Native.ClientToScreen(box, ref q)) Native.SetCursorPos(q.X, q.Y);
            return 0;
        });
    }

    /// <summary>Technische Daten, Ausstattung und die ganze Beschreibung in einem Durchgang lesen (alles exakt).</summary>
    /// <summary>Das Beschreibungsfeld wurde im letzten Durchgang ganz aufgenommen und enthielt keinen Text.</summary>
    private bool _beschreibungLeer;

    private async Task AllesLesenAsync(Fahrzeug f)
    {
        if (_ansicht == null) return;
        (System.Drawing.Bitmap Bild, BeschreibungGanz.Abdeckung Gesehen, int Rasten)?[]? teile = null;
        try
        {
            teile = await BeschreibungGanz.AbbildMehrereAsync(_ansicht, new[] { _ansicht.TechnikTabelle, _ansicht.Ausstattung, _ansicht.Beschreibung });
            if (teile == null) return;
            if (teile[0] is { } technik)
            {
                var m = TabellenLeser.Technik(technik.Bild);
                if (m.Count > 0)
                {
                    f.Merkmale = m;
                    TabellenLeser.Uebernehmen(f, m);
                }
            }
            if (teile[1] is { } ausstattung) f.Ausstattung = TabellenLeser.Ausstattung(ausstattung.Bild);
            _beschreibungLeer = false;
            if (teile[2] is { } text)
            {
                bool allesGesehen = text.Gesehen.Vollstaendig;
                var ganz = await BeschreibungGanz.LiesAsync(_ocr!, _ansicht, text);      // gibt das Bild selbst frei
                teile[2] = null;
                f.BeschreibungVoll = ganz?.Text;
                f.BeschreibungExakt = ganz?.Exakt ?? false;
                _beschreibungLeer = ganz == null && allesGesehen;
            }
        }
        catch (Exception ex) { Protokoll.Schreibe("Detailansicht nicht ganz gelesen: " + ex.Message); }
        finally
        {
            if (teile != null)
                foreach (var teil in teile) teil?.Bild.Dispose();
        }
    }

    private async Task MerkmaleLesenAsync(Fahrzeug f)
    {
        if (_ansicht == null) return;
        try
        {
            if (await BeschreibungGanz.AbbildAsync(_ansicht, _ansicht.TechnikTabelle) is { } t)
                using (t.Bild)
                {
                    var m = TabellenLeser.Technik(t.Bild);
                    if (m.Count > 0)
                    {
                        f.Merkmale = m;
                        TabellenLeser.Uebernehmen(f, m);
                    }
                }
            if (_ansicht.Ausstattung != IntPtr.Zero && await BeschreibungGanz.AbbildAsync(_ansicht, _ansicht.Ausstattung) is { } a)
                using (a.Bild) f.Ausstattung = TabellenLeser.Ausstattung(a.Bild);
        }
        catch (Exception ex) { Protokoll.Schreibe("Merkmale nicht gelesen: " + ex.Message); }
    }

    /// <summary>Nicht sicher gelesen: melden und das Auto EINMAL neu anstellen (meist traf der Klick die Nachbarzeile).</summary>
    private void Fehlschlag(ListenZeile ziel, string grund, long geladen, Stopwatch uhr)
    {
        bool nochmal = _nochmal.Add(ziel.Kennung);
        if (_nochmal.Count > Gemerkt) _nochmal.Clear();
        if (nochmal)
        {
            _warten.Insert(0, ziel);
            _nochmalZiel = ziel.Kennung;
            _melde(new Ereignis("nochmal", ziel, grund + " – wird noch einmal versucht", KlickBisGeladenMs: geladen, GesamtMs: uhr.ElapsedMilliseconds));
            return;
        }
        Falsch++;
        _melde(new Ereignis("falsch", ziel, grund, KlickBisGeladenMs: geladen, GesamtMs: uhr.ElapsedMilliseconds));
    }

    internal static KopfDaten Kopf(DetailAnsicht ansicht)
    {
        if (ansicht.KopfTabelle == IntPtr.Zero || AutoPointerFenster.Verdeckt(ansicht.KopfTabelle)) return new KopfDaten("", "", null);
        using var bild = AutoPointerFenster.Abbild(ansicht.KopfTabelle);
        return bild == null ? new KopfDaten("", "", null) : Kopf(bild);
    }

    private static int _pxKopf, _pxKopfTitel, _pxKopfPreis;

    private static bool Rot(int f) => ((f >> 16) & 0xFF) > 185 && ((f >> 8) & 0xFF) < 80 && (f & 0xFF) < 80;

    /// <summary>Kopf-Tabelle lesen: erste Zeile (Uhrzeit, Portal), darunter der Titel (fett, hinter der Marke „Neu“, vor
    /// dem Portal-Logo), darunter der Preis (fett).</summary>
    internal static KopfDaten Kopf(Bitmap bild)
    {
        int b = bild.Width;
        var p = ListenLeser.Punkte(bild);
        // Rechts steht das Portal-Logo — bei AutoScout24 so hoch, dass es mit der ersten Textzeile verschmolz („Kopf nicht
        // lesbar“, gemessen 27.11.2026 abends: kein AutoScout-Auto wurde gelesen). Die Textzeilen deshalb nur im linken
        // Teil suchen und lesen; das Logo-Feld ist rund 190 Bildpunkte breit.
        int rechts = Math.Max(b / 2, b - 190);
        var baender = ListenLeser.Baender(p, b, 0, rechts, 0, bild.Height);
        if (baender.Count == 0) return new KopfDaten("", "", null);

        string Lies(int i, int x0, int x1, bool fett, ref int px)
        {
            int oben = Math.Max(i > 0 ? baender[i - 1].Bis + 1 : 0, baender[i].Von - 8);
            int unten = Math.Min(i + 1 < baender.Count ? baender[i + 1].Von - 1 : bild.Height, baender[i].Bis + 7);
            if (x1 - x0 < 8 || unten - oben < 6) return "";
            using var stueck = bild.Clone(new Rectangle(x0, oben, x1 - x0, unten - oben), PixelFormat.Format32bppRgb);
            var l = SchriftLeser.LiesFeld(stueck, amStueck: true, fett: fett, nurPx: px);
            if (l is not { Luecken: 0, Zeilen.Count: 1 }) return "";
            px = l.Px;
            return l.Text;
        }

        string zeile = Lies(0, 0, rechts, false, ref _pxKopf), titel = "";
        int? preis = null;
        if (baender.Count > 1)
        {
            // Titel: hinter der roten Marke beginnen, vor der ersten breiten weissen Luecke (danach kommt das Logo) enden
            var (von, bis) = baender[1];
            int ab = 0;
            for (int x = 0; x < Math.Min(b, 120); x++)
                for (int y = von; y < bis; y++)
                    if (Rot(p[y * b + x])) { ab = x + 8; break; }
            // Rechts steht das Portal-Logo — bei langen Titeln („…“) nur wenige Punkte dahinter. Darum jede weisse Luecke
            // (ab 5 Spalten) als moegliches Titelende probieren, die am weitesten links zuerst nicht: von rechts nach
            // links, bis der Titel genau aufgeht (das Logo geht nie auf).
            var enden = new List<int>();
            int leer = 0;
            bool text = false;
            for (int x = ab; x < rechts; x++)
            {
                bool tinte = false;
                for (int y = von; y < bis && !tinte; y++)
                {
                    int f = p[y * b + x];
                    tinte = (f & 0xFF) <= 200 || ((f >> 8) & 0xFF) <= 200 || ((f >> 16) & 0xFF) <= 200;
                }
                if (tinte)
                {
                    if (text && leer >= 5) enden.Add(x - leer + 3);
                    text = true;
                    leer = 0;
                }
                else leer++;
            }
            enden.Add(rechts);
            for (int i = enden.Count - 1; i >= 0 && i >= enden.Count - 8 && titel.Length == 0; i--)
                titel = Lies(1, ab, enden[i], true, ref _pxKopfTitel);
        }
        if (baender.Count > 2)
        {
            var m = Regex.Match(Lies(2, 0, rechts, true, ref _pxKopfPreis), @"^(\d{1,3}(?:\.\d{3})*) EUR(?: \*)?$");
            if (m.Success) preis = int.Parse(m.Groups[1].Value.Replace(".", ""));
        }
        return new KopfDaten(zeile, titel, preis);
    }

    /// <summary>Die Zeile im frischen Listenbild suchen, im naechsten Bild an derselben Stelle bestaetigen und sofort
    /// anklicken. false = nicht (mehr) zu sehen oder an der Stelle liegt nicht die Liste.</summary>
    private bool Klicke(ListenZeile ziel)
    {
        for (int versuch = 0; versuch < 4; versuch++)
        {
            (int Oben, int Unten)? stelle = null;
            ulong summe = 0;
            int x = 0;
            using (var bild = AutoPointerFenster.Abbild(_liste))
            {
                if (bild == null) return false;
                var p = ListenLeser.Punkte(bild);
                var r = ListenLeser.FindeRaster(p, bild.Width, bild.Height);
                if (r == null) return false;
                foreach (var z in r.Zeilen)
                {
                    if (ListenLeser.Markiert(p, bild.Width, r, z) || Zeile(bild, p, r, z).Kennung != ziel.Kennung) continue;
                    stelle = z;
                    summe = ListenLeser.Summe(p, bild.Width, r, z);
                    x = r.Anbieter - 12;            // freie Stelle am rechten Rand der Beschreibungsspalte
                    break;
                }
            }
            if (stelle is not { } s) return false;
            // das Suchen kann gedauert haben (neue Zeilen wurden gelesen): im naechsten Bild muss die Zeile noch an
            // derselben Stelle stehen — dann sofort klicken
            using (var bild = AutoPointerFenster.Abbild(_liste))
            {
                if (bild == null) return false;
                var p = ListenLeser.Punkte(bild);
                var r = ListenLeser.FindeRaster(p, bild.Width, bild.Height);
                if (r == null || !r.Zeilen.Contains(s) || ListenLeser.Summe(p, bild.Width, r, s) != summe) continue;
            }
            int y = (s.Oben + s.Unten) / 2;
            return Native.ImDpiKontext(_liste, () =>
            {
                var q = new Native.POINT { X = x, Y = y };
                if (!Native.ClientToScreen(_liste, ref q) || Native.WindowFromPoint(q) != _liste) return false;
                if (!AutoPointerFenster.ImVordergrund(_haupt)) return false;
                Native.SetCursorPos(q.X, q.Y);
                Native.mouse_event(Native.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, UIntPtr.Zero);
                Native.mouse_event(Native.MOUSEEVENTF_LEFTUP, 0, 0, 0, UIntPtr.Zero);
                return true;
            });
        }
        return false;
    }
}

/// <summary>Probelauf an der Konsole (<c>--dauerlauf &lt;Sekunden&gt;</c>): zeigt, was der Dauerlauf sieht und tut. Es geht
/// nichts an den Server.</summary>
internal static class DauerlaufProbe
{
    public static async Task<int> LaufAsync(string[] args, string sekunden)
    {
        Native.AttachConsole(-1);
        Protokoll.DateiAktiv = false;
        Protokoll.Ausgabe = z => Console.WriteLine(z);
        int dauer = int.TryParse(sekunden, out int n) ? Math.Clamp(n, 1, 3600) : 20;
        int hoechstens = int.TryParse(KonsolenModus.Argument(args, "--hoechstens"), out int h) ? h : int.MaxValue;
        bool klicken = !args.Contains("--nur-liste");
        BeschreibungGanz.Diagnose = args.Contains("--ganz");
        Dauerlauf.KeinEchtesRad = args.Contains("--rad-wm");      // nur zum Messen: geschickte Nachricht statt echtem Rad
        var regeln = DauerlaufRegeln.Aus(KonsolenModus.Argument(args, "--regeln"));
        var ocr = TextErkennung.Erstelle(out string fehler);
        if (ocr == null && klicken) { Console.WriteLine(fehler); return 4; }

        // --senden: die gelesenen Autos in die Trefferliste legen. Nur mit ausdruecklichem --server UND --schluessel —
        // der Probelauf nimmt nie den gespeicherten Schluessel (der gehoert zum echten Konto).
        AutoSchnellDienst? dienst = null;
        var gesendet = new List<Task>();
        if (args.Contains("--senden"))
        {
            string? server = KonsolenModus.Argument(args, "--server"), schluessel = KonsolenModus.Argument(args, "--schluessel");
            if (string.IsNullOrWhiteSpace(server) || string.IsNullOrWhiteSpace(schluessel))
            {
                Console.WriteLine("--senden braucht --server <Adresse> und --schluessel <Programm-Schlüssel>.");
                return 5;
            }
            dienst = new AutoSchnellDienst(server, () => schluessel);
        }

        var uhr = Stopwatch.StartNew();
        var lauf = new Dauerlauf(ocr, () => regeln, e =>
        {
            var z = e.Zeile;
            string kopf = $"{uhr.ElapsedMilliseconds,6} ms  {e.Art,-11} {z.Portal} {z.Zeit[^8..]}  {(z.Privat == true ? "Privat " : z.Privat == false ? "Händler" : "?      ")} "
                          + $"{z.Preis,7} €  {z.Jahr} {z.Km,7} km  „{z.Titel}“";
            if (e.Fahrzeug is { } f)
            {
                string text = f.BeschreibungVoll ?? "";
                Console.WriteLine($"{kopf}\n            geladen nach {e.KlickBisGeladenMs} ms, fertig nach {e.GesamtMs} ms: {f.MarkeModellText}, {f.Preis} €, "
                                  + $"{f.Kilometer} km, EZ {f.EzMonat}/{f.EzJahr}, Beschreibung {text.Length} Zeichen, exakt={f.BeschreibungExakt}, "
                                  + $"{f.Merkmale.Count} Merkmale ({f.Kraftstoff}, {f.Getriebe}, Nr. {f.InseratId}), {f.Ausstattung.Count} Ausstattung");
                if (args.Contains("--text")) Console.WriteLine("            | " + text.Replace("\n", "\n            | "));
                if (dienst != null && AutoSchnellDienst.TrefferMeldbar(f))
                    gesendet.Add(Task.Run(async () =>
                    {
                        var start = uhr.ElapsedMilliseconds;
                        try
                        {
                            var a = await dienst.TrefferMeldenAsync(f, z);
                            Console.WriteLine($"{uhr.ElapsedMilliseconds,6} ms  gesendet    „{z.Titel}“ -> {a.Urteil} ({uhr.ElapsedMilliseconds - start} ms{(a.Neu ? "" : ", schon bekannt")}, Inserat {a.InseratUrl ?? "ohne Adresse"})");
                        }
                        catch (Exception ex) { Console.WriteLine($"{uhr.ElapsedMilliseconds,6} ms  senden fehlgeschlagen: {ex.Message}"); }
                    }));
                else if (dienst != null) Console.WriteLine("            nicht gesendet: Marke/Modell, Erstzulassung oder Kilometer fehlen");
            }
            else Console.WriteLine($"{kopf}{(e.Grund == null ? "" : "\n            " + e.Grund)}{(e.GesamtMs > 0 ? $" ({e.GesamtMs} ms)" : "")}");
        }, klicken);

        string hindernis = "", stand = "";
        if (KonsolenModus.Argument(args, "--bilder") is { } ordner) Dauerlauf.BildSpeichern = Path.Combine(ordner, "liste.png");
        while (uhr.ElapsedMilliseconds < dauer * 1000L)
        {
            bool tat = await lauf.SchrittAsync();
            if (lauf.Stand != stand && args.Contains("--stand")) Console.WriteLine($"{uhr.ElapsedMilliseconds,6} ms  -- {stand = lauf.Stand}");
            if (lauf.Hindernis != hindernis)
            {
                hindernis = lauf.Hindernis;
                if (hindernis.Length > 0) Console.WriteLine($"{uhr.ElapsedMilliseconds,6} ms  -- {hindernis}");
            }
            if (lauf.Gelesen + lauf.Falsch >= hoechstens) break;
            if (!tat) await Task.Delay(hindernis.Length > 0 ? 300 : 120);
        }
        await Task.WhenAll(gesendet);
        double s = uhr.ElapsedMilliseconds / 1000.0;
        Console.WriteLine($"\n{s:F1} s: {lauf.Neu} neue Zeilen ({lauf.Neu / s:F2} je s), aussortiert {lauf.Aussortiert}, ganz gelesen {lauf.Gelesen}, "
                          + $"verpasst {lauf.Verpasst}, falsch {lauf.Falsch}, warten noch {lauf.Wartend}");
        return 0;
    }
}
