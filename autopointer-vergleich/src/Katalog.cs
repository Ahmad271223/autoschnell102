using System.Globalization;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace AutoPointerVergleich;

internal sealed class MobileMarke
{
    public required string Name { get; init; }
    public required string Id { get; init; }
    /// <summary>normalisierter Modellname -> Id, in Katalogreihenfolge.</summary>
    public List<(string Norm, string Id, string Name)> Modelle { get; } = new();
    public Dictionary<string, string> ModellIndex { get; } = new();
    public List<string> ModelleRoh { get; } = new();
}

internal sealed record AutoScoutModell(int ModelId, string ModelName);

internal sealed class AutoScoutMarke
{
    public required int MakeId { get; init; }
    public required string MakeName { get; init; }
    public List<AutoScoutModell> Modelle { get; } = new();
}

internal sealed record ModellTreffer(string Id, string Name, bool Unscharf);

/// <summary>Marken-/Modellkataloge von mobile.de und AutoScout24 mit derselben
/// Zuordnungslogik wie im Backend (mobile_service._resolve_make/_resolve_model,
/// autoscout_service._find_make/_find_model) - damit dieses Programm und der
/// AutoSchnell-Vergleich dieselbe Suche bauen. Zusaetzlich: unscharfer Abgleich
/// fuer Lesefehler der Texterkennung.</summary>
internal sealed class Katalog
{
    private readonly Dictionary<string, MobileMarke> _mobile = new();
    private readonly List<AutoScoutMarke> _autoscout = new();

    // mobile_service._MAKE_ALIASES
    private static readonly Dictionary<string, string> MobileAliase = new()
    {
        ["vw"] = "volkswagen", ["mercedes"] = "mercedesbenz", ["rangerover"] = "landrover",
        ["dsautomobiles"] = "ds", ["ds"] = "ds",
    };

    // autoscout_service._MAKE_ALIASES
    private static readonly Dictionary<string, string> AutoScoutAliase = new()
    {
        ["vw"] = "Volkswagen", ["merc"] = "Mercedes-Benz", ["mercedesbenz"] = "Mercedes-Benz",
        ["mercedes"] = "Mercedes-Benz", ["rangerover"] = "Land Rover", ["rolls"] = "Rolls-Royce",
        ["rollsroyce"] = "Rolls-Royce", ["astonmartin"] = "Aston Martin", ["alfaromeo"] = "Alfa Romeo",
        ["lambo"] = "Lamborghini",
    };

    // mobile_service._MODELL_ALIASE (RP-419)
    private static readonly Dictionary<(string, string), string> MobileModellAliase = new()
    {
        [("kia", "ceedsw")] = "ceedsportswagon",
        [("kia", "ceedswceedsw")] = "ceedsportswagon",
    };

    private static readonly Regex GenerationPunkt = new(@"\b(T\d)\.\d\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private static readonly Regex KlammerZusatz = new(@"\s*\([^)]*\)\s*", RegexOptions.Compiled);
    private static readonly Regex Generisch = new(@"^\s*(weitere|andere|sonstige|other|others|misc)\b", RegexOptions.IgnoreCase | RegexOptions.Compiled);

    /// <summary>Platzhalter statt Modell ("weitere VW" bei Kleinanzeigen, "Andere", "Sonstige").</summary>
    public static bool IstPlatzhalter(string? modell) => string.IsNullOrWhiteSpace(modell) || Generisch.IsMatch(modell);

    public int AnzahlMobileMarken => _mobile.Count;
    public int AnzahlAutoScoutMarken => _autoscout.Count;

    public static Katalog Laden()
    {
        var asm = Assembly.GetExecutingAssembly();
        string Lies(string name)
        {
            using var s = asm.GetManifestResourceStream(name) ?? throw new InvalidOperationException($"Katalog {name} fehlt");
            using var r = new StreamReader(s, Encoding.UTF8);
            return r.ReadToEnd();
        }
        return AusJson(Lies("mobile_makes_models.json"), Lies("autoscout_makes.json"));
    }

