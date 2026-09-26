import { useState } from "react";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";
import { MessageSquare, Plus, X } from "lucide-react";
import { toast } from "sonner";

/**
 * Rückfrage an den Fahrer (Entscheidung Ahmad 26.09.2026, Nr. 3 der offenen
 * Entscheidungen): strukturierte Frage statt nur „Zurück an den Fahrer“ mit
 * Notiz. Der Chef tippt die Frage, wählt den Bezug (allgemein, ein neuer
 * Schaden des Protokolls oder eine KI-Position) und die Antwortart —
 * Antwortmöglichkeiten als Knöpfe (2–6, frei eintippbar, Vorschlag
 * Ja/Nein/Unklar) oder Freitext. Gesendet wird über die bestehende Route
 * POST /protocols/{id}/freigabe mit FreigabeIn.rueckfrage_frage
 * ({ source_id, question, options, freitext }); den Bezugstext (source_label)
 * und die frage_id setzt der Server.
 */
export const FRAGE_MAX = 300;
export const OPTION_MAX = 60;
export const OPTIONEN_MIN = 2;
export const OPTIONEN_MAX = 6;
export const OPTIONEN_VORSCHLAG = ["Ja", "Nein", "Unklar"];

/** Aus den Eingaben den Körper für FreigabeIn.rueckfrage_frage — oder ein Fehlertext. */
export function rueckfrageKoerper({ frage, bezug, antwortart, optionen }) {
  const question = String(frage || "").trim();
  if (!question) return { fehler: "Bitte die Frage an den Fahrer eintippen." };
  if (question.length > FRAGE_MAX) return { fehler: `Die Frage darf höchstens ${FRAGE_MAX} Zeichen haben.` };
  const freitext = antwortart === "freitext";
  const sauber = [];
  for (const o of optionen || []) {
    const t = String(o ?? "").trim().slice(0, OPTION_MAX);
    if (t && !sauber.includes(t)) sauber.push(t);
  }
  if (!freitext && sauber.length < OPTIONEN_MIN) {
    return { fehler: "Bitte mindestens zwei Antwortmöglichkeiten angeben — oder „Freitext“ wählen." };
  }
  if (!freitext && sauber.length > OPTIONEN_MAX) {
    return { fehler: `Höchstens ${OPTIONEN_MAX} Antwortmöglichkeiten.` };
  }
  return { koerper: { source_id: String(bezug || ""), question, options: freitext ? [] : sauber, freitext } };
}

const feld = {
  background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)",
};

/**
 * @param {object} p
 * @param {boolean} p.open
 * @param {(k: object) => (boolean|void|Promise<boolean|void>)} p.onSenden  bekommt den Körper; false = abgelehnt
 * @param {() => void} p.onClose
 * @param {{id: string, label: string}[]} p.bezuege  neue Schäden und KI-Positionen
 * @param {boolean} p.busy
 */
