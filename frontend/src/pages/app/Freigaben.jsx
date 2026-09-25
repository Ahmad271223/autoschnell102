import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { freigabeZaehlerAktualisieren } from "@/lib/freigaben";
import { preisAusText, preisText } from "@/lib/preis";
import { useUngespeichert } from "@/lib/ungespeichert";
import { toast } from "sonner";
import KiBewertungKarte from "@/components/KiBewertungKarte";
import {
  AlertTriangle, Check, ChevronDown, ChevronUp, ClipboardCheck, Clock, Euro,
  Phone, RotateCcw, Undo2,
} from "lucide-react";

/**
 * Freigaben (Wunsch Ahmad, 12.09.2026): "was wenn mehrere Fahrer gerade Autos
 * abholen und vom Chef die Nachverhandlung brauchen — vielleicht eine eigene
 * Unterseite, wo er alle empfangen und nach Belieben bearbeiten kann".
 *
 * Vorher lag der Kasten nur auf der Termine-Seite: kein Zaehler, neueste
 * zuerst, Liste bei 50 abgeschnitten, und gaben Chef und Sucher gleichzeitig
 * frei, gewann stillschweigend der Letzte.
 *
 * Hier:
 *   - alle wartenden Protokolle, das am LAENGSTEN wartende oben
 *   - je Protokoll ein eigener Entwurf (Preis, Vermerk), der das Nachladen
 *     uebersteht und vor dem Verlassen der Seite geschuetzt ist
 *   - Vertrag -> vor Ort fuer jede Zeile (backend/protokoll_vergleich.py)
 *   - der Server lehnt eine Freigabe ab, wenn inzwischen jemand anders
 *     freigegeben hat (Stand-Pruefung) — und sagt, wer und mit welchem Preis
 */
const eur = preisText;
const NICHT_IM_VERTRAG = "nicht im Vertrag";

function wartetSeit(iso) {
  if (!iso) return "";
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 1) return "gerade eben";
  if (min < 60) return `seit ${min} Min.`;
  const std = Math.floor(min / 60);
  return `seit ${std} Std. ${min % 60} Min.`;
}

function VergleichZeile({ z }) {
  const rot = z.abweichend;
  const ohneVertrag = !z.vertrag_text;
  // "nicht im Vertrag" steht schon links als Platzhalter — nicht doppelt.
  const hinweis = ohneVertrag && z.hinweis === NICHT_IM_VERTRAG ? "" : z.hinweis;
  // Rollenprüfung 22.09.2026 (RP-465): am Handy Bezeichnung über dem Wert
  // (eine Spalte), der Wert vor Ort in eigener Zeile und lange Werte (FIN)
  // umbrechen — vorher liefen zwei FIN nebeneinander aus der Karte.
  return (
    <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,9rem)_1fr] gap-x-3 gap-y-0.5 py-1.5 border-b last:border-b-0"
         style={{ borderColor: "var(--wa-06)" }}
         data-testid={`vergleich-${z.schluessel}`}>
      <div className="text-[11px] text-zinc-500 pt-0.5">{z.label}</div>
      <div className="text-[13px] min-w-0 [overflow-wrap:anywhere]">
        <span className={rot && !ohneVertrag ? "text-zinc-500 line-through" : "text-zinc-300"}
              data-testid={`vergleich-${z.schluessel}-vertrag`}>
          {z.vertrag_text || <i className="not-italic text-zinc-600">{NICHT_IM_VERTRAG}</i>}
        </span>
        {(rot || (z.vor_ort_text && z.vor_ort_text !== z.vertrag_text)) && (
          <span className="block sm:inline">
            <span className="mr-1.5 sm:mx-1.5 text-zinc-500">→</span>
            <b style={{ color: rot ? "var(--st-amber)" : "var(--text-strong)" }}
               data-testid={`vergleich-${z.schluessel}-vor-ort`}>
              {z.vor_ort_text || "—"}
            </b>
          </span>
        )}
        {hinweis && (
          <span className="ml-2 text-[11px]" style={{ color: rot ? "var(--st-amber)" : "var(--text-dim)" }}>
            {hinweis}
          </span>
        )}
      </div>
    </div>
  );
}