    public static Katalog AusJson(string mobileJson, string autoscoutJson)
    {
        var k = new Katalog();
        using (var doc = JsonDocument.Parse(mobileJson))
        {
            foreach (var marke in doc.RootElement.GetProperty("marken").EnumerateArray())
            {
                string name = marke.GetProperty("name").GetString() ?? "";
                if (name.Length == 0 || !marke.TryGetProperty("id", out var idEl)) continue;
                var m = new MobileMarke { Name = name, Id = JsonText(idEl) };
                var roh = new SortedSet<string>(StringComparer.Ordinal);
                foreach (var mod in marke.GetProperty("modelle").EnumerateArray())
                {
                    string mn = mod.TryGetProperty("name", out var n) ? n.GetString() ?? "" : "";
                    if (mn.Length == 0 || !mod.TryGetProperty("id", out var mid)) continue;
                    string id = JsonText(mid);
                    string norm = Norm(mn);
                    if (norm.Length > 0 && !m.ModellIndex.ContainsKey(norm))
                    {
                        m.ModellIndex[norm] = id;
                        m.Modelle.Add((norm, id, mn));
                    }
                    string sauber = KlammerZusatz.Replace(mn, " ").Trim();
                    if (sauber.Length > 0 && sauber != mn)
                    {
                        string ns = Norm(sauber);
                        if (ns.Length > 0 && !m.ModellIndex.ContainsKey(ns))
                        {
                            m.ModellIndex[ns] = id;
                            m.Modelle.Add((ns, id, mn));
                        }
                    }
                    if (sauber.Length > 0 && sauber is not ("Andere" or "Sonstige" or "Weitere")) roh.Add(sauber);
                }
                m.ModelleRoh.AddRange(roh);
                k._mobile[Norm(name)] = m;
            }
        }
        using (var doc = JsonDocument.Parse(autoscoutJson))
        {
            foreach (var marke in doc.RootElement.EnumerateArray())
            {
                var m = new AutoScoutMarke
                {
                    MakeId = marke.GetProperty("makeId").GetInt32(),
                    MakeName = marke.GetProperty("makeName").GetString() ?? "",
                };
                if (marke.TryGetProperty("models", out var mods))
                    foreach (var mod in mods.EnumerateArray())
                        m.Modelle.Add(new AutoScoutModell(mod.GetProperty("modelId").GetInt32(), mod.GetProperty("modelName").GetString() ?? ""));
                k._autoscout.Add(m);
            }
        }
        return k;
    }

    private static string JsonText(JsonElement e) => e.ValueKind == JsonValueKind.Number ? e.GetRawText() : e.GetString() ?? "";

    /// <summary>NFKD, ohne Akzente, klein, nur a-z0-9 (mobile_service._normalize).</summary>
    public static string Norm(string? s) => FahrzeugCodes.Norm(s);

    // ---- Marke/Modell trennen ------------------------------------------------

    /// <summary>"Land Rover Range Rover Evoque" -> ("Land Rover", "Range Rover Evoque").
    /// Die laengste bekannte Marke am Anfang gewinnt; bei Lesefehlern ("Bentlev")
    /// ein eindeutiger Treffer mit einem falschen Buchstaben.</summary>
    public (string Marke, string Modell)? TeileMarkeModell(string text)
    {
        var woerter = text.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        if (woerter.Length == 0) return null;
        for (int n = Math.Min(4, woerter.Length); n >= 1; n--)
        {
            string marke = string.Join(" ", woerter.Take(n));
            if (MarkeBekannt(Norm(marke)))
                return (marke, string.Join(" ", woerter.Skip(n)));
        }
        // unscharf: nur ein Wort, nur lange Namen, nur eindeutig
        string erstes = Norm(woerter[0]);
        if (erstes.Length >= 5)
        {
            var kandidaten = _mobile.Keys.Concat(_autoscout.Select(a => Norm(a.MakeName)))
                .Where(k => k.Length >= 5 && FahrzeugCodes.Abstand(k, erstes) == 1).Distinct().ToList();
            if (kandidaten.Count == 1)
            {
                string name = _mobile.TryGetValue(kandidaten[0], out var mm) ? mm.Name
                    : _autoscout.First(a => Norm(a.MakeName) == kandidaten[0]).MakeName;
                return (name, string.Join(" ", woerter.Skip(1)));
            }
        }
        return null;
    }

