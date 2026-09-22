import { useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { api, errMsg } from "@/lib/api";
import { preisAusText, preisText } from "@/lib/preis";
import { toast } from "sonner";
import { Check, Handshake, Inbox, MessageSquare, X } from "lucide-react";

/**
 * Kaufanfragen vom B2B-Marktplatz (Händler-Sicht).
 *
 * Zwischenhändler senden Interesse/Angebote zu veröffentlichten Inseraten —
 * hier beantwortet der Chef sie: akzeptieren (reserviert das Fahrzeug),
 * ablehnen oder Gegenangebot. Review 09/2026: die Endpunkte existierten,
 * es gab aber keinerlei Oberfläche dafür.
 */

const fmtEur = (n) => (n == null ? "—" : `${Number(n).toLocaleString("de-DE")} €`);
const fmtZeit = (iso) => {
  try {
    return new Date(iso).toLocaleString("de-DE", {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso || "—"; }
};

const STATUS_META = {
  offen:        { label: "Offen",        fg: "var(--tx-amber)", bg: "rgba(245,158,11,0.12)", bd: "rgba(245,158,11,0.35)" },
  gegenangebot: { label: "Gegenangebot", fg: "var(--tx-blau)", bg: "rgba(59,130,246,0.12)", bd: "rgba(59,130,246,0.35)" },
  gegenangebot_kaeufer: { label: "Gegenangebot Käufer", fg: "var(--tx-cyan)", bg: "rgba(59,130,246,0.12)", bd: "rgba(59,130,246,0.35)" },
  akzeptiert:   { label: "Akzeptiert",   fg: "var(--st-gruen)", bg: "rgba(52,199,89,0.12)",  bd: "rgba(52,199,89,0.35)" },
  abgelehnt:    { label: "Abgelehnt",    fg: "var(--text-dim)", bg: "var(--wa-05)", bd: "var(--wa-12)" },
};

const FILTERS = [
  { key: "", label: "Alle" },
  { key: "offen", label: "Offen" },
  { key: "gegenangebot", label: "Gegenangebot" },
  { key: "gegenangebot_kaeufer", label: "Gegenangebot Käufer" },
  { key: "akzeptiert", label: "Akzeptiert" },
  { key: "abgelehnt", label: "Abgelehnt" },
];

// ---------------------------------------------------------------------------
// Rollenprüfung 22.09.2026 — reine Hilfsfunktionen (vitest:
// Anfragen.rp_markt_haendler.test.jsx).
// ---------------------------------------------------------------------------

/** RP-456/RP-047: Gegenangebot deutsch lesen. Vorher Zahlenfeld +
 *  Number(): aus "20.900" wurden 20,90 €. Liefert { betrag, fehler }. */
export function gegenangebotBetrag(text) {
  if (!String(text ?? "").trim()) return { betrag: null, fehler: "Bitte einen Betrag eingeben" };
  const betrag = preisAusText(text);
  if (betrag === null || betrag <= 0) {
    return { betrag: null, fehler: "Bitte den Betrag als Zahl eintragen, z. B. 18.000 oder 18000." };
  }
  return { betrag, fehler: "" };
}

/** RP-093/192/343 (f): Wer steht im Verlauf? Systemeinträge (Inserat
 *  verkauft/gelöscht/reserviert …) erschienen als "Käufer". */
export function verlaufVon(von) {
  if (von === "haendler") return "Du";
  if (von === "system") return "System";
  return "Käufer";
}

/** RP-502/RP-089/RP-098 Nr. 10/RP-519: Warum eine Anfrage beendet ist
 *  (beendet_grund) — dieselben Codes schreiben resale.py, marketplace.py,
 *  cleanup_service.py, admin.py und indizes.py. Vorher stand nur "Abgelehnt"
 *  da, im Verlauf die rohen Codes ("netzwerk_entfernt"). */
const GRUND_TEXT = {
  kaeufer_zurueckgezogen: "Vom Käufer zurückgezogen",
  netzwerk_entfernt: "Beendet — Käufer aus dem Netzwerk entfernt",
  inserat_verkauft: "Beendet — Fahrzeug verkauft",
  inserat_geloescht: "Beendet — Inserat gelöscht",
  inserat_zurueckgezogen: "Beendet — Inserat vom Marktplatz genommen",
  inserat_entwurf: "Beendet — Inserat zurück auf Entwurf gesetzt",
  inserat_reserviert: "Beendet — Fahrzeug von Hand reserviert",
  reservierung_aufgehoben: "Beendet — Reservierung aufgehoben",
  inserat_abgelaufen: "Beendet — Inserat abgelaufen",
  inserat_weg: "Beendet — Inserat nicht mehr vorhanden",
  kaeufer_gesperrt: "Beendet — Käufer gesperrt",
  kaeufer_geloescht: "Beendet — Käuferkonto gelöscht",
  doppelte_anfrage: "Beendet — doppelte Anfrage",
};

/** Lesbarer Abschluss einer beendeten Anfrage — oder null (läuft noch). */
export function beendetText(it) {
  if (!it || it.status !== "abgelehnt") return null;
  if (it.beendet_grund) return GRUND_TEXT[it.beendet_grund] || "Beendet";
  const ablehnung = [...(it.history || [])].reverse().find((h) => h?.aktion === "ablehnen");
  if (ablehnung?.von === "kaeufer") return "Vom Käufer abgelehnt";
  if (ablehnung?.von === "haendler") return "Von dir abgelehnt";
  return "Abgelehnt";
}

const AKTION_TEXT = {
  interesse: "Anfrage", gegenangebot: "Gegenangebot", akzeptieren: "angenommen",
  annehmen: "angenommen", ablehnen: "abgelehnt", zurueckgezogen: "zurückgezogen",
};

/** RP-098 Nr. 10: eine Verlaufszeile ohne rohe Codes
 *  ("System · Beendet — Käufer aus dem Netzwerk entfernt"). Ohne Zeit. */
export function verlaufZeile(h) {
  const a = h?.aktion;
  const was = AKTION_TEXT[a] || GRUND_TEXT[a] || String(a || "").replace(/_/g, " ");
  let zeile = `${verlaufVon(h?.von)} · ${was}`;
  if (h?.angebot !== null && h?.angebot !== undefined) zeile += ` · ${fmtEur(h.angebot)}`;
  if (h?.nachricht) zeile += ` · „${h.nachricht}“`;
  return zeile;
}

/** RP-491: Was "Antworten" an den Server schickt — den Stand, den der Händler
 *  gesehen hat. Hat der Käufer inzwischen ein anderes Angebot geschickt,
 *  antwortet der Server 409, statt zu einem nie gesehenen Preis zu
 *  reservieren. erwarteter_betrag nur beim Annehmen (null = ohne Preisangebot,
 *  muss ausdrücklich mitgehen). */
export function antwortDaten(it, action, extra = {}) {
  const betrag = it?.status === "gegenangebot_kaeufer" ? it?.buyer_counter_offer : it?.offer;
  return {
    action, message: "", erwarteter_status: it?.status,
    ...(action === "akzeptieren" ? { erwarteter_betrag: betrag ?? null } : {}),
    ...extra,
  };
}

/** Rollenprüfung 22.09.2026 (RP-093(4)): Annahme ohne Preisangebot — der
 *  Server nimmt den Inseratspreis, den dieser Käufer sieht, und vermerkt die
 *  Stufe in agreed_price_quelle. */
const INSERATSPREIS_TEXT = {
  inserat_oeffentlich: "zum Inseratspreis (öffentlich)",
  inserat_b2b: "zum Inseratspreis (B2B)",
  inserat_netzwerk: "zum Inseratspreis (Netzwerk)",
};

/** RP-477: Rechter Preisblock — nach der Einigung der vereinbarte Preis groß,
 *  das ursprüngliche Angebot klein darunter. */
export function preisBlock(it) {
  if (it?.status === "akzeptiert") {
    const vereinbart = it.agreed_price;
    const quelle = vereinbart != null ? INSERATSPREIS_TEXT[it.agreed_price_quelle] : undefined;
    return {
      titel: vereinbart != null ? "Vereinbarter Preis" : "Angenommen ohne Preisangebot",
      betrag: vereinbart ?? null,
      unter: it.offer != null ? `Angebot des Käufers: ${fmtEur(it.offer)}` : (quelle || ""),
    };
  }
  return {
    titel: it?.offer != null ? "Angebot des Käufers" : "Ohne Preisangebot",
    betrag: it?.offer ?? null,
    unter: "",
  };
}

/** RP-519: Ablaufdatum des Inserats (published_at + 21 Tage) als
 *  "TT.MM.JJJJ" — nach Ablauf endet die Anfrage automatisch. */
export function laeuftAbText(iso, jetzt = new Date()) {
  if (!iso) return "";
  const ende = new Date(iso);
  if (Number.isNaN(ende.getTime())) return "";
  if (ende.getTime() <= jetzt.getTime()) {
    return "Das Inserat ist abgelaufen und wird in Kürze entfernt — die Anfrage endet dann.";
  }
  const datum = ende.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
  return `Inserat läuft ab am ${datum} — danach endet die Anfrage automatisch.`;
}

/** RP-042/141: Rückfrage vor "Akzeptieren & reservieren" — mit Käufer,
 *  Betrag und Folge (verbindliche Reservierung). */
export function annahmeFrage(it) {
  const betrag = it?.status === "gegenangebot_kaeufer" ? it?.buyer_counter_offer : it?.offer;
  const wer = [it?.buyer_name, it?.buyer_email].filter(Boolean).join(" · ") || "den Käufer";
  // Rollenprüfung 22.09.2026 (RP-093(4)): ohne Preisangebot gilt jetzt der
  // Inseratspreis, den dieser Käufer sieht (der Server vermerkt die Stufe);
  // ohne jeden Inseratspreis lehnt der Server die Annahme ab.
  const preis = typeof betrag === "number" && betrag > 0
    ? `zum Preis von ${preisText(betrag)}`
    : "OHNE eigenes Preisangebot zum Inseratspreis, den dieser Käufer sieht "
      + "(öffentlich, B2B oder Netzwerk — der niedrigste für ihn zulässige)";
  return `Anfrage annehmen und das Fahrzeug verbindlich für ${wer} ${preis} reservieren?\n\n`
    + "Das Inserat verschwindet dann vom Marktplatz; andere Käufer können nicht mehr verhandeln.";
}

function StatusBadge({ status, beendet }) {
  const m = STATUS_META[status] || STATUS_META.abgelehnt;
  // RP-502/RP-089: vom System oder vom Käufer beendete Anfragen heißen
  // "Beendet" — "Abgelehnt" klang, als hätte der Händler abgelehnt.
  const label = status === "abgelehnt" && beendet ? "Beendet" : m.label;
  return (
    <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
          style={{ color: m.fg, background: m.bg, border: `1px solid ${m.bd}` }}>
      {label}
    </span>
  );
}

export default function Anfragen() {
  const { user } = useAuth();
  const [items, setItems] = useState(null);
  const [filter, setFilter] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [counterFor, setCounterFor] = useState(null); // interest_id mit offenem Gegenangebots-Formular
  const [counterVal, setCounterVal] = useState("");
  const [counterMsg, setCounterMsg] = useState("");
  // Pruefbericht 20.09.2026 (U-19/H12): Ein Ladefehler wurde zu "Keine
  // Anfragen" — der Chef verpasste offene Kaufanfragen.
  const [ladeFehler, setLadeFehler] = useState("");
  const [gekuerzt, setGekuerzt] = useState(false);
  const anfrageNr = useRef(0);          // U-27: nur die letzte Antwort zaehlt
  const istChef = user?.role === "dealer";

  const load = useCallback(async () => {
    // U-20/M13: Sucher werden gleich umgeleitet — keinen Chef-Abruf mehr
    // abfeuern, dessen 403 dann auf der naechsten Seite als Fehler aufpoppt.
    if (!istChef) return;
    const nr = ++anfrageNr.current;
    try {
      const r = await api.get("/dealer/interessen", {
        params: filter ? { status: filter } : {},
      });
      if (nr !== anfrageNr.current) return;
      setItems(Array.isArray(r.data) ? r.data : []);
      setGekuerzt(String(r.headers?.["x-truncated"] || "") === "1");
      setLadeFehler("");
    } catch (e) {
      if (nr !== anfrageNr.current) return;
      setLadeFehler(errMsg(e, "Anfragen konnten nicht geladen werden"));
    }
  }, [filter, istChef]);
  useEffect(() => { load(); }, [load]);

  const antworten = async (it, action, extra = {}) => {
    if (busyId) return;
    // RP-042/141: Ein Klick reservierte das Fahrzeug verbindlich — ohne Rückfrage.
    if (action === "akzeptieren" && !window.confirm(annahmeFrage(it))) return;
    if (action === "ablehnen" && !window.confirm(
      `Anfrage von ${it.buyer_name || "diesem Käufer"} ablehnen? Die Verhandlung ist damit beendet.`)) return;
    setBusyId(it.id);
    try {
      // RP-491: gesehenen Stand mitschicken (siehe antwortDaten)
      await api.post(`/interessen/${it.id}/antwort`, antwortDaten(it, action, extra));
      toast.success(action === "akzeptieren"
        ? "Anfrage akzeptiert — das Fahrzeug ist jetzt für den Käufer reserviert"
        : action === "ablehnen" ? "Anfrage abgelehnt" : "Gegenangebot gesendet");
      setCounterFor(null); setCounterVal(""); setCounterMsg("");
      load();
    } catch (e) {
      toast.error(errMsg(e, "Antwort fehlgeschlagen"));
      // RP-491: Stand hat sich geändert (z. B. neues Käuferangebot) — neu laden,
      // damit der Händler den aktuellen Betrag sieht, bevor er erneut antwortet.
      if (e?.response?.status === 409) load();
    } finally {
      setBusyId(null);
    }
  };

  const sendCounter = (it) => {
    // RP-456: deutsch lesen ("20.900" = 20.900 €, nicht 20,90 €)
    const { betrag, fehler } = gegenangebotBetrag(counterVal);
    if (fehler) { toast.error(fehler); return; }
    antworten(it, "gegenangebot", { counter_offer: betrag, message: counterMsg });
  };

  // Nur der Haendler-Hauptaccount bearbeitet Kaufanfragen (Backend: current_haendler)
  // M6/U-152: nicht auf den abo-pflichtigen Vergleich umleiten — ein Sucher
  // ohne Abo landete sonst auf der Paywall. /app waehlt die passende Seite.
  if (user && user.role !== "dealer") return <Navigate to="/app" replace />;
  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="anfragen-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="overline">Marktplatz</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
            Kaufanfragen
          </h1>
        </div>
        {items && <span className="text-xs text-zinc-500">{items.length} Anfragen</span>}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilter(f.key)}
                  data-testid={`anfragen-filter-${f.key || "alle"}`}
                  className={`px-3 py-1.5 rounded-lg text-[13px] border transition ${
                    filter === f.key ? "text-white border-white/40 bg-white/10" : "text-zinc-500"}`}
                  style={filter === f.key ? {} : { borderColor: "var(--border-default)" }}>
            {f.label}
          </button>
        ))}
      </div>

      {ladeFehler && (
        <div className="mt-5 rounded-xl border px-4 py-3 text-sm flex flex-wrap items-center gap-3" role="alert"
             data-testid="anfragen-ladefehler"
             style={{ borderColor: "#ef444455", background: "#ef444414", color: "var(--text-primary)" }}>
          <span className="flex-1 min-w-0">{ladeFehler}{items?.length ? " — angezeigt ist der letzte Stand." : ""}</span>
          <button type="button" onClick={load} className="rounded-lg px-3 py-1.5 text-xs border font-semibold"
                  style={{ borderColor: "var(--border-default)" }}>Erneut versuchen</button>
        </div>
      )}
      {gekuerzt && (
        <div className="mt-4 text-xs" style={{ color: "var(--text-muted)" }} data-testid="anfragen-gekuerzt">
          Es werden nur die neuesten Anfragen angezeigt — ältere erledigte findest du über den Status-Filter.
        </div>
      )}
      {items === null ? (
        ladeFehler ? null : <div className="mt-10 text-sm" style={{ color: "var(--text-muted)" }}>Lädt…</div>
      ) : items.length === 0 && !ladeFehler ? (
        <div className="mt-10 text-center py-16 tactical-card">
          <Inbox size={28} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <div className="text-[15px] font-semibold">Keine Anfragen{filter ? " mit diesem Status" : ""}</div>
          <div className="mt-1 text-[13px]" style={{ color: "var(--text-muted)" }}>
            Anfragen erscheinen hier, sobald Zwischenhändler Interesse an deinen veröffentlichten Inseraten melden.
          </div>
        </div>
      ) : (
        <div className="mt-5 space-y-3">
          {items.map((it) => {
            const preis = preisBlock(it);
            const beendet = beendetText(it);
            // RP-478: Das Auto ist für einen ANDEREN Käufer reserviert — die
            // Anfrage ruht; Annehmen/Gegenangebot gäben nur 409.
            const ruht = !!it.anderweitig_reserviert;
            const laufend = ["offen", "gegenangebot", "gegenangebot_kaeufer"].includes(it.status);
            const ablauf = laufend && !ruht && (!it.inserat_status || it.inserat_status === "veroeffentlicht")
              ? laeuftAbText(it.laeuft_ab_am) : "";
            return (
            <div key={it.id} className="tactical-card p-4 sm:p-5" data-testid={`anfrage-${it.id}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link to={`/app/inserat/${it.listing_id}`}
                          className="font-display font-bold text-lg tracking-tight hover:underline truncate">
                      {it.listing_title || "Inserat"}
                    </Link>
                    <StatusBadge status={it.status} beendet={!!it.beendet_grund} />
                  </div>
                  <div className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
                    {it.buyer_name}{it.buyer_email ? ` · ${it.buyer_email}` : ""} · {fmtZeit(it.created_at)}
                  </div>
                  {beendet && (
                    <div className="mt-1 text-[12.5px]" data-testid={`anfrage-beendet-${it.id}`}
                         style={{ color: "var(--text-muted)" }}>
                      {beendet}
                    </div>
                  )}
                  {it.message && (
                    <div className="mt-2 text-[13.5px] flex items-start gap-1.5" style={{ color: "var(--text-secondary)" }}>
                      <MessageSquare size={14} className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }} />
                      <span className="whitespace-pre-line break-words">{it.message}</span>
                    </div>
                  )}
                </div>
                <div className="text-right shrink-0" data-testid={`anfrage-preis-${it.id}`}>
                  <div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                    {preis.titel}
                  </div>
                  <div className="font-display font-black text-2xl tracking-tight">{fmtEur(preis.betrag)}</div>
                  {preis.unter && (
                    <div className="mt-1 text-[12px]" style={{ color: "var(--text-muted)" }}>{preis.unter}</div>
                  )}
                  {it.status === "gegenangebot" && it.counter_offer != null && (
                    <div className="mt-1 text-[12px] text-sky-400">Dein Gegenangebot: {fmtEur(it.counter_offer)}</div>
                  )}
                </div>
              </div>

              {(it.history || []).length > 1 && (
                <div className="mt-3 pt-3 space-y-1 text-[12px]" style={{ borderTop: "1px solid var(--border-default)", color: "var(--text-muted)" }}>
                  {it.history.map((h, i) => (
                    <div key={i}>
                      {verlaufZeile(h)} · {fmtZeit(h.zeit)}
                    </div>
                  ))}
                </div>
              )}

              {ruht && laufend && (
                <div className="mt-3 rounded-xl p-3 text-sm" data-testid={`anfrage-ruht-${it.id}`}
                     style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.3)" }}>
                  {/* Rollenprüfung 22.09.2026 (RP-093(1)): anderweitig_reserviert gilt
                      auch für die Reservierung von Hand (ohne Käufer). */}
                  Fahrzeug ist reserviert — diese Anfrage ruht
                  (nach Aufheben der Reservierung wieder frei).
                </div>
              )}
              {ablauf && (
                <div className="mt-3 text-[12px]" data-testid={`anfrage-ablauf-${it.id}`}
                     style={{ color: "var(--text-muted)" }}>
                  {ablauf}
                </div>
              )}

              {it.status === "gegenangebot_kaeufer" && (
                <div className="mt-3 rounded-xl p-3 text-sm" data-testid={`kaeufer-gegenangebot-${it.id}`}
                     style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.3)" }}>
                  Gegenangebot des Käufers:{" "}
                  <span className="font-bold text-sky-400">{fmtEur(it.buyer_counter_offer)}</span>
                  {!ruht && (
                    <span className="text-[12px]" style={{ color: "var(--text-muted)" }}> — akzeptieren, ablehnen oder erneut ein Angebot schreiben.</span>
                  )}
                </div>
              )}
              {laufend && (
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  {/* Nachpruefung Runde 14 (Nr. 48): im Status "gegenangebot" liegt das eigene
                      Gegenangebot beim Kaeufer — der Haendler kann es nicht selbst annehmen
                      (Backend antwortet 400). Akzeptieren nur fuer offen / gegenangebot_kaeufer.
                      RP-478: ruht die Anfrage (anderweitig reserviert), bleibt nur "Ablehnen". */}
                  {["offen", "gegenangebot_kaeufer"].includes(it.status) && !ruht && (
                    <button onClick={() => antworten(it, "akzeptieren")} disabled={busyId === it.id}
                            data-testid={`anfrage-akzeptieren-${it.id}`}
                            className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                            style={{ background: "var(--st-gruen)" }}>
                      <Check size={15} /> Akzeptieren & reservieren
                    </button>
                  )}
                  {!ruht && (
                    <button onClick={() => { setCounterFor(counterFor === it.id ? null : it.id); setCounterVal(""); setCounterMsg(""); }}
                            disabled={busyId === it.id}
                            data-testid={`anfrage-gegenangebot-${it.id}`}
                            className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold border disabled:opacity-50"
                            style={{ borderColor: "var(--border-default)" }}>
                      <Handshake size={15} /> Gegenangebot
                    </button>
                  )}
                  <button onClick={() => antworten(it, "ablehnen")} disabled={busyId === it.id}
                          data-testid={`anfrage-ablehnen-${it.id}`}
                          className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm text-zinc-400 hover:text-red-400 disabled:opacity-50">
                    <X size={15} /> Ablehnen
                  </button>
                </div>
              )}
              {it.status === "gegenangebot" && !ruht && (
                <div className="mt-3 text-[12.5px]" style={{ color: "var(--text-muted)" }}>
                  Dein Gegenangebot ({fmtEur(it.counter_offer)}) liegt beim Käufer — warte auf seine Antwort oder schreibe jederzeit ein neues Angebot.
                </div>
              )}

              {counterFor === it.id && laufend && !ruht && (
                <div className="mt-3 rounded-xl p-3 flex flex-wrap items-end gap-2"
                     style={{ background: "var(--wa-03)", border: "1px solid var(--border-default)" }}>
                  <div>
                    <label className="block text-[10px] uppercase tracking-wide mb-1" style={{ color: "var(--text-muted)" }}>
                      Gegenangebot (€)
                    </label>
                    <input type="text" inputMode="decimal" placeholder="18.000" value={counterVal}
                           onChange={(e) => setCounterVal(e.target.value)}
                           aria-invalid={!!String(counterVal).trim() && !!gegenangebotBetrag(counterVal).fehler}
                           data-testid={`gegenangebot-betrag-${it.id}`} autoFocus
                           className="h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none focus:border-white/40 w-36"
                           style={{ borderColor: "var(--border-default)" }} />
                    {/* RP-456: zeigen, wie der Betrag verstanden wird */}
                    {String(counterVal).trim() && (
                      <div className="mt-1 text-[11px]" data-testid={`gegenangebot-verstanden-${it.id}`}
                           style={{ color: gegenangebotBetrag(counterVal).fehler ? "var(--st-rot)" : "var(--text-muted)" }}>
                        {gegenangebotBetrag(counterVal).fehler
                          ? "Nicht lesbar — z. B. 18.000"
                          : `= ${preisText(gegenangebotBetrag(counterVal).betrag)}`}
                      </div>
                    )}
                  </div>
                  <div className="flex-1 min-w-[180px]">
                    <label className="block text-[10px] uppercase tracking-wide mb-1" style={{ color: "var(--text-muted)" }}>
                      Nachricht (optional)
                    </label>
                    <input value={counterMsg} onChange={(e) => setCounterMsg(e.target.value)} maxLength={2000}
                           className="h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none focus:border-white/40 w-full"
                           style={{ borderColor: "var(--border-default)" }} />
                  </div>
                  <button onClick={() => sendCounter(it)} disabled={busyId === it.id}
                          data-testid={`gegenangebot-senden-${it.id}`}
                          className="h-9 rounded-lg px-4 text-sm font-semibold text-white disabled:opacity-50"
                          style={{ background: "var(--accent-red)" }}>
                    Senden
                  </button>
                </div>
              )}
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
