using System.Security.Cryptography;
using System.Text;

namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 03.10.2026: "die ausgelesenen Dateien sollen fuer Aussenstehende verschluesselt sein".
/// Was das Programm auf dem PC ablegt (Protokoll mit Fahrzeugdaten, Links und Inserat-Adressen, auf Wunsch
/// Erkennungsbilder), verschluesselt Windows (DPAPI) fuer DIESEN Windows-Benutzer: lesbar nur ueber das Programm
/// unter demselben Konto — nicht im Editor, nicht auf einem anderen PC, nicht fuer andere Benutzer.</summary>
internal static class Tresor
{
    private static readonly byte[] Zusatz = Encoding.UTF8.GetBytes("AutoSchnell.AutoPointerVergleich.Dateien");

    public static byte[] Verschluesseln(byte[] klar) =>
        ProtectedData.Protect(klar, Zusatz, DataProtectionScope.CurrentUser);

    public static byte[] Entschluesseln(byte[] geheim) =>
        ProtectedData.Unprotect(geheim, Zusatz, DataProtectionScope.CurrentUser);

    /// <summary>Eine Textzeile -> eine Base64-Zeile (anhaengbar, ohne Zeilenumbrueche).</summary>
    public static string Zeile(string klar) => Convert.ToBase64String(Verschluesseln(Encoding.UTF8.GetBytes(klar)));

    /// <summary>Base64-Zeile zurueck in Text; null, wenn sie nicht von diesem Benutzer stammt oder kaputt ist.</summary>
    public static string? ZeileLesen(string geheim)
    {
        try { return Encoding.UTF8.GetString(Entschluesseln(Convert.FromBase64String(geheim.Trim()))); }
        catch (FormatException) { return null; }
        catch (CryptographicException) { return null; }
    }
}