    private bool MarkeBekannt(string norm) =>
        norm.Length > 0 && (_mobile.ContainsKey(norm) || MobileAliase.ContainsKey(norm)
                            || AutoScoutAliase.ContainsKey(norm) || _autoscout.Any(a => Norm(a.MakeName) == norm));

    // ---- mobile.de -----------------------------------------------------------

    public MobileMarke? MobileMarkeFinden(string? name)
    {
        string n = Norm(name);
        if (n.Length == 0) return null;
        if (_mobile.TryGetValue(n, out var m)) return m;
        if (MobileAliase.TryGetValue(n, out var alias) && _mobile.TryGetValue(alias, out m)) return m;
        // Schreibweisen, die nur AutoScout kennt ("Mercedes" ...): ueber dessen Marke
        if (AutoScoutAliase.TryGetValue(n, out var asName) && _mobile.TryGetValue(Norm(asName), out m)) return m;
        return null;
    }

    private static List<string> ModellKandidaten(string modell)
    {
        var raus = new List<string>();
        string vereinfacht = GenerationPunkt.Replace(modell, "$1");
        foreach (var c in new[] { vereinfacht, modell })
            if (c.Length > 0 && !raus.Contains(c)) raus.Add(c);
        return raus;
    }

    /// <summary>mobile_service._resolve_model + Wiederherstellung generischer
    /// Modelle ("Andere") aus dem Titel + unscharfer Abgleich.</summary>
    public ModellTreffer? MobileModellFinden(MobileMarke marke, string modell, string? titel)
    {
        if (Generisch.IsMatch(modell) || modell.Trim().Length == 0)
        {
            string? ausTitel = AusTitel(marke.ModelleRoh, titel);
            if (ausTitel == null) return null;
            modell = ausTitel;
        }
        string? id = MobileAufloesen(marke, modell);
        if (id != null) return new ModellTreffer(id, ModellName(marke, id) ?? modell, false);

        string ziel = Norm(modell);
        var unscharf = Unscharf(ziel, marke.Modelle.Select(m => m.Norm));
        if (unscharf != null)
        {
            string uid = marke.ModellIndex[unscharf];
            return new ModellTreffer(uid, ModellName(marke, uid) ?? modell, true);
        }
        return null;
    }

    private static string? ModellName(MobileMarke marke, string id) =>
        marke.Modelle.Where(m => m.Id == id).Select(m => KlammerZusatz.Replace(m.Name, " ").Trim()).FirstOrDefault();

