import MonatJahrEingabe from "@/components/MonatJahrEingabe";
import { monatJahrFehler } from "@/lib/monatJahr";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useFeatures } from "@/lib/features";
import { toast } from "sonner";
import {
  Plus, AlertTriangle, Archive, Trash2, Tag, Clock, X,
} from "lucide-react";
import StatusSchild from "@/components/StatusSchild";
import { beschreibungLesbar, lifecycleText } from "@/lib/fahrzeugStatus";

/**
 * Fahrzeugbestand (B2B-Modul Phase 1).
 * - Entscheidungs-Warteschlange: abgeholte Fahrzeuge (Speichern / Verkaufen / Löschen)
 * - Bestand mit Lifecycle-Filter, 50-Tage-Countdown und Quellen-Kennzeichnung
 * - Manuelles Hinzufügen vorhandener Fahrzeuge (source: manuell)
 */

// Kilometer nur als echte Zahl anzeigen — Altdaten mit Text ergaben "NaN km"
// (Pruefbericht 20.09.2026, N17).
function kmText(n) {
  const zahl = typeof n === "number" ? n : Number(n);
  return Number.isFinite(zahl) && zahl > 0 ? `${zahl.toLocaleString("de-DE")} km` : "";
}

// Pruefbericht 20.09.2026 (B8): Field stand INNERHALB des Dialogs — jeder
// Tastendruck erzeugte einen neuen Komponententyp, React baute das Feld neu
// auf und der Fokus sprang heraus (ein Zeichen pro Klick, 14 Felder).
function Field({ label, children }) {
  return (
    <div>
      <label className="text-[11px] text-zinc-500">{label}</label>
      {children}
    </div>
  );
}

const FILTERS = [
  { key: "",                label: "Alle" },
  { key: "abgeholt",        label: "Entscheidung fällig" },
  { key: "bestand",         label: "Im Bestand" },
  { key: "verkaufsentwurf", label: "Entwürfe" },
  { key: "verkaufsbereit",  label: "Verkaufsbereit" },
  { key: "veroeffentlicht", label: "Auf dem Marktplatz" },
  { key: "reserviert",      label: "Reserviert" },
  { key: "verkauft",        label: "Verkauft" },
];

