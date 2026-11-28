import { useCallback, useEffect, useRef, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { ExternalLink, FileSignature, Mail, MapPin, Phone, TriangleAlert, X } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { startseite } from "@/lib/rollen";
import { sichererLink } from "@/components/ProgrammVergleiche";
import ContractDialog from "@/components/ContractDialog";

/**
 * Trefferliste des Dauerlaufs (Wunsch Ahmad 27.11.2026): das Windows-Programm liest unbeaufsichtigt mit und
 * schickt jedes passende Auto; hier stehen laufend nur die, die sich lohnen — ohne genannten Schaden, mit
 * leichtem Schaden oder „bitte prüfen“ (Beschreibung fehlt oder nicht sicher gelesen).
 *
 * Aussehen (Ahmad: „modern“): im Stil der App, je Auto EINE ruhige Zeile — Fahrzeug und Eckdaten, Bewertung,
 * Verkäufer, Preis — und nur die Knöpfe „Kaufvertrag“, „Anrufen“ und (wenn vorhanden) „Mail“. Die Aufteilung
 * richtet sich nach der Breite der LISTE (nicht des Fensters).
 *
 * Detailansicht rechts (Ahmad: „wie bei mobile rechts als Detailseite öffnen“): ein Klick auf die Zeile zeigt
 * alle Merkmale, die ganze Ausstattung, die Beschreibung und den Verkäufer.
 *
 * „Kaufvertrag“ öffnet den Vertrag SOFORT — vorausgefüllt mit allem, was das Programm gelesen hat; kein Link
 * einfügen, kein Umweg über den Vergleich. Was das Inserat nicht hergab (Kraftstoff, Getriebe, Modell …), sagt
 * die Seite dem Sucher, und er trägt es im Vertrag nach.
 *
 * Die Liste fragt alle zwei Sekunden nach (nur solange die Seite zu sehen ist). Ohne Freischaltung des
 * Programms gibt es die Seite nicht (404 → Startseite), und im App-Code steht kein Programmname.
 */
export const TAKT_MS = 2000;

const URTEIL = {
  gut: { text: "Kein Schaden genannt", farbe: "var(--st-gruen)" },
  leicht: { text: "Leichter Schaden", farbe: "var(--st-amber)" },
  pruefen: { text: "Bitte prüfen", farbe: "var(--st-blau)" },
  schwer: { text: "Schwerer Schaden", farbe: "var(--st-rot)" },
};

const LAGE = { guenstig: "günstig", mittel: "im Mittelfeld", teuer: "teuer" };
const LAGE_FARBE = { guenstig: "var(--tx-gruen)", mittel: "var(--text-secondary)", teuer: "var(--tx-amber)" };

function euro(n) {
  return `${Math.abs(Number(n)).toLocaleString("de-DE")} €`;
}

/** Ein Satz zur Preislage — null, wenn der Server keine Aussage hat. */
export function preisSatz(p) {
  if (!p || !p.mitte) return null;
  const abstand = p.unter_mitte_eur > 0 ? `${euro(p.unter_mitte_eur)} unter` : p.unter_mitte_eur < 0 ? `${euro(p.unter_mitte_eur)} über` : "genau auf";
  return `Preis ${LAGE[p.lage] || p.lage}: ${abstand} der Mitte von ${p.anzahl} Vergleichsautos (${euro(p.mitte)})`;
}

/** Warum „bitte prüfen“? */
export function pruefGrund(t) {
  if (t.urteil !== "pruefen") return null;
  const s = t.schaden || {};
  if (s.stufe === "ohne_text") return "Das Inserat hat keine Beschreibung.";
  if (!s.text_exakt) return "Die Beschreibung wurde nicht sicher gelesen – bitte im Inserat nachlesen.";
  return "Die Angaben zum Schaden sind unklar.";
}

/** „+49 (0)163 2364007“ → „tel:+491632364007“; null, wenn es keine wählbare Nummer ist. */
export function telLink(nummer) {
  const roh = String(nummer || "").replace("(0)", "");
  if (!/^\+?[\d ()/-]{6,40}$/.test(roh.trim())) return null;
  const ziffern = roh.replace(/[^\d+]/g, "");
  return ziffern.replace(/\D/g, "").length >= 6 ? `tel:${ziffern}` : null;
}

/** Nur eine schlichte Adresse wird zum Mail-Knopf. */
export function mailLink(adresse) {
  const a = String(adresse || "").trim();
  return /^[^@\s<>"']+@[^@\s<>"']+\.[A-Za-z]{2,}$/.test(a) ? `mailto:${a}` : null;
}

/** Eckdaten in einer Zeile: „EZ 10/2017 · 115.000 km · Diesel · 190 PS · Automatik“. */
export function eckdaten(f = {}) {
  const teile = [];
  if (f.ez_jahr) teile.push(`EZ ${f.ez_monat ? `${String(f.ez_monat).padStart(2, "0")}/` : ""}${f.ez_jahr}`);
  if (f.kilometer != null) teile.push(`${Number(f.kilometer).toLocaleString("de-DE")} km`);
  if (f.kraftstoff) teile.push(f.kraftstoff);
  if (f.ps) teile.push(`${f.ps} PS`);
  if (f.getriebe) teile.push(f.getriebe);
  return teile.join(" · ");
}

/** „vor 12 s“, „vor 4 min“, sonst die Uhrzeit. */
export function seit(iso, jetzt = Date.now()) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const s = Math.max(0, Math.round((jetzt - d.getTime()) / 1000));
  if (s < 60) return `vor ${s} s`;
  if (s < 3600) return `vor ${Math.floor(s / 60)} min`;
  return d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}

/** „Kraftstoff und Getriebe“ / „Modell, Kraftstoff und Getriebe“. */
export function fehltText(fehlt) {
  const l = Array.isArray(fehlt) ? fehlt.filter(Boolean) : [];
  if (l.length === 0) return "";
  return l.length === 1 ? l[0] : `${l.slice(0, -1).join(", ")} und ${l[l.length - 1]}`;
}

function teile(t) {
  const f = t.fahrzeug || {};
  const l = t.liste || {};
  return {
    f, l,
    u: URTEIL[t.urteil] || URTEIL.pruefen,
    inserat: sichererLink(f.inserat_url),
    preis: preisSatz(t.preislage),
    grund: pruefGrund(t),
    gruende: Array.isArray(t.schaden?.gruende) ? t.schaden.gruende : [],
    nummern: (Array.isArray(l.telefone) ? l.telefone : []).filter((n) => telLink(n)),
    mail: mailLink(l.email),
    titel: f.titel || [f.marke, f.modell].filter(Boolean).join(" ") || "Fahrzeug",
    modell: [f.marke, f.modell].filter(Boolean).join(" "),
    herkunft: [l.privat === true ? "Privat" : l.privat === false ? "Händler" : null, l.portal || f.quelle].filter(Boolean).join(" · "),
    ort: [l.ort, l.entfernung_km != null ? `${l.entfernung_km} km entfernt` : null].filter(Boolean).join(" · "),
    fehlt: fehltText(t.fehlt),
  };
}

function Knoepfe({ t, x, onVertrag, laedt }) {
  const halt = (e) => e.stopPropagation();
  return (
    <>
      <button type="button" onClick={(e) => { halt(e); onVertrag(t); }} disabled={laedt}
              data-testid={`treffer-vertrag-${t.id}`} className="apple-btn apple-btn-primary min-h-[40px]">
        <FileSignature size={15} aria-hidden="true" /> {laedt ? "Öffnet …" : "Kaufvertrag"}
      </button>
      {x.nummern.length > 0 && (
        <a href={telLink(x.nummern[0])} onClick={halt} data-testid={`treffer-anrufen-${t.id}`} aria-label={`${x.nummern[0]} anrufen`}
           className="apple-btn apple-btn-secondary min-h-[40px]">
          <Phone size={15} aria-hidden="true" /> Anrufen
        </a>
      )}
      {x.mail && (
        <a href={x.mail} onClick={halt} data-testid={`treffer-mail-${t.id}`} className="apple-btn apple-btn-secondary min-h-[40px]">
          <Mail size={15} aria-hidden="true" /> Mail
        </a>
      )}
      {/* Wunsch Ahmad 28.11.2026: der Link zum Inserat steht IMMER dabei (Zeile und Detailansicht) */}
      {x.inserat && (
        <a href={x.inserat} target="_blank" rel="noopener noreferrer" onClick={halt} data-testid={`treffer-inserat-${t.id}`}
           className="apple-btn apple-btn-ghost min-h-[40px]">
          <ExternalLink size={15} aria-hidden="true" /> Inserat
        </a>
      )}
    </>
  );
}

function Zeile({ t, neu, aktiv, onDetails, onVertrag, laedt }) {
  const x = teile(t);
  const { f, l, u } = x;
  const p = t.preislage;
  return (
    <li data-testid={`treffer-${t.id}`} className={`tz${aktiv ? " tz-aktiv" : ""}`} style={{ opacity: t.erledigt ? 0.55 : 1 }}
        onClick={() => onDetails(t)}>
      <div className="tz-auto min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          {neu && <span className="tz-neu">Neu</span>}
          <h2 className="tz-titel min-w-0">
            <button type="button" className="tz-titel-knopf truncate" title={x.titel} aria-expanded={aktiv}
                    data-testid={`treffer-details-${t.id}`} onClick={(e) => { e.stopPropagation(); onDetails(t); }}>
              {x.titel}
            </button>
          </h2>
        </div>
        <div className="tz-daten truncate" data-testid={`treffer-daten-${t.id}`}>
          {x.modell && x.modell !== x.titel ? <span style={{ color: "var(--text-primary)" }}>{x.modell} · </span> : null}{eckdaten(f) || l.daten}
        </div>
        <div className="tz-leise truncate">
          {x.herkunft}{x.herkunft ? " · " : ""}<time dateTime={t.erstellt_am} title={l.zeit || ""}>{seit(t.erstellt_am)}</time>
        </div>
      </div>

      <div className="tz-preis">
        <div className="tz-preis-wert">{f.preis != null ? `${Number(f.preis).toLocaleString("de-DE")} €` : "–"}</div>
        {p?.lage && <div className="tz-leise" style={{ color: LAGE_FARBE[p.lage] }} title={x.preis || ""}>{LAGE[p.lage]}</div>}
      </div>

      <div className="tz-urteil min-w-0">
        <div className="flex items-center gap-2">
          <span className="tz-punkt" style={{ background: u.farbe }} aria-hidden="true" />
          <span className="font-semibold text-[14px]" style={{ color: "var(--text-strong)" }} data-testid={`treffer-urteil-${t.id}`}>{u.text}</span>
        </div>
        {x.gruende.length > 0 && (
          <ul className="tz-leise mt-0.5 space-y-0.5" data-testid={`treffer-gruende-${t.id}`}>
            {x.gruende.slice(0, 2).map((g, i) => <li key={i} className="line-clamp-2">{g}</li>)}
          </ul>
        )}
        {x.grund && <div className="tz-leise mt-0.5">{x.grund}</div>}
        {x.preis && <div className="tz-leise mt-0.5" data-testid={`treffer-preis-${t.id}`}>{x.preis}</div>}
        {x.fehlt && <div className="tz-leise mt-0.5" style={{ color: "var(--tx-amber)" }} data-testid={`treffer-fehlt-${t.id}`}>Im Inserat fehlt: {x.fehlt}</div>}
      </div>

      <div className="tz-verkaeufer min-w-0">
        {(l.anbieter || x.nummern.length > 0) && (
          <div className="truncate text-[12.5px]" style={{ color: "var(--text-primary)" }}>{l.anbieter || x.nummern[0]}</div>
        )}
        {l.anbieter && x.nummern.length > 0 && <div className="tz-leise truncate">{x.nummern.join(" · ")}</div>}
        {x.ort && <div className="tz-leise truncate flex items-center gap-1"><MapPin size={12} aria-hidden="true" className="shrink-0" /><span className="truncate">{x.ort}</span></div>}
      </div>

      <div className="tz-knoepfe"><Knoepfe t={t} x={x} onVertrag={onVertrag} laedt={laedt} /></div>
    </li>
  );
}

/** Detailansicht rechts: alles, was das Programm zu diesem Auto gelesen hat. */
function Details({ t, onZu, onVertrag, laedt }) {
  const x = teile(t);
  const { f, l, u } = x;
  const merkmale = Array.isArray(t.merkmale) ? t.merkmale : [];
  const ausstattung = Array.isArray(t.ausstattung) ? t.ausstattung : [];
  return (
    <aside className="td tactical-card" data-testid="treffer-detail" aria-label={`Details zu ${x.titel}`}>
      <div className="td-kopf">
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold leading-snug" style={{ color: "var(--text-strong)" }}>{x.titel}</h2>
          <div className="tz-leise mt-0.5">{x.herkunft}{l.zeit ? ` · ${l.zeit}` : ""}</div>
        </div>
        <button type="button" onClick={onZu} aria-label="Details schließen" data-testid="treffer-detail-zu"
                className="apple-btn apple-btn-ghost min-h-[40px] min-w-[40px] !px-2"><X size={18} aria-hidden="true" /></button>
      </div>

      <div className="td-inhalt">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-[18px] font-bold" style={{ color: "var(--text-strong)", fontVariantNumeric: "tabular-nums" }}>
            {f.preis != null ? `${Number(f.preis).toLocaleString("de-DE")} €` : "Preis nicht gelesen"}
          </span>
          {x.preis && <span className="tz-leise">{x.preis}</span>}
        </div>

        <div className="mt-3 flex items-center gap-2">
          <span className="tz-punkt" style={{ background: u.farbe }} aria-hidden="true" />
          <span className="font-semibold text-[14px]" style={{ color: "var(--text-strong)" }}>{u.text}</span>
        </div>
        {x.gruende.length > 0 && <ul className="tz-leise mt-1 space-y-1">{x.gruende.map((g, i) => <li key={i}>{g}</li>)}</ul>}
        {x.grund && <div className="tz-leise mt-1">{x.grund}</div>}

        {x.fehlt && (
          <div className="td-hinweis mt-3" data-testid="treffer-detail-fehlt">
            <TriangleAlert size={15} aria-hidden="true" className="shrink-0 mt-0.5" />
            <span>Im Inserat fehlt: <strong>{x.fehlt}</strong>. Bitte im Kaufvertrag nachtragen.</span>
          </div>
        )}

        <div className="flex flex-wrap gap-2 mt-4"><Knoepfe t={t} x={x} onVertrag={onVertrag} laedt={laedt} /></div>

        <h3 className="td-titel">Daten</h3>
        {merkmale.length > 0 ? (
          <dl className="td-daten" data-testid="treffer-detail-merkmale">
            {merkmale.map((m) => (
              <div key={m.name} className="td-zeile"><dt>{m.name}</dt><dd>{m.wert}</dd></div>
            ))}
          </dl>
        ) : (
          <p className="tz-daten">{[x.modell, eckdaten(f)].filter(Boolean).join(" · ") || "Keine Daten gelesen."}</p>
        )}

        <h3 className="td-titel">Ausstattung{ausstattung.length > 0 ? ` (${ausstattung.length})` : ""}</h3>
        {ausstattung.length > 0 ? (
          <ul className="td-ausstattung" data-testid="treffer-detail-ausstattung">{ausstattung.map((a) => <li key={a}>{a}</li>)}</ul>
        ) : (
          <p className="tz-leise">{l.ausstattung || "Keine Angaben zur Ausstattung."}</p>
        )}

        <h3 className="td-titel">Beschreibung</h3>
        {t.beschreibung
          ? <p className="td-text" data-testid="treffer-detail-text">{t.beschreibung}</p>
          : <p className="tz-leise">Das Inserat hat keine Beschreibung.</p>}

        <h3 className="td-titel">Verkäufer</h3>
        <div className="tz-daten">
          {l.anbieter && <div style={{ color: "var(--text-primary)" }}>{l.anbieter}</div>}
          {x.nummern.map((n) => <div key={n}>{n}</div>)}
          {x.mail && <div>{l.email}</div>}
          {x.ort && <div>{x.ort}</div>}
          {!l.anbieter && x.nummern.length === 0 && !x.ort && <div className="tz-leise">Keine Angaben gelesen.</div>}
        </div>
      </div>
    </aside>
  );
}

const STIL = `
  .treffer-liste { container-type: inline-size; }
  .tz {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas: "auto preis" "urteil urteil" "verkaeufer verkaeufer" "knoepfe knoepfe";
    gap: 10px 16px;
    padding: 16px;
    border-bottom: 1px solid var(--divider);
    cursor: pointer;
  }
  .tz:last-child { border-bottom: 0; }
  .tz:hover { background: var(--wa-03); }
  .tz-aktiv, .tz-aktiv:hover { background: color-mix(in srgb, var(--knopf-primaer, var(--accent-blue)) 8%, transparent); }
  .tz-auto { grid-area: auto; }
  .tz-preis { grid-area: preis; text-align: right; white-space: nowrap; }
  .tz-urteil { grid-area: urteil; }
  .tz-verkaeufer { grid-area: verkaeufer; }
  .tz-knoepfe { grid-area: knoepfe; display: flex; flex-wrap: wrap; gap: 8px; }
  .tz-knoepfe > .apple-btn { flex: 1 1 8.5rem; }
  .tz-titel { font-size: 13.5px; font-weight: 600; line-height: 1.35; color: var(--text-strong); display: flex; }
  .tz-titel-knopf { font: inherit; color: inherit; text-align: left; background: none; border: 0; padding: 0; cursor: pointer; max-width: 100%; }
  .tz-titel-knopf:hover { text-decoration: underline; }
  .tz-daten { font-size: 12.5px; line-height: 1.5; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
  .tz-leise { font-size: 12px; line-height: 1.45; color: var(--text-muted); }
  .tz-preis-wert { font-size: 15px; font-weight: 700; line-height: 1.35; color: var(--text-strong); font-variant-numeric: tabular-nums; }
  .tz-punkt { width: 8px; height: 8px; border-radius: 999px; flex: none; }
  .tz-neu { font-size: 12px; font-weight: 700; line-height: 18px; padding: 0 6px; border-radius: 6px; flex: none;
            color: var(--tx-blau); background: color-mix(in srgb, var(--st-blau) 14%, transparent); }
  @container (min-width: 620px) {
    .tz {
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
      grid-template-areas: "auto urteil preis" "verkaeufer knoepfe knoepfe";
      align-items: start;
    }
    .tz-knoepfe { justify-content: flex-end; align-self: end; }
    .tz-knoepfe > .apple-btn { flex: 0 0 auto; }
    .tz-verkaeufer { align-self: end; }
  }
  @container (min-width: 1040px) {
    .tz {
      grid-template-columns: minmax(0, 1.5fr) minmax(0, 1.15fr) minmax(0, 0.9fr) 7.5rem 17rem;
      grid-template-areas: "auto urteil verkaeufer preis knoepfe";
      align-items: center;
      padding: 14px 16px;
    }
    .tz-knoepfe { align-self: center; flex-wrap: nowrap; }
    .tz-verkaeufer { align-self: center; }
  }

  /* Detailansicht: am PC rechts neben der Liste, sonst als Blatt über der Seite */
  .treffer-raster { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; align-items: start; }
  .td { display: flex; flex-direction: column; overflow: hidden; }
  .td-kopf { display: flex; align-items: flex-start; gap: 8px; padding: 16px 12px 12px 20px; border-bottom: 1px solid var(--divider); }
  .td-inhalt { padding: 16px 20px 24px; overflow-y: auto; }
  .td-titel { font-size: 12px; font-weight: 600; color: var(--text-muted); margin: 18px 0 6px; }
  .td-zeile { display: grid; grid-template-columns: minmax(0, 11rem) minmax(0, 1fr); gap: 12px; padding: 4px 0; border-bottom: 1px solid var(--divider); font-size: 12.5px; }
  .td-zeile:last-child { border-bottom: 0; }
  .td-zeile dt { color: var(--text-muted); }
  .td-zeile dd { color: var(--text-strong); font-weight: 500; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
  .td-ausstattung { display: grid; grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr)); gap: 2px 14px; font-size: 12.5px; color: var(--text-primary); }
  .td-text { font-size: 12.5px; line-height: 1.5; color: var(--text-primary); white-space: pre-line; overflow-wrap: anywhere; max-width: 70ch; }
  .td-hinweis { display: flex; gap: 8px; padding: 10px 12px; border-radius: 10px; font-size: 12px; line-height: 1.45;
                color: var(--tx-amber); background: color-mix(in srgb, var(--st-amber) 12%, transparent); }
  .td { position: fixed; inset: 0; z-index: 40; border-radius: 0; }
  @media (min-width: 1200px) {
    .treffer-raster.mit-detail { grid-template-columns: minmax(0, 1fr) minmax(24rem, 30rem); }
    .td { position: sticky; inset: auto; top: 16px; z-index: auto; border-radius: 14px; max-height: calc(100vh - 32px); max-height: calc(100dvh - 32px); }
  }
`;

export default function Treffer() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [daten, setDaten] = useState(null);
  const [alle, setAlle] = useState(false);
  const [fehler, setFehler] = useState("");
  const [gesperrt, setGesperrt] = useState(false);
  const [offen, setOffen] = useState(null);        // id des Treffers in der Detailansicht
  const [laedt, setLaedt] = useState(null);        // id, dessen Vertrag gerade vorbereitet wird
  const [vertrag, setVertrag] = useState(null);    // { vehicle, vehicleId }
  const bekannt = useRef(null);                    // Treffer, die beim ersten Laden schon da waren (kein „Neu“)
  const [neue, setNeue] = useState(() => new Set());

  const laden = useCallback(async () => {
    try {
      const { data } = await api.get("/dauerlauf/treffer", { params: { alle } });
      const liste = Array.isArray(data?.treffer) ? data.treffer : [];
      if (bekannt.current === null) bekannt.current = new Set(liste.map((t) => t.id));
      else {
        const frisch = liste.filter((t) => !bekannt.current.has(t.id)).map((t) => t.id);
        if (frisch.length) {
          frisch.forEach((id) => bekannt.current.add(id));
          setNeue((alt) => new Set([...alt, ...frisch]));
        }
      }
      setDaten({ ...data, treffer: liste });
      setFehler("");
    } catch (e) {
      if (e?.response?.status === 404) setGesperrt(true);
      else setFehler(errMsg(e, "Die Treffer konnten nicht geladen werden"));
    }
  }, [alle]);

  useEffect(() => {
    let aktiv = true;
    // im Takt nur, solange die Seite zu sehen ist — aber immer einmal sofort (auch im Hintergrund-Tab geöffnet)
    // und gleich wieder, sobald sie nach vorne kommt
    const tick = () => { if (aktiv && document.visibilityState !== "hidden") laden(); };
    const sichtbar = () => tick();
    laden();
    const uhr = setInterval(tick, TAKT_MS);
    document.addEventListener("visibilitychange", sichtbar);
    return () => { aktiv = false; clearInterval(uhr); document.removeEventListener("visibilitychange", sichtbar); };
  }, [laden]);

  useEffect(() => {
    if (!offen) return undefined;
    const taste = (e) => { if (e.key === "Escape" && !vertrag) setOffen(null); };
    document.addEventListener("keydown", taste);
    return () => document.removeEventListener("keydown", taste);
  }, [offen, vertrag]);

  // „Kaufvertrag“: das Fahrzeug aus dem Treffer anlegen lassen und den Vertrag sofort öffnen
  const vertragOeffnen = async (t) => {
    if (laedt) return;
    setLaedt(t.id);
    try {
      const { data } = await api.post(`/dauerlauf/treffer/${encodeURIComponent(t.id)}/vertrag`);
      const fehlt = fehltText(data?.fehlt);
      if (fehlt) toast.warning(`Im Inserat fehlt: ${fehlt}. Bitte im Vertrag nachtragen.`, { duration: 12000 });
      if (data?.kollege?.name) toast.info(`Dieses Auto bearbeitet auch ${data.kollege.name}.`);
      setVertrag({ vehicle: data.vehicle, vehicleId: data.vehicle_id });
    } catch (e) {
      toast.error(errMsg(e, "Der Vertrag konnte nicht vorbereitet werden"));
    } finally {
      setLaedt(null);
    }
  };

  if (gesperrt) return <Navigate to={startseite(user)} replace />;
  const liste = daten?.treffer || [];
  const detail = offen ? liste.find((t) => t.id === offen) : null;
  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-[1500px] mx-auto" data-testid="treffer-seite">
      <style>{STIL}</style>
      <div className="flex flex-wrap items-end gap-3 mb-4">
        <div className="flex-1 min-w-[14rem]">
          <div className="overline">Dauerlauf</div>
          <h1 className="font-display font-black text-2xl tracking-tight">Treffer</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-dim)" }}>
            Autos, die das Programm selbst gelesen hat – nur was sich lohnt. Neue erscheinen von selbst;
            nach {daten?.stunden || 48} Stunden verschwinden sie.
          </p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm min-h-[40px]" style={{ color: "var(--text-secondary)" }}>
          <input type="checkbox" checked={alle} onChange={(e) => setAlle(e.target.checked)} data-testid="treffer-alle" className="w-4 h-4" />
          Auch aussortierte zeigen
        </label>
      </div>
      {fehler && <div className="text-sm mb-3" style={{ color: "var(--tx-rot)" }} role="alert">{fehler}</div>}
      {daten && liste.length === 0 && (
        <div className="tactical-card p-5 text-sm" style={{ color: "var(--text-dim)" }} data-testid="treffer-leer">
          {daten.gesamt > 0
            ? "Im Moment ist nichts Passendes dabei – alles Gelesene war aussortiert."
            : "Noch keine Treffer. Den Dauerlauf schaltest du einmal im Windows-Programm ein: in der Leiste „Mehr“ → „Dauerlauf starten …“. Danach läuft er von selbst, sobald der PC nicht benutzt wird, und jedes passende Auto steht hier."}
        </div>
      )}
      {liste.length > 0 && (
        <div className={`treffer-raster${detail ? " mit-detail" : ""}`}>
          <ul className="tactical-card treffer-liste overflow-hidden" data-testid="treffer-liste">
            {liste.map((t) => (
              <Zeile key={t.id} t={t} neu={neue.has(t.id)} aktiv={t.id === offen} laedt={laedt === t.id}
                     onDetails={(x) => setOffen((alt) => (alt === x.id ? null : x.id))} onVertrag={vertragOeffnen} />
            ))}
          </ul>
          {detail && <Details t={detail} onZu={() => setOffen(null)} onVertrag={vertragOeffnen} laedt={laedt === detail.id} />}
        </div>
      )}
      {vertrag && (
        <ContractDialog open onClose={() => setVertrag(null)} vehicle={vertrag.vehicle} vehicleId={vertrag.vehicleId}
                        onCreated={() => { setVertrag(null); toast.success("Kaufvertrag gespeichert."); nav("/app/vertraege"); }} />
      )}
    </div>
  );
}
