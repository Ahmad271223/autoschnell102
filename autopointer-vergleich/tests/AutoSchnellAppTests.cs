using Xunit;

namespace AutoPointerVergleich.Tests;

/// <summary>Wunsch Ahmad 03.10.2026: "Vertrag" oeffnet die installierte AutoSchnell-App statt eines Browser-Tabs.</summary>
public class AutoSchnellAppTests
{
    private const string Server = "https://app.auto-schnellkauf.de";
    private const string Edge = @"C:\Program Files (x86)\Microsoft\Edge\Application\msedge_proxy.exe";

    [Fact]   // so legt Edge die Verknuepfung an (echte Werte von Ahmads PC, 03.10.2026)
    public void Edge_App_wird_erkannt()
    {
        var v = AutoSchnellApp.AusVerknuepfung(Edge,
            "--profile-directory=Default --app-id=kloggooekeelopkjnmmejeamhcghhkim --app-url=https://app.auto-schnellkauf.de/start --app-run-on-os-login-mode=windowed --app-launch-source=19",
            @"C:\Users\x\Desktop\AutoSchnell.lnk", Server);
        Assert.NotNull(v);
        Assert.Equal(("Default", "kloggooekeelopkjnmmejeamhcghhkim"), (v!.Profil, v.AppId));
        Assert.Equal("--profile-directory=\"Default\" --app-id=kloggooekeelopkjnmmejeamhcghhkim "
                     + "--app-launch-url-for-shortcuts-menu-item=\"https://app.auto-schnellkauf.de/app/vergleich?url=x\"",
                     AutoSchnellApp.Argumente(v, "https://app.auto-schnellkauf.de/app/vergleich?url=x"));
    }

    [Fact]
    public void Andere_Web_Apps_und_Programme_nicht()
    {
        // eine andere installierte Web-App (Ahmads PC: "Ums Eck - Lokaler Marktplatz")
        Assert.Null(AutoSchnellApp.AusVerknuepfung(Edge,
            "--profile-directory=Default --app-id=jaecofbdpipjafphofeaceimgcclphin --app-url=http://localhost:3001/ --app-launch-source=4",
            "Ums Eck - Lokaler Marktplatz.lnk", Server));
        // gleicher Name, aber andere Adresse
        Assert.Null(AutoSchnellApp.AusVerknuepfung(Edge,
            "--app-id=kloggooekeelopkjnmmejeamhcghhkim --app-url=https://boese.example/", "AutoSchnell.lnk", Server));
        // kein Browser / keine App-Kennung
        Assert.Null(AutoSchnellApp.AusVerknuepfung(@"C:\Tools\irgendwas.exe", "--app-id=kloggooekeelopkjnmmejeamhcghhkim", "AutoSchnell.lnk", Server));
        Assert.Null(AutoSchnellApp.AusVerknuepfung(Edge, "--profile-directory=Default", "AutoSchnell.lnk", Server));
    }

    [Fact]   // Chrome schreibt keine Adresse in die Verknuepfung — dann zaehlt der Name
    public void Chrome_App_ueber_den_Namen()
    {
        var v = AutoSchnellApp.AusVerknuepfung(@"C:\Program Files\Google\Chrome\Application\chrome_proxy.exe",
            "--profile-directory=\"Profile 1\" --app-id=abcdefghijklmnopabcdefghijklmnop", @"C:\Startmenue\AutoSchnell.lnk", Server);
        Assert.Equal("Profile 1", v?.Profil);
        Assert.Null(AutoSchnellApp.AusVerknuepfung(@"C:\Program Files\Google\Chrome\Application\chrome_proxy.exe",
            "--profile-directory=Default --app-id=abcdefghijklmnopabcdefghijklmnop", @"C:\Startmenue\YouTube.lnk", Server));
    }

    [Fact]   // nur zur Entwicklung: AUTOSCHNELL_APP_PRUEFEN=1 sucht die echte App auf diesem PC (startet nichts)
    public void Finden_auf_diesem_PC()
    {
        if (Environment.GetEnvironmentVariable("AUTOSCHNELL_APP_PRUEFEN") != "1") return;
        var v = AutoSchnellApp.Finden(Server);
        Assert.NotNull(v);
        Assert.Equal("kloggooekeelopkjnmmejeamhcghhkim", v!.AppId);
    }
}
