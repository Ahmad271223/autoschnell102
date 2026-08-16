import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { driverApi } from "@/context/DriverContext";
import { errMsg, API_BASE } from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowLeft, Save, CheckCircle2, FileText, AlertTriangle, Pencil,
} from "lucide-react";
import SignaturePad from "@/components/SignaturePad";

/* Section/Check sind bewusst AUSSERHALB der Seite definiert: innerhalb
 * definierte Komponenten bekommen bei jedem Render eine neue Identität —
 * React baut dann alle Eingabefelder neu auf und der Fokus/Eingaben
 * gehen beim Tippen verloren. */
const Section = ({ n, title, children, hint }) => (
  <div className="mt-4 rounded-2xl p-4" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
    <div className="flex items-center gap-2">
      <span className="w-6 h-6 rounded-md flex items-center justify-center text-[11px] font-bold text-white"
            style={{ background: "var(--accent-red)" }}>{n}</span>
      <div className="text-sm font-bold">{title}</div>
    </div>
    {hint && <div className="text-[11px] text-zinc-500 mt-1">{hint}</div>}
    <div className="mt-3">{children}</div>
  </div>
);

const Check = ({ on, onClick, disabled, children }) => (
  <button type="button" onClick={onClick} disabled={disabled}
          className="w-full flex items-center gap-2.5 py-2 text-left text-sm disabled:opacity-60">
    <span className="w-5 h-5 rounded-md border flex items-center justify-center shrink-0"
          style={{ borderColor: on ? "#34c759" : "var(--border-default)",
                   background: on ? "#34c759" : "transparent" }}>
      {on && <CheckCircle2 size={13} className="text-black" />}
    </span>
    <span className={on ? "text-white" : "text-zinc-400"}>{children}</span>
  </button>
);

/**
 * Digitales Abhol-Protokoll — dieselben Abschnitte wie das PDF, nur
 * ausfüllbar: abhaken, eintippen, unterschreiben. Zwischenstände werden
 * automatisch gespeichert; beim Abschließen entsteht das fertige PDF und
 * das Fahrzeug gilt als abgeholt.
 */
