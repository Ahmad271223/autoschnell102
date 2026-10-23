import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ExternalLink, BarChart3, RefreshCw, UserRound } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { kmAusText, preisAusText } from "@/lib/preis";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState } from "./_ui";
import { PRIVATE_SORTIERUNGEN, datumKurz, datumZeit, eur, mobileLink, pct, trendFarbe, trendText } from "@/lib/markt";

/**
 * Admin → Marktanalyse → Private Deals (Wunsch Ahmad 26.09.2026 abends): die 3 günstigsten
 * Privatangebote je Segment — ausschließlich aus dem Sample, das die Marktanalyse ohnehin je Lauf
 * abruft (kein eigener Privat-Crawl, keine Zusatzkosten). Nur der Super-Admin sieht diese Seite;
 * nichts davon landet in den Chancen der Firmen oder in der Fahrzeugkarte.
 */
const FILTER_LEER = { make: "", model_id: "", ez: "", km_min: "", km_max: "", preis_von: "", preis_bis: "", abstand_pct_max: "", plz: "",
                      heute_neu: false, preis_reduziert: false, sort: "abstand_pct", nur_aktuell: true };

// Startpruefung 28.09.2026: die Zahlenfelder liefen durch Number(v) — "km von 15.000" wurde 15 km,
// "Preis bis 20.000" 20 €, "abc" ging als NaN an den Server. Jetzt dieselben Helfer wie Markt-Budget und
// km-Bereiche (lib/preis): deutsche Schreibweise, Unlesbares wird gemeldet statt still gesendet.
function prozentAusText(v) {
  const t = String(v).replace(/[%\s  ]/g, "").replace(/^−/, "-").replace(",", ".");
  return /^[-+]?\d+(\.\d+)?$/.test(t) ? Number(t) : NaN;
}
const ZAHL_FELDER = {
  ez: { label: "EZ (Jahr)", lesen: (v) => (/^\d{4}$/.test(v) ? Number(v) : NaN) },
  km_min: { label: "km von", lesen: kmAusText },
  km_max: { label: "km bis", lesen: kmAusText },
  preis_von: { label: "Preis von", lesen: (v) => preisAusText(v) ?? NaN },
  preis_bis: { label: "Preis bis", lesen: (v) => preisAusText(v) ?? NaN },
  abstand_pct_max: { label: "Abstand zum Median", lesen: prozentAusText },
};

/** Filter -> GET-Parameter; `fehler` nennt jedes Zahlenfeld, das sich nicht lesen laesst (dann nicht laden). */
export function filterLesen(f) {
  const params = { sort: f.sort || "abstand_pct", limit: 300, nur_aktuell: f.nur_aktuell !== false };
  const fehler = [];
  for (const k of ["make", "model_id", "ez", "km_min", "km_max", "preis_von", "preis_bis", "abstand_pct_max", "plz"]) {
    const v = String(f[k] ?? "").trim();
    if (v === "") continue;
    const zahl = ZAHL_FELDER[k];
    if (!zahl) { params[k] = v; continue; }
    const n = zahl.lesen(v);
    if (typeof n === "number" && Number.isFinite(n)) params[k] = n;
    else fehler.push(`${zahl.label}: „${v}“ ist keine gültige Angabe`);
  }
  if (f.heute_neu) params.heute_neu = true;
  if (f.preis_reduziert) params.preis_reduziert = true;
  return { params, fehler };
}

export function filterParams(f) {
  return filterLesen(f).params;
}

