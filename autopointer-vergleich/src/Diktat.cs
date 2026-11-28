using System.Reflection;
using System.Runtime.InteropServices;
using NAudio.Wave;
using Whisper.net;
using Whisper.net.LibraryLoader;

namespace AutoPointerVergleich;

/// <summary>
/// Sprachdiktat am PC (Wunsch Ahmad 24.11.2026): Knopf "Diktat" in der Leiste oder Strg+Alt+D — sprechen,
/// nochmal tippen, der Text wird in das Feld geschrieben, in dem der Cursor steht (Browser, AutoSchnell, egal wo).
/// Erkennung mit Whisper.net/whisper.cpp (MIT) auf diesem PC: der Ton geht an keinen Server. Das Sprachmodell
/// (574 MB) wird einmalig von Hugging Face geladen und unter %LOCALAPPDATA%\AutoSchnell\Diktat abgelegt.
/// </summary>
internal sealed class Diktat : IDisposable
{
    public const string ModellDatei = "ggml-large-v3-turbo-q5_0.bin";
    public const string ModellUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/" + ModellDatei;
    public const long ModellBytes = 574_041_195;
    public const int ModellMB = 574;
    public const int MaxSekunden = 300;
    private const int Abtastrate = 16_000;
    private const string LaufzeitVersion = "1.9.1";

    private readonly string _ordner;
    private readonly object _sperre = new();
    private WaveInEvent? _mikro;
    private List<float>? _proben;
    private WhisperFactory? _fabrik;
    private bool _laufzeitBereit;

    public Diktat(string? ordner = null)
    {
        _ordner = ordner ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AutoSchnell", "Diktat");
    }

    public string ModellPfad => Path.Combine(_ordner, ModellDatei);
    public bool ModellVorhanden => File.Exists(ModellPfad) && new FileInfo(ModellPfad).Length == ModellBytes;
    public bool NimmtAuf { get { lock (_sperre) return _mikro != null; } }
    public static bool HatMikrofon => WaveInEvent.DeviceCount > 0;

    /// <summary>Laedt das Modell (mit .part-Datei, erst am Ende umbenannt — ein Abbruch hinterlaesst kein halbes Modell).</summary>
    public async Task ModellLadenAsync(IProgress<double>? fortschritt, CancellationToken abbruch)
    {
        Directory.CreateDirectory(_ordner);
        string teil = ModellPfad + ".part";
        using var http = new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        http.DefaultRequestHeaders.UserAgent.ParseAdd($"AutoSchnell-Vergleich/{Application.ProductVersion}");
        using var antwort = await http.GetAsync(ModellUrl, HttpCompletionOption.ResponseHeadersRead, abbruch);
        antwort.EnsureSuccessStatusCode();
        long gesamt = antwort.Content.Headers.ContentLength ?? ModellBytes;
        await using (var quelle = await antwort.Content.ReadAsStreamAsync(abbruch))
        await using (var ziel = new FileStream(teil, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20, true))
        {
            var puffer = new byte[1 << 20];
            long gelesen = 0;
            int n;
            while ((n = await quelle.ReadAsync(puffer, abbruch)) > 0)
            {
                await ziel.WriteAsync(puffer.AsMemory(0, n), abbruch);
                gelesen += n;
                fortschritt?.Report(gesamt > 0 ? (double)gelesen / gesamt : 0);
            }
        }
        if (new FileInfo(teil).Length != ModellBytes)
        {
            File.Delete(teil);
            throw new IOException("Das Sprachmodell kam unvollständig an.");
        }
        File.Move(teil, ModellPfad, overwrite: true);
    }

    public void AufnahmeStarten()
    {
        lock (_sperre)
        {
            if (_mikro != null) return;
            var proben = new List<float>(Abtastrate * 30);
            var mikro = new WaveInEvent { WaveFormat = new WaveFormat(Abtastrate, 16, 1), BufferMilliseconds = 100 };
            mikro.DataAvailable += (_, e) =>
            {
                lock (_sperre)
                {
                    if (!ReferenceEquals(_proben, proben)) return;
                    PcmAnhaengen(proben, e.Buffer, e.BytesRecorded, Abtastrate * MaxSekunden);
                    _stille.Messen(PegelDb(e.Buffer, e.BytesRecorded), Environment.TickCount64);
                }
            };
            _stille = new StilleMesser();
            mikro.StartRecording();
            _mikro = mikro;
            _proben = proben;
        }
    }

