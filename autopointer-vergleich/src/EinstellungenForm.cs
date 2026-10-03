namespace AutoPointerVergleich;

/// <summary>Kleines Einstellungsfenster (ohne Designer gebaut).</summary>
internal sealed class EinstellungenForm : Form
{
    private readonly Einstellungen _e;

    private readonly CheckBox _automatik = Haken("Vergleich automatisch öffnen, sobald in AutoPointer ein Fahrzeug angezeigt wird");
    private readonly CheckBox _mobile = Haken("mobile.de");
    private readonly CheckBox _autoscout = Haken("AutoScout24");

    private readonly RadioButton _kmBereich = Wahl("Bereich: Kilometerstand ± Spanne");
    private readonly RadioButton _kmBis = Wahl("bis Kilometerstand + Spanne");
    private readonly RadioButton _kmAus = Wahl("kein Kilometer-Filter");
    private readonly NumericUpDown _kmSpanne = Zahl(0, 500_000, 1000);
    private readonly CheckBox _kmRunden = Haken("Grenzen auf volle 5.000 km runden");

    private readonly RadioButton _ezExakt = Wahl("nur das Jahr der Erstzulassung (03/2017 → 2017)");
    private readonly RadioButton _ezPlusMinus = Wahl("Jahr ± Jahre (2017 → 2016–2018)");
    private readonly RadioButton _ezAb = Wahl("ab Jahr − Jahre und neuer");
    private readonly RadioButton _ezAus = Wahl("kein Erstzulassungs-Filter");
    private readonly NumericUpDown _ezJahre = Zahl(0, 20, 1);

    private readonly CheckBox _leistung = Haken("gleiche Leistung ±");
    private readonly NumericUpDown _ps = Zahl(0, 200, 1);
    private readonly CheckBox _kraftstoff = Haken("gleicher Kraftstoff");
    private readonly CheckBox _getriebe = Haken("gleiches Getriebe");
    private readonly CheckBox _unfall = Haken("Unfallfahrzeuge ausblenden");
    private readonly CheckBox _deutschland = Haken("nur Deutschland");
    private readonly CheckBox _ohneModell = Haken("Modell unbekannt: trotzdem nur nach Marke suchen");
    private readonly ComboBox _sortierung = Auswahl("Preis aufsteigend", "Kilometer aufsteigend", "Erstzulassung neueste zuerst", "Relevanz");

    private readonly ComboBox _browser = Auswahl("Standardbrowser", "Microsoft Edge", "Google Chrome");
    private readonly CheckBox _zurueck = Haken("danach AutoPointer wieder nach vorne holen (bei zwei Bildschirmen)");

    private readonly NumericUpDown _wartezeit = Zahl(100, 5000, 50);
    private readonly NumericUpDown _abstand = Zahl(0, 60_000, 100);
    private readonly CheckBox _hinweise = Haken("Hinweise unten rechts anzeigen");
    private readonly CheckBox _hotkey = Haken("Strg+Alt+P schaltet die Automatik an/aus");
    private readonly CheckBox _autostart = Haken("mit Windows starten");
    private readonly CheckBox _bilder = Haken("Erkennungsbilder speichern (nur zur Fehlersuche)");

    public Einstellungen Ergebnis => _e;