    private static string? MobileAufloesen(MobileMarke marke, string modell)
    {
        string markeNorm = Norm(marke.Name);
        foreach (var kandidat in ModellKandidaten(modell))
        {
            string norm = Norm(kandidat);
            if (norm.Length == 0) continue;
            norm = MobileModellAliase.GetValueOrDefault((markeNorm, norm), norm);
            if (marke.ModellIndex.TryGetValue(norm, out var id)) return id;
            // B-03: zuerst <Name>-Klasse, dann ein Katalogname, der mit dem vollen Namen beginnt
            var treffer = marke.Modelle.Select(m => m.Norm).Where(n => n.StartsWith(norm + "klasse", StringComparison.Ordinal))
                .OrderBy(n => n.Length).ToList();
            if (treffer.Count == 0 && norm.Length >= 3)
                treffer = marke.Modelle.Select(m => m.Norm).Where(n => n.StartsWith(norm, StringComparison.Ordinal))
                    .OrderBy(n => n.Length).ToList();
            if (treffer.Count > 0) return marke.ModellIndex[treffer[0]];
            bool mitZiffer = norm.Any(char.IsDigit);
            for (int laenge = norm.Length - 1; laenge > 0; laenge--)
            {
                string praefix = norm[..laenge];
                foreach (var (mn, mid, _) in marke.Modelle)
                    if (mn == praefix || (mitZiffer && mn.StartsWith(praefix + "klasse", StringComparison.Ordinal)))
                        return mid;
            }
            var erstes = Regex.Match(norm, "^[a-z]+");
            if (!erstes.Success) erstes = Regex.Match(norm, @"^\d+");
            if (erstes.Success && marke.ModellIndex.TryGetValue(erstes.Value, out var fid)) return fid;
        }
        return null;
    }

    private static string? AusTitel(IEnumerable<string> modelle, string? titel)
    {
        if (string.IsNullOrWhiteSpace(titel)) return null;
        string t = titel.ToLowerInvariant();
        foreach (var name in modelle.OrderByDescending(n => n.Length))
        {
            string n = name.ToLowerInvariant().Trim();
            if (n.Length == 0) continue;
            if (Regex.IsMatch(t, @"(?<![\w])" + Regex.Escape(n) + @"(?![\w])")) return name;
        }
        return null;
    }

    /// <summary>Genau ein Katalogname mit kleinem Abstand (Lesefehler), sonst null.</summary>
    private static string? Unscharf(string ziel, IEnumerable<string> namen)
    {
        if (ziel.Length < 4) return null;
        int erlaubt = ziel.Length >= 8 ? 2 : 1;
        var beste = namen.Distinct().Select(n => (n, d: FahrzeugCodes.Abstand(ziel, n)))
            .Where(x => x.d <= erlaubt).OrderBy(x => x.d).ToList();
        if (beste.Count == 0) return null;
        if (beste.Count > 1 && beste[1].d == beste[0].d) return null;
        return beste[0].n;
    }

    // ---- AutoScout24 ---------------------------------------------------------

    public AutoScoutMarke? AutoScoutMarkeFinden(string? name)
    {
        string ziel = Norm(name);
        if (ziel.Length == 0) return null;
        var m = _autoscout.FirstOrDefault(a => Norm(a.MakeName) == ziel);
        if (m != null) return m;
        if (AutoScoutAliase.TryGetValue(ziel, out var alias))
        {
            m = _autoscout.FirstOrDefault(a => Norm(a.MakeName) == Norm(alias));
            if (m != null) return m;
        }
        return _autoscout.FirstOrDefault(a => Norm(a.MakeName).StartsWith(ziel, StringComparison.Ordinal));
    }

    /// <summary>autoscout_service._find_model (RP-435/RP-447: lieber kein Modell
    /// als ein falsches) + Titel/unscharf wie bei mobile.de.</summary>
    public ModellTreffer? AutoScoutModellFinden(AutoScoutMarke marke, string modell, string? titel)
    {
        if (Generisch.IsMatch(modell) || modell.Trim().Length == 0)
        {
            string? ausTitel = AusTitel(marke.Modelle.Select(m => m.ModelName).Where(n => !Generisch.IsMatch(n)), titel);
            if (ausTitel == null) return null;
            modell = ausTitel;
        }
        var m = AutoScoutAufloesen(marke, modell);
        if (m != null) return new ModellTreffer(m.ModelId.ToString(CultureInfo.InvariantCulture), m.ModelName, false);
        var unscharf = Unscharf(Norm(modell), marke.Modelle.Select(x => Norm(x.ModelName)));
        if (unscharf != null)
        {
            var u = marke.Modelle.First(x => Norm(x.ModelName) == unscharf);
            return new ModellTreffer(u.ModelId.ToString(CultureInfo.InvariantCulture), u.ModelName, true);
        }
        return null;
    }

