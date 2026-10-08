namespace AutoPointerVergleich;

/// <summary>Kleines Einstellungsfenster (ohne Designer gebaut). Die Suchregeln UND die Portalwahl (seit 1.5.8)
/// kommen aus AutoSchnell — hier nur Browser, Leiste und Ablauf.</summary>
internal sealed class EinstellungenForm : Form
{
    private readonly Einstellungen _e;

    private readonly CheckBox _automatik = Haken("Vergleich automatisch öffnen, sobald in AutoPointer ein Fahrzeug angezeigt wird");

    private readonly ComboBox _browser = Auswahl("Standardbrowser", "Microsoft Edge", "Google Chrome");
    private readonly CheckBox _zurueck = Haken("danach AutoPointer wieder nach vorne holen (bei zwei Bildschirmen)");

    private readonly NumericUpDown _wartezeit = Zahl(100, 5000, 50);
    private readonly NumericUpDown _abstand = Zahl(0, 60_000, 100);
    private readonly CheckBox _hinweise = Haken("Hinweise unten rechts anzeigen");
    private readonly CheckBox _hotkey = Haken("Strg+Alt+P schaltet die Automatik an/aus");
    private readonly CheckBox _autostart = Haken("mit Windows starten");
    private readonly CheckBox _bilder = Haken("Erkennungsbilder speichern (nur zur Fehlersuche)");
    private readonly ComboBox _ecke = Auswahl("unten links", "unten rechts");

    public Einstellungen Ergebnis => _e;

    public EinstellungenForm(Einstellungen aktuell)
    {
        _e = aktuell.Kopie();
        Text = TrayApp.Name + " – Einstellungen";
        Font = new Font("Segoe UI", 9f);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        AutoScaleMode = AutoScaleMode.Dpi;
        Icon = Symbole.Icon(Symbole.Aktiv);
        ShowInTaskbar = true;

        var spalte = new TableLayoutPanel { ColumnCount = 1, AutoSize = true, Padding = new Padding(8), Dock = DockStyle.Fill };
        // 1.5.9 (E): seit 1.5.8 verbindet man ueber die Leiste (das Symbol im Infobereich hat nur noch zwei Eintraege)
        var verbunden = Beschriftung(string.IsNullOrEmpty(_e.VerbundenAls)
            ? "Nicht verbunden – auf der Leiste „Mehr ▾“ → „Mit AutoSchnell verbinden …“."
            : $"Verbunden als {_e.VerbundenAls}");
        spalte.Controls.Add(Stapel(
            Gruppe("AutoSchnell", verbunden,
                   Beschriftung("Die Suchregeln (Baujahr, Kilometer, Leistung, Kraftstoff, Getriebe …) kommen aus\n" +
                                "AutoSchnell: Einstellungen → Vergleich. Änderungen dort gelten sofort auch hier.")),
            Gruppe("Automatische Vergleiche", _automatik,
                   Beschriftung("Welche Portale (mobile.de, AutoScout24 oder beide) aufgehen, stellst du in\n" +
                                "AutoSchnell auf der Vergleichsseite ein – das gilt hier automatisch mit.")),
            Gruppe("Leiste", Reihe(Beschriftung("Position:"), _ecke)),
            Gruppe("Browser", Reihe(Beschriftung("Öffnen mit:"), _browser), _zurueck),
            Gruppe("Ablauf",
                   Reihe(Beschriftung("Wartezeit nach dem Anklicken:"), _wartezeit, Beschriftung("ms")),
                   Reihe(Beschriftung("Mindestabstand zwischen Vergleichen:"), _abstand, Beschriftung("ms")),
                   _hinweise, _hotkey, _autostart, _bilder)), 0, 0);

        var speichern = new Button { Text = "Speichern", AutoSize = true, DialogResult = DialogResult.OK };
        var abbrechen = new Button { Text = "Abbrechen", AutoSize = true, DialogResult = DialogResult.Cancel };
        var knoepfe = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Dock = DockStyle.Fill };
        knoepfe.Controls.Add(abbrechen);
        knoepfe.Controls.Add(speichern);
        spalte.Controls.Add(knoepfe, 0, 1);
        Controls.Add(spalte);
        AcceptButton = speichern;
        CancelButton = abbrechen;

