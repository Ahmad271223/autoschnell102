using System.Diagnostics;

namespace AutoPointerVergleich;

/// <summary>Zeigt das Protokoll live mit - fuer die Entwicklung und Fehlersuche.</summary>
internal sealed class ProtokollForm : Form
{
    private readonly TextBox _text;

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
        var ordner = new Button { Text = "Protokollordner öffnen", AutoSize = true };
        ordner.Click += (_, _) =>
        {
            Directory.CreateDirectory(Protokoll.Ordner);
            Process.Start(new ProcessStartInfo(Protokoll.Ordner) { UseShellExecute = true })?.Dispose();
        };
        var leeren = new Button { Text = "Anzeige leeren", AutoSize = true };
        leeren.Click += (_, _) => _text.Clear();
        var leiste = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, Padding = new Padding(4) };
        leiste.Controls.Add(ordner);
        leiste.Controls.Add(leeren);
        Controls.Add(_text);
        Controls.Add(leiste);

        _text.Text = string.Join(Environment.NewLine, Protokoll.Letzte()).Replace("\n", Environment.NewLine) + Environment.NewLine;
        Protokoll.NeueZeile += Neu;
        FormClosed += (_, _) => Protokoll.NeueZeile -= Neu;
        Shown += (_, _) => AnsEnde();
    }

    private void Neu(string zeile)
    {
        if (IsDisposed) return;
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
