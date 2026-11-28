import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { diktatAufruf, diktatVerfuegbar, textAnhaengen } from "@/lib/sprachdiktat";
import { nachbearbeiten, zahlAusSprache, zahlText } from "@/lib/diktatRegeln";

/** Nach so viel Stille (nachdem schon gesprochen wurde) hört die Aufnahme von selbst auf. */
export const AUTO_STOPP_MS = 2000;
/** So lange steht "Rückgängig" neben dem Knopf. */
export const RUECKGAENGIG_MS = 8000;
const ABFRAGE_MS = 200;

const EINHEIT = { km: "km", preis: "€" };

/**
 * Mikrofon-Knopf zum Diktieren (nur iPhone-App, Wunsch Ahmad 24.11.2026).
 * Erste Nutzung: einmalig Sprachmodell laden (Rückfrage mit Größe), danach offline.
 * Tippen = Aufnahme; sie endet nach 2 s Stille von selbst (oder nochmal tippen).
 * Während der Aufnahme zeigt ein Balken, ob das Mikrofon etwas hört.
 *
 * Drei Arten:
 *   wert + onChange (+ max, neueZeile, einzeilig): Text wird ans Feld angehängt, danach 8 s "Rückgängig".
 *   zahl="km" | "preis" + onZahl(n):    gesprochene Zahl, erst nach Bestätigung übernommen.
 *   onText(text):                       nur den erkannten Text liefern (z. B. Schäden ansagen).
 * Sprachbefehle ("Komma", "neue Zeile") und Fachwörter (TÜV, AHK …) sind schon umgesetzt.
 * Außerhalb der iPhone-App wird nichts angezeigt.
 */
