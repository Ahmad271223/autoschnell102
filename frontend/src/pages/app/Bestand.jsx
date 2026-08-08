import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  Plus, AlertTriangle, Archive, Trash2, Tag, Clock, X,
} from "lucide-react";

/**
 * Fahrzeugbestand (B2B-Modul Phase 1).
 * - Entscheidungs-Warteschlange: abgeholte Fahrzeuge (Speichern / Verkaufen / Löschen)
 * - Bestand mit Lifecycle-Filter, 50-Tage-Countdown und Quellen-Kennzeichnung
 * - Manuelles Hinzufügen vorhandener Fahrzeuge (source: manuell)
 */

const LIFECYCLE_LABELS = {
  verglichen: "Verglichen", besichtigung: "Besichtigung",
  verhandlung: "Verhandlung", vertrag_erstellt: "Vertrag erstellt",
  gekauft: "Gekauft", abholung_geplant: "Abholung geplant",
  abgeholt: "Abgeholt", bestand: "Im Bestand",
  verkaufsentwurf: "Verkaufsentwurf", verkaufsbereit: "Verkaufsbereit",
  veroeffentlicht: "Veröffentlicht", reserviert: "Reserviert",
  verkauft: "Verkauft", nicht_abgeholt: "Nicht abgeholt",
  storniert: "Storniert", archiviert: "Archiviert",
};

const LIFECYCLE_COLORS = {
  abgeholt: "#f59e0b", bestand: "#0ea5e9", verkaufsentwurf: "#a855f7",
  verkaufsbereit: "#34c759", veroeffentlicht: "#34c759",
  reserviert: "#eab308", verkauft: "#71717a", archiviert: "#52525b",
};

const FILTERS = [
  { key: "",                label: "Alle" },
  { key: "abgeholt",        label: "Entscheidung fällig" },
  { key: "bestand",         label: "Im Bestand" },
  { key: "verkaufsentwurf", label: "Entwürfe" },
  { key: "verkaufsbereit",  label: "Verkaufsbereit" },
  { key: "reserviert",      label: "Reserviert" },
  { key: "verkauft",        label: "Verkauft" },
];

