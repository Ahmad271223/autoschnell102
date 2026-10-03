namespace AutoPointerVergleich;

/// <summary>Was das Steuerfenster anzeigt (jede Sekunde neu abgefragt).</summary>
internal sealed record FensterZustand(Status Status, bool AutomatikAn, bool Verbunden, string VerbundenAls,
                                      string? LetztesAuto, bool HatInseratLink, string? LetzteMeldung, bool Probelauf);

/// <summary>Wunsch Ahmad 03.10.2026: "man kann nicht stoppen, aktivieren, nichts — das sollen Buttons sein".
/// Sichtbares Fenster mit Knoepfen statt nur eines Symbols im Infobereich. Solange das Programm laeuft,
/// steht es in der Taskleiste; Schliessen (X) verkleinert nur, ganz aus nur ueber "Programm beenden".
/// Ein zweiter Start des Programms holt dieses Fenster nach vorne.</summary>
internal sealed class SteuerFenster : Form
{
    private const int Breite = 420;

    private readonly Func<FensterZustand> _zustand;
    private readonly Panel _ampel;
    private readonly Label _titel, _unterzeile, _verbindung, _letztes, _meldung;
    private readonly Button _aktivieren, _stoppen, _jetzt, _erneut, _vertrag, _verbinden, _einstellungen, _protokoll, _beenden;
    private readonly System.Windows.Forms.Timer _takt;
    private FensterZustand? _zuletzt;

    /// <summary>true nur bei "Programm beenden" — sonst legt das X das Fenster in die Taskleiste.</summary>
    private bool _wirklichSchliessen;
    /// <summary>Mit Leiste: X blendet das Fenster ganz aus (die Leiste bleibt sichtbar).</summary>
    private bool _nurAusblenden;
    private readonly Label _hinweisX;

    public event Action? Aktivieren, Stoppen, JetztVergleichen, LetztenOeffnen, VertragOeffnen,
        Verbinden, Trennen, EinstellungenOeffnen, ProtokollOeffnen, Beenden;

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

        // Die zwei wichtigsten Knoepfe
        var reihe = Reihe();
        _aktivieren = Gross("▶  Aktivieren", Symbole.Aktiv);
        _stoppen = Gross("■  Stoppen", Symbole.Fehler);
        _stoppen.Margin = new Padding(10, 0, 0, 0);
        reihe.Controls.Add(_aktivieren);
        reihe.Controls.Add(_stoppen);
        stapel.Controls.Add(reihe);

        _jetzt = Knopf("Aktuelles Auto jetzt vergleichen");
        _erneut = Knopf("Letzten Vergleich nochmal öffnen");
        _vertrag = Knopf("Kaufvertrag: Auto in AutoSchnell öffnen");
        _jetzt.Margin = new Padding(0, 14, 0, 0);
        stapel.Controls.Add(_jetzt);
        stapel.Controls.Add(_erneut);
        stapel.Controls.Add(_vertrag);

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

        var unten = Reihe();
        unten.Margin = new Padding(0, 14, 0, 0);
        int drittel = (Breite - 20) / 3;
        _einstellungen = Knopf("Einstellungen", drittel);
        _protokoll = Knopf("Protokoll", drittel);
        _beenden = Knopf("Beenden", drittel);
        _beenden.ForeColor = Symbole.Fehler;
        _einstellungen.Margin = new Padding(0);
        _protokoll.Margin = _beenden.Margin = new Padding(10, 0, 0, 0);
        unten.Controls.Add(_einstellungen);
        unten.Controls.Add(_protokoll);
        unten.Controls.Add(_beenden);
        stapel.Controls.Add(unten);

        _hinweisX = new Label
        {
            AutoSize = true, MaximumSize = new Size(Breite, 0), ForeColor = SystemColors.GrayText,
            Font = new Font("Segoe UI", 8.5f), Margin = new Padding(0, 10, 0, 0),
        };
        NurAusblenden(false);
        stapel.Controls.Add(_hinweisX);
        Controls.Add(stapel);

        _aktivieren.Click += (_, _) => { Aktivieren?.Invoke(); Aktualisieren(); };
        _stoppen.Click += (_, _) => { Stoppen?.Invoke(); Aktualisieren(); };
        _jetzt.Click += (_, _) => JetztVergleichen?.Invoke();
        _erneut.Click += (_, _) => LetztenOeffnen?.Invoke();
        _vertrag.Click += (_, _) => VertragOeffnen?.Invoke();
        _verbinden.Click += (_, _) =>
        {
            if (_zuletzt?.Verbunden == true) Trennen?.Invoke(); else Verbinden?.Invoke();
            Aktualisieren();
        };
        _einstellungen.Click += (_, _) => EinstellungenOeffnen?.Invoke();
        _protokoll.Click += (_, _) => ProtokollOeffnen?.Invoke();
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

