namespace AutoPointerVergleich;

/// <summary>Zeigt das Protokoll — fuer die Fehlersuche. Die Dateien sind seit 03.10.2026 verschluesselt
/// (<see cref="Tresor"/>); lesbar sind sie nur hier (bzw. mit --protokoll) unter demselben Windows-Konto.
/// "Kopieren" legt den angezeigten Tag in die Zwischenablage, z. B. um ihn dem Support zu schicken.</summary>
internal sealed class ProtokollForm : Form
{
    private readonly TextBox _text;
    private readonly ComboBox _tag;
    private DateTime _angezeigt = DateTime.Today;

    public ProtokollForm()
    {
        Text = "AutoPointer-Vergleich – Protokoll";
        Icon = Symbole.Icon(Symbole.Aktiv);
        Size = new Size(980, 560);
        StartPosition = FormStartPosition.CenterScreen;
        AutoScaleMode = AutoScaleMode.Dpi;
        _text = new TextBox
        {
            Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Both, WordWrap = false,
            Dock = DockStyle.Fill, Font = new Font("Consolas", 9.5f), BackColor = SystemColors.Window,
        };
        _tag = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 140 };
        var tage = Protokoll.Tage();
        if (!tage.Contains(DateTime.Today)) tage.Insert(0, DateTime.Today);
        foreach (var t in tage) _tag.Items.Add(t.ToString("dd.MM.yyyy"));
        _tag.SelectedIndex = 0;
        _tag.SelectedIndexChanged += (_, _) =>
        {
            if (DateTime.TryParseExact(_tag.SelectedItem as string, "dd.MM.yyyy", null,
                                       System.Globalization.DateTimeStyles.None, out var t)) Zeigen(t);
        };
        var kopieren = new Button { Text = "Kopieren", AutoSize = true };
        kopieren.Click += (_, _) =>
        {
            if (_text.TextLength > 0) Clipboard.SetText(_text.Text);
        };
        var leiste = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, Padding = new Padding(4) };
        leiste.Controls.Add(new Label { Text = "Tag:", AutoSize = true, Margin = new Padding(3, 8, 3, 3) });
        leiste.Controls.Add(_tag);
        leiste.Controls.Add(kopieren);
        Controls.Add(_text);
        Controls.Add(leiste);

        Zeigen(DateTime.Today);
        Protokoll.NeueZeile += Neu;
        FormClosed += (_, _) => Protokoll.NeueZeile -= Neu;
        Shown += (_, _) => AnsEnde();
    }

    private void Zeigen(DateTime tag)
    {
        _angezeigt = tag.Date;
        var eintraege = Protokoll.Tag(tag);
        // Laeuft das Protokoll nicht in eine Datei (Konsolenmodus), wenigstens diese Sitzung zeigen
        if (eintraege.Count == 0 && tag.Date == DateTime.Today) eintraege = Protokoll.Letzte();
        _text.Text = string.Join(Environment.NewLine, eintraege).Replace("\n", Environment.NewLine) + Environment.NewLine;
        AnsEnde();
    }

    private void Neu(string zeile)
    {
        if (IsDisposed || _angezeigt != DateTime.Today) return;
        BeginInvoke(() =>
        {
            _text.AppendText(zeile.Replace("\n", Environment.NewLine) + Environment.NewLine);
        });
    }

    private void AnsEnde()
    {
        _text.SelectionStart = _text.TextLength;
        _text.ScrollToCaret();
    }
}
