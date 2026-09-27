import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, Flame } from "lucide-react";
import { Bar, BarChart, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Card, Badge, Spinner, EmptyState } from "./_ui";
import {
  BERICHT_ALT_TEXT, BERICHT_TYP, CONFIDENCE, DATENQUALITAET, HOTDEAL_KLASSE, LIQUIDITAET, MARKTTIEFE, PLAN_STATUS, RICHTUNG,
  berichtAltesSchema, berichtDiagrammPunkt, datumKurz, datumZeit, eur, pct, periodeText, richtungAusPct, richtungFarbe, trendText,
} from "@/lib/markt";

/**
 * Admin → Marktanalyse → Berichte → Modell (Master-Auftrag Phase E, Abschnitte 13–21, 45–47, 52–55):
 * ein Bericht je Modell und Periode. Final = eingefroren (ändert sich nie mehr), laufende Periode = vorläufig
 * (live gerechnet, nie gespeichert). Diagramm je Kalendertag mit Lücken (keine Interpolation), zweite Ansicht
 * für Listings / neue Listings / Preisänderungen, Tagestabelle mit Farblogik (fällt = grün, steigt = rot,
 * stabil = neutral), Fallen/Steigen/Stabil, 5-Tage-Blöcke, Segmentdetail, Hot Deals des Zeitraums.
 */
const ZWEITE = [["listings", "Anzahl Listings"], ["neue", "neue Listings"], ["preis", "Preisänderungen"]];
const GETRIEBE = { AUTOMATIC_GEAR: "Automatik", MANUAL_GEAR: "Schaltung", SEMIAUTOMATIC_GEAR: "Halbautomatik" };
// Diagramm-Farben über Tokens, damit die helle Ansicht lesbar bleibt (keine festen Dunkel-Werte)
const ACHSE = { fill: "var(--text-secondary)" };
const TOOLTIP_STIL = { background: "var(--bg-surface)", border: "1px solid var(--wa-12)", color: "var(--text-primary)", fontSize: 12 };
const TOOLTIP_LABEL = { color: "var(--text-primary)" };