    public EinstellungenForm(Einstellungen aktuell)
    {
        _e = aktuell.Kopie();
        Text = "AutoPointer-Vergleich – Einstellungen";
        Font = new Font("Segoe UI", 9f);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        AutoScaleMode = AutoScaleMode.Dpi;
        Icon = Symbole.Icon(Symbole.Aktiv);
        ShowInTaskbar = true;

        var spalten = new TableLayoutPanel { ColumnCount = 2, AutoSize = true, Padding = new Padding(8), Dock = DockStyle.Fill };
        spalten.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        spalten.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));

        var links = Stapel(
            Gruppe("Automatische Vergleiche", _automatik, Reihe(_mobile, _autoscout)),
            Gruppe("Kilometer", _kmBereich, _kmBis, _kmAus, Reihe(Beschriftung("Spanne:"), _kmSpanne, Beschriftung("km")), _kmRunden),
            Gruppe("Erstzulassung", _ezExakt, _ezPlusMinus, _ezAb, _ezAus, Reihe(Beschriftung("Jahre:"), _ezJahre)));
        var rechts = Stapel(
            Gruppe("Motor und Filter", Reihe(_leistung, _ps, Beschriftung("PS (gleiche Motorisierung)")), _kraftstoff, _getriebe,
                   _unfall, _deutschland, _ohneModell, Reihe(Beschriftung("Sortierung:"), _sortierung)),
            Gruppe("Browser", Reihe(Beschriftung("Öffnen mit:"), _browser), _zurueck),
            Gruppe("Ablauf",
                   Reihe(Beschriftung("Wartezeit nach dem Anklicken:"), _wartezeit, Beschriftung("ms")),
                   Reihe(Beschriftung("Mindestabstand zwischen Vergleichen:"), _abstand, Beschriftung("ms")),
                   _hinweise, _hotkey, _autostart, _bilder));
        spalten.Controls.Add(links, 0, 0);
        spalten.Controls.Add(rechts, 1, 0);

        var speichern = new Button { Text = "Speichern", AutoSize = true, DialogResult = DialogResult.OK };
        var abbrechen = new Button { Text = "Abbrechen", AutoSize = true, DialogResult = DialogResult.Cancel };
        var knoepfe = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Dock = DockStyle.Fill };
        knoepfe.Controls.Add(abbrechen);
        knoepfe.Controls.Add(speichern);
        spalten.Controls.Add(knoepfe, 0, 1);
        spalten.SetColumnSpan(knoepfe, 2);
        Controls.Add(spalten);
        AcceptButton = speichern;
        CancelButton = abbrechen;

        Einlesen();
        speichern.Click += (_, _) => Uebernehmen();
    }

    private void Einlesen()
    {
        _automatik.Checked = _e.AutomatikAktiv;
        _mobile.Checked = _e.MobileDe;
        _autoscout.Checked = _e.AutoScout24;
        (_e.KmModus switch { KmModus.BisPlus => _kmBis, KmModus.Aus => _kmAus, _ => _kmBereich }).Checked = true;
        _kmSpanne.Value = _e.KmSpanne;
        _kmRunden.Checked = _e.KmRunden;
        (_e.EzModus switch { EzModus.PlusMinus => _ezPlusMinus, EzModus.AbJahr => _ezAb, EzModus.Aus => _ezAus, _ => _ezExakt }).Checked = true;
        _ezJahre.Value = _e.EzJahre;
        _leistung.Checked = _e.LeistungFiltern;
        _ps.Value = _e.LeistungTolerantPs;
        _kraftstoff.Checked = _e.KraftstoffFiltern;
        _getriebe.Checked = _e.GetriebeFiltern;
        _unfall.Checked = _e.UnfallwagenAusblenden;
        _deutschland.Checked = _e.NurDeutschland;
        _ohneModell.Checked = _e.OhneModellNurMarke;
        _sortierung.SelectedIndex = (int)_e.Sortierung;
        _browser.SelectedIndex = (int)_e.Browser;
        _zurueck.Checked = _e.ZurueckZuAutoPointer;
        _wartezeit.Value = _e.WartezeitMs;
        _abstand.Value = _e.MindestabstandMs;
        _hinweise.Checked = _e.HinweiseAnzeigen;
        _hotkey.Checked = _e.TastenkuerzelAktiv;
        _autostart.Checked = _e.MitWindowsStarten;
        _bilder.Checked = _e.ErkennungsbilderSpeichern;
    }

    private void Uebernehmen()
    {
        _e.AutomatikAktiv = _automatik.Checked;
        _e.MobileDe = _mobile.Checked;
        _e.AutoScout24 = _autoscout.Checked;
        _e.KmModus = _kmBis.Checked ? KmModus.BisPlus : _kmAus.Checked ? KmModus.Aus : KmModus.Bereich;
        _e.KmSpanne = (int)_kmSpanne.Value;
        _e.KmRunden = _kmRunden.Checked;
        _e.EzModus = _ezPlusMinus.Checked ? EzModus.PlusMinus : _ezAb.Checked ? EzModus.AbJahr : _ezAus.Checked ? EzModus.Aus : EzModus.ExaktesJahr;
        _e.EzJahre = (int)_ezJahre.Value;
        _e.LeistungFiltern = _leistung.Checked;
        _e.LeistungTolerantPs = (int)_ps.Value;
        _e.KraftstoffFiltern = _kraftstoff.Checked;
        _e.GetriebeFiltern = _getriebe.Checked;
        _e.UnfallwagenAusblenden = _unfall.Checked;
        _e.NurDeutschland = _deutschland.Checked;
        _e.OhneModellNurMarke = _ohneModell.Checked;
        _e.Sortierung = (Sortierung)Math.Max(0, _sortierung.SelectedIndex);
        _e.Browser = (BrowserWahl)Math.Max(0, _browser.SelectedIndex);
        _e.ZurueckZuAutoPointer = _zurueck.Checked;
        _e.WartezeitMs = (int)_wartezeit.Value;
        _e.MindestabstandMs = (int)_abstand.Value;
        _e.HinweiseAnzeigen = _hinweise.Checked;
        _e.TastenkuerzelAktiv = _hotkey.Checked;
        _e.MitWindowsStarten = _autostart.Checked;
        _e.ErkennungsbilderSpeichern = _bilder.Checked;
        _e.Bereinigt();
        if (!_e.MobileDe && !_e.AutoScout24)
            MessageBox.Show(this, "Kein Portal ausgewählt – es werden keine Vergleiche geöffnet.", Text,
                MessageBoxButtons.OK, MessageBoxIcon.Information);
    }

    // ---- kleine Bauhelfer ----------------------------------------------------

    private static CheckBox Haken(string text) => new() { Text = text, AutoSize = true, Margin = new Padding(3, 3, 3, 1) };
    private static RadioButton Wahl(string text) => new() { Text = text, AutoSize = true, Margin = new Padding(3, 2, 3, 0) };
    private static Label Beschriftung(string text) => new() { Text = text, AutoSize = true, Anchor = AnchorStyles.Left, Margin = new Padding(3, 6, 3, 3) };

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
