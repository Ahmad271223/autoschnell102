namespace AutoPointerVergleich;

/// <summary>Dauerlauf starten (Wunsch Ahmad 27.11.2026): vor dem Start sagt der Sucher, welche Autos er haben will —
/// und sieht klar, dass das Programm ab jetzt die Maus fuehrt. Der Dauerlauf ist nie von selbst an: er wird hier
/// bewusst gestartet und haelt an, sobald jemand die Maus bewegt.</summary>
internal sealed class DauerlaufForm : Form
{
    private readonly CheckBox _privat = new() { Text = "Nur Privatverkäufer", AutoSize = true };
    private readonly CheckBox _unfall = new() { Text = "Keine Unfallwagen (auch nicht „an Bastler“, „Motorschaden“ im Titel)", AutoSize = true };
    private readonly NumericUpDown _preisVon = Zahl(0, 500_000, 500), _preisBis = Zahl(0, 500_000, 500);
    private readonly NumericUpDown _km = Zahl(0, 1_000_000, 10_000), _jahr = Zahl(0, 2100, 1);
    private readonly CheckBox _kleinanzeigen = new() { Text = "Kleinanzeigen", AutoSize = true };
    private readonly CheckBox _mobile = new() { Text = "mobile.de", AutoSize = true };
    private readonly CheckBox _autoscout = new() { Text = "AutoScout24", AutoSize = true };

    /// <summary>Portalnamen, wie sie in der Liste des Programms stehen.</summary>
    internal static readonly string[] Portale = { "Kleinanzeigen", "Mobile.de", "AutoScout24" };

    public DauerlaufRegeln Regeln { get; private set; }

    private static NumericUpDown Zahl(int min, int max, int schritt) =>
        new() { Minimum = min, Maximum = max, Increment = schritt, ThousandsSeparator = true, Width = 110 };

    public DauerlaufForm(DauerlaufRegeln regeln)
    {
        Regeln = regeln;
        Text = "Dauerlauf starten";
        Font = new Font("Segoe UI", 9.5f);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        ShowInTaskbar = false;
        TopMost = true;
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        Padding = new Padding(14);

        // Live-Test 27.11.2026: ohne festen Platz klebte der Text am linken Fensterrand (Padding rueckt ein
        // nicht angedocktes Feld nicht ein)
        var spalte = new FlowLayoutPanel { Location = new Point(14, 14), FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink };
        spalte.Controls.Add(new Label
        {
            AutoSize = true, MaximumSize = new Size(470, 0), Margin = new Padding(0, 0, 0, 10),
            Text = "Das Programm liest alle neuen Inserate mit, sortiert Unpassendes aus und klickt die passenden Autos selbst an. "
                   + "Was sich lohnt, steht danach in AutoSchnell unter „Treffer“.",
        });
        spalte.Controls.Add(new Label
        {
            AutoSize = true, MaximumSize = new Size(470, 0), Margin = new Padding(0, 0, 0, 12), ForeColor = Color.FromArgb(160, 80, 0),
            Text = "Achtung: Das Programm führt dabei die Maus. Dieser PC sollte so lange nicht benutzt werden. "
                   + "Sobald jemand die Maus bewegt, hält der Dauerlauf an.",
        });
        spalte.Controls.Add(_privat);
        spalte.Controls.Add(_unfall);
        spalte.Controls.Add(Zeile("Preis von", _preisVon, "bis", _preisBis, "€  (0 = egal)"));
        spalte.Controls.Add(Zeile("Höchstens", _km, "km  (0 = egal)"));
        spalte.Controls.Add(Zeile("Erstzulassung ab", _jahr, "(0 = egal)"));
        spalte.Controls.Add(Zeile("Portale:", _kleinanzeigen, _mobile, _autoscout));

        var start = new Button { Text = "Dauerlauf starten", AutoSize = true, DialogResult = DialogResult.OK, Padding = new Padding(8, 3, 8, 3) };
        var abbrechen = new Button { Text = "Abbrechen", AutoSize = true, DialogResult = DialogResult.Cancel, Padding = new Padding(8, 3, 8, 3) };
        var knoepfe = new FlowLayoutPanel { FlowDirection = FlowDirection.RightToLeft, AutoSize = true, Dock = DockStyle.Fill, Margin = new Padding(0, 14, 0, 0) };
        knoepfe.Controls.Add(abbrechen);
        knoepfe.Controls.Add(start);
        spalte.Controls.Add(knoepfe);
        Controls.Add(spalte);
        AcceptButton = start;
        CancelButton = abbrechen;

        _privat.Checked = regeln.NurPrivat;
        _unfall.Checked = regeln.OhneUnfall;
        _preisVon.Value = Math.Clamp(regeln.PreisVon, 0, 500_000);
        _preisBis.Value = Math.Clamp(regeln.PreisBis, 0, 500_000);
        _km.Value = Math.Clamp(regeln.KmBis, 0, 1_000_000);
        _jahr.Value = Math.Clamp(regeln.JahrAb, 0, 2100);
        bool alle = regeln.Portale.Count == 0;
        _kleinanzeigen.Checked = alle || regeln.Portale.Contains(Portale[0], StringComparer.OrdinalIgnoreCase);
        _mobile.Checked = alle || regeln.Portale.Contains(Portale[1], StringComparer.OrdinalIgnoreCase);
        _autoscout.Checked = alle || regeln.Portale.Contains(Portale[2], StringComparer.OrdinalIgnoreCase);

        FormClosing += (_, e) =>
        {
            if (DialogResult != DialogResult.OK) return;
            Regeln = Lesen();
        };
    }

    private static FlowLayoutPanel Zeile(params object[] teile)
    {
        var z = new FlowLayoutPanel { FlowDirection = FlowDirection.LeftToRight, WrapContents = false, AutoSize = true, Margin = new Padding(0, 6, 0, 0) };
        foreach (object t in teile)
            z.Controls.Add(t as Control ?? new Label { Text = (string)t, AutoSize = true, Margin = new Padding(0, 6, 6, 0) });
        return z;
    }

    private DauerlaufRegeln Lesen() =>
        AusWerten(_privat.Checked, _unfall.Checked, (int)_preisVon.Value, (int)_preisBis.Value, (int)_km.Value, (int)_jahr.Value,
                  new[] { _kleinanzeigen.Checked, _mobile.Checked, _autoscout.Checked });

    /// <summary>Eingaben → Regeln. Alle Portale angehakt (oder keines) = keine Einschraenkung. (rein, fuer Tests)</summary>
    internal static DauerlaufRegeln AusWerten(bool privat, bool unfall, int preisVon, int preisBis, int km, int jahr, bool[] portale)
    {
        var r = new DauerlaufRegeln { NurPrivat = privat, OhneUnfall = unfall, PreisVon = preisVon, PreisBis = preisBis, KmBis = km, JahrAb = jahr };
        if (preisBis > 0 && preisVon > preisBis) (r.PreisVon, r.PreisBis) = (preisBis, preisVon);
        var gewaehlt = Portale.Where((_, i) => i < portale.Length && portale[i]).ToList();
        if (gewaehlt.Count > 0 && gewaehlt.Count < Portale.Length) r.Portale = gewaehlt;
        return r;
    }
}
