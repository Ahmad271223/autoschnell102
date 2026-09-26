import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { BarChart3, RefreshCw, Play, Pause, Settings2, Radar, AlertTriangle, ListPlus } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { DATENLAGE, datumZeit, eur, pct, trendFarbe } from "@/lib/markt";

const STATUS_TONE = { ok: "green", fehler: "red", wartet: "gray" };

/**
 * Admin → Marktanalyse (Auftrag Ahmad 25.09.2026, Teil von Phase 1):
 * alle beobachteten Modelle mit Kennzahlen, Status des Crawlers, Budget,
 * Taktung und Konfiguration (km-/EZ-Bereiche, Budget). Klick auf ein
 * Modell öffnet die Segmentanalyse.
 */
export default function Markt() {
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;
  const [modelle, setModelle] = useState(null);
  const [status, setStatus] = useState(null);
  const [fehler, setFehler] = useState("");
  const [busy, setBusy] = useState("");
  const [konfigOffen, setKonfigOffen] = useState(false);

  const laden = async () => {
    try {
      const [m, s] = await Promise.all([api.get("/admin/market/models"), api.get("/admin/market/status")]);
      setModelle(m.data.modelle || []);
      setStatus(s.data);
      setFehler("");
    } catch (e) { setFehler(errMsg(e, "Marktanalyse konnte nicht geladen werden")); }
  };
  useEffect(() => { laden(); }, []);

  const aktion = async (name, fn, erfolg) => {
    setBusy(name);
    try { const r = await fn(); toast.success(typeof erfolg === "function" ? erfolg(r.data) : erfolg); await laden(); }
    catch (e) { toast.error(errMsg(e, "Aktion fehlgeschlagen")); }
    finally { setBusy(""); }
  };
  const schalten = (m) => aktion(`modell-${m.id}`, () => api.post(`/admin/market/models/${m.id}/enabled`, { enabled: !m.enabled }),
    !m.enabled ? "Modell wird beobachtet" : "Modell pausiert");

  if (fehler) {
    return <Card data-testid="markt-ladefehler"><div className="text-red-300 text-sm">{fehler}</div>
      <Button size="sm" className="mt-2" onClick={laden}><RefreshCw size={14} /> Erneut laden</Button></Card>;
  }
  if (!modelle || !status) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  const b = status.budget || {};
  const takt = status.takt || {};
  const jobs = status.jobs || {};
  return (
    <div>
      <PageHeader title="Marktanalyse" subtitle="Eigene historische mobile.de-Beobachtung: je Segment die 20 günstigsten Angebote, täglich."
                  action={<div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={laden}><RefreshCw size={14} /> Aktualisieren</Button>
                    <Button variant="outline" size="sm" onClick={() => setKonfigOffen((o) => !o)} data-testid="markt-konfig-oeffnen"><Settings2 size={14} /> Bereiche & Budget</Button>
                    <Link to="/admin/markt/chancen" data-testid="markt-chancen-link"><Button variant="outline" size="sm"><Radar size={14} /> Chancen</Button></Link>
                    <Link to="/admin/markt/auftraege" data-testid="markt-auftraege-link"><Button size="sm"><ListPlus size={14} /> Suchaufträge</Button></Link>
                  </div>} />

      {(status.monitoring?.alarme || []).length > 0 && (
        <Card className="mb-4" data-testid="markt-alarme">
          <div className="text-[13px] font-semibold text-white mb-1 inline-flex items-center gap-1.5"><AlertTriangle size={14} className="text-amber-300" /> Hinweise</div>
          <ul className="text-[12px] space-y-0.5">
            {status.monitoring.alarme.map((a) => <li key={a.typ} className={a.stufe === "rot" ? "text-red-300" : a.stufe === "info" ? "text-zinc-400" : "text-amber-300"}>{a.text}</li>)}
          </ul>
        </Card>
      )}

      <Card className="mb-4" data-testid="markt-status">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[12px]">
          <Kachel label="Crawler" wert={status.aktiv ? "an — läuft automatisch" : "aus"} tone={status.aktiv ? "text-emerald-300" : "text-amber-300"}
                  hint={status.aktiv_quelle === "admin" ? "per Knopf gesetzt" : "Vorgabe aus der Umgebung (MARKT_AKTIV)"} />
          <Kachel label="Segmente aktiv" wert={`${status.segmente} · ${status.modelle} Modelle`} />
          <Kachel label="Taktung" wert={`jedes Segment alle ${takt.intervall_tage} Tag(e)`} hint={`${takt.segmente_je_tag} Segmente/Tag in Bündeln zu ${takt.buendel || 1} ≈ ${Number(takt.kosten_je_tag_usd || 0).toFixed(2)} $ · ≈ ${Number(takt.kosten_je_monat_usd || 0).toFixed(0)} $/Monat${takt.automatisch ? " (automatisch aus dem Budget)" : ""}`} />
          <Kachel label={`Budget ${b._id || ""}`} wert={`${Number(b.used_usd || 0).toFixed(2)} $ von ${Number(b.budget_usd || 0).toFixed(0)} $`}
                  hint={`reserviert ${Number(b.reserved_usd || 0).toFixed(2)} $ · ${b.rows || 0} Zeilen · ${b.runs || 0} Läufe`} />
          <Kachel label={`Jobs heute (${jobs.tag || ""})`} wert={`${jobs.completed || 0} fertig · ${jobs.running || 0} laufen · ${jobs.queued || 0} warten`}
                  hint={jobs.failed ? `${jobs.failed} fehlgeschlagen` : "keine Fehler"} tone={jobs.failed ? "text-red-300" : ""} />
          <Kachel label="Nächster Lauf" wert={jobs.naechster ? datumZeit(jobs.naechster.scheduled_at) : "—"} hint={jobs.naechster?.segment_id || ""} />
          <Kachel label="Listings / Snapshots" wert={`${(status.listings || 0).toLocaleString("de-DE")} / ${(status.snapshots || 0).toLocaleString("de-DE")}`} />
          <Kachel label="Scraper" wert={status.token_vorhanden ? "Token vorhanden" : "APIFY_TOKEN fehlt"} hint={status.actor} tone={status.token_vorhanden ? "" : "text-red-300"} />
        </div>
        {status.monitoring && (
          <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-[12px]" data-testid="markt-monitoring">
            <Kachel label="Heute geplant / fertig / Fehler" wert={`${status.monitoring.geplant} / ${status.monitoring.erfolgreich} / ${status.monitoring.fehlgeschlagen}`} hint={`${status.monitoring.wartend} wartend · ${status.monitoring.laufend} laufend`} />
            <Kachel label="Zeilen heute / Monat" wert={`${(status.monitoring.rows_heute || 0).toLocaleString("de-DE")} / ${(status.monitoring.rows_monat || 0).toLocaleString("de-DE")}`} />
            <Kachel label="Kosten heute / Monat" wert={`${Number(status.monitoring.kosten_heute_usd || 0).toFixed(2)} $ / ${Number(status.monitoring.kosten_monat_usd || 0).toFixed(2)} $`}
                    hint={`Budget übrig ${Number(status.monitoring.budget_uebrig_usd || 0).toFixed(2)} $ (${status.monitoring.budget_anteil_pct} % verbraucht)`} />
            <Kachel label="Ø Laufzeit · letzter Erfolg" wert={status.monitoring.mittlere_laufzeit_s != null ? `${status.monitoring.mittlere_laufzeit_s} s` : "—"}
                    hint={status.monitoring.letzter_erfolg ? `${datumZeit(status.monitoring.letzter_erfolg.finished_at)} · ${status.monitoring.letzter_erfolg.segment_id}` : "noch keiner"} />
          </div>
        )}
        {superAdmin && (
          <div className="mt-3 flex flex-wrap gap-2">
            {/* Wunsch Ahmad 26.09.2026: EIN Knopf — an heisst: alle aktiven Suchauftraege laufen von selbst nach Tagesplan */}
            <Button size="sm" variant={status.aktiv ? "outline" : undefined} disabled={!!busy || (!status.aktiv && !status.token_vorhanden)} data-testid="markt-crawler-schalter"
                    title={status.aktiv ? "Crawler ausschalten — wartende Jobs bleiben liegen, nichts wird gelöscht" : "Crawler einschalten — alle aktiven Suchaufträge laufen automatisch nach Tagesplan"}
                    onClick={() => {
                      if (!status.aktiv && !window.confirm(`Crawler einschalten?\n\nAb dann laufen alle aktiven Suchaufträge automatisch nach Tagesplan — etwa ${Number(takt.kosten_je_monat_usd || 0).toFixed(0)} $ im Monat bei ${status.segmente} Segmenten.`)) return;
                      aktion("crawler", () => api.post("/admin/market/crawler", { aktiv: !status.aktiv }),
                             (d) => (d.aktiv ? "Crawler an — die Suchaufträge laufen jetzt automatisch" : "Crawler aus"));
                    }}>
              {status.aktiv ? <><Pause size={14} /> Crawler ausschalten</> : <><Play size={14} /> Crawler einschalten</>}
            </Button>
            <Button size="sm" variant="outline" disabled={!!busy} data-testid="markt-sync"
                    onClick={() => aktion("sync", () => api.post("/admin/market/sync"), (d) => `Modelle: ${d.modelle?.neu ?? 0} neu · Segmente: ${d.segmente?.segmente ?? 0}`)}>
              Startliste & Segmente aufbauen
            </Button>
            <Button size="sm" variant="outline" disabled={!!busy || !status.token_vorhanden} data-testid="markt-plan"
                    onClick={() => aktion("plan", () => api.post("/admin/market/plan"), (d) => `${d.neu} Jobs für ${d.tag} geplant`)}>
              <Play size={14} /> Tagesplan jetzt anlegen
            </Button>
            <Button size="sm" variant="outline" disabled={!!busy || !status.token_vorhanden} data-testid="markt-worker"
                    onClick={() => aktion("worker", () => api.post("/admin/market/worker/einmal"), (d) => `${d.erledigt} Job(s) im Vordergrund verarbeitet`)}
                    title="Einen Takt sofort ausführen (auch bei ausgeschaltetem Crawler) — kostet echtes Apify-Budget">
              Einen Takt jetzt ausführen
            </Button>
          </div>
        )}
      </Card>

      {konfigOffen && <KonfigKarte status={status} superAdmin={superAdmin} onGespeichert={laden} />}

      <Card padded={false} data-testid="markt-modelle">
        <div className="px-4 py-3 text-[13px] text-zinc-400" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <BarChart3 size={14} className="inline mr-1" /> {modelle.length} Modelle · Kennzahlen = nur die 20 günstigsten je Segment (untere Marktpreisspanne), kein Marktmedian
        </div>
        {modelle.length === 0 ? <EmptyState title="Noch keine Modelle" hint="„Startliste & Segmente aufbauen“ spielt die 52 Startmodelle ein — oder unter Suchaufträge eigene anlegen." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13px] min-w-[860px]">
              <thead>
                <tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                  <th className="px-4 py-2.5 font-medium">Modell</th>
                  <th className="px-4 py-2.5 font-medium">Segmente</th>
                  <th className="px-4 py-2.5 font-medium">Letzter Crawl</th>
                  <th className="px-4 py-2.5 font-medium text-right">Listings</th>
                  <th className="px-4 py-2.5 font-medium text-right">Günstigstes</th>
                  <th className="px-4 py-2.5 font-medium text-right">Top-20-Median (Ø)</th>
                  <th className="px-4 py-2.5 font-medium text-right">7 Tage</th>
                  <th className="px-4 py-2.5 font-medium text-right">30 Tage</th>
                  <th className="px-4 py-2.5 font-medium">Status</th>
                  <th className="px-4 py-2.5 font-medium text-right">Aktion</th>
                </tr>
              </thead>
              <tbody>
                {modelle.map((m) => (
                  <tr key={m.id} className="border-t border-white/5" data-testid={`markt-modell-${m.id}`}>
                    <td className="px-4 py-2.5">
                      <Link to={`/admin/markt/${m.id}`} className="text-white font-medium hover:underline" data-testid={`markt-modell-link-${m.id}`}>{m.label}</Link>
                      <div className="text-[11px] text-zinc-500">{m.fuel}{m.power_kw_min ? ` · ${m.power_kw_min}–${m.power_kw_max} kW` : ""}{m.model_id ? "" : " · keine mobile.de-ID"}</div>
                    </td>
                    <td className="px-4 py-2.5 text-zinc-300">{m.segmente_mit_daten}/{m.segmente_aktiv}</td>
                    <td className="px-4 py-2.5 text-zinc-400">{m.last_success_at ? fmtDate(m.last_success_at) : "—"}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-zinc-300">{m.listings}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-zinc-200">{eur(m.min_price)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums text-zinc-200">{eur(m.median_top20_mittel)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums" style={{ color: trendFarbe(m.trend_7d_pct) }}>{pct(m.trend_7d_pct)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums" style={{ color: trendFarbe(m.trend_30d_pct) }}>{pct(m.trend_30d_pct)}</td>
                    <td className="px-4 py-2.5"><Badge tone={m.enabled ? STATUS_TONE[m.crawl_status] || "gray" : "gray"}>{m.enabled ? m.crawl_status : "pausiert"}</Badge></td>
                    <td className="px-4 py-2.5 text-right">
                      <Button size="sm" variant="ghost" disabled={!superAdmin || !!busy || (!m.enabled && !m.model_id)} onClick={() => schalten(m)}
                              data-testid={`markt-modell-schalten-${m.id}`}>{m.enabled ? "Pausieren" : "Beobachten"}</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function Kachel({ label, wert, hint, tone = "" }) {
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--wa-06)" }}>
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className={`text-[13px] font-semibold text-white ${tone}`}>{wert}</div>
      {hint && <div className="text-[11px] text-zinc-500 mt-0.5">{hint}</div>}
    </div>
  );
}

function KonfigKarte({ status, superAdmin, onGespeichert }) {
  const [km, setKm] = useState((status.km_buckets || []).map((b) => `${b.min_km}-${b.max_km}`).join(", "));
  const [ez, setEz] = useState((status.ez_buckets || []).map((b) => `${b.year_from || ""}-${b.year_to || ""}`).join(", "));
  const [rows, setRows] = useState(String(status.einstellungen?.rows_je_segment || 20));
  const [budget, setBudget] = useState(String(status.budget?.budget_usd ?? 450));
  const [busy, setBusy] = useState(false);
  const parse = (text, a, b) => text.split(",").map((t) => t.trim()).filter(Boolean).map((t) => {
    const [x, y] = t.split("-").map((v) => v.trim());
    return { [a]: x ? Number(x) : null, [b]: y ? Number(y) : null };
  });
  const speichern = async () => {
    setBusy(true);
    try {
      const r = await api.put("/admin/market/config", {
        km_buckets: parse(km, "min_km", "max_km"), ez_buckets: parse(ez, "year_from", "year_to"),
        rows_je_segment: Number(rows) || 20, budget_usd: Number(budget) || 0,
      });
      toast.success(`Gespeichert · ${r.data.segmente} Segmente · alle ${r.data.takt?.intervall_tage} Tag(e)`);
      onGespeichert?.();
    } catch (e) { toast.error(errMsg(e, "Speichern fehlgeschlagen")); }
    finally { setBusy(false); }
  };
  const feld = "w-full rounded-lg px-2.5 py-1.5 text-[13px] outline-none";
  const st = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };
  return (
    <Card className="mb-4" data-testid="markt-konfig">
      <div className="text-[15px] font-semibold text-white mb-2">Bereiche & Budget</div>
      <div className="grid md:grid-cols-2 gap-3 text-[12px] text-zinc-400">
        <label>km-Bereiche (min-max, Komma-getrennt)
          <input className={feld} style={st} value={km} onChange={(e) => setKm(e.target.value)} data-testid="markt-konfig-km" /></label>
        <label>EZ-Bereiche (von-bis, leer = keine EZ-Aufteilung)
          <input className={feld} style={st} value={ez} onChange={(e) => setEz(e.target.value)} data-testid="markt-konfig-ez" /></label>
        <label>Zeilen je Segment (die N günstigsten)
          <input className={feld} style={st} value={rows} onChange={(e) => setRows(e.target.value)} inputMode="numeric" /></label>
        <label>Monatsbudget (US-Dollar, Apify)
          <input className={feld} style={st} value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" data-testid="markt-konfig-budget" /></label>
      </div>
      <div className="mt-2 text-[11px] text-zinc-500">
        Segmente = Modelle × km-Bereiche × EZ-Bereiche. Ein Lauf kostet ≈ 0,004 $ + Zeilen × 0,003 $. Die Taktung (alle N Tage) ergibt sich aus dem Budget.
      </div>
      <Button size="sm" className="mt-3" onClick={speichern} disabled={busy || !superAdmin} data-testid="markt-konfig-speichern">Speichern</Button>
    </Card>
  );
}

export { DATENLAGE };
