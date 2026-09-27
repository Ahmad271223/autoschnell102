import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, ExternalLink, BarChart3, RefreshCw, Flame, History, Play } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState } from "./_ui";
import {
  HOTDEAL_EREIGNIS, HOTDEAL_GRUND, HOTDEAL_KLASSE, HOTDEAL_SORTIERUNGEN, HOTDEAL_STATUS, LIQUIDITAET,
  datumKurz, datumZeit, eur, mobileLink, pct, trendFarbe, trendText,
} from "@/lib/markt";

/**
 * Admin → Marktanalyse → Hot Deals (Master-Auftrag Ahmad 26.09.2026, Phase D, Abschnitte 22–26/58):
 * auffällig günstige Inserate gegenüber dem historischen Low-Market-Median DESSELBEN Segments
 * (gleiches Modell, Getriebe, Kraftstoff, EZ-Jahr und km-Bereich). Nur aus gespeicherten Tageswerten —
 * keine Zusatzabrufe, keine Zusatzkosten. Nur der Super-Admin sieht diese Seite; nichts davon landet in den
 * Chancen der Firmen oder in der Fahrzeugkarte. Links nur auf mobile.de.
 */
const FILTER_LEER = { status: "aktuell", klasse: "", privat: "", model_id: "", make: "", ez: "", km_min: "", km_max: "", heute_neu: false, sort: "vorteil_pct" };
const ZAHL_FELDER = ["ez", "km_min", "km_max"];

/**
 * Prüfbefund B15/B22: km so lesen, wie die Tabelle sie zeigt („150.000“ = 150000 — Tausenderpunkte und
 * Leerzeichen fallen weg), EZ als vierstelliges Jahr. Alles andere (Komma, Text, Minus) ist ungültig und
 * wird NIE gesendet — vorher wurde „150.000“ still zu 150 km und die Liste war ohne Hinweis leer.
 */
export function zahlFeld(k, wert) {
  const v = String(wert ?? "").trim();
  if (v === "") return { leer: true };
  const s = k === "ez" ? v.replace(/\s/g, "") : v.replace(/[.\s]/g, "");
  const ok = k === "ez" ? /^\d{4}$/.test(s) : /^\d+$/.test(s);
  return ok ? { zahl: Number(s) } : { ungueltig: true };
}

export function ungueltigeFelder(f) {
  return ZAHL_FELDER.filter((k) => zahlFeld(k, f[k]).ungueltig);
}

export function hotDealParams(f) {
  const p = { sort: f.sort || "vorteil_pct", status: f.status || "aktuell", limit: 300 };
  for (const k of ["klasse", "model_id", "make"]) {
    const v = String(f[k] ?? "").trim();
    if (v !== "") p[k] = v;
  }
  for (const k of ZAHL_FELDER) {
    const z = zahlFeld(k, f[k]);
    if (z.zahl !== undefined) p[k] = z.zahl;
  }
  if (f.privat === "privat") p.privat = true;
  if (f.privat === "haendler") p.privat = false;
  if (f.heute_neu) p.heute_neu = true;
  return p;
}

/**
 * Prüfbefund B19: ein verlassener Deal trägt den Stand beim Verlassen — liegt der Preis ÜBER der Referenz, ist
 * der Vorteil negativ. Dann nicht grün und nicht „−400 € (−2 %)“ als Vorteil, sondern „400 € (2 %) über Referenz“.
 */
export function vorteilText(diffEur, diffPct) {
  if (diffEur == null) return "—";
  if (Number(diffEur) < 0) return `${eur(Math.abs(diffEur))} (${pct(Math.abs(diffPct), false)}) über Referenz`;
  return `${eur(diffEur)} (${pct(diffPct, false)})`;
}

export function abstandText(diffPct, referenz) {
  const p = Number(diffPct);
  return `${pct(Math.abs(p), false)} ${p < 0 ? "über" : "unter"} ${eur(referenz)}`;
}

function schwellenText(s) {
  if (!s) return "";
  const klassen = (s.klassen || []).slice().reverse().map((k) => `${(HOTDEAL_KLASSE[k.klasse] || {}).text || k.klasse} ab ${pct(k.ab_pct, false)}`).join(", ");
  const staffel = (s.mindest_eur_staffel || []).slice().reverse().map((x) => eur(x.mindest_eur)).join(" / ");
  return `Referenz: Median der Tages-Low-Market-Mediane der letzten ${s.referenz_fenster_tage} Tage im selben Segment · mindestens ${s.min_basis_tage} gültige Tage und ${s.min_basis_inserate} verschiedene Inserate · ${klassen} · Mindestvorteil ${staffel} (nach Preisklasse)`;
}

