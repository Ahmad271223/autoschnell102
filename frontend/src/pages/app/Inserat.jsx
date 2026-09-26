import MonatJahrEingabe from "@/components/MonatJahrEingabe";
import { monatJahrFehler } from "@/lib/monatJahr";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errMsg, openAuthedFile } from "@/lib/api";
import { openContractPdf } from "@/lib/pdf";
import { thumbSrc, thumbFehler, verkleinereBildDatei } from "@/lib/bilder";
import { INSERAT_LABELS } from "@/lib/fahrzeugStatus";
import { kmAusText, preisAusText, preisText } from "@/lib/preis";
import { useUngespeichert } from "@/lib/ungespeichert";
import { toast } from "sonner";
import {
  ArrowLeft, Camera, CheckCircle2, Undo2, Tag, Globe, EyeOff, Trash2, X, FileText, PenLine,
  ChevronLeft, ChevronRight, Star,
} from "lucide-react";

/**
 * Inserats-Editor: automatisch vorausgefüllter Entwurf aus der Fahrzeugakte
 * (inkl. Abholungs-Abweichungen), Foto-Modus, Preise mit Margen-Rechner,
 * Workflow Entwurf → Verkaufsbereit → Reserviert/Verkauft.
 */

const fmtEur = (n) => (n == null ? "—" : `${Number(n).toLocaleString("de-DE", { minimumFractionDigits: 0 })} €`);

const STATUS_LABELS = INSERAT_LABELS;

// ---------------------------------------------------------------------------
// Rollenprüfung 22.09.2026 — reine Hilfsfunktionen (mit vitest geprüft,
// Inserat.rp_markt_haendler.test.jsx).
// ---------------------------------------------------------------------------

/** Die Felder, die der Editor bearbeitet — Grundlage für "ungespeichert"
 *  (RP-044) und für den Abgleich nach Foto-Aktionen (RP-522). */
export function inseratStand(l) {
  if (!l) return "";
  return JSON.stringify({
    title: l.title ?? "", description: l.description ?? "",
    known_defects: l.known_defects || [], prices: l.prices || {}, data: l.data || {},
  });
}

/** RP-523: Kilometerstand deutsch lesen ("150.000", "150 Tkm") — Fehlertext
 *  oder "" (leer ist erlaubt). */
export function kmFehler(data) {
  const km = data?.mileage;
  if (km === null || km === undefined || typeof km === "number") return "";
  return Number.isNaN(kmAusText(km))
    ? "Bitte den Kilometerstand als Zahl eintragen, z. B. 150.000 oder 150 Tkm."
    : "";
}

/** RP-523: Fahrzeugdaten so, wie der Server sie bekommt — der Kilometerstand
 *  als Zahl ("150 Tkm" → 150000), damit nicht still 150 km daraus werden. */
export function datenFuerServer(data) {
  const d = { ...(data || {}) };
  if (typeof d.mileage === "string") {
    const km = kmAusText(d.mileage);
    if (km === null) d.mileage = "";
    else if (!Number.isNaN(km)) d.mileage = km;
  }
  return d;
}

/** Was "Speichern" an den Server schickt.
 *  RP-037/136/287/455/091/190/341: Während einer Reservierung lehnt der
 *  Server Preis, Fahrzeugdaten und Mängel ab (der Käufer ist an das gesehene
 *  Angebot gebunden). Vorher gingen sie immer mit — jedes Speichern und auch
 *  "Reservierung aufheben" scheiterten mit 400. Jetzt nur, was erlaubt ist.
 *  RP-463: `stand` (updated_at) — ein veralteter Tab bekommt 409 statt den
 *  Live-Stand still zurückzusetzen. RP-458: geleerte Preise gehen als null
 *  mit und werden entfernt.
 *  RP-532: Den Foto-Modus schickt der Editor nicht mehr mit (der Umschalter
 *  ist seit dem 20.09. weg) — sonst konnte ein Speichern kurz nach dem
 *  Hochladen den vom Server gesetzten Modus "beide" auf "einkauf"
 *  zurückdrehen, und die neuen Fotos wären beim Käufer wieder unsichtbar. */
export function speicherDaten(l) {
  const basis = {
    title: l.title, description: l.description, costs: l.costs,
    ...(l.updated_at ? { stand: l.updated_at } : {}),
  };
  if (l.status === "reserviert") return basis;
  return {
    ...basis,
    known_defects: (l.known_defects || []).map((m) => String(m).trim()).filter(Boolean),
    price_public: l.prices?.public ?? null,
    price_b2b: l.prices?.b2b ?? null,
    price_network: l.prices?.network ?? null,
    data: datenFuerServer(l.data),
  };
}

/** RP-036: zu lange Beschreibung an einer Zeilen-/Aufzählungs-/Wortgrenze
 *  kürzen (wie routes/resale._beschreibung_kuerzen), Länge inkl. "…" ≤ max. */
export function beschreibungKuerzen(text, max) {
  const t = String(text || "").trim();
  if (t.length <= max) return t;
  let schnitt = t.slice(0, max - 1);
  for (const trenner of ["\n", ", ", " "]) {
    const pos = schnitt.lastIndexOf(trenner);
    if (pos >= Math.floor(max / 2)) { schnitt = schnitt.slice(0, pos); break; }
  }
  return schnitt.replace(/[\s,;:]+$/, "") + "…";
}

/** RP-522/RP-045: Nach Foto-Aktionen nur die Foto-Felder vom Server
 *  übernehmen — vorher ersetzte load() das ganze Formular, und ungespeicherte
 *  Preise, Beschreibung und Mängel waren weg. Den neuen Stand (updated_at)
 *  nur übernehmen, wenn sonst niemand die bearbeiteten Felder geändert hat;
 *  sonst meldet das nächste Speichern zu Recht "inzwischen geändert". */
export function fotoFelderUebernehmen(alt, neu, basis) {
  if (!alt) return neu;
  if (!neu) return alt;
  const out = {
    ...alt, photos: neu.photos, photo_urls: neu.photo_urls,
    einkauf_thumbs: neu.einkauf_thumbs, abholfotos: neu.abholfotos,
  };
  if (inseratStand(neu) === basis) out.updated_at = neu.updated_at;
  return out;
}

/** RP-469: Foto um eine Stelle verschieben (richtung -1/+1) bzw. mit
 *  richtung "titel" an den Anfang. Liefert die neue Reihenfolge oder null. */
export function fotoReihenfolge(keys, key, richtung) {
  const liste = [...(keys || [])];
  const i = liste.indexOf(key);
  if (i < 0) return null;
  const ziel = richtung === "titel" ? 0 : i + richtung;
  if (ziel < 0 || ziel >= liste.length || ziel === i) return null;
  liste.splice(i, 1);
  liste.splice(ziel, 0, key);
  return liste;
}

/** RP-460: Vorschlag für "Verkauft" — der mit dem Käufer vereinbarte Preis
 *  (akzeptierte Anfrage), sonst der öffentliche Preis. */
export function verkaufsVorschlag(l) {
  const v = l?.vereinbarter_preis;
  if (typeof v === "number" && v > 0) return { betrag: v, vereinbart: true };
  const p = l?.prices?.public;
  return { betrag: p != null ? Number(p) : null, vereinbart: false };
}