        Einlesen();
        speichern.Click += (_, _) => Uebernehmen();
    }

    private void Einlesen()
    {
        _automatik.Checked = _e.AutomatikAktiv;
        _browser.SelectedIndex = (int)_e.Browser;
        _zurueck.Checked = _e.ZurueckZuAutoPointer;
        _wartezeit.Value = _e.WartezeitMs;
        _abstand.Value = _e.MindestabstandMs;
        _hinweise.Checked = _e.HinweiseAnzeigen;
        _hotkey.Checked = _e.TastenkuerzelAktiv;
        _autostart.Checked = _e.MitWindowsStarten;
        _bilder.Checked = _e.ErkennungsbilderSpeichern;
        _ecke.SelectedIndex = _e.LeisteEcke == Leiste.Rechts ? 1 : 0;
    }

    private void Uebernehmen()
    {
        _e.AutomatikAktiv = _automatik.Checked;
        _e.Browser = (BrowserWahl)Math.Max(0, _browser.SelectedIndex);
        _e.ZurueckZuAutoPointer = _zurueck.Checked;
        _e.WartezeitMs = (int)_wartezeit.Value;
        _e.MindestabstandMs = (int)_abstand.Value;
        _e.HinweiseAnzeigen = _hinweise.Checked;
        _e.TastenkuerzelAktiv = _hotkey.Checked;
        _e.MitWindowsStarten = _autostart.Checked;
        _e.ErkennungsbilderSpeichern = _bilder.Checked;
        _e.LeisteEcke = _ecke.SelectedIndex == 1 ? Leiste.Rechts : Leiste.Links;
        _e.Bereinigt();
    }

    /// <summary>Pruefung 05.10.2026 (Paket 2, A11): nur die Felder dieses Dialogs in die laufenden Einstellungen
    /// schreiben. Vorher ersetzte "Speichern" das ganze Objekt durch die Kopie vom Oeffnen — hatte sich die
    /// Verbindung inzwischen geaendert (Lizenzpruefung, 401, neu verbunden), ueberschrieb der Dialog
    /// Schluessel/VerbundenAls/Server mit dem alten Stand.</summary>
    public void AnwendenAuf(Einstellungen ziel)
    {
        ziel.AutomatikAktiv = _e.AutomatikAktiv;
        ziel.Browser = _e.Browser;
        ziel.ZurueckZuAutoPointer = _e.ZurueckZuAutoPointer;
        ziel.WartezeitMs = _e.WartezeitMs;
        ziel.MindestabstandMs = _e.MindestabstandMs;
        ziel.HinweiseAnzeigen = _e.HinweiseAnzeigen;
        ziel.TastenkuerzelAktiv = _e.TastenkuerzelAktiv;
        // 1.5.9 (F): selbst umgestellt -> das Programm schaltet den Autostart nie mehr von sich aus ein
        if (ziel.MitWindowsStarten != _e.MitWindowsStarten) ziel.AutostartSelbstGewaehlt = true;
        ziel.MitWindowsStarten = _e.MitWindowsStarten;
        ziel.ErkennungsbilderSpeichern = _e.ErkennungsbilderSpeichern;
        ziel.LeisteEcke = _e.LeisteEcke;
    }

    // ---- kleine Bauhelfer ----------------------------------------------------

    private static CheckBox Haken(string text) => new() { Text = text, AutoSize = true, Margin = new Padding(3, 3, 3, 1) };
    internal static Label Beschriftung(string text) => new() { Text = text, AutoSize = true, Anchor = AnchorStyles.Left, Margin = new Padding(3, 6, 3, 3) };

    private static NumericUpDown Zahl(int min, int max, int schritt) => new()
    {
        Minimum = min, Maximum = max, Increment = schritt, Width = 80, ThousandsSeparator = true,
    };

    private static ComboBox Auswahl(params string[] eintraege)
    {
        var c = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 200 };
        c.Items.AddRange(eintraege);
        return c;
    }

    private static FlowLayoutPanel Reihe(params Control[] c)
    {
        var p = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = new Padding(0) };
        p.Controls.AddRange(c);
        return p;
    }

    private static FlowLayoutPanel Stapel(params Control[] c)
    {
        var p = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false, Margin = new Padding(0) };
        p.Controls.AddRange(c);
        return p;
    }

    private static GroupBox Gruppe(string titel, params Control[] inhalt)
    {
        var g = new GroupBox { Text = titel, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, Padding = new Padding(8, 4, 8, 6), Margin = new Padding(4) };
        var stapel = Stapel(inhalt);
        stapel.Dock = DockStyle.Fill;
        g.Controls.Add(stapel);
        return g;
    }
}
