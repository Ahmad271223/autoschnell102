import { useCallback, useEffect, useMemo, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { freigabeZaehlerAktualisieren } from "@/lib/freigaben";
import { preisAusText, preisText } from "@/lib/preis";
import { useUngespeichert } from "@/lib/ungespeichert";
import { toast } from "sonner";
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
  return (
    <div className="grid grid-cols-[minmax(0,9rem)_1fr] gap-x-3 gap-y-0.5 py-1.5 border-b last:border-b-0"
         style={{ borderColor: "rgba(255,255,255,0.06)" }}
         data-testid={`vergleich-${z.schluessel}`}>
      <div className="text-[11px] text-zinc-500 pt-0.5">{z.label}</div>
      <div className="text-[13px] min-w-0">
        <span className={rot && !ohneVertrag ? "text-zinc-500 line-through" : "text-zinc-300"}
              data-testid={`vergleich-${z.schluessel}-vertrag`}>
          {z.vertrag_text || <i className="not-italic text-zinc-600">{NICHT_IM_VERTRAG}</i>}
        </span>
        {(rot || (z.vor_ort_text && z.vor_ort_text !== z.vertrag_text)) && (
          <>
            <span className="mx-1.5 text-zinc-500">→</span>
            <b style={{ color: rot ? "#ff9f0a" : "#e4e4e7" }}
               data-testid={`vergleich-${z.schluessel}-vor-ort`}>
              {z.vor_ort_text || "—"}
            </b>
          </>
        )}
        {hinweis && (
          <span className="ml-2 text-[11px]" style={{ color: rot ? "#ff9f0a" : "#a1a1aa" }}>
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
          <div className="text-[12px] inline-flex items-center gap-1.5" style={{ color: "#34c759" }}>
            <Check size={13} /> Alle Fahrzeugdaten stimmen mit dem Vertrag überein
          </div>
        )
      )}
      {sichtbar.length > 0 && (
        <div className="rounded-lg px-3 py-1"
             style={{ background: wichtig.some((z) => z.abweichend) ? "#ff9f0a10" : "rgba(255,255,255,0.03)",
                      border: `1px solid ${wichtig.some((z) => z.abweichend) ? "#ff9f0a44" : "rgba(255,255,255,0.06)"}` }}>
          <div className="grid grid-cols-[minmax(0,9rem)_1fr] gap-x-3 pt-1.5 pb-1 text-[10px] uppercase tracking-wider text-zinc-500">
            <span />
            <span>laut Vertrag → vor Ort</span>
          </div>
          {sichtbar.map((z) => <VergleichZeile key={z.schluessel} z={z} />)}
        </div>
      )}
      <button type="button" onClick={() => setAlle((a) => !a)}
              className="mt-1.5 text-[11px] text-zinc-500 hover:text-white inline-flex items-center gap-1">
        {alle ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {alle ? "nur Auffälligkeiten" : `alle ${zeilen.length} Zeilen zeigen`}
      </button>
    </div>
  );
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
            <span className="text-[11px] rounded-full px-2 py-0.5" style={{ background: "#34c75922", color: "#34c759" }}>
              freigegeben — wartet auf Unterschriften
            </span>
          ) : (
            <span className="text-[11px] rounded-full px-2 py-0.5 inline-flex items-center gap-1"
                  style={{ background: "#ff9f0a22", color: "#ff9f0a" }}>
              <Clock size={11} /> wartet {wartetSeit(e.erstmals_abgeschickt_am || e.abgeschickt_am)}
            </span>
          )}
          {e.freigegeben_von_name && freigegeben && (
            <span className="text-[11px] text-zinc-500">von {e.freigegeben_von_name}</span>
          )}
        </div>
      </div>

      {e.ladefehler && (
        <div className="mt-3 rounded-lg px-3 py-2 text-[12px]" style={{ background: "#ff3b3014", color: "#ff8a80" }}>
          {e.ladefehler}
        </div>
      )}

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {[["Kilometerstand bei Abholung", e.kilometerstand_text || e.kilometerstand || "—"],
          ["Bekannte Schäden bestätigt", e.schaeden_bestaetigt === true ? "ja" : e.schaeden_bestaetigt === false ? "nein" : "—"],
          ["Schlüssel", e.schluessel ? `${e.schluessel}${e.schluessel_vereinbart ? ` (vereinbart ${e.schluessel_vereinbart})` : ""}` : "—"],
        ].map(([k, v]) => (
          <div key={k} className="rounded-lg p-2" style={{ background: "rgba(255,255,255,0.03)" }}>
            <div className="text-[11px] text-zinc-500">{k}</div>
            <div className="text-sm">{v}</div>
          </div>
        ))}
      </div>

      {!e.ladefehler && <div className="mt-3"><Vergleich eintrag={e} /></div>}

      {neueSchaeden.length > 0 && (
        <div className="mt-3 rounded-lg p-2.5" style={{ background: "#ff3b3014", border: "1px solid #ff3b3044" }}>
          <div className="text-[12px] font-semibold mb-1 inline-flex items-center gap-1" style={{ color: "#ff3b30" }}>
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

      <div className="mt-3 rounded-lg p-3" style={{ background: "rgba(255,255,255,0.03)" }}>
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
            <input type="text" maxLength={200}
                   data-testid={`freigabe-notiz-${e.protocol_id}`}
                   value={meinEntwurf.notiz ?? ""}
                   onChange={(ev) => setzen("notiz", ev.target.value)}
                   placeholder="z. B. Rost am Schweller"
                   className="mt-1 w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-sm outline-none focus:border-white/40"
                   style={{ borderColor: "var(--border-default)" }} />
          </div>
        </div>
        {e.neuer_preis != null && (
          <div className="mt-2 text-[12px] flex flex-wrap items-center gap-2" style={{ color: freigegeben ? "#34c759" : "#a1a1aa" }}>
            <span data-testid={`freigabe-aktueller-preis-${e.protocol_id}`}>
              {freigegeben ? "Freigegeben mit" : "Verhandelter Preis"} {eur(e.neuer_preis)}
              {e.preis_notiz ? ` · ${e.preis_notiz}` : ""}
            </span>
            {kannZuruecksetzen && (
              <button type="button" disabled={busy}
                      onClick={() => senden(e, { preis_zuruecksetzen: true })}
                      data-testid={`freigabe-preis-zuruecksetzen-${e.protocol_id}`}
                      className="inline-flex items-center gap-1 text-[11px] text-zinc-400 hover:text-white disabled:opacity-50">
                <Undo2 size={11} /> auf Vertragspreis zurücksetzen
              </button>
            )}
          </div>
        )}
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        <button onClick={() => senden(e, {})} disabled={busy}
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

export default function Freigaben() {
  const [liste, setListe] = useState(null);
  const [ladeFehler, setLadeFehler] = useState("");
  const [entwurf, setEntwurf] = useState({});
  const [busy, setBusy] = useState({});

  const laden = useCallback(async () => {
    try {
      const { data } = await api.get("/protocols/zur-freigabe");
      const neu = Array.isArray(data) ? data : [];
      setListe(neu);
      setLadeFehler("");
      // Gegenpruefung 12.09.2026: Entwuerfe zu Protokollen, die nicht mehr
      // warten (abgeschlossen, zurueckgeschickt), verwerfen — sonst hielten
      // unsichtbare Eingaben die Warnung "ungespeichert" dauerhaft aktiv.
      const ids = new Set(neu.map((x) => x.protocol_id));
      setEntwurf((s) => {
        const behalten = Object.entries(s).filter(([id]) => ids.has(id));
        return behalten.length === Object.keys(s).length ? s : Object.fromEntries(behalten);
      });
    } catch (e) {
      setLadeFehler(errMsg(e, "Freigaben konnten nicht geladen werden"));
      setListe((l) => l ?? []);
    }
  }, []);

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
    () => Object.values(entwurf).some((x) => String(x?.preis ?? "").trim() || String(x?.notiz ?? "").trim()),
    [entwurf]);
  useUngespeichert(ungespeichert);

  const senden = async (e, { zurueck = false, preis_zuruecksetzen = false } = {}) => {
    const id = e.protocol_id;
    setBusy((b) => ({ ...b, [id]: true }));
    try {
      const koerper = { zurueck, stand: e.stand };
      if (preis_zuruecksetzen) koerper.preis_zuruecksetzen = true;
      const eigener = entwurf[id] || {};
      const freigabe = !zurueck && !preis_zuruecksetzen;
      if (freigabe && String(eigener.preis ?? "").trim() !== "") {
        const zahl = preisAusText(eigener.preis);
        if (zahl === null) {
          toast.error("Bitte einen gültigen Preis eingeben, z. B. 15.000 oder 15000,50");
          return;
        }
        koerper.neuer_preis = zahl;
      }
      // Der Vermerk geht mit der Freigabe ins Protokoll bzw. als Rueckfrage an
      // den Fahrer — beim Zuruecksetzen des Preises wird er nicht verwendet.
      if (!preis_zuruecksetzen && String(eigener.notiz ?? "").trim() !== "") {
        koerper.notiz = String(eigener.notiz).trim();
      }
      await api.post(`/protocols/${id}/freigabe`, koerper);
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
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm" style={{ borderColor: "#ff3b3055", background: "#ff3b3014", color: "#ff8a80" }}>
          {ladeFehler} — es wird automatisch erneut versucht.
        </div>
      )}

      {liste === null && <div className="mt-8 text-sm text-zinc-500">lädt …</div>}

      {liste !== null && !liste.length && !ladeFehler && (
        <div className="mt-10 text-center text-sm text-zinc-500" data-testid="freigaben-leer">
          Gerade wartet kein Fahrer auf eine Freigabe.
        </div>
      )}

      {wartend.length > 0 && (
        <div className="mt-6 space-y-4">
          <div className="text-[12px] uppercase tracking-wider text-zinc-500">Warten auf Freigabe ({wartend.length})</div>
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
