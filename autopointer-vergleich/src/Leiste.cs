namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 03.10.2026: "die Programm-Buttons sollen nicht in den Hintergrund, wenn Tabs geöffnet
/// werden — kleiner machen und immer fest vorne lassen, am besten unten rechts oder unten links".
/// Schmale Leiste mit den wichtigsten Knöpfen: immer im Vordergrund (auch über dem Browser), fest in der
/// gewählten Ecke über der Taskleiste — auf dem Bildschirm, auf dem AutoPointer läuft. Sie nimmt AutoPointer
/// nie den Fokus weg (WS_EX_NOACTIVATE): ein Klick auf einen Knopf wirkt, AutoPointer bleibt vorne.
/// 1.5.8 (Wunsch Ahmad 08.10.2026, externe Pruefung "zu viele Knoepfe fuer dieselbe Sache"): die Leiste ist die EINE
/// Bedienung — Status (Klick = Start/Stopp) | Vergleichen | Vertrag | Mehr (Menue mit allem anderen). Das grosse Fenster
/// zeigt nur noch Status und Hilfe, das Symbol im Infobereich hat nur "Leiste und Status zeigen" und "Beenden".</summary>
internal sealed class Leiste : Form
{
    public const string Links = "links", Rechts = "rechts";

    private readonly Func<FensterZustand> _zustand;
    private readonly Func<IntPtr> _bezug;
    private readonly Label _status;
    private readonly Button _vergleichen, _vertrag, _mehr;
    private readonly ToolStripMenuItem _links, _rechts, _verbinden, _letzten;
    private readonly ContextMenuStrip _menue;
    private readonly ToolTip _tipps = new() { InitialDelay = 300, ShowAlways = true };   // Leiste ist nie aktiv
    private readonly System.Windows.Forms.Timer _takt;
    private string _ecke = Links;
    private FensterZustand? _zuletzt;
    private bool _endgueltig;
    private readonly Griff _griff;
    /// <summary>Frei verschobene Stelle (linke obere Ecke) oder null = feste Ecke.</summary>
    private Point? _frei;
    private Point? _ziehVersatz;

    public event Action? Aktivieren, Stoppen, JetztVergleichen, VertragOeffnen, FensterOeffnen, Beenden,
        LetztenOeffnen, EinstellungenOeffnen, SystemcheckOeffnen, Verbinden, Trennen, UpdateOeffnen;
    /// <summary>1.5.9 (B): "Update auf … verfügbar …" — erst da, wenn AutoSchnell eine neuere Version anbietet.</summary>
    private ToolStripMenuItem? _update;
    private ToolStripSeparator? _updateTrenner;
    private const string MehrTipp = "Mehr: letzten Vergleich, Einstellungen, Systemcheck, Verbindung, Beenden";
    public event Action<string>? EckeGewechselt;
    /// <summary>Leiste per Griff-Punkt verschoben (Stelle) bzw. zurueck in die Ecke (null).</summary>
    public event Action<Point?>? PositionGeaendert;

    /// <param name="bezug">AutoPointer-Hauptfenster (oder Zero): die Leiste sitzt auf dessen Bildschirm.</param>
    public Leiste(Func<FensterZustand> zustand, Func<IntPtr> bezug)
    {
        _zustand = zustand;
        _bezug = bezug;
        Text = "AutoSchnell Vergleich – Leiste";
        Font = new Font("Segoe UI", 9.5f);
        FormBorderStyle = FormBorderStyle.None;
        ShowInTaskbar = false;
        TopMost = true;
        StartPosition = FormStartPosition.Manual;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        AutoScaleMode = AutoScaleMode.Dpi;
        BackColor = Color.FromArgb(31, 41, 55);
        Padding = new Padding(4);

        var reihe = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.LeftToRight, WrapContents = false, AutoSize = true, Margin = new Padding(0),
            AutoSizeMode = AutoSizeMode.GrowAndShrink, BackColor = BackColor,
        };
        _status = new Label
        {
            AutoSize = false, Width = 172, Height = 34, TextAlign = ContentAlignment.MiddleCenter,
            ForeColor = Color.White, Font = new Font("Segoe UI", 9f, FontStyle.Bold), Margin = new Padding(0),
            Cursor = Cursors.Hand, BackColor = Symbole.Pause,
        };
        _vergleichen = Knopf("Vergleichen", 100);
        _vertrag = Knopf("Vertrag", 74);
        _mehr = Knopf("Mehr ▾", 70);
        // Wunsch Ahmad 04.10.2026: kleiner runder Punkt in der Ecke — gedrueckt halten und ziehen verschiebt die Leiste
        _griff = new Griff { Width = 16, Height = 34, Margin = new Padding(0, 0, 4, 0), Cursor = Cursors.SizeAll };
        _griff.MouseDown += (_, e) => { if (e.Button == MouseButtons.Left) GriffRunter(Cursor.Position); };
        _griff.MouseMove += (_, e) => { if (e.Button == MouseButtons.Left) GriffZiehen(Cursor.Position); };
        _griff.MouseUp += (_, _) => GriffLos();
        reihe.Controls.AddRange(new Control[] { _griff, _status, _vergleichen, _vertrag, _mehr });
        Controls.Add(reihe);

