import { useEffect, useState } from "react";
import { Radar, RefreshCw } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { chanceTypText, datumKurz, datumZeit, eur, trendFarbe, trendText, zustandText } from "@/lib/markt";

const TYPEN = [["", "alle"], ["neu_guenstig", "neu & günstig"], ["stark_reduziert", "stark reduziert"], ["neu_top5", "neues Top-5-Angebot"], ["neues_minimum", "neues günstigstes Fahrzeug"]];

/**
 * Markt → Chancen (Deal Radar, Auftrag Ahmad 25.09.2026): regelbasiert,
 * ohne KI. Filter: Art, Modell, km-Bereich, Zeitraum. Nur lesend.
 */
export default function Chancen({ pfad = "/markt/chancen", modellePfad = "/markt/modelle", admin = false }) {
  const [filter, setFilter] = useState({ typ: "", model_id: "", km: "", tage: 7 });
  const [modelle, setModelle] = useState({ modelle: [], km_buckets: [] });
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");
  const [laedt, setLaedt] = useState(false);

  useEffect(() => { api.get(modellePfad).then((r) => setModelle(r.data)).catch(() => {}); }, [modellePfad]);
  const laden = async (f = filter) => {
    setLaedt(true);
    try {
      const params = { tage: f.tage, limit: 200 };
      if (f.typ) params.typ = f.typ;
      if (f.model_id) params.model_id = f.model_id;
      if (f.km) { const [a, b] = f.km.split("-"); params.km_min = a; params.km_max = b; }
      const r = await api.get(pfad, { params, timeout: 15000 });
      setDaten(r.data);
      setFehler("");
    } catch (e) { setFehler(errMsg(e, "Chancen konnten nicht geladen werden")); }
    finally { setLaedt(false); }
  };
  useEffect(() => { laden(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const setzen = (k, v) => { const f = { ...filter, [k]: v }; setFilter(f); laden(f); };
  const sel = "rounded-lg px-2.5 py-1.5 text-[12px] outline-none";
  const st = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };

  return (
    <div className={admin ? "" : "p-4 md:p-6 max-w-6xl mx-auto"} data-testid="chancen-page">
      <div className="overline inline-flex items-center gap-1.5"><Radar size={13} /> {admin ? "Marktanalyse" : "Markt"}</div>
      <h1 className="font-display font-black text-2xl tracking-tighter mt-1">Chancen</h1>
      <div className="text-[12px] mt-1" style={{ color: "var(--text-secondary)" }}>
        Auffällige Angebote aus der täglichen Beobachtung der 20 günstigsten Fahrzeuge je Modell, km- und EZ-Bereich. Regelbasiert, kein Marktmedian, keine Kaufempfehlung.
      </div>
      <div className="mt-4 flex flex-wrap gap-2 items-center" data-testid="chancen-filter">
        <select className={sel} style={st} value={filter.typ} onChange={(e) => setzen("typ", e.target.value)} data-testid="chancen-typ">
          {TYPEN.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select className={sel} style={st} value={filter.model_id} onChange={(e) => setzen("model_id", e.target.value)} data-testid="chancen-modell">
          <option value="">alle Modelle</option>
          {(modelle.modelle || []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
        </select>
        <select className={sel} style={st} value={filter.km} onChange={(e) => setzen("km", e.target.value)} data-testid="chancen-km">
          <option value="">alle km-Bereiche</option>
          {(modelle.km_buckets || []).map((b) => <option key={b.min_km} value={`${b.min_km}-${b.max_km}`}>{Math.round(b.min_km / 1000)}–{Math.round(b.max_km / 1000)}k km</option>)}
        </select>
        <select className={sel} style={st} value={filter.tage} onChange={(e) => setzen("tage", Number(e.target.value))} data-testid="chancen-tage">
          {[1, 3, 7, 14, 30].map((t) => <option key={t} value={t}>{t === 1 ? "heute" : `${t} Tage`}</option>)}
        </select>
        <button type="button" onClick={() => laden()} className="inline-flex items-center gap-1 text-[12px] px-2 py-1.5 rounded-lg hover:bg-white/10" style={{ color: "var(--text-secondary)" }}>
          <RefreshCw size={12} className={laedt ? "animate-spin" : ""} /> Aktualisieren
        </button>
      </div>
      {fehler && <div className="mt-4 text-sm" style={{ color: "var(--st-rot)" }} data-testid="chancen-fehler">{fehler}</div>}
      {daten && daten.chancen.length === 0 && !fehler && (
        <div className="mt-6 text-sm" style={{ color: "var(--text-secondary)" }} data-testid="chancen-leer">Keine Chancen im gewählten Zeitraum. Die Beobachtung wächst mit jedem Tag.</div>
      )}
      <ul className="mt-4 space-y-2" data-testid="chancen-liste">
        {(daten?.chancen || []).map((c) => (
          <li key={c.id} className="apple-surface p-4" data-testid={`chance-${c.id}`}>
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: "var(--accent-red)" }}>{chanceTypText(c.typ)}</div>
                <div className="text-sm font-semibold mt-0.5">{c.title || c.label}</div>
                <div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
                  {c.label} · {c.km_label} · {c.mileage_km?.toLocaleString("de-DE")} km · EZ {c.first_registration}{c.power_kw ? ` · ${c.power_kw} kW` : ""}{c.gearbox ? ` · ${c.gearbox}` : ""} · {c.postal_code} {c.city}
                </div>
              </div>
              <div className="text-right">
                <div className="text-lg font-semibold">{eur(c.price)}</div>
                {c.referenz_eur != null && c.differenz_eur != null && (
                  <div className="text-[12px]" style={{ color: trendFarbe(c.differenz_eur) }}>{trendText(c.differenz_eur, c.differenz_pct)} zu {eur(c.referenz_eur)}</div>
                )}
                {c.typ === "stark_reduziert" && c.delta_eur != null && <div className="text-[12px]" style={{ color: trendFarbe(c.delta_eur) }}>{trendText(c.delta_eur, c.delta_pct)} gegenüber Vortag</div>}
              </div>
            </div>
            <div className="mt-1.5 text-[11px] flex flex-wrap gap-x-3" style={{ color: "var(--text-dim)" }}>
              <span>{c.text}</span>
              {c.rang ? <span>heute Platz {c.rang}{c.rang_vorher ? ` (gestern ${c.rang_vorher})` : ""}</span> : null}
              {c.first_price != null && c.first_price !== c.price ? <span>Erstbeobachtung {eur(c.first_price)}</span> : null}
              <span>bei mobile seit {datumKurz(c.mobile_created_at)}</span>
              <span>gefunden {datumZeit(c.created_at)}</span>
              {c.active_state && c.active_state !== "seen" ? <span style={{ color: "var(--st-amber)" }}>{zustandText(c.active_state)}</span> : null}
              {c.url && <a href={c.url} target="_blank" rel="noopener noreferrer" className="underline">Inserat öffnen</a>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