    // ---- Pegel + Auto-Stopp (24.11.2026): Anzeige in der Leiste, nach 2 s Stille hoert die Aufnahme auf ----

    public const int AutoStoppMs = 2000;
    private StilleMesser _stille = new();

    /// <summary>Lautstaerke 0..1 fuer die Anzeige.</summary>
    public double Pegel { get { lock (_sperre) return _stille.Pegel; } }

    /// <summary>Millisekunden seit zuletzt gesprochen wurde; -1, solange noch nichts gesagt wurde.</summary>
    public long StilleMs { get { lock (_sperre) return _stille.StilleMs(Environment.TickCount64); } }

    /// <summary>Lautstaerke eines 16-bit-PCM-Puffers in dBFS (-160 = still, 0 = Vollaussteuerung).</summary>
    internal static float PegelDb(byte[] puffer, int bytes)
    {
        int n = bytes / 2;
        if (n == 0) return -160f;
        double summe = 0;
        for (int i = 0; i + 1 < bytes; i += 2)
        {
            double p = BitConverter.ToInt16(puffer, i) / 32768.0;
            summe += p * p;
        }
        double rms = Math.Sqrt(summe / n);
        return rms <= 1e-8 ? -160f : (float)(20 * Math.Log10(rms));
    }

    /// <summary>Wie in der iPhone-App: Grundrauschen wird mitgefuehrt (steigt ~1 dB/s nach), Sprache ist deutlich
    /// lauter (12 dB ueber dem Rauschen, mindestens -50 dB). So stoppt es auch im lauten Buero nicht zu frueh.
    /// Wurde nie gesprochen, wird gar nicht erst erkannt: Whisper erfindet aus Stille Woerter ("Amen.").</summary>
    internal sealed class StilleMesser
    {
        private float _rauschen = float.NaN;
        private float _letzter = -160f;
        private bool _gesprochen;
        private long _letzteStimme;

        public void Messen(float db, long jetzt)
        {
            _letzter = db;
            _rauschen = float.IsNaN(_rauschen) ? db : Math.Min(_rauschen + 0.1f, db);   // je 100-ms-Puffer
            if (db > Math.Max(_rauschen + 12f, -50f))
            {
                _gesprochen = true;
                _letzteStimme = jetzt;
            }
        }

        public double Pegel => Math.Clamp((_letzter + 50.0) / 40.0, 0, 1);
        public long StilleMs(long jetzt) => _gesprochen ? jetzt - _letzteStimme : -1;
    }

    /// <summary>"▮▮▯▯▯" fuer den Leisten-Knopf.</summary>
    internal static string PegelBalken(double pegel)
    {
        int voll = (int)Math.Round(Math.Clamp(pegel, 0, 1) * 5);
        return new string('▮', voll) + new string('▯', 5 - voll);
    }

    /// <summary>16-bit-PCM (little endian) als float -1..1 anhaengen, hoechstens bis max Proben.</summary>
    internal static void PcmAnhaengen(List<float> ziel, byte[] puffer, int bytes, int max)
    {
        for (int i = 0; i + 1 < bytes && ziel.Count < max; i += 2)
            ziel.Add(BitConverter.ToInt16(puffer, i) / 32768f);
    }

    /// <summary>Stoppt die Aufnahme und gibt die Proben zurueck (leer, wenn nichts lief).</summary>
    public float[] AufnahmeStoppen()
    {
        WaveInEvent? mikro;
        List<float>? proben;
        lock (_sperre)
        {
            mikro = _mikro;
            proben = _proben;
            _mikro = null;
            _proben = null;
        }
        if (mikro != null)
        {
            try { mikro.StopRecording(); } catch { /* Geraet weg */ }
            mikro.Dispose();
        }
        return proben?.ToArray() ?? Array.Empty<float>();
    }

    /// <summary>Optionaler Vorab-Text fuer Whisper (Fachwoerter). Nur fuer Messungen (DiktatEchtTests,
    /// DIKTAT_PROMPT) — ob er eingeschaltet wird, entscheidet die Messung, siehe README.</summary>
    internal string? Hinweistext { get; set; }