function Vergleich({ eintrag }) {
  const [alle, setAlle] = useState(false);
  const zeilen = Array.isArray(eintrag.vergleich) ? eintrag.vergleich : null;

  // Waehrend eines Rollouts kann noch ein aelteres Backend antworten: dann
  // gibt es nur die bisherige Abweichungsliste.
  if (!zeilen) {
    const alt = eintrag.abweichungen || [];
    if (!alt.length) return null;
    return (
      <ul className="text-[12px] space-y-0.5">
        {alt.map((a, i) => (
          <li key={i}><span className="text-zinc-400">{a.feld}:</span> <b>{a.status}</b>{a.wert ? ` → ${a.wert}` : ""}</li>
        ))}
      </ul>
    );
  }
  // Auffaellig ist, was abweicht oder einen echten Hinweis traegt — eine
  // leere Vertragsangabe allein (z. B. keine FIN im Vertrag) nicht.
  const wichtig = zeilen.filter((z) => z.abweichend || (z.hinweis && z.hinweis !== NICHT_IM_VERTRAG));
  const ohneVertragsdaten = zeilen.every((z) => !z.vertrag_text);
  const sichtbar = alle ? zeilen : wichtig;
  return (
    <div>
      {wichtig.length === 0 && !alle && (
        ohneVertragsdaten ? (
          <div className="text-[12px] text-zinc-500">Keine Fahrzeugdaten im Vertrag zum Abgleichen.</div>
        ) : (
          <div className="text-[12px] inline-flex items-center gap-1.5" style={{ color: "var(--st-gruen)" }}>
            <Check size={13} /> Alle Fahrzeugdaten stimmen mit dem Vertrag überein
          </div>
        )
      )}
      {sichtbar.length > 0 && (
        <div className="rounded-lg px-3 py-1"
             style={{ background: wichtig.some((z) => z.abweichend) ? "#ff9f0a10" : "var(--wa-03)",
                      border: `1px solid ${wichtig.some((z) => z.abweichend) ? "#ff9f0a44" : "var(--wa-06)"}` }}>
          <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,9rem)_1fr] gap-x-3 pt-1.5 pb-1 text-[10px] uppercase tracking-wider text-zinc-500">
            <span className="hidden sm:block" />
            <span>laut Vertrag → vor Ort</span>
          </div>
          {sichtbar.map((z) => <VergleichZeile key={z.schluessel} z={z} />)}
        </div>
      )}
      <button type="button" onClick={() => setAlle((a) => !a)}
              className="mt-0.5 text-[11px] text-zinc-500 hover:text-white inline-flex items-center gap-1 min-h-[40px] px-1 -ml-1">
        {alle ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {alle ? "nur Auffälligkeiten" : `alle ${zeilen.length} Zeilen zeigen`}
      </button>
    </div>
  );
}

/** Rollenprüfung 22.09.2026 (RP-453): Zusatz hinter dem Fahrer-Vorschlag. */
export function vorschlagHinweis(e, freigegeben) {
  if (e?.preis_vorschlag_verworfen) return " — verworfen (auf Vertragspreis zurückgesetzt)";
  if (e?.neuer_preis == null && !freigegeben) return " — gilt, wenn du ohne eigenen Preis freigibst";
  // Rollenprüfung 22.09.2026 (RP-080/179): Der geltende Preis stammt aus einem
  // früheren Vorschlag des Fahrers, jetzt schlägt er einen anderen vor — der
  // neue ersetzt ihn bei der Freigabe ohne eigenen Preis (wie im Backend).
  if (e?.preis_quelle === "fahrer" && e?.neuer_preis != null && e?.preis_vorschlag_fahrer != null
      && Math.abs(Number(e.preis_vorschlag_fahrer) - Number(e.neuer_preis)) > 0.004) {
    return " — ersetzt den bisherigen Fahrer-Preis, wenn du ohne eigenen Preis freigibst";
  }
  return "";
}