export default function MarktPrivateDeals() {
  const [filter, setFilter] = useState(FILTER_LEER);
  const [daten, setDaten] = useState(null);
  const [modelle, setModelle] = useState([]);
  const [fehler, setFehler] = useState("");
  const [laedt, setLaedt] = useState(false);

  const laden = useCallback(async (f) => {
    const { params, fehler: unlesbar } = filterLesen(f);
    if (unlesbar.length) { setFehler(`Filter nicht übernommen — ${unlesbar.join("; ")}.`); return; }
    setLaedt(true);
    try {
      const r = await api.get("/admin/market/private-deals", { params, timeout: 15000 });
      setDaten(r.data);
      setFehler("");
    } catch (e) { setFehler(errMsg(e, "Private Deals konnten nicht geladen werden")); }
    finally { setLaedt(false); }
  }, []);
  useEffect(() => { laden(FILTER_LEER); }, [laden]);
  useEffect(() => { api.get("/admin/market/models").then((r) => setModelle(r.data?.modelle || [])).catch(() => {}); }, []);

  // EZ laedt beim Tippen sofort — aber erst, wenn das Jahr vollstaendig (oder das Feld leer) ist.
  const ezFertig = (v) => /^(\d{4})?$/.test(String(v).trim());
  const setzen = (k, v) => { const f = { ...filter, [k]: v }; setFilter(f); if (typeof v === "boolean" || k === "sort" || k === "model_id" || (k === "ez" && ezFertig(v))) laden(f); };
  const anwenden = () => laden(filter);
  const zuruecksetzen = () => { setFilter(FILTER_LEER); laden(FILTER_LEER); };

  const feld = "rounded-lg px-2.5 py-1.5 text-[12px] outline-none w-full";
  const st = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };
  const z = daten?.zusammenfassung || {};
  const deals = daten?.deals || [];
  return (
    <div data-testid="private-deals-seite">
      <Link to="/admin/markt" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Marktanalyse</Link>
      <PageHeader title="Private Deals" subtitle="Die 3 günstigsten Privatangebote je Segment — aus dem ohnehin abgerufenen Sample, kein eigener Crawl, keine Zusatzkosten. Nur für den Betreiber."
                  action={<Button variant="outline" size="sm" onClick={() => laden(filter)} disabled={laedt}><RefreshCw size={14} /> Aktualisieren</Button>} />

      {fehler && <Card className="mb-4" data-testid="private-deals-fehler"><div className="text-red-300 text-sm">{fehler}</div></Card>}

      <Card className="mb-4" data-testid="private-deals-zusammenfassung">
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-[12px]">
          <Kachel label="Segmente aktiv" wert={String(z.segmente_aktiv ?? "—")} />
          <Kachel label="Segmente mit Privat-Deals" wert={String(z.segmente_mit_deals ?? "—")} />
          <Kachel label="Aktuelle Top-3-Angebote" wert={String(z.aktuelle_top3 ?? "—")} tone="text-emerald-300" />
          <Kachel label="Heute neu" wert={String(z.heute_neu ?? "—")} />
          <Kachel label="Heute reduziert" wert={String(z.heute_reduziert ?? "—")} />
        </div>
        <div className="mt-2 text-[11px] text-zinc-500">{daten?.hinweis || ""}</div>
      </Card>

      <Card className="mb-4" data-testid="private-deals-filter">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 text-[11px] text-zinc-400">
          <label>Marke<input className={feld} style={st} value={filter.make} onChange={(e) => setzen("make", e.target.value)} placeholder="z. B. BMW" data-testid="pd-filter-make" /></label>
          <label>Suchauftrag
            <select className={feld} style={st} value={filter.model_id} onChange={(e) => setzen("model_id", e.target.value)} data-testid="pd-filter-modell">
              <option value="">alle</option>
              {modelle.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select></label>
          <label>EZ (Jahr)<input className={feld} style={st} value={filter.ez} onChange={(e) => setzen("ez", e.target.value)} inputMode="numeric" placeholder="2020" data-testid="pd-filter-ez" /></label>
          <label>km von<input className={feld} style={st} value={filter.km_min} onChange={(e) => setzen("km_min", e.target.value)} inputMode="numeric" data-testid="pd-filter-km-min" /></label>
          <label>km bis<input className={feld} style={st} value={filter.km_max} onChange={(e) => setzen("km_max", e.target.value)} inputMode="numeric" data-testid="pd-filter-km-max" /></label>
          <label>PLZ beginnt mit<input className={feld} style={st} value={filter.plz} onChange={(e) => setzen("plz", e.target.value)} placeholder="80" data-testid="pd-filter-plz" /></label>
          <label>Preis von<input className={feld} style={st} value={filter.preis_von} onChange={(e) => setzen("preis_von", e.target.value)} inputMode="numeric" data-testid="pd-filter-preis-von" /></label>
          <label>Preis bis<input className={feld} style={st} value={filter.preis_bis} onChange={(e) => setzen("preis_bis", e.target.value)} inputMode="numeric" data-testid="pd-filter-preis-bis" /></label>
          <label>Abstand zum Median höchstens (%)<input className={feld} style={st} value={filter.abstand_pct_max} onChange={(e) => setzen("abstand_pct_max", e.target.value)} placeholder="-5" data-testid="pd-filter-abstand" /></label>
          <label>Sortierung
            <select className={feld} style={st} value={filter.sort} onChange={(e) => setzen("sort", e.target.value)} data-testid="pd-filter-sort">
              {PRIVATE_SORTIERUNGEN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></label>
          <label className="flex items-end gap-1.5 pb-1.5"><input type="checkbox" checked={filter.heute_neu} onChange={(e) => setzen("heute_neu", e.target.checked)} data-testid="pd-filter-heute-neu" /> nur heute neu</label>
          <label className="flex items-end gap-1.5 pb-1.5"><input type="checkbox" checked={filter.preis_reduziert} onChange={(e) => setzen("preis_reduziert", e.target.checked)} data-testid="pd-filter-reduziert" /> nur Preis reduziert</label>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={anwenden} disabled={laedt} data-testid="pd-filter-anwenden">Filter anwenden</Button>
          <Button size="sm" variant="ghost" onClick={zuruecksetzen} disabled={laedt}>Zurücksetzen</Button>
          <span className="ml-auto inline-flex rounded-full overflow-hidden text-[11px]" style={{ border: "1px solid var(--wa-12)" }} data-testid="pd-umschalter">
            <button type="button" className="px-3 py-1" aria-pressed={filter.nur_aktuell} data-testid="pd-umschalter-aktuell"
                    style={{ background: filter.nur_aktuell ? "var(--wa-12)" : "transparent", color: "var(--text-primary)" }} onClick={() => setzen("nur_aktuell", true)}>aktuell</button>
            <button type="button" className="px-3 py-1" aria-pressed={!filter.nur_aktuell} data-testid="pd-umschalter-historisch"
                    style={{ background: !filter.nur_aktuell ? "var(--wa-12)" : "transparent", color: "var(--text-primary)" }} onClick={() => setzen("nur_aktuell", false)}>historisch (auch herausgefallene)</button>
          </span>
        </div>
      </Card>

      <Card padded={false} data-testid="private-deals-liste">
        <div className="px-4 py-3 text-[13px] text-zinc-400" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <UserRound size={14} className="inline mr-1" /> {deals.length} Privatangebot{deals.length === 1 ? "" : "e"}{daten?.gekuerzt ? " (gekürzt — Filter enger setzen)" : ""}{laedt ? " · lädt…" : ""}
        </div>
        {!daten && !fehler ? <div className="flex items-center gap-2 text-zinc-500 text-sm p-4"><Spinner /> lade…</div>
          : deals.length === 0 ? <EmptyState title="Keine Privatangebote" hint="In den abgerufenen Samples stehen derzeit keine Privatangebote zu diesen Filtern." /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[1180px]">
                <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                  <th className="px-3 py-2">Segment</th><th className="px-3 py-2">Rang</th><th className="px-3 py-2">Fahrzeug</th>
                  <th className="px-3 py-2 text-right">Preis</th><th className="px-3 py-2 text-right">km</th><th className="px-3 py-2">EZ</th><th className="px-3 py-2">Ort</th>
                  <th className="px-3 py-2 text-right">Segment-Median</th><th className="px-3 py-2 text-right">Abstand</th>
                  <th className="px-3 py-2">bei mobile.de seit</th><th className="px-3 py-2">AutoSchnell erstmals</th><th className="px-3 py-2">Hinweise</th><th className="px-3 py-2 text-right">Aktion</th>
                </tr></thead>
                <tbody>{deals.map((d) => <DealZeile key={`${d.segment_id}:${d.listing_id}`} d={d} />)}</tbody>
              </table>
            </div>
          )}
      </Card>
    </div>
  );
}

