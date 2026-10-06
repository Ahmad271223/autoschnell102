namespace AutoPointerVergleich;

/// <summary>Wunsch Ahmad 06.10.2026: "das Programm vergleicht selber alle Autos auf AutoPointer und klickt sich
/// selber durch — es soll nur die vergleichen, die man anklickt". Ursache: der Ueberwacher vergleicht bei JEDER
/// Aenderung der Detailansicht — auch wenn AutoPointer selbst ein anderes Auto zeigt (Live-Liste fuegt neue Inserate
/// oben ein, die Markierung rutscht weiter). Hier wird gemerkt, wann der Sucher zuletzt mit der Maus IN AutoPointer
/// geklickt hat; nur danach wird verglichen.
///
/// Bewusst: nur die Maustasten (links/rechts, auch bei vertauschten Tasten) und nur, ob der Mauszeiger dabei ueber
/// einem AutoPointer-Fenster lag — kein Hook, keine Tastatur, nichts wird gespeichert ausser dem Zeitpunkt.
/// Ein eigener Faden fragt alle 15 ms nach (ein Klick dauert 50-150 ms, er geht also nicht verloren).</summary>
internal sealed class Klicks : IDisposable
{
    private const int VK_LBUTTON = 0x01;
    private const int VK_RBUTTON = 0x02;
    private const int AbfrageMs = 15;

    private readonly Func<IntPtr> _autoPointer;
    private readonly Thread _faden;
    private volatile bool _ende;
    private long _letzter = long.MinValue / 2;

    /// <param name="autoPointer">Hauptfenster von AutoPointer (IntPtr.Zero = nicht gefunden).</param>
    public Klicks(Func<IntPtr> autoPointer)
    {
        _autoPointer = autoPointer;
        _faden = new Thread(Lauf) { IsBackground = true, Name = "AutoPointer-Klicks", Priority = ThreadPriority.BelowNormal };
        _faden.Start();
    }

    /// <summary>Zeitpunkt (Environment.TickCount64) des letzten Mausklicks in AutoPointer.</summary>
    public long LetzterKlick => Interlocked.Read(ref _letzter);

    private void Lauf()
    {
        bool linksVorher = false, rechtsVorher = false;
        while (!_ende)
        {
            try
            {
                bool links = (Native.GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0;
                bool rechts = (Native.GetAsyncKeyState(VK_RBUTTON) & 0x8000) != 0;
                if ((links && !linksVorher) || (rechts && !rechtsVorher))
                {
                    if (UeberAutoPointer()) Interlocked.Exchange(ref _letzter, Environment.TickCount64);
                }
                linksVorher = links;
                rechtsVorher = rechts;
            }
            catch (Exception)
            {
                // nie den Faden beenden — im schlimmsten Fall wird ein Klick nicht bemerkt ("Vergleichen" geht immer)
            }
            Thread.Sleep(AbfrageMs);
        }
    }

    private bool UeberAutoPointer()
    {
        IntPtr ap = _autoPointer();
        if (ap == IntPtr.Zero || !Native.GetCursorPos(out var p)) return false;
        IntPtr unter = Native.WindowFromPoint(p);
        if (unter == IntPtr.Zero) return false;
        IntPtr wurzel = Native.GetAncestor(unter, Native.GA_ROOT);
        Native.GetWindowThreadProcessId(wurzel == IntPtr.Zero ? unter : wurzel, out uint pidUnter);
        Native.GetWindowThreadProcessId(ap, out uint pidAp);
        return pidUnter != 0 && pidUnter == pidAp;
    }

    public void Dispose() => _ende = true;
}
