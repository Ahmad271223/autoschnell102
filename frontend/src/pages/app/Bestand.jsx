import MonatJahrEingabe from "@/components/MonatJahrEingabe";
import { monatJahrFehler } from "@/lib/monatJahr";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useFeatures } from "@/lib/features";
import { toast } from "sonner";
import {
  Plus, AlertTriangle, Archive, Trash2, Tag, Clock, X, Pencil,
} from "lucide-react";
import StatusSchild from "@/components/StatusSchild";
import { beschreibungLesbar, lifecycleText } from "@/lib/fahrzeugStatus";
import { kmAusText, preisAusText } from "@/lib/preis";
import { betragAlsText, fristErneuertText } from "@/lib/bestandForm";
import { termineOffenDetail, termineStornoFrage } from "@/lib/akteHinweise";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";

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
  // Rollenprüfung 22.09.2026 (RP-411): manuelles Fahrzeug in Bearbeitung
  const [bearbeiten, setBearbeiten] = useState(null);
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
  const decide = async (vehicleId, decision, vonLifecycle) => {
    if (busy) return;
    if (decision === "loeschen" &&
        !window.confirm("Fahrzeug wirklich löschen?\nFotos werden entfernt — Vertrag und Historie bleiben erhalten.")) {
      return;
    }
    setBusy(vehicleId);
    try {
      if (decision === "verkaufsentwurf") {
        // Rollenprüfung 22.09.2026 (RP-092/RP-191/RP-342): EIN Aufruf statt
        // zwei. create_draft setzt den Fahrzeugstatus selbst (und nimmt ihn
        // bei einem Fehler zurück) — vorher blieb nach einem Abbruch
        // zwischen /decision und /resale/draft ein "Verkaufsentwurf" ohne
        // Inserat und ohne Knopf zurück.
        const draft = await api.post(`/resale/draft/${vehicleId}`);
        toast.success("Inseratsentwurf erstellt");
        // N14: innerhalb der App navigieren statt die Seite neu zu laden.
        if (draft?.data?.id) nav(`/app/inserat/${draft.data.id}`);
        else load();
        return;
      }
      // RP-496: der angezeigte Zustand geht mit (409, wenn ein anderer Tab
      // das Fahrzeug inzwischen inseriert hat).
      const body = { decision, von_lifecycle: vonLifecycle };
      let r;
      try {
        r = await api.post(`/vehicles/${vehicleId}/decision`, body);
      } catch (e) {
        // RP-454 (Welle B2): offene Abholtermine — der Chef sieht sie (Datum,
        // Fahrer) und entscheidet, ob sie mit dem Fahrzeug storniert werden.
        // Nie still: erst nach dem Ja geht termine_stornieren=true raus.
        const offen = termineOffenDetail(e);
        if (!offen || !window.confirm(termineStornoFrage(offen))) throw e;
        r = await api.post(`/vehicles/${vehicleId}/decision`, { ...body, termine_stornieren: true });
      }
      toast.success(r.data?.verlaengert
        ? fristErneuertText(r.data?.expires_at)
        : decision === "bestand"
          ? "Ins Bestand übernommen (50 Tage Aufbewahrung)"
          : r.data?.termine_storniert?.length
            ? `Fahrzeug gelöscht, ${r.data.termine_storniert.length} Termin(e) storniert`
            : "Fahrzeug gelöscht");
      load();
    } catch (e) {
      toast.error(errMsg(e));
      if (e?.response?.status === 409) load();
    } finally {
      setBusy(null);
    }
  };

  // Prüfbericht 20.09. U-44: Sucher haben hier keine Chef-Entscheidungen,
  // aber wie in der Akte "Aus meiner Liste entfernen" — verschwindet nur bei
  // ihnen, der Chef behält Fahrzeug, Vertrag, Termine und Historie.
  const entfernen = async (vehicleId) => {
    if (busy) return;
    if (!window.confirm("Fahrzeug aus deiner Liste entfernen?\n"
        + "Es verschwindet nur bei dir — der Chef behält Fahrzeug, Vertrag, Termine und Historie.")) return;
    setBusy(vehicleId);
    try {
      await api.post(`/vehicles/${vehicleId}/entfernen`);
      toast.success("Aus deiner Liste entfernt — der Chef behält das Fahrzeug");
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
  // Rollenprüfung 22.09.2026 (RP-450): Fahrzeuge, deren Bestandsfrist in den
  // nächsten 7 Tagen endet — danach archiviert der Aufräumer sie endgültig.
  const baldArchiviert = (data.items || []).filter((v) => v.lifecycle === "bestand"
    && v.retention_days_left != null && v.retention_days_left <= 7).length;
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
          {/* Prüfbericht 20.09. U-44: Sucher entscheiden nicht (Knöpfe sind
              chef-gated) — der Hinweis sagt ihnen, dass der Chef dran ist. */}
          {pending} abgeholte(s) Fahrzeug(e) warten auf {chef ? "deine Entscheidung" : "die Entscheidung des Chefs"}.
        </div>
      )}

      {chef && baldArchiviert > 0 && (
        <div className="mt-4 rounded-xl border px-4 py-3 flex items-center gap-2 text-sm"
             data-testid="bestand-frist-hinweis"
             style={{ borderColor: "#f59e0b55", background: "#f59e0b14", color: "var(--tx-amber)" }}>
          <Clock size={16} />
          {/* Rollenprüfung 22.09.2026 (Review): der Server setzt die Frist auf
              50 Tage AB HEUTE (nicht alte Frist + 50) — Knopf und Hinweis sagen
              das jetzt so; dazu Einzahl/Mehrzahl ("Stehen sie ..."). */}
          {baldArchiviert === 1
            ? "Ein Fahrzeug wird in den nächsten 7 Tagen archiviert (Fotos werden gelöscht). Steht es noch auf dem Hof?"
            : `${baldArchiviert} Fahrzeuge werden in den nächsten 7 Tagen archiviert (Fotos werden gelöscht). Stehen sie noch auf dem Hof?`}
          {" "}Dann „Frist erneuern“ wählen — sie gilt danach 50 Tage ab heute.
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
                      {/* Rollenprüfung 22.09.2026 (RP-045/RP-144): auch hier nur
                          mit freigeschaltetem Marktplatz — sonst endete der
                          Klick in "Demnächst verfügbar" (503). */}
                      {features.marktplatz && (
                        <button onClick={() => decide(v.id, "verkaufsentwurf", lc)} disabled={busy === v.id}
                                data-testid={`bestand-weiterverkaufen-${v.id}`}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                                style={{ background: "var(--accent-red)" }}>
                          <Tag size={13} /> Speichern & weiterverkaufen
                        </button>
                      )}
                      <button onClick={() => decide(v.id, "bestand", v.lifecycle)} disabled={busy === v.id}
                              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border text-zinc-200"
                              style={{ borderColor: "var(--border-default)" }}>
                        <Archive size={13} /> Nur speichern
                      </button>
                      <button onClick={() => decide(v.id, "loeschen", v.lifecycle)} disabled={busy === v.id}
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
                      {/* Rollenprüfung 22.09.2026: "Weiterverkaufen" nur mit
                          freigeschaltetem Marktplatz (wie in der Akte) — sonst
                          endete der Klick in "Demnächst verfügbar" (503). */}
                      {chef && features.marktplatz && (lc === "bestand") && (
                        <button onClick={() => decide(v.id, "verkaufsentwurf", lc)} disabled={busy === v.id}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                                style={{ background: "var(--accent-red)" }}>
                          <Tag size={13} /> Weiterverkaufen
                        </button>
                      )}
                      {/* Rollenprüfung 22.09.2026 (RP-450): Frist verlängern,
                          bevor der Aufräumer das Fahrzeug archiviert.
                          Review 22.09.: die neue Frist ist 50 Tage ab heute,
                          nicht die alte Frist + 50 — so steht es jetzt da. */}
                      {chef && lc === "bestand" && (
                        <button onClick={() => decide(v.id, "bestand", v.lifecycle)} disabled={busy === v.id}
                                data-testid={`bestand-verlaengern-${v.id}`}
                                title="Setzt die Frist neu auf 50 Tage ab heute"
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border text-zinc-200"
                                style={{ borderColor: "var(--border-default)" }}>
                          <Clock size={13} /> Frist erneuern (50 Tage ab heute)
                        </button>
                      )}
                      {/* RP-411: von Hand angelegte Fahrzeuge lassen sich
                          korrigieren und — solange sie nur im Bestand stehen —
                          auch wieder löschen. */}
                      {chef && v.source === "manuell" && !["verkauft", "archiviert", "geloescht"].includes(lc) && (
                        <button onClick={() => setBearbeiten(v)} disabled={busy === v.id}
                                data-testid={`bestand-bearbeiten-${v.id}`}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border text-zinc-200"
                                style={{ borderColor: "var(--border-default)" }}>
                          <Pencil size={13} /> Bearbeiten
                        </button>
                      )}
                      {chef && v.source === "manuell" && lc === "bestand" && (
                        <button onClick={() => decide(v.id, "loeschen", v.lifecycle)} disabled={busy === v.id}
                                data-testid={`bestand-loeschen-${v.id}`}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-zinc-500 hover:text-red-400">
                          <Trash2 size={13} /> Löschen
                        </button>
                      )}
                      {/* RP-191/RP-342: "Verkaufsentwurf" ohne geöffnetes
                          Inserat — öffnet das vorhandene oder legt es an. */}
                      {chef && features.marktplatz && lc === "verkaufsentwurf" && (
                        <button onClick={() => decide(v.id, "verkaufsentwurf", lc)} disabled={busy === v.id}
                                data-testid={`bestand-inserat-${v.id}`}
                                className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                                style={{ background: "var(--accent-red)" }}>
                          <Tag size={13} /> Zum Inserat
                        </button>
                      )}
                      {/* Ab Vertragserstellung sofort inserierbar — die Abholung
                          läuft parallel weiter (Bericht landet in der Akte). */}
                      {chef && features.marktplatz && ["vertrag_erstellt", "gekauft", "abholung_geplant"].includes(lc) && (
                        <button onClick={() => decide(v.id, "verkaufsentwurf", lc)} disabled={busy === v.id}
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
                  {/* U-44: Sucher — nur aus der eigenen Liste entfernen (wie in der Akte) */}
                  {!chef && (
                    <button onClick={() => entfernen(v.id)} disabled={busy === v.id}
                            data-testid={`bestand-sucher-entfernen-${v.id}`}
                            className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-zinc-500 hover:text-red-400 disabled:opacity-50">
                      <Trash2 size={13} /> Aus meiner Liste entfernen
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {showManual && <ManualVehicleDialog onClose={() => setShowManual(false)} onDone={() => { setShowManual(false); load(); }} />}
      {bearbeiten && (
        <ManualVehicleDialog fahrzeug={bearbeiten} onClose={() => setBearbeiten(null)}
                             onDone={() => { setBearbeiten(null); load(); }} />
      )}
    </div>
  );
}

/** Formular eines manuellen Fahrzeugs (leer oder aus einem vorhandenen). */
export function manuellFormAus(fahrzeug) {
  const d = fahrzeug?.data || {};
  const text = (x) => (x === null || x === undefined ? "" : String(x));
  return {
    make_label: text(d.make_label), model_label: text(d.model_label),
    model_description: text(d.model_description),
    first_registration: text(d.first_registration),
    mileage: typeof d.mileage === "number" ? d.mileage.toLocaleString("de-DE") : text(d.mileage),
    fuel_label: text(d.fuel_label), gearbox_label: text(d.gearbox_label),
    power_ps: text(d.power_ps), color: text(d.color), vin: text(d.vin),
    previous_owners: text(d.previous_owners),
    features: Array.isArray(d.features) ? d.features.join(", ") : text(d.features),
    description: text(d.description),
    purchase_price: betragAlsText(fahrzeug?.purchase_price),
  };
}

/**
 * Rollenprüfung 22.09.2026 (RP-411/RP-146): Kilometer, PS und Einkaufspreis
 * deutsch lesen. Vorher machten parseInt/parseFloat aus "150.000 km" 150 km
 * und aus "12.990 €" 12,99 € — und korrigieren ließ es sich nicht.
 * Liefert { payload, fehler }. power_kw bleibt beim Bearbeiten erhalten.
 */
export function manuellPayload(f, fahrzeug = null) {
  if (!String(f.make_label || "").trim() || !String(f.model_label || "").trim()) {
    return { payload: null, fehler: "Marke und Modell sind Pflichtfelder" };
  }
  const km = kmAusText(f.mileage);
  if (Number.isNaN(km)) {
    return { payload: null, fehler: "Kilometerstand: bitte als ganze Zahl eingeben, z. B. 150.000" };
  }
  const psRoh = String(f.power_ps ?? "").trim();
  if (psRoh && !/^\d{1,4}$/.test(psRoh)) {
    return { payload: null, fehler: "Leistung: bitte die PS als ganze Zahl eingeben, z. B. 150" };
  }
  const ekRoh = String(f.purchase_price ?? "").trim();
  const ek = ekRoh ? preisAusText(ekRoh) : null;
  if (ekRoh && ek === null) {
    return { payload: null, fehler: "Einkaufspreis: bitte als Betrag eingeben, z. B. 12.990 oder 12.990,50" };
  }
  const altKw = fahrzeug?.data?.power_kw;
  return {
    fehler: null,
    payload: {
      ...f,
      make_label: f.make_label.trim(), model_label: f.model_label.trim(),
      mileage: km,
      power_ps: psRoh ? Number(psRoh) : null,
      power_kw: typeof altKw === "number" ? altKw : null,
      purchase_price: ek,
      features: String(f.features || "").split(",").map((x) => x.trim()).filter(Boolean),
    },
  };
}

function ManualVehicleDialog({ fahrzeug = null, onClose, onDone }) {
  const [f, setF] = useState(() => manuellFormAus(fahrzeug));
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));
  const bearbeiten = Boolean(fahrzeug?.id);

  const submit = async () => {
    const { payload, fehler } = manuellPayload(f, fahrzeug);
    if (fehler) { toast.error(fehler); return; }
    const ezFehler = monatJahrFehler(f.first_registration);
    if (ezFehler) { toast.error(`Erstzulassung: ${ezFehler}`); return; }
    setBusy(true);
    try {
      if (bearbeiten) {
        // RP-411: PUT /vehicles/manual/{id} hatte keinen Aufrufer — ein
        // Tippfehler ließ sich nicht mehr korrigieren.
        await api.put(`/vehicles/manual/${fahrzeug.id}`, payload);
        toast.success("Fahrzeugdaten gespeichert");
      } else {
        await api.post("/vehicles/manual", payload);
        toast.success("Fahrzeug im Bestand angelegt");
      }
      onDone?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };
  // Pruefbericht 20.09.2026 (M-07): role=dialog, Escape, Fokus (lib/useModal);
  // waehrend des Speicherns schliesst Escape nicht.
  const dialogRef = useModal(() => { if (!busy) onClose?.(); });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.7)" }}>
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="manuell-dialog-titel"
           className="w-full max-w-2xl max-h-[92vh] overflow-y-auto rounded-2xl px-5 pb-5"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)" }}>
        {/* M-11: Kopfzeile bleibt beim Scrollen oben, Schliessen mit 44-px-Trefferflaeche */}
        <div className="flex items-center justify-between gap-3 py-4 mb-1 sticky top-0 z-10"
             style={{ background: "var(--bg-elevated)" }}>
          <div>
            <div className="text-lg font-bold" id="manuell-dialog-titel">
              {bearbeiten ? "Fahrzeugdaten bearbeiten" : "Fahrzeug manuell hinzufügen"}
            </div>
            <div className="text-xs text-zinc-500">
              {bearbeiten
                ? "Von Hand angelegtes Fahrzeug korrigieren. Ein bestehendes Inserat behält seine eigene Kopie der Daten."
                : "Für Fahrzeuge, die du bereits besitzt oder außerhalb der Plattform gekauft hast."}
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Schließen" data-testid="manuell-schliessen"
                  className="w-11 h-11 -mr-2 shrink-0 flex items-center justify-center rounded-full text-zinc-400 hover:text-white hover:bg-white/10">
            <X size={20} />
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Marke *"><input value={f.make_label} onChange={set("make_label")} className={inputCls} style={st} placeholder="BMW" /></Field>
          <Field label="Modell *"><input value={f.model_label} onChange={set("model_label")} className={inputCls} style={st} placeholder="320d" /></Field>
          <div className="col-span-2">
            <Field label="Modellbezeichnung"><input value={f.model_description} onChange={set("model_description")} className={inputCls} style={st} placeholder="320d Touring M Sport" /></Field>
          </div>
          <Field label="Erstzulassung"><MonatJahrEingabe value={f.first_registration} onChange={(v) => set("first_registration")({ target: { value: v } })} art="ez" testid="manuell-ez" className={inputCls} style={st} /></Field>
          <Field label="Kilometerstand"><input type="text" inputMode="numeric" autoComplete="off" value={f.mileage} onChange={set("mileage")} className={inputCls} style={st} placeholder="z. B. 150.000" data-testid="manuell-km" /></Field>
          <Field label="Kraftstoff"><input value={f.fuel_label} onChange={set("fuel_label")} className={inputCls} style={st} placeholder="Diesel" /></Field>
          <Field label="Getriebe"><input value={f.gearbox_label} onChange={set("gearbox_label")} className={inputCls} style={st} placeholder="Automatik" /></Field>
          <Field label="Leistung (PS)"><input type="text" inputMode="numeric" autoComplete="off" value={f.power_ps} onChange={set("power_ps")} className={inputCls} style={st} placeholder="z. B. 150" /></Field>
          <Field label="Farbe"><input value={f.color} onChange={set("color")} className={inputCls} style={st} /></Field>
          <Field label="FIN"><input value={f.vin} onChange={set("vin")} className={inputCls} style={st} /></Field>
          <Field label="Vorbesitzer"><input value={f.previous_owners} onChange={set("previous_owners")} className={inputCls} style={st} /></Field>
          <div className="col-span-2">
            <Field label="Ausstattung (Komma-getrennt)"><input value={f.features} onChange={set("features")} className={inputCls} style={st} placeholder="Navi, LED, AHK" /></Field>
          </div>
          <div className="col-span-2">
            <Field label="Beschreibung"><textarea rows={3} value={f.description} onChange={set("description")} className={inputCls} style={st} /></Field>
          </div>
          <Field label="Einkaufspreis (€)"><input type="text" inputMode="decimal" autoComplete="off" value={f.purchase_price} onChange={set("purchase_price")} className={inputCls} style={st} placeholder="z. B. 12.990" data-testid="manuell-ek" /></Field>
        </div>
        <button onClick={submit} disabled={busy}
                className="mt-4 w-full rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                style={{ background: "var(--accent-red)" }}>
          {bearbeiten
            ? (busy ? "Wird gespeichert…" : "Änderungen speichern")
            : (busy ? "Wird angelegt…" : "In den Bestand aufnehmen")}
        </button>
      </div>
    </div>
  );
}
