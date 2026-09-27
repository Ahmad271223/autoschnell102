import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, RefreshCw, Play, Gauge, Lock } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState } from "./_ui";
import { HealthBadge, HealthZaehler } from "@/components/MarktHealth";
import {
  AENDERUNG_STATUS, DATENQUALITAET, OPTIMIERUNG_MODUS, VORSCHLAG_STATUS, VORSCHLAG_TYP, datumZeit, ersparnisText, frequenzText, healthInfo,
  laeufeText, pct, wirkungGeldText, wirkungText,
} from "@/lib/markt";

/**
 * Admin → Marktanalyse → Segment-Optimierung (Master-Auftrag Ahmad 26.09.2026, Phase F, Abschnitte 27–33, 42–44, 47):
 * Health je Suchauftrag und Segment (nur aus gespeicherten Tageswerten, getrennt von der technischen Datenqualität),
 * konfigurierbare Zuordnung Activity Score → empfohlene Frequenz, Optimierungsvorschläge mit Annehmen/Ablehnen/
 * Übernehmen (Zusammenlegen/Aufteilen nur per Übernahme durch den Super-Admin: neue Fassung, Auftrag pausiert,
 * danach Testlauf), geschätzte Ersparnis aus den echten Crawl-Kosten. Lesen: Admin; Schreiben: Super-Admin.
 * Phase G: Modus-Schalter OBSERVE ↔ SAFE_AUTO (FULL_AUTO gesperrt, mit Rückfrage) und das Protokoll der
 * SAFE_AUTO-Änderungen (wer, alt → neu, Grund) mit Rücknahme je Eintrag.
 */
const MODUS_FRAGE = {
  SAFE_AUTO: "SAFE_AUTO einschalten?\n\nDas System darf dann NUR:\n• die Abruf-Frequenz einzelner Segmente senken,\n• EMPTY-Segmente pausieren (Nachprüfung in festen Abständen),\n• HOT-Segmente im Tagesplan zuerst planen.\n\nNie km-Bereiche, EZ-Jahre, Zeilen oder Filter ändern, nie mehr Abrufe als der Suchauftrag vorsieht. Jede Änderung steht im Protokoll und ist einzeln rücknehmbar.",
  OBSERVE: "Zurück auf OBSERVE?\n\nAlle SAFE_AUTO-Wirkungen werden aufgehoben — ab dem nächsten Tagesplan wird wieder jedes Segment wie vom Suchauftrag vorgesehen geplant. Die Empfehlungen bleiben sichtbar.",
};
const STATUS_FILTER = [["offen", "offen"], ["alle", "alle"], ["PROPOSED", "vorgeschlagen"], ["ACCEPTED", "angenommen"],
  ["APPLIED", "übernommen"], ["REJECTED", "abgelehnt"], ["OBSOLETE", "überholt"]];
const STRUKTUR = ["MERGE_KM_BUCKETS", "SPLIT_KM_BUCKET"];

/** Frequenzstufen als Formularzeilen (Zahlen als Text, leer = keine Spanne). */
export function stufenZuFormular(frequenz) {
  return (frequenz?.stufen || []).map((s) => ({
    ab: String(s.ab ?? ""), crawls_per_day: String(s.crawls_per_day ?? 1), intervall_tage: String(s.intervall_tage ?? 1),
    intervall_tage_bis: s.intervall_tage_bis ? String(s.intervall_tage_bis) : "",
  }));
}

/** Formular → Anfrage (PUT /admin/market/optimierung/frequenz); ungültige Zahl -> null (nichts senden). */
export function formularZuFrequenz(stufen, nachpruefung) {
  const zahl = (v) => (/^\d+$/.test(String(v ?? "").trim()) ? Number(String(v).trim()) : null);
  const raus = [];
  for (const s of stufen) {
    const ab = zahl(s.ab); const k = zahl(s.crawls_per_day); const n = zahl(s.intervall_tage);
    const bis = String(s.intervall_tage_bis ?? "").trim() === "" ? null : zahl(s.intervall_tage_bis);
    if (ab === null || k === null || n === null || (String(s.intervall_tage_bis ?? "").trim() !== "" && bis === null)) return null;
    raus.push({ ab, crawls_per_day: k, intervall_tage: n, intervall_tage_bis: bis });
  }
  const nach = zahl(nachpruefung);
  if (nach === null) return null;
  return { stufen: raus, empty_nachpruefung_tage: nach };
}

function evidenzText(v) {
  const e = v.evidence || {};
  if (v.typ === "MERGE_KM_BUCKETS") {
    return `${e.days ?? "—"} Tage · Ø ${e.avg_rows?.a ?? "—"} / ${e.avg_rows?.b ?? "—"} Autos je Lauf · leer ${pct((e.empty_rate?.a ?? 0) * 100, false)} / ${pct((e.empty_rate?.b ?? 0) * 100, false)} · ${(e.je_ez || []).length} EZ-Jahre`;
  }
  if (v.typ === "SPLIT_KM_BUCKET") {
    return `${e.days ?? "—"} Tage · Ø ${e.avg_rows ?? "—"} Autos (voll) · Preisstreuung ${pct(e.streuung_median_pct, false)} · ${(e.je_ez || []).length} EZ-Jahre`;
  }
  return `${e.days ?? "—"} Tage · ${e.valid_runs ?? "—"} gültige Läufe · Ø ${e.avg_rows ?? "—"} Autos · leer ${pct((e.empty_rate ?? 0) * 100, false)} · Score ${e.activity_score ?? "—"}`;
}

const SEITE = 100;
const LEER_LISTE = { liste: null, gesamt: 0, weitere: false, fehler: "", laedt: false };