export default function Protokoll() {
  const { id } = useParams();          // appointment id
  const nav = useNavigate();
  const [data, setData] = useState(null);
  const [f, setF] = useState({
    documents: {}, features: {}, condition: {}, keys_count: "", keys_expected: "",
    notes: "", place: "", damages_confirmed: false,
  });
  const [sigDriver, setSigDriver] = useState(null);
  const [sigSeller, setSigSeller] = useState(null);
  const [sellerName, setSellerName] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const saveTimer = useRef(null);

  const load = useCallback(async () => {
    try {
      const r = await driverApi.get(`/driver/appointments/${id}/protocol`);
      setData(r.data);
      const p = r.data.protocol;
      if (p) {
        setF((s) => ({
          ...s,
          documents: p.documents || {}, features: p.features || {},
          condition: p.condition || {}, keys_count: p.keys_count || "",
          keys_expected: p.keys_expected || "", notes: p.notes || "",
          place: p.place || "", damages_confirmed: !!p.damages_confirmed,
        }));
      }
      setSellerName(r.data.appointment?.seller_name || "");
    } catch (e) {
      toast.error(errMsg(e, "Protokoll konnte nicht geladen werden"));
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  const isFinal = data?.protocol?.status === "final";

  // Immer den AKTUELLEN Stand speichern (nie einen veralteten Klick-Zustand):
  // fRef spiegelt f nach jedem Render, der Auto-Save liest daraus.
  const fRef = useRef(f);
  useEffect(() => { fRef.current = f; });

  // Automatisch speichern (1,2 s nach der letzten Änderung)
  const queueSave = useCallback(() => {
    if (isFinal) return;
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      try {
        await driverApi.put(`/driver/appointments/${id}/protocol`, fRef.current);
        setSavedAt(new Date());
      } catch (e) { /* stiller Retry beim nächsten Tippen */ }
    }, 1200);
  }, [id, isFinal]);

  // patch darf ein Objekt ODER eine Funktion (voriger Stand -> Teilupdate)
  // sein — die Funktionsform verhindert, dass schnelle Klicks hintereinander
  // sich gegenseitig überschreiben (stale state).
  const upd = (patch) => {
    setF((s) => ({ ...s, ...(typeof patch === "function" ? patch(s) : patch) }));
    queueSave();
  };
  const toggleDoc = (name) =>
    upd((s) => ({ documents: { ...s.documents, [name]: !s.documents[name] } }));
  const toggleFeat = (name) =>
    upd((s) => ({ features: { ...s.features, [name]: !s.features[name] } }));
  const setCond = (k, v) =>
    upd((s) => ({ condition: { ...s.condition, [k]: v } }));

  const saveNow = async () => {
    setBusy(true);
    try {
      await driverApi.put(`/driver/appointments/${id}/protocol`, f);
      setSavedAt(new Date());
      toast.success("Zwischenstand gespeichert");
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };

  const finalize = async () => {
    if (!sigDriver || !sigSeller) {
      toast.error("Bitte beide Unterschriften erfassen"); return;
    }
    if (!window.confirm("Protokoll abschließen?\n\nDas Fahrzeug gilt danach als "
                        + "abgeholt und das PDF wird erstellt.")) return;
    setBusy(true);
    try {
      await driverApi.put(`/driver/appointments/${id}/protocol`, f);
      await driverApi.post(`/driver/appointments/${id}/protocol/finalize`, {
        signature_driver_b64: sigDriver,
        signature_seller_b64: sigSeller,
        seller_name: sellerName,
        place: f.place,
      });
      toast.success("Protokoll abgeschlossen — Fahrzeug ist abgeholt");
      load();
    } catch (e) { toast.error(errMsg(e, "Abschließen fehlgeschlagen")); }
    finally { setBusy(false); }
  };

  const startCorrection = async () => {
    if (!window.confirm("Korrektur starten?\n\nDas bisherige Protokoll bleibt als "
                        + "Nachweis erhalten, du erstellst Version "
                        + ((data?.protocol?.version || 1) + 1) + ".")) return;
    try {
      await driverApi.post(`/driver/appointments/${id}/protocol/correction`);
      setSigDriver(null); setSigSeller(null);
      toast.success("Korrektur-Version gestartet");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  if (!data) return <div className="p-8 text-zinc-500 text-sm">lade…</div>;

  const tpl = data.template || {};
  const veh = data.vehicle || {};
  const appt = data.appointment || {};
  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };

  return (
    <div className="p-4 pb-28 max-w-2xl mx-auto" data-testid="protokoll-page">
      <button onClick={() => nav("/fahrer")} className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
        <ArrowLeft size={14} /> Zurück zu den Fahrten
      </button>

      <div className="mt-2 flex items-start justify-between gap-3">
        <div>
          <div className="overline">Abhol-Protokoll{data.protocol?.version > 1 ? ` · Version ${data.protocol.version}` : ""}</div>
          <h1 className="font-display font-black text-2xl tracking-tighter mt-1">
            {veh.make_label} {veh.model_label}
          </h1>
          <div className="text-xs text-zinc-500 mt-0.5">
            {appt.pickup_date} {appt.pickup_time} · {appt.pickup_address}
          </div>
        </div>
        <a href={`${API_BASE}/driver/appointments/${id}/pickup-order.pdf`}
           target="_blank" rel="noreferrer"
           className="shrink-0 inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-xs border"
           style={st}>
          <FileText size={13} /> Papier-PDF
        </a>
      </div>

      {isFinal && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2"
             style={{ borderColor: "#34c75955", background: "#34c75914", color: "#34c759" }}>
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            Protokoll abgeschlossen — Fahrzeug gilt als abgeholt.
            <div className="mt-2 flex flex-wrap gap-2">
              <a href={`${API_BASE}/driver/appointments/${id}/protocol.pdf`}
                 target="_blank" rel="noreferrer"
                 className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                 style={{ background: "var(--accent-red)" }}>
                <FileText size={13} /> Ausgefülltes PDF öffnen
              </a>
              <button onClick={startCorrection}
                      className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border text-zinc-200"
                      style={st}>
                <Pencil size={13} /> Korrektur starten
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 1 Fahrzeugdaten */}
      <Section n="1" title="Fahrzeugdaten" hint="Aus dem Kaufvertrag — vor Ort prüfen.">
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
          {[["Marke", veh.make_label], ["Modell", veh.model_label],
            ["Erstzulassung", veh.first_registration],
            ["Kilometerstand", veh.mileage], ["Kraftstoff", veh.fuel_label],
            ["FIN", veh.vin]].map(([k, v]) => (
            <div key={k}>
              <div className="text-[11px] text-zinc-500">{k}</div>
              <div>{v || "—"}</div>
            </div>
          ))}
        </div>
      </Section>

      {/* 2 Dokumente */}
      <Section n="2" title="Dokumente & Zubehör" hint="Vor Ort einsammeln und abhaken.">
        {(tpl.documents || []).map((doc) => (
          <Check key={doc} disabled={isFinal} on={!!f.documents[doc]} onClick={() => toggleDoc(doc)}>{doc}</Check>
        ))}
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-zinc-500">Schlüssel erhalten</label>
            <input type="number" inputMode="numeric" value={f.keys_count} disabled={isFinal}
                   onChange={(e) => upd({ keys_count: e.target.value })}
                   className={inputCls} style={st} placeholder="z.B. 2" />
          </div>
          <div>
            <label className="text-[11px] text-zinc-500">davon vereinbart</label>
            <input type="number" inputMode="numeric" value={f.keys_expected} disabled={isFinal}
                   onChange={(e) => upd({ keys_expected: e.target.value })}
                   className={inputCls} style={st} placeholder="z.B. 2" />
          </div>
        </div>
      </Section>

      {/* 3 Ausstattung */}
      {(tpl.features || []).length > 0 && (
        <Section n="3" title="Ausstattung laut Inserat" hint="Vorhanden? Abhaken.">
          {tpl.features.map((ft) => (
            <Check key={ft} disabled={isFinal} on={!!f.features[ft]} onClick={() => toggleFeat(ft)}>{ft}</Check>
          ))}
        </Section>
      )}

      {/* 4 Technischer Zustand */}
      <Section n="4" title="Technischer Zustand">
        <div className="space-y-3">
          {(tpl.condition_fields || []).map((fld) => (
            <div key={fld.key}>
              <label className="text-[11px] text-zinc-500">{fld.label}</label>
              {fld.options ? (
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {fld.options.map((o) => (
                    <button key={o} type="button" disabled={isFinal}
                            onClick={() => setCond(fld.key, o)}
                            className={`px-3 py-1.5 rounded-lg text-xs border disabled:opacity-60 ${
                              f.condition[fld.key] === o ? "bg-white/15 font-semibold text-white" : "text-zinc-400"}`}
                            style={st}>
                      {o}
                    </button>
                  ))}
                </div>
              ) : (
                <input value={f.condition[fld.key] || ""} disabled={isFinal}
                       onChange={(e) => setCond(fld.key, e.target.value)}
                       className={inputCls} style={st}
                       placeholder={fld.key === "mileage" ? "z.B. 85120" : ""} />
              )}
            </div>
          ))}
        </div>
      </Section>

      {/* 5 Vorbestehende Schäden */}
      <Section n="5" title="Vorbestehende Schäden" hint="Laut Kaufvertrag dokumentiert.">
        {(data.damages || []).length === 0 ? (
          <div className="text-sm text-zinc-500">Keine Schäden im Vertrag vermerkt.</div>
        ) : (
          <div className="space-y-1 text-sm">
            {data.damages.map((d, i) => (
              <div key={i} className="text-amber-400">• {d.label || d.note || d.type || "Schaden"}</div>
            ))}
          </div>
        )}
        <Check disabled={isFinal} on={!!f.damages_confirmed} onClick={() => upd((s) => ({ damages_confirmed: !s.damages_confirmed }))}>
          Zustand entspricht der Dokumentation
        </Check>
      </Section>

      {/* 6 Fotos — Hinweis auf den bestehenden Abhol-Check */}
      <Section n="6" title="Vor-Ort-Aufnahme" hint="Fotos & Abweichungen erfasst du über den Abhol-Check auf der Fahrten-Seite.">
        <div className="text-xs text-zinc-500">
          Abweichungen mit Foto (z.B. Kratzer) meldest du im Abhol-Check —
          sie erscheinen automatisch in der Fahrzeugakte des Händlers.
        </div>
      </Section>

      {/* 7 Bemerkungen */}
      <Section n="7" title="Bemerkungen">
        <textarea value={f.notes} disabled={isFinal} rows={4}
                  onChange={(e) => upd({ notes: e.target.value })}
                  className={inputCls} style={st}
                  placeholder="Auffälligkeiten, Absprachen, Zustand …" />
      </Section>

      {/* 8 Übergabe */}
      <Section n="8" title="Übergabe-Bestätigung" hint="Beide Parteien unterschreiben auf dem Handy.">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-zinc-500">Ort</label>
            <input value={f.place} disabled={isFinal}
                   onChange={(e) => upd({ place: e.target.value })}
                   className={inputCls} style={st} placeholder="z.B. Hannover" />
          </div>
          <div>
            <label className="text-[11px] text-zinc-500">Name Verkäufer</label>
            <input value={sellerName} disabled={isFinal}
                   onChange={(e) => setSellerName(e.target.value)}
                   className={inputCls} style={st} />
          </div>
        </div>
        {!isFinal && (
          <div className="mt-4 space-y-4">
            <SignaturePad label="Unterschrift Verkäufer" onChange={setSigSeller} />
            <SignaturePad label="Unterschrift Fahrer" onChange={setSigDriver} />
          </div>
        )}
      </Section>

      {/* Fixe Aktionsleiste */}
      {!isFinal && (
        <div className="fixed bottom-0 left-0 right-0 p-3 flex gap-2"
             style={{ background: "rgba(10,10,10,0.95)", borderTop: "1px solid rgba(255,255,255,0.08)" }}>
          <button onClick={saveNow} disabled={busy}
                  className="flex-1 rounded-xl py-3 text-sm border inline-flex items-center justify-center gap-2 disabled:opacity-50"
                  style={st}>
            <Save size={15} /> Speichern
          </button>
          <button onClick={finalize} disabled={busy}
                  className="flex-1 rounded-xl py-3 text-sm font-semibold text-white inline-flex items-center justify-center gap-2 disabled:opacity-50"
                  style={{ background: "var(--accent-red)" }}>
            <CheckCircle2 size={16} /> Abschließen
          </button>
        </div>
      )}
      {savedAt && !isFinal && (
        <div className="fixed bottom-16 right-4 text-[10px] text-zinc-600">
          gespeichert {savedAt.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}
        </div>
      )}
      {!isFinal && (!sigDriver || !sigSeller) && (
        <div className="mt-3 text-[11px] text-amber-400/80 inline-flex items-center gap-1.5">
          <AlertTriangle size={12} /> Zum Abschließen werden beide Unterschriften benötigt.
        </div>
      )}
    </div>
  );
}