export default function RueckfrageDialog({ open, onClose, onSenden, bezuege = [], busy = false }) {
  const [frage, setFrage] = useState("");
  const [bezug, setBezug] = useState("");
  const [antwortart, setAntwortart] = useState("optionen");
  const [optionen, setOptionen] = useState(OPTIONEN_VORSCHLAG);
  const [neueOption, setNeueOption] = useState("");
  const [arbeitet, setArbeitet] = useState(false);
  const dialogRef = useModal(() => { if (!arbeitet) onClose?.(); }, { offen: Boolean(open) });
  if (!open) return null;

  const optionHinzu = () => {
    const t = neueOption.trim().slice(0, OPTION_MAX);
    if (!t) return;
    if (optionen.includes(t)) { setNeueOption(""); return; }
    if (optionen.length >= OPTIONEN_MAX) {
      toast.error(`Höchstens ${OPTIONEN_MAX} Antwortmöglichkeiten.`);
      return;
    }
    setOptionen((o) => [...o, t]);
    setNeueOption("");
  };
  const optionWeg = (t) => setOptionen((o) => o.filter((x) => x !== t));

  const senden = async () => {
    const { koerper, fehler } = rueckfrageKoerper({ frage, bezug, antwortart, optionen });
    if (fehler) { toast.error(fehler); return; }
    setArbeitet(true);
    try {
      // onSenden liefert false, wenn der Server abgelehnt hat — dann bleibt
      // der Dialog mit den Eingaben offen.
      const ok = await onSenden?.(koerper);
      if (ok !== false) onClose?.();
    } finally {
      setArbeitet(false);
    }
  };

  const gesperrt = arbeitet || busy;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         data-testid="rueckfrage-dialog">
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="rueckfrage-titel"
           className="w-full max-w-lg rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", maxHeight: "90vh" }}>
        <div className="flex items-center justify-between px-5 py-4"
             style={{ borderBottom: "1px solid var(--border-default)" }}>
          <div className="flex items-center gap-2 font-semibold" style={{ color: "var(--text-primary)" }}
               id="rueckfrage-titel">
            <MessageSquare size={18} /> Rückfrage an den Fahrer
          </div>
          <button type="button" onClick={() => onClose?.()} data-testid="rueckfrage-schliessen"
                  className="w-11 h-11 -mr-1 rounded-full flex items-center justify-center hover:bg-white/10"
                  style={{ color: "var(--text-secondary)" }} title="Schließen" aria-label="Schließen">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 overflow-y-auto flex flex-col gap-3">
          <div className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            Das Protokoll geht an den Fahrer zurück. Er beantwortet die Frage in der App und schickt das
            Protokoll erneut zur Freigabe — die Antwort steht dann hier und geht in die KI-Einschätzung ein.
          </div>

          <label className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>Frage (Pflicht, max. {FRAGE_MAX} Zeichen)</span>
            <textarea value={frage} maxLength={FRAGE_MAX} rows={2} data-testid="rueckfrage-frage" data-autofocus=""
                      onChange={(e) => setFrage(e.target.value)}
                      placeholder="z. B. Geht die Delle bis aufs Blech?"
                      className="px-3 py-2 rounded-xl outline-none" style={feld} />
            <span className="text-[11px] text-right" style={{ color: "var(--text-dim)" }}>{frage.length}/{FRAGE_MAX}</span>
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>Bezug</span>
            <select value={bezug} onChange={(e) => setBezug(e.target.value)} data-testid="rueckfrage-bezug"
                    className="px-3 py-2 rounded-xl outline-none appearance-none" style={feld}>
              <option value="">Allgemein (kein bestimmter Punkt)</option>
              {bezuege.map((b) => (
                <option key={b.id} value={b.id}>{b.label}</option>
              ))}
            </select>
          </label>

          <div className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>Antwortart</span>
            <div className="flex flex-wrap gap-2">
              {[["optionen", "Antwortmöglichkeiten (Knöpfe)"], ["freitext", "Freitext"]].map(([wert, text]) => {
                const aktiv = antwortart === wert;
                return (
                  <button key={wert} type="button" onClick={() => setAntwortart(wert)}
                          data-testid={`rueckfrage-art-${wert}`} aria-pressed={aktiv}
                          className="min-h-[40px] px-3 rounded-xl text-sm font-semibold border"
                          style={aktiv
                            ? { background: "var(--accent-red)", color: "#fff", borderColor: "var(--accent-red)" }
                            : { borderColor: "var(--border-default)", color: "var(--text-primary)", background: "var(--wa-03)" }}>
                    {text}
                  </button>
                );
              })}
            </div>
          </div>

          {antwortart === "optionen" ? (
            <div className="flex flex-col gap-2" data-testid="rueckfrage-optionen">
              <div className="flex flex-wrap gap-2">
                {optionen.map((o) => (
                  <span key={o} data-testid={`rueckfrage-chip-${o}`}
                        className="inline-flex items-center gap-1 rounded-full pl-3 pr-1 py-1 text-sm"
                        style={{ background: "var(--wa-06)", color: "var(--text-primary)", border: "1px solid var(--border-default)" }}>
                    {o}
                    <button type="button" onClick={() => optionWeg(o)} aria-label={`${o} entfernen`}
                            data-testid={`rueckfrage-chip-entfernen-${o}`}
                            className="w-7 h-7 rounded-full flex items-center justify-center hover:bg-white/10"
                            style={{ color: "var(--text-secondary)" }}>
                      <X size={12} />
                    </button>
                  </span>
                ))}
                {optionen.length === 0 && (
                  <span className="text-xs" style={{ color: "var(--text-dim)" }}>noch keine Antwortmöglichkeit</span>
                )}
              </div>
              <div className="flex gap-2">
                <input value={neueOption} maxLength={OPTION_MAX} data-testid="rueckfrage-option-neu"
                       onChange={(e) => setNeueOption(e.target.value)}
                       onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); optionHinzu(); } }}
                       placeholder="Antwortmöglichkeit eintippen"
                       className="flex-1 min-w-0 px-3 py-2 rounded-xl outline-none" style={feld} />
                <button type="button" onClick={optionHinzu} data-testid="rueckfrage-option-hinzu"
                        className="inline-flex items-center gap-1 px-3 py-2 rounded-xl text-sm"
                        style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
                  <Plus size={14} /> Hinzufügen
                </button>
              </div>
              <div className="text-[11px]" style={{ color: "var(--text-dim)" }}>
                {OPTIONEN_MIN} bis {OPTIONEN_MAX} Möglichkeiten — der Fahrer tippt genau eine an.
              </div>
            </div>
          ) : (
            <div className="text-xs" style={{ color: "var(--text-secondary)" }} data-testid="rueckfrage-freitext-hinweis">
              Der Fahrer antwortet mit eigenem Text (bis 300 Zeichen).
            </div>
          )}
        </div>

        <div className="px-5 py-4 flex justify-end gap-2" style={{ borderTop: "1px solid var(--border-default)" }}>
          <button type="button" onClick={() => onClose?.()} data-testid="rueckfrage-abbrechen"
                  className="px-4 py-2 rounded-xl"
                  style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
            Abbrechen
          </button>
          <button type="button" onClick={senden} disabled={gesperrt} data-testid="rueckfrage-senden"
                  className="px-4 py-2 rounded-xl disabled:opacity-50"
                  style={{ background: "var(--accent-blue)", color: "#fff" }}>
            {arbeitet ? "Wird gesendet …" : "Rückfrage senden"}
          </button>
        </div>
      </div>
    </div>
  );
}
