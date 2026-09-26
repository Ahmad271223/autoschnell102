import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Copy, Pause, Play, Archive, Pencil, Plus, FlaskConical, RefreshCw, X } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { eur, datumZeit } from "@/lib/markt";

/**
 * Admin → Marktanalyse → Suchaufträge (Auftrag v3, 26.09.2026): der Super-Admin
 * legt Marktanalysen selbst an — Marke, Modell (mobile.de-Katalog), Variante,
 * Filter, EZ-Jahre, km-Bereiche, Zeilen je Segment, Abrufe je Tag. Vorher
 * Kostenprognose (deterministisch) und Testlauf mit wenigen Treffern; danach
 * Aktivieren. Bearbeiten, Pausieren, Duplizieren, Archivieren (nie löschen).
 */
const JAHRE = Array.from({ length: 16 }, (_, i) => new Date().getFullYear() + 1 - i);
const KRAFTSTOFF = { "": "alle", PETROL: "Benzin", DIESEL: "Diesel", HYBRID: "Hybrid (Benzin)", HYBRID_DIESEL: "Hybrid (Diesel)", ELECTRICITY: "Elektro", LPG: "LPG", CNG: "CNG" };
const GETRIEBE = { "": "alle (nicht empfohlen — mischt Schalter und Automatik)", AUTOMATIC_GEAR: "Automatik (auch DSG / S tronic)", MANUAL_GEAR: "Schaltgetriebe", SEMIAUTOMATIC_GEAR: "Halbautomatik" };
const VERKAEUFER = { "": "alle", DEALER: "Händler", FSBO: "Privat" };
const STATUS_TONE = { active: "green", paused: "yellow", archived: "gray" };
const STATUS_TEXT = { active: "aktiv", paused: "pausiert", archived: "archiviert" };

const LEER = { make: "", model: "", variant: "", fuel: "", gearbox: "", power_kw_min: "", power_kw_max: "", seller_type: "",
               country: "DE", zip: "", radius_km: "", ez_years: [], km_buckets: [], rows: 20, crawls_per_day: 2, label: "" };

