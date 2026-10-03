using System.Runtime.InteropServices;
using System.Text;

namespace AutoPointerVergleich;

/// <summary>Win32-Aufrufe. Alles hier liest nur: Fenster finden, Klassen/Texte
/// abfragen, Inhalte abfotografieren. AutoPointer wird nie veraendert.</summary>
internal static class Native
{
    public delegate bool EnumProc(IntPtr hwnd, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT
    {
        public int Left, Top, Right, Bottom;
        public readonly int Width => Right - Left;
        public readonly int Height => Bottom - Top;
    }

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr lParam);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, StringBuilder sb, int max);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] private static extern int GetWindowText(IntPtr hwnd, StringBuilder sb, int max);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hwnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hwnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr hwnd, IntPtr dc);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr dc, uint flags);
    [DllImport("gdi32.dll")] public static extern bool BitBlt(IntPtr dst, int x, int y, int w, int h, IntPtr src, int sx, int sy, int rop);
    public const int SRCCOPY = 0x00CC0020;

    [DllImport("user32.dll")] public static extern IntPtr GetWindowDpiAwarenessContext(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr ctx);
    [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr hwnd);

    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool AllowSetForegroundWindow(int dwProcessId);
    public const int ASFW_ANY = -1;
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hwnd);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool attach);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();

    [DllImport("user32.dll")] public static extern bool RegisterHotKey(IntPtr hwnd, int id, uint modifiers, uint vk);
    [DllImport("user32.dll")] public static extern bool UnregisterHotKey(IntPtr hwnd, int id);
    public const uint MOD_ALT = 0x1, MOD_CONTROL = 0x2, MOD_NOREPEAT = 0x4000;
    public const int WM_HOTKEY = 0x0312;

    [DllImport("kernel32.dll")] public static extern bool AttachConsole(int pid);

    [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr hIcon);

    public static string Klasse(IntPtr hwnd)
    {
        var sb = new StringBuilder(256);
        GetClassName(hwnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static string Text(IntPtr hwnd)
    {
        var sb = new StringBuilder(512);
        GetWindowText(hwnd, sb, sb.Capacity);
        return sb.ToString();
    }

    public static List<IntPtr> Kinder(IntPtr parent)
    {
        var liste = new List<IntPtr>();
        EnumChildWindows(parent, (h, _) => { liste.Add(h); return true; }, IntPtr.Zero);
        return liste;
    }

    /// <summary>Fuehrt <paramref name="aktion"/> im DPI-Kontext des Zielfensters aus,
    /// damit Groessen und PrintWindow-Ausgabe zusammenpassen - auch wenn
    /// AutoPointer nicht DPI-bewusst ist und Windows es skaliert.</summary>
    public static T ImDpiKontext<T>(IntPtr hwnd, Func<T> aktion)
    {
        IntPtr alt = IntPtr.Zero;
        try
        {
            var ctx = GetWindowDpiAwarenessContext(hwnd);
            if (ctx != IntPtr.Zero) alt = SetThreadDpiAwarenessContext(ctx);
        }
        catch (EntryPointNotFoundException) { }
        try { return aktion(); }
        finally
        {
            if (alt != IntPtr.Zero) SetThreadDpiAwarenessContext(alt);
        }
    }
}
