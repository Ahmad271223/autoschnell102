import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";
import { UserRoundPen, X } from "lucide-react";
import { toast } from "sonner";

/**
 * Verkäuferdaten eines Kaufvertrags korrigieren (Entscheidung Ahmad
 * 22.09.2026, Rollenprüfung RP-481).
 *
 * Vorher gab es für einen Tippfehler im Namen oder eine falsche Anschrift in
 * einem verschickten Vertrag keinen Weg außer Löschen. Jetzt: der Server
 * erzeugt eine neue Fassung (die alte bleibt im Archiv), der offene
 * Abholtermin bekommt die Daten mit — und danach schickt man den Vertrag über
 * „Senden“ erneut; der Versand-Dialog nimmt von selbst die Korrektur-Vorlage.
 *
 * onClose(result): result = null (abgebrochen) oder die Antwort des Servers
 * ({ geaendert, version, verkaeufer }).
 */
const FELDER = [
  ["seller_name", "Name / Firma des Verkäufers", "text"],
  ["seller_address", "Straße und Hausnummer", "text"],
  ["seller_zip", "PLZ", "text"],
  ["seller_city", "Ort", "text"],
  ["seller_phone", "Telefon", "tel"],
  ["seller_email", "E-Mail", "email"],
  ["id_document", "Ausweis (Nr.)", "text"],
];

export default function VerkaeuferKorrekturDialog({ open, contract, onClose }) {
  const cd = contract?.contract_data || {};
  const [form, setForm] = useState(() => Object.fromEntries(
    FELDER.map(([k]) => [k, String(cd[k] ?? contract?.[k] ?? "")])));
  const [arbeitet, setArbeitet] = useState(false);
  // Pruefbericht 20.09.2026 (M-07): role=dialog, Escape, Fokus (lib/useModal);
  // waehrend des Speicherns schliesst Escape nicht.
  const dialogRef = useModal(() => { if (!arbeitet) onClose?.(null); }, { offen: Boolean(open) });
  if (!open) return null;

  const speichern = async () => {
    if (!form.seller_name.trim()) {
      toast.error("Bitte den Namen des Verkäufers angeben.");
      return;
    }
    setArbeitet(true);
    try {
      const { data } = await api.put(`/contracts/${contract.id}/verkaeufer`, form);
      if (!data?.geaendert) {
        toast.info("Keine Änderung — der Vertrag bleibt, wie er ist.");
        onClose?.(null);
        return;
      }
      toast.success(`Neue Fassung ${data.version} erstellt — jetzt an den Verkäufer senden.`);
      onClose?.(data);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setArbeitet(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
         data-testid="verkaeufer-korrektur-dialog">
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="verkaeufer-korrektur-titel"
           className="w-full max-w-lg rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)",
                    maxHeight: "90vh" }}>
        <div className="flex items-center justify-between px-5 py-4"
             style={{ borderBottom: "1px solid var(--border-default)" }}>
          <div className="flex items-center gap-2 font-semibold" style={{ color: "var(--text-primary)" }}
               id="verkaeufer-korrektur-titel">
            <UserRoundPen size={18} /> Verkäuferdaten korrigieren
          </div>
          <button type="button" onClick={() => onClose?.(null)} data-testid="verkaeufer-korrektur-schliessen"
                  className="w-11 h-11 -mr-1 rounded-full flex items-center justify-center hover:bg-white/10"
                  style={{ color: "var(--text-secondary)" }} title="Schließen" aria-label="Schließen">
            <X size={18} />
          </button>
        </div>

        <div className="px-5 py-4 overflow-y-auto flex flex-col gap-3">
          <div className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>
            Es entsteht eine neue Fassung des Vertrags (Fassung {(contract?.version || 1) + 1}); die
            bisherige bleibt im Archiv. Ein offener Abholtermin bekommt die neuen Daten mit.
            Danach den Vertrag über „Senden“ noch einmal an den Verkäufer schicken.
          </div>
          {FELDER.map(([k, label, typ]) => (
            <label key={k} className="flex flex-col gap-1">
              <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{label}</span>
              <input value={form[k]} type={typ} data-testid={`verkaeufer-${k}`}
                     data-autofocus={k === "seller_name" ? "" : undefined}
                     onChange={(e) => setForm((alt) => ({ ...alt, [k]: e.target.value }))}
                     className="px-3 py-2 rounded-xl outline-none"
                     style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)",
                              border: "1px solid var(--wa-12)" }} />
            </label>
          ))}
        </div>

        <div className="px-5 py-4 flex justify-end gap-2" style={{ borderTop: "1px solid var(--border-default)" }}>
          <button onClick={() => onClose?.(null)} className="px-4 py-2 rounded-xl"
                  style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}>
            Abbrechen
          </button>
          <button onClick={speichern} disabled={arbeitet} data-testid="verkaeufer-korrektur-speichern"
                  className="px-4 py-2 rounded-xl disabled:opacity-50"
                  style={{ background: "var(--accent-blue)", color: "#fff" }}>
            {arbeitet ? "Wird erstellt …" : "Neue Fassung erstellen"}
          </button>
        </div>
      </div>
    </div>
  );
}