function DealZeile({ d }) {
  const link = mobileLink(d.url);
  const rang = d.currently_top3 ? `#${d.current_rank_private} PRIVAT` : `war #${d.best_rank_private || "?"}`;
  return (
    <tr className="border-t border-white/5 tabular-nums" data-testid={`pd-deal-${d.listing_id}`}>
      <td className="px-3 py-1.5"><div className="text-white">{d.segment_label || d.model_id}</div><div className="text-[10px] text-zinc-500">{[d.ez_label, d.km_label].filter(Boolean).join(" · ")}</div></td>
      <td className="px-3 py-1.5"><Badge tone={d.currently_top3 ? (d.current_rank_private === 1 ? "green" : "blue") : "gray"}>{rang}</Badge></td>
      <td className="px-3 py-1.5"><div className="text-white">{d.title || [d.make, d.model, d.variant].filter(Boolean).join(" ")}</div>
        <div className="text-[10px] text-zinc-500">{d.power_kw ? `${d.power_kw} kW · ` : ""}{d.fuel || ""}{d.gearbox ? ` · ${d.gearbox}` : ""} · Platz {d.rank_in_sample} im Sample</div></td>
      <td className="px-3 py-1.5 text-right text-white">{eur(d.current_price)}{d.price_change_since_first_eur ? <div className="text-[10px]" style={{ color: trendFarbe(d.price_change_since_first_eur) }}>{trendText(d.price_change_since_first_eur)} seit Top-3</div> : null}</td>
      <td className="px-3 py-1.5 text-right">{d.mileage_km != null ? d.mileage_km.toLocaleString("de-DE") : "—"}</td>
      <td className="px-3 py-1.5">{d.first_registration || "—"}</td>
      <td className="px-3 py-1.5">{[d.postal_code, d.city].filter(Boolean).join(" ") || "—"}</td>
      <td className="px-3 py-1.5 text-right">{eur(d.segment_median)}</td>
      <td className="px-3 py-1.5 text-right" style={{ color: trendFarbe(d.difference_to_segment_median_eur) }} data-testid={`pd-abstand-${d.listing_id}`}>
        {d.difference_to_segment_median_eur == null ? "—" : `${trendText(d.difference_to_segment_median_eur)} (${pct(d.difference_to_segment_median_pct)})`}</td>
      <td className="px-3 py-1.5">{datumKurz(d.mobile_created_at)}</td>
      <td className="px-3 py-1.5">{datumKurz(d.first_entered_top3_at)}</td>
      <td className="px-3 py-1.5">
        <div className="flex flex-wrap gap-1">
          {d.heute_neu && <Badge tone="green">heute neu</Badge>}
          {d.preis_reduziert && <Badge tone="yellow">Preis reduziert</Badge>}
          {d.stale && <span className="text-[10px]" style={{ color: "var(--st-amber)" }} data-testid={`pd-stale-${d.listing_id}`}>stale (letzter gültiger Lauf {datumZeit(d.stand_at)})</span>}
          {!d.currently_top3 && d.left_top3_at && <span className="text-[10px] text-zinc-500">herausgefallen {datumKurz(d.left_top3_at)}</span>}
          {d.active_state === "confirmed_removed" && <Badge tone="red">nicht mehr online</Badge>}
        </div>
      </td>
      <td className="px-3 py-1.5 text-right whitespace-nowrap">
        {link ? <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] underline text-zinc-300 mr-2" data-testid={`pd-inserat-${d.listing_id}`}><ExternalLink size={12} /> Inserat öffnen</a>
              : <span className="text-[11px] text-zinc-600 mr-2" data-testid={`pd-kein-link-${d.listing_id}`}>kein Link</span>}
        <Link to={`/admin/markt/${d.model_id}?segment=${encodeURIComponent(d.segment_id)}`} className="inline-flex items-center gap-1 text-[11px] underline text-zinc-300" data-testid={`pd-segment-${d.listing_id}`}><BarChart3 size={12} /> Segmentanalyse</Link>
      </td>
    </tr>
  );
}

function Kachel({ label, wert, tone = "" }) {
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--wa-06)" }}>
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className={`text-[13px] font-semibold text-white ${tone}`}>{wert}</div>
    </div>
  );
}
