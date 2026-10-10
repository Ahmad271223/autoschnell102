using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Pruefung 05.10.2026 (Paket 2, A6/A7): fester Installationsordner — die Pfad-Logik ohne echte Registrierung
/// und ohne Kopieren; Neustart nach Absturz (Befehlszeile).</summary>
public class InstallationTests
{
    private const string Fest = @"C:\Users\x\AppData\Local\Programs\AutoSchnell-Vergleich\AutoSchnell-Vergleich.exe";
    private const string Temp = @"C:\Users\x\AppData\Local\Temp\";
    private const string Profil = @"C:\Users\x";

    [Theory]
    [InlineData(@"C:\Users\x\AppData\Local\Temp\Temp1_AutoSchnell-Vergleich.zip\AutoSchnell-Vergleich.exe", true)]   // ZIP-Vorschau
    [InlineData(@"C:\Users\x\AppData\Local\Temp\AutoSchnell-Vergleich.exe", true)]
    [InlineData(@"D:\Ablage\Temp1_AutoSchnell.zip\AutoSchnell-Vergleich.exe", true)]            // ZIP-Vorschau bei umgebogenem %TEMP%
    [InlineData(@"C:\Users\x\Downloads\AutoSchnell-Vergleich (1).exe", true)]
    [InlineData(@"C:\Users\x\downloads\neu\AutoSchnell-Vergleich.exe", true)]
    [InlineData(@"C:\Users\x\Desktop\AutoSchnell-Vergleich.exe", false)]
    [InlineData(@"D:\Programme\AutoSchnell-Vergleich.exe", false)]
    [InlineData(Fest, false)]
    [InlineData("", true)]
    [InlineData(null, true)]
    public void Temp_und_Downloads_sind_fluechtig(string? pfad, bool erwartet) =>
        Assert.Equal(erwartet, Installation.IstFluechtig(pfad, Temp, Profil));

    [Fact]
    public void Fester_Pfad_wird_unabhaengig_von_der_Schreibweise_erkannt()
    {
        Assert.True(Installation.IstFesterPfad(Fest.ToUpperInvariant(), Fest));
        Assert.True(Installation.IstFesterPfad(@"C:\Users\x\AppData\Local\Programs\.\AutoSchnell-Vergleich\AutoSchnell-Vergleich.exe", Fest));
        Assert.False(Installation.IstFesterPfad(@"C:\Users\x\Downloads\AutoSchnell-Vergleich.exe", Fest));
        Assert.False(Installation.IstFesterPfad(null, Fest));
        Assert.EndsWith(@"\Programs\AutoSchnell-Vergleich\AutoSchnell-Vergleich.exe", Installation.FesterPfad);
    }

    [Theory]   // die Kopie am festen Platz wird ueberschrieben, wenn sie aelter oder gleich ist — eine NEUERE bleibt
    [InlineData("1.5.4", "1.5.5", true)]
    [InlineData("1.5.5", "1.5.5", true)]
    [InlineData("1.5.5+abc123", "1.5.5", true)]
    [InlineData("1.6.0", "1.5.5", false)]
    [InlineData("2.0.0", "1.5.5", false)]
    [InlineData(null, "1.5.5", true)]
    [InlineData("kaputt", "1.5.5", true)]
    public void Welche_Version_gewinnt(string? dort, string laufend, bool ueberschreiben) =>
        Assert.Equal(ueberschreiben, Installation.Ueberschreiben(dort, laufend));

    [Fact]   // Autostart zeigt auf die feste Kopie, sobald es sie gibt — sonst auf die laufende Datei, nie auf Temp/Downloads
    public void Autostart_Ziel_nie_Temp_oder_Downloads()
    {
        string downloads = @"C:\Users\x\Downloads\AutoSchnell-Vergleich.exe";
        Assert.Equal(Fest, Installation.AutostartZiel(downloads, kopieVorhanden: true, Fest, Temp, Profil));
        Assert.Equal(Fest, Installation.AutostartZiel(Fest, kopieVorhanden: true, Fest, Temp, Profil));
        Assert.Null(Installation.AutostartZiel(downloads, kopieVorhanden: false, Fest, Temp, Profil));
        Assert.Null(Installation.AutostartZiel(Temp + @"Temp1_a.zip\AutoSchnell-Vergleich.exe", kopieVorhanden: false, Fest, Temp, Profil));
        Assert.Null(Installation.AutostartZiel(null, kopieVorhanden: false, Fest, Temp, Profil));
        string desktop = @"C:\Users\x\Desktop\AutoSchnell-Vergleich.exe";
        Assert.Equal(desktop, Installation.AutostartZiel(desktop, kopieVorhanden: false, Fest, Temp, Profil));
    }