    public async Task<string> ErkennenAsync(float[] proben, CancellationToken abbruch)
    {
        if (proben.Length < Abtastrate / 2) return "";           // unter einer halben Sekunde: nichts gesagt
        WhisperFactory fabrik;
        lock (_fabrikSperre) fabrik = FabrikHolen();
        // Messung 24.11.2026 (Ryzen 7, 8 Kerne/16 Threads, nur CPU): 16 Threads 10 s statt 13 s mit 8
        int kerne = Math.Clamp(Environment.ProcessorCount, 2, 16);
        var bauer = fabrik.CreateBuilder()
            .WithLanguage("de")
            .WithThreads(kerne)
            .WithNoContext();
        if (!string.IsNullOrWhiteSpace(Hinweistext)) bauer = bauer.WithPrompt(Hinweistext);
        await using var verarbeitung = bauer.Build();
        var teile = new List<string>();
        await foreach (var abschnitt in verarbeitung.ProcessAsync(proben, abbruch))
            teile.Add(abschnitt.Text);
        return TextAufbereiten(teile);
    }

    /// <summary>Abschnitte zusammenfuegen; Whisper-Platzhalter wie "[Musik]" / "(Stille)" fallen weg.</summary>
    internal static string TextAufbereiten(IEnumerable<string> teile)
    {
        var sauber = teile
            .Select(t => System.Text.RegularExpressions.Regex.Replace(t ?? "", @"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*", " "))
            .Select(t => System.Text.RegularExpressions.Regex.Replace(t, @"\s+", " ").Trim())
            .Where(t => t.Length > 0);
        return string.Join(" ", sauber);
    }

    /// <summary>Modell schon beim Start der Aufnahme in den Speicher holen (dauert einige Sekunden) — waehrend
    /// gesprochen wird, statt danach. Mehrfach aufrufbar.</summary>
    public void Vorbereiten()
    {
        lock (_fabrikSperre) FabrikHolen();
    }

    private readonly object _fabrikSperre = new();

    /// <summary>Modell laden und einmal mit einer Sekunde Stille rechnen lassen — beim allerersten Mal richtet der
    /// Grafiktreiber dabei seine Programme ein (gemessen ~30 s, danach dauerhaft im Treiber-Cache). Im Hintergrund
    /// beim Programmstart, damit das erste echte Diktat nicht wartet.</summary>
    public async Task AufwaermenAsync(CancellationToken abbruch)
    {
        if (!ModellVorhanden) return;
        await Task.Run(Vorbereiten, abbruch);
        await ErkennenAsync(new float[Abtastrate], abbruch);
    }

    private WhisperFactory FabrikHolen()
    {
        if (_fabrik != null) return _fabrik;
        if (!ModellVorhanden) throw new InvalidOperationException("Das Sprachmodell fehlt.");
        LaufzeitBereitstellen();
        // Flash Attention: CPU 15,6 → 13 s je Diktat; auf der Grafikkarte (Vulkan) 0,3-0,8 s
        _fabrik = WhisperFactory.FromPath(ModellPfad, new WhisperFactoryOptions { UseFlashAttention = true });
        return _fabrik;
    }

    /// <summary>
    /// Die vier whisper.cpp-DLLs stecken als Ressource in der EXE (eine Datei bleibt eine Datei) und werden beim
    /// ersten Diktat nach %LOCALAPPDATA%\AutoSchnell\Diktat\laufzeit-…\runtimes\win-x64 entpackt. Whisper.net sucht
    /// unter dem Ordner von RuntimeOptions.LibraryPath nach "runtimes\win-x64".
    /// </summary>
    /// <summary>Fuer Messungen/Notfall: Grafikkarte nicht nutzen (Umgebungsvariable AUTOSCHNELL_DIKTAT_NUR_CPU=1).</summary>
    internal static bool NurCpu => Environment.GetEnvironmentVariable("AUTOSCHNELL_DIKTAT_NUR_CPU") == "1";

