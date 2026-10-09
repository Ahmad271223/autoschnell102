using System.Diagnostics;
using System.Drawing;
using System.Reflection;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Pruefung 09.10.2026 (Wunsch Ahmad: "dass AutoPointer nicht meckert"), Programm 1.5.14: das Programm darf
/// AutoPointer nie stoeren, haengen lassen oder ihm Fokus/Tasten nehmen. Alles, was ohne echten AutoPointer pruefbar ist —
/// die Win32-Aufrufe selbst sind in reinen Entscheidungsfunktionen gekapselt, die hier geprueft werden. Befund 3
/// ("Vergleichen" nur, wenn AutoPointer vorne liegt) steht in den UeberwacherTests, die Elektroautos in den
/// DetailLeserTests.</summary>
[Collection("Protokolldateien")]   // setzt Protokoll.DateiAktiv und schreibt ins gedrosselte Protokoll
public class Pruefung20261009Tests
{
    public Pruefung20261009Tests() => Protokoll.DateiAktiv = false;

    // ------------------------------------------------------------------ Befund 1
    [Fact]   // BringWindowToTop (SetWindowPos ohne SWP_ASYNCWINDOWPOS) stellte synchron an AutoPointers Thread zu — ist ganz weg
    public void Befund1_kein_BringWindowToTop_mehr_im_Programm()
    {
        Assert.Null(typeof(Native).GetMethod("BringWindowToTop", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static));
        // und ZurueckZu sagt jetzt, ob AutoPointer danach vorne liegt (Befund 2e)
        Assert.Equal(typeof(bool), typeof(BrowserOeffner).GetMethod("ZurueckZu")!.ReturnType);
    }

    // ------------------------------------------------------------------ Befund 2
    private static BrowserOeffner.FensterLage Vor(bool gueltig = true, bool minimiert = false, bool schonVorne = false,
                                                   bool aktiviert = true, bool hatPopup = false, bool haengt = false,
                                                   bool vorneHaengt = false, uint fremd = 11, uint ich = 22) =>
        new(gueltig, minimiert, schonVorne, aktiviert, hatPopup, haengt, vorneHaengt, fremd, ich);