        _tipps.SetToolTip(_vergleichen, "Vergleich für das Auto, das AutoPointer gerade zeigt, jetzt öffnen");
        _tipps.SetToolTip(_vertrag, "Kaufvertrag: das zuletzt angeklickte Auto in AutoSchnell öffnen");
        _tipps.SetToolTip(_mehr, MehrTipp);
        _tipps.SetToolTip(_griff, "Gedrückt halten und ziehen: Leiste verschieben\n(Rechtsklick: zurück in eine Ecke)");

        _menue = new ContextMenuStrip();
        _letzten = new ToolStripMenuItem("Letzten Vergleich nochmal öffnen", null, (_, _) => LetztenOeffnen?.Invoke());
        _menue.Items.Add(_letzten);
        _menue.Items.Add("Status und Hilfe …", null, (_, _) => FensterOeffnen?.Invoke());
        _menue.Items.Add(new ToolStripSeparator());
        _menue.Items.Add("Einstellungen …", null, (_, _) => EinstellungenOeffnen?.Invoke());
        _menue.Items.Add("Systemcheck: läuft alles? …", null, (_, _) => SystemcheckOeffnen?.Invoke());
        _verbinden = new ToolStripMenuItem("Mit AutoSchnell verbinden …", null, (_, _) =>
        {
            if (_zuletzt?.Verbunden == true) Trennen?.Invoke(); else Verbinden?.Invoke();
        });
        _menue.Items.Add(_verbinden);
        _menue.Items.Add(new ToolStripSeparator());
        _links = new ToolStripMenuItem("Leiste unten links", null, (_, _) => EckeWaehlen(Links));
        _rechts = new ToolStripMenuItem("Leiste unten rechts", null, (_, _) => EckeWaehlen(Rechts));
        _menue.Items.Add(_links);
        _menue.Items.Add(_rechts);
        _menue.Items.Add(new ToolStripSeparator());
        _menue.Items.Add("Programm beenden", null, (_, _) => Beenden?.Invoke());
        ContextMenuStrip = _menue;
        foreach (Control c in reihe.Controls) c.ContextMenuStrip = _menue;
        reihe.ContextMenuStrip = _menue;

        // Status-Feld = Start/Stopp (nicht verbunden: verbinden)
        _status.Click += (_, _) => StatusKlick();
        _vergleichen.Click += (_, _) => JetztVergleichen?.Invoke();
        _vertrag.Click += (_, _) => VertragOeffnen?.Invoke();
        _mehr.Click += (_, _) => _menue.Show(_mehr, new Point(0, _mehr.Height));

