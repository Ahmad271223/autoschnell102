import { useEffect, useRef, useState } from "react";
import { Loader2, Mic, Square } from "lucide-react";
import { toast } from "sonner";
import { diktatAufruf, diktatVerfuegbar } from "@/lib/sprachdiktat";

/**
 * Mikrofon-Knopf zum Diktieren (nur iPhone-App, Wunsch Ahmad 24.11.2026).
 * Erste Nutzung: einmalig Sprachmodell laden (Rückfrage mit Größe), danach
 * offline. Tippen = Aufnahme starten, nochmal tippen = Text wird erkannt und
 * über onText(text) übergeben — der Aufrufer hängt ihn ans Feld an.
 * Außerhalb der iPhone-App wird nichts angezeigt.
 */
export default function DiktatKnopf({ onText, disabled = false, testid = "diktat-knopf", className = "" }) {
  const [sichtbar, setSichtbar] = useState(false);
  // frei | laedt | aufnahme | erkennt
  const [zustand, setZustand] = useState("frei");
  const [prozent, setProzent] = useState(0);
  const [sekunden, setSekunden] = useState(0);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const zustandRef = useRef(zustand);
  zustandRef.current = zustand;
  const timer = useRef(null);
  const lebt = useRef(true);

  useEffect(() => {
    lebt.current = true;
    diktatVerfuegbar().then((ja) => { if (lebt.current) setSichtbar(ja); });
    return () => {
      lebt.current = false;
      window.clearInterval(timer.current);
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

  const starten = async () => {
    try {
      if (!(await vorbereiten())) { setZustand("frei"); return; }
      await diktatAufruf("aufnahmeStarten");
      if (!lebt.current) { diktatAufruf("abbrechen").catch(() => {}); return; }
      setSekunden(0);
      setZustand("aufnahme");
      window.clearInterval(timer.current);
      timer.current = window.setInterval(() => setSekunden((x) => x + 1), 1000);
    } catch (e) {
      if (lebt.current) setZustand("frei");
      toast.error(e?.message || "Diktat konnte nicht gestartet werden.");
    }
  };

  const stoppen = async () => {
    window.clearInterval(timer.current);
    setZustand("erkennt");
    try {
      const { text } = await diktatAufruf("aufnahmeStoppen");
      if (!lebt.current) return;
      if (String(text || "").trim()) onTextRef.current?.(text);
      else toast.info("Nichts verstanden — bitte näher am Mikrofon noch einmal sprechen.");
    } catch (e) {
      toast.error(e?.message || "Diktat fehlgeschlagen.");
    } finally {
      if (lebt.current) setZustand("frei");
    }
  };

  const klick = () => {
    if (zustand === "frei") starten();
    else if (zustand === "aufnahme") stoppen();
  };

  const beschriftung = {
    frei: "Diktieren",
    laedt: prozent >= 90 ? "Wird vorbereitet…" : `Lädt ${prozent} %`,
    aufnahme: `Stopp ${Math.floor(sekunden / 60)}:${String(sekunden % 60).padStart(2, "0")}`,
    erkennt: "Wird erkannt…",
  }[zustand];

  const aktiv = zustand === "aufnahme";
  return (
    <button type="button" onClick={klick} data-testid={testid}
            disabled={disabled || zustand === "laedt" || zustand === "erkennt"}
            aria-label={aktiv ? "Diktat beenden" : "Diktieren"}
            className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-colors hover:opacity-90 disabled:opacity-60 ${className}`}
            style={aktiv
              ? { background: "var(--accent-red, #FF3B30)", color: "#fff" }
              : { background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
      {zustand === "laedt" || zustand === "erkennt"
        ? <Loader2 size={12} className="animate-spin" />
        : aktiv ? <Square size={12} /> : <Mic size={12} />}
      {beschriftung}
    </button>
  );
}