    [Fact]
    public void Befund2_Normalfall_Browser_vorne_Hauptfenster_holen_und_an_dessen_Thread_haengen()
    {
        var plan = BrowserOeffner.Planen(Vor());
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Hauptfenster, plan.Ziel);
        Assert.True(plan.Anhaengen);
    }

    [Fact]   // 2a: liegt AutoPointer schon vorne, nie an seinen eigenen Thread haengen — nichts tun, Ergebnis "vorne"
    public void Befund2a_schon_vorne_dann_nichts_anfassen()
    {
        var plan = BrowserOeffner.Planen(Vor(schonVorne: true));
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.SchonVorne, plan.Ziel);
        Assert.False(plan.Anhaengen);
    }

    [Fact]   // 2b: ein minimiertes Fenster wird nie zum Vordergrund gemacht (Windows stellt es nicht wieder her)
    public void Befund2b_minimiert_dann_nichts()
    {
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Nichts, BrowserOeffner.Planen(Vor(minimiert: true)).Ziel);
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Nichts, BrowserOeffner.Planen(Vor(minimiert: true, schonVorne: true)).Ziel);
    }

    [Fact]   // 2c: Modal-Dialog offen (Hauptfenster deaktiviert) -> der Dialog ist das Ziel; ohne Dialog nichts
    public void Befund2c_bei_Modal_Dialog_den_Dialog_holen_nie_das_deaktivierte_Hauptfenster()
    {
        var mitDialog = BrowserOeffner.Planen(Vor(aktiviert: false, hatPopup: true));
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Popup, mitDialog.Ziel);
        Assert.True(mitDialog.Anhaengen);
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Nichts, BrowserOeffner.Planen(Vor(aktiviert: false)).Ziel);
    }

    [Fact]   // 2d: an ein haengendes Vordergrundfenster (eingefrorener Browser) nie anhaengen; haengt AutoPointer selbst: nichts
    public void Befund2d_nie_an_haengende_Fenster_haengen()
    {
        var plan = BrowserOeffner.Planen(Vor(vorneHaengt: true));
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Hauptfenster, plan.Ziel);
        Assert.False(plan.Anhaengen);
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Nichts, BrowserOeffner.Planen(Vor(haengt: true)).Ziel);
    }

    [Fact]   // nie an den eigenen Thread, nie an "Thread 0" (kein Vordergrundfenster); ungueltiges Handle: nichts
    public void Befund2_eigener_Thread_unbekannter_Thread_ungueltiges_Handle()
    {
        Assert.False(BrowserOeffner.Planen(Vor(fremd: 22, ich: 22)).Anhaengen);
        Assert.False(BrowserOeffner.Planen(Vor(fremd: 0)).Anhaengen);
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Hauptfenster, BrowserOeffner.Planen(Vor(fremd: 0)).Ziel);
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Nichts, BrowserOeffner.Planen(Vor(gueltig: false)).Ziel);
        Assert.Equal(BrowserOeffner.ZurueckZuZiel.Nichts, BrowserOeffner.Planen(Vor(gueltig: false, schonVorne: true)).Ziel);
    }

    // ------------------------------------------------------------------ Befund 4
    [Fact]   // Windows verwendet Handles wieder: nach einem AutoPointer-Neustart darf ein fremdes Fenster nie fotografiert werden
    public void Befund4_gemerkte_Tabellen_muessen_Klasse_und_Thread_von_AutoPointer_haben()
    {
        var haupt = new AutoPointerFenster.FensterKennung(AutoPointerFenster.HauptfensterKlasse, 5);
        Assert.True(AutoPointerFenster.GehoertZusammen(haupt, new("TcxGridSite", 5), new("TcxGridSite", 5)));
        Assert.True(AutoPointerFenster.GehoertZusammen(haupt, new("TcxGridSite", 5), null));
        // Handle der Technik-Tabelle jetzt ein Browser-Fenster
        Assert.False(AutoPointerFenster.GehoertZusammen(haupt, new("Chrome_RenderWidgetHostHWND", 5), null));
        // richtige Klasse, aber anderer Thread (= anderer Prozess, z. B. ein zweites AutoPointer)
        Assert.False(AutoPointerFenster.GehoertZusammen(haupt, new("TcxGridSite", 9), null));
        Assert.False(AutoPointerFenster.GehoertZusammen(haupt, new("TcxGridSite", 5), new("TcxGridSite", 9)));
        Assert.False(AutoPointerFenster.GehoertZusammen(haupt, new("TcxGridSite", 5), new("Edit", 5)));
        // das Hauptfenster-Handle selbst wiederverwendet
        Assert.False(AutoPointerFenster.GehoertZusammen(new("Chrome_WidgetWin_1", 5), new("TcxGridSite", 5), null));
        Assert.False(AutoPointerFenster.GehoertZusammen(new(AutoPointerFenster.HauptfensterKlasse, 0), new("TcxGridSite", 0), null));
        Assert.Equal("TMainForm", AutoPointerFenster.HauptfensterKlasse);
        Assert.Equal("TcxGridSite", AutoPointerFenster.TabellenKlasse);
    }

    // ------------------------------------------------------------------ Befund 5 (+ 4 am Hauptfenster)
    private sealed class Fenster
    {
        public long Jetzt = 1_000_000;
        public int HauptSuchen, DetailSuchen;
        public DetailAnsicht? Details;
        public bool HauptGueltig = true;
        public bool Vorne = true;
        public readonly FensterZugriff Zugriff;

        public Fenster()
        {
            Zugriff = new FensterZugriff(
                () => { HauptSuchen++; return HauptGueltig ? (IntPtr)42 : IntPtr.Zero; },
                h => h == (IntPtr)42 && HauptGueltig,
                h => { DetailSuchen++; return Details; },
                d => Details != null && HauptGueltig,
                _ => Vorne,
                _ => 7UL);
        }

        public AutoPointerQuelle Quelle() => new(null, () => new Einstellungen(), Zugriff, () => Jetzt);
    }

    [Fact]   // FindeDetails (EnumChildWindows ueber 500–1.500 Kindfenster) lief alle 250 ms, solange kein Auto angezeigt wird
    public void Befund5_Detailsuche_hoechstens_jede_Sekunde_solange_AutoPointer_kein_Auto_zeigt()
    {
        var w = new Fenster();
        var q = w.Quelle();
        for (int i = 0; i < 40; i++)                                   // 10 s im 250-ms-Takt
        {
            Assert.Equal(Lage.KeineDetails, q.Pruefe().Lage);
            w.Jetzt += 250;
        }
        Assert.Equal(1, w.HauptSuchen);                                // das Hauptfenster wurde einmal gefunden
        Assert.Equal(10, w.DetailSuchen);                              // vorher 40
        Assert.Equal(1000, AutoPointerQuelle.DetailSucheMs);
        Assert.Equal((IntPtr)42, q.Hauptfenster);
        // ein Auto erscheint: die naechste faellige Suche findet es — danach keine Suche mehr, nur die Pruefsumme
        w.Details = new DetailAnsicht((IntPtr)42, (IntPtr)43, (IntPtr)44);
        Assert.Equal(Lage.Details, q.Pruefe().Lage);
        for (int i = 0; i < 8; i++) { w.Jetzt += 250; Assert.Equal(Lage.Details, q.Pruefe().Lage); }
        Assert.Equal(11, w.DetailSuchen);
        Assert.True(q.ImVordergrund);
        w.Vorne = false;
        Assert.False(q.ImVordergrund);
    }

    [Fact]   // ohne AutoPointer bleibt die Hauptfenster-Suche bei 2 s; ein wiederverwendetes Hauptfenster-Handle wird neu gesucht
    public void Befund4_5_Hauptfenster_alle_2_Sekunden_und_nach_Handle_Wiederverwendung_neu()
    {
        var w = new Fenster { HauptGueltig = false };
        var q = w.Quelle();
        for (int i = 0; i < 40; i++) { Assert.Equal(Lage.KeinAutoPointer, q.Pruefe().Lage); w.Jetzt += 250; }
        Assert.Equal(5, w.HauptSuchen);                                // alle 2 s
        Assert.Equal(0, w.DetailSuchen);
        Assert.Equal(2000, AutoPointerQuelle.HauptSucheMs);

        // AutoPointer da, Auto angezeigt
        w.HauptGueltig = true;
        w.Details = new DetailAnsicht((IntPtr)42, (IntPtr)43, (IntPtr)44);
        Assert.Equal(Lage.Details, q.Pruefe().Lage);
        int suchen = w.HauptSuchen;
        // AutoPointer beendet, das Handle 42 gehoert jetzt einem fremden Fenster (IsWindow sagt "ja", Klasse stimmt nicht)
        w.HauptGueltig = false;
        w.Jetzt += 2500;
        Assert.Equal(Lage.KeinAutoPointer, q.Pruefe().Lage);
        Assert.Equal(IntPtr.Zero, q.Hauptfenster);                     // nie das fremde Fenster behalten
        Assert.Equal(suchen + 1, w.HauptSuchen);
        Assert.False(q.ImVordergrund);
    }

    // ------------------------------------------------------------------ Befund 7
    private static void ImSta(Action a)
    {
        Exception? fehler = null;
        var t = new Thread(() => { try { a(); } catch (Exception ex) { fehler = ex; } });
        t.SetApartmentState(ApartmentState.STA);
        t.Start();
        t.Join();
        if (fehler != null) throw fehler;
    }

    [Fact]   // der Verbinden-Dialog liegt nicht mehr "immer oben" ueber AutoPointer
    public void Befund7_Verbinden_Dialog_ist_nicht_TopMost()
    {
        ImSta(() =>
        {
            var dienst = new AutoSchnellDienst("https://app.example.test", () => null);
            using var ohneAnlass = new VerbindenForm(dienst, "Dieses Programm ist nicht (mehr) verbunden.", aktivieren: false);
            ohneAnlass.CreateControl();
            Assert.False(ohneAnlass.TopMost);
            using var aufKlick = new VerbindenForm(dienst, null);
            aufKlick.CreateControl();
            Assert.False(aufKlick.TopMost);
        });
    }

    [Fact]   // bei Verbindungsverlust nur eine Sprechblase mit dem Weg zur Leiste — kurz genug fuer Windows
    public void Befund7_Verbindungsverlust_ist_eine_kurze_Sprechblase_mit_dem_Weg_zur_Leiste()
    {
        string kurz = TrayApp.VerlustText("Dieses Programm ist nicht (mehr) verbunden – ein anderer PC hat sich mit dem Konto verbunden.");
        Assert.True(kurz.Length <= Hinweis.MaxZeichen, kurz.Length.ToString());
        Assert.Contains("„NICHT VERBUNDEN“", kurz);
        Assert.StartsWith("Dieses Programm ist nicht (mehr) verbunden", kurz);
        string lang = TrayApp.VerlustText(string.Join(" ", Enumerable.Repeat("Wort", 80)));
        Assert.True(lang.Length <= Hinweis.MaxZeichen, lang.Length.ToString());
        Assert.EndsWith(TrayApp.VerlustAnleitung, lang);
        Assert.Contains("Leiste", TrayApp.VerlustAnleitung);
    }

    // ------------------------------------------------------------------ Befund 8
    [Fact]   // wirft das Kopf-Abbild, war das Technik-Bild schon erzeugt, aber in keinem using: Bitmap-Leck
    public void Befund8_wirft_das_zweite_Abbild_wird_das_erste_freigegeben()
    {
        var erstes = new Bitmap(4, 4);
        var ex = Assert.Throws<InvalidOperationException>(
            () => AutoPointerFenster.Abbilder(() => erstes, () => throw new InvalidOperationException("GDI+ generic error")));
        Assert.Equal("GDI+ generic error", ex.Message);
        Assert.ThrowsAny<Exception>(() => erstes.Width);                // freigegeben
        // Normalfall: beide zurueck (Kopf darf fehlen), nichts freigegeben
        var (technik, kopf) = AutoPointerFenster.Abbilder(() => new Bitmap(4, 4), () => null);
        Assert.NotNull(technik);
        Assert.Null(kopf);
        Assert.Equal(4, technik!.Width);
        technik.Dispose();
        // wirft das erste, gibt es nichts freizugeben — die Ausnahme kommt durch
        Assert.Throws<InvalidOperationException>(() => AutoPointerFenster.Abbilder(() => throw new InvalidOperationException("x"), () => null));
    }

    // ------------------------------------------------------------------ Befund 9
    [Fact]   // nach einer Zeitueberschreitung stapelten sich Auftraege (je ~10 MB) auf einer toten OcrEngine
    public async Task Befund9_haengende_Engine_bekommt_keinen_neuen_Auftrag_bis_der_alte_zurueck_ist()
    {
        var wache = new TextErkennung.Haengewache();
        var haengt = new TaskCompletionSource<int>(TaskCreationOptions.RunContinuationsAsynchronously);
        var aufgeraeumt = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        int gestartet = 0;
        Task<int> Auftrag() { gestartet++; return haengt.Task; }

        Assert.False(wache.Haengt);
        var ex = await Assert.ThrowsAsync<TimeoutException>(
            () => wache.MitFrist(Auftrag, TimeSpan.FromMilliseconds(100), () => aufgeraeumt.SetResult()));
        Assert.Contains("nicht geantwortet", ex.Message);
        Assert.True(wache.Haengt);
        Assert.Equal(1, gestartet);

        // jeder weitere Versuch (Takt 3x, "Vergleichen" beliebig oft): sofort derselbe Lesefehler, NICHTS gestartet
        var uhr = Stopwatch.StartNew();
        for (int i = 0; i < 5; i++)
        {
            var noch = await Assert.ThrowsAsync<TimeoutException>(() => wache.MitFrist(Auftrag, TimeSpan.FromSeconds(10)));
            Assert.Equal(TextErkennung.Haengewache.HaengtNoch, noch.Message);
        }
        Assert.Equal(1, gestartet);
        Assert.True(uhr.ElapsedMilliseconds < 5000, uhr.ElapsedMilliseconds.ToString());
        Assert.False(aufgeraeumt.Task.IsCompleted);                    // das Bild bleibt, solange der Auftrag laeuft

        // kommt der Auftrag doch noch zurueck: aufgeraeumt und die Engine wieder frei
        haengt.SetResult(1);
        await aufgeraeumt.Task.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.False(wache.Haengt);
        Assert.Equal(7, await wache.MitFrist(() => Task.FromResult(7), TimeSpan.FromSeconds(1)));

        // ein Auftrag, der FEHLSCHLAEGT (nicht haengt), sperrt nicht
        await Assert.ThrowsAsync<InvalidOperationException>(
            () => wache.MitFrist(() => Task.FromException<int>(new InvalidOperationException("kaputt")), TimeSpan.FromSeconds(1)));
        Assert.False(wache.Haengt);
    }

    // ------------------------------------------------------------------ Befund 11a
    [Fact]   // das Woerterbuch der gedrosselten Meldungen wuchs mit jedem neuen Schluessel (Fehlertexte mit Handles/Zahlen)
    public void Befund11a_gedrosselte_Schluessel_bleiben_begrenzt_die_aeltesten_fliegen_raus()
    {
        string basis = "test:" + Guid.NewGuid().ToString("N") + ":";
        int n = Protokoll.GedrosseltMax + 50;
        for (int i = 0; i < n; i++) Protokoll.SchreibeGedrosselt(basis + i, "Fehler " + i, TimeSpan.FromMinutes(1));
        Assert.True(Protokoll.GedrosseltAnzahl <= Protokoll.GedrosseltMax, Protokoll.GedrosseltAnzahl.ToString());
        Assert.Equal(200, Protokoll.GedrosseltMax);

        var zeilen = new List<string>();
        Protokoll.NeueZeile += zeilen.Add;
        try
        {
            // der neueste Schluessel ist noch gedrosselt (keine zweite Zeile) …
            Protokoll.SchreibeGedrosselt(basis + (n - 1), "Fehler neu", TimeSpan.FromMinutes(1));
            Assert.Empty(zeilen);
            // … der aelteste ist vergessen und wird wieder geschrieben (ohne "unterdrueckt": sein Zaehler ist mit weg)
            Protokoll.SchreibeGedrosselt(basis + 0, "Fehler alt", TimeSpan.FromMinutes(1));
            Assert.Single(zeilen, z => z.Contains("Fehler alt") && !z.Contains("unterdrückt"));
            Assert.True(Protokoll.GedrosseltAnzahl <= Protokoll.GedrosseltMax);
        }
        finally { Protokoll.NeueZeile -= zeilen.Add; }
    }
}
