import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, FileBarChart, RefreshCw, Lock } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState } from "./_ui";
import {
  BERICHT_SORTIERUNGEN, BERICHT_TYP, CONFIDENCE, DATENQUALITAET, LIQUIDITAET, RICHTUNG, berichtSortieren, datumZeit, pct, periodeText, trendText,
} from "@/lib/markt";

/**
 * Admin → Marktanalyse → Berichte (Master-Auftrag Ahmad 26.09.2026, Phase E, Abschnitte 13–21, 35–37):
 * eingefrorene Berichte je Periode (5 Tage: 01–05 … 26–Monatsende, 15 Tage: 01–15 / 16–Monatsende, Monat)
 * und die Übersicht ALLER Marktmodelle einer Periode. Ein finaler Bericht ändert sich nie mehr; die laufende
 * Periode gibt es nur vorläufig im Modellbericht. Alles aus gespeicherten Tageswerten — keine Zusatzabrufe.
 */
export default function MarktBerichte() {
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;
  const [typ, setTyp] = useState("MONTHLY");
  const [perioden, setPerioden] = useState(null);
  const [wahl, setWahl] = useState("");
  const [daten, setDaten] = useState(null);
  const [sort, setSort] = useState("rueckgang");
  const [fehler, setFehler] = useState("");
  const [busy, setBusy] = useState(false);
  // Neu-Laden der Tabelle erzwingen: bleibt die neueste Periode nach „Aktualisieren“ / „Berichte jetzt erstellen“
  // dieselbe, ändert sich wahl nicht — die Zeilen müssen trotzdem neu kommen (neu eingefrorene Modelle)
  const [ladeZaehler, setLadeZaehler] = useState(0);

  const periodenLaden = useCallback(async (t) => {
    try {
      const r = await api.get("/admin/market/reports/periods", { params: { typ: t } });
      setPerioden(r.data);
      const erste = r.data?.final?.[0];
      setWahl(erste ? `${erste.von}|${erste.bis}` : "");
      setLadeZaehler((n) => n + 1);
      setFehler("");
    } catch (e) { setFehler(errMsg(e, "Berichtsperioden konnten nicht geladen werden")); }
  }, []);
  useEffect(() => { periodenLaden(typ); }, [typ, periodenLaden]);

  useEffect(() => {
    if (!wahl) { setDaten(null); return undefined; }
    let aktiv = true;
    const [von, bis] = wahl.split("|");
    api.get("/admin/market/reports", { params: { typ, von, bis } })
      .then((r) => { if (aktiv) { setDaten(r.data); setFehler(""); } })
      .catch((e) => { if (aktiv) setFehler(errMsg(e, "Übersicht konnte nicht geladen werden")); });
    return () => { aktiv = false; };
  }, [typ, wahl, ladeZaehler]);

  const erstellen = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/market/reports/finalize");
      const b = r.data?.berichte || {};
      toast.success(`${b.erstellt ?? 0} Bericht(e) eingefroren · ${b.perioden ?? 0} fällige Periode(n) geprüft${b.wartet ? ` · ${b.wartet} warten auf Hot-Deal-Auswertung` : ""}`);
      await periodenLaden(typ);
    } catch (e) { toast.error(errMsg(e, "Berichte konnten nicht erstellt werden")); }
    finally { setBusy(false); }
  };

  const zeilen = useMemo(() => berichtSortieren(daten?.zeilen || [], sort), [daten, sort]);
  const [von, bis] = wahl ? wahl.split("|") : ["", ""];
  const feld = "rounded-lg px-2.5 py-1.5 text-[12px] outline-none";
  const st = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };
  const stand = perioden?.stand || {};
  return (
    <div data-testid="berichte-seite">
      <Link to="/admin/markt" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Marktanalyse</Link>
      <PageHeader title="Berichte" subtitle="Eingefrorene 5-Tage-, 15-Tage- und Monatsberichte je Marktmodell — aus gespeicherten Tageswerten, keine Zusatzabrufe."
                  action={<div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => periodenLaden(typ)} data-testid="berichte-aktualisieren"><RefreshCw size={14} /> Aktualisieren</Button>
                    {superAdmin && <Button size="sm" onClick={erstellen} disabled={busy} data-testid="berichte-erstellen"
                                           title="Nur fällige Perioden (Periodenende + Karenz) werden eingefroren — laufende nie"><Lock size={14} /> Berichte jetzt erstellen</Button>}
                  </div>} />

      {fehler && <Card className="mb-4" data-testid="berichte-fehler"><div className="text-red-300 text-sm">{fehler}</div></Card>}

      <Card className="mb-4" data-testid="berichte-auswahl">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex rounded-full overflow-hidden text-[12px]" style={{ border: "1px solid var(--wa-12)" }}>
            {Object.entries(BERICHT_TYP).map(([k, l]) => (
              <button key={k} type="button" className="px-3 py-1" aria-pressed={typ === k} data-testid={`berichte-typ-${k}`}
                      style={{ background: typ === k ? "var(--wa-12)" : "transparent", color: "var(--text-primary)" }} onClick={() => { setWahl(""); setTyp(k); }}>{l}</button>
            ))}
          </span>
          <select className={feld} style={st} value={wahl} onChange={(e) => setWahl(e.target.value)} data-testid="berichte-periode">
            {(perioden?.final || []).length === 0 && <option value="">noch kein finaler Bericht</option>}
            {(perioden?.final || []).map((p) => <option key={`${p.von}|${p.bis}`} value={`${p.von}|${p.bis}`}>{periodeText(p.von, p.bis)} · {p.anzahl} Modelle</option>)}
          </select>
          <label className="text-[11px] text-zinc-400 inline-flex items-center gap-1.5">Sortierung
            <select className={feld} style={st} value={sort} onChange={(e) => setSort(e.target.value)} data-testid="berichte-sort">
              {BERICHT_SORTIERUNGEN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select></label>
        </div>
        <div className="mt-2 text-[11px] text-zinc-500" data-testid="berichte-stand">
          Final wird eine Periode {perioden?.karenz_stunden ?? 6} h nach Periodenende (deutsche Zeit); stabil = ±{String(perioden?.stabil_zone_pct ?? 0.5).replace(".", ",")} %.
          {stand.letzter_lauf_at ? ` Letzte Prüfung ${datumZeit(stand.letzter_lauf_at)}.` : ""}
        </div>
        {(perioden?.laufend || []).length > 0 && (
          <div className="mt-1 text-[11px] text-zinc-500" data-testid="berichte-laufend">
            Laufend (nur vorläufig im Modellbericht): {perioden.laufend.map((p) => `${periodeText(p.von, p.bis)} (final ab ${datumZeit(p.faellig_ab)})`).join(" · ")}
          </div>
        )}
      </Card>

      <Card padded={false} data-testid="berichte-uebersicht">
        <div className="px-4 py-3 text-[13px] text-zinc-400" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <FileBarChart size={14} className="inline mr-1" /> {BERICHT_TYP[typ]} {periodeText(von, bis)} · {zeilen.length} Modelle
        </div>
        {!perioden ? <div className="flex items-center gap-2 text-zinc-500 text-sm p-4"><Spinner /> lade…</div>
          : zeilen.length === 0 ? <EmptyState title="Keine Berichte" hint="Für diese Periode liegt noch kein eingefrorener Bericht vor. Laufende Perioden gibt es vorläufig je Modell." /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[1250px]">
                <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                  <th className="px-3 py-2">Modell</th><th className="px-3 py-2">Kraftstoff</th><th className="px-3 py-2">Getriebe</th><th className="px-3 py-2">Trend</th>
                  <th className="px-3 py-2 text-right">Δ €</th><th className="px-3 py-2 text-right">Δ %</th><th className="px-3 py-2 text-right">Listings</th>
                  <th className="px-3 py-2 text-right">Senkungen</th><th className="px-3 py-2 text-right">Erhöhungen</th><th className="px-3 py-2 text-right">Hot Deals</th>
                  <th className="px-3 py-2 text-right">privat</th><th className="px-3 py-2">Liquidität</th><th className="px-3 py-2">Datenqualität</th>
                  <th className="px-3 py-2">Health</th><th className="px-3 py-2 text-right">EMPTY</th><th className="px-3 py-2 text-right">Kosten</th><th className="px-3 py-2">Abdeckung</th>
                </tr></thead>
                <tbody>{zeilen.map((z) => {
                  const r = RICHTUNG[z.richtung] || RICHTUNG.UNKNOWN;
                  const dq = DATENQUALITAET[z.data_quality] || DATENQUALITAET.UNKNOWN;
                  const liq = LIQUIDITAET[z.liquiditaet] || LIQUIDITAET.UNKNOWN;
                  const conf = CONFIDENCE[z.confidence];
                  return (
                    <tr key={z.model_id} className="border-t border-white/5 tabular-nums" data-testid={`bericht-zeile-${z.model_id}`}>
                      <td className="px-3 py-1.5"><Link to={`/admin/markt/berichte/${z.model_id}?typ=${typ}&von=${von}&bis=${bis}`} className="text-white hover:underline" data-testid={`bericht-link-${z.model_id}`}>{z.label}</Link></td>
                      <td className="px-3 py-1.5">{z.fuel || "—"}</td><td className="px-3 py-1.5">{z.gearbox || "—"}</td>
                      <td className="px-3 py-1.5"><Badge tone={r.tone}>{r.text}</Badge></td>
                      <td className="px-3 py-1.5 text-right" style={{ color: r.farbe }}>{z.delta_eur == null ? "—" : trendText(z.delta_eur)}</td>
                      <td className="px-3 py-1.5 text-right" style={{ color: r.farbe }}>{pct(z.delta_pct)}</td>
                      <td className="px-3 py-1.5 text-right">{z.listings ?? "—"}</td>
                      <td className="px-3 py-1.5 text-right">{z.preissenkungen ?? 0}</td><td className="px-3 py-1.5 text-right">{z.preiserhoehungen ?? 0}</td>
                      <td className="px-3 py-1.5 text-right">{z.hot_deals ?? 0}</td><td className="px-3 py-1.5 text-right">{z.private_hot_deals ?? 0}</td>
                      <td className="px-3 py-1.5" style={{ color: liq.farbe }}>{liq.text}</td>
                      <td className="px-3 py-1.5" style={{ color: dq.farbe }}>{dq.zaehler}</td>
                      <td className="px-3 py-1.5 text-zinc-500" title="Segment Health folgt mit Phase F">{z.health || "—"}</td>
                      <td className="px-3 py-1.5 text-right">{z.empty_segmente ?? 0}</td>
                      <td className="px-3 py-1.5 text-right">{Number(z.kosten_usd || 0).toFixed(2)} $</td>
                      <td className="px-3 py-1.5">{z.coverage_days ?? 0}/{z.expected_days ?? 0} Tage{conf ? <span className="ml-1 text-[10px]" style={{ color: conf.farbe }}>· {conf.text}</span> : null}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </div>
          )}
        <div className="px-4 py-2 text-[11px] text-zinc-500">{daten?.hinweis || ""} Δ = gleicher Segmentkorb (je Segment erster und letzter gültiger Tag), gewichtet nach Stichprobe. Grün = Preis fällt, Rot = steigt. Kosten = Abrufkosten des Zeitraums (Berichte selbst kosten nichts).</div>
      </Card>
    </div>
  );
}

