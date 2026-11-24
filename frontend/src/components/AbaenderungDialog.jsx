import { useCallback, useEffect, useState } from "react";
import { api, errMsg, openAuthedFile } from "@/lib/api";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";
import { FilePenLine, FileText, Link2, Mail, X } from "lucide-react";
import { toast } from "sonner";

/**
 * Abänderungsverträge (Wunsch Ahmad 24.11.2026).
 *
 * Wird ein schon unterschriebener Kaufvertrag geändert (im Büro oder durch die
 * Abholung: Preis, Schaden, fehlende Ausstattung, Nachlackierung), entsteht
 * ein eigenes PDF „Abänderungsvertrag“ mit NUR den Änderungen. Die
 * unterschriebene Fassung bleibt, wie sie ist.
 *
 * Beim Erstellen wählt das Büro: online unterschreiben lassen, PDF per
 * E-Mail senden oder nur intern ändern. Verschickt wird NUR über die Knöpfe
 * hier — nie automatisch, nie doppelt (der Server fragt mit 409 zurück).
 */

export const STAND = {
  offen: { text: "offen", farbe: "var(--tx-amber)", hg: "rgba(255,159,10,0.14)" },
  intern: { text: "intern", farbe: "var(--text-secondary)", hg: "var(--apple-btn-secondary-bg)" },
  gesendet: { text: "gesendet", farbe: "var(--accent-blue)", hg: "rgba(0,122,255,0.12)" },
  unterschrieben: { text: "unterschrieben", farbe: "var(--tx-gruen)", hg: "rgba(52,199,89,0.14)" },
  ersetzt: { text: "ersetzt", farbe: "var(--text-muted)", hg: "var(--apple-btn-secondary-bg)" },
};

export const WAHL = [
  { id: "online", titel: "Online unterschreiben lassen",
    text: "Der Verkäufer bekommt einen Link per E-Mail und unterschreibt am Handy." },
  { id: "email", titel: "PDF per E-Mail senden",
    text: "Der Verkäufer bekommt das PDF, unterschreibt und schickt es zurück." },
  { id: "intern", titel: "Nur intern ändern",
    text: "Nichts wird verschickt. Senden geht später hier." },
];

const RUECKFRAGE = new Set(["bereits_versendet", "frueherer_versand_unklar"]);

/** Senden mit der üblichen Rückfrage: 409 „schon verschickt“ → fragen → mit erneut=true. */
export async function sendenMitRueckfrage(senden, fragen = (t) => window.confirm(t)) {
  try {
    return await senden(false);
  } catch (e) {
    const d = e?.response?.data?.detail;
    if (e?.response?.status === 409 && d && RUECKFRAGE.has(d.code)) {
      if (!fragen(d.msg || "Schon verschickt. Wirklich noch einmal senden?")) return null;
      return senden(true);
    }
    throw e;
  }
}

/** Nach dem Speichern einer neuen Fassung: braucht es einen Abänderungsvertrag? (sonst null) */
export async function abaenderungNoetig(contractId) {
  try {
    const { data } = await api.get(`/contracts/${contractId}/abaenderungen`);
    return data?.unterschrieben && (data?.vorschlag || []).length ? data : null;
  } catch {
    return null;
  }
}

const datum = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString("de-DE");
};

const eingabe = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };
const knopf = "min-h-[44px] px-3.5 rounded-xl inline-flex items-center justify-center gap-1.5 text-[13px] "
  + "font-semibold disabled:opacity-50";
