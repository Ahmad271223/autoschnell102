using AutoPointerVergleich;
using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Pruefung 04.10.2026: Nach einem Update an anderer Stelle startete Windows weiter die ALTE Datei —
/// der Autostart-Eintrag zeigt jetzt beim Programmstart auf die laufende Datei (nur wenn er an ist).</summary>
public class AutostartTests
{
    private const string Neu = @"C:\Users\x\Downloads\AutoSchnell-Vergleich (1).exe";

    [Fact]
    public void Wert_hat_Anfuehrungszeichen_und_Autostart_Schalter() =>
        Assert.Equal("\"" + Neu + "\" --autostart", Autostart.Wert(Neu));

    [Fact]
    public void Alter_Pfad_wird_nachgezogen() =>
        Assert.True(Autostart.MussNachziehen("\"C:\\Programme\\AutoSchnell-Vergleich.exe\" --autostart", Neu));

    [Fact]
    public void Gleicher_Pfad_bleibt_auch_bei_anderer_Schreibweise()
    {
        Assert.False(Autostart.MussNachziehen(Autostart.Wert(Neu), Neu));
        Assert.False(Autostart.MussNachziehen(Autostart.Wert(Neu.ToUpperInvariant()) + " ", Neu));
    }

    [Fact]
    public void Ohne_Eintrag_bleibt_der_Autostart_aus()
    {
        Assert.False(Autostart.MussNachziehen(null, Neu));
        Assert.False(Autostart.MussNachziehen("\"a.exe\" --autostart", null));
    }
}