export default function DiktatKnopf({
  wert, onChange, max, neueZeile = false, einzeilig = false, zahl, onZahl, onText,
  disabled = false, testid = "diktat-knopf", className = "", beschriftung: frei = "Diktieren",
}) {
  const [sichtbar, setSichtbar] = useState(false);
  // frei | laedt | aufnahme | erkennt
  const [zustand, setZustand] = useState("frei");
  const [prozent, setProzent] = useState(0);
  const [sekunden, setSekunden] = useState(0);
  const [pegel, setPegel] = useState(0);
  const [rueck, setRueck] = useState(null);   // { vorher, nachher }
  const props = useRef({});
  props.current = { wert, onChange, max, neueZeile, einzeilig, zahl, onZahl, onText };
  const zustandRef = useRef(zustand);
  zustandRef.current = zustand;
  const uhr = useRef(null);
  const rueckUhr = useRef(null);
  const start = useRef(0);
  // null = App meldet es nicht (älterer Stand); false = bisher nur Stille
  const gesprochen = useRef(null);
  const lebt = useRef(true);

  useEffect(() => {
    lebt.current = true;
    diktatVerfuegbar().then((ja) => { if (lebt.current) setSichtbar(ja); });
    return () => {
      lebt.current = false;
      window.clearInterval(uhr.current);
      window.clearTimeout(rueckUhr.current);
      if (zustandRef.current === "aufnahme") diktatAufruf("abbrechen").catch(() => {});
    };
  }, []);

  if (!sichtbar) return null;

  const warteBisBereit = async () => {
    for (;;) {
      const s = await diktatAufruf("status");
      if (!lebt.current) return false;
      if (s.phase === "bereit") return true;
      if (s.phase === "fehler") throw new Error(s.fehler || "Sprachmodell konnte nicht geladen werden.");
      setProzent(Math.round((s.fortschritt || 0) * 100));
      await new Promise((r) => setTimeout(r, 1000));
    }
  };

  const vorbereiten = async () => {
    const s = await diktatAufruf("status");
    if (s.phase === "bereit") return true;
    if (!s.heruntergeladen && s.phase !== "laedt") {
      const ok = window.confirm(
        `Für das Diktat lädt die App einmalig ein Sprachmodell (ca. ${s.groesseMB} MB) auf dein iPhone. `
        + "Am besten im WLAN. Danach funktioniert das Diktat auch ohne Internet, "
        + "und deine Sprache verlässt das iPhone nie. Jetzt laden?",
      );
      if (!ok) return false;
    }
    setZustand("laedt");
    setProzent(0);
    await diktatAufruf("vorbereiten");
    return warteBisBereit();
  };

  // Während der Aufnahme: Sekunden, Pegel und Auto-Stopp (ältere App-Stände liefern keinen Pegel —
  // dann bleibt es beim Tippen zum Beenden).
  const abfragen = async () => {
    if (zustandRef.current !== "aufnahme") return;
    setSekunden(Math.floor((Date.now() - start.current) / 1000));
    try {
      const s = await diktatAufruf("status");
      if (!lebt.current || zustandRef.current !== "aufnahme") return;
      if (typeof s.pegel === "number") setPegel(s.pegel);
      if (typeof s.gesprochen === "boolean") gesprochen.current = s.gesprochen;
      if (s.aufnahme === false) { stoppen(); return; }          // 5-Minuten-Grenze der App
      if (s.gesprochen && typeof s.stille_ms === "number" && s.stille_ms >= AUTO_STOPP_MS) stoppen();
    } catch { /* nächste Runde */ }
  };

  const starten = async () => {
    try {
      window.clearTimeout(rueckUhr.current);
      setRueck(null);
      if (!(await vorbereiten())) { setZustand("frei"); return; }
      await diktatAufruf("aufnahmeStarten");
      if (!lebt.current) { diktatAufruf("abbrechen").catch(() => {}); return; }
      start.current = Date.now();
      gesprochen.current = null;
      setSekunden(0);
      setPegel(0);
      zustandRef.current = "aufnahme";
      setZustand("aufnahme");
      window.clearInterval(uhr.current);
      uhr.current = window.setInterval(abfragen, ABFRAGE_MS);
    } catch (e) {
      if (lebt.current) setZustand("frei");
      toast.error(e?.message || "Diktat konnte nicht gestartet werden.");
    }
  };

  const uebernehmen = (roh) => {
    const p = props.current;
    if (p.zahl) {
      const n = zahlAusSprache(roh);
      if (n == null) {
        toast.info(`„${String(roh).trim()}“ — keine Zahl erkannt. Bitte nur die Zahl sagen, z. B. „hundertzwanzigtausend“.`);
        return;
      }
      const einheit = EINHEIT[p.zahl] ? ` ${EINHEIT[p.zahl]}` : "";
      if (window.confirm(`Erkannt: ${zahlText(n)}${einheit} — übernehmen?`)) {
        p.onZahl?.(n);
      }
      return;
    }
    const text = nachbearbeiten(roh);
    if (!text) return;
    if (p.onChange) {
      const vorher = String(p.wert ?? "");
      let nachher = p.neueZeile && vorher.trim()
        ? `${vorher.replace(/\s+$/, "")}\n${text}`.slice(0, p.max || undefined)
        : textAnhaengen(vorher, text, p.max);
      // einzeilige Eingabefelder (input) verschlucken Zeilenumbrüche
      if (p.einzeilig) nachher = nachher.replace(/\s*\n\s*/g, " ");
      p.onChange(nachher);
      setRueck({ vorher, nachher });
      window.clearTimeout(rueckUhr.current);
      rueckUhr.current = window.setTimeout(() => { if (lebt.current) setRueck(null); }, RUECKGAENGIG_MS);
    } else {
      p.onText?.(text);
    }
  };

  const rueckgaengig = () => {
    const p = props.current;
    window.clearTimeout(rueckUhr.current);
    if (!rueck) return;
    setRueck(null);
    if (String(p.wert ?? "") !== rueck.nachher) {
      toast.info("Das Feld wurde inzwischen geändert — bitte von Hand korrigieren.");
      return;
    }
    p.onChange?.(rueck.vorher);
  };

  async function stoppen() {
    if (zustandRef.current !== "aufnahme") return;
    window.clearInterval(uhr.current);
    zustandRef.current = "erkennt";
    setZustand("erkennt");
    try {
      // Aus reiner Stille erfindet Whisper Wörter ("Amen.", Messung 24.11.2026) — dann gar nicht erst erkennen
      if (gesprochen.current === false) {
        await diktatAufruf("abbrechen").catch(() => {});
        toast.info("Nichts gehört — bitte näher am Mikrofon noch einmal sprechen.");
        return;
      }
      const { text } = await diktatAufruf("aufnahmeStoppen");
      if (!lebt.current) return;
      if (String(text || "").trim()) uebernehmen(text);
      else toast.info("Nichts verstanden — bitte näher am Mikrofon noch einmal sprechen.");
    } catch (e) {
      toast.error(e?.message || "Diktat fehlgeschlagen.");
    } finally {
      if (lebt.current) setZustand("frei");
    }
  }

  const klick = () => {
    if (zustand === "frei") starten();
    else if (zustand === "aufnahme") stoppen();
  };

  const text = {
    frei,
    laedt: prozent >= 90 ? "Wird vorbereitet…" : `Lädt ${prozent} %`,
    aufnahme: `Stopp ${Math.floor(sekunden / 60)}:${String(sekunden % 60).padStart(2, "0")}`,
    erkennt: "Wird erkannt…",
  }[zustand];

  const aktiv = zustand === "aufnahme";
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <button type="button" onClick={klick} data-testid={testid}
              disabled={disabled || zustand === "laedt" || zustand === "erkennt"}
              aria-label={aktiv ? "Diktat beenden" : frei}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-colors hover:opacity-90 disabled:opacity-60"
              style={aktiv
                ? { background: "var(--accent-red, #FF3B30)", color: "#fff" }
                : { background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
        {zustand === "laedt" || zustand === "erkennt"
          ? <Loader2 size={12} className="animate-spin" />
          : aktiv ? <Square size={12} /> : <Mic size={12} />}
        {text}
        {aktiv && (
          // Pegel: fünf Striche, damit man sieht, dass das Mikrofon etwas hört
          <span className="inline-flex items-end gap-[2px] h-3" data-testid={`${testid}-pegel`} aria-hidden="true">
            {[0, 1, 2, 3, 4].map((i) => (
              <span key={i} className="w-[3px] rounded-sm"
                    style={{ height: `${4 + i * 2}px`, background: "#fff", opacity: pegel > i / 5 ? 1 : 0.3 }} />
            ))}
          </span>
        )}
      </button>
      {rueck && zustand === "frei" && (
        <button type="button" onClick={rueckgaengig} data-testid={`${testid}-rueckgaengig`}
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-semibold hover:opacity-90"
                style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
          <Undo2 size={12} /> Rückgängig
        </button>
      )}
    </span>
  );
}
