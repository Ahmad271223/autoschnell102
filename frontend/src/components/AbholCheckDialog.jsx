import { useState } from "react";
import { driverApi } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
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
  const [mileage, setMileage] = useState("");
  const [keys, setKeys] = useState("2");
  const [fuel, setFuel] = useState("1/2");
  const [notes, setNotes] = useState("");
  const [deviations, setDeviations] = useState([]);
  const [busy, setBusy] = useState(false);

  const addDeviation = () =>
    setDeviations((d) => [...d, { field: "damage", label: "", expected: "", actual: "", note: "", photo_b64: null }]);

  const updateDev = (i, patch) =>
    setDeviations((d) => d.map((x, idx) => (idx === i ? { ...x, ...patch } : x)));

  const removeDev = (i) => setDeviations((d) => d.filter((_, idx) => idx !== i));

  const attachPhoto = (i, file) => {
    if (!file) return;
    if (file.size > 6 * 1024 * 1024) { toast.error("Foto zu groß (max. 6 MB)"); return; }
    const reader = new FileReader();
    reader.onload = () => updateDev(i, { photo_b64: reader.result });
    reader.readAsDataURL(file);
  };

  const submit = async () => {
    if (busy) return;
    for (const d of deviations) {
      if (!d.label.trim()) { toast.error("Bitte jede Abweichung kurz benennen."); return; }
    }
    setBusy(true);
    try {
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
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)" }}>
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

        {deviations.map((d, i) => (
          <div key={i} className="mt-2 rounded-xl border p-3 space-y-2" style={inputStyle}>
            <div className="flex items-center gap-2">
              <select value={d.field}
                      onChange={(e) => updateDev(i, { field: e.target.value })}
                      className="flex-1 rounded-lg border bg-[#141416] px-2 py-1.5 text-xs"
                      style={inputStyle}>
                {DEVIATION_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
              <button type="button" onClick={() => removeDev(i)} className="text-zinc-500 hover:text-red-400">
                <Trash2 size={15} />
              </button>
            </div>
            <input value={d.label} onChange={(e) => updateDev(i, { label: e.target.value })}
                   placeholder="Kurzbeschreibung, z.B. Kratzer hinten rechts *"
                   className={inputCls} style={inputStyle} />
            <div className="grid grid-cols-2 gap-2">
              <input value={d.expected} onChange={(e) => updateDev(i, { expected: e.target.value })}
                     placeholder="Laut Vertrag (z.B. 84.000 km)" className={inputCls} style={inputStyle} />
              <input value={d.actual} onChange={(e) => updateDev(i, { actual: e.target.value })}
                     placeholder="Vor Ort (z.B. 85.120 km)" className={inputCls} style={inputStyle} />
            </div>
            <div className="flex items-center gap-2">
              <label className="inline-flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer hover:text-white">
                <Camera size={14} />
                {d.photo_b64 ? "Foto ersetzen" : "Foto anhängen"}
                <input type="file" accept="image/*" capture="environment" className="hidden"
                       onChange={(e) => attachPhoto(i, e.target.files?.[0])} />
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

        <button onClick={submit} disabled={busy}
                className="mt-4 w-full inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                style={{ background: "var(--accent-red, #FF3B30)" }}>
          <CheckCircle2 size={17} />
          {busy ? "Wird gesendet…" : deviations.length
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
