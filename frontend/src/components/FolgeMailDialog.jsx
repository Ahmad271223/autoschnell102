import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { Mail, X } from "lucide-react";
import { toast } from "sonner";

/**
 * Nachträgliche Mails zu einem Kaufvertrag (Wunsch Ahmad 20.09.2026).
 *
 * Drei Vorlagen, die NIE automatisch rausgehen — der Sucher schickt sie von
 * Hand, wenn es passt:
 *
 *   korrektur  erneuter Versand, nachdem am Vertrag etwas geändert wurde
 *   nach_kauf  Hinweis nach dem Kaufabschluss (Inserat rausnehmen usw.)
 *   bahn       Bahnverbindung / Abholinformation für den Verkäufer
 *
 * Betreff und Text kommen aus den Einstellungen der Firma, mit bereits
 * eingesetzten Platzhaltern — der Server liefert sie fertig. Beides lässt
 * sich hier vor dem Senden noch ändern.
 */
const ARTEN = [
  { id: "korrektur", label: "Korrigierte Fassung",
    hilfe: "Nach einer Änderung am Vertrag erneut schicken." },
  { id: "nach_kauf", label: "Hinweis nach Kaufabschluss",
    hilfe: "Inserat rausnehmen, keine Auskünfte, Abholung." },
  { id: "bahn", label: "Bahnverbindung",
    hilfe: "Ankunftszeit des Fahrers ankündigen." },
];

export default function FolgeMailDialog({ open, contract, onClose }) {
  const [art, setArt] = useState("korrektur");
  const [empfaenger, setEmpfaenger] = useState("");
  const [betreff, setBetreff] = useState("");
  const [text, setText] = useState("");
  const [laedt, setLaedt] = useState(false);
  const [sendet, setSendet] = useState(false);

  // Vorschau vom Server holen: er setzt die Platzhalter ein, damit hier
  // genau das steht, was gleich rausgeht.
  useEffect(() => {
    if (!open || !contract?.id) return;
    let abgebrochen = false;
    setLaedt(true);
    api.get(`/contracts/${contract.id}/folge-mail/${art}`)
      .then(({ data }) => {
        if (abgebrochen) return;
        setEmpfaenger(data.empfaenger || contract.seller_email || "");
        setBetreff(data.betreff || "");
        setText(data.text || "");
      })
      .catch((e) => !abgebrochen && toast.error(errMsg(e)))
      .finally(() => !abgebrochen && setLaedt(false));
    return () => { abgebrochen = true; };
  }, [open, contract?.id, art]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  const senden = async () => {
    if (!empfaenger.includes("@")) {
      toast.error("Bitte eine gültige E-Mail-Adresse angeben.");
      return;
    }
    setSendet(true);
    try {
      const { data } = await api.post(`/contracts/${contract.id}/folge-mail`, {
        art, recipient: empfaenger.trim(), subject: betreff, message: text,
        // Doppelklick-Schutz: ein Schlüssel je Klick.
        idempotency_key: `fm-${art}-${crypto.randomUUID().slice(0, 18)}`,
      });
      toast.success(data?.bereits_gesendet
        ? "Diese Mail wurde bereits verschickt."
        : "Mail verschickt.");
      onClose?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setSendet(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: "rgba(0,0,0,.55)" }} data-testid="folgemail-dialog">
      <div className="w-full max-w-2xl rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--surface-1)", maxHeight: "90vh" }}>
        <div className="flex items-center justify-between px-5 py-4"
             style={{ borderBottom: "1px solid var(--line)" }}>
          <div className="flex items-center gap-2 font-semibold"
               style={{ color: "var(--text-primary)" }}>
            <Mail size={18} /> Nachträgliche Mail
          </div>
          <button onClick={onClose} data-testid="folgemail-schliessen"
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
                          ? "var(--apple-btn-primary-bg)" : "var(--apple-btn-secondary-bg)",
                        color: art === a.id ? "#fff" : "var(--text-primary)",
                      }}
                      title={a.hilfe}>
                {a.label}
              </button>
            ))}
          </div>
          <div className="text-xs" style={{ color: "var(--text-secondary)" }}>
            {ARTEN.find((a) => a.id === art)?.hilfe}
          </div>

          <label className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              Empfänger
            </span>
            <input value={empfaenger} onChange={(e) => setEmpfaenger(e.target.value)}
                   data-testid="folgemail-empfaenger" type="email"
                   className="px-3 py-2 rounded-xl outline-none"
                   style={{ background: "var(--surface-2)", color: "var(--text-primary)",
                            border: "1px solid var(--line)" }} />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              Betreff
            </span>
            <input value={betreff} onChange={(e) => setBetreff(e.target.value)}
                   data-testid="folgemail-betreff"
                   className="px-3 py-2 rounded-xl outline-none"
                   style={{ background: "var(--surface-2)", color: "var(--text-primary)",
                            border: "1px solid var(--line)" }} />
          </label>

          <label className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              Text {laedt && "(wird geladen …)"}
            </span>
            <textarea value={text} onChange={(e) => setText(e.target.value)}
                      rows={12} data-testid="folgemail-text"
                      className="px-3 py-2 rounded-xl outline-none resize-y"
                      style={{ background: "var(--surface-2)", color: "var(--text-primary)",
                               border: "1px solid var(--line)" }} />
          </label>
        </div>

        <div className="px-5 py-4 flex justify-end gap-2"
             style={{ borderTop: "1px solid var(--line)" }}>
          <button onClick={onClose} className="px-4 py-2 rounded-xl"
                  style={{ background: "var(--apple-btn-secondary-bg)",
                           color: "var(--text-primary)" }}>
            Abbrechen
          </button>
          <button onClick={senden} disabled={sendet || laedt}
                  data-testid="folgemail-senden"
                  className="px-4 py-2 rounded-xl disabled:opacity-50"
                  style={{ background: "var(--apple-btn-primary-bg)", color: "#fff" }}>
            {sendet ? "Wird verschickt …" : "Verschicken"}
          </button>
        </div>
      </div>
    </div>
  );
}