export default function Bestand() {
  const [data, setData] = useState({ items: [], counts: {} });
  const [filter, setFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [busy, setBusy] = useState(null);

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams();
      if (filter) params.set("lifecycle", filter);
      if (sourceFilter) params.set("source", sourceFilter);
      const r = await api.get(`/bestand?${params.toString()}`);
      setData(r.data);
    } catch (e) {
      toast.error(errMsg(e, "Bestand konnte nicht geladen werden"));
    }
  }, [filter, sourceFilter]);

  useEffect(() => { load(); }, [load]);

  const decide = async (vehicleId, decision) => {
    if (busy) return;
    if (decision === "loeschen" &&
        !window.confirm("Fahrzeug wirklich löschen?\nFotos werden entfernt — Vertrag und Historie bleiben erhalten.")) {
      return;
    }
    setBusy(vehicleId);
    try {
      const r = await api.post(`/vehicles/${vehicleId}/decision`, { decision });
      if (decision === "verkaufsentwurf") {
        const draft = await api.post(`/resale/draft/${vehicleId}`);
        toast.success("Inseratsentwurf erstellt");
        window.location.href = `/app/inserat/${draft.data.id}`;
        return;
      }
      toast.success(decision === "bestand"
        ? "Ins Bestand übernommen (50 Tage Aufbewahrung)"
        : "Fahrzeug gelöscht");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(null);
    }
  };

  const counts = data.counts || {};
  const pending = (counts["abgeholt"] || 0);

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-7xl mx-auto" data-testid="bestand-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="overline">Bestand</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
            Fahrzeugbestand & Weiterverkauf
          </h1>
        </div>
        <button onClick={() => setShowManual(true)}
                className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                style={{ background: "var(--accent-red)" }}>
          <Plus size={16} /> Fahrzeug manuell hinzufügen
        </button>
      </div>

      {pending > 0 && (
        <div className="mt-4 rounded-xl border px-4 py-3 flex items-center gap-2 text-sm"
             style={{ borderColor: "#f59e0b55", background: "#f59e0b14", color: "#fbbf24" }}>
          <AlertTriangle size={16} />
          {pending} abgeholte(s) Fahrzeug(e) warten auf deine Entscheidung.
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilter(f.key)}
                  className={`px-3 py-1.5 rounded-lg text-[13px] border transition ${
                    filter === f.key ? "bg-white/10 font-semibold" : "text-zinc-400 hover:text-white"}`}
                  style={{ borderColor: "var(--border-default)" }}>
            {f.label}
            {f.key && counts[f.key] ? ` (${counts[f.key]})` : ""}
          </button>
        ))}
        <div className="ml-auto flex gap-1.5">
          {[["", "Alle Quellen"], ["plattform", "Über Sucher"], ["manuell", "Manuell"]].map(([k, l]) => (
            <button key={k} onClick={() => setSourceFilter(k)}
                    className={`px-2.5 py-1.5 rounded-lg text-[12px] ${sourceFilter === k ? "bg-white/10 font-semibold" : "text-zinc-500 hover:text-white"}`}>
              {l}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {data.items.length === 0 && (
          <div className="col-span-full text-center py-16 text-zinc-500 text-sm">
            Keine Fahrzeuge für diesen Filter.
          </div>
        )}
        {data.items.map((v) => {
          const d = v.data || {};
          const lc = v.lifecycle || "verglichen";
          const color = LIFECYCLE_COLORS[lc] || "#71717a";
          const img = (d.image_urls || [])[0];
          return (
            <div key={v.id} className="tactical-card overflow-hidden flex flex-col" data-testid={`bestand-${v.id}`}>
              {img && (
                <div className="h-36 overflow-hidden">
                  <img src={img} alt="" className="w-full h-full object-cover" />
                </div>
              )}
              <div className="p-4 flex-1 flex flex-col">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="font-semibold">{d.make_label} {d.model_label}</div>
                    <div className="text-xs text-zinc-500 line-clamp-1">{d.model_description}</div>
                  </div>
                  <span className="shrink-0 text-[11px] px-2 py-0.5 rounded-md border font-medium"
                        style={{ borderColor: `${color}66`, color, background: `${color}1a` }}>
                    {LIFECYCLE_LABELS[lc] || lc}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400">
                  {d.first_registration && <span>EZ {d.first_registration}</span>}
                  {d.mileage && <span>{Number(d.mileage).toLocaleString("de-DE")} km</span>}
                  {v.purchase_price != null && (
                    <span className="text-zinc-300">EK {Number(v.purchase_price).toLocaleString("de-DE")} €</span>
                  )}
                  <span className="text-zinc-600">{v.source === "manuell" ? "manuell" : "über System"}</span>
                </div>

                {lc === "bestand" && v.retention_days_left != null && (
                  <div className={`mt-2 inline-flex items-center gap-1.5 text-[11px] ${
                    v.retention_days_left <= 3 ? "text-red-400"
                    : v.retention_days_left <= 10 ? "text-amber-400" : "text-zinc-500"}`}>
                    <Clock size={12} />
                    {v.retention_days_left <= 10
                      ? `Fahrzeugdaten werden in ${v.retention_days_left} Tag(en) archiviert`
                      : `Noch ${v.retention_days_left} Tage im Bestand`}
                  </div>
                )}

                <div className="mt-3 pt-3 border-t flex flex-wrap gap-2" style={{ borderColor: "var(--border-default)" }}>
                  {lc === "abgeholt" ? (
                    <>
                      <button onClick={() => decide(v.id, "verkaufsentwurf")} disabled={busy === v.id}
                              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                              style={{ background: "var(--accent-red)" }}>
                        <Tag size={13} /> Speichern & weiterverkaufen
                      </button>
                      <button onClick={() => decide(v.id, "bestand")} disabled={busy === v.id}
                              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border text-zinc-200"
                              style={{ borderColor: "var(--border-default)" }}>
                        <Archive size={13} /> Nur speichern
                      </button>
                      <button onClick={() => decide(v.id, "loeschen")} disabled={busy === v.id}
                              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-zinc-500 hover:text-red-400">
                        <Trash2 size={13} /> Löschen
                      </button>
                    </>
                  ) : (
                    <>
                      {(lc === "bestand") && (
                        <button onClick={() => decide(v.id, "verkaufsentwurf")} disabled={busy === v.id}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                                style={{ background: "var(--accent-red)" }}>
                          <Tag size={13} /> Weiterverkaufen
                        </button>
                      )}
                      <Link to={`/app/akte/${v.id}`}
                            className="inline-flex items-center rounded-lg px-3 py-1.5 text-xs border text-zinc-300 hover:text-white"
                            style={{ borderColor: "var(--border-default)" }}>
                        Fahrzeugakte
                      </Link>
                    </>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {showManual && <ManualVehicleDialog onClose={() => setShowManual(false)} onDone={() => { setShowManual(false); load(); }} />}
    </div>
  );
}

function ManualVehicleDialog({ onClose, onDone }) {
  const [f, setF] = useState({
    make_label: "", model_label: "", model_description: "",
    first_registration: "", mileage: "", fuel_label: "", gearbox_label: "",
    power_ps: "", color: "", vin: "", previous_owners: "",
    features: "", description: "", purchase_price: "",
  });
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async () => {
    if (!f.make_label.trim() || !f.model_label.trim()) {
      toast.error("Marke und Modell sind Pflichtfelder"); return;
    }
    setBusy(true);
    try {
      await api.post("/vehicles/manual", {
        ...f,
        mileage: f.mileage ? parseInt(f.mileage, 10) : null,
        power_ps: f.power_ps ? parseInt(f.power_ps, 10) : null,
        power_kw: null,
        purchase_price: f.purchase_price ? parseFloat(f.purchase_price) : null,
        features: f.features.split(",").map((x) => x.trim()).filter(Boolean),
      });
      toast.success("Fahrzeug im Bestand angelegt");
      onDone?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };
  const Field = ({ label, children }) => (
    <div>
      <label className="text-[11px] text-zinc-500">{label}</label>
      {children}
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.7)" }}>
      <div className="w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-2xl p-5"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)" }}>
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-lg font-bold">Fahrzeug manuell hinzufügen</div>
            <div className="text-xs text-zinc-500">
              Für Fahrzeuge, die du bereits besitzt oder außerhalb der Plattform gekauft hast.
            </div>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Marke *"><input value={f.make_label} onChange={set("make_label")} className={inputCls} style={st} placeholder="BMW" /></Field>
          <Field label="Modell *"><input value={f.model_label} onChange={set("model_label")} className={inputCls} style={st} placeholder="320d" /></Field>
          <div className="col-span-2">
            <Field label="Modellbezeichnung"><input value={f.model_description} onChange={set("model_description")} className={inputCls} style={st} placeholder="320d Touring M Sport" /></Field>
          </div>
          <Field label="Erstzulassung"><input value={f.first_registration} onChange={set("first_registration")} className={inputCls} style={st} placeholder="03/2019" /></Field>
          <Field label="Kilometerstand"><input type="number" value={f.mileage} onChange={set("mileage")} className={inputCls} style={st} /></Field>
          <Field label="Kraftstoff"><input value={f.fuel_label} onChange={set("fuel_label")} className={inputCls} style={st} placeholder="Diesel" /></Field>
          <Field label="Getriebe"><input value={f.gearbox_label} onChange={set("gearbox_label")} className={inputCls} style={st} placeholder="Automatik" /></Field>
          <Field label="Leistung (PS)"><input type="number" value={f.power_ps} onChange={set("power_ps")} className={inputCls} style={st} /></Field>
          <Field label="Farbe"><input value={f.color} onChange={set("color")} className={inputCls} style={st} /></Field>
          <Field label="FIN"><input value={f.vin} onChange={set("vin")} className={inputCls} style={st} /></Field>
          <Field label="Vorbesitzer"><input value={f.previous_owners} onChange={set("previous_owners")} className={inputCls} style={st} /></Field>
          <div className="col-span-2">
            <Field label="Ausstattung (Komma-getrennt)"><input value={f.features} onChange={set("features")} className={inputCls} style={st} placeholder="Navi, LED, AHK" /></Field>
          </div>
          <div className="col-span-2">
            <Field label="Beschreibung"><textarea rows={3} value={f.description} onChange={set("description")} className={inputCls} style={st} /></Field>
          </div>
          <Field label="Einkaufspreis (€)"><input type="number" value={f.purchase_price} onChange={set("purchase_price")} className={inputCls} style={st} /></Field>
        </div>
        <button onClick={submit} disabled={busy}
                className="mt-4 w-full rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                style={{ background: "var(--accent-red)" }}>
          {busy ? "Wird angelegt…" : "In den Bestand aufnehmen"}
        </button>
      </div>
    </div>
  );
}
