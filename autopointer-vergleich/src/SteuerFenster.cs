namespace AutoPointerVergleich;

/// <summary>Was das Steuerfenster anzeigt (jede Sekunde neu abgefragt).</summary>
/// <param name="Sperrgrund">Paket 2 (A8): Text des Servers zur Sperre (402/403 bei der Lizenzpruefung), sonst null.</param>
/// <param name="LetzteHinweise">Pruefung 08.10.2026 (1.5.9, C): die letzten Hinweise ganz, mit Uhrzeit, der neueste
/// zuerst — die Sprechblasen sind kurz und verschwinden.</param>
/// <param name="NurGemerkt">1.5.9 (D): das letzte Auto wurde beim Start nur gemerkt, noch nicht verglichen.</param>
/// <param name="VertragLaeuft">Pruefung 09.10.2026 (Vertragsweg, P1): "Vertrag" laeuft gerade (Lesung abwarten, App
/// suchen/starten, Rueckmeldung der App) — der Knopf zeigt "Vertrag …" und ist gesperrt.</param>
internal sealed record FensterZustand(Status Status, bool AutomatikAn, bool Verbunden, string VerbundenAls,
                                      string? LetztesAuto, bool HatInseratLink, string? LetzteMeldung, bool Probelauf,
                                      string? Sperrgrund = null, IReadOnlyList<string>? LetzteHinweise = null,
                                      bool NurGemerkt = false, bool VertragLaeuft = false);

/// <summary>Wunsch Ahmad 03.10.2026: "man kann nicht stoppen, aktivieren, nichts — das sollen Buttons sein".
/// Seit 1.5.8 (08.10.2026, "zu viele Knoepfe fuer dieselbe Sache"): bedient wird nur noch ueber die kleine Leiste
/// (Start/Stopp, Vergleichen, Vertrag, Mehr). Dieses Fenster zeigt Status, letztes Auto, Verbindung und Hilfe —
/// Verbinden, Systemcheck, Einstellungen, Beenden. Ein zweiter Start des Programms holt es nach vorne.</summary>
internal sealed class SteuerFenster : Form
{
    private const int Breite = 420;

    private readonly Func<FensterZustand> _zustand;
    private readonly Panel _ampel;
    private readonly Label _titel, _unterzeile, _verbindung, _letztes, _meldung;
    private readonly Button _verbinden, _einstellungen, _beenden;
    private readonly System.Windows.Forms.Timer _takt;
    private FensterZustand? _zuletzt;

    /// <summary>true nur bei "Programm beenden" — sonst legt das X das Fenster in die Taskleiste.</summary>
    private bool _wirklichSchliessen;
    /// <summary>Mit Leiste: X blendet das Fenster ganz aus (die Leiste bleibt sichtbar).</summary>
    private bool _nurAusblenden;
    private readonly Label _hinweisX;

    public event Action? Verbinden, Trennen, EinstellungenOeffnen, Beenden, SystemcheckOeffnen;

    public SteuerFenster(Func<FensterZustand> zustand)
    {
        _zustand = zustand;
        Text = "AutoSchnell Vergleich";
        Font = new Font("Segoe UI", 10f);
        FormBorderStyle = FormBorderStyle.FixedSingle;
        MaximizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        AutoScaleMode = AutoScaleMode.Dpi;
        Icon = Symbole.Icon(Symbole.Aktiv);
        ShowInTaskbar = true;
        BackColor = Color.White;

        var stapel = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, Padding = new Padding(16),
        };

        // Grosse farbige Anzeige: AKTIV / GESTOPPT / NICHT VERBUNDEN ...
        _ampel = new Panel { Width = Breite, Height = 74, Margin = new Padding(0, 0, 0, 12), BackColor = Symbole.Pause };
        _titel = new Label
        {
            AutoSize = false, Dock = DockStyle.Top, Height = 40, ForeColor = Color.White,
            Font = new Font("Segoe UI", 16f, FontStyle.Bold), TextAlign = ContentAlignment.BottomCenter,
        };
        _unterzeile = new Label
        {
            AutoSize = false, Dock = DockStyle.Fill, ForeColor = Color.White, TextAlign = ContentAlignment.TopCenter,
        };
        _ampel.Controls.Add(_unterzeile);
        _ampel.Controls.Add(_titel);
        stapel.Controls.Add(_ampel);