/** Prüfbefund F10/F12: eine gefilterte, seitenweise Liste mit Gesamtzahl. Prüfbefund F11: nur die Antwort der
 *  LETZTEN Anfrage zählt (laufNr) — eine spät eintreffende ältere Antwort überschreibt den aktuellen Filter nie. */
function useSeitenListe(url, feld, parameter, fehlerText) {
  const [stand, setStand] = useState(LEER_LISTE);
  const laufNr = useRef(0);
  const laden = useCallback(async (filter, { anhaengen = false, offset = 0 } = {}) => {
    const nr = ++laufNr.current;
    setStand((s) => ({ ...s, laedt: true, fehler: "" }));
    try {
      const r = await api.get(url, { params: { ...parameter(filter), limit: SEITE, offset } });
      if (nr !== laufNr.current) return;
      const neu = r.data?.[feld] || [];
      setStand((s) => ({ liste: anhaengen ? [...(s.liste || []), ...neu] : neu, gesamt: Number(r.data?.gesamt ?? neu.length),
                         weitere: !!r.data?.weitere, fehler: "", laedt: false, jeTyp: r.data?.je_typ }));
    } catch (e) {
      if (nr !== laufNr.current) return;
      setStand((s) => ({ ...s, liste: anhaengen ? s.liste : (s.liste || null), fehler: errMsg(e, fehlerText), laedt: false }));
    }
  }, [url, feld, parameter, fehlerText]);
  return [stand, laden];
}

const vorschlagParameter = (f) => ({ status: f.status, ...(f.typ ? { typ: f.typ } : {}), ...(f.model_id ? { model_id: f.model_id } : {}) });
const protokollParameter = (f) => ({ status: f.status, ...(f.model_id ? { model_id: f.model_id } : {}) });

function ListenFuss({ stand, testid, onWeitere, onErneut }) {
  if (stand.fehler) {
    return (
      <div className="px-4 py-3 text-[12px] flex items-center gap-2" style={{ color: "var(--st-rot)" }} role="alert" data-testid={`${testid}-fehler`}>
        {stand.fehler}
        <Button size="sm" variant="outline" onClick={onErneut} data-testid={`${testid}-erneut`}><RefreshCw size={12} /> Erneut versuchen</Button>
      </div>
    );
  }
  if (!stand.liste) return null;
  return (
    <div className="px-4 py-2 flex items-center gap-2 text-[11px] text-zinc-500" data-testid={`${testid}-zahl`}>
      {stand.liste.length} von {stand.gesamt}
      {stand.weitere && <Button size="sm" variant="ghost" disabled={stand.laedt} onClick={onWeitere} data-testid={`${testid}-weitere`}>Weitere laden</Button>}
    </div>
  );
}

