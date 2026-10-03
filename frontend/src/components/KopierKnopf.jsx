import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { textKopieren } from "@/lib/kopieren";

/**
 * Knopf "Kopieren" für Vorlagen, die die App nicht verschickt (Wunsch Ahmad
 * 21.09.2026) — in den Einstellungen und im PDF-Archiv beim Vertrag.
 * Kopiert den Text so, wie er gerade im Feld steht (auch ungespeichert).
 */
export default function KopierKnopf({ text, label = "", testid, className = "", disabled = false }) {
  const [kopiert, setKopiert] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const klick = async () => {
    if (!String(text || "").trim()) {
      toast.error("Das Feld ist leer — nichts zu kopieren.");
      return;
    }
    if (await textKopieren(text)) {
      setKopiert(true);
      toast.success(`${label || "Text"} kopiert — jetzt in deine E-Mail oder WhatsApp einfügen.`);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setKopiert(false), 2000);
    } else {
      toast.error("Kopieren hat nicht geklappt — bitte den Text markieren und von Hand kopieren.");
    }
  };

  return (
    <button type="button" onClick={klick} data-testid={testid} disabled={disabled}
            className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-semibold normal-case tracking-normal transition-colors hover:opacity-90 disabled:opacity-50 ${className}`}
            style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
      {kopiert ? <Check size={12} /> : <Copy size={12} />}
      {kopiert ? "Kopiert" : (label ? `${label} kopieren` : "Kopieren")}
    </button>
  );
}