/**
 * Rollenprüfung 22.09.2026 (RP-146): Was geht als Vermerk mit? Das Feld zeigt
 * den gespeicherten Vermerk, solange nichts getippt wurde. undefined = nicht
 * senden (Vermerk bleibt), "" = leeren (nur wenn einer gespeichert ist), sonst
 * der getippte Text. Bei „Zurück an den Fahrer“ ist der Text die Rückfrage —
 * dort zählt nur Getipptes.
 */
export function notizFuerSenden(eigener, e, { zurueck = false } = {}) {
  if (!eigener || !Object.prototype.hasOwnProperty.call(eigener, "notiz")) return undefined;
  const text = String(eigener.notiz ?? "").trim();
  if (text) return text;
  return !zurueck && e?.preis_notiz ? "" : undefined;
}

/**
 * Ungespeicherte Eingaben? Ein getippter Preis immer; ein Vermerk nur, wenn er
 * vom gespeicherten abweicht (RP-146: das Feld zeigt den gespeicherten
 * Vermerk — auch ein geleerter zählt als Änderung).
 */
export function entwurfUngespeichert(entwurf, liste) {
  const gespeichert = new Map((liste || []).map((e) => [e.protocol_id, e.preis_notiz || ""]));
  return Object.entries(entwurf || {}).some(([id, x]) => {
    if (String(x?.preis ?? "").trim()) return true;
    if (!x || !Object.prototype.hasOwnProperty.call(x, "notiz")) return false;
    return String(x.notiz ?? "").trim() !== String(gespeichert.get(id) ?? "").trim();
  });
}

/*
 * Rollenprüfung 22.09.2026 (RP-499): Ein getippter Verhandlungspreis sollte
 * „Zurück an den Fahrer“ überstehen. laden() verwarf aber alle Entwürfe, deren
 * Protokoll nicht mehr in der Liste stand — und nach dem Zurückschicken ist das
 * Protokoll ein Entwurf des Fahrers, also nicht in der Liste. Beim erneuten
 * Einreichen war das Feld leer, und die Freigabe galt mit Vertrags- bzw.
 * Fahrerpreis. Jetzt wird der Preis beim Zurückschicken gemerkt (im Browser,
 * je Protokoll, höchstens 7 Tage) und wieder eingesetzt, sobald das Protokoll
 * erneut wartet.
 */
const PREIS_MERKER_SCHLUESSEL = "freigaben_preis_zurueck";
const PREIS_MERKER_TAGE = 7;

function preisMerkerLesen() {
  try {
    const roh = JSON.parse(window.localStorage.getItem(PREIS_MERKER_SCHLUESSEL) || "{}");
    return roh && typeof roh === "object" ? roh : {};
  } catch { return {}; }
}

function preisMerkerSchreiben(merker) {
  try {
    if (Object.keys(merker).length) {
      window.localStorage.setItem(PREIS_MERKER_SCHLUESSEL, JSON.stringify(merker));
    } else {
      window.localStorage.removeItem(PREIS_MERKER_SCHLUESSEL);
    }
  } catch { /* privates Fenster / gesperrter Speicher: dann ohne Merker */ }
}

/**
 * Entwürfe mit der neu geladenen Liste abgleichen (rein, ohne Seiteneffekte).
 * ids: Protokolle, die jetzt warten. gemerkt: {protocol_id: {preis, am}}.
 * Liefert { entwurf, gemerkt } — Entwürfe verschwundener Protokolle fallen weg
 * (keine dauerhafte „ungespeichert“-Warnung), gemerkte Preise kommen zurück,
 * sobald ihr Protokoll wieder wartet; Merker älter als 7 Tage verfallen.
 */
export function entwuerfeAbgleichen(entwurf, ids, gemerkt, jetztMs = Date.now()) {
  const neu = {};
  for (const [id, wert] of Object.entries(entwurf || {})) {
    if (ids.has(id)) neu[id] = wert;
  }
  const rest = {};
  const grenze = jetztMs - PREIS_MERKER_TAGE * 24 * 3600 * 1000;
  for (const [id, m] of Object.entries(gemerkt || {})) {
    if (!m || typeof m !== "object" || !(Number(m.am) >= grenze)) continue;
    if (ids.has(id)) {
      const vorhanden = String(neu[id]?.preis ?? "").trim();
      if (!vorhanden && String(m.preis ?? "").trim()) neu[id] = { ...neu[id], preis: String(m.preis) };
      continue;   // eingesetzt (oder schon neu getippt) — Merker erledigt
    }
    rest[id] = m;
  }
  return { entwurf: neu, gemerkt: rest };
}