    [Theory]   // Pruefung 08.10.2026 (1.5.9, B): ersetzen nur, wenn die laufende Version sicher aelter ist
    [InlineData("1.5.8", "1.5.9", true)]
    [InlineData("1.5.9", "1.6.0", true)]
    [InlineData("1.5.9", "1.5.9", false)]                   // gleiche: wie bisher nur das Fenster zeigen
    [InlineData("1.6.0", "1.5.9", false)]                   // die laufende ist neuer
    [InlineData(null, "1.5.9", false)]                      // unbekannt: nicht raten
    public void Zweiter_Start_ersetzt_nur_eine_aeltere_Version(string? laufend, string neu, bool fragen)
    {
        var instanz = new LaufendesProgramm.Instanz(laufend, 4711, MitSignal: true);
        string? frage = LaufendesProgramm.ErsetzenFrage(instanz, neu);
        Assert.Equal(fragen, frage != null);
        if (frage != null) Assert.Equal($"AutoSchnell Vergleich {laufend} läuft noch. Jetzt durch Version {neu} ersetzen?", frage);
        Assert.Null(LaufendesProgramm.ErsetzenFrage(null, neu));
    }

    [Fact]   // 1.5.9 (B): die laufende Instanz veroeffentlicht Version und Prozess im geteilten Speicher
    public void Version_der_laufenden_Instanz_wird_veroeffentlicht_und_gelesen()
    {
        Assert.Equal("1.5.9|4711", LaufendesProgramm.Inhalt("1.5.9", 4711));
        Assert.Equal(new LaufendesProgramm.Instanz("1.5.9", 4711, true), LaufendesProgramm.AusInhalt("1.5.9|4711"));
        Assert.Null(LaufendesProgramm.AusInhalt(""));
        Assert.Null(LaufendesProgramm.AusInhalt("1.5.9"));
        Assert.Null(LaufendesProgramm.AusInhalt("1.5.9|x"));
        Assert.Null(LaufendesProgramm.AusInhalt("|12"));

        string name = @"Local\AutoSchnell.Test." + Guid.NewGuid().ToString("N");
        Assert.Null(LaufendesProgramm.Gelesen(name));
        using (var v = LaufendesProgramm.Veroeffentlichen("1.5.9", 4711, name))
        {
            Assert.NotNull(v);
            Assert.Equal(new LaufendesProgramm.Instanz("1.5.9", 4711, true), LaufendesProgramm.Gelesen(name));
        }
        Assert.Null(LaufendesProgramm.Gelesen(name));                    // beendet: nichts mehr da
        Assert.True(LaufendesProgramm.UnserProdukt("AutoSchnell Vergleich"));
        Assert.True(LaufendesProgramm.UnserProdukt("AutoSchnell AutoPointer-Vergleich"));   // Name bis 06.10.2026
        Assert.False(LaufendesProgramm.UnserProdukt("AutoSchnell Analyse"));
        Assert.False(LaufendesProgramm.UnserProdukt(null));
    }

    [Fact]   // 1.5.9 (F): Startmenue-Eintrag nur auf die feste Kopie, nur wenn er fehlt oder woanders hinzeigt
    public void Startmenue_Eintrag_nur_wenn_noetig()
    {
        Assert.True(Installation.StartmenueAnlegen(kopieVorhanden: true, verknuepfungVorhanden: false, null, Fest));
        Assert.False(Installation.StartmenueAnlegen(kopieVorhanden: true, verknuepfungVorhanden: true, Fest.ToUpperInvariant(), Fest));
        Assert.True(Installation.StartmenueAnlegen(kopieVorhanden: true, verknuepfungVorhanden: true, @"C:\Users\x\Downloads\a.exe", Fest));
        Assert.False(Installation.StartmenueAnlegen(kopieVorhanden: false, verknuepfungVorhanden: false, null, Fest));
        Assert.EndsWith(@"\AutoSchnell Vergleich.lnk", Installation.StartmenuePfad);
    }

    [Fact]   // Paket 2 (A7): Befehlszeile fuer den Neustart nach Absturz
    public void Neustart_Befehlszeile_behaelt_Argumente_und_startet_leise()
    {
        Assert.Equal("--autostart", Program.NeustartBefehl(Array.Empty<string>()));
        Assert.Equal("--probelauf --server http://127.0.0.1:9 --autostart",
                     Program.NeustartBefehl(new[] { "--probelauf", "--server", "http://127.0.0.1:9" }));
        Assert.Equal("--autostart", Program.NeustartBefehl(new[] { "--autostart" }));
        Assert.Equal("\"mit leer\" --autostart", Program.NeustartBefehl(new[] { "mit leer" }));
    }
}
