import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { driverApi } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { verkleinereBildDatei } from "@/lib/bilder";
import { toast } from "sonner";
import { X, Plus, Trash2, Camera, CheckCircle2 } from "lucide-react";

/**
 * Abhol-Check der Fahrer-App: km-Stand, Schlüssel, Tankstand + Abweichungen
 * (optional mit Foto). Wird VOR der "Abgeholt"-Bestätigung ausgefüllt und
 * als unveränderbarer Abholbericht ans Backend gemeldet. Der Händler sieht
 * die Abweichungen anschließend in der Fahrzeugakte.
 */

const DEVIATION_TYPES = [
  { key: "mileage",       label: "Kilometerstand weicht ab" },
  { key: "keys",          label: "Schlüssel fehlt" },
  { key: "damage",        label: "Schaden / Kratzer" },
  { key: "tires",         label: "Reifen schlechter als angegeben" },
  { key: "warning_light", label: "Warnleuchte an" },
  { key: "equipment",     label: "Ausstattung fehlt" },
  { key: "documents",     label: "Dokument fehlt" },
  { key: "other",         label: "Sonstiges" },
];

const FUEL_LEVELS = ["leer", "1/4", "1/2", "3/4", "voll"];

export default function AbholCheckDialog({ appointment, onDone, onClose }) {
  const navigate = useNavigate();
  const [mileage, setMileage] = useState("");
  const [keys, setKeys] = useState("2");
  const [fuel, setFuel] = useState("1/2");
  const [notes, setNotes] = useState("");
  const [deviations, setDeviations] = useState([]);
  const [busy, setBusy] = useState(false);
  // Runde 21 (Gegenpruefung): Fotos, die gerade noch verkleinert werden —
  // solange darf nicht abgesendet werden (sonst fehlt das Foto im
  // unveraenderbaren Bericht).
  const [fotoLaeuft, setFotoLaeuft] = useState(0);

  const addDeviation = () =>
    setDeviations((d) => [...d, {
      // feste Kennung je Zeile: ein spaet fertiges Foto landet sicher in der
      // richtigen Abweichung, auch wenn inzwischen eine Zeile geloescht wurde
      id: (window.crypto && window.crypto.randomUUID && window.crypto.randomUUID()) || `${Date.now()}-${Math.random()}`,
      field: "damage", label: "", expected: "", actual: "", note: "", photo_b64: null,
    }]);

  const updateDev = (id, patch) =>
    setDeviations((d) => d.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const removeDev = (id) => setDeviations((d) => d.filter((x) => x.id !== id));

  // Runde 21: Foto im Handy auf max. 2000 px verkleinern — schneller Upload
  // auch mit schwachem Netz, und Aufnahmeort/Geraetedaten fallen weg. Klappt
  // das nicht, geht das Original wie bisher (max. 6 MB).
  const attachPhoto = async (id, file) => {
    if (!file) return;
    setFotoLaeuft((n) => n + 1);
    try {
      let dataUrl = null;
      try { dataUrl = await verkleinereBildDatei(file); } catch { dataUrl = null; }
      if (!dataUrl) { toast.error("Foto konnte nicht gelesen werden"); return; }
      if (dataUrl.length > 8000000) { toast.error("Foto zu groß (max. 6 MB)"); return; }
      updateDev(id, { photo_b64: dataUrl });
    } finally {
      setFotoLaeuft((n) => n - 1);
    }
  };

  const submit = async () => {
    if (busy) return;
    if (fotoLaeuft > 0) { toast.info("Foto wird noch vorbereitet – bitte kurz warten."); return; }
    for (const d of deviations) {
      if (!d.label.trim()) { toast.error("Bitte jede Abweichung kurz benennen."); return; }
    }
    setBusy(true);
    try {
      // "Abgeholt" gibt es nur mit unterschriebenem Protokoll. Vorher wurde
      // erst der Bericht geschickt und dann der Status — der lief ohne
      // Protokoll in 409, jeder neue Versuch legte eine weitere
      // Berichtsversion an (Pruefbericht Runde 4). Jetzt: Protokoll zuerst.
      if ((appointment.status || "") !== "abgeholt") {
        let proto = null;
        try {
          const r = await driverApi.get(`/driver/appointments/${appointment.id}/protocol`);
          proto = r.data?.protocol || r.data?.doc || r.data;
        } catch (_) { proto = null; }
        const final = proto && (proto.status === "final" || proto.protocol?.status === "final");
        if (!final) {
          toast.info("Bitte zuerst das Abholprotokoll ausfüllen und unterschreiben — danach den Abhol-Check senden.");
          onClose?.();
          navigate(`/fahrer/protokoll/${appointment.id}`);
          return;
        }
      }
      await driverApi.post(`/driver/appointments/${appointment.id}/report`, {
        mileage_at_pickup: mileage ? parseInt(mileage, 10) : null,
        keys_count: keys ? parseInt(keys, 10) : null,
        fuel_level: fuel,
        deviations: deviations.map((d) => ({
          field: d.field,
          label: d.label,
          expected: d.expected, actual: d.actual, note: d.note,
          photo_b64: d.photo_b64,
        })),
        notes,
      });
      // Idempotent: nach dem Protokoll-Abschluss ist der Termin bereits
      // "abgeholt"; das Backend bestaetigt das ohne Fehler.
      await driverApi.put(`/driver/appointments/${appointment.id}/status`, { status: "abgeholt" });
      toast.success(deviations.length
        ? `Abgeholt — ${deviations.length} Abweichung(en) gemeldet`
        : "Abgeholt — keine Abweichungen");
      onDone?.();
    } catch (e) {
      toast.error(errMsg(e, "Abholbericht konnte nicht gesendet werden"));
    } finally {
      setBusy(false);
    }
  };

  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const inputStyle = { borderColor: "var(--border-default)" };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.7)" }}>
      <div className="w-full sm:max-w-xl max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl p-5"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)",
                    color: "#ffffff" /* Dialog ist bewusst dunkel — Schrift
                    auch im hellen Theme explizit hell, sonst dunkel-auf-dunkel */ }}>
        <div className="flex items-center justify-between mb-1">
          <div className="text-lg font-bold">Abhol-Check</div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="text-xs text-zinc-500 mb-4">
          {appointment.title} — bitte vor Ort prüfen und dann bestätigen.
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-zinc-500">Kilometerstand bei Abholung</label>
            <input type="number" inputMode="numeric" value={mileage}
                   onChange={(e) => setMileage(e.target.value)}
                   placeholder="z.B. 85120" className={inputCls} style={inputStyle} />
          </div>
          <div>
            <label className="text-[11px] text-zinc-500">Anzahl Schlüssel</label>
            <input type="number" inputMode="numeric" value={keys}
                   onChange={(e) => setKeys(e.target.value)} className={inputCls} style={inputStyle} />
          </div>
        </div>

        <div className="mt-3">
          <label className="text-[11px] text-zinc-500">Tankfüllstand</label>
          <div className="flex gap-1.5 mt-1">
            {FUEL_LEVELS.map((f) => (
              <button key={f} type="button" onClick={() => setFuel(f)}
                      className={`px-3 py-1.5 rounded-lg text-xs border ${fuel === f ? "bg-white/15 font-semibold" : "text-zinc-400"}`}
                      style={inputStyle}>
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between">
          <div className="text-sm font-semibold">Abweichungen ({deviations.length})</div>
          <button type="button" onClick={addDeviation}
                  className="inline-flex items-center gap-1 text-xs text-zinc-300 hover:text-white">
            <Plus size={14} /> Abweichung hinzufügen
          </button>
        </div>

        {deviations.map((d) => (
          <div key={d.id} className="mt-2 rounded-xl border p-3 space-y-2" style={inputStyle}>
            <div className="flex items-center gap-2">
              <select value={d.field}
                      onChange={(e) => updateDev(d.id, { field: e.target.value })}
                      className="flex-1 rounded-lg border bg-[#141416] px-2 py-1.5 text-xs"
                      style={inputStyle}>
                {DEVIATION_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
              <button type="button" onClick={() => removeDev(d.id)} className="text-zinc-500 hover:text-red-400">
                <Trash2 size={15} />
              </button>
            </div>
            <input value={d.label} onChange={(e) => updateDev(d.id, { label: e.target.value })}
                   placeholder="Kurzbeschreibung, z.B. Kratzer hinten rechts *"
                   className={inputCls} style={inputStyle} />
            <div className="grid grid-cols-2 gap-2">
              <input value={d.expected} onChange={(e) => updateDev(d.id, { expected: e.target.value })}
                     placeholder="Laut Vertrag (z.B. 84.000 km)" className={inputCls} style={inputStyle} />
              <input value={d.actual} onChange={(e) => updateDev(d.id, { actual: e.target.value })}
                     placeholder="Vor Ort (z.B. 85.120 km)" className={inputCls} style={inputStyle} />
            </div>
            <div className="flex items-center gap-2">
              <label className="inline-flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer hover:text-white">
                <Camera size={14} />
                {d.photo_b64 ? "Foto ersetzen" : "Foto anhängen"}
                <input type="file" accept="image/*" capture="environment" className="hidden"
                       onChange={(e) => attachPhoto(d.id, e.target.files?.[0])} />
              </label>
              {d.photo_b64 && <img src={d.photo_b64} alt="" className="h-10 w-10 rounded object-cover" />}
            </div>
          </div>
        ))}

        <div className="mt-3">
          <label className="text-[11px] text-zinc-500">Bemerkungen (optional)</label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2}
                    className={inputCls} style={inputStyle} />
        </div>

        <button onClick={submit} disabled={busy || fotoLaeuft > 0}
                className="mt-4 w-full inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                style={{ background: "var(--accent-red, #FF3B30)" }}>
          <CheckCircle2 size={17} />
          {fotoLaeuft > 0 ? "Foto wird vorbereitet…" : busy ? "Wird gesendet…" : deviations.length
            ? `Abholung mit ${deviations.length} Abweichung(en) bestätigen`
            : "Abholung ohne Abweichungen bestätigen"}
        </button>
        <div className="mt-2 text-[10px] text-zinc-600 text-center">
          Der Bericht ist nach dem Absenden nicht mehr änderbar — Korrekturen
          erzeugen eine neue Version.
        </div>
      </div>
    </div>
  );
}