function Karte({ eintrag: e, entwurf, setEntwurf, busy, senden }) {
  const freigegeben = e.status === "freigegeben";
  const meinEntwurf = entwurf[e.protocol_id] || {};
  const setzen = (feld, wert) => setEntwurf((s) => ({ ...s, [e.protocol_id]: { ...s[e.protocol_id], [feld]: wert } }));
  const neueSchaeden = e.neue_schaeden || [];
  // "Auf Vertragspreis zuruecksetzen" kennt erst das neue Backend — ein
  // aelteres wuerde den Klick als normale Freigabe mit altem Preis verstehen.
  const kannZuruecksetzen = Array.isArray(e.vergleich);
  return (
    <div className="apple-surface-gloss p-4 lg:p-5" data-testid={`freigabe-${e.protocol_id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-display font-bold text-lg tracking-tight truncate">{e.fahrzeug || "Fahrzeug"}</div>
          <div className="text-[12px] text-zinc-500 truncate">{e.abholung} · {e.abholort}</div>
          <div className="text-[12px] text-zinc-500 truncate">
            Fahrer: {e.fahrer || "—"} · Verkäufer: {e.verkaeufer || "—"}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          {freigegeben ? (
            <span className="text-[11px] rounded-full px-2 py-0.5" style={{ background: "#34c75922", color: "var(--st-gruen)" }}>
              freigegeben — wartet auf Unterschriften
            </span>
          ) : (
            <span className="text-[11px] rounded-full px-2 py-0.5 inline-flex items-center gap-1"
                  style={{ background: "#ff9f0a22", color: "var(--st-amber)" }}>
              <Clock size={11} /> wartet {wartetSeit(e.erstmals_abgeschickt_am || e.abgeschickt_am)}
            </span>
          )}
          {e.freigegeben_von_name && freigegeben && (
            <span className="text-[11px] text-zinc-500">von {e.freigegeben_von_name}</span>
          )}
        </div>
      </div>

      {e.ladefehler && (
        <div className="mt-3 rounded-lg px-3 py-2 text-[12px]" style={{ background: "#ff3b3014", color: "var(--tx-rot)" }}>
          {e.ladefehler}
        </div>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {[["Kilometerstand bei Abholung", e.kilometerstand_text || e.kilometerstand || "—"],
          ["Bekannte Schäden bestätigt", e.schaeden_bestaetigt === true ? "ja" : e.schaeden_bestaetigt === false ? "nein" : "—"],
          ["Schlüssel", e.schluessel ? `${e.schluessel}${e.schluessel_vereinbart ? ` (vereinbart ${e.schluessel_vereinbart})` : ""}` : "—"],
        ].map(([k, v]) => (
          <div key={k} className="rounded-lg p-2" style={{ background: "var(--wa-03)" }}>
            <div className="text-[11px] text-zinc-500">{k}</div>
            <div className="text-sm">{v}</div>
          </div>
        ))}
      </div>

      {!e.ladefehler && <div className="mt-3"><Vergleich eintrag={e} /></div>}

      {neueSchaeden.length > 0 && (
        <div className="mt-3 rounded-lg p-2.5" style={{ background: "#ff3b3014", border: "1px solid #ff3b3044" }}>
          <div className="text-[12px] font-semibold mb-1 inline-flex items-center gap-1" style={{ color: "var(--st-rot)" }}>
            <AlertTriangle size={12} /> Neue Schäden vor Ort: {neueSchaeden.length}
          </div>
          <ul className="text-[12px] space-y-0.5">
            {neueSchaeden.map((s, i) => (
              <li key={i}>
                {(s.type_label || s.label || s.type || "Schaden")}{(s.zone || s.part_label) ? ` · ${s.zone || s.part_label}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      {e.bemerkungen && (
        <div className="mt-2 text-[12px] text-zinc-400">
          <span className="text-zinc-500">Bemerkung des Fahrers:</span> {e.bemerkungen}
        </div>
      )}

      {/* Wunsch Ahmad 25.09.2026: KI-Einschätzung der Abweichungen — nur
          beratend. "Preis übernehmen" füllt das Feld "Neuer Preis", mehr nicht;
          "Fahrer fragen" = bestehendes "Zurück an den Fahrer" mit der Frage. */}
      {!e.ladefehler && !freigegeben && (
        <KiBewertungKarte eintrag={e} busy={busy}
          onPreis={(p) => { setzen("preis", preisText(p)); toast.info("Preis ins Feld übernommen — Freigeben bestätigt ihn."); }} />
      )}
      {(e.rueckfrage_antworten || []).length > 0 && (
        // Stufe 3 KI (26.09.2026): was der Fahrer per Knopf geantwortet hat
        <div className="mt-2 text-[12px]" data-testid={`freigabe-antworten-${e.protocol_id}`}>
          <span className="text-zinc-500">Antworten des Fahrers:</span>{" "}
          {e.rueckfrage_antworten.map((a, i) => (
            <span key={i}>{i > 0 ? " · " : ""}{a.question} <b>{a.answer}</b></span>
          ))}
        </div>
      )}

      <div className="mt-3 rounded-lg p-3" style={{ background: "var(--wa-03)" }}>
        <div className="flex flex-wrap items-end gap-3">
          <div className="text-[11px]">
            <div className="text-zinc-500">Preis laut Vertrag</div>
            <div className="text-base font-semibold">{eur(e.preis_vertrag)}</div>
          </div>
          <div className="text-[11px] flex-1 min-w-[140px]">
            <label className="text-zinc-500 inline-flex items-center gap-1">
              <Euro size={11} /> Neuer Preis (nach Verhandlung)
            </label>
            <input type="text" inputMode="decimal"
                   data-testid={`freigabe-preis-${e.protocol_id}`}
                   placeholder={e.neuer_preis != null ? eur(e.neuer_preis) : "z. B. 15.000"}
                   value={meinEntwurf.preis ?? ""}
                   onChange={(ev) => setzen("preis", ev.target.value)}
                   className="mt-1 w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-sm outline-none focus:border-white/40"
                   style={{ borderColor: "var(--border-default)" }} />
          </div>
          <div className="text-[11px] flex-1 min-w-[160px]">
            <label className="text-zinc-500">Vermerk (erscheint im Protokoll)</label>
            {/* Rollenprüfung 22.09.2026 (RP-146): gespeicherter Vermerk steht im
                Feld und lässt sich leeren (vorher nie mehr löschbar). */}
            <input type="text" maxLength={200}
                   data-testid={`freigabe-notiz-${e.protocol_id}`}
                   value={"notiz" in meinEntwurf ? (meinEntwurf.notiz ?? "") : (e.preis_notiz || "")}
                   onChange={(ev) => setzen("notiz", ev.target.value)}
                   placeholder="z. B. Rost am Schweller"
                   className="mt-1 w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-sm outline-none focus:border-white/40"
                   style={{ borderColor: "var(--border-default)" }} />
          </div>
        </div>
        {/* Wunsch Ahmad 14.09.2026: Vorschlag und Sondervereinbarung des Fahrers vor Ort */}
        {e.preis_vorschlag_fahrer != null && (
          <div className="mt-2 text-[12px] text-amber-300" data-testid={`freigabe-vorschlag-${e.protocol_id}`}>
            Vorschlag des Fahrers vor Ort: {eur(e.preis_vorschlag_fahrer)}
            {/* Rollenprüfung 22.09.2026 (RP-453): nach „auf Vertragspreis
                zurücksetzen“ gilt der Vorschlag nicht mehr — auch nicht beim
                nächsten „Preis aktualisieren“ ohne eigenen Preis. */}
            {vorschlagHinweis(e, freigegeben)}
          </div>
        )}
        {e.sondervereinbarung && (
          <div className="mt-1 text-[12px] text-zinc-300" data-testid={`freigabe-sonder-${e.protocol_id}`}>
            Sondervereinbarung vor Ort: {e.sondervereinbarung}
          </div>
        )}
        {e.neuer_preis != null && (
          <div className="mt-2 text-[12px] flex flex-wrap items-center gap-2" style={{ color: freigegeben ? "var(--st-gruen)" : "var(--text-dim)" }}>
            <span data-testid={`freigabe-aktueller-preis-${e.protocol_id}`}>
              {freigegeben ? "Freigegeben mit" : "Verhandelter Preis"} {eur(e.neuer_preis)}
              {e.preis_notiz ? ` · ${e.preis_notiz}` : ""}
            </span>
            {kannZuruecksetzen && (
              <button type="button" disabled={busy}
                      onClick={() => senden(e, { preis_zuruecksetzen: true })}
                      data-testid={`freigabe-preis-zuruecksetzen-${e.protocol_id}`}
                      className="inline-flex items-center gap-1 text-[11px] text-zinc-400 hover:text-white disabled:opacity-50 min-h-[36px] px-1 -mx-1">
                <Undo2 size={11} /> auf Vertragspreis zurücksetzen
              </button>
            )}
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {/* Pruefung 14.09.2026 (Liste 4, Nr. 7): ein Protokoll mit Ladefehler wird nicht freigegeben */}
        <button onClick={() => senden(e, {})} disabled={busy || !!e.ladefehler}
                title={e.ladefehler ? "Protokoll fehlerhaft — bitte an den Fahrer zurückschicken" : undefined}
                data-testid={`freigabe-ok-${e.protocol_id}`}
                className="apple-btn apple-btn-primary !py-2 disabled:opacity-50">
          <Check size={14} /> {freigegeben ? "Preis aktualisieren" : "Freigeben"}
        </button>
        <button onClick={() => senden(e, { zurueck: true })} disabled={busy}
                data-testid={`freigabe-zurueck-${e.protocol_id}`}
                className="apple-btn apple-btn-secondary !py-2 disabled:opacity-50">
          <RotateCcw size={14} /> Zurück an den Fahrer
        </button>
        {e.verkaeufer && !freigegeben && (
          <span className="inline-flex items-center gap-1 text-[11px] text-zinc-500 self-center">
            <Phone size={11} /> Vor der Freigabe beim Verkäufer nachverhandeln
          </span>
        )}
      </div>
    </div>
  );
}

/** Prüfbericht 20.09. U-164: der Server kappt die Warteliste und meldet das
 *  per X-Truncated — dann "mindestens N" statt einer scheinbar vollen Zahl. */
export function wartendUeberschrift(anzahl, gekuerzt) {
  return gekuerzt
    ? `Warten auf Freigabe (mindestens ${anzahl} — neueste nicht angezeigt)`
    : `Warten auf Freigabe (${anzahl})`;
}

/** Prüfbericht 20.09. U-171: Fehlertext für einen getippten Preis, null wenn
 *  er gesendet werden darf. preisAusText("0") liefert 0 (nicht null) — der
 *  Server verlangt gt=0 und antwortete 422 mit englischem Text. */
export function preisFehler(zahl) {
  if (zahl === null) return "Bitte einen gültigen Preis eingeben, z. B. 15.000 oder 15000,50";
  if (!(zahl > 0)) return "Der Preis muss größer als 0 sein – zum Entfernen „Auf Vertragspreis zurücksetzen“ nutzen.";
  return null;
}

export default function Freigaben() {
  const { user } = useAuth();
  // 14.09.2026 (Wunsch Ahmad): "Nur der Firmenchef darf mit dem Fahrer vor Ort
  // kommunizieren, Sucher nicht." Das Backend antwortet Suchern mit 403 —
  // hier gar nicht erst laden, sondern erklaeren.
  const chef = user?.role === "dealer";
  const [liste, setListe] = useState(null);
  const [ladeFehler, setLadeFehler] = useState("");
  const [gekuerzt, setGekuerzt] = useState(false);
  const [entwurf, setEntwurf] = useState({});
  const [busy, setBusy] = useState({});
  // Prüfbericht 20.09. U-163: Intervall, Sichtbarwerden und "nach dem Senden"
  // rufen laden() parallel — nur die zuletzt GESTARTETE Anfrage darf die
  // Liste setzen, sonst gewann die zuletzt ankommende (ältere) Antwort.
  const lauf = useRef(0);

  const laden = useCallback(async () => {
    if (!chef) { setListe([]); return; }
    const n = ++lauf.current;
    try {
      const r = await api.get("/protocols/zur-freigabe");
      if (n !== lauf.current) return;
      const data = r.data;
      const neu = Array.isArray(data) ? data : [];
      setListe(neu);
      setGekuerzt(String(r.headers?.["x-truncated"] || "") === "1");
      setLadeFehler("");
      // Gegenpruefung 12.09.2026: Entwuerfe zu Protokollen, die nicht mehr
      // warten (abgeschlossen, zurueckgeschickt), verwerfen — sonst hielten
      // unsichtbare Eingaben die Warnung "ungespeichert" dauerhaft aktiv.
      // Rollenprüfung 22.09.2026 (RP-499): beim Zurückschicken gemerkte Preise
      // wieder einsetzen, sobald das Protokoll erneut wartet.
      const ids = new Set(neu.map((x) => x.protocol_id));
      const gemerkt = preisMerkerLesen();
      preisMerkerSchreiben(entwuerfeAbgleichen({}, ids, gemerkt).gemerkt);
      setEntwurf((s) => {
        const erg = entwuerfeAbgleichen(s, ids, gemerkt).entwurf;
        return JSON.stringify(erg) === JSON.stringify(s) ? s : erg;
      });
    } catch (e) {
      if (n !== lauf.current) return;
      setLadeFehler(errMsg(e, "Freigaben konnten nicht geladen werden"));
      setListe((l) => l ?? []);
    }
  }, [chef]);

  useEffect(() => {
    laden();
    const t = setInterval(() => {
      if (document.visibilityState === "visible") laden();
    }, 15000);
    const sichtbar = () => { if (document.visibilityState === "visible") laden(); };
    document.addEventListener("visibilitychange", sichtbar);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", sichtbar); };
  }, [laden]);

  const ungespeichert = useMemo(
    () => entwurfUngespeichert(entwurf, liste),
    [entwurf, liste]);
  useUngespeichert(ungespeichert);

  const senden = async (e, { zurueck = false, preis_zuruecksetzen = false, notiz: notizVorgabe,
                            rueckfrage_frage } = {}) => {
    const id = e.protocol_id;
    setBusy((b) => ({ ...b, [id]: true }));
    try {
      const koerper = { zurueck, stand: e.stand };
      // Stufe 3 KI (26.09.2026): die konkrete Frage geht strukturiert mit —
      // der Fahrer antwortet per Knopf.
      if (zurueck && rueckfrage_frage?.question) {
        koerper.rueckfrage_frage = { source_id: rueckfrage_frage.source_id || "",
                                     question: rueckfrage_frage.question,
                                     options: rueckfrage_frage.options || [] };
      }
      if (preis_zuruecksetzen) koerper.preis_zuruecksetzen = true;
      const eigener = entwurf[id] || {};
      const freigabe = !zurueck && !preis_zuruecksetzen;
      if (freigabe && String(eigener.preis ?? "").trim() !== "") {
        const zahl = preisAusText(eigener.preis);
        const fehler = preisFehler(zahl);
        if (fehler) {
          toast.error(fehler);
          return;
        }
        koerper.neuer_preis = zahl;
      }
      // Der Vermerk geht mit der Freigabe ins Protokoll bzw. als Rueckfrage an
      // den Fahrer — beim Zuruecksetzen des Preises wird er nicht verwendet.
      // Rollenprüfung 22.09.2026 (RP-146): ein geleerter Vermerk geht als ""
      // mit (der Server entfernt ihn), ein unberührtes Feld gar nicht.
      const notiz = preis_zuruecksetzen ? undefined
        : (notizVorgabe !== undefined ? notizVorgabe : notizFuerSenden(eigener, e, { zurueck }));
      if (notiz !== undefined) koerper.notiz = notiz;
      await api.post(`/protocols/${id}/freigabe`, koerper);
      if (zurueck && String(eigener.preis ?? "").trim()) {
        // Rollenprüfung 22.09.2026 (RP-499): getippten Preis merken — laden()
        // verwirft den Entwurf gleich (das Protokoll wartet nicht mehr).
        const m = preisMerkerLesen();
        m[id] = { preis: String(eigener.preis).trim(), am: Date.now() };
        preisMerkerSchreiben(m);
      }
      toast.success(zurueck ? "Zurück an den Fahrer geschickt"
        : preis_zuruecksetzen ? "Verhandelter Preis entfernt — es gilt wieder der Vertragspreis"
          : "Freigegeben — der Fahrer kann jetzt unterschreiben lassen");
      // Nur entfernen, was wirklich verschickt wurde (ein getippter Preis
      // bleibt beim "Zurueck an den Fahrer" stehen).
      setEntwurf((s) => {
        if (preis_zuruecksetzen) return s;
        const n = { ...s };
        if (zurueck) n[id] = { ...n[id], notiz: "" };
        else delete n[id];
        return n;
      });
    } catch (err) {
      toast.error(errMsg(err, "Freigabe fehlgeschlagen"));
    } finally {
      // Erst den neuen Stand laden, dann die Knoepfe wieder freigeben — ein
      // schneller zweiter Klick schickte sonst den alten Stand.
      await laden();
      setBusy((b) => { const n = { ...b }; delete n[id]; return n; });
      freigabeZaehlerAktualisieren();
    }
  };

  const wartend = (liste || []).filter((e) => e.status !== "freigegeben");
  const freigegeben = (liste || []).filter((e) => e.status === "freigegeben");

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="freigaben-page">
      <div className="overline">Abholung</div>
      <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1 inline-flex items-center gap-3">
        <ClipboardCheck size={30} style={{ color: "var(--accent-red)" }} /> Freigaben
      </h1>
      <div className="text-[13px] mt-1" style={{ color: "var(--text-muted)" }}>
        Abholprotokolle, die Fahrer vor Ort ausgefüllt haben. Prüfen, beim Verkäufer nachverhandeln,
        freigeben — erst dann wird unterschrieben. Wer am längsten wartet, steht oben.
      </div>

      {ladeFehler && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm" style={{ borderColor: "#ff3b3055", background: "#ff3b3014", color: "var(--tx-rot)" }}>
          {ladeFehler} — es wird automatisch erneut versucht.
        </div>
      )}

      {!chef && (
        <div className="mt-6 rounded-xl border px-4 py-3 text-sm" data-testid="freigaben-chefsache"
             style={{ borderColor: "var(--border-default)", color: "var(--text-muted)" }}>
          Freigaben sind Chefsache: Nur der Firmen-Hauptaccount prüft die Abholprotokolle, verhandelt
          mit dem Verkäufer und gibt frei. Deine eigenen Vorgänge findest du weiter im Terminplaner
          und im Fahrzeugpool.
        </div>
      )}

      {chef && liste === null && <div className="mt-8 text-sm text-zinc-500">lädt …</div>}

      {chef && liste !== null && !liste.length && !ladeFehler && (
        <div className="mt-10 text-center text-sm text-zinc-500" data-testid="freigaben-leer">
          Gerade wartet kein Fahrer auf eine Freigabe.
        </div>
      )}

      {wartend.length > 0 && (
        <div className="mt-6 space-y-4">
          <div className="text-[12px] uppercase tracking-wider text-zinc-500" data-testid="freigaben-wartend-titel">
            {wartendUeberschrift(wartend.length, gekuerzt)}
          </div>
          {wartend.map((e) => (
            <Karte key={e.protocol_id} eintrag={e} entwurf={entwurf} setEntwurf={setEntwurf}
                   busy={Boolean(busy[e.protocol_id])} senden={senden} />
          ))}
        </div>
      )}

      {freigegeben.length > 0 && (
        <div className="mt-8 space-y-4">
          <div className="text-[12px] uppercase tracking-wider text-zinc-500">Freigegeben — vor Ort wird unterschrieben ({freigegeben.length})</div>
          {freigegeben.map((e) => (
            <Karte key={e.protocol_id} eintrag={e} entwurf={entwurf} setEntwurf={setEntwurf}
                   busy={Boolean(busy[e.protocol_id])} senden={senden} />
          ))}
        </div>
      )}
    </div>
  );
}