        Faerben(_aktivieren, !z.AutomatikAn, Symbole.Aktiv, z.AutomatikAn ? "✓  Ist aktiv" : "▶  Aktivieren");
        Faerben(_stoppen, z.AutomatikAn, Symbole.Fehler, z.AutomatikAn ? "■  Stoppen" : "✓  Ist gestoppt");

        _jetzt.Enabled = z.Verbunden;
        _erneut.Enabled = z.Verbunden && z.LetztesAuto != null;
        _vertrag.Enabled = z.Verbunden && z.LetztesAuto != null;
        _letztes.Text = z.LetztesAuto != null
            ? "Letztes Auto: " + z.LetztesAuto + (z.HatInseratLink ? "" : "   (Inserat-Adresse fehlt – für den Vertrag selbst einfügen)")
            : "Letztes Auto: noch keins – in AutoPointer ein Inserat anklicken.";
        _meldung.Text = z.LetzteMeldung ?? "";
        _meldung.Visible = !string.IsNullOrEmpty(z.LetzteMeldung);
        _verbindung.Text = z.Verbunden ? "Verbunden: " + z.VerbundenAls : "Nicht mit AutoSchnell verbunden.";
        _verbindung.ForeColor = z.Verbunden ? SystemColors.ControlText : Symbole.Fehler;
        _verbinden.Text = z.Verbunden ? "Verbindung trennen" : "Mit AutoSchnell verbinden …";
    }

    /// <summary>Farbe, grosse Zeile, kleine Zeile — fuer jeden Zustand genau eine klare Aussage.</summary>
    internal static (Color Farbe, string Titel, string Unterzeile) Anzeige(FensterZustand z)
    {
        if (!z.Verbunden || z.Status == Status.NichtVerbunden)
            return (Symbole.Fehler, "NICHT VERBUNDEN", "Unten „Mit AutoSchnell verbinden …“ drücken.");
        if (z.Status == Status.Gesperrt)
            return (Symbole.Fehler, "GESPERRT", "Kein aktives Abo oder nicht freigeschaltet.");
        if (!z.AutomatikAn)
            return (Symbole.Pause, "GESTOPPT", "Es öffnet sich nichts. Zum Starten „Aktivieren“ drücken.");
        if (z.Status == Status.KeinAutoPointer)
            return (Symbole.Warten, "AKTIV – WARTET", "AutoPointer ist nicht offen bzw. zeigt kein Auto.");
        return (Symbole.Aktiv, "AKTIV", "In AutoPointer ein Auto anklicken – die Vergleiche öffnen sich.");
    }

    private static void Faerben(Button b, bool klickbar, Color farbe, string text)
    {
        b.Text = text;
        b.Enabled = klickbar;
        b.BackColor = klickbar ? farbe : Color.FromArgb(236, 236, 236);
        b.ForeColor = klickbar ? Color.White : Color.FromArgb(90, 90, 90);
        b.FlatAppearance.BorderColor = klickbar ? farbe : Color.FromArgb(200, 200, 200);
    }

    private static FlowLayoutPanel Reihe() =>
        new() { FlowDirection = FlowDirection.LeftToRight, WrapContents = false, AutoSize = true, Margin = new Padding(0) };

    private static Button Gross(string text, Color farbe)
    {
        var b = new Button
        {
            Text = text, Width = (Breite - 10) / 2, Height = 54, Margin = new Padding(0),
            Font = new Font("Segoe UI", 13f, FontStyle.Bold), FlatStyle = FlatStyle.Flat, BackColor = farbe,
            ForeColor = Color.White, Cursor = Cursors.Hand, UseVisualStyleBackColor = false,
        };
        b.FlatAppearance.BorderSize = 1;
        return b;
    }

    private static Button Knopf(string text, int breite = Breite) =>
        new()
        {
            Text = text, Width = breite, Height = 38, Margin = new Padding(0, 6, 0, 0), Cursor = Cursors.Hand,
            UseVisualStyleBackColor = true,
        };

    private static Label Info() =>
        new() { AutoSize = true, MaximumSize = new Size(Breite, 0), Margin = new Padding(0, 4, 0, 0) };
}
