import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowLeft, AlertTriangle, Clock, Tag, Archive, Trash2, FileText,
} from "lucide-react";

/**
 * Durchgehende Fahrzeugakte: Beschaffung · Kauf · Abholung (mit Abweichungs-
 * Diff) · Bestand · Verkauf · Historie. Kein Wissen über das Fahrzeug geht
 * verloren — alles an einem Ort.
 */

const fmtDate = (s) => {
  if (!s) return "—";
  try { return new Date(s).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return s; }
};
const fmtEur = (n) => (n == null ? "—" : `${Number(n).toLocaleString("de-DE")} €`);

export default function FahrzeugAkte() {
  const { id } = useParams();
  const nav = useNavigate();
  const [akte, setAkte] = useState(null);
  const [selectedDevs, setSelectedDevs] = useState([]);
  const [bestandForm, setBestandForm] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await api.get(`/vehicles/${id}/akte`);
      setAkte(r.data);
      const b = r.data.vehicle?.bestand || {};
      setBestandForm({
        location: b.location || "", notes: b.notes || "",
        costs: b.costs || [],
      });
    } catch (e) {
      toast.error(errMsg(e, "Akte konnte nicht geladen werden"));
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  if (!akte) return <div className="p-10 text-zinc-500 text-sm">lade…</div>;

  const v = akte.vehicle;
  const d = v.data || {};
  const report = akte.pickup_report;
  const deviations = report?.deviations || [];
  const listing = (akte.listings || [])[0];

  const decide = async (decision) => {
    if (busy) return;
    if (decision === "loeschen" &&
        !window.confirm("Fahrzeug wirklich löschen?\nFotos werden entfernt — Vertrag und Historie bleiben erhalten.")) return;
    setBusy(true);
    try {
      await api.post(`/vehicles/${v.id}/decision`, { decision });
      if (decision === "verkaufsentwurf") {
        const draft = await api.post(`/resale/draft/${v.id}`);
        nav(`/app/inserat/${draft.data.id}`);
        return;
      }
      toast.success("Gespeichert");
      load();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };

  const applyDeviations = async () => {
    if (!selectedDevs.length) { toast.error("Bitte Abweichungen auswählen"); return; }
    try {
      const r = await api.post(`/vehicles/${v.id}/apply-deviations`, { deviation_ids: selectedDevs });
      toast.success(`${r.data.applied.length} Änderung(en) übernommen`);
      setSelectedDevs([]);
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const saveBestand = async () => {
    try {
      await api.put(`/vehicles/${v.id}/bestand`, bestandForm);
      toast.success("Bestandsdaten gespeichert");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const Section = ({ title, children, warn }) => (
    <div className="tactical-card p-4 mt-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-1 h-4 rounded" style={{ background: "var(--accent-red)" }} />
        <div className="text-sm font-bold uppercase tracking-wide">{title}</div>
        {warn}
      </div>
      {children}
    </div>
  );

  const KV = ({ k, val }) => (
    <div className="flex justify-between gap-4 py-1 border-b text-sm" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
      <span className="text-zinc-500">{k}</span>
      <span className="text-right">{val ?? "—"}</span>
    </div>
  );

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="akte-page">
      <Link to="/app/bestand" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
        <ArrowLeft size={14} /> Zurück zum Bestand
      </Link>
      <div className="mt-2 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="overline">Fahrzeugakte · {v.source === "manuell" ? "manuell angelegt" : "über System beschafft"}</div>
          <h1 className="font-display font-black text-2xl lg:text-3xl tracking-tighter mt-1">
            {d.make_label} {d.model_label} <span className="text-zinc-500 font-normal text-xl">{d.model_description}</span>
          </h1>
          <div className="mt-1 text-xs text-zinc-500">
            Status: <b className="text-zinc-300">{v.lifecycle}</b>
            {akte.retention_days_left != null && (
              <span className={akte.retention_days_left <= 10 ? "text-amber-400" : ""}>
                {" "}· <Clock size={11} className="inline" /> noch {akte.retention_days_left} Tage im Bestand
              </span>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {v.lifecycle === "abgeholt" && (
            <>
              <button onClick={() => decide("verkaufsentwurf")} className="rounded-lg px-3 py-2 text-xs font-semibold text-white inline-flex items-center gap-1.5" style={{ background: "var(--accent-red)" }}>
                <Tag size={13} /> Speichern & weiterverkaufen
              </button>
              <button onClick={() => decide("bestand")} className="rounded-lg px-3 py-2 text-xs border inline-flex items-center gap-1.5" style={{ borderColor: "var(--border-default)" }}>
                <Archive size={13} /> Nur speichern
              </button>
              <button onClick={() => decide("loeschen")} className="rounded-lg px-3 py-2 text-xs text-zinc-500 hover:text-red-400 inline-flex items-center gap-1.5">
                <Trash2 size={13} /> Löschen
              </button>
            </>
          )}
          {v.lifecycle === "bestand" && (
            <button onClick={() => decide("verkaufsentwurf")} className="rounded-lg px-3 py-2 text-xs font-semibold text-white inline-flex items-center gap-1.5" style={{ background: "var(--accent-red)" }}>
              <Tag size={13} /> Weiterverkaufen
            </button>
          )}
          {/* Ab Vertragserstellung sofort inserierbar (Abholung läuft parallel) */}
          {["vertrag_erstellt", "gekauft", "abholung_geplant"].includes(v.lifecycle) && (
            <button onClick={() => decide("verkaufsentwurf")} className="rounded-lg px-3 py-2 text-xs font-semibold text-white inline-flex items-center gap-1.5" style={{ background: "var(--accent-red)" }}>
              <Tag size={13} /> Jetzt inserieren
            </button>
          )}
          {listing && ["entwurf", "verkaufsbereit", "reserviert"].includes(listing.status) && (
            <Link to={`/app/inserat/${listing.id}`} className="rounded-lg px-3 py-2 text-xs border inline-flex items-center gap-1.5" style={{ borderColor: "var(--border-default)" }}>
              Inserat öffnen ({listing.status})
            </Link>
          )}
        </div>
      </div>

      {/* Abholung + Diff */}
      {report && (
        <Section
          title="Abholung"
          warn={deviations.length > 0 && (
            <span className="ml-auto inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md" style={{ background: "#f59e0b1c", color: "#fbbf24" }}>
              <AlertTriangle size={12} /> {deviations.length} Abweichung(en)
            </span>
          )}
        >
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm mb-3">
            <div><div className="text-[11px] text-zinc-500">Fahrer</div>{report.driver_name || "—"}</div>
            <div><div className="text-[11px] text-zinc-500">km bei Abholung</div>{report.mileage_at_pickup?.toLocaleString("de-DE") || "—"}</div>
            <div><div className="text-[11px] text-zinc-500">Schlüssel</div>{report.keys_count ?? "—"}</div>
            <div><div className="text-[11px] text-zinc-500">Tank</div>{report.fuel_level || "—"}</div>
          </div>
          {deviations.length > 0 && (
            <>
              <div className="text-xs text-zinc-400 mb-2">
                Ursprüngliche Daten vs. bei Abholung festgestellt — auswählen und übernehmen:
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left overline">
                    <th className="py-2 pr-2 w-8"></th>
                    <th className="py-2 pr-2">Abweichung</th>
                    <th className="py-2 pr-2 text-right">Beim Einkauf</th>
                    <th className="py-2 text-right">Bei Abholung</th>
                  </tr>
                </thead>
                <tbody>
                  {deviations.map((dev) => (
                    <tr key={dev.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                      <td className="py-2 pr-2">
                        <input type="checkbox"
                               checked={selectedDevs.includes(dev.id)}
                               onChange={(e) => setSelectedDevs((s) =>
                                 e.target.checked ? [...s, dev.id] : s.filter((x) => x !== dev.id))} />
                      </td>
                      <td className="py-2 pr-2">
                        {dev.label}
                        {dev.photo_key && (
                          <a href={`${process.env.REACT_APP_BACKEND_URL}/api/files/${dev.photo_key}`}
                             target="_blank" rel="noreferrer" className="ml-2 text-xs text-sky-400 hover:underline">Foto</a>
                        )}
                      </td>
                      <td className="py-2 pr-2 text-right text-zinc-400">{dev.expected || "—"}</td>
                      <td className="py-2 text-right font-medium">{dev.actual || "Ja"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button onClick={applyDeviations}
                      className="mt-3 rounded-lg px-3 py-2 text-xs font-semibold text-white"
                      style={{ background: "var(--accent-red)" }}>
                Änderungen übernehmen ({selectedDevs.length})
              </button>
              {v.deviations_applied_at && (
                <span className="ml-3 text-[11px] text-zinc-500">zuletzt übernommen: {fmtDate(v.deviations_applied_at)}</span>
              )}
            </>
          )}
          {report.notes && <div className="mt-3 text-xs text-zinc-400">Bemerkung Fahrer: {report.notes}</div>}
        </Section>
      )}

      {/* Fahrzeugdaten + Kauf */}
      <div className="grid md:grid-cols-2 gap-4">
        <Section title="Fahrzeugdaten">
          <KV k="Erstzulassung" val={d.first_registration} />
          <KV k="Kilometerstand" val={d.mileage ? `${Number(d.mileage).toLocaleString("de-DE")} km` : null} />
          <KV k="Kraftstoff" val={d.fuel_label || d.fuel} />
          <KV k="Getriebe" val={d.gearbox_label || d.gearbox} />
          <KV k="Leistung" val={d.power_ps ? `${d.power_ps} PS` : null} />
          <KV k="Farbe" val={d.color} />
          <KV k="FIN" val={d.vin} />
          {(v.known_defects || []).length > 0 && (
            <div className="mt-2 text-xs">
              <div className="text-zinc-500 mb-1">Bekannte Mängel:</div>
              {v.known_defects.map((m, i) => <div key={i} className="text-amber-400">• {m}</div>)}
            </div>
          )}
        </Section>
        <Section title="Beschaffung & Kauf">
          <KV k="Einkaufspreis" val={fmtEur(v.purchase_price)} />
          <KV k="Quelle" val={v.source === "manuell" ? "Manuell angelegt" : (d.detail_url ? "Inserat (Plattform)" : "Plattform")} />
          {(akte.appointments || []).slice(0, 1).map((a) => (
            <KV key={a.id} k="Geplante Abholung"
                val={`${a.pickup_date || "—"}${a.pickup_time ? ` · ${a.pickup_time}` : ""} (${a.status || "offen"})`} />
          ))}
          {akte.contracts.map((c) => (
            <div key={c.id} className="mt-2 flex items-center justify-between text-sm">
              <span className="inline-flex items-center gap-1.5 text-zinc-300">
                <FileText size={13} /> Kaufvertrag {c.contract_no || c.id.slice(0, 8)}
              </span>
              <span className="text-zinc-500 text-xs">{fmtDate(c.created_at)}</span>
            </div>
          ))}
          {akte.comparisons.length > 0 && (
            <div className="mt-2 text-xs text-zinc-500">{akte.comparisons.length} Vergleich(e) durchgeführt</div>
          )}
        </Section>
      </div>

      {/* Bestand */}
      {bestandForm && ["bestand", "verkaufsentwurf", "verkaufsbereit", "abgeholt", "reserviert"].includes(v.lifecycle) && (
        <Section title="Bestand · Standort & Kosten">
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] text-zinc-500">Standort</label>
              <input value={bestandForm.location}
                     onChange={(e) => setBestandForm((s) => ({ ...s, location: e.target.value }))}
                     className="w-full rounded-lg border bg-transparent px-3 py-2 text-sm"
                     style={{ borderColor: "var(--border-default)" }} placeholder="z.B. Platz 4" />
            </div>
            <div>
              <label className="text-[11px] text-zinc-500">Interne Notizen</label>
              <input value={bestandForm.notes}
                     onChange={(e) => setBestandForm((s) => ({ ...s, notes: e.target.value }))}
                     className="w-full rounded-lg border bg-transparent px-3 py-2 text-sm"
                     style={{ borderColor: "var(--border-default)" }} />
            </div>
          </div>
          <div className="mt-3">
            <div className="flex items-center justify-between">
              <label className="text-[11px] text-zinc-500">Kosten (Transport, Aufbereitung, Reparatur, …)</label>
              <button onClick={() => setBestandForm((s) => ({ ...s, costs: [...s.costs, { label: "", amount: 0 }] }))}
                      className="text-xs text-zinc-400 hover:text-white">+ Kostenposition</button>
            </div>
            {bestandForm.costs.map((c, i) => (
              <div key={i} className="mt-1.5 flex gap-2">
                <input value={c.label} placeholder="Bezeichnung"
                       onChange={(e) => setBestandForm((s) => ({ ...s, costs: s.costs.map((x, xi) => xi === i ? { ...x, label: e.target.value } : x) }))}
                       className="flex-1 rounded-lg border bg-transparent px-3 py-1.5 text-sm" style={{ borderColor: "var(--border-default)" }} />
                <input type="number" value={c.amount} placeholder="€"
                       onChange={(e) => setBestandForm((s) => ({ ...s, costs: s.costs.map((x, xi) => xi === i ? { ...x, amount: parseFloat(e.target.value || 0) } : x) }))}
                       className="w-28 rounded-lg border bg-transparent px-3 py-1.5 text-sm text-right" style={{ borderColor: "var(--border-default)" }} />
                <button onClick={() => setBestandForm((s) => ({ ...s, costs: s.costs.filter((_, xi) => xi !== i) }))}
                        className="text-zinc-500 hover:text-red-400"><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
          <button onClick={saveBestand} className="mt-3 rounded-lg px-3 py-2 text-xs border font-semibold"
                  style={{ borderColor: "var(--border-default)" }}>
            Bestandsdaten speichern
          </button>
        </Section>
      )}

      {/* Historie */}
      <Section title="Historie">
        <div className="space-y-1 max-h-64 overflow-y-auto">
          {akte.history.map((h) => (
            <div key={h.id} className="flex justify-between gap-4 text-xs py-1 border-b" style={{ borderColor: "rgba(255,255,255,0.04)" }}>
              <span className="text-zinc-300">{h.action}</span>
              <span className="text-zinc-600 whitespace-nowrap">{fmtDate(h.created_at)}</span>
            </div>
          ))}
          {akte.history.length === 0 && <div className="text-xs text-zinc-500">Noch keine Einträge.</div>}
        </div>
      </Section>
    </div>
  );
}