        // 1.5.8: bedient wird ueber die Leiste — hier steht, wie
        var bedienung = Info();
        bedienung.Text = "Bedienen mit der kleinen Leiste unten am Bildschirm:\n"
                         + "• Status-Feld anklicken = Start / Stopp\n"
                         + "• „Vergleichen“ = aktuelles Auto jetzt vergleichen\n"
                         + "• „Vertrag“ = zuletzt angeklicktes Auto in AutoSchnell öffnen\n"
                         + "• „Mehr“ = letzter Vergleich, Einstellungen, Systemcheck, Verbindung, Beenden";
        stapel.Controls.Add(bedienung);

        _letztes = Info();
        _letztes.Margin = new Padding(0, 12, 0, 0);
        _meldung = Info();
        _meldung.ForeColor = Color.FromArgb(150, 80, 0);
        stapel.Controls.Add(_letztes);
        stapel.Controls.Add(_meldung);

        _verbindung = Info();
        _verbindung.Margin = new Padding(0, 12, 0, 0);
        stapel.Controls.Add(_verbindung);
        _verbinden = Knopf("Mit AutoSchnell verbinden …");
        stapel.Controls.Add(_verbinden);
        // Pruefbericht 03.10.2026 (Nr. 6/8): "Laeuft alles?" — Windows, Texterkennung, Server, Abo, AutoPointer, Probe
        var systemcheck = Knopf("Systemcheck: läuft alles?");
        systemcheck.Name = "systemcheck";
        systemcheck.Click += (_, _) => SystemcheckOeffnen?.Invoke();
        stapel.Controls.Add(systemcheck);

        var unten = Reihe();
        unten.Margin = new Padding(0, 14, 0, 0);
        // Wunsch Ahmad 04.10.2026: das Protokoll wird nirgends angezeigt (nur verschluesselt gespeichert)
        int haelfte = (Breite - 10) / 2;
        _einstellungen = Knopf("Einstellungen", haelfte);
        _beenden = Knopf("Beenden", haelfte);
        _beenden.ForeColor = Symbole.Fehler;
        _einstellungen.Margin = new Padding(0);
        _beenden.Margin = new Padding(10, 0, 0, 0);
        unten.Controls.Add(_einstellungen);
        unten.Controls.Add(_beenden);
        stapel.Controls.Add(unten);

        _hinweisX = new Label
        {
            AutoSize = true, MaximumSize = new Size(Breite, 0), ForeColor = SystemColors.GrayText,
            Font = new Font("Segoe UI", 8.5f), Margin = new Padding(0, 10, 0, 0),
        };
        NurAusblenden(true);
        stapel.Controls.Add(_hinweisX);
        Controls.Add(stapel);

        _verbinden.Click += (_, _) =>
        {
            if (_zuletzt?.Verbunden == true) Trennen?.Invoke(); else Verbinden?.Invoke();
            Aktualisieren();
        };
        _einstellungen.Click += (_, _) => EinstellungenOeffnen?.Invoke();
        _beenden.Click += (_, _) => Beenden?.Invoke();

