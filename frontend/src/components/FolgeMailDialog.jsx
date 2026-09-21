import { useEffect, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import KopierKnopf from "@/components/KopierKnopf";
import { Mail, Send, X } from "lucide-react";
import { toast } from "sonner";

/**
 * Vorlagen beim Kaufvertrag (Wunsch Ahmad 21.09.2026): kopieren ODER per
 * E-Mail verschicken ("doch zum Verschicken kann bleiben"). Nie automatisch.
 *
 *   nach_kauf           Hinweis nach Kaufabschluss (E-Mail)
 *   nach_kauf_whatsapp  Hinweis nach Kaufabschluss (WhatsApp)
 *   bahn                Bahnverbindung
 *
 * Betreff und Text kommen aus den Einstellungen der Firma, der Server setzt
 * Name und Daten dieses Vertrags schon ein. Vor dem Kopieren lässt sich
 * beides hier noch ändern.
 *
 * Die Korrektur geht über den normalen Versand — mit dem korrigierten
 * Vertrag als Anhang (SendDialog), nicht als Textmail von hier.
 */
const ARTEN = [
  { id: "nach_kauf", label: "Hinweis nach Kaufabschluss (E-Mail)", mail: true,
    hilfe: "Inserat rausnehmen, keine Auskünfte, Abholung." },
  { id: "nach_kauf_whatsapp", label: "Hinweis nach Kaufabschluss (WhatsApp)", mail: false,
    hilfe: "Derselbe Hinweis für WhatsApp — zum Kopieren, ohne Betreff." },
  { id: "bahn", label: "Bahnverbindung", mail: true,
    hilfe: "Ankunftszeit des Fahrers ankündigen." },
];

// Rollenpruefung 21.09.2026: crypto.randomUUID gibt es nur auf https bzw.
// localhost — im Firmennetz ueber http warf der Knopf einen TypeError.
function neuerSchluessel() {
  let roh = "";
  try { roh = globalThis.crypto?.randomUUID?.() || ""; } catch { roh = ""; }
  if (!roh) roh = `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return `fm-${roh.replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40)}`;
}

export default function FolgeMailDialog({ open, contract, onClose }) {
  const [art, setArt] = useState("nach_kauf");
  const [empfaenger, setEmpfaenger] = useState("");
  const [betreff, setBetreff] = useState("");
  const [text, setText] = useState("");
  const [laedt, setLaedt] = useState(false);
  const [sendet, setSendet] = useState(false);
  const [gesendet, setGesendet] = useState(false);
  // Doppelklick-/Wiederholungsschutz: EIN Schluessel je Dialog und Vorlage
  // (nicht je Klick) — ein zweiter Klick nach Netzabbruch stellt nicht
  // doppelt zu. Nach einem Erfolg gibt es einen neuen Schluessel.
  const schluessel = useRef(neuerSchluessel());

  // Vorlage vom Server holen: er setzt Name und Daten des Vertrags ein.
  // Beim Wechsel und nach einem Fehler werden die Felder geleert — sonst
  // liesse sich waehrenddessen noch der Text der VORIGEN Vorlage kopieren.
  useEffect(() => {
    if (!open || !contract?.id) return;
    let abgebrochen = false;
    setLaedt(true);
    setBetreff("");
    setText("");
    schluessel.current = neuerSchluessel();
    api.get(`/contracts/${contract.id}/folge-mail/${art}`)
      .then(({ data }) => {
        if (abgebrochen) return;
        setEmpfaenger((alt) => alt || data.empfaenger || contract.seller_email || "");
        setBetreff(data.betreff || "");
        setText(data.text || "");
      })
      .catch((e) => {
        if (abgebrochen) return;
        setBetreff("");
        setText("");
        toast.error(errMsg(e));
      })
      .finally(() => !abgebrochen && setLaedt(false));
    return () => { abgebrochen = true; };
  }, [open, contract?.id, art]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;
  const mitBetreff = art !== "nach_kauf_whatsapp";
  const perMail = !!ARTEN.find((a) => a.id === art)?.mail;

  const senden = async () => {
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(empfaenger.trim())) {
      toast.error("Bitte eine gültige E-Mail-Adresse angeben.");
      return;
    }
    if (!text.trim()) {
      toast.error("Der Text ist leer.");
      return;
    }
    setSendet(true);
    try {
      const { data } = await api.post(`/contracts/${contract.id}/folge-mail`, {
        art, recipient: empfaenger.trim(), subject: betreff, message: text,
        idempotency_key: schluessel.current,
      });
      toast.success(data?.bereits_gesendet
        ? "Diese Mail wurde bereits verschickt."
        : "Mail verschickt.");
      setGesendet(true);
      schluessel.current = neuerSchluessel();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setSendet(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         data-testid="folgemail-dialog">
      {/* 21.09.2026 (Bildschirmfoto Ahmad): nur Farben aus index.css — die
          frueher hier benutzten Farbnamen gab es nicht, das Fenster war
          durchsichtig und der Text lief ueber die Vertragsliste
          (Waechter: backend/tests/test_farb_tokens_20260921.py). */}
      <div className="w-full max-w-2xl rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)",
                    maxHeight: "90vh" }}>
        <div className="flex items-center justify-between px-5 py-4"
             style={{ borderBottom: "1px solid var(--border-default)" }}>
          <div className="flex items-center gap-2 font-semibold"
               style={{ color: "var(--text-primary)" }}>
            <Mail size={18} /> Hinweis &amp; Bahnverbindung
          </div>
          <button onClick={() => onClose?.(gesendet)} data-testid="folgemail-schliessen"
                  className="w-9 h-9 rounded-full flex items-center justify-center hover:bg-white/10"
                  style={{ color: "var(--text-secondary)" }} title="Schließen">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 overflow-y-auto flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            {ARTEN.map((a) => (
              <button key={a.id} onClick={() => setArt(a.id)}
                      data-testid={`folgemail-art-${a.id}`}
                      className="px-3 py-2 rounded-xl text-sm transition-colors"
                      style={{
                        background: art === a.id
                          ? "var(--accent-blue)" : "var(--apple-btn-secondary-bg)",
                        color: art === a.id ? "#fff" : "var(--text-primary)",
                      }}
                      title={a.hilfe}>
                {a.label}
              </button>
            ))}
          </div>
          <div className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}
               data-testid="folgemail-hinweis">
            {ARTEN.find((a) => a.id === art)?.hilfe}{" "}
            {perMail
              ? "Per E-Mail verschicken (mit deinem Firmennamen, Antworten gehen an dich) — oder kopieren und selbst senden."
              : "Kopieren und in WhatsApp einfügen."}
          </div>

          {perMail && (
            <label className="flex flex-col gap-1">
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                Empfänger
              </span>
              <input value={empfaenger} onChange={(e) => setEmpfaenger(e.target.value)}
                     data-testid="folgemail-empfaenger" type="email"
                     className="px-3 py-2 rounded-xl outline-none"
                     style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)",
                              border: "1px solid var(--wa-12)" }} />
            </label>
          )}

          {mitBetreff && (
            <label className="flex flex-col gap-1">
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
                Betreff
              </span>
              <input value={betreff} onChange={(e) => setBetreff(e.target.value)}
                     data-testid="folgemail-betreff"
                     className="px-3 py-2 rounded-xl outline-none"
                     style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)",
                              border: "1px solid var(--wa-12)" }} />
            </label>
          )}

          <label className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              Text {laedt && "(wird geladen …)"}
            </span>
            <textarea value={text} onChange={(e) => setText(e.target.value)}
                      rows={14} data-testid="folgemail-text"
                      className="px-3 py-2 rounded-xl outline-none resize-y"
                      style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)",
                               border: "1px solid var(--wa-12)" }} />
          </label>
        </div>

        <div className="px-5 py-4 flex flex-wrap justify-end gap-2"
             style={{ borderTop: "1px solid var(--border-default)" }}>
          {mitBetreff && (
            <KopierKnopf text={betreff} label="Betreff" disabled={laedt}
                         testid="folgemail-betreff-kopieren" />
          )}
          <KopierKnopf text={text} label="Text" disabled={laedt}
                       testid="folgemail-text-kopieren" />
          {perMail ? (
            <button onClick={senden} disabled={sendet || laedt}
                    data-testid="folgemail-senden"
                    className="inline-flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold disabled:opacity-50"
                    style={{ background: "var(--accent-blue)", color: "#fff" }}>
              <Send size={13} /> {sendet ? "Wird verschickt …" : "Per E-Mail verschicken"}
            </button>
          ) : (
            <button onClick={() => onClose?.(gesendet)} className="px-4 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: "var(--accent-blue)", color: "#fff" }}>
              Fertig
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