export default function MarktAuftraege() {
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;
  const [daten, setDaten] = useState(null);
  const [katalog, setKatalog] = useState(null);
  const [fehler, setFehler] = useState("");
  const [formular, setFormular] = useState(null);       // {modus: "neu"|"bearbeiten"|"duplizieren", id?, werte}
  const [busy, setBusy] = useState("");
  const [archiv, setArchiv] = useState(false);

  const laden = useCallback(async () => {
    try {
      const [a, k] = await Promise.all([api.get("/admin/market/auftraege", { params: { archiv } }), katalog ? Promise.resolve({ data: katalog }) : api.get("/admin/market/katalog")]);
      setDaten(a.data);
      if (!katalog) setKatalog(k.data);
      setFehler("");
    } catch (e) { setFehler(errMsg(e, "Suchaufträge konnten nicht geladen werden")); }
  }, [archiv, katalog]);
  useEffect(() => { laden(); }, [laden]);

  const aktion = async (name, fn, text) => {
    setBusy(name);
    try { const r = await fn(); toast.success(typeof text === "function" ? text(r.data) : text); await laden(); }
    catch (e) { toast.error(errMsg(e, "Aktion fehlgeschlagen")); }
    finally { setBusy(""); }
  };
  const status = (m, st) => aktion(`status-${m.id}`, () => api.post(`/admin/market/models/${m.id}/status`, { status: st }),
    st === "active" ? "Aktiviert — Segmente werden ab dem nächsten Tagesplan gecrawlt" : st === "paused" ? "Pausiert" : "Archiviert (Historie bleibt)");
  const neu = () => setFormular({ modus: "neu", werte: { ...LEER, ez_years: katalog?.standard?.ez_years || [], km_buckets: katalog?.standard?.km_buckets || [],
                                                         rows: katalog?.standard?.rows || 20, crawls_per_day: katalog?.standard?.crawls_per_day || 2 } });
  const bearbeiten = (m) => setFormular({ modus: "bearbeiten", id: m.id, werte: ausModell(m) });
  const duplizieren = (m) => setFormular({ modus: "duplizieren", id: m.id, werte: { ...ausModell(m), label: "", variant: m.variant || "" } });

  if (fehler) return <Card data-testid="auftraege-fehler"><div className="text-red-300 text-sm">{fehler}</div><Button size="sm" className="mt-2" onClick={laden}><RefreshCw size={14} /> Erneut laden</Button></Card>;
  if (!daten || !katalog) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  const p = daten.prognose || {};
  return (
    <div>
      <Link to="/admin/markt" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Marktanalyse</Link>
      <PageHeader title="Suchaufträge" subtitle="Eigene Marktanalysen anlegen: Modell, EZ-Jahre, km-Bereiche, Zeilen, Abrufe je Tag — mit Kostenprognose und Testlauf."
                  action={<div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={laden}><RefreshCw size={14} /> Aktualisieren</Button>
                    <Button size="sm" onClick={neu} disabled={!superAdmin} data-testid="auftrag-neu"><Plus size={14} /> Neue Marktanalyse</Button>
                  </div>} />

      <Card className="mb-4" data-testid="auftraege-kosten">
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3 text-[12px]">
          <K label="Aktive Modelle" wert={String(p.aktive_modelle ?? 0)} />
          <K label="Aktive Segmente" wert={(p.segmente || 0).toLocaleString("de-DE")} />
          <K label="Erwartete Zeilen / Tag" wert={(p.rows_tag || 0).toLocaleString("de-DE")} />
          <K label="Erwartete Zeilen / Monat" wert={(p.rows_monat || 0).toLocaleString("de-DE")} />
          <K label="Monatsbudget" wert={`${Number(p.budget_usd || 0).toFixed(0)} $`} />
          <K label="Prognostizierte Nutzung" wert={`${Number(p.kosten_monat_usd || 0).toFixed(2)} $`} rot={p.ueberschritten} />
          <K label="Verbraucht / verbleibend" wert={`${Number(p.verbraucht_usd || 0).toFixed(2)} $ / ${Number(p.verbleibend_usd || 0).toFixed(2)} $`} />
        </div>
        {p.ueberschritten && <div className="mt-2 text-[12px] text-red-300" data-testid="auftraege-budget-warnung">Die aktive Konfiguration würde das Monatsbudget voraussichtlich überschreiten. Das harte Budgetlimit im Worker bleibt bestehen — Läufe stoppen, wenn es erreicht ist.</div>}
        <div className="mt-1 text-[11px] text-zinc-500">Rechnung: Segmente = EZ-Jahre × km-Bereiche · Zeilen/Tag = Segmente × Zeilen × Abrufe · Kosten = Läufe × {p.preise?.start_usd} $ + Zeilen × {p.preise?.row_usd} $ (Bündel zu {p.preise?.buendel}, {p.preise?.actor}). Keine KI.</div>
      </Card>

      {formular && <AuftragFormular katalog={katalog} formular={formular} superAdmin={superAdmin}
                                    onClose={() => setFormular(null)} onGespeichert={() => { setFormular(null); laden(); }} />}

      <Card padded={false} data-testid="auftraege-liste">
        <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <span className="text-[13px] text-zinc-400">{daten.auftraege.length} Marktanalysen</span>
          <label className="text-[12px] text-zinc-400 inline-flex items-center gap-1.5"><input type="checkbox" checked={archiv} onChange={(e) => setArchiv(e.target.checked)} data-testid="auftraege-archiv" /> archivierte zeigen</label>
        </div>
        {daten.auftraege.length === 0 ? <EmptyState title="Noch keine Marktanalysen" hint="„Neue Marktanalyse“ oder auf der Marktanalyse-Seite „Startliste & Segmente aufbauen“ (52 Startmodelle)." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] min-w-[1100px]">
              <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                <th className="px-3 py-2">Modell</th><th className="px-3 py-2">Variante / Filter</th><th className="px-3 py-2">EZ-Jahre</th><th className="px-3 py-2">km-Bereiche</th>
                <th className="px-3 py-2 text-right">Zeilen</th><th className="px-3 py-2 text-right">Abrufe/Tag</th><th className="px-3 py-2 text-right">Segmente</th>
                <th className="px-3 py-2">Letzter Crawl</th><th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">Monat</th><th className="px-3 py-2 text-right">Aktion</th>
              </tr></thead>
              <tbody>{daten.auftraege.map((m) => (
                <tr key={m.id} className="border-t border-white/5" data-testid={`auftrag-${m.id}`}>
                  <td className="px-3 py-2"><Link to={`/admin/markt/${m.id}`} className="text-white font-medium hover:underline">{m.label}</Link><div className="text-[11px] text-zinc-500">{m.make} · {m.model}{m.model_id ? "" : " · keine mobile.de-ID"}</div></td>
                  <td className="px-3 py-2 text-zinc-300">{m.variant}<div className="text-[11px] text-zinc-500">{[KRAFTSTOFF[m.fuel] !== "alle" && KRAFTSTOFF[m.fuel], m.gearbox && GETRIEBE[m.gearbox], m.power_kw_min || m.power_kw_max ? `${m.power_kw_min || "…"}–${m.power_kw_max || "…"} kW` : null, m.seller_type && VERKAEUFER[m.seller_type], m.zip ? `PLZ ${m.zip} +${m.radius_km} km` : null].filter(Boolean).join(" · ")}</div></td>
                  <td className="px-3 py-2 text-zinc-300 tabular-nums">{(m.ez_years || []).join(", ") || "Standard"}</td>
                  <td className="px-3 py-2 text-zinc-300 tabular-nums">{(m.km_buckets || []).map((b) => `${Math.round(b.min_km / 1000)}–${Math.round(b.max_km / 1000)}k`).join(", ") || "Standard"}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{m.rows || 20}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{m.crawls_per_day || 1}×</td>
                  <td className="px-3 py-2 text-right tabular-nums">{m.prognose?.segmente ?? m.segmente_aktiv}</td>
                  <td className="px-3 py-2 text-zinc-400">{m.last_success_at ? fmtDate(m.last_success_at) : "—"}</td>
                  <td className="px-3 py-2"><Badge tone={STATUS_TONE[m.status] || "gray"}>{STATUS_TEXT[m.status] || m.status}</Badge>{m.crawl_status === "fehler" && <Badge tone="red">Fehler</Badge>}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{Number(m.monatsverbrauch_usd || 0).toFixed(2)} $</td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <Button size="sm" variant="ghost" disabled={!superAdmin || !!busy} onClick={() => bearbeiten(m)} title="Bearbeiten" data-testid={`auftrag-bearbeiten-${m.id}`}><Pencil size={13} /></Button>
                    <Button size="sm" variant="ghost" disabled={!superAdmin || !!busy} onClick={() => duplizieren(m)} title="Duplizieren" data-testid={`auftrag-duplizieren-${m.id}`}><Copy size={13} /></Button>
                    {m.status === "active"
                      ? <Button size="sm" variant="ghost" disabled={!superAdmin || !!busy} onClick={() => status(m, "paused")} title="Pausieren" data-testid={`auftrag-pausieren-${m.id}`}><Pause size={13} /></Button>
                      : <Button size="sm" variant="ghost" disabled={!superAdmin || !!busy || !m.model_id} onClick={() => status(m, "active")} title="Aktivieren" data-testid={`auftrag-aktivieren-${m.id}`}><Play size={13} /></Button>}
                    {m.status !== "archived" && <Button size="sm" variant="ghost" disabled={!superAdmin || !!busy} title="Archivieren (Historie bleibt)" data-testid={`auftrag-archivieren-${m.id}`}
                                                         onClick={() => { if (window.confirm(`${m.label} archivieren? Es werden keine neuen Daten gesammelt, die Historie bleibt erhalten.`)) status(m, "archived"); }}><Archive size={13} /></Button>}
                  </td>
                </tr>))}</tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function ausModell(m) {
  return { make: m.make || "", model: m.model || "", variant: m.variant || "", fuel: m.fuel || "", gearbox: m.gearbox || "",
           power_kw_min: m.power_kw_min ?? "", power_kw_max: m.power_kw_max ?? "", seller_type: m.seller_type || "", country: m.country || "DE",
           zip: m.zip || "", radius_km: m.radius_km ?? "", ez_years: m.ez_years || [], km_buckets: (m.km_buckets || []).map((b) => ({ ...b })),
           rows: m.rows || 20, crawls_per_day: m.crawls_per_day || 1, label: m.label || "" };
}

function K({ label, wert, rot }) {
  return <div className="rounded-lg p-2.5" style={{ background: "var(--wa-06)" }}><div className="text-[11px] text-zinc-500">{label}</div><div className={`text-[13px] font-semibold ${rot ? "text-red-300" : "text-white"}`}>{wert}</div></div>;
}

const feld = "w-full rounded-lg px-2.5 py-1.5 text-[13px] outline-none";
const st = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };

function AuftragFormular({ katalog, formular, superAdmin, onClose, onGespeichert }) {
  const [w, setW] = useState(formular.werte);
  const [modelle, setModelle] = useState([]);
  const [prognose, setPrognose] = useState(null);
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState("");
  const [ezModus, setEzModus] = useState("liste");
  const [ezVon, setEzVon] = useState("");
  const [ezBis, setEzBis] = useState("");
  const timer = useRef(null);
  const set = (k, v) => setW((x) => ({ ...x, [k]: v }));

  useEffect(() => {
    if (!w.make) { setModelle([]); return; }
    api.get("/admin/market/katalog", { params: { marke: w.make } }).then((r) => setModelle(r.data.modelle || [])).catch(() => setModelle([]));
  }, [w.make]);

  const nutzlast = useMemo(() => ({ ...w, ez_years: ezModus === "liste" ? w.ez_years : [], ez_from: ezModus === "vonbis" ? ezVon : "", ez_to: ezModus === "vonbis" ? ezBis : "",
                                     power_kw_min: w.power_kw_min || null, power_kw_max: w.power_kw_max || null, radius_km: w.radius_km || null }), [w, ezModus, ezVon, ezBis]);
  // Prognose live (Kostenrechner), 400 ms nach der letzten Eingabe
  useEffect(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      api.post("/admin/market/prognose", { ...nutzlast, status: "active" }, { params: formular.modus === "bearbeiten" ? { ohne_id: formular.id } : {} })
        .then((r) => setPrognose(r.data)).catch(() => {});
    }, 400);
    return () => clearTimeout(timer.current);
  }, [nutzlast, formular.modus, formular.id]);

  const jahrUmschalten = (j) => set("ez_years", w.ez_years.includes(j) ? w.ez_years.filter((x) => x !== j) : [...w.ez_years, j].sort());
  const kmSetzen = (i, k, v) => set("km_buckets", w.km_buckets.map((b, idx) => (idx === i ? { ...b, [k]: v === "" ? "" : Number(String(v).replace(/[^0-9]/g, "")) } : b)));
  const kmHinzu = () => { const letzte = w.km_buckets[w.km_buckets.length - 1]; set("km_buckets", [...w.km_buckets, { min_km: letzte ? Number(letzte.max_km) + 1 : 0, max_km: letzte ? Number(letzte.max_km) + 30000 : 30000 }]); };
  const kmWeg = (i) => set("km_buckets", w.km_buckets.filter((_, idx) => idx !== i));

  const speichern = async (status) => {
    if (status === "active" && prognose?.ueberschritten && !window.confirm("Diese Konfiguration würde das aktuelle Monatsbudget voraussichtlich überschreiten. Trotzdem aktivieren?")) return;
    setBusy("speichern");
    try {
      const body = { ...nutzlast, status };
      if (formular.modus === "bearbeiten") await api.put(`/admin/market/models/${formular.id}`, body);
      else if (formular.modus === "duplizieren") await api.post(`/admin/market/models/${formular.id}/duplicate`, body);
      else await api.post("/admin/market/models", body);
      toast.success(status === "active" ? "Marktanalyse aktiviert — Segmente angelegt" : "Marktanalyse gespeichert (pausiert)");
      onGespeichert();
    } catch (e) { toast.error(errMsg(e, "Speichern fehlgeschlagen")); }
    finally { setBusy(""); }
  };
  const testlauf = async () => {
    setBusy("test");
    try { const r = await api.post("/admin/market/testlauf", nutzlast, { params: { n: 5 }, timeout: 120000 }); setTest(r.data); }
    catch (e) { toast.error(errMsg(e, "Testlauf fehlgeschlagen")); }
    finally { setBusy(""); }
  };
  const e = prognose?.entwurf;
  return (
    <Card className="mb-4" data-testid="auftrag-formular">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[15px] font-semibold text-white">{formular.modus === "neu" ? "Neue Marktanalyse" : formular.modus === "bearbeiten" ? "Marktanalyse bearbeiten" : "Marktanalyse duplizieren"}</div>
        <button type="button" onClick={onClose} className="text-zinc-400 hover:text-white" aria-label="Schließen"><X size={18} /></button>
      </div>
      <div className="grid md:grid-cols-3 gap-3 text-[12px] text-zinc-400">
        <label>Marke *
          <select className={feld} style={st} value={w.make} onChange={(ev) => { set("make", ev.target.value); set("model", ""); }} data-testid="auftrag-marke">
            <option value="">— wählen —</option>{(katalog.marken || []).map((m) => <option key={m.make_id} value={m.name}>{m.name}</option>)}
          </select></label>
        <label>Modell * <span className="text-zinc-600">(mobile.de-Katalog)</span>
          <select className={feld} style={st} value={w.model} onChange={(ev) => set("model", ev.target.value)} disabled={!w.make} data-testid="auftrag-modell">
            <option value="">— wählen —</option>{modelle.map((m) => <option key={m.model_id} value={m.name}>{m.name}</option>)}
          </select></label>
        <label>Variante / Motorisierung *
          <input className={feld} style={st} value={w.variant} onChange={(ev) => set("variant", ev.target.value)} placeholder="z. B. 320d" data-testid="auftrag-variante" /></label>
        <label>Kraftstoff<select className={feld} style={st} value={w.fuel} onChange={(ev) => set("fuel", ev.target.value)} data-testid="auftrag-kraftstoff">{Object.entries(KRAFTSTOFF).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label>Getriebe<select className={feld} style={st} value={w.gearbox} onChange={(ev) => set("gearbox", ev.target.value)} data-testid="auftrag-getriebe">{Object.entries(GETRIEBE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          {!w.gearbox && <div className="text-[11px] mt-1" style={{ color: "var(--st-amber, #f59e0b)" }}>Ohne Getriebe mischen sich Schalt- und Automatikpreise (1–3 T€ Unterschied). Für beide Getriebe zwei Aufträge anlegen (Duplizieren).</div>}</label>
        <label>Leistung kW von – bis<div className="flex gap-2"><input className={feld} style={st} inputMode="numeric" value={w.power_kw_min} onChange={(ev) => set("power_kw_min", ev.target.value)} placeholder="von" data-testid="auftrag-kw-von" /><input className={feld} style={st} inputMode="numeric" value={w.power_kw_max} onChange={(ev) => set("power_kw_max", ev.target.value)} placeholder="bis" /></div></label>
        <label>Verkäuferart<select className={feld} style={st} value={w.seller_type} onChange={(ev) => set("seller_type", ev.target.value)}>{Object.entries(VERKAEUFER).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <label>Land<input className={feld} style={st} value={w.country} onChange={(ev) => set("country", ev.target.value.toUpperCase().slice(0, 2))} /></label>
        <label>PLZ + Radius (km)<div className="flex gap-2"><input className={feld} style={st} value={w.zip} onChange={(ev) => set("zip", ev.target.value)} placeholder="PLZ" /><input className={feld} style={st} inputMode="numeric" value={w.radius_km} onChange={(ev) => set("radius_km", ev.target.value)} placeholder="Radius" /></div></label>
      </div>

      <div className="mt-3 text-[12px] text-zinc-400">Erstzulassung * <span className="text-zinc-600">(jedes Jahr wird ein eigenes Segment — ein 2019er landet nie in EZ 2020)</span></div>
      <div className="mt-1 flex flex-wrap items-center gap-1.5" data-testid="auftrag-ez">
        <Chip aktiv={ezModus === "liste"} onClick={() => setEzModus("liste")}>Jahre wählen</Chip>
        <Chip aktiv={ezModus === "vonbis"} onClick={() => setEzModus("vonbis")}>von – bis</Chip>
        {ezModus === "liste" ? JAHRE.map((j) => <Chip key={j} aktiv={w.ez_years.includes(j)} onClick={() => jahrUmschalten(j)} testid={`auftrag-ez-${j}`}>{j}</Chip>)
          : <><input className="w-24 rounded-lg px-2 py-1 text-[12px]" style={st} inputMode="numeric" value={ezVon} onChange={(ev) => setEzVon(ev.target.value)} placeholder="von" data-testid="auftrag-ez-von" /><input className="w-24 rounded-lg px-2 py-1 text-[12px]" style={st} inputMode="numeric" value={ezBis} onChange={(ev) => setEzBis(ev.target.value)} placeholder="bis" data-testid="auftrag-ez-bis" /></>}
      </div>

      <div className="mt-3 text-[12px] text-zinc-400">Kilometerbereiche * <span className="text-zinc-600">(von &lt; bis, keine Überschneidungen)</span></div>
      <div className="mt-1 space-y-1" data-testid="auftrag-km">
        {w.km_buckets.map((b, i) => (
          <div key={i} className="flex items-center gap-2 text-[12px]">
            <input className="w-28 rounded-lg px-2 py-1" style={st} inputMode="numeric" value={b.min_km} onChange={(ev) => kmSetzen(i, "min_km", ev.target.value)} data-testid={`auftrag-km-von-${i}`} />
            <span className="text-zinc-500">–</span>
            <input className="w-28 rounded-lg px-2 py-1" style={st} inputMode="numeric" value={b.max_km} onChange={(ev) => kmSetzen(i, "max_km", ev.target.value)} data-testid={`auftrag-km-bis-${i}`} />
            <span className="text-zinc-500">km</span>
            <button type="button" onClick={() => kmWeg(i)} className="text-zinc-500 hover:text-red-300" aria-label="Bereich entfernen" data-testid={`auftrag-km-weg-${i}`}><X size={14} /></button>
          </div>
        ))}
        <Button size="sm" variant="outline" onClick={kmHinzu} data-testid="auftrag-km-hinzu"><Plus size={13} /> Bereich hinzufügen</Button>
      </div>

      <div className="mt-3 grid md:grid-cols-3 gap-3 text-[12px] text-zinc-400">
        <label>Max. Ergebnisse je Segment (die N günstigsten)<input className={feld} style={st} inputMode="numeric" value={w.rows} onChange={(ev) => set("rows", Number(String(ev.target.value).replace(/[^0-9]/g, "")) || "")} data-testid="auftrag-rows" /></label>
        <label>Abrufe je Tag<select className={feld} style={st} value={w.crawls_per_day} onChange={(ev) => set("crawls_per_day", Number(ev.target.value))} data-testid="auftrag-frequenz">{[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}× täglich{n === 2 ? " (≈ 12 h Abstand)" : ""}</option>)}</select></label>
        <label>Anzeigename (optional)<input className={feld} style={st} value={w.label} onChange={(ev) => set("label", ev.target.value)} placeholder="automatisch: Marke + Variante" /></label>
      </div>

      <div className="mt-3 rounded-lg p-3 text-[12px]" style={{ background: "var(--wa-06)" }} data-testid="auftrag-prognose">
        {prognose?.fehler ? <span className="text-amber-300">{prognose.fehler}</span> : e ? (
          <>
            <b className="text-white">Prognose:</b> {e.segmente} Segmente ({e.ez_jahre} EZ × {e.km_bereiche} km) · Zeilen/Tag {e.rows_tag.toLocaleString("de-DE")} ({e.segmente} × {e.rows} × {e.crawls_per_day}) · Zeilen/30 Tage {e.rows_monat.toLocaleString("de-DE")} · geschätzte Monatskosten <b className="text-white">{Number(e.kosten_monat_usd).toFixed(2)} $</b>
            <div className="mt-1 text-zinc-400">Alle aktiven Marktanalysen zusammen: {prognose.segmente} Segmente · {(prognose.rows_monat || 0).toLocaleString("de-DE")} Zeilen/Monat · <span className={prognose.ueberschritten ? "text-red-300 font-semibold" : ""}>{Number(prognose.kosten_monat_usd).toFixed(2)} $ von {Number(prognose.budget_usd).toFixed(0)} $ Budget</span>{prognose.ueberschritten ? " — würde das Monatsbudget überschreiten" : ""}</div>
          </>
        ) : <span className="text-zinc-500">Marke, Modell, EZ-Jahre und km-Bereiche wählen — die Prognose rechnet sofort.</span>}
      </div>

      {test && (
        <div className="mt-3 rounded-lg p-3 text-[12px]" style={{ background: "var(--wa-06)" }} data-testid="auftrag-testlauf">
          <div className="text-white font-semibold">Testlauf: {test.anzahl} Fahrzeuge · {test.segment} · {test.sortiert ? "Preis aufsteigend ✓" : "Sortierung NICHT bestätigt"} · EZ {test.alle_ez_ok ? "✓" : "✗"} · km {test.alle_km_ok ? "✓" : "✗"} · {Number(test.usd || 0).toFixed(3)} $ · {test.actor}</div>
          <table className="w-full mt-1"><thead><tr className="text-left text-zinc-500 text-[11px] uppercase"><th className="pr-2">Fahrzeug</th><th className="pr-2">EZ</th><th className="pr-2 text-right">km</th><th className="pr-2 text-right">Preis</th><th className="pr-2">Motor</th></tr></thead>
            <tbody>{test.zeilen.map((z, i) => <tr key={i} className="border-t border-white/5 tabular-nums"><td className="pr-2 text-zinc-200">{z.title}</td><td className="pr-2" style={{ color: z.ez_ok ? undefined : "var(--st-rot)" }}>{z.first_registration}</td><td className="pr-2 text-right" style={{ color: z.km_ok ? undefined : "var(--st-rot)" }}>{z.mileage_km?.toLocaleString("de-DE")}</td><td className="pr-2 text-right text-white">{eur(z.price_gross)}</td><td className="pr-2">{z.power_kw ? `${z.power_kw} kW ` : ""}{z.fuel} {z.gearbox}</td></tr>)}</tbody></table>
          <div className="mt-1 text-[11px] text-zinc-500">Stand {datumZeit(new Date().toISOString())} · Der Testlauf prüft nur das erste Segment (erstes EZ-Jahr, erster km-Bereich).</div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={testlauf} disabled={!superAdmin || !!busy || !w.make || !w.model} data-testid="auftrag-testlauf-knopf"><FlaskConical size={13} /> Testlauf (5 Treffer, kostet Budget)</Button>
        <Button size="sm" variant="outline" onClick={() => speichern("paused")} disabled={!superAdmin || !!busy} data-testid="auftrag-speichern">Speichern (pausiert)</Button>
        <Button size="sm" onClick={() => speichern("active")} disabled={!superAdmin || !!busy} data-testid="auftrag-aktivieren"><Play size={13} /> Aktivieren</Button>
      </div>
    </Card>
  );
}

function Chip({ aktiv, onClick, children, testid }) {
  return (
    <button type="button" onClick={onClick} data-testid={testid} aria-pressed={!!aktiv}
            className="rounded-full px-2.5 py-1 text-[12px] border transition-colors"
            style={{ borderColor: aktiv ? "var(--accent-red)" : "var(--wa-12)", background: aktiv ? "rgba(255,59,48,.15)" : "transparent", color: aktiv ? "#fff" : "var(--text-secondary)" }}>
      {children}
    </button>
  );
}