        _takt = new System.Windows.Forms.Timer { Interval = 1000 };
        _takt.Tick += (_, _) => { Aktualisieren(); Platzieren(); ObenHalten(); };
        _takt.Start();
        EckeSetzen(Links);
        Aktualisieren();
    }

    // Immer oben, nie im Alt+Tab, nie aktiviert (AutoPointer behaelt den Fokus)
    protected override CreateParams CreateParams
    {
        get
        {
            var cp = base.CreateParams;
            cp.ExStyle |= Native.WS_EX_TOPMOST | Native.WS_EX_TOOLWINDOW | Native.WS_EX_NOACTIVATE;
            return cp;
        }
    }

    protected override bool ShowWithoutActivation => true;

    protected override void WndProc(ref Message m)
    {
        if (m.Msg == Native.WM_MOUSEACTIVATE)
        {
            m.Result = (IntPtr)Native.MA_NOACTIVATE;
            return;
        }
        base.WndProc(ref m);
    }

    protected override void OnLayout(LayoutEventArgs e)
    {
        base.OnLayout(e);
        if (IsHandleCreated) Platzieren();
    }

    protected override void OnFormClosing(FormClosingEventArgs e)
    {
        if (!_endgueltig && e.CloseReason == CloseReason.UserClosing)
        {
            e.Cancel = true;
            return;
        }
        _takt.Stop();
        base.OnFormClosing(e);
    }

    public void EndgueltigSchliessen()
    {
        _endgueltig = true;
        Close();
    }

    /// <summary>"links" oder "rechts" (jeweils unten, über der Taskleiste).</summary>
    public void EckeSetzen(string ecke)
    {
        _ecke = ecke == Rechts ? Rechts : Links;
        _links.Checked = _ecke == Links;
        _rechts.Checked = _ecke == Rechts;
        if (IsHandleCreated) Platzieren();
    }

    private void EckeWaehlen(string ecke)
    {
        _frei = null;                    // zurueck in die feste Ecke
        EckeSetzen(ecke);
        EckeGewechselt?.Invoke(_ecke);
        PositionGeaendert?.Invoke(null);
    }

    /// <summary>Gespeicherte freie Stelle uebernehmen (null = feste Ecke).</summary>
    public void PositionSetzen(Point? stelle)
    {
        _frei = stelle;
        if (IsHandleCreated) Platzieren();
    }

    // ---- Griff-Punkt: gedrueckt halten und ziehen (Bildschirmpunkte, damit Tests es nachstellen koennen) ----
    internal void GriffRunter(Point maus) => _ziehVersatz = new Point(maus.X - Left, maus.Y - Top);

    internal void GriffZiehen(Point maus)
    {
        if (_ziehVersatz is not { } v) return;
        Location = new Point(maus.X - v.X, maus.Y - v.Y);
    }

    internal void GriffLos()
    {
        if (_ziehVersatz == null) return;
        _ziehVersatz = null;
        _frei = Sichtbar(Location) ? Location : null;
        Platzieren();
        PositionGeaendert?.Invoke(_frei);
    }

    /// <summary>Liegt die Leiste an dieser Stelle wenigstens zum Teil auf einem Bildschirm? (Bildschirm
    /// abgesteckt, andere Aufloesung -> sonst waere sie unerreichbar.)</summary>
    private bool Sichtbar(Point stelle)
    {
        var flaeche = new Rectangle(stelle, Size);
        return Screen.AllScreens.Any(s => Rectangle.Intersect(s.WorkingArea, flaeche) is { Width: >= 40, Height: >= 20 });
    }

    /// <summary>Ziel-Ecke auf dem Bildschirm von AutoPointer (sonst dem Hauptbildschirm).</summary>
    internal Point Zielpunkt()
    {
        IntPtr bezug = IntPtr.Zero;
        try { bezug = _bezug(); } catch (Exception) { /* AutoPointer gerade weg */ }
        var schirm = bezug != IntPtr.Zero && Native.IsWindow(bezug) ? Screen.FromHandle(bezug)
                   : Screen.PrimaryScreen ?? Screen.AllScreens[0];
        var bereich = schirm.WorkingArea;            // ohne Taskleiste
        int abstand = LogicalToDeviceUnits(8);
        int y = bereich.Bottom - Height - abstand;
        return _ecke == Rechts ? new Point(bereich.Right - Width - abstand, y) : new Point(bereich.Left + abstand, y);
    }

    internal void Platzieren()
    {
        if (_ziehVersatz != null) return;                  // wird gerade gezogen
        if (_frei is { } frei)
        {
            if (Sichtbar(frei))
            {
                if (Location != frei) Location = frei;
                return;
            }
            _frei = null;                                  // Bildschirm weg: zurueck in die Ecke
            PositionGeaendert?.Invoke(null);
        }
        var ziel = Zielpunkt();
        if (Location != ziel) Location = ziel;
    }

    /// <summary>Andere "immer oben"-Fenster (Browser im Vollbild, Hinweise) können sich davorschieben —
    /// jede Sekunde wieder ganz nach oben, ohne zu aktivieren.</summary>
    private void ObenHalten()
    {
        if (IsHandleCreated && Visible)
            Native.SetWindowPos(Handle, Native.HWND_TOPMOST, 0, 0, 0, 0,
                                Native.SWP_NOMOVE | Native.SWP_NOSIZE | Native.SWP_NOACTIVATE);
    }

    /// <summary>Klick aufs Status-Feld: nicht verbunden -> verbinden, sonst Automatik an/aus.</summary>
    internal void StatusKlick()
    {
        if (_zuletzt?.Verbunden != true) Verbinden?.Invoke();
        else if (_zuletzt.AutomatikAn) Stoppen?.Invoke();
        else Aktivieren?.Invoke();
        Aktualisieren();
    }

    /// <summary>Die Eintraege des Mehr-Menues (fuer Tests).</summary>
    internal IReadOnlyList<string> MenueEintraege() =>
        _menue.Items.OfType<ToolStripMenuItem>().Select(i => i.Text ?? "").ToList();

    /// <summary>Pruefung 08.10.2026 (1.5.9, B): bietet AutoSchnell eine neuere Version an, steht dauerhaft ganz oben im
    /// Mehr-Menue "Update auf {version} verfügbar …" (vorher nur eine Sprechblase, die verschwand). null = keiner.</summary>
    public void UpdateSetzen(string? version)
    {
        if (string.IsNullOrWhiteSpace(version))
        {
            if (_update == null) return;
            _menue.Items.Remove(_update);
            if (_updateTrenner != null) _menue.Items.Remove(_updateTrenner);
            _update.Dispose();
            _updateTrenner?.Dispose();
            _update = null;
            _updateTrenner = null;
            _tipps.SetToolTip(_mehr, MehrTipp);
            return;
        }
        string text = $"Update auf {version.Trim()} verfügbar …";
        if (_update == null)
        {
            _update = new ToolStripMenuItem(text, null, (_, _) => UpdateOeffnen?.Invoke());
            _update.Font = new Font(_update.Font, FontStyle.Bold);
            _updateTrenner = new ToolStripSeparator();
            _menue.Items.Insert(0, _updateTrenner);
            _menue.Items.Insert(0, _update);
        }
        else _update.Text = text;
        _tipps.SetToolTip(_mehr, $"Mehr: Update auf {version.Trim()}, letzter Vergleich, Einstellungen, Systemcheck, Verbindung, Beenden");
    }

    public void Aktualisieren()
    {
        FensterZustand z;
        try { z = _zustand(); }
        catch (Exception ex) { Protokoll.Schreibe("Leiste: " + ex.Message); return; }
        _zuletzt = z;
        var (farbe, titel, unter) = SteuerFenster.Anzeige(z);
        _status.BackColor = farbe;
        _status.Text = StatusText(z, titel);
        string klick = !z.Verbunden ? "Klick: mit AutoSchnell verbinden"
                     : z.AutomatikAn ? "Klick: stoppen – es öffnet sich nichts mehr"
                     : "Klick: starten – Vergleiche öffnen sich beim Anklicken";
        _tipps.SetToolTip(_status, unter + (z.LetztesAuto != null ? "\nLetztes Auto: " + z.LetztesAuto : "")
                                   + (string.IsNullOrEmpty(z.LetzteMeldung) ? "" : "\n" + z.LetzteMeldung)
                                   + "\n(" + klick + ")");
        _vergleichen.Enabled = z.Verbunden;
        _vertrag.Enabled = z.Verbunden && z.LetztesAuto != null;
        _letzten.Enabled = z.Verbunden && z.LetztesAuto != null;
        _verbinden.Text = z.Verbunden ? "Verbindung trennen" : "Mit AutoSchnell verbinden …";
    }

    /// <summary>Status mit dem, was ein Klick tut: "●  AKTIV  ❚❚" (stoppen) bzw. "●  GESTOPPT  ▶" (starten).</summary>
    internal static string StatusText(FensterZustand z, string titel)
    {
        if (!z.Verbunden) return "●  " + Kurz(titel);
        return "●  " + Kurz(titel) + (z.AutomatikAn ? "  ❚❚" : "  ▶");
    }

    internal static string Kurz(string titel) => titel switch
    {
        "AKTIV – WARTET" => "WARTET",
        "TEXTERKENNUNG FEHLT" => "KEINE TEXTERK.",     // Paket 2 (A9): muss in die schmale Statusfläche passen
        _ => titel,
    };

    /// <summary>Kleiner runder Punkt (Griff zum Verschieben).</summary>
    private sealed class Griff : Control
    {
        public Griff()
        {
            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.UserPaint, true);
            BackColor = Color.FromArgb(31, 41, 55);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            e.Graphics.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            int d = Math.Min(Width, Height) - LogicalToDeviceUnits(6);
            var kreis = new Rectangle((Width - d) / 2, (Height - d) / 2, d, d);
            using var pinsel = new SolidBrush(Color.FromArgb(203, 213, 225));
            e.Graphics.FillEllipse(pinsel, kreis);
        }
    }

    private Button Knopf(string text, int breite)
    {
        var b = new Button
        {
            Text = text, Width = breite, Height = 34, Margin = new Padding(4, 0, 0, 0), FlatStyle = FlatStyle.Flat,
            BackColor = Color.FromArgb(55, 65, 81), ForeColor = Color.White, Cursor = Cursors.Hand,
            UseVisualStyleBackColor = false, TabStop = false,
        };
        b.FlatAppearance.BorderSize = 0;
        return b;
    }
}