/** RP-468: Restlaufzeit ab der ersten Veröffentlichung. */
export function laufzeitInfo(laufzeitBis, jetzt = new Date()) {
  if (!laufzeitBis) return null;
  const ende = new Date(laufzeitBis);
  if (Number.isNaN(ende.getTime())) return null;
  const tage = Math.ceil((ende.getTime() - jetzt.getTime()) / 86400000);
  return { abgelaufen: tage <= 0, tage: Math.max(0, tage),
           // RP-519: "Läuft ab am TT.MM.JJJJ"
           datum: ende.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" }) };
}

/** RP-521: Der Marktplatz rechnet mit dem NIEDRIGSTEN Preis, den ein Käufer
 *  sehen darf (öffentlich gilt für alle). Liegt der öffentliche Preis unter
 *  dem B2B- oder Netzwerkpreis, sehen auch B2B- und Netzwerk-Käufer den
 *  öffentlichen — der höhere Stufenpreis wirkt dann nicht. "" = kein Hinweis. */
export function preisStufenHinweis(prices) {
  const pub = prices?.public;
  if (!(typeof pub === "number" && pub > 0)) return "";
  const hoeher = [];
  if (typeof prices?.b2b === "number" && prices.b2b > pub) hoeher.push("der B2B-Preis");
  if (typeof prices?.network === "number" && prices.network > pub) hoeher.push("der Netzwerkpreis");
  if (!hoeher.length) return "";
  return `Der öffentliche Preis ist niedriger als ${hoeher.join(" und ")}. Käufer sehen immer den `
    + "niedrigsten für sie zulässigen Preis — hier den öffentlichen.";
}

/** RP-533: Rückmeldung nach dem Hochladen — Dateien, die der Browser (HEIC)
 *  oder der Server (kein Bild, zu groß) abgelehnt hat, mit Namen nennen. */
export function fotoAblehnungText(heic, abgelehnt) {
  const teile = [];
  if (heic > 0) teile.push(`${heic} Foto(s) im HEIC-Format übersprungen — bitte als JPG speichern`);
  if (abgelehnt?.length) {
    const namen = abgelehnt.slice(0, 3).map((a) => `${a.name || "Foto"}: ${a.grund}`).join("; ");
    teile.push(`${abgelehnt.length} Foto(s) abgelehnt (${namen}${abgelehnt.length > 3 ? "; …" : ""})`);
  }
  return teile.join(". ");
}

/** Rollenprüfung 22.09.2026 (RP-057 b): Einkaufspreis noch offen — mehrere
 *  Kaufverträge verschiedener Konten mit verschiedenen Preisen, nichts
 *  abgeholt. Der Server liefert dann purchase_price null und
 *  purchase_price_quelle "mehrdeutig" (keine Summe, keine Marge). */
export function einkaufspreisOffen(margin) {
  return margin?.purchase_price_quelle === "mehrdeutig" && margin?.purchase_price == null;
}

/** Lesbarer Anfragestatus für die Karte im Editor (vorher der rohe Code). */
const ANFRAGE_STATUS = {
  offen: "offen", gegenangebot: "dein Gegenangebot", gegenangebot_kaeufer: "Gegenangebot Käufer",
  akzeptiert: "angenommen", abgelehnt: "beendet",
};

export default function Inserat() {
  const { id } = useParams();
  const nav = useNavigate();
  const [l, setL] = useState(null);
  const [busy, setBusy] = useState(false);
  const [abholBusy, setAbholBusy] = useState(false);
  const [fotoBusy, setFotoBusy] = useState(false);   // RP-469: Umsortieren läuft
  // Rollenprüfung 22.09.2026 (Review): Statuswechsel läuft — sperrt alle
  // Status-Knöpfe bis der neue Stand geladen ist (vorher schickte ein
  // Doppelklick einen zweiten Wechsel mit dem alten angezeigten Status).
  // Die Ref fängt Klicks ab, die vor dem nächsten Rendern ankommen.
  const [statusBusy, setStatusBusy] = useState(false);
  const statusLaeuft = useRef(false);
  // U-103/H35: waehrend des Hochladens gesperrt (kein zweiter Upload parallel)
  const [ladeHoch, setLadeHoch] = useState(false);
  // Wunsch Ahmad 14.09.2026: Beim Inserieren soll der Chef das unterschriebene
  // Abholprotokoll und den abschliessenden Kaufvertrag weiter oeffnen koennen —
  // nur in SEINER Ansicht. Der Marktplatz bekommt davon nichts: die
  // oeffentliche Sicht (routes/marketplace._public_listing_view) ist eine
  // feste Feldliste ohne Fahrzeug-ID, Vertraege oder Protokolle.
  const [unterlagen, setUnterlagen] = useState(null);
  const fileRef = useRef(null);
  const backend = process.env.REACT_APP_BACKEND_URL;

  // Pruefbericht 20.09.2026 (B18/F1-F4): Jeder Ladefehler (404, 403 fuer
  // Sucher, 500, Funkloch) liess die Seite fuer immer auf "lade…" stehen —
  // ohne Text, ohne Rueckweg, ohne neuen Versuch.
  const [ladeFehler, setLadeFehler] = useState("");
  // Rollenprüfung 22.09.2026 (RP-044): zuletzt geladener bzw. gespeicherter
  // Stand der bearbeiteten Felder — weicht das Formular davon ab, gilt die
  // Seite als ungespeichert (Rückfrage vor dem Verlassen).
  const basisRef = useRef("");
  const load = useCallback(async () => {
    try {
      const r = await api.get(`/resale/${id}`);
      setLadeFehler("");
      basisRef.current = inseratStand(r.data);
      setL(r.data);
    } catch (e) {
      const status = e?.response?.status;
      const text = status === 404
        ? "Dieses Inserat gibt es nicht (mehr)."
        : status === 403
          ? errMsg(e, "Inserate bearbeitet der Hauptaccount der Firma.")
          : errMsg(e, "Inserat konnte nicht geladen werden");
      setLadeFehler(text);
      toast.error(text);
    }
  }, [id]);

  useEffect(() => {
    setL(null);
    setLadeFehler("");
    load();
  }, [load]);

  // Pruefbericht 20.09.2026 (U-97): Eigene Fotos haben signierte Links
  // (Standard 1 h). Laedt der Browser danach neu (Tab-Wiederherstellung,
  // Originalgroesse), gab es leere Rahmen. Dann frische Links holen — NUR die
  // Fotoadressen, damit nichts Ungespeichertes ueberschrieben wird.
  const fotoLinksAm = useRef(0);
  const fotoFehler = useCallback(async () => {
    const jetzt = Date.now();
    if (jetzt - fotoLinksAm.current < 60_000) return;
    fotoLinksAm.current = jetzt;
    try {
      const r = await api.get(`/resale/${id}`);
      setL((s) => (s ? { ...s, photo_urls: r.data?.photo_urls || [] } : s));
    } catch { /* bleibt beim leeren Rahmen */ }
  }, [id]);

  // RP-522/RP-045: nach Foto-Aktionen nur die Foto-Felder nachladen —
  // ungespeicherte Eingaben bleiben stehen.
  const fotosNeuLaden = useCallback(async () => {
    try {
      const r = await api.get(`/resale/${id}`);
      setL((s) => fotoFelderUebernehmen(s, r.data, basisRef.current));
    } catch { /* Anzeige bleibt beim letzten Stand */ }
  }, [id]);

  // RP-044: ungespeicherte Änderungen — Browser fragt vor Neuladen/Schließen,
  // der Link zurück zur Akte fragt selbst (die Seitenleiste: Übergabe an
  // AppLayout, siehe Bericht).
  const geaendert = !!l && l.status !== "verkauft" && !!basisRef.current
    && inseratStand(l) !== basisRef.current;
  useUngespeichert(geaendert);
  const wegNavigieren = (e) => {
    if (geaendert && !window.confirm("Es gibt ungespeicherte Änderungen am Inserat. Trotzdem verlassen?")) {
      e.preventDefault();
    }
  };

  // Eingabetext je Preisfeld (so, wie getippt) — gezeigt wird der Text, gerechnet
  // mit der gelesenen Zahl in l.prices.
  const [preisEingabe, setPreisEingabe] = useState({});
  const inseratId = l?.id;
  useEffect(() => {
    if (!inseratId) return;
    const text = (n) => (n === null || n === undefined ? "" : Number(n).toLocaleString("de-DE"));
    setPreisEingabe({ public: text(l?.prices?.public), b2b: text(l?.prices?.b2b),
                      network: text(l?.prices?.network) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inseratId]);

  const vehicleId = l?.vehicle_id;
  useEffect(() => {
    if (!vehicleId) return undefined;
    let aktiv = true;
    api.get(`/vehicles/${vehicleId}/akte`)
      .then((r) => { if (aktiv) setUnterlagen({ protocols: r.data.protocols || [], contracts: r.data.contracts || [] }); })
      .catch(() => { if (aktiv) setUnterlagen({ protocols: [], contracts: [] }); });
    return () => { aktiv = false; };
  }, [vehicleId]);

  if (!l) {
    return (
      <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="inserat-laedt">
        <Link to="/app/bestand" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
          <ArrowLeft size={14} /> Zurück zum Bestand
        </Link>
        {ladeFehler ? (
          <div className="tactical-card p-4 mt-4 text-sm" role="alert" data-testid="inserat-ladefehler">
            <div style={{ color: "var(--text-primary)" }}>{ladeFehler}</div>
            <button type="button" onClick={() => { setLadeFehler(""); load(); }}
                    className="mt-3 rounded-lg px-3 py-2 text-xs border font-semibold"
                    style={{ borderColor: "var(--border-default)" }}>
              Erneut versuchen
            </button>
          </div>
        ) : (
          <div className="mt-6 text-zinc-500 text-sm">lade…</div>
        )}
      </div>
    );
  }

  const set = (k) => (e) => setL((s) => ({ ...s, [k]: e.target.value }));
  // Pruefbericht 20.09.2026 (H36/U-104): Preise wurden mit parseFloat bzw. als
  // Zahlenfeld gelesen — "20.900" wurde 20,90 €, "20.900 €" leer, und die
  // Margenanzeige rechnete damit weiter. Jetzt deutsch (preisAusText), und ein
  // unlesbarer Wert blockiert das Speichern mit Hinweis statt still falsch.
  const setPrice = (k) => (e) => {
    const text = e.target.value;
    setPreisEingabe((p) => ({ ...p, [k]: text }));
    const zahl = preisAusText(text);
    setL((s) => ({ ...s, prices: { ...s.prices, [k]: text.trim() === "" ? null : zahl } }));
  };
  const preisFehler = Object.entries(preisEingabe)
    .filter(([, text]) => String(text || "").trim() && preisAusText(text) === null)
    .map(([k]) => k);

  const reserviert = l.status === "reserviert";

  const save = async (extra = {}) => {
    // RP-037: Preis, Fahrzeugdaten und Mängel gehen während einer
    // Reservierung nicht mit — dann auch nicht vorher prüfen.
    if (!reserviert) {
      const ezFehler = monatJahrFehler(l.data?.first_registration);
      if (ezFehler) { toast.error(`Erstzulassung: ${ezFehler}`); return false; }
      if (preisFehler.length) {
        toast.error("Bitte die Preise als Zahl eintragen, z. B. 20.900 oder 20900.");
        return false;
      }
      const km = kmFehler(l.data);
      if (km) { toast.error(km); return false; }
    }
    // RP-036/135/286: Der Server nimmt höchstens BESCHREIBUNG_MAX Zeichen.
    // Ältere Entwürfe sind länger (maxLength kürzt vorhandenen Text nicht) —
    // vorher endete jedes Speichern mit einer englischen 422-Meldung.
    const text = l.description || "";
    if (text.length > BESCHREIBUNG_MAX) {
      if (window.confirm(`Die Beschreibung ist ${text.length} Zeichen lang, erlaubt sind `
          + `${BESCHREIBUNG_MAX}.\n\nJetzt auf ${BESCHREIBUNG_MAX} Zeichen kürzen? `
          + "Bitte den gekürzten Text danach prüfen und erneut speichern.")) {
        setL((s) => ({ ...s, description: beschreibungKuerzen(s.description, BESCHREIBUNG_MAX) }));
        toast.message("Beschreibung gekürzt — bitte prüfen und erneut speichern.");
      } else {
        toast.error(`Bitte die Beschreibung auf höchstens ${BESCHREIBUNG_MAX} Zeichen kürzen.`);
      }
      return false;
    }
    setBusy(true);
    try {
      const r = await api.put(`/resale/${l.id}`, { ...speicherDaten(l), ...extra });
      basisRef.current = inseratStand(r.data);
      setL(r.data);
      toast.success("Gespeichert");
      return true;
    } catch (e) { toast.error(errMsg(e)); return false; }
    finally { setBusy(false); }
  };

  const setStatus = async (status, soldPrice) => {
    if (busy || statusLaeuft.current) return;
    // Rollenprüfung 22.09.2026 (Review): selbst sperren, bis der neue Stand
    // geladen ist — vorher prüfte setStatus nur busy, setzte es aber nie.
    statusLaeuft.current = true;
    setStatusBusy(true);
    try {
      // RP-455: "Reservierung aufheben" speichert vorher nur, was während der
      // Reservierung erlaubt ist (speicherDaten) — vorher scheiterte es immer.
      if (status === "verkaufsbereit" && !(await save())) return;
      try {
        // RP-492: angezeigten Status mitschicken — hat inzwischen ein Käufer
        // reserviert, antwortet der Server 409 statt die Reservierung zu treffen.
        await api.post(`/resale/${l.id}/status`, { status, sold_price: soldPrice, von_status: l.status });
        toast.success(`Status: ${STATUS_LABELS[status] || status}`);
        await load();
      } catch (e) {
        toast.error(errMsg(e));
        if (e?.response?.status === 409) await load();
      }
    } finally {
      statusLaeuft.current = false;
      setStatusBusy(false);
    }
  };
  // Status-Knöpfe: gesperrt während Speichern oder Statuswechsel.
  const statusGesperrt = busy || statusBusy;

  // RP-093/192/343: Reservieren von Hand beendet offene Kaufanfragen.
  const reservierenVonHand = () => {
    if (!window.confirm("Inserat von Hand reservieren (z. B. für einen Käufer am Telefon)?\n\n"
        + "Offene Kaufanfragen vom Marktplatz werden dabei beendet.")) return;
    setStatus("reserviert");
  };

  // RP-455: Aufheben beendet auch die angenommene Anfrage des Käufers.
  const reservierungAufheben = () => {
    if (!window.confirm("Reservierung aufheben?\n\nEine angenommene Kaufanfrage wird dabei "
        + "beendet, das Inserat steht danach wieder auf „verkaufsbereit“.")) return;
    setStatus("verkaufsbereit");
  };

  // H36/U-104: Der tatsaechliche Verkaufspreis wird deutsch gelesen und vor
  // dem Speichern so angezeigt, wie er verstanden wurde ("20.900" -> 20.900 €).
  // RP-460: Vorschlag ist der mit dem Käufer vereinbarte Preis, falls es einen gibt.
  const verkauftMelden = () => {
    const { betrag, vereinbart } = verkaufsVorschlag(l);
    const vorschlag = betrag != null ? Number(betrag).toLocaleString("de-DE") : "";
    const roh = window.prompt(vereinbart
      ? `Tatsächlicher Verkaufspreis in € (mit dem Käufer vereinbart: ${preisText(betrag)}):`
      : "Tatsächlicher Verkaufspreis in € (z. B. 20.900):", vorschlag);
    if (roh === null) return;
    if (!roh.trim()) {
      if (window.confirm("Ohne Verkaufspreis als verkauft markieren?")) setStatus("verkauft", null);
      return;
    }
    const preis = preisAusText(roh);
    if (preis === null || preis <= 0) {
      toast.error("Bitte den Verkaufspreis als Zahl eintragen, z. B. 20.900.");
      return;
    }
    if (!window.confirm(`Verkaufspreis ${preisText(preis)} speichern?`)) return;
    setStatus("verkauft", preis);
  };

  // RP-093/192/343 (e): Der Dialog versprach "jederzeit wieder
  // veröffentlichen" — gelöscht ist aber endgültig (der Server setzt
  // "geloescht"), und das Kontingent zählt weiter.
  const removeListing = async () => {
    if (!window.confirm(
      "Inserat endgültig löschen?\n\nEs verschwindet vom Marktplatz und lässt sich nicht "
      + "wiederherstellen; offene Kaufanfragen werden beendet. Bereits veröffentlichte "
      + "Inserate zählen im laufenden Monat weiter auf dein Kontingent.")) return;
    try {
      await api.delete(`/resale/${l.id}`);
      basisRef.current = "";
      toast.success("Inserat gelöscht");
      nav("/app/bestand");
    } catch (e) { toast.error(errMsg(e)); }
  };

  const publish = async (visibility = "public") => {
    if (busy || statusLaeuft.current) return;
    // Prüfbericht 20.09. U-108: wie setStatus bis zum neu geladenen Stand
    // sperren — vorher gab save() busy vor dem Publish-POST wieder frei und
    // ein Doppelklick schickte zwei Publish-Anfragen.
    statusLaeuft.current = true;
    setStatusBusy(true);
    try {
      // RP-467: Öffentliche Inserate sehen Käufer nur bei öffentlichem
      // Marktplatz-Profil. Vorher kam "veröffentlicht", obwohl bei neuen Firmen
      // niemand außer dem eigenen Netzwerk das Inserat sah.
      if (visibility === "public" && l.marktplatz_profil_oeffentlich === false) {
        if (!window.confirm("Dein Marktplatz-Profil ist noch nicht öffentlich — ein öffentliches "
            + "Inserat sähen dann nur deine Netzwerk-Partner.\n\nProfil jetzt öffentlich schalten "
            + "und veröffentlichen?\n(Abbrechen = nichts veröffentlichen)")) return;
        try {
          await api.put("/dealer/marketplace-profile", { public: true });
          setL((s) => (s ? { ...s, marktplatz_profil_oeffentlich: true } : s));
        } catch (e) {
          toast.error(errMsg(e, "Das Marktplatz-Profil konnte nicht öffentlich geschaltet werden"));
          return;
        }
      }
      if (!(await save())) return;           // zuerst aktuellen Stand sichern (v.a. Preis)
      try {
        const r = await api.post(`/resale/${l.id}/publish`, { visibility });
        if (r.data?.hinweis) toast.warning(r.data.hinweis);
        else toast.success(visibility === "private"
          ? "Für dein Netzwerk veröffentlicht" : "Auf dem Marktplatz veröffentlicht");
        await load();
      } catch (e) {
        // 402 = kein Verkaufspaket / Kontingent voll -> aussagekräftige Meldung
        toast.error(errMsg(e, "Veröffentlichen nicht möglich"));
      }
    } finally {
      statusLaeuft.current = false;
      setStatusBusy(false);
    }
  };

  // Nachpruefung 20.09.2026 (Nr. 52/53): Hier wurden bis zu 20 Fotos
  // UNVERKLEINERT als Base64 gelesen und in EINER Anfrage geschickt.
  // Ein Handyfoto hat oft 5-8 MB, Base64 macht daraus rund ein Drittel
  // mehr — schon drei Fotos sprengten die 25 MB, die nginx je Anfrage
  // durchlaesst (deploy/nginx.conf), und der Nutzer sah nur einen
  // unverstaendlichen Fehler. Jetzt wird jedes Foto im Browser auf 2000 px
  // verkleinert (dabei fallen auch Aufnahmeort und Geraet weg) und in
  // kleinen Paketen hochgeladen.
  const FOTOS_JE_PAKET = 4;
  // Pruefbericht 20.09.2026 (DP-01/U1): Pakete zusaetzlich nach GROESSE —
  // scheiterte die Verkleinerung (HEIC, defektes EXIF), ging das Original
  // (6-8 MB) mit, und vier davon sprengten die 25 MB des Proxys.
  const PAKET_ZEICHEN_MAX = 15_000_000;
  const EINZELFOTO_ZEICHEN_MAX = 12_000_000;
  // Regeln vom 20.09.2026 (Ahmad) — dieselben Zahlen wie im Server
  // (routes/resale.py: INSERAT_BESCHREIBUNG_MAX / INSERAT_FOTOS_MAX).
  const BESCHREIBUNG_MAX = 500;
  const FOTOS_MAX = 10;

  const uploadPhotos = async (files) => {
    if (!files?.length || ladeHoch) return;
    // 20.09.2026 (Ahmad): hoechstens FOTOS_MAX je Inserat. Lieber hier
    // abschneiden und es sagen, als den Server 400 werfen lassen, nachdem
    // der Nutzer zehn Fotos hochgeladen hat.
    const frei = FOTOS_MAX - (l.photos?.uploaded_keys || []).length;
    if (frei <= 0) {
      toast.error(`Dieses Inserat hat schon ${FOTOS_MAX} Fotos — bitte zuerst eines entfernen.`);
      return;
    }
    const auswahl = [...files].slice(0, frei);
    if (files.length > frei) {
      toast.message(`Es werden ${frei} von ${files.length} Fotos übernommen (maximal ${FOTOS_MAX} je Inserat).`);
    }
    setLadeHoch(true);
    let fertig = 0;
    let gesamt = 0;
    let heic = 0;
    const abgelehnt = [];                  // RP-533: vom Server übersprungene Fotos
    try {
      const photos = [];                   // { bild, name }
      let zuGross = 0;
      for (const f of auswahl) {
        // RP-533: HEIC u. ä. kann der Browser nicht umwandeln und der Server
        // nicht annehmen — die Datei überspringen statt die ganze Auswahl
        // abzubrechen. Andere Fehler brechen wie bisher ab.
        let bild;
        try {
          bild = await verkleinereBildDatei(f);
        } catch (err) {
          if (err?.name === "BildFormatFehler") { heic += 1; continue; }
          throw err;
        }
        if (String(bild || "").length > EINZELFOTO_ZEICHEN_MAX) { zuGross += 1; continue; }
        photos.push({ bild, name: f?.name || "" });
      }
      if (zuGross) {
        toast.warning(`${zuGross} Foto(s) zu groß und nicht verkleinerbar — bitte als JPG aufnehmen oder speichern.`);
      }
      gesamt = photos.length;
      // Pakete: hoechstens FOTOS_JE_PAKET Fotos UND hoechstens PAKET_ZEICHEN_MAX.
      const pakete = [];
      let aktuell = [];
      let groesse = 0;
      for (const foto of photos) {
        const n = String(foto.bild).length;
        if (aktuell.length && (aktuell.length >= FOTOS_JE_PAKET || groesse + n > PAKET_ZEICHEN_MAX)) {
          pakete.push(aktuell);
          aktuell = [];
          groesse = 0;
        }
        aktuell.push(foto);
        groesse += n;
      }
      if (aktuell.length) pakete.push(aktuell);
      for (const paket of pakete) {
        const r = await api.post(`/resale/${l.id}/photos`, { photos_b64: paket.map((p) => p.bild) });
        // RP-533: einzelne unbrauchbare Fotos lehnt der Server jetzt je Foto
        // ab (statt das ganze Paket) — die Stelle im Paket nennt die Datei.
        const weg = Array.isArray(r?.data?.abgelehnt) ? r.data.abgelehnt : [];
        weg.forEach((a) => abgelehnt.push({ name: paket[a?.index]?.name || "", grund: a?.grund || "" }));
        fertig += paket.length - weg.length;
      }
      if (fertig) toast.success(`${fertig} Foto(s) hochgeladen`);
    } catch (e) {
      const grund = e?.response?.status === 413
        ? "Die Fotos sind zu groß für eine Übertragung — bitte weniger Fotos auf einmal hochladen."
        : errMsg(e);
      // RP-045/144 (1): Scheiterte ein späteres Paket, fehlte der Hinweis,
      // dass die ersten schon oben sind — und die Ansicht zeigte sie nicht.
      toast.error(fertig ? `${fertig} von ${gesamt} Fotos hochgeladen, der Rest nicht: ${grund}` : grund);
    } finally {
      const hinweis = fotoAblehnungText(heic, abgelehnt);
      if (hinweis) toast.warning(hinweis);
      // RP-522: nur die Foto-Felder nachladen (auch nach einem Teilfehler) —
      // vor dem Freigeben, damit ein Speichern den neuen Stand mitschickt.
      await fotosNeuLaden();
      setLadeHoch(false);
    }
  };

  // Runde 21: Fotos aus dem Abholbericht (z.B. Schaeden) mit einem Klick uebernehmen.
  const fahrerfotosUebernehmen = async () => {
    if (abholBusy) return;                 // Doppelklick-Sperre
    setAbholBusy(true);
    try {
      const r = await api.post(`/resale/${l.id}/photos/aus-abholbericht`, {});
      // RP-090/340: übersprungene (Datei weg) und nicht mehr passende Fotos nennen
      const zusatz = [
        r.data?.fehlend ? `${r.data.fehlend} nicht mehr vorhanden` : "",
        r.data?.kein_platz ? `${r.data.kein_platz} ohne freien Platz (max. ${FOTOS_MAX})` : "",
      ].filter(Boolean).join(", ");
      toast.success(`${r.data?.uebernommen || 0} Foto(s) vom Fahrer übernommen${zusatz ? ` — ${zusatz}` : ""}`);
    } catch (e) { toast.error(errMsg(e)); }
    finally {
      await fotosNeuLaden();               // RP-522: Eingaben bleiben stehen
      setAbholBusy(false);
    }
  };

  // RP-469: Reihenfolge / Titelbild (das erste Foto ist das Titelbild).
  const fotoVerschieben = async (key, richtung) => {
    const neu = fotoReihenfolge(l.photos?.uploaded_keys, key, richtung);
    if (!neu || fotoBusy) return;
    setFotoBusy(true);
    try {
      const r = await api.post(`/resale/${l.id}/photos/reihenfolge`, { keys: neu });
      setL((s) => ({ ...s, photos: { ...s.photos, uploaded_keys: r.data?.uploaded_keys || neu } }));
      if (richtung === "titel") toast.success("Titelbild geändert");
    } catch (e) { toast.error(errMsg(e)); }
    finally {
      await fotosNeuLaden();
      setFotoBusy(false);
    }
  };

  const margin = l.margin || {};
  // Rollenprüfung 22.09.2026 (RP-057 b): Einkaufspreis noch nicht eindeutig
  const einkaufOffen = einkaufspreisOffen(margin);
  const inputCls ="w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };
  const removePhoto = async (which) => {
    if (!window.confirm(which.key
        ? "Dieses hochgeladene Bild endgültig löschen?"
        : "Dieses Einkaufsfoto aus dem Inserat entfernen?\n(Das Original bleibt in der Fahrzeugakte.)")) return;
    if (fotoBusy) return;
    setFotoBusy(true);
    try {
      const r = await api.post(`/resale/${l.id}/photos/remove`, which);
      setL((s) => ({ ...s, photos: { ...s.photos,
        ...(r.data.uploaded_keys ? { uploaded_keys: r.data.uploaded_keys } : {}),
        ...(r.data.einkauf_urls ? { einkauf_urls: r.data.einkauf_urls } : {}) } }));
      toast.success("Bild entfernt" + (l.status === "veroeffentlicht" ? " — Änderung ist sofort live" : ""));
      // Pruefbericht 20.09.2026 (U-96): die Vorschaubilder (einkauf_thumbs)
      // haengen am Index — ohne Neuladen waren sie danach verschoben.
      // RP-522: nur die Foto-Felder, ungespeicherte Eingaben bleiben.
      await fotosNeuLaden();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setFotoBusy(false); }
  };

  const einkaufFotos = l.photos?.einkauf_urls || [];
  const uploadedKeys = l.photos?.uploaded_keys || [];
  // Signierte, kurzlebige Links (Audit 09/2026) — Fallback nur fuer alte Antworten
  const fotoUrl = (k) => (l.photo_urls || []).find((p) => p.key === k)?.url || `${backend}/api/files/${k}`;
  const mode = l.photos?.mode || "einkauf";
  const abgeschlossen = ["verkauft", "geloescht"].includes(l.status);
  // RP-468: Laufzeit ab der ersten Veröffentlichung
  const laufzeit = laufzeitInfo(l.laeuft_ab_am ?? l.laufzeit_bis);
  const erneutGesperrt = !!laufzeit?.abgelaufen && ["zurueckgezogen", "verkaufsbereit"].includes(l.status);
  // RP-037: während einer Reservierung sind Preis, Daten und Mängel gesperrt
  const gesperrt = l.status === "verkauft" || reserviert;

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="inserat-page">
      <Link to={`/app/akte/${l.vehicle_id}`} onClick={wegNavigieren}
            className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
        <ArrowLeft size={14} /> Zur Fahrzeugakte
      </Link>
      {unterlagen && (unterlagen.protocols.length > 0 || unterlagen.contracts.length > 0) && (
        <div className="mt-3 rounded-xl border px-4 py-3" data-testid="inserat-unterlagen"
             style={{ borderColor: "var(--border-default)" }}>
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">
            Unterlagen zum Auto — nur für dich, nie im Marktplatz sichtbar
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {unterlagen.protocols.map((p) => (
              <button key={p.id} type="button" data-testid={`inserat-protokoll-${p.id}`}
                      onClick={() => openAuthedFile(`/protocols/${p.id}.pdf`)
                        .catch(() => toast.error("Protokoll konnte nicht geladen werden"))}
                      className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-zinc-200 hover:bg-white/5"
                      style={{ borderColor: "var(--border-default)" }}>
                <PenLine size={13} className="text-[color:var(--accent-green,#34c759)]" />
                Abhol-Protokoll (unterschrieben){p.version > 1 ? ` v${p.version}` : ""}
              </button>
            ))}
            {unterlagen.contracts.map((c, i) => (
              <button key={c.id || i} type="button" data-testid={`inserat-vertrag-${c.id}`}
                      onClick={() => openContractPdf(c.id)
                        .catch((e) => toast.error(errMsg(e, "Kaufvertrag konnte nicht geladen werden")))}
                      className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-zinc-200 hover:bg-white/5"
                      style={{ borderColor: "var(--border-default)" }}>
                <FileText size={13} />
                {/* Prüfbericht 20.09. U-125: ohne id nicht abstürzen */}
                Kaufvertrag {c.contract_no || String(c.id || "").slice(0, 8) || "ohne Nummer"}{i === 0 ? " · aktuelle Fassung" : ""}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="overline">Verkaufsinserat · {STATUS_LABELS[l.status] || l.status}</div>
          <h1 className="font-display font-black text-2xl tracking-tighter mt-1">Inserat bearbeiten</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {l.status === "entwurf" && (
            <button onClick={() => setStatus("verkaufsbereit")} disabled={statusGesperrt}
                    className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                    style={{ background: "var(--accent-red)" }}>
              <CheckCircle2 size={16} /> Verkaufsbereit machen
            </button>
          )}
          {l.status === "verkaufsbereit" && (
            <>
              <button onClick={() => publish("public")} disabled={statusGesperrt || erneutGesperrt}
                      className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                      style={{ background: "var(--accent-red)" }}>
                <Globe size={16} /> Öffentlich veröffentlichen
              </button>
              {/* Prüfbericht 20.09. M-18: Statusknöpfe mindestens 44 px hoch;
                  der Rückschritt (Entwurf / vom Marktplatz) steht abgesetzt
                  vom grünen "Verkauft" — auf dem Handy in eigener Zeile. */}
              <button onClick={() => publish("private")} disabled={statusGesperrt || erneutGesperrt}
                      title="Nur für eingeladene Netzwerk-Partner sichtbar"
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 min-h-[44px] text-xs font-semibold border disabled:opacity-50"
                      style={st}>
                <EyeOff size={14} /> Nur Netzwerk (privat)
              </button>
              <button onClick={reservierenVonHand} disabled={statusGesperrt} className="rounded-xl px-3 py-2 min-h-[44px] text-xs border disabled:opacity-50" style={st}>Reservieren</button>
              <button onClick={() => {
                        verkauftMelden();
                      }} disabled={statusGesperrt}
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 min-h-[44px] text-xs font-semibold text-white disabled:opacity-50"
                      style={{ background: "var(--st-gruen)" }}>
                <Tag size={13} /> Verkauft
              </button>
              <button onClick={() => setStatus("entwurf")} disabled={statusGesperrt} data-testid="inserat-zurueck-entwurf" className="basis-full sm:basis-auto sm:ml-auto rounded-xl px-3 py-2 min-h-[44px] text-xs text-zinc-400 hover:text-white inline-flex items-center gap-1 disabled:opacity-50"><Undo2 size={13} /> Zurück zu Entwurf</button>
            </>
          )}
          {l.status === "veroeffentlicht" && (
            <>
              <span className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold"
                    style={{ background: "#34c75920", color: "var(--st-gruen)", border: "1px solid #34c75955" }}>
                <Globe size={14} /> {l.visibility === "private" ? "Live für dein Netzwerk" : "Live auf dem Marktplatz"}
              </span>
              <button onClick={reservierenVonHand} disabled={statusGesperrt} className="rounded-xl px-3 py-2 min-h-[44px] text-xs border disabled:opacity-50" style={st}>Reservieren</button>
              <button onClick={() => {
                        verkauftMelden();
                      }} disabled={statusGesperrt}
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 min-h-[44px] text-xs font-semibold text-white disabled:opacity-50"
                      style={{ background: "var(--st-gruen)" }}>
                <Tag size={13} /> Verkauft
              </button>
              <button onClick={() => setStatus("zurueckgezogen")} disabled={statusGesperrt} data-testid="inserat-vom-marktplatz"
                      className="basis-full sm:basis-auto sm:ml-auto rounded-xl px-3 py-2 min-h-[44px] text-xs text-zinc-400 hover:text-white inline-flex items-center gap-1 disabled:opacity-50">
                <EyeOff size={13} /> Vom Marktplatz nehmen
              </button>
            </>
          )}
          {l.status === "zurueckgezogen" && (
            <>
              {/* RP-091/190/341: vorher immer publish("public") — ein privates
                  Netzwerk-Inserat wurde beim erneuten Veröffentlichen öffentlich.
                  Jetzt mit der bisherigen Sichtbarkeit, die andere als zweiter Knopf. */}
              <button onClick={() => publish(l.visibility === "private" ? "private" : "public")}
                      disabled={statusGesperrt || erneutGesperrt} data-testid="inserat-erneut-veroeffentlichen"
                      className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
                      style={{ background: "var(--accent-red)" }}>
                {l.visibility === "private" ? <EyeOff size={16} /> : <Globe size={16} />}
                {l.visibility === "private" ? " Erneut veröffentlichen (nur Netzwerk)" : " Erneut veröffentlichen (öffentlich)"}
              </button>
              <button onClick={() => publish(l.visibility === "private" ? "public" : "private")}
                      disabled={statusGesperrt || erneutGesperrt}
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 min-h-[44px] text-xs font-semibold border disabled:opacity-50"
                      style={st}>
                {l.visibility === "private" ? <><Globe size={14} /> Stattdessen öffentlich</> : <><EyeOff size={14} /> Stattdessen nur Netzwerk</>}
              </button>
              <button onClick={() => setStatus("verkaufsbereit")} disabled={statusGesperrt} className="rounded-xl px-3 py-2 min-h-[44px] text-xs border disabled:opacity-50" style={st}>Auf „verkaufsbereit" setzen</button>
            </>
          )}
          {l.status === "reserviert" && (
            <>
              <button onClick={() => {
                        verkauftMelden();
                      }} disabled={statusGesperrt}
                      className="rounded-xl px-4 py-2.5 min-h-[44px] text-sm font-semibold text-white disabled:opacity-50" style={{ background: "var(--st-gruen)" }}>
                Als verkauft markieren
              </button>
              <button onClick={reservierungAufheben} disabled={statusGesperrt} data-testid="inserat-reservierung-aufheben"
                      className="rounded-xl px-3 py-2 min-h-[44px] text-xs border disabled:opacity-50" style={st}>Reservierung aufheben</button>
            </>
          )}
        </div>
      </div>

      {laufzeit && ["veroeffentlicht", "zurueckgezogen", "verkaufsbereit"].includes(l.status) && (
        <div className="mt-3 text-[12px]" data-testid="inserat-laufzeit"
             style={{ color: laufzeit.abgelaufen ? "var(--st-rot)" : "var(--text-muted)" }}>
          {laufzeit.abgelaufen
            ? (l.status === "veroeffentlicht"
              ? "Die Laufzeit ist abgelaufen — das Inserat wird in Kürze automatisch entfernt."
              : "Die Laufzeit (seit der ersten Veröffentlichung) ist abgelaufen — erneut veröffentlichen geht nicht mehr. Du kannst das Inserat löschen und aus der Fahrzeugakte neu anlegen.")
            : `Läuft ab am ${laufzeit.datum} (noch ${laufzeit.tage} Tag${laufzeit.tage === 1 ? "" : "e"}) — gezählt ab der ersten Veröffentlichung, danach wird das Inserat automatisch entfernt und laufende Kaufanfragen enden.`}
        </div>
      )}
      {reserviert && (
        <div className="mt-3 text-[12px]" data-testid="inserat-reserviert-hinweis" style={{ color: "var(--text-muted)" }}>
          {l.reserviert_manuell ? "Von Hand reserviert." : "Für einen Käufer vom Marktplatz reserviert."}
          {typeof l.vereinbarter_preis === "number" ? ` Vereinbarter Preis: ${preisText(l.vereinbarter_preis)}.` : ""}
          {" "}Preis, Fahrzeugdaten und Mängel bleiben während der Reservierung unverändert; Titel,
          Beschreibung und Fotos kannst du weiter bearbeiten.
        </div>
      )}

      {(l.auto_notes || []).length > 0 && (
        <div className="mt-3 rounded-xl border px-4 py-3 text-xs space-y-0.5"
             style={{ borderColor: "#0ea5e955", background: "#0ea5e914", color: "var(--tx-cyan)" }}>
          {l.auto_notes.map((n, i) => <div key={i}>ℹ {n}</div>)}
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-4 mt-4">
        {/* Linke Spalte: Inhalt */}
        <div className="lg:col-span-2 space-y-4">
          <div className="tactical-card p-4">
            <label className="text-[11px] text-zinc-500">Titel</label>
            <input value={l.title || ""} onChange={set("title")} className={inputCls} style={st}
                   disabled={l.status === "verkauft"} />
            <div className="mt-3 flex items-baseline justify-between">
              <label className="text-[11px] text-zinc-500">Beschreibung</label>
              <span className={`text-[11px] ${(l.description || "").length > BESCHREIBUNG_MAX
                ? "text-red-400 font-semibold" : "text-zinc-500"}`}
                    data-testid="beschreibung-zaehler">
                {(l.description || "").length} / {BESCHREIBUNG_MAX}
              </span>
            </div>
            <textarea value={l.description || ""} onChange={set("description")} rows={7}
                      maxLength={BESCHREIBUNG_MAX}
                      className={inputCls} style={st} disabled={l.status === "verkauft"} />
            <label className="text-[11px] text-zinc-500 mt-3 block">Bekannte Mängel (eine je Zeile)</label>
            <textarea value={(l.known_defects || []).join("\n")}
                      onChange={(e) => setL((s) => ({ ...s, known_defects: e.target.value.split("\n") }))}
                      rows={4} className={inputCls} style={st} disabled={gesperrt} />
          </div>

          {/* Fahrzeugdaten: 1:1 aus der Akte übernommen — vor Veröffentlichung
              prüfbar/korrigierbar; erscheinen so beim B2B-Käufer. */}
          <div className="tactical-card p-4">
            <div className="text-sm font-bold uppercase tracking-wide mb-3">Fahrzeugdaten</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {[
                ["make_label", "Marke"], ["model_label", "Modell"],
                ["first_registration", "Erstzulassung"], ["mileage", "Kilometerstand"],
                ["power_ps", "PS"], ["fuel_label", "Kraftstoff"],
                ["gearbox_label", "Getriebe"], ["color", "Farbe"],
                ["previous_owners", "Halter-Anzahl"],
              ].map(([k, label]) => (
                <div key={k}>
                  <label className="text-[11px] text-zinc-500">{label}</label>
                  {k === "first_registration" ? (
                    <MonatJahrEingabe value={String(l.data?.[k] ?? "")} disabled={gesperrt}
                                      onChange={(v) => setL((s) => ({ ...s, data: { ...s.data, [k]: v } }))}
                                      art="ez" className={inputCls} style={st} />
                  ) : k === "mileage" ? (
                    // RP-523: deutsch lesen ("150.000", "150 Tkm") und formatiert zeigen
                    <input value={typeof l.data?.mileage === "number"
                             ? l.data.mileage.toLocaleString("de-DE") : (l.data?.mileage ?? "")}
                           disabled={gesperrt} inputMode="numeric" placeholder="z. B. 150.000"
                           aria-invalid={!!kmFehler(l.data)} data-testid="inserat-km"
                           onChange={(e) => setL((s) => ({ ...s, data: { ...s.data, mileage: e.target.value } }))}
                           className={inputCls} style={st} />
                  ) : (
                    <input value={l.data?.[k] ?? ""} disabled={gesperrt}
                           onChange={(e) => setL((s) => ({ ...s, data: { ...s.data, [k]: e.target.value } }))}
                           className={inputCls} style={st} />
                  )}
                </div>
              ))}
              <div>
                <label className="text-[11px] text-zinc-500">Unfallfrei</label>
                <select value={l.data?.accident_free ?? ""} disabled={gesperrt}
                        onChange={(e) => setL((s) => ({ ...s, data: { ...s.data, accident_free: e.target.value } }))}
                        className={inputCls + " bg-[var(--bg-elevated)]"} style={st}>
                  <option value="">— bitte angeben —</option>
                  <option value="Ja">Ja</option>
                  <option value="Nein">Nein</option>
                </select>
              </div>
            </div>
            <div className="mt-2 text-[10px] text-zinc-600">
              Wird 1:1 aus dem Einkauf übernommen — bitte vor der Veröffentlichung prüfen.
            </div>
          </div>

          {/* Fotos */}
          <div className="tactical-card p-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-bold uppercase tracking-wide">Fotos</div>
              <button onClick={() => fileRef.current?.click()} disabled={ladeHoch || abgeschlossen}
                      className="inline-flex items-center gap-1.5 text-xs text-zinc-300 hover:text-white disabled:opacity-50">
                <Camera size={14} /> {ladeHoch ? "Wird hochgeladen…" : "Neue Fotos hochladen"}
              </button>
              <input ref={fileRef} type="file" accept="image/*" multiple className="hidden"
                     disabled={ladeHoch}
                     onChange={(e) => { const f = e.target.files; uploadPhotos(f ? [...f] : []); e.target.value = ""; }} />
            </div>
            {/* 20.09.2026 (Ahmad): Der Umschalter Einkauf/Neu/Beide ist weg.
                Fotos aus dem urspruenglichen Inserat werden nicht mehr
                uebernommen — sie gehoeren dem Verkaeufer bzw. dem Portal.
                Jedes Inserat braucht eigene Fotos. */}
            <div className="mt-2 text-[11px] text-zinc-500" data-testid="fotos-regel">
              {uploadedKeys.length} / {FOTOS_MAX} eigene Fotos
              {einkaufFotos.length > 0 && ` · ${einkaufFotos.length} aus dem alten Bestand`}
              {" · "}Fotos aus dem ursprünglichen Inserat werden nicht übernommen.
            </div>
            {(l.abholfotos || []).length > 0 && !["verkauft", "geloescht"].includes(l.status) && (
              <div className="mt-3 rounded-lg p-2.5 flex flex-wrap items-center gap-2" data-testid="abholfotos-hinweis"
                   style={{ background: "rgba(56,189,248,0.06)", border: "1px solid rgba(56,189,248,0.25)" }}>
                <button onClick={fahrerfotosUebernehmen} data-testid="abholfotos-uebernehmen" disabled={abholBusy}
                        className="inline-flex items-center gap-1.5 text-xs font-semibold text-sky-300 hover:text-sky-200">
                  <Camera size={14} /> {abholBusy ? "Wird übernommen…" : `${l.abholfotos.length} Foto${l.abholfotos.length === 1 ? "" : "s"} vom Fahrer übernehmen`}
                </button>
                <span className="text-[11px] text-zinc-500">
                  Aus dem Abholbericht, z.B. Schäden. Übernommene Fotos bleiben im Inserat, auch wenn der
                  Abholbericht seine Fotos nach {l.fahrerfoto_tage || 90} Tagen löscht.
                </span>
              </div>
            )}
            <div className="mt-3 grid grid-cols-4 sm:grid-cols-6 gap-2">
              {/* Prüfbericht 20.09. U-106: alle Einkaufsfotos zeigen — vorher
                  .slice(0, 12), Fotos ab Nr. 13 waren weder sichtbar noch
                  entfernbar, der Marktplatz lieferte sie aber aus. */}
              {(mode !== "neu") && einkaufFotos.map((u, i) => (
                <div key={`e${i}`} className="relative group">
                  <a href={u} target="_blank" rel="noreferrer" title="Foto in Originalgröße öffnen">
                    <img src={thumbSrc(l.einkauf_thumbs?.[i], u)} alt={`Foto ${i + 1} aus dem alten Bestand`} loading="lazy" referrerPolicy="no-referrer"
                         onError={(e) => thumbFehler(e, u)}
                         className="aspect-square w-full object-cover rounded-lg opacity-90 hover:opacity-100 cursor-zoom-in" />
                  </a>
                  <button onClick={() => removePhoto({ url: u })}
                          data-testid={`foto-del-e${i}`}
                          title="Bild aus dem Inserat entfernen (Original bleibt in der Akte)"
                          aria-label="Bild aus dem Inserat entfernen"
                          className="foto-aktion absolute top-1 right-1 w-9 h-9 rounded-full flex items-center justify-center text-white transition"
                          style={{ background: "rgba(0,0,0,0.7)" }}>
                    <X size={13} />
                  </button>
                </div>
              ))}
              {/* RP-532: eigene Fotos immer zeigen — Altinserate stehen noch auf
                  photos.mode "einkauf", dort waren sie unsichtbar und nicht löschbar. */}
              {uploadedKeys.map((k, i) => (
                <div key={k} className="relative group">
                  <a href={fotoUrl(k)} target="_blank" rel="noreferrer" title="Foto in Originalgröße öffnen">
                    <img src={fotoUrl(k)} alt={`Foto ${i + 1}`} onError={fotoFehler}
                         className="aspect-square w-full object-cover rounded-lg hover:opacity-90 cursor-zoom-in" />
                  </a>
                  {/* RP-469: Titelbild und Reihenfolge (das erste eigene Foto ist das Titelbild) */}
                  {i === 0 && uploadedKeys.length > 1 && (
                    <span className="absolute bottom-1 left-1 rounded px-1.5 py-0.5 text-[10px] font-semibold text-white"
                          style={{ background: "rgba(0,0,0,0.7)" }} data-testid="foto-titelbild">Titelbild</span>
                  )}
                  {!abgeschlossen && uploadedKeys.length > 1 && (
                    <div className="absolute bottom-1 right-1 flex gap-1">
                      {i > 0 && (
                        <button type="button" onClick={() => fotoVerschieben(k, "titel")} disabled={fotoBusy}
                                title="Als Titelbild verwenden" aria-label="Als Titelbild verwenden"
                                data-testid={`foto-titel-${k.slice(-8)}`}
                                className="foto-aktion w-8 h-8 rounded-full flex items-center justify-center text-white transition"
                                style={{ background: "rgba(0,0,0,0.7)" }}>
                          <Star size={13} />
                        </button>
                      )}
                      {i > 0 && (
                        <button type="button" onClick={() => fotoVerschieben(k, -1)} disabled={fotoBusy}
                                title="Nach vorne" aria-label="Foto nach vorne"
                                className="foto-aktion w-8 h-8 rounded-full flex items-center justify-center text-white transition"
                                style={{ background: "rgba(0,0,0,0.7)" }}>
                          <ChevronLeft size={14} />
                        </button>
                      )}
                      {i < uploadedKeys.length - 1 && (
                        <button type="button" onClick={() => fotoVerschieben(k, 1)} disabled={fotoBusy}
                                title="Nach hinten" aria-label="Foto nach hinten"
                                className="foto-aktion w-8 h-8 rounded-full flex items-center justify-center text-white transition"
                                style={{ background: "rgba(0,0,0,0.7)" }}>
                          <ChevronRight size={14} />
                        </button>
                      )}
                    </div>
                  )}
                  <button onClick={() => removePhoto({ key: k })}
                          data-testid={`foto-del-${k.slice(-8)}`}
                          title="Bild endgültig löschen"
                          aria-label="Bild endgültig löschen"
                          className="foto-aktion absolute top-1 right-1 w-9 h-9 rounded-full flex items-center justify-center text-white transition"
                          style={{ background: "rgba(0,0,0,0.7)" }}>
                    <X size={13} />
                  </button>
                </div>
              ))}
              {uploadedKeys.length === 0 && (mode === "neu" || einkaufFotos.length === 0) && (
                <div className="col-span-full text-xs text-zinc-500 py-4">Noch keine neuen Fotos hochgeladen.</div>
              )}
            </div>
          </div>
        </div>

        {/* Rechte Spalte: Preise + Marge */}
        <div className="space-y-4">
          <div className="tactical-card p-4">
            <div className="text-sm font-bold uppercase tracking-wide mb-2">Preise</div>
            <label className="text-[11px] text-zinc-500">Verkaufspreis (öffentlich) *</label>
            <input type="text" inputMode="decimal" value={preisEingabe.public ?? ""} onChange={setPrice("public")}
                   aria-invalid={preisFehler.includes("public")}
                   className={inputCls} style={st} placeholder="20.900" disabled={gesperrt} />
            <label className="text-[11px] text-zinc-500 mt-2 block">B2B-Preis (optional)</label>
            <input type="text" inputMode="decimal" value={preisEingabe.b2b ?? ""} onChange={setPrice("b2b")}
                   aria-invalid={preisFehler.includes("b2b")}
                   className={inputCls} style={st} disabled={gesperrt} />
            <label className="text-[11px] text-zinc-500 mt-2 block">Privater Netzwerkpreis (optional)</label>
            <input type="text" inputMode="decimal" value={preisEingabe.network ?? ""} onChange={setPrice("network")}
                   aria-invalid={preisFehler.includes("network")}
                   className={inputCls} style={st} disabled={gesperrt} />
            {/* RP-458: geleerte optionale Preise werden jetzt wirklich entfernt */}
            <div className="mt-1 text-[10px] text-zinc-600">Feld leeren und speichern entfernt einen optionalen Preis.</div>
            {preisStufenHinweis(l.prices) && (
              <div className="mt-2 text-[11px]" data-testid="inserat-preisstufen-hinweis"
                   style={{ color: "var(--tx-amber)" }}>
                {preisStufenHinweis(l.prices)}
              </div>
            )}
          </div>

          <div className="tactical-card p-4">
            <div className="text-sm font-bold uppercase tracking-wide mb-2">Kalkulation</div>
            <div className="space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-500">
                  Einkaufspreis
                  {margin.purchase_price_quelle === "vertrag" && <span className="ml-1 text-[11px] text-zinc-500">(aus dem Kaufvertrag)</span>}
                  {margin.purchase_price_quelle === "abgeholt" && <span className="ml-1 text-[11px] text-zinc-500">(bei Abholung)</span>}
                  {margin.purchase_price_quelle === "fahrzeug" && <span className="ml-1 text-[11px] text-zinc-500">(Fahrzeugakte)</span>}
                </span>
                <span data-testid="kalkulation-einkaufspreis">{einkaufOffen ? "offen" : fmtEur(margin.purchase_price)}</span>
              </div>
              {/* Rollenprüfung 22.09.2026 (RP-057 b): mehrere offene Kaufverträge
                  mit verschiedenen Preisen — welcher gilt, zeigt erst die Abholung. */}
              {einkaufOffen && (
                <div className="text-[11px]" data-testid="kalkulation-einkauf-offen"
                     style={{ color: "var(--tx-amber)" }}>
                  Einkaufspreis offen: mehrere Kaufverträge mit verschiedenen Preisen.
                  Er steht fest, sobald das Fahrzeug abgeholt ist.
                </div>
              )}
              <div className="flex justify-between"><span className="text-zinc-500">Kosten gesamt</span><span>{fmtEur(margin.costs_total)}</span></div>
              <div className="flex justify-between border-t pt-1" style={st}><span className="text-zinc-500">Gesamtkosten</span><span>{fmtEur(margin.total_cost)}</span></div>
              <div className="flex justify-between text-base font-bold pt-1">
                {/* RP-459: brutto gegen brutto, ohne Steuer — ehrlich beschriften */}
                <span>{l.status === "verkauft" ? "Rohertrag" : "Erwarteter Rohertrag"}</span>
                {(() => {
                  // M39: Farbe und angezeigter Wert aus DERSELBEN Zahl — vorher
                  // war ein Verlustgeschaeft nach dem Verkauf gruen.
                  // RP-057 b: ohne feststehenden Einkaufspreis kein Rohertrag
                  const marge = einkaufOffen
                    ? null
                    : l.status === "verkauft" && l.sold_price != null
                      ? l.sold_price - (margin.total_cost || 0)
                      : margin.expected_margin;
                  return (
                    <span style={{ color: (marge ?? 0) >= 0 ? "var(--st-gruen)" : "var(--st-rot)" }}>
                      {fmtEur(marge)}
                    </span>
                  );
                })()}
              </div>
              {l.status === "verkauft" && (
                <div className="flex justify-between text-xs text-zinc-500">
                  <span>Verkauft für</span><span>{fmtEur(l.sold_price)}</span>
                </div>
              )}
            </div>
            <div className="mt-2 text-[10px] text-zinc-600">
              Kosten werden in der Fahrzeugakte gepflegt (Transport, Aufbereitung, …) und hier
              laufend übernommen. Rohertrag = Verkaufspreis minus Einkauf und Kosten, brutto —
              vor Umsatzsteuer bzw. §25a-Differenzsteuer.
            </div>
          </div>

          {l.status !== "verkauft" && (
            <button onClick={() => save()} disabled={busy || ladeHoch || abholBusy || fotoBusy}
                    data-testid="inserat-speichern"
                    className="w-full rounded-xl py-3 text-sm font-semibold border disabled:opacity-50" style={st}>
              {busy ? "Speichert…" : geaendert ? "Änderungen speichern •" : "Änderungen speichern"}
            </button>
          )}
          {geaendert && !busy && (
            <div className="-mt-2 text-center text-[11px]" data-testid="inserat-ungespeichert"
                 style={{ color: "var(--text-muted)" }}>Es gibt ungespeicherte Änderungen.</div>
          )}
          <AnfragenKarte listingId={id} onWeg={wegNavigieren} />

          {l.status !== "verkauft" && (
            <button onClick={removeListing}
                    className="w-full rounded-xl py-2.5 text-xs text-zinc-500 hover:text-red-400 inline-flex items-center justify-center gap-1.5">
              <Trash2 size={13} /> Inserat löschen
            </button>
          )}
        </div>
      </div>
    </div>
  );
}


/** Eingehende Marktplatz-Anfragen zu DIESEM Inserat (beantwortet werden
 *  sie zentral unter /app/anfragen). Sucher bekommen auf dem dealer-only
 *  Endpunkt 403 — die Karte bleibt dann einfach leer.
 *  Prüfbericht 20.09. U-118: jeder andere Fehler zeigt eine kleine Karte
 *  mit "Erneut versuchen" statt still zu verschwinden; eine späte Antwort
 *  eines früheren Inserats wird verworfen (aktiv-Merker). */
function AnfragenKarte({ listingId, onWeg }) {
  const [anfragen, setAnfragen] = useState(null);
  const [fehler, setFehler] = useState(false);
  const [versuch, setVersuch] = useState(0);
  useEffect(() => {
    let aktiv = true;
    setFehler(false);
    api.get("/dealer/interessen", { params: { listing_id: listingId } })
      .then((r) => { if (aktiv) setAnfragen(Array.isArray(r.data) ? r.data : []); })
      .catch((e) => {
        if (!aktiv) return;
        setAnfragen(null);
        setFehler(e?.response?.status !== 403);
      });
    return () => { aktiv = false; };
  }, [listingId, versuch]);
  if (fehler) {
    return (
      <div className="tactical-card p-4 text-[12.5px]" data-testid="inserat-anfragen-fehler"
           style={{ color: "var(--text-muted)" }}>
        Kaufanfragen konnten nicht geladen werden.{" "}
        <button type="button" onClick={() => setVersuch((n) => n + 1)}
                className="font-semibold hover:underline" style={{ color: "var(--accent-red)" }}>
          Erneut versuchen
        </button>
      </div>
    );
  }
  if (!anfragen || anfragen.length === 0) return null;
  const offen = anfragen.filter((a) => a.status === "offen").length;
  return (
    <div className="tactical-card p-4" data-testid="inserat-anfragen">
      <div className="text-sm font-bold uppercase tracking-wide mb-2">Kaufanfragen</div>
      <div className="space-y-1.5 text-sm">
        {anfragen.slice(0, 4).map((a) => (
          <div key={a.id} className="flex items-center justify-between gap-2">
            <span className="truncate" style={{ color: "var(--text-secondary)" }}>{a.buyer_name}</span>
            <span className="shrink-0 text-[12px]" style={{ color: "var(--text-muted)" }}>
              {a.offer != null ? `${Number(a.offer).toLocaleString("de-DE")} €` : "ohne Angebot"} · {ANFRAGE_STATUS[a.status] || a.status}
            </span>
          </div>
        ))}
      </div>
      <Link to="/app/anfragen" onClick={onWeg}
            className="mt-3 inline-block text-[12.5px] font-semibold hover:underline"
            style={{ color: "var(--accent-red)" }}>
        {offen > 0 ? `${offen} offene Anfrage(n) beantworten ›` : "Alle Anfragen ansehen ›"}
      </Link>
    </div>
  );
}
