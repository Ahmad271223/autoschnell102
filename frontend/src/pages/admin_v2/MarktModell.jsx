import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, RefreshCw, Play, X, FileBarChart } from "lucide-react";
import { toast } from "sonner";
import {
  Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { BEREICHE, DATENLAGE, bestandText, datumKurz, datumZeit, eur, lowMarketLabel, mobileLink, pct, trendFarbe, trendText, zustandText } from "@/lib/markt";
import MarktQualitaet from "@/components/MarktQualitaet";
import { HealthBadge, HealthZaehler } from "@/components/MarktHealth";

const GETRIEBE_TEXT = { AUTOMATIC_GEAR: "Automatik", MANUAL_GEAR: "Schaltgetriebe", SEMIAUTOMATIC_GEAR: "Halbautomatik" };
// Review 26.09.2026 abends P1: Job-Status 'data_invalid' = Lauf lieferte unsortierte Daten, nichts gespeichert
const JOB_STATUS_TEXT = { completed: "fertig", failed: "Fehler", data_invalid: "ungültig (Sortierung unsicher, nichts gespeichert)", cancelled: "abgebrochen" };
const jobStatusText = (st) => JOB_STATUS_TEXT[st] || st;

/**
 * Admin → Marktanalyse → Modell: km-/EZ-Segmente wählen, Kennzahlen,
 * Zeitreihe (Minimum / Median / Durchschnitt der N günstigsten je Tag,
 * p25–p75 als Band), Tagesveränderung, Wochenmediane, Tagestabelle,
 * aktuelle Top-N mit Preis-Historie je Listing. Alle Diagrammdaten kommen
 * aus den Tagesaggregaten (30 Tage = 30 Dokumente). N = Zeilen des Suchauftrags.
 */
const FASSUNG_TEXT = { fuel: "Kraftstoff", gearbox: "Getriebe", body: "Karosserie", seller_type: "Verkäufer", country: "Land" };
/** Reparaturwelle 6 Nr. 123: die Definition einer (auch historischen) Fassung als Kurztext. */
function fassungText(d) {
  if (!d) return "";
  const teile = [];
  for (const k of ["fuel", "gearbox", "body", "seller_type"]) if (d[k]) teile.push(`${FASSUNG_TEXT[k]} ${d[k]}`);
  if (d.country && d.country !== "DE") teile.push(`${FASSUNG_TEXT.country} ${d.country}`);
  if (d.power_kw_min || d.power_kw_max) teile.push(`${d.power_kw_min || "…"}–${d.power_kw_max || "…"} kW`);
  if (d.zip) teile.push(`PLZ ${d.zip} +${d.radius_km} km`);
  if (d.rows) teile.push(`${d.rows} Zeilen`);
  return teile.join(" · ");
}
export default function MarktModell() {
  const { modell: modellId } = useParams();
  const [params, setParams] = useSearchParams();
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;
  const [modell, setModell] = useState(null);
  const [zeigeInaktiv, setZeigeInaktiv] = useState(false);
  const [fehler, setFehler] = useState("");
  const segmentId = params.get("segment") || "";
  const bereich = params.get("bereich") || "30d";

  // Go-Live-Pruefung 28.09.2026 (admin4): Wechsel zu einem anderen Modell, waehrend die Anfrage fuer
  // das vorige noch lief — deren spaete Antwort ueberschrieb das neue Modell (samt Segment-Vorwahl in
  // der URL), "Jetzt crawlen" (kostet Apify-Laeufe) traf dann ein Segment des falschen Modells; ein
  // spaeter Fehler zeigte die Fehlerkarte ueber dem neuen. Jetzt: laufende Nummer, nur die juengste
  // Anfrage schreibt; angezeigt wird ein Modell nur, wenn es zur Id in der URL gehoert.
  const ladeNr = useRef(0);
  const laden = useCallback(async () => {
    const nr = ++ladeNr.current;
    try {
      const r = await api.get(`/admin/market/models/${modellId}`);
      if (nr !== ladeNr.current) return;
      setModell(r.data);
      setFehler("");
      if (!params.get("segment") && r.data.segmente?.length) {
        const erstes = r.data.segmente.find((s) => s.stats?.sample_size) || r.data.segmente[0];
        setParams((p) => { const n = new URLSearchParams(p); n.set("segment", erstes.id); return n; }, { replace: true });
      }
    } catch (e) {
      if (nr !== ladeNr.current) return;
      setFehler(errMsg(e, "Modell konnte nicht geladen werden"));
    }
  }, [modellId]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setFehler(""); laden(); }, [laden]);
  // Master-Auftrag Phase F: Health des Modells und seiner Segmente — eigene Anfrage; ohne Berechnung (404) oder bei
  // einem Fehler bleibt die Seite unveraendert (keine Health-Anzeige)
  const [health, setHealth] = useState(null);
  useEffect(() => {
    let aktiv = true;
    api.get(`/admin/market/health/models/${modellId}`).then((r) => { if (aktiv) setHealth(r.data || null); }).catch(() => { if (aktiv) setHealth(null); });
    return () => { aktiv = false; };
  }, [modellId]);
  // Prüfbefund F18: Badges nur, wenn der Health-Stand zur AKTUELLEN Fassung eines aktiven Auftrags gehört — sonst kein
  // altes Badge, sondern „pausiert“ bzw. „neue Fassung noch nicht bewertet“ mit Stichtag
  const healthAktuell = health?.aktuell === true;
  const healthJeSegment = useMemo(() => (health?.aktuell === true ? Object.fromEntries((health?.segmente || []).map((s) => [s.segment_id, s])) : {}), [health]);

  const setzen = (k, v) => setParams((p) => { const n = new URLSearchParams(p); n.set(k, v); return n; });

  if (fehler) return <Card data-testid="markt-modell-fehler"><div className="text-red-300 text-sm">{fehler}</div></Card>;
  // admin4: ein Modell mit anderer Id als in der URL (vorige Seite) nie anzeigen — sonst "lade…"
  if (!modell || (modell.id && modell.id !== modellId)) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  const segmente = modell.segmente || [];
  // Befund Ahmad 26.09.2026 abends: nach Umstellungen der Bereiche stehen alte Segmente als
  // "inaktiv" weiter in der Liste (Historie bleibt) — Auswahl und Tabelle zeigen nur aktive,
  // die inaktiven auf Wunsch.
  const aktive = segmente.filter((s) => s.enabled);
  const basis = aktive.length ? aktive : segmente;
  const kmWerte = [...new Map(basis.map((s) => [`${s.min_km}-${s.max_km}`, s])).values()].sort((a, b) => (a.min_km || 0) - (b.min_km || 0));
  const ezWerte = [...new Map(basis.map((s) => [s.ez_label || "alle", s])).values()].sort((a, b) => (a.year_from || 0) - (b.year_from || 0));
  const inaktive = segmente.length - aktive.length;
  const tabelle = zeigeInaktiv ? segmente : aktive;
  const aktuell = segmente.find((s) => s.id === segmentId) || null;

  return (
    <div>
      <Link to="/admin/markt" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Marktanalyse</Link>
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h1 className="text-[22px] font-semibold text-white" data-testid="markt-modell-titel">{modell.label}</h1>
          {/* Master-Auftrag Phase F: Modell-Health (Aggregat der Segmente, getrennt von der Datenqualität) */}
          {health?.modell?.health && healthAktuell && (
            <div className="my-1 inline-flex flex-wrap items-center gap-2 text-[11px]" data-testid="markt-modell-health-zeile">
              <Link to="/admin/markt/optimierung" data-testid="markt-modell-health-link"><HealthBadge health={health.modell.health} testid="markt-modell-health" /></Link>
              <HealthZaehler zaehler={health.modell.zaehler} testid="markt-modell-health-zaehler" />
              <span className="text-zinc-500" data-testid="markt-modell-health-stand">Stand {health.modell.tag || "—"}</span>
              {health.modell.vorschlaege_offen ? <span className="text-zinc-500">{health.modell.vorschlaege_offen} Vorschlag/Vorschläge offen</span> : null}
            </div>
          )}
          {health?.modell?.health && !healthAktuell && (
            <div className="my-1 text-[11px] text-zinc-500" data-testid="markt-modell-health-veraltet">
              {health.nicht_aktuell_grund === "neue_fassung"
                ? `Health: neue Fassung v${health.auftrag_version} noch nicht bewertet (letzter Stand Fassung v${health.health_version ?? "?"} vom ${health.health_tag || "—"})`
                : health.nicht_aktuell_grund === "pausiert"
                  ? `Health: Suchauftrag pausiert — letzter Stand vom ${health.health_tag || "—"} (Fassung v${health.health_version ?? "?"}), wird erst nach dem Aktivieren neu bewertet`
                  : `Health: keine aktiven Segmente — letzter Stand vom ${health.health_tag || "—"}`}
            </div>
          )}
          <div className="text-[12px] text-zinc-500" data-testid="markt-modell-technik">{modell.fuel}{modell.gearbox ? ` · ${GETRIEBE_TEXT[modell.gearbox] || modell.gearbox}` : " · alle Getriebe (gemischt!)"}{modell.power_kw_min ? ` · ${modell.power_kw_min}–${modell.power_kw_max} kW` : ""} · mobile.de {modell.make_id}/{modell.model_id} · {segmente.filter((s) => s.enabled).length} Segmente</div>
        </div>
        <div className="flex gap-2">
          {/* Master-Auftrag Phase E: 5-Tage-, 15-Tage- und Monatsberichte dieses Modells (eingefroren bzw. vorläufig) */}
          <Link to={`/admin/markt/berichte/${modell.id || modellId}`} data-testid="markt-modell-berichte"><Button variant="outline" size="sm"><FileBarChart size={14} /> Berichte</Button></Link>
          <Button variant="outline" size="sm" onClick={laden}><RefreshCw size={14} /> Aktualisieren</Button>
        </div>
      </div>

      {/* Segmentwahl: km-Bereich x EZ-Bereich */}
      <Card className="mb-4" data-testid="markt-segmentwahl">
        <div className="text-[11px] text-zinc-500 mb-1.5">km-Bereich</div>
        <div className="flex flex-wrap gap-1.5">
          {kmWerte.map((s) => {
            const aktiv = aktuell && aktuell.min_km === s.min_km && aktuell.max_km === s.max_km;
            return <Chip key={s.km_label} aktiv={aktiv} testid={`markt-km-${s.min_km}`}
                         onClick={() => { const ziel = segmente.find((x) => x.min_km === s.min_km && x.max_km === s.max_km && (x.ez_label || "alle") === (aktuell?.ez_label || ezWerte[0]?.ez_label || "alle")) || s; setzen("segment", ziel.id); }}>
              {s.km_label}</Chip>;
          })}
        </div>
        {ezWerte.length > 1 && (
          <>
            <div className="text-[11px] text-zinc-500 mt-3 mb-1.5">Erstzulassung</div>
            <div className="flex flex-wrap gap-1.5">
              {ezWerte.map((s) => {
                const aktiv = aktuell && (aktuell.ez_label || "alle") === (s.ez_label || "alle");
                return <Chip key={s.ez_label || "alle"} aktiv={aktiv} testid={`markt-ez-${s.year_from || "alle"}`}
                             onClick={() => { const ziel = segmente.find((x) => (x.ez_label || "alle") === (s.ez_label || "alle") && x.min_km === (aktuell?.min_km ?? kmWerte[0]?.min_km)) || s; setzen("segment", ziel.id); }}>
                  {s.ez_label || "alle Baujahre"}</Chip>;
              })}
            </div>
          </>
        )}
      </Card>

      {aktuell ? <SegmentAnalyse key={aktuell.id} segment={aktuell} bereich={bereich} onBereich={(b) => setzen("bereich", b)} superAdmin={superAdmin} />
               : <EmptyState title="Kein Segment" hint="Segmente entstehen über „Startliste & Segmente aufbauen“." />}

      {/* Segmentuebersicht des Modells (Auftrag v3) */}
      <Card padded={false} className="mt-4" data-testid="markt-segmentuebersicht">
        <div className="px-4 py-3 text-[13px] font-semibold text-white" style={{ borderBottom: "1px solid var(--wa-08)" }}>Alle Segmente dieses Modells ({aktive.length} aktiv)
          {inaktive > 0 && (
            <button type="button" className="ml-3 text-[11px] font-normal text-zinc-400 hover:text-white underline" onClick={() => setZeigeInaktiv((v) => !v)} data-testid="markt-inaktive-schalter">
              {zeigeInaktiv ? `${inaktive} inaktive ausblenden` : `${inaktive} inaktive (alte Bereiche / frühere Fassungen) anzeigen`}
            </button>
          )}
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] min-w-[980px]">
            <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
              <th className="px-3 py-2">Segment</th><th className="px-3 py-2">Letzter Crawl</th><th className="px-3 py-2">Nächster</th><th className="px-3 py-2 text-right">N</th>
              <th className="px-3 py-2 text-right">Günstigstes</th><th className="px-3 py-2 text-right">Median</th><th className="px-3 py-2 text-right">Ø</th>
              <th className="px-3 py-2 text-right">7 Tage</th><th className="px-3 py-2 text-right">30 Tage</th><th className="px-3 py-2">Qualität / Tiefe</th><th className="px-3 py-2">Health</th><th className="px-3 py-2">Status</th>
            </tr></thead>
            <tbody>{tabelle.map((s) => (
              <tr key={s.id} className={`border-t border-white/5 tabular-nums ${s.id === segmentId ? "bg-white/5" : ""} ${s.enabled ? "" : "opacity-50"}`} data-testid={`markt-segmentzeile-${s.id}`}>
                <td className="px-3 py-1.5"><button type="button" className="text-white hover:underline text-left" onClick={() => setzen("segment", s.id)}>{s.ez_label || "alle EZ"} · {s.km_label}</button>
                  {/* P4: alte Fassung des Auftrags (materielle Merkmale wurden geaendert) — Historie bleibt getrennt */}
                  {(s.version || 1) !== (modell.version || 1) && <span className="ml-1 text-[10px] text-zinc-500" data-testid={`markt-fassung-${s.id}`}>Fassung v{s.version || 1} (aktuell v{modell.version || 1})</span>}</td>
                <td className="px-3 py-1.5 text-zinc-400">{s.last_success_at ? fmtDate(s.last_success_at) : "—"}</td>
                <td className="px-3 py-1.5 text-zinc-400">{s.naechster_crawl ? datumZeit(s.naechster_crawl) : "—"}</td>
                <td className="px-3 py-1.5 text-right">{s.stats?.sample_size ?? "—"}</td>
                <td className="px-3 py-1.5 text-right">{eur(s.stats?.min_price)}</td>
                <td className="px-3 py-1.5 text-right text-white">{eur(s.stats?.median_price)}</td>
                <td className="px-3 py-1.5 text-right">{eur(s.stats?.avg_price)}</td>
                <td className="px-3 py-1.5 text-right" style={{ color: trendFarbe(s.stats?.trend_7d_pct) }}>{pct(s.stats?.trend_7d_pct)}</td>
                <td className="px-3 py-1.5 text-right" style={{ color: trendFarbe(s.stats?.trend_30d_pct) }}>{pct(s.stats?.trend_30d_pct)}</td>
                <td className="px-3 py-1.5"><MarktQualitaet q={s.qualitaet} klein testid={`markt-segment-qualitaet-${s.id}`} /></td>
                {/* Master-Auftrag Phase F: Health des Segments (getrennt von der Datenqualität) */}
                <td className="px-3 py-1.5">{healthJeSegment[s.id] ? <HealthBadge health={healthJeSegment[s.id].health} klein titel={healthJeSegment[s.id].health_text || undefined} testid={`markt-segment-health-${s.id}`} /> : <span className="text-zinc-600">—</span>}</td>
                <td className="px-3 py-1.5">{!s.enabled ? <Badge tone="gray">inaktiv</Badge> : s.letzter_job?.status === "failed" ? <Badge tone="red">Fehler</Badge> : s.letzter_job?.status === "data_invalid" ? <Badge tone="yellow">ungültig</Badge> : s.stats?.sample_size ? <Badge tone="green">ok</Badge> : <Badge tone="gray">wartet</Badge>}</td>
              </tr>))}</tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function Chip({ aktiv, onClick, children, testid }) {
  return (
    <button type="button" onClick={onClick} data-testid={testid} aria-pressed={!!aktiv}
            className="rounded-full px-3 py-1 text-[12px] border transition-colors"
            style={{ borderColor: aktiv ? "var(--accent-red)" : "var(--wa-12)", background: aktiv ? "rgba(255,59,48,.15)" : "transparent", color: aktiv ? "#fff" : "var(--text-secondary)" }}>
      {children}
    </button>
  );
}

function SegmentAnalyse({ segment, bereich, onBereich, superAdmin }) {
  const [zusammen, setZusammen] = useState(null);
  const [verlauf, setVerlauf] = useState(null);
  const [listings, setListings] = useState(null);
  const [fehler, setFehler] = useState("");
  const [offen, setOffen] = useState(null);       // Listing-Historie
  const [busy, setBusy] = useState(false);
  // Private Deals (Ahmad 26.09.2026 abends): eigener Block nur fuer den Super-Admin; ein Fehler laesst das Segment stehen
  const [privat, setPrivat] = useState(null);

  useEffect(() => {
    let aktiv = true;
    Promise.all([
      api.get(`/admin/market/segments/${segment.id}/summary`),
      api.get(`/admin/market/segments/${segment.id}/listings`),
    ]).then(([z, l]) => { if (aktiv) { setZusammen(z.data); setListings(l.data); setFehler(""); } })
      .catch((e) => { if (aktiv) setFehler(errMsg(e, "Segment konnte nicht geladen werden")); });
    if (superAdmin) {
      api.get(`/admin/market/segments/${segment.id}/private-deals`).then((r) => { if (aktiv) setPrivat(r.data); })
        .catch(() => { if (aktiv) setPrivat(null); });
    }
    return () => { aktiv = false; };
  }, [segment.id, superAdmin]);
  useEffect(() => {
    let aktiv = true;
    api.get(`/admin/market/segments/${segment.id}/history`, { params: { range: bereich } })
      .then((r) => { if (aktiv) setVerlauf(r.data); }).catch(() => { if (aktiv) setVerlauf({ reihe: [], wochen: [], auswertung: {} }); });
    return () => { aktiv = false; };
  }, [segment.id, bereich]);

  const crawlJetzt = async () => {
    setBusy(true);
    try { await api.post(`/admin/market/segments/${segment.id}/crawl-now`); toast.success("Job angelegt — der Worker holt ihn innerhalb einer Minute"); }
    catch (e) { toast.error(errMsg(e, "Crawl konnte nicht angelegt werden")); }
    finally { setBusy(false); }
  };

  const reihe = useMemo(() => (verlauf?.reihe || []).map((r) => ({ ...r, tag: datumKurz(r.date), band: [r.p25, r.p75] })), [verlauf]);
  if (fehler) return <Card><div className="text-red-300 text-sm">{fehler}</div></Card>;
  if (!zusammen) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-6"><Spinner /> lade Segment…</div>;
  const st = zusammen.stats || {};
  const dl = DATENLAGE[st.datenlage] || DATENLAGE.keine;
  const aw = verlauf?.auswertung || {};
  return (
    <div className="space-y-4" data-testid="markt-segment">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-[15px] font-semibold text-white">{segment.label} · {segment.km_label}{segment.ez_label ? ` · ${segment.ez_label}` : ""}</div>
            <div className="text-[11px] text-zinc-500">{st.sample_size || 0} günstigste Vergleichsangebote · letzter Crawl {segment.last_success_at ? fmtDate(segment.last_success_at) : "—"}
              {zusammen.letzter_job ? ` · letzter Job ${jobStatusText(zusammen.letzter_job.status)}${zusammen.letzter_job.error && zusammen.letzter_job.status !== "data_invalid" ? ` (${zusammen.letzter_job.error})` : ""}` : ""}</div>
            {/* Reparaturwelle 6 Nr. 123/124: eine frühere Fassung zeigt IHRE Definition, nicht den heutigen Auftrag */}
            {zusammen.historisch && (
              <div className="text-[11px] mt-0.5" style={{ color: "var(--st-amber)" }} data-testid="markt-fassung-historisch">
                Frühere Fassung v{zusammen.segment?.version || 1} (Auftrag heute v{zusammen.modell?.version || 1}) — beobachtet mit: {fassungText(zusammen.fassung) || "Definition nicht gespeichert"}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] rounded-full px-2 py-0.5 border" style={{ color: dl.farbe, borderColor: dl.farbe }} data-testid="markt-segment-datenlage">{dl.text}</span>
            <MarktQualitaet q={zusammen.qualitaet} mitVollstaendigkeit testid="markt-segment-qualitaet" />
            {superAdmin && <Button size="sm" variant="outline" onClick={crawlJetzt} disabled={busy} data-testid="markt-crawl-jetzt"><Play size={13} /> Jetzt crawlen</Button>}
          </div>
        </div>
        {!st.sample_size ? <EmptyState title="Noch keine Daten" hint="Nach dem ersten erfolgreichen Crawl erscheinen hier die Kennzahlen." /> : (
          <div className="mt-3 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 text-[12px]" data-testid="markt-kennzahlen">
            <K label="Aktuell beobachtet" wert={`${st.sample_size} Fahrzeuge`} />
            <K label="Billigstes" wert={eur(st.min_price)} />
            {/* Master-Auftrag Phase C: neutrale Begriffe — Low-Market-Median der N günstigsten, nicht "Top-N" */}
            <K label={lowMarketLabel(st.sample_size)} wert={eur(st.median_price)} gross />
            <K label={`Durchschnitt (${st.sample_size || "N"} günstigste)`} wert={eur(st.avg_price)} />
            <K label={`Teuerstes (${st.sample_size || "N"} günstigste)`} wert={eur(st.max_price)} />
            <K label="p25 / p75" wert={`${eur(st.p25_price)} / ${eur(st.p75_price)}`} />
            <K label="7-Tage-Trend Low-Market-Median" wert={trendText(st.trend_7d_eur, st.trend_7d_pct)} farbe={trendFarbe(st.trend_7d_eur)}
               unter={bestandText(st.trend_7d_bestand_eur, st.trend_7d_bestand_pct, st.anzahl_gemeinsam)} testid="markt-trend-7d" />
            <K label="30-Tage-Trend Low-Market-Median" wert={trendText(st.trend_30d_eur, st.trend_30d_pct)} farbe={trendFarbe(st.trend_30d_eur)}
               unter={bestandText(st.trend_30d_bestand_eur, st.trend_30d_bestand_pct, st.anzahl_gemeinsam_30d)} testid="markt-trend-30d" />
            <K label="Neue Listings 7 Tage" wert={String(st.new_listings_7d ?? 0)} />
            <K label="Preisreduzierungen 7 Tage" wert={String(st.price_reductions_7d ?? 0)} />
            <K label="Beobachtete Tage" wert={String(st.beobachtete_tage ?? 0)} />
            <K label="Datenstand" wert={datumZeit(st.updated_at)} />
          </div>
        )}
        {zusammen.qualitaet && (
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]" style={{ color: "var(--text-secondary)" }} data-testid="markt-qualitaet">
            <span>Daten seit {zusammen.qualitaet.daten_seit || "—"}</span>
            <span>{zusammen.qualitaet.tage_beobachtet} Tage beobachtet{zusammen.qualitaet.tage_mit_treffern != null && zusammen.qualitaet.tage_mit_treffern !== zusammen.qualitaet.tage_beobachtet ? ` (${zusammen.qualitaet.tage_mit_treffern} mit Treffern)` : ""}</span>
            {zusammen.qualitaet.abdeckung_pct != null && <span>Abdeckung {pct(zusammen.qualitaet.abdeckung_pct, false)} der erwarteten Läufe</span>}
            <span>{zusammen.qualitaet.erfolgreiche_crawls} von {zusammen.qualitaet.erwartete_crawls} erwarteten Crawls gültig{zusammen.qualitaet.laeufe_nur_monoton ? ` (${zusammen.qualitaet.laeufe_nur_monoton} nur monoton sortiert)` : ""}</span>
            <span>aktuelle Sample-Größe {zusammen.qualitaet.sample_size}</span>
            <span style={{ color: dl.farbe }}>{dl.text}</span>
            {/* Phase C: ungültige Läufe (bezahlt, aber keine gültige Statistik) getrennt sichtbar — keine Marktlücke */}
            {zusammen.qualitaet.laeufe_ungueltig ? <span style={{ color: "var(--st-amber)" }} data-testid="markt-ungueltige-laeufe">{zusammen.qualitaet.laeufe_ungueltig} ungültige Läufe{zusammen.qualitaet.letzter_ungueltiger_grund ? ` (zuletzt: ${zusammen.qualitaet.letzter_ungueltiger_grund})` : ""}</span> : null}
            {/* Reparaturwelle 5 Nr. 1: Scraper ohne Positionsnummer — die Werte sind monoton sortiert, aber nicht bewiesen die Top-N */}
            {zusammen.qualitaet.top_n_hinweis && <span style={{ color: "var(--st-amber)" }} data-testid="markt-top-n-hinweis">{zusammen.qualitaet.top_n_hinweis}</span>}
          </div>
        )}
        <div className="mt-2 text-[10px] text-zinc-500">Durchschnitt und Median beziehen sich nur auf die beobachteten {zusammen.stats?.sample_size || segment.max_items || ""} günstigsten Angebote, nicht auf den Gesamtmarkt.</div>
      </Card>

      {superAdmin && privat && <PrivatBlock privat={privat} segment={segment} />}

      {/* Zeitreihe */}
      <Card data-testid="markt-verlauf">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <div className="text-[13px] font-semibold text-white">Verlauf der günstigsten je Tag</div>
          <div className="flex flex-wrap gap-1">
            {BEREICHE.map(([k, l]) => <Chip key={k} aktiv={bereich === k} onClick={() => onBereich(k)} testid={`markt-bereich-${k}`}>{l}</Chip>)}
          </div>
        </div>
        {reihe.length === 0 ? <div className="text-[12px] text-zinc-500">Noch keine Tagesdaten im gewählten Zeitraum.</div> : (
          <>
            <div style={{ height: 280 }}>
              <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 320, height: 200 }}>
                <ComposedChart data={reihe} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,.06)" vertical={false} />
                  <XAxis dataKey="tag" tick={{ fill: "#a1a1aa", fontSize: 11 }} />
                  <YAxis tick={{ fill: "#a1a1aa", fontSize: 11 }} tickFormatter={(v) => `${Math.round(v / 1000)}k`} domain={["auto", "auto"]} width={40} />
                  <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", fontSize: 12 }} formatter={(v, n) => [Array.isArray(v) ? `${eur(v[0])} – ${eur(v[1])}` : eur(v), n]} />
                  <Area type="monotone" dataKey="band" name="p25–p75" stroke="none" fill="#60a5fa" fillOpacity={0.12} />
                  <Line type="monotone" dataKey="min" name="Billigstes" stroke="#34d399" dot={false} strokeWidth={1.5} />
                  <Line type="monotone" dataKey="median" name="Low-Market-Median" stroke="#f87171" dot={false} strokeWidth={2.2} />
                  <Line type="monotone" dataKey="avg" name="Durchschnitt (günstigste)" stroke="#fbbf24" dot={false} strokeWidth={1.5} strokeDasharray="4 3" />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-2 text-[11px] text-zinc-500">Grün Billigstes · Rot Median · Gelb Durchschnitt · Blau p25–p75. Fällt nur das Billigste, war es oft ein einzelnes Inserat; fallen alle drei, bewegt sich das ganze günstige Segment.{aw.tage_ohne_angebot ? ` Lücken = Tage ohne Angebot (${aw.tage_ohne_angebot}).` : ""}{aw.tage_nur_ungueltig ? ` ${aw.tage_nur_ungueltig} Tag(e) nur mit ungültigen Läufen (Datenlücke, keine Marktlücke).` : ""}</div>
            <div className="mt-4 text-[13px] font-semibold text-white">Tagesveränderung des Medians</div>
            <div style={{ height: 120 }}>
              <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 320, height: 200 }}>
                <BarChart data={reihe} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                  <XAxis dataKey="tag" tick={{ fill: "#a1a1aa", fontSize: 10 }} />
                  <YAxis tick={{ fill: "#a1a1aa", fontSize: 10 }} tickFormatter={(v) => `${v}%`} width={40} />
                  <Tooltip contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", fontSize: 12 }} formatter={(v) => [pct(v), "Veränderung"]} />
                  <Bar dataKey="change_pct" name="Veränderung" fill="#60a5fa" />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2 text-[12px]" data-testid="markt-auswertung">
              <K label={`Verlauf ${BEREICHE.find(([k]) => k === bereich)?.[1] || ""}`} wert={trendText(aw.veraenderung_eur, aw.veraenderung_pct)} farbe={trendFarbe(aw.veraenderung_eur)} />
              <K label="Größter Tagesrückgang" wert={aw.groesster_rueckgang ? `${trendText(aw.groesster_rueckgang.change_eur)} am ${datumKurz(aw.groesster_rueckgang.date)}` : "—"} />
              <K label="Größter Tagesanstieg" wert={aw.groesster_anstieg ? `${trendText(aw.groesster_anstieg.change_eur)} am ${datumKurz(aw.groesster_anstieg.date)}` : "—"} />
              <K label="Tage fallend / steigend / gleich" wert={`${aw.tage_fallend ?? 0} / ${aw.tage_steigend ?? 0} / ${aw.tage_unveraendert ?? 0}`} />
              <K label="Höchster Median" wert={eur(aw.hoechster_median)} />
              <K label="Niedrigster Median" wert={eur(aw.niedrigster_median)} />
            </div>
            {(verlauf?.wochen || []).length > 0 && (
              <div className="mt-3" data-testid="markt-wochen">
                <div className="text-[12px] text-zinc-400 mb-1">Wochenmedian (Median der Tagesmediane)</div>
                <div className="flex flex-wrap gap-2 text-[12px]">
                  {verlauf.wochen.map((w) => <span key={w.woche} className="rounded-lg px-2 py-1" style={{ background: "var(--wa-06)" }}>{w.woche}: <b>{eur(w.median)}</b> <span className="text-zinc-500">({w.tage} T.)</span></span>)}
                </div>
              </div>
            )}
            <details className="mt-3">
              <summary className="text-[12px] text-zinc-400 cursor-pointer">Tagestabelle ({reihe.length} Tage)</summary>
              <div className="overflow-x-auto mt-2">
                <table className="w-full text-[12px] min-w-[620px]" data-testid="markt-tagestabelle">
                  <thead><tr className="text-left text-zinc-500 text-[11px] uppercase"><th className="py-1 pr-3">Datum</th><th className="py-1 pr-3 text-right">Fahrzeuge</th><th className="py-1 pr-3 text-right">Min</th><th className="py-1 pr-3 text-right">Low-Market-Median</th><th className="py-1 pr-3 text-right">Durchschnitt</th><th className="py-1 pr-3 text-right">Max</th><th className="py-1 pr-3 text-right">Veränderung</th></tr></thead>
                  <tbody>{reihe.map((r) => (
                    <tr key={r.date} className="border-t border-white/5 tabular-nums" style={r.kein_angebot ? { color: "var(--text-dim)" } : undefined}>
                      <td className="py-1 pr-3 text-zinc-300">{r.date}{(r.laeufe?.length || 0) > 1 && <span className="ml-1 text-[10px] text-zinc-500" data-testid={`markt-laeufe-${r.date}`}>{r.laeufe.length} Läufe</span>}
                        {/* Nr. 58: Tag mit 0 Treffern als Marker, nicht als Sprung */}
                        {r.kein_angebot && <span className="ml-1 text-[10px]" style={{ color: "var(--st-amber)" }} data-testid={`markt-kein-angebot-${r.date}`}>kein Angebot</span>}
                        {/* Phase C: nur ungültige Läufe = Datenlücke, keine Marktlücke; schlechte Datenqualität markiert */}
                        {r.nur_ungueltig && <span className="ml-1 text-[10px]" style={{ color: "var(--st-rot)" }} data-testid={`markt-nur-ungueltig-${r.date}`}>nur ungültige Läufe</span>}
                        {!r.nur_ungueltig && r.data_quality === "POOR" && <span className="ml-1 text-[10px]" style={{ color: "var(--st-rot)" }} data-testid={`markt-tag-schlecht-${r.date}`}>Datenqualität schlecht</span>}</td><td className="py-1 pr-3 text-right">{r.sample_size}</td>
                      <td className="py-1 pr-3 text-right">{eur(r.min)}</td><td className="py-1 pr-3 text-right text-white">{eur(r.median)}</td>
                      <td className="py-1 pr-3 text-right">{eur(r.avg)}</td><td className="py-1 pr-3 text-right">{eur(r.max)}</td>
                      <td className="py-1 pr-3 text-right" style={{ color: trendFarbe(r.change_eur) }}>{r.change_pct == null ? "—" : pct(r.change_pct)}</td>
                    </tr>))}</tbody>
                </table>
              </div>
            </details>
          </>
        )}
      </Card>

      {/* Die N günstigsten des letzten gültigen Laufs */}
      <Card padded={false} data-testid="markt-listings">
        <div className="px-4 py-3 text-[13px] font-semibold text-white" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          Aktuell {listings?.listings?.length || 0} günstigste Fahrzeuge {listings?.date ? `(Stand ${listings.date}${listings.lauf_tag && listings.lauf_tag.includes("#") ? `, letzter Lauf ${listings.lauf_tag.split("#")[1]}` : ""})` : ""}
          {listings?.top_n_bewiesen === false && <span className="ml-2 text-[11px] font-normal" style={{ color: "var(--st-amber)" }}>Top-N nicht bewiesen</span>}
        </div>
        {!listings?.listings?.length ? <EmptyState title="Noch keine Fahrzeuge" /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] min-w-[1100px]">
              <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                <th className="px-3 py-2">#</th><th className="px-3 py-2">Fahrzeug</th><th className="px-3 py-2 text-right">Preis</th><th className="px-3 py-2 text-right">km</th>
                <th className="px-3 py-2">EZ</th><th className="px-3 py-2">Motor</th><th className="px-3 py-2">Getriebe</th><th className="px-3 py-2">Ort</th><th className="px-3 py-2">Verkäufer</th>
                <th className="px-3 py-2">mobile-Bewertung</th><th className="px-3 py-2">bei mobile seit</th><th className="px-3 py-2">erstmals gesehen</th><th className="px-3 py-2 text-right">seit Erstbeob.</th><th className="px-3 py-2 text-right">gestern</th>
              </tr></thead>
              <tbody>{listings.listings.map((l) => (
                <tr key={l.listing_id} className="border-t border-white/5 tabular-nums" data-testid={`markt-listing-${l.listing_id}`}>
                  <td className="px-3 py-1.5 text-zinc-400">{l.rank_today}</td>
                  <td className="px-3 py-1.5"><button type="button" className="text-white hover:underline text-left" onClick={() => setOffen(l.listing_id)} data-testid={`markt-listing-oeffnen-${l.listing_id}`}>{l.title || `${l.make} ${l.model} ${l.variant}`}</button></td>
                  <td className="px-3 py-1.5 text-right text-white">{eur(l.price_today ?? l.current_price)}{l.price_change_eur ? <span className="ml-1" style={{ color: trendFarbe(l.price_change_eur) }}>({trendText(l.price_change_eur)})</span> : null}</td>
                  <td className="px-3 py-1.5 text-right">{l.mileage_km?.toLocaleString("de-DE")}</td>
                  <td className="px-3 py-1.5">{l.first_registration}</td>
                  <td className="px-3 py-1.5">{l.power_kw ? `${l.power_kw} kW` : ""} {l.fuel}</td>
                  <td className="px-3 py-1.5">{l.gearbox}</td>
                  <td className="px-3 py-1.5">{l.postal_code} {l.city}</td>
                  <td className="px-3 py-1.5">{l.seller_type === "DEALER" ? "Händler" : l.seller_type === "PRIVATE" ? "Privat" : l.seller_type}</td>
                  <td className="px-3 py-1.5">{l.price_rating_today || l.price_rating?.rating || "—"}</td>
                  <td className="px-3 py-1.5">{datumKurz(l.mobile_created_at)}</td>
                  {/* Reparaturwelle 6 Nr. 139: erstmals gesehen IN DIESEM SEGMENT (global kann es älter sein) */}
                  <td className="px-3 py-1.5">{datumKurz(l.segment_first_seen_at || l.first_seen_at)}
                    {l.neu_im_segment && <span className="ml-1 text-[10px]" style={{ color: "var(--st-gruen)" }} data-testid={`markt-neu-segment-${l.listing_id}`}>neu in diesem Segment</span>}
                    {l.segment_first_seen_at && l.first_seen_at && l.segment_first_seen_at.slice(0, 10) !== l.first_seen_at.slice(0, 10) ? <span className="ml-1 text-[10px] text-zinc-500" title="erstmals in irgendeinem Segment">(global {datumKurz(l.first_seen_at)})</span> : null}</td>
                  <td className="px-3 py-1.5 text-right" style={{ color: trendFarbe(l.change_since_first_eur) }}>{l.change_since_first_eur ? trendText(l.change_since_first_eur) : "—"}</td>
                  <td className="px-3 py-1.5 text-right text-zinc-400">{l.rank_yesterday ?? "neu"}</td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Card>
      {offen && <ListingHistorie listingId={offen} onClose={() => setOffen(null)} />}
    </div>
  );
}

/** Private Deals (Ahmad 26.09.2026 abends): die günstigsten Privatangebote im Sample dieses Segments —
 *  "#1 … #2 … #3 …", "2 von 3" oder "keine Privatangebote unter den N günstigsten". Nur Super-Admin. */
function PrivatBlock({ privat, segment }) {
  const top = privat.top3 || [];
  const n = privat.sample_size || segment.max_items || "N";
  return (
    <Card data-testid="markt-privat-block">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[13px] font-semibold text-white">Günstigste Privatangebote im Sample
          {top.length > 0 && top.length < 3 && <span className="ml-2 text-[11px] font-normal text-zinc-400" data-testid="markt-privat-anzahl">{top.length} von 3</span>}
          {privat.stale && <span className="ml-2 text-[11px] font-normal" style={{ color: "var(--st-amber)" }} data-testid="markt-privat-stale">stale (letzter gültiger Lauf {datumZeit(privat.stand_at)})</span>}
        </div>
        <Link to="/admin/markt/private-deals" className="text-[11px] underline text-zinc-400 hover:text-white" data-testid="markt-privat-alle">alle Private Deals</Link>
      </div>
      {top.length === 0 ? (
        <div className="mt-1 text-[12px] text-zinc-500" data-testid="markt-privat-leer">
          {privat.stand_at ? `keine Privatangebote unter den ${n} günstigsten` : "noch kein gültiger Lauf"}
        </div>
      ) : (
        <ul className="mt-1 space-y-0.5 text-[12px]">
          {top.map((d) => {
            const link = mobileLink(d.url);
            return (
              <li key={d.listing_id} className="tabular-nums flex flex-wrap items-center gap-x-2" data-testid={`markt-privat-${d.listing_id}`}>
                <Badge tone={d.current_rank_private === 1 ? "green" : "blue"}>#{d.current_rank_private} PRIVAT</Badge>
                <span className="text-white">{eur(d.current_price)}</span>
                <span className="text-zinc-400">{d.title || d.listing_id} · {d.mileage_km?.toLocaleString("de-DE")} km · EZ {d.first_registration} · {[d.postal_code, d.city].filter(Boolean).join(" ")}</span>
                <span style={{ color: trendFarbe(d.difference_to_segment_median_eur) }}>{d.difference_to_segment_median_eur == null ? "" : `${trendText(d.difference_to_segment_median_eur)} (${pct(d.difference_to_segment_median_pct)}) zum Median`}</span>
                {d.heute_neu && <Badge tone="green">heute neu</Badge>}
                {d.preis_reduziert && <Badge tone="yellow">Preis reduziert</Badge>}
                {link && <a href={link} target="_blank" rel="noopener noreferrer" className="underline text-zinc-300" data-testid={`markt-privat-link-${d.listing_id}`}>Inserat öffnen</a>}
              </li>
            );
          })}
        </ul>
      )}
      <div className="mt-1 text-[10px] text-zinc-500">{privat.anzahl_im_sample != null ? `${privat.anzahl_im_sample} Privatangebot(e) unter den ${n} günstigsten · ` : ""}aus dem vorhandenen Sample, kein eigener Crawl.</div>
    </Card>
  );
}

function K({ label, wert, farbe, gross, unter, testid }) {
  return (
    <div className="rounded-lg p-2" style={{ background: "var(--wa-06)" }} data-testid={testid}>
      <div className="text-[10px] text-zinc-500">{label}</div>
      <div className={`${gross ? "text-lg" : "text-[13px]"} font-semibold`} style={{ color: farbe || "#fff" }}>{wert}</div>
      {unter && <div className="text-[10px] text-zinc-500">{unter}</div>}
    </div>
  );
}

function ListingHistorie({ listingId, onClose }) {
  const [d, setD] = useState(null);
  const [fehler, setFehler] = useState("");
  useEffect(() => {
    let aktiv = true;   // admin4: Antwort eines vorigen Inserats verwerfen
    setD(null);
    setFehler("");
    api.get(`/admin/market/listings/${listingId}/history`)
      .then((r) => { if (aktiv) setD(r.data); })
      .catch((e) => { if (aktiv) setFehler(errMsg(e, "Historie nicht ladbar")); });
    return () => { aktiv = false; };
  }, [listingId]);
  const l = d?.listing;
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-3" style={{ background: "rgba(0,0,0,.6)" }} onClick={onClose}>
      <div className="w-full max-w-2xl rounded-2xl p-4 max-h-[85vh] overflow-y-auto" style={{ background: "var(--bg-elevated, #18181b)", border: "1px solid var(--wa-12)" }}
           onClick={(e) => e.stopPropagation()} data-testid="markt-listing-historie">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-[15px] font-semibold text-white">{l?.title || listingId}</div>
            {l && <div className="text-[11px] text-zinc-500">{l.mileage_km?.toLocaleString("de-DE")} km · EZ {l.first_registration} · {l.postal_code} {l.city} · <Badge tone={l.active_state === "seen" ? "green" : l.active_state === "confirmed_removed" ? "red" : "yellow"}>{zustandText(l.active_state)}</Badge></div>}
          </div>
          <button type="button" onClick={onClose} className="text-zinc-400 hover:text-white" aria-label="Schließen"><X size={18} /></button>
        </div>
        {fehler && <div className="text-red-300 text-sm mt-2">{fehler}</div>}
        {d && (
          <>
            {d.hinweis_zustand && <div className="mt-2 text-[11px] text-zinc-400">{d.hinweis_zustand}</div>}
            <div className="mt-3 text-[12px] text-zinc-400">Preisverlauf (jede Änderung)</div>
            <ul className="mt-1 text-[13px] space-y-0.5" data-testid="markt-preisverlauf">
              {(l?.price_history || []).map((p, i) => <li key={i} className="tabular-nums">{datumKurz(p.at)} <b className="text-white">{eur(p.price)}</b></li>)}
            </ul>
            <div className="mt-3 text-[12px] text-zinc-400">Beobachtungen ({d.snapshots?.length || 0}){d.gekuerzt ? <span className="ml-1" style={{ color: "var(--st-amber)" }} data-testid="markt-historie-gekuerzt">— gekürzt auf die ersten {d.snapshots?.length || 0}</span> : null}</div>
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[420px]"><thead><tr className="text-left text-zinc-500 text-[11px] uppercase"><th className="py-1 pr-3">Datum</th><th className="py-1 pr-3">Segment</th><th className="py-1 pr-3 text-right">Preis</th><th className="py-1 pr-3 text-right">Platz</th><th className="py-1 pr-3">Bewertung</th></tr></thead>
                <tbody>{(d.snapshots || []).map((s, i) => <tr key={i} className="border-t border-white/5 tabular-nums"><td className="py-1 pr-3">{s.date}</td><td className="py-1 pr-3 text-zinc-400">{s.segment_id}</td><td className="py-1 pr-3 text-right">{eur(s.price)}</td><td className="py-1 pr-3 text-right">{s.rank_in_sample}</td><td className="py-1 pr-3">{s.price_rating || "—"}</td></tr>)}</tbody></table>
            </div>
            {l?.url && <a href={l.url} target="_blank" rel="noopener noreferrer" className="inline-block mt-3 text-[12px] underline text-zinc-300">Inserat bei mobile.de öffnen</a>}
          </>
        )}
      </div>
    </div>
  );
}