export default function MarktOptimierung() {
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;
  const [daten, setDaten] = useState(null);
  const [filter, setFilter] = useState({ status: "offen", typ: "", model_id: "" });
  const [fehler, setFehler] = useState("");
  const [busy, setBusy] = useState("");
  const [protokoll, setProtokoll] = useState({ status: "alle", model_id: "" });
  // aufgeklappte Segmenttabellen laden nach jeder Aktion neu (Prüfbefund F15)
  const [neuLaden, setNeuLaden] = useState(0);
  // Prüfbefund F11: der AKTUELLE Filter (nicht der aus dem Moment des Klicks) gilt beim Neuladen nach einer Aktion
  const filterRef = useRef(filter);
  const protokollRef = useRef(protokoll);
  const datenNr = useRef(0);
  const [vorschlaege, ladenVorschlaege] = useSeitenListe("/admin/market/optimierung/vorschlaege", "vorschlaege", vorschlagParameter,
    "Vorschläge konnten nicht geladen werden");
  const [aenderungen, ladenAenderungen] = useSeitenListe("/admin/market/optimierung/aenderungen", "aenderungen", protokollParameter,
    "SAFE_AUTO-Protokoll konnte nicht geladen werden");

  const laden = useCallback(async () => {
    const nr = ++datenNr.current;
    try {
      const r = await api.get("/admin/market/optimierung");
      if (nr !== datenNr.current) return;
      setDaten(r.data);
      setFehler("");
    } catch (e) { if (nr === datenNr.current) setFehler(errMsg(e, "Segment-Optimierung konnte nicht geladen werden")); }
    setNeuLaden((n) => n + 1);
    await Promise.all([ladenVorschlaege(filterRef.current), ladenAenderungen(protokollRef.current)]);
  }, [ladenVorschlaege, ladenAenderungen]);
  useEffect(() => { laden(); }, [laden]);

  const filterSetzen = (k, v) => { const f = { ...filterRef.current, [k]: v }; filterRef.current = f; setFilter(f); ladenVorschlaege(f); };
  const protokollSetzen = (k, v) => { const p = { ...protokollRef.current, [k]: v }; protokollRef.current = p; setProtokoll(p); ladenAenderungen(p); };
  const aktion = async (name, fn, erfolg) => {
    setBusy(name);
    try { const r = await fn(); toast.success(typeof erfolg === "function" ? erfolg(r.data) : erfolg); await laden(); }
    catch (e) { toast.error(errMsg(e, "Aktion fehlgeschlagen")); }
    finally { setBusy(""); }
  };
  const modusSetzen = (modus) => {
    if (!window.confirm(MODUS_FRAGE[modus] || `Modus ${modus} setzen?`)) return;
    aktion(`modus-${modus}`, () => api.put("/admin/market/optimierung/modus", { modus }),
      (d) => (d.modus === "SAFE_AUTO" ? `SAFE_AUTO an — ${d.safe_auto?.angewendet ?? 0} Änderung(en) angewendet` : `OBSERVE — ${d.aufgehoben ?? 0} Wirkung(en) aufgehoben`));
  };
  const zuruecknehmen = (a, beschreibung) => {
    const was = beschreibung || `${wirkungText(a.alt, a.typ)} → ${wirkungText(a.neu, a.typ)}`;
    if (!window.confirm(`Diese SAFE_AUTO-Änderung zurücknehmen?\n\n${a.label || a.model_id} · ${[a.ez_label, a.km_label].filter(Boolean).join(" · ")}\n${was}\n\nDiese Art Änderung gilt für das Segment dann als abgelehnt (bis Sie die Ablehnung aufheben); SAFE_AUTO lässt das Segment zusätzlich 30 Tage in Ruhe.`)) return;
    aktion(`zurueck-${a.id}`, () => api.post(`/admin/market/optimierung/aenderungen/${a.id}/zuruecknehmen`), "Änderung zurückgenommen");
  };
  const vorschlagZuruecknehmen = (v) => zuruecknehmen(
    { id: v.aenderung_id, label: v.label, model_id: v.model_id, ez_label: v.ez_label, km_label: v.km_label, typ: v.typ },
    `${VORSCHLAG_TYP[v.typ] || v.typ}: ${v.reason || ""}`);
  const ablehnungAufheben = (v) => {
    if (!window.confirm(`Ablehnung aufheben?\n\n${VORSCHLAG_TYP[v.typ] || v.typ} · ${v.label || v.model_id} ${[v.ez_label, v.km_label].filter(Boolean).join(" · ")}\n\nDanach darf SAFE_AUTO diese Art Änderung für das Segment wieder anwenden (ab dem nächsten Lauf; eine Ruhezeit nach einer Rücknahme bleibt).`)) return;
    aktion(`aufheben-${v.id}`, () => api.post(`/admin/market/optimierung/vorschlaege/${v.id}/ablehnung-aufheben`), "Ablehnung aufgehoben");
  };
  const berechnen = () => aktion("berechnen", () => api.post("/admin/market/optimierung/berechnen"),
    (d) => `Health berechnet: ${d.segmente ?? 0} Segmente in ${d.modelle ?? 0} Aufträgen · ${d.vorschlaege_neu ?? 0} neue Vorschläge`);
  const entscheiden = (v, was) => aktion(`${was}-${v.id}`, () => api.post(`/admin/market/optimierung/vorschlaege/${v.id}/${was}`),
    was === "annehmen" ? "Vorschlag angenommen" : "Vorschlag abgelehnt");
  const uebernehmen = (v) => {
    const text = `${VORSCHLAG_TYP[v.typ]} übernehmen?\n\n${v.km_label || ""}\n\nDer Suchauftrag bekommt eine NEUE FASSUNG mit den neuen km-Bereichen (die alte Historie bleibt unverändert) und wird PAUSIERT. Aktivieren erst nach einem neuen Testlauf über alle Segmente.`;
    if (!window.confirm(text)) return;
    aktion(`uebernehmen-${v.id}`, () => api.post(`/admin/market/optimierung/vorschlaege/${v.id}/uebernehmen`),
      (d) => `Übernommen — Fassung v${d.modell?.version ?? "?"}, Suchauftrag pausiert (Testlauf nötig)`);
  };

  if (fehler) {
    return <Card data-testid="opt-fehler"><div className="text-red-300 text-sm">{fehler}</div>
      <Button size="sm" className="mt-2" onClick={laden}><RefreshCw size={14} /> Erneut laden</Button></Card>;
  }
  if (!daten) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  const stand = daten.stand || {};
  const modelle = daten.modelle || [];
  const liste = vorschlaege.liste;
  const protokollListe = aenderungen.liste;
  const auswahlStil = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };
  return (
    <div data-testid="opt-seite">
      <Link to="/admin/markt" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-2"><ArrowLeft size={14} /> Marktanalyse</Link>
      <PageHeader title="Segment-Optimierung" subtitle="Wie sinnvoll ist jedes Segment? Health, empfohlene Frequenz und Vorschläge — nur aus gespeicherten Tageswerten, keine Zusatzabrufe, keine Kosten."
                  action={<div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={laden}><RefreshCw size={14} /> Aktualisieren</Button>
                    {superAdmin && <Button size="sm" onClick={berechnen} disabled={!!busy} data-testid="opt-berechnen"
                                           title="Sonst einmal täglich nach dem Crawl-Fenster im Hintergrund"><Play size={14} /> Health jetzt berechnen</Button>}
                  </div>} />

      <Card className="mb-4" data-testid="opt-hinweis">
        <div className="text-[12px] text-zinc-300">{daten.hinweis}</div>
        <div className="mt-1 text-[11px] text-zinc-500" data-testid="opt-stand">{standText(stand)}</div>
      </Card>

      <Card className="mb-4" data-testid="opt-modus">
        <div className="text-[13px] font-semibold text-white mb-2 inline-flex items-center gap-1.5"><Gauge size={14} /> Modus: {OPTIMIERUNG_MODUS[daten.modus] || daten.modus}</div>
        <div className="grid md:grid-cols-3 gap-2">
          {(daten.modi || []).map((m) => {
            const aktiv = m.modus === daten.modus;
            return (
              <div key={m.modus} className="rounded-lg p-2.5 text-[12px]" data-testid={`opt-modus-${m.modus}`}
                   style={{ background: aktiv ? "var(--wa-12)" : "var(--wa-06)", border: `1px solid ${aktiv ? "var(--st-gruen)" : "var(--wa-08)"}` }}>
                <div className="font-semibold text-white inline-flex items-center gap-1">{m.gesperrt && <Lock size={12} />}{m.modus}{aktiv ? " · aktiv" : ""}</div>
                <div className="text-zinc-400 mt-0.5">{m.text}</div>
                {/* Phase G: Umschalten nur Super-Admin, mit Rückfrage und Protokoll; FULL_AUTO bleibt gesperrt */}
                {superAdmin && !aktiv && !m.gesperrt && (
                  <Button size="sm" variant="outline" className="mt-2" disabled={!!busy} onClick={() => modusSetzen(m.modus)} data-testid={`opt-modus-setzen-${m.modus}`}>
                    {m.modus === "OBSERVE" ? "Zurück auf OBSERVE" : `${m.modus} einschalten`}</Button>
                )}
              </div>
            );
          })}
        </div>
      </Card>

      <FrequenzKarte daten={daten} superAdmin={superAdmin} onGespeichert={laden} />

      <Card className="mb-4" data-testid="opt-uebersicht">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[12px]">
          <Kachel label="Segmente je Health" wert={<HealthZaehler zaehler={daten.zaehler} testid="opt-zaehler" />} />
          <Kachel label="Offene Vorschläge" wert={String((daten.vorschlaege_je_status?.PROPOSED || 0) + (daten.vorschlaege_je_status?.ACCEPTED || 0))} />
          <Kachel label="Geschätzte Ersparnis (offene Vorschläge, je Segment einmal)" testid="opt-ersparnis"
                  wert={wirkungGeldText(daten.ersparnis_offen_usd, daten.laeufe_frei_offen_monat, daten.budget_grenze)} />
          <Kachel label={`Wirkung von SAFE_AUTO (${daten.safe_auto_aktiv ?? 0} aktive Änderungen)`} testid="opt-ersparnis-safe-auto"
                  wert={wirkungGeldText(daten.ersparnis_safe_auto_usd, daten.laeufe_frei_safe_auto_monat, daten.budget_grenze)} />
        </div>
        {daten.budget_grenze && <div className="mt-2 text-[11px]" style={{ color: "var(--st-amber)" }} data-testid="opt-budget-grenze">
          Das Monatsbudget ist die Grenze (fällige Segmente warten) — weniger Abrufe einzelner Segmente senken die Rechnung nicht, die frei werdenden Plätze gehen an andere Segmente.
        </div>}
        {daten.schwellen && <div className="mt-2 text-[11px] text-zinc-500" data-testid="opt-schwellen">
          Fenster {daten.schwellen.fenster_tage} Tage · EMPTY ab {daten.schwellen.min_laeufe_empty} gültigen Läufen mit ≥ {Math.round(daten.schwellen.empty_anteil * 100)} % ohne Treffer ·
          THIN unter Ø {daten.schwellen.thin_max_zeilen} Autos je Lauf · HOT ab Score {daten.schwellen.hot_ab_score} · HEALTHY ab {daten.schwellen.healthy_ab_score} ·
          UNSTABLE ab {Math.round(daten.schwellen.unstable_ungueltig_anteil * 100)} % ungültigen Läufen oder {daten.schwellen.unstable_volatilitaet_pct} % Schwankung ·
          STALE nach erwartetem Abstand + {daten.schwellen.stale_puffer_tage} Tage · Zusammenlegen ab {daten.schwellen.merge_min_laeufe} Läufen · Aufteilen ab {Math.round(daten.schwellen.split_voll_anteil * 100)} % voll und {daten.schwellen.split_streuung_pct} % Preisstreuung
          {daten.schwellen.erwartet_puffer_anteil ? ` · unter SAFE_AUTO gemessen an den erwartbaren Läufen (mindestens ${Math.round(daten.schwellen.erwartet_puffer_anteil * 100)} %, ein Ausfall zählt nicht)` : ""}
        </div>}
      </Card>

      <Card padded={false} className="mb-4" data-testid="opt-health">
        <div className="px-4 py-3 text-[13px] font-semibold text-white" style={{ borderBottom: "1px solid var(--wa-08)" }}>Health je Suchauftrag ({modelle.length})</div>
        {modelle.length === 0 ? <EmptyState title="Noch keine Health-Werte" hint="Health entsteht aus mindestens 7 gültigen Läufen je Segment — täglich nach dem Crawl-Fenster oder per „Health jetzt berechnen“." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] min-w-[900px]">
              <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                <th className="px-3 py-2">Suchauftrag</th><th className="px-3 py-2">Health</th><th className="px-3 py-2">Segmente</th>
                <th className="px-3 py-2 text-right">Ø Score</th><th className="px-3 py-2 text-right">Vorschläge</th><th className="px-3 py-2 text-right">Ersparnis</th><th className="px-3 py-2 text-right">Aktion</th>
              </tr></thead>
              <tbody>{modelle.map((m) => <ModellZeile key={m.model_id} m={m} neuLaden={neuLaden} />)}</tbody>
            </table>
          </div>
        )}
      </Card>

      <Card padded={false} className="mb-4" data-testid="opt-vorschlaege">
        <div className="px-4 py-3 flex flex-wrap items-center gap-2 text-[13px]" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <span className="font-semibold text-white">Vorschläge</span>
          <select className="rounded-lg px-2 py-1 text-[12px]" style={auswahlStil}
                  value={filter.status} onChange={(e) => filterSetzen("status", e.target.value)} data-testid="opt-filter-status">
            {STATUS_FILTER.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <select className="rounded-lg px-2 py-1 text-[12px]" style={auswahlStil}
                  value={filter.typ} onChange={(e) => filterSetzen("typ", e.target.value)} data-testid="opt-filter-typ">
            <option value="">alle Typen</option>
            {Object.entries(VORSCHLAG_TYP).map(([k, l]) => <option key={k} value={k}>{l}{vorschlaege.jeTyp?.[k] !== undefined ? ` (${vorschlaege.jeTyp[k]})` : ""}</option>)}
          </select>
          <select className="rounded-lg px-2 py-1 text-[12px]" style={auswahlStil}
                  value={filter.model_id} onChange={(e) => filterSetzen("model_id", e.target.value)} data-testid="opt-filter-auftrag">
            <option value="">alle Aufträge</option>
            {modelle.map((m) => <option key={m.model_id} value={m.model_id}>{m.label || m.model_id}</option>)}
          </select>
          <span className="text-[11px] text-zinc-500">Sortiert nach Wirkung. Zusammenlegen/Aufteilen nie automatisch — nur per „Übernehmen“ (neue Fassung, Auftrag pausiert, danach Testlauf).</span>
        </div>
        {!liste ? (vorschlaege.fehler ? null : <div className="flex items-center gap-2 text-zinc-500 text-sm p-4"><Spinner /> lade…</div>)
          : liste.length === 0 ? <EmptyState title="Keine Vorschläge" hint="Zu diesem Filter liegt kein Vorschlag vor." /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[1100px]">
                <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                  <th className="px-3 py-2">Typ</th><th className="px-3 py-2">Suchauftrag / Segment</th><th className="px-3 py-2">Begründung</th>
                  <th className="px-3 py-2">Confidence</th><th className="px-3 py-2 text-right">Wirkung</th><th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">Aktion</th>
                </tr></thead>
                <tbody>{liste.map((v) => (
                  <tr key={v.id} className="border-t border-white/5 align-top" data-testid={`opt-vorschlag-${v.id}`}>
                    <td className="px-3 py-1.5 text-white whitespace-nowrap">{VORSCHLAG_TYP[v.typ] || v.typ}{v.health && <div className="mt-0.5"><HealthBadge health={v.health} klein testid={`opt-vorschlag-health-${v.id}`} /></div>}</td>
                    <td className="px-3 py-1.5"><Link to={`/admin/markt/${v.model_id}${v.segment_id ? `?segment=${encodeURIComponent(v.segment_id)}` : ""}`} className="text-white hover:underline">{v.label || v.model_id}</Link>
                      <div className="text-[10px] text-zinc-500">{[v.ez_label, v.km_label].filter(Boolean).join(" · ")}{v.version ? ` · Fassung v${v.version}` : ""}</div></td>
                    <td className="px-3 py-1.5 text-zinc-300">{v.reason}<div className="text-[10px] text-zinc-500" data-testid={`opt-evidenz-${v.id}`}>{evidenzText(v)}</div></td>
                    <td className="px-3 py-1.5">{v.confidence || "—"}</td>
                    <td className="px-3 py-1.5 text-right whitespace-nowrap" style={{ color: Number(v.estimated_monthly_saving_usd) > 0 || Number(v.laeufe_frei_monat) > 0 ? "var(--st-gruen)" : Number(v.estimated_monthly_saving_usd) < 0 || Number(v.laeufe_frei_monat) < 0 ? "var(--st-amber)" : "var(--text-secondary)" }}
                        data-testid={`opt-ersparnis-${v.id}`}>{v.ersparnis_budget_grenze ? laeufeText(v.laeufe_frei_monat) : ersparnisText(v.estimated_monthly_saving_usd)}</td>
                    <td className="px-3 py-1.5"><Badge tone={(VORSCHLAG_STATUS[v.status] || {}).tone || "gray"}>{(VORSCHLAG_STATUS[v.status] || {}).text || v.status}</Badge>
                      {v.status === "OBSOLETE" && v.obsolet_grund && <div className="text-[10px] text-zinc-500">{v.obsolet_grund}</div>}
                      {v.status === "REJECTED" && v.entscheidung_grund && <div className="text-[10px] text-zinc-500">{v.entscheidung_grund}</div>}
                      {v.status === "APPLIED" && v.angewendet_von === "safe_auto" && <div className="text-[10px] text-zinc-500">durch SAFE_AUTO</div>}
                      {v.status === "APPLIED" && v.neue_version && <div className="text-[10px] text-zinc-500">Fassung v{v.alte_version} → v{v.neue_version}</div>}</td>
                    <td className="px-3 py-1.5 text-right whitespace-nowrap">
                      {superAdmin && v.status === "PROPOSED" && <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => entscheiden(v, "annehmen")} data-testid={`opt-annehmen-${v.id}`}>Annehmen</Button>}
                      {superAdmin && ["PROPOSED", "ACCEPTED"].includes(v.status) && <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => entscheiden(v, "ablehnen")} data-testid={`opt-ablehnen-${v.id}`}>Ablehnen</Button>}
                      {superAdmin && STRUKTUR.includes(v.typ) && ["PROPOSED", "ACCEPTED"].includes(v.status) && (
                        <Button size="sm" variant="outline" disabled={!!busy} onClick={() => uebernehmen(v)} data-testid={`opt-uebernehmen-${v.id}`}>Übernehmen</Button>)}
                      {/* Prüfbefund F12: jede angewendete SAFE_AUTO-Wirkung ist auch hier rücknehmbar */}
                      {superAdmin && v.status === "APPLIED" && v.angewendet_von === "safe_auto" && v.aenderung_id && (
                        <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => vorschlagZuruecknehmen(v)} data-testid={`opt-vorschlag-zuruecknehmen-${v.id}`}>Zurücknehmen</Button>)}
                      {/* Prüfbefund F3/F7/F13: abgelehnt bleibt abgelehnt, bis der Super-Admin es ausdrücklich aufhebt */}
                      {superAdmin && v.status === "REJECTED" && (
                        <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => ablehnungAufheben(v)} data-testid={`opt-aufheben-${v.id}`}>Ablehnung aufheben</Button>)}
                    </td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        <ListenFuss stand={vorschlaege} testid="opt-vorschlaege" onErneut={() => ladenVorschlaege(filterRef.current)}
                    onWeitere={() => ladenVorschlaege(filterRef.current, { anhaengen: true, offset: (liste || []).length })} />
      </Card>

      <Card padded={false} className="mb-4" data-testid="opt-protokoll">
        <div className="px-4 py-3 flex flex-wrap items-center gap-2 text-[13px]" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <span className="font-semibold text-white">Protokoll der SAFE_AUTO-Änderungen</span>
          <select className="rounded-lg px-2 py-1 text-[12px]" style={auswahlStil}
                  value={protokoll.status} onChange={(e) => protokollSetzen("status", e.target.value)} data-testid="opt-protokoll-status">
            <option value="alle">alle</option>
            {Object.entries(AENDERUNG_STATUS).map(([k, v]) => <option key={k} value={k}>{v.text}</option>)}
          </select>
          <select className="rounded-lg px-2 py-1 text-[12px]" style={auswahlStil}
                  value={protokoll.model_id} onChange={(e) => protokollSetzen("model_id", e.target.value)} data-testid="opt-protokoll-auftrag">
            <option value="">alle Aufträge</option>
            {modelle.map((m) => <option key={m.model_id} value={m.model_id}>{m.label || m.model_id}</option>)}
          </select>
          <span className="text-[11px] text-zinc-500">SAFE_AUTO ändert nur die Planung (seltener, pausiert mit Nachprüfung, HOT zuerst) — nie km-Bereiche, EZ, Zeilen oder Filter.</span>
        </div>
        {!protokollListe ? (aenderungen.fehler ? null : <div className="flex items-center gap-2 text-zinc-500 text-sm p-4"><Spinner /> lade…</div>)
          : protokollListe.length === 0 ? <EmptyState title="Keine SAFE_AUTO-Änderungen" hint={daten.modus === "SAFE_AUTO" ? "SAFE_AUTO ist an — es gab noch keinen Anlass für eine Änderung." : "Im Modus OBSERVE ändert das System nichts, es gibt nur Empfehlungen."} /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[1000px]">
                <thead><tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                  <th className="px-3 py-2">Zeit</th><th className="px-3 py-2">Wer</th><th className="px-3 py-2">Segment</th><th className="px-3 py-2">alt → neu</th>
                  <th className="px-3 py-2">Grund</th><th className="px-3 py-2 text-right">Wirkung</th><th className="px-3 py-2">Status</th><th className="px-3 py-2 text-right">Aktion</th>
                </tr></thead>
                <tbody>{protokollListe.map((a) => (
                  <tr key={a.id} className="border-t border-white/5 align-top" data-testid={`opt-aenderung-${a.id}`}>
                    <td className="px-3 py-1.5 whitespace-nowrap">{datumZeit(a.at)}</td>
                    <td className="px-3 py-1.5">{a.wer === "safe_auto" ? "SAFE_AUTO" : a.wer}</td>
                    <td className="px-3 py-1.5"><Link to={`/admin/markt/${a.model_id}?segment=${encodeURIComponent(a.segment_id)}`} className="text-white hover:underline">{a.label || a.model_id}</Link>
                      <div className="text-[10px] text-zinc-500">{[a.ez_label, a.km_label].filter(Boolean).join(" · ")} · {VORSCHLAG_TYP[a.typ] || a.typ}</div></td>
                    <td className="px-3 py-1.5 whitespace-nowrap" data-testid={`opt-aenderung-wirkung-${a.id}`}>{wirkungText(a.alt, a.typ)} → {wirkungText(a.neu, a.typ)}</td>
                    <td className="px-3 py-1.5 text-zinc-300">{a.grund}{a.status !== "aktiv" && a.beendet_grund && <div className="text-[10px] text-zinc-500">beendet {datumZeit(a.beendet_at)} von {a.beendet_von === "safe_auto" ? "SAFE_AUTO" : a.beendet_von}: {a.beendet_grund}</div>}</td>
                    <td className="px-3 py-1.5 text-right whitespace-nowrap">{a.ersparnis_budget_grenze ? laeufeText(a.laeufe_frei_monat) : ersparnisText(a.estimated_monthly_saving_usd)}</td>
                    <td className="px-3 py-1.5"><Badge tone={(AENDERUNG_STATUS[a.status] || {}).tone || "gray"}>{(AENDERUNG_STATUS[a.status] || {}).text || a.status}</Badge></td>
                    <td className="px-3 py-1.5 text-right">{superAdmin && a.status === "aktiv" && (
                      <Button size="sm" variant="ghost" disabled={!!busy} onClick={() => zuruecknehmen(a)} data-testid={`opt-zuruecknehmen-${a.id}`}>Zurücknehmen</Button>)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        <ListenFuss stand={aenderungen} testid="opt-protokoll" onErneut={() => ladenAenderungen(protokollRef.current)}
                    onWeitere={() => ladenAenderungen(protokollRef.current, { anhaengen: true, offset: (protokollListe || []).length })} />
      </Card>
    </div>
  );
}

/** Prüfbefund F9: der Admin-Knopf und der tägliche Lauf haben je einen eigenen Stand. */
export function standText(stand) {
  if (!stand?.letzter_lauf_at) return "Noch nicht berechnet — täglich nach dem Crawl-Fenster oder per Knopf.";
  const teile = [`Zuletzt berechnet ${datumZeit(stand.letzter_lauf_at)} (${stand.quelle === "admin" ? "per Knopf" : "täglich"})`];
  if (stand.quelle === "admin" && stand.taeglich_lauf_at) teile.push(`täglicher Lauf ${datumZeit(stand.taeglich_lauf_at)}`);
  if (stand.fehler) teile.push(`${stand.fehler} Auftrag/Aufträge mit Fehler`);
  return teile.join(" · ");
}

function ModellZeile({ m, neuLaden }) {
  const [offen, setOffen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [fehler, setFehler] = useState("");
  const laufNr = useRef(0);
  const ladenDetail = useCallback(async () => {
    const nr = ++laufNr.current;
    setFehler("");
    try {
      const r = await api.get(`/admin/market/health/models/${m.model_id}`);
      if (nr === laufNr.current) setDetail(r.data);
    } catch (e) {
      if (nr === laufNr.current) { setFehler(errMsg(e, "Segmente konnten nicht geladen werden")); toast.error(errMsg(e, "Segmente konnten nicht geladen werden")); }
    }
  }, [m.model_id]);
  // Prüfbefund F15: eine aufgeklappte Tabelle lädt nach jeder Aktion (Rücknahme, Moduswechsel, Berechnung) neu
  useEffect(() => { if (offen) ladenDetail(); }, [neuLaden]); // eslint-disable-line react-hooks/exhaustive-deps
  const umschalten = () => {
    const neu = !offen;
    setOffen(neu);
    if (neu && (!detail || fehler)) ladenDetail();
  };
  return (
    <Fragment>
      <tr className="border-t border-white/5" data-testid={`opt-modell-${m.model_id}`}>
        <td className="px-3 py-1.5"><Link to={`/admin/markt/${m.model_id}`} className="text-white hover:underline">{m.label || m.model_id}</Link>
          <div className="text-[10px] text-zinc-500">Fassung v{m.version || 1} · Stichtag {m.tag || "—"}</div></td>
        <td className="px-3 py-1.5"><HealthBadge health={m.health} klein testid={`opt-modell-health-${m.model_id}`} /></td>
        <td className="px-3 py-1.5"><HealthZaehler zaehler={m.zaehler} testid={`opt-modell-zaehler-${m.model_id}`} /></td>
        <td className="px-3 py-1.5 text-right tabular-nums">{m.activity_score_mittel ?? "—"}</td>
        <td className="px-3 py-1.5 text-right tabular-nums">{m.vorschlaege_offen ?? 0}</td>
        <td className="px-3 py-1.5 text-right tabular-nums">{wirkungGeldText(m.ersparnis_offen_usd, m.laeufe_frei_offen_monat, m.ersparnis_budget_grenze)}</td>
        <td className="px-3 py-1.5 text-right"><button type="button" className="text-[11px] underline text-zinc-300" onClick={umschalten} data-testid={`opt-modell-segmente-${m.model_id}`}>{offen ? "Segmente ausblenden" : "Segmente"}</button></td>
      </tr>
      {offen && (
        <tr data-testid={`opt-modell-detail-${m.model_id}`}>
          <td colSpan={7} className="px-3 pb-3">
            {fehler ? (
              <div className="text-[11px] flex items-center gap-2" style={{ color: "var(--st-rot)" }} role="alert" data-testid={`opt-modell-detail-fehler-${m.model_id}`}>
                {fehler}
                <Button size="sm" variant="outline" onClick={ladenDetail} data-testid={`opt-modell-detail-erneut-${m.model_id}`}><RefreshCw size={12} /> Erneut versuchen</Button>
              </div>
            ) : !detail ? <div className="text-[11px] text-zinc-500"><Spinner size={12} /> lade…</div>
              : (detail.segmente || []).length === 0 ? <div className="text-[11px] text-zinc-500">Keine Segmente mit Health-Werten.</div> : (
              <table className="w-full text-[11px]">
                <thead><tr className="text-left text-zinc-500">
                  <th className="px-2 py-1">Segment</th><th className="px-2 py-1">Health</th><th className="px-2 py-1 text-right">Score</th><th className="px-2 py-1">Empfohlen</th>
                  <th className="px-2 py-1 text-right">gültige Läufe</th><th className="px-2 py-1 text-right">Ø Autos</th><th className="px-2 py-1 text-right">leer</th>
                  <th className="px-2 py-1">Qualität</th><th className="px-2 py-1">Confidence</th><th className="px-2 py-1">Grund</th><th className="px-2 py-1">SAFE_AUTO</th>
                </tr></thead>
                <tbody>{(detail.segmente || []).map((s) => (
                  <tr key={s.segment_id} className={`border-t border-white/5 tabular-nums ${s.enabled ? "" : "opacity-50"}`} data-testid={`opt-segment-${s.segment_id}`}>
                    <td className="px-2 py-1"><Link to={`/admin/markt/${m.model_id}?segment=${encodeURIComponent(s.segment_id)}`} className="text-white hover:underline">{s.ez_label || "alle EZ"} · {s.km_label}</Link></td>
                    <td className="px-2 py-1"><HealthBadge health={s.health} klein titel={`${healthInfo(s.health).text}${s.health_text ? ` — ${s.health_text}` : ""}`} testid={`opt-segment-health-${s.segment_id}`} /></td>
                    <td className="px-2 py-1 text-right">{s.activity_score ?? "—"}</td>
                    <td className="px-2 py-1">{frequenzText(s.recommended_frequency_days, s.recommended_pause)}</td>
                    <td className="px-2 py-1 text-right">{s.valid_runs ?? 0}{s.invalid_runs ? ` (+${s.invalid_runs} ungültig)` : ""}{s.erwartete_laeufe != null ? ` von ${s.erwartete_laeufe} erwartet` : ""}</td>
                    <td className="px-2 py-1 text-right">{s.avg_valid_rows ?? "—"}</td>
                    <td className="px-2 py-1 text-right">{s.empty_rate != null ? pct(s.empty_rate * 100, false) : "—"}</td>
                    <td className="px-2 py-1" style={{ color: (DATENQUALITAET[s.data_quality] || DATENQUALITAET.UNKNOWN).farbe }}>{(DATENQUALITAET[s.data_quality] || DATENQUALITAET.UNKNOWN).zaehler}</td>
                    <td className="px-2 py-1">{s.confidence || "—"}</td>
                    <td className="px-2 py-1 text-zinc-400">{s.health_text || ""}</td>
                    <td className="px-2 py-1" data-testid={`opt-segment-wirkung-${s.segment_id}`}>{s.safe_auto_wirkung ? wirkungText(s.safe_auto_wirkung) : "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function FrequenzKarte({ daten, superAdmin, onGespeichert }) {
  const [stufen, setStufen] = useState(() => stufenZuFormular(daten.frequenz));
  const [nach, setNach] = useState(String(daten.frequenz?.empty_nachpruefung_tage ?? 7));
  const [busy, setBusy] = useState(false);
  const anfrage = formularZuFrequenz(stufen, nach);
  const setzen = (i, k, v) => setStufen((alt) => alt.map((s, j) => (j === i ? { ...s, [k]: v } : s)));
  const speichern = async () => {
    if (!anfrage) return;
    setBusy(true);
    try { await api.put("/admin/market/optimierung/frequenz", anfrage); toast.success("Frequenz-Zuordnung gespeichert — wirkt ab der nächsten Berechnung"); onGespeichert?.(); }
    catch (e) { toast.error(errMsg(e, "Speichern fehlgeschlagen")); }
    finally { setBusy(false); }
  };
  const standard = () => { setStufen(stufenZuFormular(daten.frequenz_standard)); setNach(String(daten.frequenz_standard?.empty_nachpruefung_tage ?? 7)); };
  const feld = "w-16 rounded-lg px-2 py-1 text-[12px] outline-none";
  const st = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };
  return (
    <Card className="mb-4" data-testid="opt-frequenz">
      <div className="text-[13px] font-semibold text-white mb-1">Frequenz-Zuordnung (Activity Score → empfohlene Frequenz)</div>
      <div className="text-[11px] text-zinc-500 mb-2">Im Modus OBSERVE nur eine Empfehlung; in SAFE_AUTO wird damit nur gesenkt (nie häufiger als der Suchauftrag vorsieht). EMPTY: pausiert mit Nachprüfung.</div>
      <table className="text-[12px]">
        <thead><tr className="text-left text-zinc-500 text-[11px]"><th className="pr-3 py-1">ab Score</th><th className="pr-3 py-1">Abrufe je Tag</th><th className="pr-3 py-1">alle … Tage</th><th className="pr-3 py-1">bis … Tage (Spanne)</th></tr></thead>
        <tbody>{stufen.map((s, i) => (
          <tr key={i}>
            <td className="pr-3 py-1"><input className={feld} style={st} value={s.ab} disabled={!superAdmin} onChange={(e) => setzen(i, "ab", e.target.value)} data-testid={`opt-frequenz-ab-${i}`} /></td>
            <td className="pr-3 py-1"><input className={feld} style={st} value={s.crawls_per_day} disabled={!superAdmin} onChange={(e) => setzen(i, "crawls_per_day", e.target.value)} data-testid={`opt-frequenz-k-${i}`} /></td>
            <td className="pr-3 py-1"><input className={feld} style={st} value={s.intervall_tage} disabled={!superAdmin} onChange={(e) => setzen(i, "intervall_tage", e.target.value)} data-testid={`opt-frequenz-n-${i}`} /></td>
            <td className="pr-3 py-1"><input className={feld} style={st} value={s.intervall_tage_bis} disabled={!superAdmin} onChange={(e) => setzen(i, "intervall_tage_bis", e.target.value)} data-testid={`opt-frequenz-bis-${i}`} /></td>
          </tr>
        ))}
          <tr><td className="pr-3 py-1 text-zinc-400">EMPTY</td><td colSpan={3} className="pr-3 py-1 text-zinc-400">pausiert, Nachprüfung alle <input className={feld} style={st} value={nach} disabled={!superAdmin} onChange={(e) => setNach(e.target.value)} data-testid="opt-frequenz-nach" /> Tage</td></tr>
        </tbody>
      </table>
      {!anfrage && <div className="mt-1 text-[11px]" style={{ color: "var(--st-rot)" }} role="alert" data-testid="opt-frequenz-ungueltig">Bitte nur ganze Zahlen eingeben.</div>}
      {superAdmin && <div className="mt-2 flex gap-2">
        <Button size="sm" onClick={speichern} disabled={busy || !anfrage} data-testid="opt-frequenz-speichern">Speichern</Button>
        <Button size="sm" variant="ghost" onClick={standard} disabled={busy} data-testid="opt-frequenz-standard">Standard (75/45/20/0)</Button>
      </div>}
    </Card>
  );
}

function Kachel({ label, wert, testid }) {
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--wa-06)" }} data-testid={testid}>
      <div className="text-[11px] text-zinc-500">{label}</div>
      <div className="text-[13px] font-semibold text-white">{wert}</div>
    </div>
  );
}