    internal void LaufzeitBereitstellen()
    {
        if (_laufzeitBereit) return;
        string basis = Path.Combine(_ordner, "laufzeit-" + LaufzeitVersion);
        var asm = Assembly.GetExecutingAssembly();
        // "diktat/" = CPU (runtimes\win-x64), "diktat-vulkan/" = Grafikkarte (runtimes\vulkan\win-x64)
        foreach (var (praefix, ziel) in new[]
                 {
                     ("diktat/", Path.Combine(basis, "runtimes", "win-x64")),
                     ("diktat-vulkan/", Path.Combine(basis, "runtimes", "vulkan", "win-x64")),
                 })
        {
            Directory.CreateDirectory(ziel);
            foreach (var name in asm.GetManifestResourceNames().Where(n => n.StartsWith(praefix, StringComparison.Ordinal)))
            {
                string datei = Path.Combine(ziel, name[praefix.Length..]);
                using var quelle = asm.GetManifestResourceStream(name)!;
                if (File.Exists(datei) && new FileInfo(datei).Length == quelle.Length) continue;
                using var aus = File.Create(datei);
                quelle.CopyTo(aus);
            }
        }
        RuntimeOptions.LibraryPath = Path.Combine(basis, "whisper.dll");
        // Grafikkarte zuerst; ohne Vulkan-Treiber nimmt Whisper.net die naechste (CPU)
        RuntimeOptions.RuntimeLibraryOrder = NurCpu
            ? new List<RuntimeLibrary> { RuntimeLibrary.Cpu, RuntimeLibrary.CpuNoAvx }
            : new List<RuntimeLibrary> { RuntimeLibrary.Vulkan, RuntimeLibrary.Cpu, RuntimeLibrary.CpuNoAvx };
        _laufzeitBereit = true;
    }

    public void Dispose()
    {
        AufnahmeStoppen();
        _fabrik?.Dispose();
        _fabrik = null;
    }

    // ---- Text ins aktive Feld schreiben (wie getippt, ohne die Zwischenablage anzufassen) ----

    /// <summary>Zeilenumbrueche werden Leerzeichen — ein Enter wuerde in einzeiligen Feldern das Formular abschicken.</summary>
    internal static string FuerEingabe(string text) =>
        System.Text.RegularExpressions.Regex.Replace(text ?? "", @"\s+", " ").Trim();

    public static bool Eintippen(string text)
    {
        string t = FuerEingabe(text);
        if (t.Length == 0) return true;
        var eingaben = new INPUT[t.Length * 2];
        for (int i = 0; i < t.Length; i++)
        {
            eingaben[2 * i] = Taste(t[i], hoch: false);
            eingaben[2 * i + 1] = Taste(t[i], hoch: true);
        }
        uint gesendet = SendInput((uint)eingaben.Length, eingaben, Marshal.SizeOf<INPUT>());
        return gesendet == eingaben.Length;
    }

    /// <summary>"Rückgängig": so viele Zeichen per Rücktaste wieder loeschen, wie eingetippt wurden.</summary>
    public static bool Loeschen(int anzahl)
    {
        if (anzahl <= 0) return true;
        var eingaben = new INPUT[anzahl * 2];
        for (int i = 0; i < anzahl; i++)
        {
            eingaben[2 * i] = Ruecktaste(hoch: false);
            eingaben[2 * i + 1] = Ruecktaste(hoch: true);
        }
        return SendInput((uint)eingaben.Length, eingaben, Marshal.SizeOf<INPUT>()) == eingaben.Length;
    }

    private static INPUT Ruecktaste(bool hoch) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion { ki = new KEYBDINPUT { wVk = VK_BACK, dwFlags = hoch ? KEYEVENTF_KEYUP : 0 } },
    };

    private const ushort VK_BACK = 0x08;

    private static INPUT Taste(char zeichen, bool hoch) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion
        {
            ki = new KEYBDINPUT
            {
                wVk = 0, wScan = zeichen,
                dwFlags = KEYEVENTF_UNICODE | (hoch ? KEYEVENTF_KEYUP : 0),
            },
        },
    };

    private const uint INPUT_KEYBOARD = 1;
    private const uint KEYEVENTF_KEYUP = 0x0002;
    private const uint KEYEVENTF_UNICODE = 0x0004;

    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT
    {
        public uint type;
        public InputUnion U;
    }

    // Die Union muss so gross sein wie ihr groesstes Mitglied (MOUSEINPUT), sonst lehnt SendInput ab.
    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT
    {
        public int dx, dy;
        public uint mouseData, dwFlags, time;
        public IntPtr dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct KEYBDINPUT
    {
        public ushort wVk, wScan;
        public uint dwFlags, time;
        public IntPtr dwExtraInfo;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    internal static int InputGroesse => Marshal.SizeOf<INPUT>();
}
