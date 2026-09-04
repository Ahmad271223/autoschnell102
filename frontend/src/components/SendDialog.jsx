import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { X, Send, MessageCircle, Mail, Save, Calendar as CalIcon } from "lucide-react";

export default function SendDialog({ open, contract, onClose }) {
  const { dealer } = useAuth();
  const nav = useNavigate();
  const [tab, setTab] = useState("whatsapp");
  const [phone, setPhone] = useState(contract.seller_phone || "");
  const [email, setEmail] = useState(contract.seller_email || "");
  const [subject, setSubject] = useState(dealer?.email_subject || "Kaufvertrag für Ihr Fahrzeug");
  const [waMsg, setWaMsg] = useState((dealer?.whatsapp_template || "").replaceAll("{händler_name}", dealer?.company_name || ""));
  const [emailMsg, setEmailMsg] = useState((dealer?.email_template || "").replaceAll("{händler_name}", dealer?.company_name || ""));
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const send = async (channel) => {
    setBusy(true);
    try {
      // Je Klick ein eigener Schluessel: Doppelklick oder Netz-
      // Wiederholung erzeugt serverseitig garantiert nur EINEN Eintrag.
      const idempotency_key = (crypto.randomUUID && crypto.randomUUID())
        || `${Date.now()}-${Math.random()}`;
      const body = channel === "whatsapp"
        ? { channel, recipient: phone, message: waMsg, idempotency_key }
        : { channel, recipient: email, subject, message: emailMsg,
            idempotency_key };
      const { data } = await api.post(`/contracts/${contract.id}/send`, body);
      if (channel === "whatsapp" && data.wa_url) {
        window.open(data.wa_url, "_blank", "noopener");
        toast.success("WhatsApp Chat geöffnet · PDF separat anhängen");
      } else {
        const z = data?.zustellung;
        if (data?.bereits_gesendet) toast.info("Dieser Versand wurde bereits registriert.");
        else if (z === "versendet") {
          // Der Sucher bekommt immer eine Kopie mit dem PDF (09/2026).
          toast.success(data?.kopie === "gesendet"
            ? "E-Mail mit Vertrag versendet · Kopie liegt in deinem Postfach"
            : "E-Mail mit Vertrag versendet");
          if (data?.kopie === "fehlgeschlagen") {
            toast.warning("Die Kopie an dich konnte nicht zugestellt werden — "
              + "der Vertrag ist beim Kunden angekommen.");
          }
        }
        else if (z === "mock") toast.success("Testmodus: Versand nur protokolliert, keine E-Mail");
        else toast.success("Versand registriert");
      }
    } catch (err) {
      toast.error(errMsg(err, "Versand fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  const saveOnly = () => {
    toast.success("PDF gespeichert");
    onClose();
    nav("/app/vertraege");
  };

  const saveAndSchedule = async () => {
    setBusy(true);
    try {
      // Termin wird beim PDF-Erstellen bereits automatisch angelegt, wenn ein
      // Abholdatum gesetzt war. Falls noch keiner existiert (z.B. ohne
      // pickup_date), legen wir hier einen Fallback-Termin an.
      if (!contract.appointment_id) {
        await api.post("/appointments", {
          vehicle_id: contract.vehicle_id, contract_id: contract.id,
          seller_name: contract.seller_name, seller_phone: contract.seller_phone,
          seller_email: contract.seller_email,
          pickup_address: `${contract.contract_data?.seller_address || ""} ${contract.contract_data?.seller_zip || ""} ${contract.contract_data?.seller_city || ""}`.trim(),
          pickup_date: contract.pickup_date || "",
          pickup_time: contract.pickup_time || "",
          status: "offen",
        });
      }
      toast.success("Termin im Terminplaner angelegt");
      onClose();
      nav("/app/termine");
    } catch (err) {
      toast.error(errMsg(err, "Termin konnte nicht erstellt werden"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="bg-[var(--bg-surface)] border w-full max-w-2xl rounded-md max-h-[90vh] overflow-y-auto"
           style={{ borderColor: "var(--border-default)" }} data-testid="send-dialog">
        <div className="flex items-center justify-between px-6 py-4 border-b" style={{ borderColor: "var(--border-default)" }}>
          <div>
            <div className="overline">Vertrag versenden</div>
            <div className="font-display font-bold text-lg">{contract.make} {contract.model}</div>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white" data-testid="close-send">
            <X size={20} />
          </button>
        </div>

        <div className="px-6 pt-4">
          <div className="flex border rounded-sm overflow-hidden w-full" style={{ borderColor: "var(--border-default)" }}>
            <TabBtn active={tab === "whatsapp"} onClick={() => setTab("whatsapp")} icon={MessageCircle} label="WhatsApp" testid="tab-whatsapp" />
            <TabBtn active={tab === "email"} onClick={() => setTab("email")} icon={Mail} label="E-Mail" testid="tab-email" />
          </div>
        </div>

        <div className="p-6 space-y-4">
          {tab === "whatsapp" ? (
            <>
              <Field label="Telefonnummer (international, z.B. +49…)" value={phone} onChange={setPhone} testid="wa-phone" />
              <div>
                <label className="text-xs text-zinc-400">Nachricht</label>
                <textarea data-testid="wa-message" rows={5} className="input-base w-full mt-1"
                          value={waMsg} onChange={(e) => setWaMsg(e.target.value)} />
              </div>
              <div className="text-[11px] text-zinc-500">
                Hinweis: WhatsApp-Anhang via wa.me ist eingeschränkt – PDF separat über "PDF öffnen".
              </div>
              <button data-testid="send-wa-btn" onClick={() => send("whatsapp")} disabled={busy || !phone}
                      className="kinetic-button w-full py-3 rounded-sm flex items-center justify-center gap-2 font-bold disabled:opacity-50">
                <Send size={15} /> WhatsApp-Chat öffnen
              </button>
            </>
          ) : (
            <>
              <Field label="E-Mail-Empfänger" value={email} onChange={setEmail} type="email" testid="email-to" />
              <Field label="Betreff" value={subject} onChange={setSubject} testid="email-subject" />
              <div className="text-[11px] text-zinc-500 mt-1">Versand über AutoSchnell mit deinem Firmennamen. Antwortet der Verkäufer, landet die Antwort in deinem Postfach — du bekommst zusätzlich eine Kopie mit PDF.</div>
              <div>
                <label className="text-xs text-zinc-400">Nachricht</label>
                <textarea data-testid="email-message" rows={5} className="input-base w-full mt-1"
                          value={emailMsg} onChange={(e) => setEmailMsg(e.target.value)} />
              </div>
              <div className="text-[11px] text-zinc-500">
                Die E-Mail wird mit dem Vertrags-PDF im Anhang über den Server versendet und im Archiv protokolliert.
              </div>
              <button data-testid="send-email-btn" onClick={() => send("email")} disabled={busy || !email}
                      className="kinetic-button w-full py-3 rounded-sm flex items-center justify-center gap-2 font-bold disabled:opacity-50">
                <Send size={15} /> E-Mail senden
              </button>
            </>
          )}

          <div className="border-t pt-4 flex gap-3" style={{ borderColor: "var(--border-default)" }}>
            <button onClick={saveOnly} data-testid="save-only-btn"
                    className="flex-1 px-4 py-3 rounded-sm border hover:bg-white/5 flex items-center justify-center gap-2"
                    style={{ borderColor: "var(--border-default)" }}>
              <Save size={14} /> PDF speichern
            </button>
            <button onClick={saveAndSchedule} data-testid="save-and-schedule-btn" disabled={busy}
                    className="flex-1 px-4 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-50"
                    style={{ background: "rgba(0,122,255,0.15)", color: "var(--accent-blue)", border: "1px solid rgba(0,122,255,0.4)" }}>
              <CalIcon size={14} /> Speichern & Termin
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const TabBtn = ({ active, onClick, icon: Icon, label, testid }) => (
  <button onClick={onClick} data-testid={testid}
          className={`flex-1 px-4 py-2.5 text-sm flex items-center justify-center gap-2 transition-colors ${
            active ? "bg-white/5 text-white" : "text-zinc-400 hover:text-white"
          }`}>
    <Icon size={14} /> {label}
  </button>
);

const Field = ({ label, value, onChange, type = "text", testid }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    <input data-testid={testid} type={type} value={value} onChange={(e) => onChange(e.target.value)}
           className="input-base w-full mt-1" />
  </div>
);