const zweit = { background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" };
const haupt = { background: "var(--accent-blue)", color: "#fff" };

function Zeilen({ zeilen, testid }) {
  return (
    <ul className="flex flex-col gap-1 text-[13px]" data-testid={testid} style={{ color: "var(--text-primary)" }}>
      {(zeilen || []).map((z, i) => <li key={i} className="leading-snug">• {z}</li>)}
    </ul>
  );
}

function SendenForm({ art, standard, onSenden, laeuft }) {
  const [an, setAn] = useState(standard || "");
  const [danach, setDanach] = useState(false);
  return (
    <div className="flex flex-col gap-2" data-testid={`abaenderung-senden-form-${art}`}>
      <label className="flex flex-col gap-1">
        <span className="text-xs" style={{ color: "var(--text-secondary)" }}>E-Mail des Verkäufers</span>
        <input type="email" value={an} onChange={(e) => setAn(e.target.value)} data-testid="abaenderung-empfaenger"
               className="px-3 py-2 rounded-xl outline-none" style={eingabe} />
      </label>
      {art === "online" && (
        <label className="flex items-start gap-2 text-[13px]" style={{ color: "var(--text-primary)" }}>
          <input type="checkbox" checked={danach} onChange={(e) => setDanach(e.target.checked)}
                 data-testid="abaenderung-danach-senden" className="mt-1" />
          <span>Nach der Unterschrift das unterschriebene PDF automatisch an diese Adresse senden</span>
        </label>
      )}
      <button type="button" className={knopf} style={haupt} disabled={laeuft || !an.trim()}
              data-testid="abaenderung-senden-los" onClick={() => onSenden(an.trim(), danach)}>
        {art === "online" ? <Link2 size={15} /> : <Mail size={15} />}
        {laeuft ? "Wird gesendet …" : art === "online" ? "Link per E-Mail senden" : "PDF per E-Mail senden"}
      </button>
    </div>
  );
}

export default function AbaenderungDialog({ open, contract, onClose }) {
  const [daten, setDaten] = useState(null);
  const [laedt, setLaedt] = useState(true);
  const [wahl, setWahl] = useState("online");
  const [erstellt, setErstellt] = useState(false);
  const [laeuft, setLaeuft] = useState(false);
  const [formFuer, setFormFuer] = useState(null); // {id, art}
  const [geaendert, setGeaendert] = useState(false);
  const schliessen = () => { if (!laeuft) onClose?.(geaendert); };
  const dialogRef = useModal(schliessen, { offen: Boolean(open) });
  const standardMail = contract?.seller_email || contract?.contract_data?.seller_email || "";

  const laden = useCallback(async () => {
    setLaedt(true);
    try {
      const { data } = await api.get(`/contracts/${contract.id}/abaenderungen`);
      setDaten(data);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLaedt(false);
    }
  }, [contract?.id]);

  useEffect(() => { if (open && contract?.id) laden(); }, [open, contract?.id, laden]);

  const erstellen = async () => {
    setLaeuft(true);
    try {
      const { data } = await api.post(`/contracts/${contract.id}/abaenderungen`, { art: wahl });
      setGeaendert(true);
      setErstellt(true);
      toast.success("Abänderungsvertrag erstellt.");
      if (wahl !== "intern") setFormFuer({ id: data.id, art: wahl === "online" ? "online" : "pdf" });
      await laden();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLaeuft(false);
    }
  };

  const senden = async (aid, art, an, danach) => {
    setLaeuft(true);
    try {
      const erg = await sendenMitRueckfrage((erneut) => api.post(
        `/contracts/${contract.id}/abaenderungen/${aid}/senden`,
        { art, recipient: an, erneut, nach_unterschrift_senden: Boolean(danach) }));
      if (erg) {
        toast.success(art === "online" ? "Link verschickt." : "PDF verschickt.");
        setGeaendert(true);
        setFormFuer(null);
        await laden();
      }
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setLaeuft(false);
    }
  };

  if (!open) return null;
  const vorschlag = daten?.vorschlag_zeilen || [];
  const liste = daten?.liste || [];

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center sm:p-4 bg-black/70 backdrop-blur-sm"
         data-testid="abaenderung-dialog">
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="abaenderung-titel"
           className="w-full max-w-xl rounded-t-2xl sm:rounded-2xl overflow-hidden flex flex-col"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", maxHeight: "92vh" }}>
        <div className="flex items-center justify-between px-4 sm:px-5 py-3.5"
             style={{ borderBottom: "1px solid var(--border-default)" }}>
          <div className="flex items-center gap-2 font-semibold" id="abaenderung-titel"
               style={{ color: "var(--text-primary)" }}>
            <FilePenLine size={18} /> Abänderungsverträge
          </div>
          <button type="button" onClick={schliessen} data-testid="abaenderung-schliessen"
                  className="w-11 h-11 -mr-1 rounded-full flex items-center justify-center hover:bg-white/10"
                  style={{ color: "var(--text-secondary)" }} title="Schließen" aria-label="Schließen">
            <X size={18} />
          </button>
        </div>

        <div className="px-4 sm:px-5 py-4 overflow-y-auto flex flex-col gap-4">
          {laedt && !daten && <div className="text-sm" style={{ color: "var(--text-secondary)" }}>Lädt …</div>}

          {daten && !daten.unterschrieben && (
            <div className="text-sm" data-testid="abaenderung-nicht-unterschrieben" style={{ color: "var(--text-secondary)" }}>
              Der Kaufvertrag ist noch nicht unterschrieben. Änderungen stehen direkt in der neuen Fassung —
              ein Abänderungsvertrag ist nicht nötig.
            </div>
          )}

          {vorschlag.length > 0 && !erstellt && (
            <div className="flex flex-col gap-3 rounded-xl p-3" data-testid="abaenderung-vorschlag"
                 style={{ border: "1px solid var(--border-default)" }}>
              <div className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Geändert seit der Unterschrift
              </div>
              <Zeilen zeilen={vorschlag} testid="abaenderung-vorschlag-zeilen" />
              <div className="flex flex-col gap-2" role="radiogroup" aria-label="Wie weiter?">
                {WAHL.map((w) => (
                  <label key={w.id} className="flex items-start gap-2 rounded-xl px-3 py-2 cursor-pointer"
                         style={{ background: wahl === w.id ? "var(--hover-bg)" : "transparent" }}>
                    <input type="radio" name="abaenderung-wahl" value={w.id} checked={wahl === w.id}
                           onChange={() => setWahl(w.id)} data-testid={`abaenderung-wahl-${w.id}`} className="mt-1" />
                    <span>
                      <span className="block text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>{w.titel}</span>
                      <span className="block text-[12px]" style={{ color: "var(--text-muted)" }}>{w.text}</span>
                    </span>
                  </label>
                ))}
              </div>
              <button type="button" className={knopf} style={haupt} disabled={laeuft} onClick={erstellen}
                      data-testid="abaenderung-erstellen">
                <FilePenLine size={15} /> Abänderungsvertrag erstellen
              </button>
              <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                Erstellen verschickt noch nichts.
              </div>
            </div>
          )}

          {daten?.unterschrieben && vorschlag.length === 0 && liste.length === 0 && (
            <div className="text-sm" data-testid="abaenderung-keine" style={{ color: "var(--text-secondary)" }}>
              Seit der Unterschrift hat sich nichts geändert.
            </div>
          )}

          {liste.map((a) => {
            const st = STAND[a.status] || STAND.offen;
            const kannLink = ["offen", "intern", "gesendet"].includes(a.status);
            return (
              <div key={a.id} className="flex flex-col gap-2 rounded-xl p-3" data-testid={`abaenderung-${a.id}`}
                   style={{ border: "1px solid var(--border-default)", opacity: a.status === "ersetzt" ? 0.7 : 1 }}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
                    Abänderungsvertrag vom {datum(a.erstellt_am)}
                  </div>
                  <span className="text-[11.5px] font-semibold px-2 py-0.5 rounded-full"
                        data-testid={`abaenderung-stand-${a.id}`} style={{ color: st.farbe, background: st.hg }}>
                    {st.text}
                  </span>
                </div>
                <Zeilen zeilen={a.zeilen} />
                {a.status === "unterschrieben" && (
                  <div className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                    Unterschrieben am {datum(a.unterschrieben_am)}{a.unterschrieben_von ? ` von ${a.unterschrieben_von}` : ""}
                  </div>
                )}
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={knopf} style={zweit} data-testid={`abaenderung-pdf-${a.id}`}
                          onClick={() => openAuthedFile(`/contracts/${contract.id}/abaenderungen/${a.id}/pdf`)}>
                    <FileText size={15} /> PDF ansehen
                  </button>
                  {a.status !== "ersetzt" && (
                    <button type="button" className={knopf} style={zweit} data-testid={`abaenderung-mail-${a.id}`}
                            onClick={() => setFormFuer({ id: a.id, art: "pdf" })}>
                      <Mail size={15} /> Per E-Mail senden
                    </button>
                  )}
                  {kannLink && (
                    <button type="button" className={knopf} style={zweit} data-testid={`abaenderung-link-${a.id}`}
                            onClick={() => setFormFuer({ id: a.id, art: "online" })}>
                      <Link2 size={15} /> Online unterschreiben lassen
                    </button>
                  )}
                </div>
                {formFuer?.id === a.id && (
                  <SendenForm key={formFuer.art} art={formFuer.art} standard={standardMail} laeuft={laeuft}
                              onSenden={(an, danach) => senden(a.id, formFuer.art, an, danach)} />
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