export default function MarktHotDeals() {
  const [filter, setFilter] = useState(FILTER_LEER);
  const [daten, setDaten] = useState(null);
  const [modelle, setModelle] = useState([]);
  const [fehler, setFehler] = useState("");
  const [laedt, setLaedt] = useState(false);
  const [busy, setBusy] = useState(false);
  // Prüfbefund B23: nur die Antwort des NEUESTEN Aufrufs zählt — eine spät eintreffende Antwort eines älteren
  // Filters (zwei Server hinter dem Lastverteiler) überschreibt die Liste nicht mehr
  const laufNr = useRef(0);

  const laden = useCallback(async (f) => {
    const nr = ++laufNr.current;
    if (ungueltigeFelder(f).length) { setLaedt(false); return; }   // ungültige Zahl: nichts senden, Hinweis am Feld
    setLaedt(true);
    try {
      const r = await api.get("/admin/market/hot-deals", { params: hotDealParams(f), timeout: 15000 });
      if (nr !== laufNr.current) return;
      setDaten(r.data);
      setFehler("");
    } catch (e) { if (nr === laufNr.current) setFehler(errMsg(e, "Hot Deals konnten nicht geladen werden")); }
    finally { if (nr === laufNr.current) setLaedt(false); }
  }, []);
  useEffect(() => { laden(FILTER_LEER); }, [laden]);
  useEffect(() => { api.get("/admin/market/models").then((r) => setModelle(r.data?.modelle || [])).catch(() => {}); }, []);

  const sofort = ["status", "klasse", "privat", "model_id", "sort", "heute_neu"];
  const setzen = (k, v) => { const f = { ...filter, [k]: v }; setFilter(f); if (sofort.includes(k)) laden(f); };
  const zuruecksetzen = () => { setFilter(FILTER_LEER); laden(FILTER_LEER); };
  const auswerten = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/market/hot-deals/auswerten");
      const h = r.data?.hot_deals || {};
      toast.success(`${h.ausgewertet ?? 0} Tageswert(e) ausgewertet · ${h.ereignisse ?? 0} Ereignis(se)`);
      await laden(filter);
    } catch (e) { toast.error(errMsg(e, "Auswertung fehlgeschlagen")); }
    finally { setBusy(false); }
  };

  const feld = "rounded-lg px-2.5 py-1.5 text-[12px] outline-none w-full";
  const st = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };
  const ungueltig = ungueltigeFelder(filter);
  const zahlSt = (k) => (ungueltig.includes(k) ? { ...st, border: "1px solid var(--st-rot)" } : st);
  const z = daten?.zusammenfassung || {};
  const deals = daten?.deals || [];
  return (
    <div data-testid="hot-deals-seite">
      <Link to="/admin/markt" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Marktanalyse</Link>
      <PageHeader title="Hot Deals" subtitle="Auffällig günstige Inserate gegenüber dem historischen Low-Market-Median desselben Segments — aus gespeicherten Tageswerten, keine Zusatzabrufe. Nur für den Betreiber."
                  action={<div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => laden(filter)} disabled={laedt}><RefreshCw size={14} /> Aktualisieren</Button>
                    <Button variant="outline" size="sm" onClick={auswerten} disabled={busy} data-testid="hd-auswerten" title="Neue Tageswerte jetzt auswerten (sonst alle 5 Minuten im Hintergrund) — liest nur Gespeichertes, kostet nichts"><Play size={14} /> Jetzt auswerten</Button>
                  </div>} />

      {fehler && <Card className="mb-4" data-testid="hot-deals-fehler"><div className="text-red-300 text-sm">{fehler}</div></Card>}

      <Card className="mb-4" data-testid="hot-deals-hinweis">
        <div className="text-[12px]" style={{ color: "var(--st-amber)" }}>{daten?.hinweis || "Hot Deal heißt nur: auffällig günstig gegenüber unserer Vergleichsgruppe — nicht automatisch unfallfrei, technisch gut, seriös oder ein guter Kauf."}</div>
        {daten?.schwellen && <div className="mt-1 text-[11px] text-zinc-500" data-testid="hot-deals-schwellen">{schwellenText(daten.schwellen)}</div>}
      </Card>

      <Card className="mb-4" data-testid="hot-deals-zusammenfassung">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 text-[12px]">
          <Kachel label={`Heute geprüfte Modelle${z.tag ? ` (${datumKurz(z.tag)})` : ""}`} wert={String(z.modelle_geprueft ?? "—")} />
          <Kachel label="davon mit gültiger Basis" wert={String(z.modelle_gueltig ?? "—")} />
          <Kachel label="Neue Deals heute" wert={String(z.neue_deals_heute ?? "—")} tone="text-emerald-300" />
          <Kachel label="Aktive Deals" wert={String(z.aktiv ?? "—")} />
          <Kachel label="Stark" wert={String(z.strong ?? "—")} />
          <Kachel label="Extrem" wert={String(z.extreme ?? "—")} tone="text-emerald-300" />
          <Kachel label="davon privat" wert={`${z.davon_privat ?? "—"}${z.neue_privat_heute ? ` (${z.neue_privat_heute} neu)` : ""}`} />
        </div>
      </Card>

      <Card className="mb-4" data-testid="hot-deals-filter">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2 text-[11px] text-zinc-400">
          <label>Klasse
            <select className={feld} style={st} value={filter.klasse} onChange={(e) => setzen("klasse", e.target.value)} data-testid="hd-filter-klasse">
              <option value="">alle</option>
              {Object.entries(HOTDEAL_KLASSE).map(([k, v]) => <option key={k} value={k}>{v.text}</option>)}
            </select></label>
          <label>Verkäufer
            <select className={feld} style={st} value={filter.privat} onChange={(e) => setzen("privat", e.target.value)} data-testid="hd-filter-privat">
              <option value="">alle</option><option value="privat">nur privat</option><option value="haendler">nur Händler/unbekannt</option>
            </select></label>
          <label>Suchauftrag
            <select className={feld} style={st} value={filter.model_id} onChange={(e) => setzen("model_id", e.target.value)} data-testid="hd-filter-modell">
              <option value="">alle</option>
              {modelle.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select></label>
          <label>Marke<input className={feld} style={st} value={filter.make} onChange={(e) => setzen("make", e.target.value)} placeholder="z. B. BMW" data-testid="hd-filter-make" /></label>
          <label>EZ (Jahr)<input className={feld} style={zahlSt("ez")} value={filter.ez} onChange={(e) => setzen("ez", e.target.value)} inputMode="numeric" placeholder="2020" aria-invalid={ungueltig.includes("ez")} data-testid="hd-filter-ez" /></label>
          <label>km von<input className={feld} style={zahlSt("km_min")} value={filter.km_min} onChange={(e) => setzen("km_min", e.target.value)} inputMode="numeric" placeholder="z. B. 50.000" aria-invalid={ungueltig.includes("km_min")} data-testid="hd-filter-km-min" /></label>
          <label>km bis<input className={feld} style={zahlSt("km_max")} value={filter.km_max} onChange={(e) => setzen("km_max", e.target.value)} inputMode="numeric" placeholder="z. B. 150.000" aria-invalid={ungueltig.includes("km_max")} data-testid="hd-filter-km-max" /></label>
          <label>Sortierung
            <select className={feld} style={st} value={filter.sort} onChange={(e) => setzen("sort", e.target.value)} data-testid="hd-filter-sort">
              {HOTDEAL_SORTIERUNGEN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></label>
        </div>
        {ungueltig.length > 0 && (
          <div className="mt-2 text-[11px]" style={{ color: "var(--st-rot)" }} role="alert" data-testid="hd-filter-ungueltig">
            Bitte nur ganze Zahlen eingeben — km z. B. 150000 oder 150.000, EZ als Jahr z. B. 2020. Mit dieser Eingabe wird nicht geladen.
          </div>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => laden(filter)} disabled={laedt || ungueltig.length > 0} data-testid="hd-filter-anwenden">Filter anwenden</Button>
          <Button size="sm" variant="ghost" onClick={zuruecksetzen} disabled={laedt}>Zurücksetzen</Button>
          <label className="inline-flex items-center gap-1.5 text-[11px] text-zinc-400"><input type="checkbox" checked={filter.heute_neu} onChange={(e) => setzen("heute_neu", e.target.checked)} data-testid="hd-filter-heute-neu" /> nur heute neu</label>
          <span className="ml-auto inline-flex rounded-full overflow-hidden text-[11px]" style={{ border: "1px solid var(--wa-12)" }} data-testid="hd-umschalter">
            <button type="button" className="px-3 py-1" aria-pressed={filter.status === "aktuell"} data-testid="hd-umschalter-aktuell"
                    style={{ background: filter.status === "aktuell" ? "var(--wa-12)" : "transparent", color: "var(--text-primary)" }} onClick={() => setzen("status", "aktuell")}>aktuell</button>
            <button type="button" className="px-3 py-1" aria-pressed={filter.status === "alle"} data-testid="hd-umschalter-alle"
                    style={{ background: filter.status === "alle" ? "var(--wa-12)" : "transparent", color: "var(--text-primary)" }} onClick={() => setzen("status", "alle")}>alle (auch verlassene)</button>
          </span>
        </div>
      </Card>

      <Card padded={false} data-testid="hot-deals-liste">
        <div className="px-4 py-3 text-[13px] text-zinc-400" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <Flame size={14} className="inline mr-1" /> {deals.length} Hot Deal{deals.length === 1 ? "" : "s"}{daten?.gekuerzt ? " (gekürzt — Filter enger setzen)" : ""}{daten?.fenster_von ? ` · Historie seit ${datumKurz(daten.fenster_von)} (letztes Ereignis)` : ""}{laedt ? " · lädt…" : ""}
        </div>
        {!daten && !fehler ? <div className="flex items-center gap-2 text-zinc-500 text-sm p-4"><Spinner /> lade…</div>
          : deals.length === 0 ? <EmptyState title="Keine Hot Deals" hint="Zu diesen Filtern liegt gerade kein Inserat deutlich unter dem historischen Low-Market-Median seines Segments — oder die Basis ist noch zu dünn (mindestens 7 gültige Tage)." /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[1280px]">
                <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                  <th className="px-3 py-2">Segment</th><th className="px-3 py-2">Klasse</th><th className="px-3 py-2">Fahrzeug</th>
                  <th className="px-3 py-2 text-right">Preis</th><th className="px-3 py-2 text-right">Referenz</th><th className="px-3 py-2 text-right">Vorteil</th>
                  <th className="px-3 py-2">Verkäufer</th><th className="px-3 py-2">EZ</th><th className="px-3 py-2 text-right">km</th><th className="px-3 py-2">Ort</th>
                  <th className="px-3 py-2">Liquidität</th><th className="px-3 py-2">Hot seit</th><th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">Aktion</th>
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
  const [offen, setOffen] = useState(false);
  const [verlauf, setVerlauf] = useState(null);
  const link = mobileLink(d.url);
  const kl = HOTDEAL_KLASSE[d.klasse] || { text: d.klasse || "—", tone: "gray" };
  const liq = LIQUIDITAET[d.liquiditaet] || LIQUIDITAET.UNKNOWN;
  const umschalten = async () => {
    const neu = !offen;
    setOffen(neu);
    if (neu && !verlauf) {
      try {
        const r = await api.get("/admin/market/hot-deals/ereignisse", { params: { segment_id: d.segment_id, listing_id: d.listing_id } });
        setVerlauf(r.data?.ereignisse || []);
      } catch (e) { setVerlauf([]); toast.error(errMsg(e, "Verlauf konnte nicht geladen werden")); }
    }
  };
  return (
    <Fragment>
      <tr className="border-t border-white/5 tabular-nums" data-testid={`hd-deal-${d.listing_id}`}>
        <td className="px-3 py-1.5"><div className="text-white">{d.segment_label || d.model_id}</div><div className="text-[10px] text-zinc-500">{[d.ez_label, d.km_label].filter(Boolean).join(" · ")}</div></td>
        <td className="px-3 py-1.5"><Badge tone={d.status === "ACTIVE" ? kl.tone : "gray"}>{kl.text}</Badge></td>
        <td className="px-3 py-1.5"><div className="text-white">{d.title || [d.make, d.model, d.variant].filter(Boolean).join(" ") || d.listing_id}</div>
          <div className="text-[10px] text-zinc-500">{d.power_kw ? `${d.power_kw} kW · ` : ""}{d.fuel || ""}{d.gearbox ? ` · ${d.gearbox}` : ""}{d.rank ? ` · Platz ${d.rank} im Sample` : ""}</div></td>
        <td className="px-3 py-1.5 text-right text-white">{eur(d.current_price)}
          {d.price_change_since_detection_eur ? <div className="text-[10px]" style={{ color: trendFarbe(d.price_change_since_detection_eur) }}>{trendText(d.price_change_since_detection_eur)} seit Erkennung</div> : null}</td>
        <td className="px-3 py-1.5 text-right">{eur(d.reference_price)}<div className="text-[10px] text-zinc-500">{d.basis_tage ? `${d.basis_tage} Tage Basis` : ""}</div></td>
        <td className="px-3 py-1.5 text-right" style={{ color: Number(d.diff_eur) > 0 ? "var(--st-gruen)" : "var(--text-secondary)" }} data-testid={`hd-vorteil-${d.listing_id}`}>{vorteilText(d.diff_eur, d.diff_pct)}</td>
        <td className="px-3 py-1.5">{d.privat ? <Badge tone="purple">privat</Badge> : <span className="text-zinc-400">{d.seller_type === "DEALER" ? "Händler" : "unbekannt"}</span>}</td>
        <td className="px-3 py-1.5">{d.first_registration || d.ez_year || "—"}</td>
        <td className="px-3 py-1.5 text-right">{d.mileage_km != null ? Number(d.mileage_km).toLocaleString("de-DE") : "—"}</td>
        <td className="px-3 py-1.5">{[d.postal_code, d.city].filter(Boolean).join(" ") || "—"}</td>
        <td className="px-3 py-1.5" style={{ color: liq.farbe }}>{liq.text}</td>
        <td className="px-3 py-1.5">{datumKurz(d.hot_seit_at)}<div className="text-[10px] text-zinc-500">erstmals {datumKurz(d.deal_first_detected_at)}</div></td>
        <td className="px-3 py-1.5">
          <div className="flex flex-wrap gap-1 items-center">
            <Badge tone={d.status === "ACTIVE" ? "green" : d.status === "REMOVED" ? "red" : "gray"}>{HOTDEAL_STATUS[d.status] || d.status}</Badge>
            {d.heute_neu && <Badge tone="green">heute neu</Badge>}
            {d.status !== "ACTIVE" && d.left_grund && <span className="text-[10px] text-zinc-500">{HOTDEAL_GRUND[d.left_grund] || d.left_grund}</span>}
            {d.stand_alter_tage > 2 && <span className="text-[10px]" style={{ color: "var(--st-amber)" }} data-testid={`hd-alt-${d.listing_id}`}>Stand {datumKurz(d.stand_tag)}</span>}
          </div>
        </td>
        <td className="px-3 py-1.5 text-right whitespace-nowrap">
          {link ? <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-[11px] underline text-zinc-300 mr-2" data-testid={`hd-inserat-${d.listing_id}`}><ExternalLink size={12} /> Inserat öffnen</a>
                : <span className="text-[11px] text-zinc-600 mr-2" data-testid={`hd-kein-link-${d.listing_id}`}>kein Link</span>}
          <Link to={`/admin/markt/${d.model_id}?segment=${encodeURIComponent(d.segment_id)}`} className="inline-flex items-center gap-1 text-[11px] underline text-zinc-300 mr-2" data-testid={`hd-segment-${d.listing_id}`}><BarChart3 size={12} /> Segment</Link>
          <button type="button" className="inline-flex items-center gap-1 text-[11px] underline text-zinc-300" onClick={umschalten} data-testid={`hd-verlauf-${d.listing_id}`}><History size={12} /> Verlauf</button>
        </td>
      </tr>
      {offen && (
        <tr data-testid={`hd-ereignisse-${d.listing_id}`}>
          <td colSpan={14} className="px-3 pb-3">
            {!verlauf ? <div className="text-[11px] text-zinc-500"><Spinner size={12} /> lade Verlauf…</div> : verlauf.length === 0 ? <div className="text-[11px] text-zinc-500">keine Ereignisse</div> : (
              <ol className="text-[11px] space-y-0.5">
                {verlauf.map((e) => (
                  <li key={`${e.typ}:${e.lauf_key}`} className="text-zinc-300">
                    <span className="text-zinc-500">{datumZeit(e.lauf_at)}</span> · <b>{HOTDEAL_EREIGNIS[e.typ] || e.typ}</b>
                    {e.klasse ? ` · ${(HOTDEAL_KLASSE[e.klasse] || {}).text || e.klasse}` : ""}
                    {e.price != null ? ` · ${eur(e.price)}` : ""}{e.diff_pct != null ? ` (${abstandText(e.diff_pct, e.reference_price)})` : ""}
                    {e.grund ? ` · ${HOTDEAL_GRUND[e.grund] || e.grund}` : ""}
                  </li>
                ))}
              </ol>
            )}
          </td>
        </tr>
      )}
    </Fragment>
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
