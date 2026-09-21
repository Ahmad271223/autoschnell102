import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import KopierKnopf from "@/components/KopierKnopf";
import { Copy, X } from "lucide-react";
import { toast } from "sonner";

/**
 * Vorlagen zum Kopieren beim Kaufvertrag (Wunsch Ahmad 21.09.2026).
 *
 * "Wir selber schicken die nicht raus — diese Vorlagen sollen da nur sein,
 * damit der Kunde sie immer kopieren kann und bei Mail selber einfügen
 * kann." Die App verschickt hier NICHTS: der Sucher kopiert Betreff und
 * Text und schickt sie aus seinem eigenen Postfach oder WhatsApp.
 *
 *   nach_kauf           Hinweis nach Kaufabschluss (E-Mail)
 *   nach_kauf_whatsapp  Hinweis nach Kaufabschluss (WhatsApp)
 *   bahn                Bahnverbindung
 *
 * Betreff und Text kommen aus den Einstellungen der Firma, der Server setzt
 * Name und Daten dieses Vertrags schon ein. Vor dem Kopieren lässt sich
 * beides hier noch ändern.
 *
 * Bis 21.09.2026 lief hier auch der Versand (samt "Korrektur") über unsere
 * Adresse. Die Korrektur geht jetzt über den normalen Versand — mit dem
 * korrigierten Vertrag als Anhang (SendDialog).
 */
const ARTEN = [
  { id: "nach_kauf", label: "Hinweis nach Kaufabschluss (E-Mail)",
    hilfe: "Inserat rausnehmen, keine Auskünfte, Abholung — für deine eigene E-Mail." },
  { id: "nach_kauf_whatsapp", label: "Hinweis nach Kaufabschluss (WhatsApp)",
    hilfe: "Derselbe Hinweis für WhatsApp — ohne Betreff." },
  { id: "bahn", label: "Bahnverbindung",
    hilfe: "Ankunftszeit des Fahrers ankündigen — die Bahnverbindung selbst hängst du an." },
];

export default function FolgeMailDialog({ open, contract, onClose }) {
  const [art, setArt] = useState("nach_kauf");
  const [betreff, setBetreff] = useState("");
  const [text, setText] = useState("");
  const [laedt, setLaedt] = useState(false);

  // Vorlage vom Server holen: er setzt Name und Daten des Vertrags ein.
  // Beim Wechsel und nach einem Fehler werden die Felder geleert — sonst
  // liesse sich waehrenddessen noch der Text der VORIGEN Vorlage kopieren.
  useEffect(() => {
    if (!open || !contract?.id) return;
    let abgebrochen = false;
    setLaedt(true);
    setBetreff("");
    setText("");
    api.get(`/contracts/${contract.id}/folge-mail/${art}`)
      .then(({ data }) => {
        if (abgebrochen) return;
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
  }, [open, contract?.id, art]);

  if (!open) return null;
  const mitBetreff = art !== "nach_kauf_whatsapp";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: "rgba(0,0,0,.55)" }} data-testid="folgemail-dialog">
      <div className="w-full max-w-2xl rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--surface-1)", maxHeight: "90vh" }}>
        <div className="flex items-center justify-between px-5 py-4"
             style={{ borderBottom: "1px solid var(--line)" }}>
          <div className="flex items-center gap-2 font-semibold"
               style={{ color: "var(--text-primary)" }}>
            <Copy size={18} /> Vorlagen zum Kopieren
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
          <div className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}
               data-testid="folgemail-hinweis">
            {ARTEN.find((a) => a.id === art)?.hilfe} Die App verschickt diese Nachricht
            nicht — kopiere sie und sende sie selbst.
          </div>

          {mitBetreff && (
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
          )}

          <label className="flex flex-col gap-1">
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              Text {laedt && "(wird geladen …)"}
            </span>
            <textarea value={text} onChange={(e) => setText(e.target.value)}
                      rows={14} data-testid="folgemail-text"
                      className="px-3 py-2 rounded-xl outline-none resize-y"
                      style={{ background: "var(--surface-2)", color: "var(--text-primary)",
                               border: "1px solid var(--line)" }} />
          </label>
        </div>

        <div className="px-5 py-4 flex flex-wrap justify-end gap-2"
             style={{ borderTop: "1px solid var(--line)" }}>
          {mitBetreff && (
            <KopierKnopf text={betreff} label="Betreff" disabled={laedt}
                         testid="folgemail-betreff-kopieren" />
          )}
          <KopierKnopf text={text} label="Text" disabled={laedt}
                       testid="folgemail-text-kopieren" />
          <button onClick={onClose} className="px-4 py-1.5 rounded-lg text-xs font-semibold"
                  style={{ background: "var(--apple-btn-primary-bg)", color: "#fff" }}>
            Fertig
          </button>
        </div>
      </div>
    </div>
  );
}
