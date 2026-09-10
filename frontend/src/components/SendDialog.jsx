import { useEffect, useMemo, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { X, Send, MessageCircle, Mail, Save, Calendar as CalIcon, FileText } from "lucide-react";
import { openContractPdf } from "@/lib/pdf";
import { dateiTeilen, kannDateiTeilen, pdfDatei } from "@/lib/teilen";

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
  // Nachpruefung Runde 10: EIN Schluessel je geoeffnetem Dialog. Ein erneuter
  // Klick nach Fehler oder Timeout traegt denselben Schluessel und laeuft
  // serverseitig in die Wiederaufnahme statt in eine zweite Zustellung.
  // Nach einem erfolgreichen Versand gibt es einen neuen Schluessel, damit
  // ein bewusster zweiter Versand moeglich bleibt.
  const neuerSchluessel = () => (crypto.randomUUID && crypto.randomUUID())
    || `${Date.now()}-${Math.random()}`;
  const keyRef = useRef(neuerSchluessel());
  useEffect(() => { if (open) keyRef.current = neuerSchluessel(); }, [open]);

  // WhatsApp am Handy (09/2026): Die digitale Fassung wird vorab geladen,
  // damit das Teilen direkt beim Tipp passiert — Browser verlangen dafuer
  // eine frische Nutzeraktion. Kann das Geraet Dateien teilen, haengt das
  // PDF von der eigenen Nummer des Suchers an; sonst (PC) kommt ein
  // Download-Link in die Nachricht.
  const [pdf, setPdf] = useState(null);
  const probe = useMemo(() => {
    try { return new File(["%PDF-1.4"], "probe.pdf", { type: "application/pdf" }); }
    catch { return null; }
  }, []);
  const geraetKannTeilen = kannDateiTeilen(pdf || probe);
  useEffect(() => {
    if (!open || !contract?.id) return undefined;
    let aktiv = true;
    setPdf(null);
    api.get(`/contracts/${contract.id}/pdf`, { responseType: "blob", params: { variante: "digital" } })
      .then((r) => {
        if (!aktiv) return;
        const name = contract.filename || `Kaufvertrag ${contract.make || ""} ${contract.model || ""}`.trim();
        setPdf(pdfDatei(r.data, name));
      })
      .catch(() => { if (aktiv) setPdf(null); });
    return () => { aktiv = false; };
  }, [open, contract?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Befund Ahmad 10.09.2026: "die Nummer des Verkaeufers wird nie geoeffnet".
  // window.open NACH dem Server-Aufruf gilt fuer den Browser nicht mehr als
  // Nutzerklick und wird als Popup geblockt. Deshalb: das Fenster SOFORT im
  // Klick oeffnen und erst danach auf die WhatsApp-Adresse leiten. Klappt
  // auch das nicht (strenger Blocker), bleibt ein Knopf zum Nachoeffnen.
  const [waUrl, setWaUrl] = useState("");

  if (!open) return null;

  const send = async (channel) => {
    setBusy(true);
    let fenster = null;
    if (channel === "whatsapp") {
      try { fenster = window.open("", "_blank"); } catch { fenster = null; }
      if (fenster) { try { fenster.opener = null; } catch { /* egal */ } }
    }
    try {
      const idempotency_key = keyRef.current;
      const body = channel === "whatsapp"
        ? { channel, recipient: phone, message: waMsg, idempotency_key, methode: "link" }
        : { channel, recipient: email, subject, message: emailMsg,
            idempotency_key };
      const { data } = await api.post(`/contracts/${contract.id}/send`, body);
      keyRef.current = neuerSchluessel();
      if (channel === "whatsapp" && data.wa_url) {
        setWaUrl(data.wa_url);
        let geoeffnet = false;
        if (fenster && !fenster.closed) {
          try { fenster.location.href = data.wa_url; fenster.focus(); geoeffnet = true; } catch { geoeffnet = false; }
        }
        if (!geoeffnet) {
          const w2 = window.open(data.wa_url, "_blank", "noopener");
          geoeffnet = !!w2;
        }
        const bis = data.link_gueltig_bis
          ? new Date(data.link_gueltig_bis).toLocaleDateString("de-DE") : null;
        if (geoeffnet) {
          toast.success(bis
            ? `WhatsApp-Chat geöffnet · Download-Link zum Vertrag steht in der Nachricht (gültig bis ${bis})`
            : "WhatsApp-Chat geöffnet · Download-Link zum Vertrag steht in der Nachricht");
        } else {
          toast.warning("Dein Browser hat das WhatsApp-Fenster blockiert — bitte unten auf „WhatsApp jetzt öffnen“ tippen.");
        }
      } else {
        if (fenster && !fenster.closed) { try { fenster.close(); } catch { /* egal */ } }
        const z = data?.zustellung;
        // Runde 8: "bereits registriert" hiess frueher auch dann, wenn der
        // Versand noch lief oder abgebrochen war. Jetzt sagt der Server,
        // was wirklich ist — und der Nutzer sieht es.
        if (data?.bereits_gesendet && z === "laeuft") {
          toast.info("Dieser Versand läuft gerade noch — bitte einen Moment warten.");
        } else if (z === "unklar") {
          toast.warning("Der Versand hat kein Ergebnis gemeldet. Bitte noch einmal "
            + "auf Senden klicken — es wird garantiert nicht doppelt zugestellt.");
        } else if (data?.bereits_gesendet) toast.info("Dieser Versand wurde bereits registriert.");
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
      if (fenster && !fenster.closed) { try { fenster.close(); } catch { /* egal */ } }
      toast.error(errMsg(err, "Versand fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  // Handy: PDF ueber das Teilen-Menue an WhatsApp uebergeben (eigene Nummer).
  // Erst teilen (Nutzeraktion!), dann den Versand am Vertrag vermerken.
  const teilen = async () => {
    if (!pdf) return;
    setBusy(true);
    try {
      const titel = `Kaufvertrag ${contract.make || ""} ${contract.model || ""}`.trim();
      const ergebnis = await dateiTeilen({ datei: pdf, text: waMsg, titel });
      if (ergebnis === "geteilt") {
        try {
          await api.post(`/contracts/${contract.id}/send`, {
            channel: "whatsapp", recipient: phone, message: waMsg,
            idempotency_key: keyRef.current, methode: "teilen",
          });
          keyRef.current = neuerSchluessel();
        } catch (err) {
          toast.warning(errMsg(err, "Der Versand konnte nicht im Archiv vermerkt werden"));
        }
        toast.success("An WhatsApp übergeben · Chat des Verkäufers wählen und senden");
      } else if (ergebnis === "abgebrochen") {
        toast.info("Teilen abgebrochen");
      } else {
        toast.info("Teilen ist auf diesem Gerät nicht möglich — der Chat wird mit Download-Link geöffnet");
        await send("whatsapp");
      }
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
              {geraetKannTeilen ? (
                <>
                  <div className="text-[11px] text-zinc-500" data-testid="wa-hinweis-teilen">
                    Dein Handy hängt das PDF direkt an: Tippe auf „Per WhatsApp teilen", wähle WhatsApp und
                    dann den Chat des Verkäufers. Der Vertrag geht von deiner eigenen Nummer raus.
                  </div>
                  <button type="button" data-testid="wa-share-btn" onClick={teilen} disabled={busy || !pdf}
                          className="kinetic-button w-full py-3 rounded-sm flex items-center justify-center gap-2 font-bold disabled:opacity-50">
                    <Send size={15} /> {pdf ? "Per WhatsApp teilen (PDF anhängen)" : "PDF wird vorbereitet…"}
                  </button>
                  <button type="button" data-testid="send-wa-btn" onClick={() => send("whatsapp")} disabled={busy || !phone}
                          className="w-full py-2.5 rounded-sm flex items-center justify-center gap-2 text-sm font-semibold border disabled:opacity-50"
                          style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
                    <MessageCircle size={15} /> Stattdessen Chat mit Download-Link öffnen
                  </button>
                </>
              ) : (
                <>
                  <div className="text-[11px] text-zinc-500" data-testid="wa-hinweis-link">
                    WhatsApp am PC kann kein PDF anhängen. Die Nachricht bekommt deshalb automatisch einen
                    zeitlich begrenzten Download-Link zur digitalen Vertragsfassung. Am Handy hängt AutoSchnell
                    das PDF direkt an.
                  </div>
                  <button data-testid="send-wa-btn" onClick={() => send("whatsapp")} disabled={busy || !phone}
                          className="kinetic-button w-full py-3 rounded-sm flex items-center justify-center gap-2 font-bold disabled:opacity-50">
                    <Send size={15} /> WhatsApp-Chat öffnen (mit Download-Link)
                  </button>
                </>
              )}
              {waUrl && (
                <a href={waUrl} target="_blank" rel="noopener noreferrer" data-testid="wa-open-again"
                   className="w-full py-2.5 rounded-sm flex items-center justify-center gap-2 text-sm font-semibold border"
                   style={{ borderColor: "var(--accent-green, #22c55e)", color: "var(--text-primary)" }}>
                  <MessageCircle size={15} /> WhatsApp jetzt öffnen (Chat mit Verkäufer)
                </a>
              )}
              <button type="button" data-testid="wa-digital-pdf-btn"
                      onClick={async () => {
                        try { await openContractPdf(contract.id, { variante: "digital" }); }
                        catch (err) { toast.error(errMsg(err, "PDF konnte nicht geöffnet werden")); }
                      }}
                      className="w-full py-2 rounded-sm flex items-center justify-center gap-2 text-xs border"
                      style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                <FileText size={13} /> Digitale Fassung ansehen
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
                Die E-Mail wird mit der digitalen Vertragsfassung im Anhang versendet (ohne Unterschriftsfelder —
                unter „Unterschriften" steht: „Dieser Vertrag ist ohne Unterschrift gültig.") und im Archiv protokolliert.
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
