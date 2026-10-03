namespace AutoPointerVergleich;

/// <summary>"Mit AutoSchnell verbinden": 6-stelliger Code aus der App (AutoPointer-Vergleich →
/// Programm verbinden). Ein Konto = ein PC — eine neue Verbindung ersetzt die alte.</summary>
internal sealed class VerbindenForm : Form
{
    private readonly AutoSchnellDienst _dienst;
    private readonly TextBox _code;
    private readonly Button _verbinden;
    private readonly Label _status;

    public VerbindenAntwort? Ergebnis { get; private set; }

    public VerbindenForm(AutoSchnellDienst dienst, string? hinweis)
    {
        _dienst = dienst;
        Text = "AutoPointer-Vergleich – mit AutoSchnell verbinden";
        Font = new Font("Segoe UI", 9.5f);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        AutoSize = true;
        AutoSizeMode = AutoSizeMode.GrowAndShrink;
        AutoScaleMode = AutoScaleMode.Dpi;
        Icon = Symbole.Icon(Symbole.Aktiv);
        ShowInTaskbar = true;
        TopMost = true;

        var stapel = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true, Padding = new Padding(16),
        };
        if (!string.IsNullOrEmpty(hinweis))
            stapel.Controls.Add(new Label { Text = hinweis, AutoSize = true, ForeColor = Color.Firebrick, MaximumSize = new Size(460, 0), Margin = new Padding(0, 0, 0, 10) });
        stapel.Controls.Add(new Label
        {
            Text = "Das Programm funktioniert nur mit einem AutoSchnell-Konto mit aktivem Abo.\n\n" +
                   "1. In AutoSchnell anmelden → „AutoPointer-Vergleich“ → „Code zum Verbinden anzeigen“.\n" +
                   "2. Den 6-stelligen Code hier eintippen (10 Minuten gültig).\n\n" +
                   "Jedes Konto kann auf einem PC verbunden sein – ein neuer PC ersetzt den alten.",
            AutoSize = true, MaximumSize = new Size(460, 0), Margin = new Padding(0, 0, 0, 12),
        });
        _code = new TextBox
        {
            Font = new Font("Consolas", 22f, FontStyle.Bold), Width = 220, MaxLength = 7,
            TextAlign = HorizontalAlignment.Center, PlaceholderText = "123456",
        };
        _verbinden = new Button { Text = "Verbinden", AutoSize = true, Padding = new Padding(10, 4, 10, 4), Margin = new Padding(12, 6, 0, 0) };
        var reihe = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = new Padding(0) };
        reihe.Controls.Add(_code);
        reihe.Controls.Add(_verbinden);
        stapel.Controls.Add(reihe);
        _status = new Label { AutoSize = true, MaximumSize = new Size(460, 0), Margin = new Padding(0, 10, 0, 0) };
        stapel.Controls.Add(_status);
        stapel.Controls.Add(new Label
        {
            Text = $"Server: {dienst.Server}   ·   PC: {Environment.MachineName}", AutoSize = true,
            ForeColor = SystemColors.GrayText, Margin = new Padding(0, 10, 0, 0),
        });
        Controls.Add(stapel);
        AcceptButton = _verbinden;
        _verbinden.Click += async (_, _) => await VerbindenAsync();
        Shown += (_, _) => { Activate(); _code.Focus(); };
    }

    private async Task VerbindenAsync()
    {
        string code = new(_code.Text.Where(char.IsDigit).ToArray());
        if (code.Length != 6)
        {
            Zeige("Bitte den 6-stelligen Code aus AutoSchnell eintippen.", true);
            return;
        }
        _verbinden.Enabled = false;
        _code.Enabled = false;
        Zeige("Verbinde …", false);
        try
        {
            var (name, kennung) = AutoSchnellDienst.PcAngaben();
            Ergebnis = await _dienst.VerbindenAsync(code, name, kennung);
            DialogResult = DialogResult.OK;
            Close();
        }
        catch (DienstFehler ex)
        {
            Zeige(ex.Message, true);
            _verbinden.Enabled = true;
            _code.Enabled = true;
            _code.SelectAll();
            _code.Focus();
        }
    }

    private void Zeige(string text, bool fehler)
    {
        _status.Text = text;
        _status.ForeColor = fehler ? Color.Firebrick : SystemColors.ControlText;
    }
}