    private static AutoScoutModell? AutoScoutAufloesen(AutoScoutMarke marke, string modell)
    {
        string ziel = Norm(modell);
        if (ziel.Length == 0) return null;
        var normiert = marke.Modelle.Select(m => (m, n: Norm(m.ModelName))).ToList();
        foreach (var (m, n) in normiert)
            if (n == ziel) return m;
        foreach (var (m, _) in normiert)
        {
            var teile = m.ModelName.Split('/').Select(Norm).ToList();
            if (teile.Count > 1 && teile.Contains(ziel)) return m;
        }
        var grenzen = Wortgrenzen(modell);
        bool AnGrenze(string n)
        {
            if (grenzen.Contains(n.Length)) return true;
            int idx = n.Length - 2;
            if (idx < 0) idx += ziel.Length;                    // Python: negativer Index
            return char.IsLetter(n[^1]) && grenzen.Contains(n.Length - 1)
                   && idx >= 0 && idx < ziel.Length && char.IsLetterOrDigit(ziel[idx]);
        }
        var anfaenge = normiert.Where(x => x.n.Length > 0 && x.n.Length < ziel.Length
                                           && ziel.StartsWith(x.n, StringComparison.Ordinal) && AnGrenze(x.n)).ToList();
        if (anfaenge.Count > 0) return anfaenge.OrderByDescending(x => x.n.Length).First().m;
        var laenger = normiert.Where(x => x.n.Length > 0 && x.n.StartsWith(ziel, StringComparison.Ordinal)).ToList();
        var anWortgrenze = laenger.Where(x => Wortgrenzen(x.m.ModelName).Contains(ziel.Length)).Select(x => x.m).ToList();
        if (anWortgrenze.Count == 1) return anWortgrenze[0];
        if (laenger.Count == 1) return laenger[0].m;
        if (laenger.Count > 0) return null;
        var enthalten = normiert.Where(x => x.n.Contains(ziel, StringComparison.Ordinal)).ToList();
        return enthalten.Count == 1 ? enthalten[0].m : null;
    }

    /// <summary>autoscout_service._wortgrenzen: Positionen im normalisierten Namen,
    /// an denen ein Wortteil endet (Leer-/Satzzeichen, Wechsel Buchstabe/Ziffer).</summary>
    internal static HashSet<int> Wortgrenzen(string name)
    {
        var grenzen = new HashSet<int>();
        if (string.IsNullOrEmpty(name)) return grenzen;
        var sb = new StringBuilder();
        foreach (char c in name.Normalize(NormalizationForm.FormD))
            if (CharUnicodeInfo.GetUnicodeCategory(c) != UnicodeCategory.NonSpacingMark) sb.Append(c);
        string text = sb.ToString().ToLowerInvariant();
        int n = 0;
        char? vorher = null;
        foreach (char ch in text)
        {
            if (ch is >= 'a' and <= 'z' or >= '0' and <= '9')
            {
                char art = char.IsDigit(ch) ? 'z' : 'b';
                if (vorher != null && art != vorher && n > 0) grenzen.Add(n);
                n++;
                vorher = art;
            }
            else
            {
                if (n > 0) grenzen.Add(n);
                vorher = null;
            }
        }
        if (n > 0) grenzen.Add(n);
        return grenzen;
    }

    /// <summary>URL-Slug fuer /lst/&lt;slug&gt; (autoscout_service._slug).</summary>
    public static string Slug(string s)
    {
        var sb = new StringBuilder();
        foreach (char c in s.Normalize(NormalizationForm.FormD))
            if (CharUnicodeInfo.GetUnicodeCategory(c) != UnicodeCategory.NonSpacingMark) sb.Append(c);
        return Regex.Replace(sb.ToString().ToLowerInvariant(), "[^a-z0-9]+", "-").Trim('-');
    }
}
