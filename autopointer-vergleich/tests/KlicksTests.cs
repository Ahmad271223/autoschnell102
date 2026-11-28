using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 06.10.2026: die Klick-Erkennung laeuft im eigenen Faden, ohne AutoPointer gibt es nie einen
/// "Klick in AutoPointer", und Beenden haelt den Faden an.</summary>
public class KlicksTests
{
    [Fact]
    public void Ohne_AutoPointer_nie_ein_Klick_und_Beenden_haelt_an()
    {
        var klicks = new Klicks(() => IntPtr.Zero);
        Thread.Sleep(60);
        Assert.True(klicks.LetzterKlick < 0, "ohne AutoPointer-Fenster darf kein Klick gezaehlt werden");
        klicks.Dispose();
        Thread.Sleep(40);
        Assert.True(klicks.LetzterKlick < 0);
    }

    [Fact]
    public void Ein_alter_Klick_reicht_nicht_der_Vorlauf_ist_5_Sekunden()
    {
        Assert.Equal(5000, Ueberwacher.KlickVorlaufMs);
    }
}
