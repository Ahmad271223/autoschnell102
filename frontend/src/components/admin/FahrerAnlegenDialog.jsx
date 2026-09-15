import { mitAbweichung } from "@/lib/anfrageAbweichung";
import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { X } from "lucide-react";
import ZugangsdatenKarte from "@/components/admin/ZugangsdatenKarte";
import PasswortFeld from "@/components/admin/PasswortFeld";
import { passwortProblem } from "@/lib/passwort";

/**
 * Kontonummer (13.09.2026): Fahrer-Konto durch den Betreiber anlegen —
 * frei (Admin-Fahrerliste) oder aus einer Zugangs-Anfrage (art=fahrer, die
 * Anfrage schliesst das Backend mit anfrage_id). Die E-Mail ist optional.
 * Die Zuordnung zur Firma macht weiter der Chef per Fahrer-ID (FD-…).
 * Wird von admin_v2/Fahrer.jsx und admin_v2/Freischaltungen.jsx genutzt.
 */
export default function FahrerAnlegenDialog({ request = null, onClose, onAngelegt }) {
  const [f, setF] = useState({
    display_name: request?.contact_person || "",
    email: request?.contact_email || "",
    phone: request?.contact_phone || "",
    password: "",
  });
  const [busy, setBusy] = useState(false);
  const [ergebnis, setErgebnis] = useState(null);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async () => {
    if (f.display_name.trim().length < 2) { toast.error("Name des Fahrers angeben"); return; }
    const problem = passwortProblem(f.password);
    if (problem) { toast.error(problem); return; }
    setBusy(true);
    try {
      const { data } = await mitAbweichung((extra) => api.post("/admin/drivers", {
        display_name: f.display_name.trim(), password: f.password,
        email: f.email.trim(), phone: f.phone.trim(),
        ...(request?.id ? { anfrage_id: request.id } : {}), ...extra,
      }));
      setErgebnis({ ...data, name: f.display_name.trim(), passwort: f.password });
      onAngelegt?.(data);
    } catch (e) { toast.error(errMsg(e, "Fahrer anlegen fehlgeschlagen")); }
    finally { setBusy(false); }
  };

  const inputCls = "w-full rounded-lg px-3 py-2 text-sm outline-none";
  const inputStyle = { background: "#18181b", color: "#fff", border: "1px solid rgba(255,255,255,0.12)" };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)" }}>
      <div className="w-full max-w-md rounded-2xl p-5"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)" }}
           data-testid="fahrer-anlegen-dialog">
        <div className="flex items-center justify-between mb-1">
          <div className="text-lg font-bold text-white">Fahrer anlegen</div>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-200" aria-label="Schließen"><X size={20} /></button>
        </div>
        {ergebnis ? (
          <ZugangsdatenKarte titel="Fahrer angelegt" name={ergebnis.name} kontonummer={ergebnis.kontonummer}
                             passwort={ergebnis.passwort}
                             driverCode={ergebnis.driver_code} bereich="fahrer" onClose={onClose} />
        ) : (
          <>
            <div className="text-[12px] text-zinc-500 mb-4">
              {request ? `Aus Anfrage: ${request.company_name || "—"}` : "Die Kontonummer vergibt das System."}
              {" — E-Mail ist nur Kontaktadresse (optional)."}
            </div>
            <div className="space-y-3">
              <input value={f.display_name} onChange={set("display_name")} placeholder="Name des Fahrers *"
                     data-testid="fahrer-anlegen-name" className={inputCls} style={inputStyle} autoFocus />
              <input value={f.email} onChange={set("email")} placeholder="Kontakt-E-Mail (optional)" type="email"
                     data-testid="fahrer-anlegen-email" className={inputCls} style={inputStyle} />
              <input value={f.phone} onChange={set("phone")} placeholder="Telefon (optional)"
                     className={inputCls} style={inputStyle} />
              <PasswortFeld value={f.password} onChange={(v) => setF((s) => ({ ...s, password: v }))}
                            testid="fahrer-anlegen-passwort" className={inputCls} style={inputStyle} />
            </div>
            <button onClick={submit} disabled={busy} data-testid="fahrer-anlegen-submit"
                    className="mt-4 w-full h-10 rounded-xl text-[14px] font-medium text-white disabled:opacity-50"
                    style={{ background: "var(--accent-red)" }}>
              {busy ? "Wird angelegt…" : "Fahrer-Konto anlegen"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