        _takt = new System.Windows.Forms.Timer { Interval = 1000 };
        _takt.Tick += (_, _) => Aktualisieren();
        _takt.Start();
        Aktualisieren();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!_wirklichSchliessen && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            if (_nurAusblenden) Hide();
            else WindowState = FormWindowState.Minimized;
            return;
        }
        _takt.Stop();
        base.OnFormClosing(e);
    }

    /// <summary>Mit Leiste blendet das X das Fenster aus, ohne Leiste verkleinert es in die Taskleiste.</summary>
    public void NurAusblenden(bool ja)
    {
        _nurAusblenden = ja;
        _hinweisX.Text = ja
            ? "Fenster schließen (X) = das Programm läuft mit der kleinen Leiste weiter. Ganz ausschalten: „Beenden“."
            : "Fenster schließen (X) = nur verkleinern, das Programm läuft unten in der Taskleiste weiter. "
              + "Ganz ausschalten: „Beenden“.";
    }

    /// <summary>"Programm beenden": diesmal wirklich schliessen.</summary>
    public void EndgueltigSchliessen()
    {
        _wirklichSchliessen = true;
        Close();
    }

    /// <summary>Nach vorne holen (Doppelklick aufs Symbol, zweiter Programmstart).</summary>
    public void Zeigen()
    {
        if (!Visible) Show();
        if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal;
        Activate();
        Native.SetForegroundWindow(Handle);
        Aktualisieren();
    }

    public void Aktualisieren()
    {
        FensterZustand z;
        try { z = _zustand(); }
        catch (Exception ex) { Protokoll.Schreibe("Fensteranzeige: " + ex.Message); return; }
        _zuletzt = z;

        var (farbe, titel, unter) = Anzeige(z);
        _ampel.BackColor = farbe;
        _titel.Text = titel;
        _unterzeile.Text = unter;
        Text = "AutoSchnell Vergleich – " + titel.ToLowerInvariant() + (z.Probelauf ? " (Probelauf)" : "");

        _letztes.Text = z.LetztesAuto != null
            ? "Letztes Auto: " + z.LetztesAuto
              + (z.NurGemerkt ? "   (noch nicht verglichen – „Vergleichen“ drücken)"
                 : z.HatInseratLink ? "" : "   (Inserat-Adresse fehlt – für den Vertrag selbst einfügen)")
            : "Letztes Auto: noch keins – in AutoPointer ein Inserat anklicken.";
        _meldung.Text = HinweisText(z);
        _meldung.Visible = _meldung.Text.Length > 0;
        _verbindung.Text = z.Verbunden ? "Verbunden: " + z.VerbundenAls : "Nicht mit AutoSchnell verbunden.";
        _verbindung.ForeColor = z.Verbunden ? SystemColors.ControlText : Symbole.Fehler;
        _verbinden.Text = z.Verbunden ? "Verbindung trennen" : "Mit AutoSchnell verbinden …";
    }

    /// <summary>Pruefung 08.10.2026 (1.5.9, C): "Letzte Hinweise" — die ganzen Erklaerungen mit Uhrzeit (die Sprechblase
    /// zeigt hoechstens 150 Zeichen). Ohne Liste wie bisher die letzte Meldung. (rein, fuer Tests)</summary>
    internal static string HinweisText(FensterZustand z)
    {
        if (z.LetzteHinweise is { Count: > 0 } liste)
            return "Letzte Hinweise:\n" + string.Join("\n", liste.Select(h => "• " + h));
        return z.LetzteMeldung ?? "";
    }

    /// <summary>Farbe, grosse Zeile, kleine Zeile — fuer jeden Zustand genau eine klare Aussage.</summary>
    internal static (Color Farbe, string Titel, string Unterzeile) Anzeige(FensterZustand z)
    {
        if (!z.Verbunden || z.Status == Status.NichtVerbunden)
            return (Symbole.Fehler, "NICHT VERBUNDEN", "Unten „Mit AutoSchnell verbinden …“ drücken.");
        if (z.Status == Status.TexterkennungFehlt)
            // Paket 2 (A9): Sprachpaket fehlt — das Programm versucht es jede Minute erneut, kein Neustart noetig
            return (Symbole.Fehler, "TEXTERKENNUNG FEHLT",
                    "Windows-Texterkennung „Deutsch“ fehlt: Einstellungen → Zeit und Sprache → Sprache → Deutsch → "
                    + "Optische Zeichenerkennung installieren. Wird jede Minute erneut geprüft.");
        if (z.Status == Status.Gesperrt)
            // Paket 2 (A8): der Text des Servers ("Kein aktives AutoSchnell-Abo …", "Für dein Konto nicht freigeschaltet.")
            return (Symbole.Fehler, "GESPERRT", z.Sperrgrund ?? "Kein aktives Abo oder nicht freigeschaltet.");
        if (!z.AutomatikAn)
            return (Symbole.Pause, "GESTOPPT", "Es öffnet sich nichts. Zum Starten das Status-Feld der Leiste anklicken.");
        if (z.Status == Status.KeinAutoPointer)
            return (Symbole.Warten, "AKTIV – WARTET", "AutoPointer ist nicht offen bzw. zeigt kein Auto.");
        return (Symbole.Aktiv, "AKTIV", "In AutoPointer ein Auto anklicken – die Vergleiche öffnen sich.");
    }

    private static FlowLayoutPanel Reihe() =>
        new() { FlowDirection = FlowDirection.LeftToRight, WrapContents = false, AutoSize = true, Margin = new Padding(0) };

    private static Button Knopf(string text, int breite = Breite) =>
        new()
        {
            Text = text, Width = breite, Height = 38, Margin = new Padding(0, 6, 0, 0), Cursor = Cursors.Hand,
            UseVisualStyleBackColor = true,
        };

    private static Label Info() =>
        new() { AutoSize = true, MaximumSize = new Size(Breite, 0), Margin = new Padding(0, 4, 0, 0) };
}