export default function Bestand() {
  const [data, setData] = useState({ items: [], counts: {} });
  const [filter, setFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [showManual, setShowManual] = useState(false);
  const [busy, setBusy] = useState(null);
  const nav = useNavigate();
  // Pruefbericht 20.09.2026 (M23/F8): Ladefehler NICHT als "Noch keine
  // Fahrzeuge" zeigen — sonst haelt der Nutzer seinen Bestand fuer weg.
  const [ladeFehler, setLadeFehler] = useState(null);
  const [geladen, setGeladen] = useState(false);
  // M20: Beim schnellen Filterwechsel gewinnt nur die LETZTE Anfrage —
  // eine langsamere aeltere Antwort darf die Liste nicht ueberschreiben.
  const anfrageNr = useRef(0);

  const load = useCallback(async () => {
    const nr = ++anfrageNr.current;
    try {
      const params = new URLSearchParams();
      if (filter) params.set("lifecycle", filter);
      if (sourceFilter) params.set("source", sourceFilter);
      const r = await api.get(`/bestand?${params.toString()}`);
      if (nr !== anfrageNr.current) return;
      setData(r.data || { items: [], counts: {} });
      setLadeFehler(null);
    } catch (e) {
      if (nr !== anfrageNr.current) return;
      setLadeFehler(errMsg(e, "Bestand konnte nicht geladen werden"));
    } finally {
      if (nr === anfrageNr.current) setGeladen(true);
    }
  }, [filter, sourceFilter]);

  useEffect(() => { load(); }, [load]);

  const features = useFeatures();          // Go-Live-Schalter (15.09.2026)
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
        // N14: innerhalb der App navigieren statt die Seite neu zu laden.
        if (draft?.data?.id) nav(`/app/inserat/${draft.data.id}`);
        else load();
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

  // Offene Marktplatz-Anfragen (nur Chef — der Endpunkt ist dealer-only,
  // Sucher bekommen 403 und sehen still kein Banner).
  const { user } = useAuth();
  const [anfragenOffen, setAnfragenOffen] = useState(0);
  useEffect(() => {
    if (user?.role !== "dealer") return;
    api.get("/dealer/interessen", { params: { status: "offen" } })
      .then((r) => setAnfragenOffen(Array.isArray(r.data) ? r.data.length : 0))
      .catch(() => {});
  }, [user?.role]);

  const counts = data.counts || {};
  const pending = (counts["abgeholt"] || 0);
  // Runde 19: Entscheidungen und manuelles Anlegen sind Chefsache (Backend:
  // current_haendler) — Sucher sehen die Knoepfe nicht mehr (vorher 403 erst beim Klick).
  const chef = user?.role === "dealer";

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-7xl mx-auto" data-testid="bestand-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="overline">Bestand</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
            Fahrzeugbestand & Weiterverkauf
          </h1>
          {/* Runde 32 (Wunsch Ahmad): nur Autos mit gespeichertem oder
              verschicktem Kaufvertrag, dazu von Hand hinzugefuegte. */}
          <div className="text-[13px] mt-1" style={{ color: "var(--text-muted)" }}
               data-testid="bestand-regel">
            Fahrzeuge mit gespeichertem oder verschicktem Kaufvertrag und von Hand hinzugefügte.
          </div>
        </div>
        {chef && (
          <button onClick={() => setShowManual(true)}
                  className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                  style={{ background: "var(--accent-red)" }}>
            <Plus size={16} /> Fahrzeug manuell hinzufügen
          </button>
        )}
      </div>

      {pending > 0 && (
        <div className="mt-4 rounded-xl border px-4 py-3 flex items-center gap-2 text-sm"
             style={{ borderColor: "#f59e0b55", background: "#f59e0b14", color: "var(--tx-amber)" }}>
          <AlertTriangle size={16} />
          {pending} abgeholte(s) Fahrzeug(e) warten auf deine Entscheidung.
        </div>
      )}

      {anfragenOffen > 0 && features.marktplatz && (
        <Link to="/app/anfragen" data-testid="bestand-anfragen-banner"
              className="mt-4 rounded-xl border px-4 py-3 flex items-center gap-2 text-sm hover:bg-sky-500/10 transition"
              style={{ borderColor: "#38bdf855", background: "#38bdf814", color: "var(--tx-cyan)" }}>
          <AlertTriangle size={16} />
          {anfragenOffen} offene Kaufanfrage(n) vom Marktplatz — jetzt beantworten ›
        </Link>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {FILTERS.filter((f) => f.key !== "veroeffentlicht" || features.marktplatz).map((f) => (
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

      {/* Runde 27: Die Liste endet bei 500 Fahrzeugen — das sagen wir jetzt,
          statt aeltere Autos stillschweigend wegzulassen. */}
      {data.gekuerzt && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm"
             data-testid="bestand-gekuerzt"
             style={{ borderColor: "#f59e0b55", background: "#f59e0b14", color: "var(--tx-amber)" }}>
          Es werden {(data.items || []).length} von {data.gesamt} Fahrzeugen angezeigt.
          Nutze die Filter oben, um ältere Fahrzeuge zu finden.
        </div>
      )}
      {ladeFehler && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex flex-wrap items-center gap-3" role="alert"
             data-testid="bestand-ladefehler"
             style={{ borderColor: "#ef444455", background: "#ef444414", color: "var(--text-primary)" }}>
          <AlertTriangle size={16} />
          <span className="flex-1 min-w-0">{ladeFehler}</span>
          <button type="button" onClick={load} className="rounded-lg px-3 py-1.5 text-xs border font-semibold"
                  style={{ borderColor: "var(--border-default)" }}>
            Erneut versuchen
          </button>
        </div>
      )}
      <div className="mt-4 grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {!geladen && !ladeFehler && (
          <div className="col-span-full text-center py-16 text-zinc-500 text-sm">lade…</div>
        )}
        {geladen && !ladeFehler && (data.items || []).length === 0 && (
          <div className="col-span-full text-center py-16 text-zinc-500 text-sm">
            {filter || sourceFilter
              ? "Keine Fahrzeuge für diesen Filter."
              : "Noch keine Fahrzeuge im Bestand. Ein Auto erscheint hier, sobald ein Kaufvertrag gespeichert oder verschickt ist — oder wenn du es von Hand hinzufügst."}
          </div>
        )}
        {(data.items || []).map((v) => {
          const d = v.data || {};
          const lc = v.lifecycle || "verglichen";
          const img = (d.image_urls || d.images || [])[0];
          return (
            <div key={v.id} className="tactical-card overflow-hidden flex flex-col" data-testid={`bestand-${v.id}`}>
              {img && (
                <div className="h-36 overflow-hidden">
                  <img src={img} alt="" className="w-full h-full object-cover" />
                </div>
              )}
              <div className="p-4 flex-1 flex flex-col">
                {/* min-w-0: sonst drueckt ein langer Untertitel das
                    Status-Schild aus der Karte (11.09.2026). */}
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="font-semibold line-clamp-2 break-words">{d.make_label} {d.model_label}</div>
                    {d.model_description && (
                      <div className="mt-0.5 text-xs text-zinc-500 line-clamp-2 break-words"
                           title={d.model_description} data-testid={`bestand-beschreibung-${v.id}`}>
                        {beschreibungLesbar(d.model_description)}
                      </div>
                    )}
                  </div>
                  <StatusSchild status={lc} text={lifecycleText(lc)} data-testid={`bestand-status-${v.id}`} />
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                  {d.first_registration && <span>EZ {d.first_registration}</span>}
                  {kmText(d.mileage) && <span>{kmText(d.mileage)}</span>}
                  {v.purchase_price != null && (
                    <span className="text-zinc-300">EK {Number(v.purchase_price).toLocaleString("de-DE")} €</span>
                  )}
                  <span className="text-zinc-600">{v.source === "manuell" ? "manuell" : "über System"}</span>
                  {v.owner_name && <span className="text-zinc-500" data-testid={`bestand-owner-${v.id}`}>Bearbeiter: {v.owner_name}{v.mitbearbeiter_namen?.length > 0 ? ` · mit ${v.mitbearbeiter_namen.join(", ")}` : ""}</span>}
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
                      {chef && (<>
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
                      </>)}
                      {/* Runde 21: gerade frisch abgeholte Fahrzeuge brauchen den Weg
                          zum Abholbericht mit den Fahrerfotos — vorher fehlte er hier. */}
                      <Link to={`/app/akte/${v.id}`} data-testid={`akte-link-${v.id}`}
                            className="inline-flex items-center rounded-lg px-3 py-1.5 text-xs border hover:opacity-80"
                            style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
                        Fahrzeugakte · Abholbericht
                      </Link>
                    </>
                  ) : (
                    <>
                      {chef && (lc === "bestand") && (
                        <button onClick={() => decide(v.id, "verkaufsentwurf")} disabled={busy === v.id}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                                style={{ background: "var(--accent-red)" }}>
                          <Tag size={13} /> Weiterverkaufen
                        </button>
                      )}
                      {/* Ab Vertragserstellung sofort inserierbar — die Abholung
                          läuft parallel weiter (Bericht landet in der Akte). */}
                      {chef && features.marktplatz && ["vertrag_erstellt", "gekauft", "abholung_geplant"].includes(lc) && (
                        <button onClick={() => decide(v.id, "verkaufsentwurf")} disabled={busy === v.id}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                                style={{ background: "var(--accent-red)" }}>
                          <Tag size={13} /> Jetzt inserieren
                        </button>
                      )}
                      <Link to={`/app/akte/${v.id}`}
                            className="inline-flex items-center rounded-lg px-3 py-1.5 text-xs border hover:opacity-80"
                            style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
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
    const ezFehler = monatJahrFehler(f.first_registration);
    if (ezFehler) { toast.error(`Erstzulassung: ${ezFehler}`); return; }
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.7)" }}>
      <div className="w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-2xl p-5"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)" }}>
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
          <Field label="Erstzulassung"><MonatJahrEingabe value={f.first_registration} onChange={(v) => set("first_registration")({ target: { value: v } })} art="ez" testid="manuell-ez" className={inputCls} style={st} /></Field>
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