export default function MarktBericht() {
  const { modell: modellId } = useParams();
  const [params, setParams] = useSearchParams();
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;
  const [liste, setListe] = useState(null);
  const [bericht, setBericht] = useState(null);
  const [fehler, setFehler] = useState("");
  const [leer, setLeer] = useState(false);
  const [zweite, setZweite] = useState("listings");
  const [block, setBlock] = useState("");
  const typ = params.get("typ") || "";
  const von = params.get("von") || "";
  const bis = params.get("bis") || "";

  useEffect(() => {
    let aktiv = true;
    api.get(`/admin/market/reports/model/${modellId}/list`).then((r) => {
      if (!aktiv) return;
      setListe(r.data);
      if (!params.get("typ")) {
        // Vorwahl: neuester finaler Bericht, sonst der laufende MONAT (die meisten Tage — der laufende 5-Tage-Block
        // ist vor dem ersten Tagesabruf oft noch leer), sonst die erste laufende Periode
        const f = r.data?.final?.[0];
        const lauf = r.data?.laufend || [];
        const l = lauf.find((p) => p.typ === "MONTHLY") || lauf[0];
        const ziel = f ? { typ: f.typ, von: f.periode_von, bis: f.periode_bis } : l ? { typ: l.typ, von: l.von, bis: l.bis } : null;
        if (ziel) setParams(new URLSearchParams(ziel), { replace: true });
      }
    }).catch((e) => { if (aktiv) setFehler(errMsg(e, "Berichte konnten nicht geladen werden")); });
    return () => { aktiv = false; };
  }, [modellId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!typ || !von || !bis) return undefined;
    let aktiv = true;
    setBericht(null);
    setBlock("");
    setFehler("");
    setLeer(false);
    api.get(`/admin/market/reports/model/${modellId}`, { params: { typ, von, bis }, timeout: 20000 })
      .then((r) => { if (aktiv) { setBericht(r.data); setFehler(""); } })
      .catch((e) => {
        if (!aktiv) return;
        // 404 = in dieser Periode noch kein Tageswert: leerer Zustand, kein Fehler (Periodenwahl bleibt bedienbar)
        if (e?.response?.status === 404) setLeer(true);
        else setFehler(errMsg(e, "Bericht konnte nicht geladen werden"));
      });
    return () => { aktiv = false; };
  }, [modellId, typ, von, bis]);

  const optionen = useMemo(() => [
    ...(liste?.final || []).map((b) => ({ wert: `${b.typ}|${b.periode_von}|${b.periode_bis}`,
                                          text: `${BERICHT_TYP[b.typ]} ${periodeText(b.periode_von, b.periode_bis)} · final${berichtAltesSchema(b) ? ` · ${BERICHT_ALT_TEXT}` : ""}` })),
    ...(liste?.laufend || []).map((p) => ({ wert: `${p.typ}|${p.von}|${p.bis}`, text: `${BERICHT_TYP[p.typ]} ${periodeText(p.von, p.bis)} · vorläufig` })),
  ], [liste]);
  const waehlen = (wert) => { const [t, a, b] = wert.split("|"); setParams(new URLSearchParams({ typ: t, von: a, bis: b })); };

  const tage = useMemo(() => {
    const alle = bericht?.tage || [];
    if (!block) return alle;
    const [a, b] = block.split("|");
    return alle.filter((r) => r.date >= a && r.date <= b);
  }, [bericht, block]);
  // Medianlinie = Korbwert (Schema 2: an Teilabdeckungstagen verkettet); ältere Berichte: Teilabdeckung = Lücke
  const reihe = useMemo(() => tage.map((r) => ({ ...berichtDiagrammPunkt(r), tag: datumKurz(r.date),
                                                  preis: (r.preissenkungen || 0) + (r.preiserhoehungen || 0) })), [tage]);
  const mitKorb = (bericht?.tage || []).some((t) => t.median_korb !== undefined);
  const altesSchema = berichtAltesSchema(bericht);
  // Tagestabelle: offene (künftige) Tage eines vorläufigen Berichts sind keine Lücke — sie werden nur gezählt
  const tabelle = useMemo(() => tage.filter((t) => !t.offen), [tage]);

  const k = bericht?.kennzahlen || {};
  const m = bericht?.modell || {};
  const r = RICHTUNG[k.richtung] || RICHTUNG.UNKNOWN;
  const bw = bericht?.bewegung || {};
  const final = bericht?.status === "FINAL";
  const zone = bericht?.stabil_zone_pct ?? 0.5;
  const segmentUnter = [k.segment_tage_erwartet != null ? `${k.segment_tage_gueltig ?? 0}/${k.segment_tage_erwartet} Segment-Tage` : "",
                        k.empty_segmente ? `${k.empty_segmente} nur leer` : ""].filter(Boolean).join(" · ");
  const kostenBasis = k.kosten_fassung_usd != null && k.kosten_fassung_usd !== k.kosten_usd
    ? ` (Basis: Kosten der gerechneten Fassung ${Number(k.kosten_fassung_usd).toFixed(2)} $)` : "";
  // Prüfung Runde 4: Planung laut Tagesplan-Protokoll (nur Berichte ab Schema 4 mit bekannten Abruf-Jobs)
  const planungTeile = [
    k.tage_tagesplan_ausfall ? `${k.tage_tagesplan_ausfall} Tag(e) Tagesplan lief nicht (Lücke)` : "",
    k.tage_planung_unbekannt ? `${k.tage_planung_unbekannt} Tag(e) Planung unbekannt` : "",
    k.tage_crawler_aus ? `${k.tage_crawler_aus} Tag(e) Crawler bewusst aus` : "",
    k.tage_budget ? `${k.tage_budget} Tag(e) Budget erschöpft` : "",
    k.budget_segment_tage ? `${k.budget_segment_tage} Segment-Tag(e) wegen Budget nicht geplant` : "",
    k.tage_ohne_plan ? `${k.tage_ohne_plan} Tag(e) ohne Plan` : "",
    k.abgelaufene_segment_tage ? `${k.abgelaufene_segment_tage} Segment-Tag(e) ohne tragbaren Wert` : "",
    k.vorab_segment_tage ? `${k.vorab_segment_tage} Segment-Tag(e) noch nicht beobachtet` : "",
  ].filter(Boolean);
  const mitPlanung = k.planung === "jobs" && k.kalendertage != null;
  return (
    <div data-testid="bericht-seite">
      <Link to="/admin/markt/berichte" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Berichte</Link>
      <div className="flex items-start justify-between gap-3 flex-wrap mb-4">
        <div>
          <h1 className="text-[22px] font-semibold text-white" data-testid="bericht-titel">{m.label || modellId}</h1>
          <div className="text-[12px] text-zinc-500">{m.fuel || ""}{m.gearbox ? ` · ${GETRIEBE[m.gearbox] || m.gearbox}` : ""}{bericht?.fassung ? ` · Fassung v${bericht.fassung.version}` : ""}
            {" · "}<Link to={`/admin/markt/${modellId}`} className="underline">Segmentanalyse</Link></div>
        </div>
        <select className="rounded-lg px-2.5 py-1.5 text-[12px] outline-none" style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" }}
                value={`${typ}|${von}|${bis}`} onChange={(e) => waehlen(e.target.value)} data-testid="bericht-periode">
          {!optionen.some((o) => o.wert === `${typ}|${von}|${bis}`) && <option value={`${typ}|${von}|${bis}`}>{BERICHT_TYP[typ] || typ} {periodeText(von, bis)}</option>}
          {optionen.map((o) => <option key={o.wert} value={o.wert}>{o.text}</option>)}
        </select>
      </div>

      {fehler && !bericht ? (
        <Card data-testid="bericht-fehler"><div className="text-red-300 text-sm">{fehler}</div>
          <Link to="/admin/markt/berichte" className="text-[12px] underline text-zinc-300">zur Berichtsübersicht</Link></Card>
      ) : !bericht ? (!typ ? <EmptyState title="Noch kein Bericht" hint="Für dieses Modell gibt es noch keine Tagesdaten in einer Berichtsperiode." />
        : leer ? <div data-testid="bericht-leer"><EmptyState title="Keine Tagesdaten in dieser Periode"
                   hint={`Für ${BERICHT_TYP[typ] || typ} ${periodeText(von, bis)} liegt noch kein gültiger Tageswert vor — oben eine andere Periode wählen (z. B. den Monat).`} /></div>
        : <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade Bericht…</div>) : (
        <div className="space-y-4">
          <Card data-testid="bericht-kopf">
            <div className="flex flex-wrap items-center gap-2 text-[13px]">
              <span className="text-white font-semibold">{BERICHT_TYP[bericht.typ]} · {periodeText(bericht.periode_von, bericht.periode_bis)}</span>
              <span data-testid="bericht-status">{final ? <Badge tone="blue">final · eingefroren {datumZeit(bericht.erstellt_at)}</Badge>
                : <Badge tone="yellow">vorläufig · Stand {datumZeit(bericht.stand_at)} · final ab {datumZeit(bericht.faellig_ab)}</Badge>}</span>
              {bericht.revision > 1 && <Badge tone="purple">Revision {bericht.revision}</Badge>}
              <Badge tone={r.tone}>{r.text}</Badge>
              {altesSchema && <span data-testid="bericht-schema-alt"><Badge tone="orange">{BERICHT_ALT_TEXT}</Badge></span>}
            </div>
            {altesSchema && (
              <div className="mt-2 text-[11px]" style={{ color: "var(--st-amber)" }} data-testid="bericht-schema-hinweis">
                Dieser Bericht wurde {BERICHT_ALT_TEXT} (Schema {bericht.schema ?? 1}): Periodenwerte (Median, Minimum, Maximum,
                Stichprobe), Tagesbewegung, Kosten je Einheit, Abdeckung, Segmentabdeckung und Confidence sind dort anders
                definiert (z. B. zählten nicht geplante Segmente der Budget-Rotation als Lücke, ein ganztägiger Ausfall des
                Tagesplans dagegen als „nicht geplant“, und „hoch“ gab es schon ab einem Tag) — mit neueren Berichten nur
                eingeschränkt vergleichbar. Eingefrorene Berichte werden nie neu gerechnet.
              </div>
            )}
            {(bericht.hinweise || []).length > 0 && (
              <ul className="mt-2 text-[11px] space-y-0.5" style={{ color: "var(--st-amber)" }} data-testid="bericht-hinweise">
                {bericht.hinweise.map((h) => <li key={h}>{h}</li>)}
              </ul>
            )}
            <div className="mt-3 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 text-[12px]" data-testid="bericht-kennzahlen">
              <K label="Startwert" wert={eur(k.startwert)} />
              <K label="Endwert" wert={eur(k.endwert)} />
              <K label="Differenz" wert={trendText(k.delta_eur, k.delta_pct)} farbe={r.farbe} unter={`gleicher Segmentkorb (${k.korb_segmente ?? 0} Segmente)`} testid="bericht-delta" />
              <K label="Median der Periode / Ø" wert={`${eur(k.median_periode)} / ${eur(k.mittelwert_periode)}`} />
              <K label="Niedrigster / höchster Tageswert" wert={`${eur(k.minimum?.wert)} / ${eur(k.maximum?.wert)}`}
                 unter={k.minimum ? `${datumKurz(k.minimum.date)} / ${datumKurz(k.maximum?.date)}` : ""} />
              <K label="Günstigstes Angebot" wert={eur(k.guenstigstes_angebot?.preis)} unter={k.guenstigstes_angebot ? datumKurz(k.guenstigstes_angebot.date) : ""} />
              <K label="Stichprobe (inkl. Mix)" wert={trendText(k.sample_market_change_eur, k.sample_market_change_pct)} farbe={richtungFarbe(richtungAusPct(k.sample_market_change_pct, zone))} unter="Änderung des täglichen Samples" testid="bericht-stichprobe" />
              <K label="Gleiche Inserate" wert={trendText(k.same_listing_price_change_eur, k.same_listing_price_change_pct)} unter={`${k.same_listing_anzahl ?? 0} Preispaare`} />
              <K label="Neue / verschwundene Listings" wert={`${k.neue_listings ?? 0} / ${k.verschwundene_listings ?? 0}`} unter="verschwunden ≠ verkauft" />
              <K label="Preissenkungen / -erhöhungen" wert={`${k.preissenkungen ?? 0} / ${k.preiserhoehungen ?? 0}`}
                 unter={`Ø ${trendText(k.mittlere_senkung_eur)} / ${trendText(k.mittlere_erhoehung_eur)} (gleiche Inserate)`} />
              <K label="Unterschiedliche Fahrzeuge" wert={String(k.unterschiedliche_listings ?? 0)} />
              <K label="Top-3- / Top-5-Wechsel" wert={`${k.top3_wechsel ?? 0} / ${k.top5_wechsel ?? 0}`} />
              <K label="Hot Deals / privat" wert={`${k.hot_deals ?? 0} / ${k.private_hot_deals ?? 0}`} unter={`${k.hot_deals_neu ?? 0} neu im Zeitraum`} testid="bericht-hot" />
              <K label="Datenqualität" wert={(DATENQUALITAET[k.data_quality] || DATENQUALITAET.UNKNOWN).kurz} farbe={(DATENQUALITAET[k.data_quality] || DATENQUALITAET.UNKNOWN).farbe} />
              <K label="Markttiefe / Liquidität" wert={`${(MARKTTIEFE[k.market_depth] || MARKTTIEFE.UNKNOWN).zaehler} / ${(LIQUIDITAET[k.liquiditaet] || LIQUIDITAET.UNKNOWN).text}`} />
              <K label="Abdeckung" wert={`${k.coverage_days ?? 0} / ${k.expected_days ?? 0} Tage`} unter={`Confidence ${(CONFIDENCE[k.confidence] || {}).text || "—"}${(k.confidence_gruende || []).length ? `: ${k.confidence_gruende.join(", ")}` : ""}${bericht.offen_ab ? ` · ab ${datumKurz(bericht.offen_ab)} noch offen` : ""}`} testid="bericht-abdeckung" />
              <K label="Segmente mit Daten" wert={`${k.segmente_mit_daten ?? 0} / ${k.segmente_gesamt ?? 0}`} unter={segmentUnter} testid="bericht-segmentabdeckung" />
              {mitPlanung && (
                <K label="Planung (Tagesplan-Protokoll)" wert={`${k.tage_mit_wert ?? 0} / ${k.kalendertage} Kalendertage mit Wert`}
                   unter={planungTeile.length ? planungTeile.join(" · ") : "jeder Tag planmäßig"} testid="bericht-planung" />
              )}
              <K label="Crawl-Kosten" wert={`${Number(k.kosten_usd || 0).toFixed(2)} $`}
                 unter={`je Beobachtung ${k.cost_per_valid_observation ?? "—"} $ · je Inserat ${k.cost_per_unique_listing ?? "—"} $ · je Hot Deal ${k.cost_per_hot_deal ?? "—"} $${kostenBasis}`} testid="bericht-kosten" />
            </div>
            <div className="mt-2 text-[10px] text-zinc-500">{bericht.hinweis}</div>
          </Card>

          <Card data-testid="bericht-verlauf">
            <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
              <div className="text-[13px] font-semibold text-white">Verlauf je Kalendertag</div>
              {(bericht.bloecke || []).length > 0 && (
                <div className="flex flex-wrap gap-1" data-testid="bericht-blockfilter">
                  <Chip aktiv={!block} onClick={() => setBlock("")} testid="bericht-block-alle">ganzer Zeitraum</Chip>
                  {bericht.bloecke.map((b) => <Chip key={b.von} aktiv={block === `${b.von}|${b.bis}`} onClick={() => setBlock(`${b.von}|${b.bis}`)} testid={`bericht-blockwahl-${b.von}`}>{periodeText(b.von, b.bis).slice(0, 13)}</Chip>)}
                </div>
              )}
            </div>
            <div style={{ height: 280 }}>
              <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 320, height: 200 }}>
                <ComposedChart data={reihe} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--wa-06)" vertical={false} />
                  <XAxis dataKey="tag" tick={{ ...ACHSE, fontSize: 11 }} />
                  <YAxis tick={{ ...ACHSE, fontSize: 11 }} tickFormatter={(v) => `${Math.round(v / 1000)}k`} domain={["auto", "auto"]} width={40} />
                  <Tooltip contentStyle={TOOLTIP_STIL} labelStyle={TOOLTIP_LABEL} formatter={(v, n) => [eur(v), n]} />
                  <Line type="monotone" dataKey="p25" name="untere Preiszone (P25)" stroke="#60a5fa" dot={false} strokeWidth={1} strokeDasharray="3 3" connectNulls={false} />
                  <Line type="monotone" dataKey="p75" name="P75" stroke="#60a5fa" dot={false} strokeWidth={1} strokeDasharray="3 3" connectNulls={false} />
                  <Line type="monotone" dataKey="min" name="Tagesminimum" stroke="#34d399" dot={false} strokeWidth={1.5} connectNulls={false} />
                  <Line type="monotone" dataKey="median" name="Tagesmedian (Low-Market, gewichtet)" stroke="#f87171" dot strokeWidth={2.2} connectNulls={false} />
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <div className="mt-1 text-[11px] text-zinc-500" data-testid="bericht-legende">{mitKorb
              ? "Rot Tagesmedian — an Tagen mit Teilabdeckung (ein Segment ohne gültigen Lauf) über die an beiden Vergleichstagen vorhandenen Segmente verkettet, kein Scheineinbruch; nicht geplante Segmente (Budget-Rotation) mit ihrem letzten geplanten Wert · Grün Tagesminimum · Blau gestrichelt P25/P75 (nur wenn jedes Segment des Tages gelaufen ist und genug Angebote hat). Tage ohne gültige Daten bleiben Lücken — nichts wird interpoliert."
              : "Rot Tagesmedian · Grün Tagesminimum · Blau gestrichelt P25/P75 (nur wenn jedes Segment des Tages genug Angebote hat). Tage ohne gültige Daten und Tage mit Teilabdeckung (ein Segment ohne gültigen Lauf) bleiben Lücken — nichts wird interpoliert."}</div>
            <div className="mt-3 flex flex-wrap items-center gap-1">
              {ZWEITE.map(([key, l]) => <Chip key={key} aktiv={zweite === key} onClick={() => setZweite(key)} testid={`bericht-zweite-${key}`}>{l}</Chip>)}
            </div>
            <div style={{ height: 140 }}>
              <ResponsiveContainer width="100%" height="100%" minWidth={0} initialDimension={{ width: 320, height: 140 }}>
                <BarChart data={reihe} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
                  <XAxis dataKey="tag" tick={{ ...ACHSE, fontSize: 10 }} />
                  <YAxis tick={{ ...ACHSE, fontSize: 10 }} width={40} allowDecimals={false} />
                  <Tooltip contentStyle={TOOLTIP_STIL} labelStyle={TOOLTIP_LABEL} />
                  <Bar dataKey={zweite} name={(ZWEITE.find(([x]) => x === zweite) || [])[1]} fill="#60a5fa" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card data-testid="bericht-bewegung">
            <div className="text-[13px] font-semibold text-white mb-2">Fallen / Steigen / Stabil (Tagesvergleiche im Zeitraum, stabil = ±{String(bericht.stabil_zone_pct ?? 0.5).replace(".", ",")} %)</div>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2 text-[12px]">
              <K label="fallend" wert={`${bw.fallend ?? 0} (${pct(bw.fallend_pct, false)})`} farbe={RICHTUNG.FALLING.farbe} />
              <K label="steigend" wert={`${bw.steigend ?? 0} (${pct(bw.steigend_pct, false)})`} farbe={RICHTUNG.RISING.farbe} />
              <K label="stabil" wert={`${bw.stabil ?? 0} (${pct(bw.stabil_pct, false)})`} unter={`${bw.vergleiche ?? 0} vergleichbare Tage`} />
              <K label="Abwärts / aufwärts / netto" wert={`${trendText(bw.summe_negativ_eur)} / ${trendText(bw.summe_positiv_eur)}`} unter={`netto ${trendText(bw.netto_eur)}`} />
              <K label="Stärkster Rückgang" wert={bw.staerkster_rueckgang_eur ? `${trendText(bw.staerkster_rueckgang_eur.eur)} am ${datumKurz(bw.staerkster_rueckgang_eur.date)}` : "—"}
                 unter={bw.staerkster_rueckgang_pct ? `in %: ${pct(bw.staerkster_rueckgang_pct.pct)} am ${datumKurz(bw.staerkster_rueckgang_pct.date)}` : ""} />
              <K label="Stärkster Anstieg" wert={bw.staerkster_anstieg_eur ? `${trendText(bw.staerkster_anstieg_eur.eur)} am ${datumKurz(bw.staerkster_anstieg_eur.date)}` : "—"}
                 unter={bw.staerkster_anstieg_pct ? `in %: ${pct(bw.staerkster_anstieg_pct.pct)} am ${datumKurz(bw.staerkster_anstieg_pct.date)}` : ""} />
              <K label="Längste Fall- / Steigeserie" wert={`${bw.laengste_fallserie ?? 0} / ${bw.laengste_steigeserie ?? 0} Tage`} />
              <K label="Volatilität" wert={bw.volatilitaet_pct == null ? "—" : `${String(bw.volatilitaet_pct).replace(".", ",")} %`} unter="Standardabweichung der Tagesänderungen" />
            </div>
          </Card>

          {(bericht.bloecke || []).length > 0 && (
            <Card padded={false} data-testid="bericht-bloecke">
              <div className="px-4 py-3 text-[13px] font-semibold text-white" style={{ borderBottom: "1px solid var(--wa-08)" }}>5-Tage-Blöcke im Vergleich</div>
              <div className="overflow-x-auto"><table className="w-full text-[12px] min-w-[760px]">
                <thead><tr className="text-left text-zinc-500 text-[11px] uppercase"><th className="px-3 py-2">Block</th><th className="px-3 py-2 text-right">Start</th><th className="px-3 py-2 text-right">Ende</th>
                  <th className="px-3 py-2 text-right">Δ</th><th className="px-3 py-2">Trend</th><th className="px-3 py-2 text-right">Senkungen / Erhöhungen</th><th className="px-3 py-2 text-right">Hot Deals</th><th className="px-3 py-2">Abdeckung</th></tr></thead>
                <tbody>{bericht.bloecke.map((b) => {
                  const rb = RICHTUNG[b.richtung] || RICHTUNG.UNKNOWN;
                  return (
                    <tr key={b.von} className="border-t border-white/5 tabular-nums" data-testid={`bericht-block-${b.von}`}>
                      <td className="px-3 py-1.5 text-zinc-300">{periodeText(b.von, b.bis)}</td>
                      <td className="px-3 py-1.5 text-right">{eur(b.startwert)}</td><td className="px-3 py-1.5 text-right">{eur(b.endwert)}</td>
                      <td className="px-3 py-1.5 text-right" style={{ color: rb.farbe }}>{trendText(b.delta_eur, b.delta_pct)}</td>
                      <td className="px-3 py-1.5">{b.offen ? <span className="text-zinc-500" data-testid={`bericht-block-offen-${b.von}`}>noch offen</span> : <Badge tone={rb.tone}>{rb.text}</Badge>}</td>
                      <td className="px-3 py-1.5 text-right">{b.preissenkungen ?? 0} / {b.preiserhoehungen ?? 0}</td>
                      <td className="px-3 py-1.5 text-right">{b.hot_deals ?? 0}{b.private_hot_deals ? ` (${b.private_hot_deals} privat)` : ""}</td>
                      <td className="px-3 py-1.5">{b.offen ? "—" : `${b.coverage_days}/${b.expected_days}`}</td>
                    </tr>
                  );
                })}</tbody>
              </table></div>
            </Card>
          )}

          <Card padded={false}>
            <div className="px-4 py-3 text-[13px] font-semibold text-white" style={{ borderBottom: "1px solid var(--wa-08)" }} data-testid="bericht-tagestabelle-titel">
              Tagestabelle ({tabelle.length} Tage{tage.length > tabelle.length ? ` · ${tage.length - tabelle.length} noch offen` : ""})</div>
            <div className="overflow-x-auto"><table className="w-full text-[12px] min-w-[900px]" data-testid="bericht-tagestabelle">
              <thead><tr className="text-left text-zinc-500 text-[11px] uppercase"><th className="px-3 py-2">Datum</th><th className="px-3 py-2 text-right">Median</th>
                <th className="px-3 py-2 text-right">Δ Vortag €</th><th className="px-3 py-2 text-right">Δ Vortag %</th><th className="px-3 py-2 text-right">Minimum</th>
                <th className="px-3 py-2 text-right">gültige Listings</th><th className="px-3 py-2 text-right">neue</th><th className="px-3 py-2 text-right">Senkungen</th>
                <th className="px-3 py-2 text-right">Erhöhungen</th><th className="px-3 py-2 text-right">Hot Deals</th><th className="px-3 py-2">Datenqualität</th></tr></thead>
              <tbody>{tabelle.map((t) => {
                const dq = t.data_quality ? DATENQUALITAET[t.data_quality] || DATENQUALITAET.UNKNOWN : null;
                // roher Tageswert ohne fehlende / heute noch laufende / nicht geplante Segmente: gedimmt (die Periodenwerte
                // nutzen den Korbwert)
                const gedimmt = t.teilabdeckung || t.ausstehende_segmente > 0 || t.nicht_geplante_segmente > 0 || t.vorab_segmente > 0;
                const plan = PLAN_STATUS[t.plan_status];
                const teilTitel = t.median_korb !== undefined
                  ? `Tageswert ohne diese Segmente — für Median, Minimum und Maximum der Periode zählt der verkettete Korbwert ${eur(t.median_korb)}`
                  : "Tageswert ohne diese Segmente — zählt nicht für Median, Minimum und Maximum der Periode";
                return (
                  <tr key={t.date} className="border-t border-white/5 tabular-nums" data-testid={`bericht-tag-${t.date}`} style={t.gueltig ? undefined : { color: "var(--text-dim)" }}>
                    <td className="px-3 py-1 text-zinc-300">{t.date}
                      {t.andere_fassung && <span className="ml-1 text-[10px] text-zinc-500">andere Fassung</span>}
                      {!t.andere_fassung && t.nur_ungueltig && <span className="ml-1 text-[10px]" style={{ color: "var(--st-rot)" }}>nur ungültige Läufe</span>}
                      {!t.andere_fassung && t.leer && <span className="ml-1 text-[10px] text-zinc-500">leer (Marktlücke)</span>}
                      {!t.andere_fassung && t.teilabdeckung && <span className="ml-1 text-[10px]" style={{ color: "var(--st-amber)" }} data-testid={`bericht-teil-${t.date}`}
                                                                      title={teilTitel}>Teilabdeckung ({t.fehlende_segmente} Segm. ohne gültigen Lauf)</span>}
                      {!t.andere_fassung && t.ausstehende_segmente > 0 && <span className="ml-1 text-[10px] text-zinc-500" data-testid={`bericht-ausstehend-${t.date}`}
                                                                                 title="Die Läufe verteilen sich über den Tag — ausstehende Segmente sind keine Lücke">läuft noch ({t.ausstehende_segmente} Segm. ausstehend)</span>}
                      {!t.andere_fassung && t.nicht_geplante_segmente > 0 && <span className="ml-1 text-[10px] text-zinc-500" data-testid={`bericht-nichtgeplant-${t.date}`}
                                                                                    title={`Budget-Rotation: an diesem Tag kein Abruf geplant — keine Lücke; im Korbwert${t.median_korb != null ? ` ${eur(t.median_korb)}` : ""} mit dem letzten geplanten Wert`}>
                        {t.nicht_geplante_segmente} Segm. nicht geplant{t.budget_segmente > 0 ? ` (${t.budget_segmente} wegen Budget)` : ""}</span>}
                      {!t.andere_fassung && t.abgelaufene_segmente > 0 && <span className="ml-1 text-[10px] text-zinc-500" data-testid={`bericht-abgelaufen-${t.date}`}
                                                                                 title="Letzter geplanter Lauf älter als 14 Tage (Intervall über 14 Tage) — an diesem Tag aus dem Korb genommen, keine technische Lücke">
                        {t.abgelaufene_segmente} Segm. ohne tragbaren Wert</span>}
                      {!t.andere_fassung && t.vorab_segmente > 0 && <span className="ml-1 text-[10px]" style={{ color: "var(--st-amber)" }} data-testid={`bericht-vorab-${t.date}`}
                                                                          title="Serienstart: schon angelegt, aber noch nicht beobachtet — der Tag ist kein Anker, sein Wert ist vom Folgetag rückwärts verkettet">
                        {t.vorab_segmente} Segm. noch nicht beobachtet</span>}
                      {!t.andere_fassung && plan && <span className="ml-1 text-[10px]" style={{ color: plan.farbe }} data-testid={`bericht-plan-${t.date}`}
                                                          title={plan.titel}>{plan.text}</span>}
                      {!t.andere_fassung && !t.gueltig && !t.nur_ungueltig && <span className="ml-1 text-[10px] text-zinc-500" data-testid={`bericht-luecke-${t.date}`}>keine Daten</span>}</td>
                    <td className={`px-3 py-1 text-right${gedimmt ? "" : " text-white"}`} style={gedimmt ? { color: "var(--text-dim)" } : undefined}>{eur(t.median)}</td>
                    <td className="px-3 py-1 text-right" style={{ color: richtungFarbe(t.richtung) }}>{t.delta_vortag_eur == null ? "—" : trendText(t.delta_vortag_eur)}</td>
                    <td className="px-3 py-1 text-right" style={{ color: richtungFarbe(t.richtung) }}>{pct(t.delta_vortag_pct)}</td>
                    <td className="px-3 py-1 text-right">{eur(t.min)}</td>
                    <td className="px-3 py-1 text-right">{t.gueltig ? t.listings : "—"}</td>
                    <td className="px-3 py-1 text-right">{t.neue ?? 0}</td><td className="px-3 py-1 text-right">{t.preissenkungen ?? 0}</td>
                    <td className="px-3 py-1 text-right">{t.preiserhoehungen ?? 0}</td><td className="px-3 py-1 text-right">{t.hot_deals ?? 0}</td>
                    <td className="px-3 py-1" style={{ color: dq?.farbe }}>{dq ? dq.zaehler : "—"}</td>
                  </tr>
                );
              })}</tbody>
            </table></div>
          </Card>

          <Card padded={false} data-testid="bericht-segmente">
            <div className="px-4 py-3 text-[13px] font-semibold text-white" style={{ borderBottom: "1px solid var(--wa-08)" }}>Segmentdetail (jede EZ × jeder km-Bereich einzeln)</div>
            <div className="overflow-x-auto"><table className="w-full text-[12px] min-w-[900px]">
              <thead><tr className="text-left text-zinc-500 text-[11px] uppercase"><th className="px-3 py-2">Segment</th><th className="px-3 py-2 text-right">aktueller Median</th>
                <th className="px-3 py-2 text-right">Änderung</th><th className="px-3 py-2">gültige Tage</th><th className="px-3 py-2 text-right">Listings</th>
                <th className="px-3 py-2 text-right">Hot Deals</th><th className="px-3 py-2">Markttiefe</th><th className="px-3 py-2">Datenqualität</th><th className="px-3 py-2">Health</th><th className="px-3 py-2 text-right">Kosten</th></tr></thead>
              <tbody>{(bericht.segmente || []).map((s) => {
                const tiefe = MARKTTIEFE[s.market_depth] || MARKTTIEFE.UNKNOWN;
                const dq = DATENQUALITAET[s.data_quality] || DATENQUALITAET.UNKNOWN;
                return (
                  <tr key={s.segment_id} className="border-t border-white/5 tabular-nums" data-testid={`bericht-segment-${s.segment_id}`}>
                    <td className="px-3 py-1.5"><Link to={`/admin/markt/${modellId}?segment=${encodeURIComponent(s.segment_id)}`} className="text-white hover:underline">{s.ez_label || "alle EZ"} · {s.km_label}</Link></td>
                    <td className="px-3 py-1.5 text-right">{eur(s.aktueller_median)}</td>
                    <td className="px-3 py-1.5 text-right" style={{ color: richtungFarbe(richtungAusPct(s.delta_pct, zone)) }} data-testid={`bericht-segment-delta-${s.segment_id}`}>{trendText(s.delta_eur, s.delta_pct)}</td>
                    <td className="px-3 py-1.5">{s.gueltige_tage}/{s.erwartete_tage}</td>
                    <td className="px-3 py-1.5 text-right">{s.listings}</td><td className="px-3 py-1.5 text-right">{s.hot_deals}</td>
                    <td className="px-3 py-1.5" style={{ color: tiefe.farbe }}>{tiefe.zaehler}</td>
                    <td className="px-3 py-1.5" style={{ color: dq.farbe }}>{dq.zaehler}</td>
                    <td className="px-3 py-1.5 text-zinc-500">{s.health || "—"}</td>
                    <td className="px-3 py-1.5 text-right">{Number(s.kosten_usd || 0).toFixed(2)} $</td>
                  </tr>
                );
              })}</tbody>
            </table></div>
          </Card>

          <Card data-testid="bericht-hotdeals">
            <div className="flex items-center justify-between gap-2 mb-1">
              <div className="text-[13px] font-semibold text-white inline-flex items-center gap-1.5"><Flame size={14} /> Hot Deals im Zeitraum</div>
              {superAdmin && <Link to={`/admin/markt/hot-deals`} className="text-[11px] underline text-zinc-300" data-testid="bericht-hotdeals-link">alle Hot Deals</Link>}
            </div>
            {(bericht.hot_deals_top || []).length === 0 ? <div className="text-[12px] text-zinc-500">keine Hot Deals in diesem Zeitraum</div> : (
              <ul className="text-[12px] space-y-0.5">
                {bericht.hot_deals_top.map((h) => (
                  <li key={`${h.segment_id}:${h.listing_id}`} className="text-zinc-300" data-testid={`bericht-hotdeal-${h.listing_id}`}>
                    <Badge tone={(HOTDEAL_KLASSE[h.klasse] || {}).tone || "gray"}>{(HOTDEAL_KLASSE[h.klasse] || {}).text || h.klasse || "—"}</Badge>{" "}
                    {eur(h.price)} statt {eur(h.reference_price)} ({pct(h.diff_pct, false)} günstiger) · {datumKurz(h.tag)}{h.privat ? " · privat" : ""}{h.rank ? ` · Platz ${h.rank}` : ""}
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-1 text-[10px] text-zinc-500">Hot Deal heißt nur: auffällig günstig gegenüber unserer Vergleichsgruppe — kein Urteil über Zustand oder Kauf.</div>
          </Card>

          {(bericht.fruehere_fassungen || []).length > 0 && (
            <Card data-testid="bericht-fassungen">
              <div className="text-[13px] font-semibold text-white mb-1">Frühere Fassungen im Zeitraum (getrennte Zeitreihe)</div>
              <ul className="text-[12px] text-zinc-300 space-y-0.5">
                {bericht.fruehere_fassungen.map((f) => <li key={`${f.version}:${f.definition_hash}`}>Fassung v{f.version}: {periodeText(f.von, f.bis)} · {f.tage} gültige Tage · {eur(f.startwert)} → {eur(f.endwert)} ({trendText(f.delta_eur, f.delta_pct)})</li>)}
              </ul>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

function K({ label, wert, unter, farbe, testid }) {
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--wa-06)" }} data-testid={testid}>
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className="text-[13px] font-semibold text-white" style={farbe ? { color: farbe } : undefined}>{wert}</div>
      {unter ? <div className="text-[10px] text-zinc-500 mt-0.5">{unter}</div> : null}
    </div>
  );
}

function Chip({ aktiv, onClick, children, testid }) {
  return (
    <button type="button" onClick={onClick} data-testid={testid} aria-pressed={!!aktiv}
            className="rounded-full px-3 py-1 text-[11px] border transition-colors"
            style={{ borderColor: aktiv ? "var(--accent-red)" : "var(--wa-12)", background: aktiv ? "rgba(255,59,48,.15)" : "transparent", color: aktiv ? "var(--text-primary)" : "var(--text-secondary)" }}>
      {children}
    </button>
  );
}
