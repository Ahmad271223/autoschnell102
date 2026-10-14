import { useEffect, useMemo, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  Calendar as CalIcon, FileText, Edit3, X, ChevronLeft, ChevronRight,
  Plus, MapPin, Phone, User as UserIcon, Trash2, Clock, Printer,
  ClipboardCheck, Download, Camera,
} from "lucide-react";
import {
  openContractPdf, printContractPdf,
  openPickupOrderPdf, printPickupOrderPdf, downloadPickupOrderPdf,
} from "@/lib/pdf";
import BeweisCard from "@/components/BeweisCard";
import PhotoGallery from "@/components/PhotoGallery";
import AbholberichtDialog from "@/components/AbholberichtDialog";
import { Link } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { useFreigabeZaehler } from "@/lib/freigaben";
import { preisAusText } from "@/lib/preis";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";
import {
  startOfMonth, endOfMonth, startOfWeek, endOfWeek, eachDayOfInterval,
  format, isSameMonth, isSameDay, addMonths, addDays, parseISO, isValid as isValidDate,
} from "date-fns";
import { de } from "date-fns/locale";

// Runde 16 (15.09.2026): alle serverseitig gueltigen Zustaende sind filterbar.
const STATUSES = ["offen", "bestätigt", "in Bearbeitung", "abgeholt", "nicht abgeholt",
                  "verschoben", "erledigt", "storniert"];

// Abgeschlossene Zustaende — wandern in der Liste automatisch nach unten
// (dieselbe Menge wie ABGESCHLOSSEN in backend/routes/appointments.py).
const ABGESCHLOSSEN = new Set(["abgeholt", "nicht abgeholt", "erledigt", "storniert"]);
// Pruefbericht 20.09.2026 (V-12): dieselben Mengen wie AUSGANG_ABGEHOLT /
// AUSGANG_ZURUECK in backend/routes/appointments.py ("erledigt" zaehlt beim
// Kauf wie "abgeholt").
const AUSGANG_ABGEHOLT = new Set(["abgeholt", "erledigt"]);
const AUSGANG_ZURUECK = new Set(["storniert", "nicht abgeholt"]);
// Pruefung 21.09.2026 (V-12): Fahrzeugzustaende NACH der Entscheidung
// "abgeholt" (wie _NACH_ABHOLUNG_WEITER im Backend) — dort setzt eine
// Stornierung nichts zurueck.
const NACH_ABHOLUNG_WEITER = new Set(["bestand", "verkaufsentwurf", "verkaufsbereit",
  "veroeffentlicht", "reserviert", "verkauft", "archiviert"]);
const AUSGANG_SPERRE = "Den Ausgang einer Abholung ändert nur der Hauptaccount";

/**
 * Pruefung 21.09.2026 (V-12): "erledigt" zaehlt nur dann wie "abgeholt", wenn
 * am Termin ein Kauf bzw. ein Fahrzeug haengt (wie ausgang_gilt_als_abgeholt
 * im Backend) — allgemeine Termine (Werkstatt, Besichtigung) bleiben frei.
 */
export function terminHatKauf(appt) {
  return !!(appt?.vehicle_id || appt?.contract_id || appt?.kaufvorgang_id);
}

function giltAlsAbgeholt(alt, hatKauf) {
  return AUSGANG_ABGEHOLT.has(alt) && (alt === "abgeholt" || !!hatKauf);
}

/**
 * Pruefung 21.09.2026 (V-12): Ein wieder geoeffneter Termin mit
 * unterschriebenem Protokoll gilt als erfolgte Abholung, solange das Fahrzeug
 * noch auf "abgeholt" (bzw. danach im Bestand/Weiterverkauf) steht — wie
 * abholung_beleg im Backend (dort: Kaufvorgang "abgeholt"). Wurde der Kauf
 * schon zurueckgenommen (Storno, danach wieder geoeffnet), ist nichts mehr
 * belegt. Zustand unbekannt: vorsichtshalber belegt.
 */
function wiederOffenBelegt(protokoll, { hatFahrzeug = true, lifecycle = null } = {}) {
  if (!protokoll) return false;
  if (!hatFahrzeug || !lifecycle) return true;
  return lifecycle === "abgeholt" || NACH_ABHOLUNG_WEITER.has(lifecycle);
}

/**
 * Pruefung 21.09.2026 (V-12): Der Merker ausgang_geaendert gilt nur, solange
 * der Termin noch in dem Status steht, den der Chef gesetzt hat — sonst zeigte
 * der Dialog einen veralteten Hinweis und sperrte Sucher dauerhaft.
 */
export function ausgangWirksam(appt) {
  const ag = appt?.ausgang_geaendert;
  return !!ag && typeof ag === "object" && (appt.status || "offen") === ag.nach_status;
}

/**
 * Darf dieses Konto den Termin auf `neu` setzen? Pruefbericht 20.09.2026
 * (H17): Das Raster bot jedem alle Zustaende an; fuer Sucher lieferten
 * einige davon 403, und danach zeigte der Dialog einen Status, der nie
 * gespeichert war. Dieselben Regeln wie im Backend (update_appointment):
 * Abgeschlossene Termine oeffnet nur der Chef wieder, und den Ausgang einer
 * Abholung ("abgeholt") aendert nur er. Rueckgabe: null = erlaubt, sonst
 * der Grund fuer den Tooltip.
 * Pruefbericht 20.09.2026 (V-12): wie im Backend auch bei "erledigt" (zaehlt
 * beim Kauf wie "abgeholt"), bei unterschriebenem Protokoll und nach einer
 * Ausgangs-Aenderung des Chefs (ausgang) — vorher waren die Knoepfe aktiv und
 * das Speichern endete in 403.
 * Pruefung 21.09.2026 (V-12): "erledigt" nur mit Kauf/Fahrzeug (hatKauf);
 * ein zur Korrektur wieder geoeffneter Termin mit Protokoll laesst sich vom
 * Sucher nicht stornieren / auf "nicht abgeholt" setzen; `ausgang` nur, solange
 * der Merker zum Status passt (ausgangWirksam).
 */
export function statusSperre(neu, alt, chef,
  { fahrer = false, protokoll = false, ausgang = false, hatKauf = true,
    hatFahrzeug = true, lifecycle = null } = {}) {
  // Pruefbericht 20.09.2026 (U-54): Mit eingeteiltem Fahrer entsteht
  // "abgeholt"/"erledigt" nur ueber sein unterschriebenes Protokoll — der
  // Server lehnt den Handweg ab, die Oberflaeche bot ihn trotzdem an.
  if (fahrer && !protokoll && neu !== alt && (neu === "abgeholt" || neu === "erledigt")) {
    return "Mit eingeteiltem Fahrer entsteht „abgeholt“ über das unterschriebene Abholprotokoll";
  }
  if (chef || !alt || neu === alt) return null;
  if (!ABGESCHLOSSEN.has(alt)) {
    return AUSGANG_ZURUECK.has(neu) && wiederOffenBelegt(protokoll, { hatFahrzeug, lifecycle })
      ? AUSGANG_SPERRE : null;
  }
  if (!ABGESCHLOSSEN.has(neu)) return "Abgeschlossene Termine öffnet nur der Hauptaccount wieder";
  if (giltAlsAbgeholt(alt, hatKauf) || protokoll || ausgang) return AUSGANG_SPERRE;
  return null;
}

/**
 * Pruefbericht 20.09.2026 (V-12), Entscheidung Ahmad 21.09.2026: Der Chef darf
 * eine abgeholte/erledigte Abholung nachtraeglich auf "storniert" bzw. "nicht
 * abgeholt" setzen — nur nach dieser Rueckfrage (der Server verlangt
 * ausgang_bestaetigt, sonst 409). Text der Rueckfrage, sonst null (keine
 * Rueckfrage noetig).
 * Pruefung 21.09.2026 (V-12): dieselben Faelle wie im Backend — auch ein zur
 * Korrektur wieder geoeffneter Termin mit Protokoll, "erledigt" nur mit
 * Kauf/Fahrzeug (hatKauf) oder Protokoll; `belegt` erzwingt die Rueckfrage,
 * wenn der Server sie verlangt (Kauf abgeholt, ohne protocol_id im Termin).
 * Der Text sagt nur zu, was das Backend tut: ohne Fahrzeug kein Fahrzeugteil,
 * im Bestand/Weiterverkauf bleibt das Fahrzeug samt Preis, ohne Kauf wird kein
 * Einkaufspreis entfernt; ist der Zustand unbekannt, bleibt die Aussage bedingt.
 */
export function ausgangFrage(alt, neu, hatProtokoll = false,
  { hatKauf = true, hatFahrzeug = true, lifecycle = null, belegt = false } = {}) {
  if (!AUSGANG_ZURUECK.has(neu) || !alt || alt === neu || AUSGANG_ZURUECK.has(alt)) return null;
  const wiederOffen = !ABGESCHLOSSEN.has(alt);
  const gefragt = belegt || giltAlsAbgeholt(alt, hatKauf)
    || (hatProtokoll && alt === "erledigt")
    || (wiederOffen && wiederOffenBelegt(hatProtokoll, { hatFahrzeug, lifecycle }));
  if (!gefragt) return null;
  const subjekt = hatKauf ? "gilt der Kauf nicht mehr als abgeholt"
    : "gilt die Abholung nicht mehr als erfolgt";
  let kauf;
  if (!hatFahrzeug) {
    kauf = `${subjekt},`;
  } else if (lifecycle && NACH_ABHOLUNG_WEITER.has(lifecycle)) {
    kauf = `${subjekt} – das Fahrzeug steht bereits im Bestand bzw. im Weiterverkauf und bleibt`
      + `${hatKauf ? " samt Einkaufspreis" : ""} unverändert (bitte dort entscheiden),`;
  } else if (lifecycle && lifecycle !== "abgeholt") {
    // Das Fahrzeug steht gar nicht (mehr) auf "abgeholt" — nichts zurueckzunehmen.
    kauf = `${subjekt},`;
  } else {
    const bedingung = lifecycle === "abgeholt"
      ? "sofern keine andere Abholung dieses Fahrzeugs abgeschlossen ist"
      : "sofern das Fahrzeug nicht schon im Bestand/Weiterverkauf steht und keine andere "
        + "Abholung dieses Fahrzeugs abgeschlossen ist";
    kauf = `${subjekt} – ${bedingung}, verschwindet das Fahrzeug aus „Entscheidung fällig“`
      + `${hatKauf ? " und der Einkaufspreis aus dieser Abholung wird entfernt" : ""},`;
  }
  const punkte = [kauf];
  if (hatProtokoll) {
    punkte.push("bleiben Protokoll und Kaufvertrag unverändert als Nachweis gespeichert,");
    // Nur bei "storniert" sperrt die Fahrer-App die Unterlagen sofort.
    if (neu === "storniert") punkte.push("sieht der Fahrer die Unterlagen dieser Fahrt nicht mehr,");
  }
  punkte.push("wird die Änderung mit deinem Namen und der Uhrzeit im Verlauf festgehalten.");
  let kopf;
  if (hatProtokoll && wiederOffen) {
    kopf = "Für diese Abholung gibt es ein unterschriebenes Abholprotokoll (der Termin ist zur "
      + "Korrektur wieder geöffnet).";
  } else if (hatProtokoll) {
    kopf = "Diese Abholung ist mit unterschriebenem Abholprotokoll abgeschlossen.";
  } else {
    kopf = "Diese Abholung ist bereits abgeschlossen.";
  }
  return `${kopf} Wenn du sie auf „${neu}“ setzt:\n`
    + punkte.map((p) => `• ${p}`).join("\n")
    + "\n\nWirklich ändern?";
}

/**
 * Rollenprüfung 22.09.2026 (RP-043/142): Hat der Nutzer im Dialog etwas
 * geändert? Ein Tipp neben das Fenster (am Handy schnell passiert) schloss den
 * Dialog bisher ohne Rückfrage — alle Eingaben waren weg. Leer, null und
 * „nicht gesetzt“ gelten als gleich (ein leeres Auswahlfeld ist keine Änderung).
 */
export function dialogGeaendert(a, appt) {
  const leer = (v) => v === null || v === undefined || v === "";
  const schluessel = new Set([...Object.keys(a || {}), ...Object.keys(appt || {})]);
  for (const k of schluessel) {
    const x = a?.[k];
    const y = appt?.[k];
    if (leer(x) && leer(y)) continue;
    if (typeof x === "object" || typeof y === "object") {
      if (JSON.stringify(x ?? null) !== JSON.stringify(y ?? null)) return true;
      continue;
    }
    if (String(x) !== String(y)) return true;
  }
  return false;
}

/**
 * Rollenprüfung 22.09.2026 (RP-418): „Sonstige Kosten“ in deutscher
 * Schreibweise. Das Feld war type="number" mit Number(...) — aus „1.200“
 * wurden 1,20 €. Jetzt über preisAusText (lib/preis.js).
 * Rückgabe { wert, fehler }: leer -> wert null; unlesbar -> fehler true.
 */
export function kostenAusEingabe(text) {
  const t = String(text ?? "").trim();
  if (!t) return { wert: null, fehler: false };
  const zahl = preisAusText(t);
  return zahl === null ? { wert: null, fehler: true } : { wert: zahl, fehler: false };
}

/** Gespeicherten Betrag für das Textfeld deutsch zeigen („1.200,5“). */
export function kostenAlsText(wert) {
  if (wert === null || wert === undefined || wert === "") return "";
  const zahl = Number(wert);
  if (!Number.isFinite(zahl)) return "";
  return new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 }).format(zahl);
}

// Rollenprüfung 22.09.2026 (RP-464): stilles Nachladen der Terminliste.
export const TERMINE_NACHLADEN_MS = 60000;

/**
 * Rollenprüfung 22.09.2026 (RP-464): Offene Termine, die der Fahrer abgelehnt
 * hat und die noch niemand neu zugeteilt hat — neueste Ablehnung zuerst.
 */
export function vomFahrerAbgelehnt(items) {
  return (Array.isArray(items) ? items : [])
    .filter((a) => a && a.zuteilung === "abgelehnt" && !a.driver_id
      && !ABGESCHLOSSEN.has(a.status || "offen"))
    .sort((x, y) => String(y.zuteilung_beantwortet_am || "")
      .localeCompare(String(x.zuteilung_beantwortet_am || "")));
}

/**
 * Rollenprüfung 22.09.2026 (RP-542): tel:-Link zur Fahrer-Telefonnummer
 * (nur Ziffern und ein führendes +); null, wenn nichts Wählbares bleibt.
 */
export function telHref(nummer) {
  const roh = String(nummer ?? "").trim();
  const ziffern = roh.replace(/[^\d+]/g, "").replace(/(?!^)\+/g, "");
  return ziffern.replace(/\D/g, "").length >= 3 ? `tel:${ziffern}` : null;
}

// Rollenprüfung 22.09.2026 (RP-482/075/174): Rückfrage, wenn der Server eine
// offene (abgebrochene) Protokoll-Korrektur meldet.
export const KORREKTUR_VERWERFEN_FRAGE = "Für diesen Termin ist eine Korrektur-Version des "
  + "Abholprotokolls offen, die der Fahrer nicht abgeschlossen hat.\n\n"
  + "Korrektur verwerfen und den Termin wieder auf „abgeholt“ setzen? Danach gilt wieder "
  + "die zuvor unterschriebene Fassung des Protokolls.";
// Rollenprüfung 22.09.2026 (RP-497): Rückfrage beim Löschen mit Abholbericht.
export const BERICHT_LOESCHEN_FRAGE = "Zu diesem Termin gibt es einen Abholbericht des Fahrers "
  + "(Fotos, Kilometerstand, Mängel). Empfohlen: den Termin stornieren statt löschen — dann "
  + "bleibt der Bericht erhalten.\n\nTrotzdem löschen? Der Bericht wird samt Fotos "
  + "unwiderruflich mitgelöscht.";

/**
 * Pruefbericht 20.09.2026 (U-63): Antwort von GET /drivers/{id}/conflicts
 * auswerten. Der Server liefert `count` (Gesamtzahl aus der Datenbank) und
 * `has_more` (Liste auf 50 gekuerzt) — gezaehlt wurde bisher nur die
 * gekuerzte Liste. Der eigene Termin zaehlt nicht mit.
 * Rueckgabe null (keine Doppelbuchung) oder { anzahl }.
 */
export function konfliktAuswerten(data, eigeneId) {
  const liste = Array.isArray(data?.conflicts) ? data.conflicts : [];
  const eigene = eigeneId ? liste.filter((c) => c?.id === eigeneId).length : 0;
  const andere = liste.length - eigene;
  const gesamt = Number(data?.count);
  const anzahl = Number.isFinite(gesamt) ? Math.max(gesamt - eigene, andere) : andere;
  return anzahl > 0 ? { anzahl } : null;
}

/** U-63: Text, wenn die Pruefung selbst scheitert (vorher stiller .catch). */
export function konfliktFehlerText(err) {
  return err?.response?.status === 404
    ? "Fahrer nicht mehr in deiner Liste — Doppelbuchung nicht prüfbar."
    : "Doppelbuchung konnte nicht geprüft werden.";
}

/**
 * Pruefbericht 20.09.2026 (U-90): "Bevorstehend" in der Monatsansicht — offene
 * Termine ab heute UND offene Termine ohne Datum (aus einem Vertrag ohne
 * Abholdatum angelegt). Die standen nur in der Listenansicht ("Ohne Datum");
 * am PC (Monatsansicht ist Standard) fehlten sie ganz. Ohne Datum zuerst —
 * sie brauchen eins.
 */
export function bevorstehend(items, heute, max = 8) {
  const offen = (Array.isArray(items) ? items : [])
    .filter((a) => a && !ABGESCHLOSSEN.has(a.status));
  const ohneDatum = offen.filter((a) => !a.pickup_date);
  const datiert = offen
    .filter((a) => a.pickup_date && a.pickup_date >= heute)
    .sort((x, y) => x.pickup_date.localeCompare(y.pickup_date)
      || (x.pickup_time || "").localeCompare(y.pickup_time || ""));
  return [...ohneDatum, ...datiert].slice(0, max);
}

/** Hinweis im Termin-Dialog, wenn der Ausgang nachtraeglich geaendert wurde. */
export function ausgangHinweis(ag) {
  if (!ag || typeof ag !== "object") return null;
  const am = safeParse(ag.am);
  const wann = am ? ` am ${format(am, "dd.MM.yyyy, HH:mm")} Uhr` : "";
  const wechsel = ag.von_status && ag.nach_status ? ` („${ag.von_status}“ → „${ag.nach_status}“)` : "";
  return `Ausgang der Abholung${wann} vom Hauptaccount nachträglich geändert${wechsel}`
    + (ag.protokoll_id ? " — das Abholprotokoll bleibt als Nachweis gespeichert." : ".");
}

const STATUS_META = {
  "offen":           { dot: "var(--st-blau)", chipClass: "st-offen-bg",         text: "st-offen" },
  "bestätigt":       { dot: "var(--st-himmel)", chipClass: "st-offen-bg",         text: "st-offen" },
  "in Bearbeitung":  { dot: "var(--st-gelb)", chipClass: "st-verschoben-bg",    text: "st-verschoben" },
  "storniert":       { dot: "var(--st-grau)", chipClass: "st-erledigt-bg",      text: "st-erledigt" },
  "abgeholt":        { dot: "var(--st-gruen)", chipClass: "st-abgeholt-bg",      text: "st-abgeholt" },
  "nicht abgeholt":  { dot: "var(--st-rot)", chipClass: "st-nicht-abgeholt-bg",text: "st-nicht-abgeholt" },
  "verschoben":      { dot: "var(--st-amber)", chipClass: "st-verschoben-bg",    text: "st-verschoben" },
  "erledigt":        { dot: "var(--st-grau)", chipClass: "st-erledigt-bg",      text: "st-erledigt" },
};

const safeParse = (s) => {
  if (!s) return null;
  try {
    const d = parseISO(s);
    return isValidDate(d) ? d : null;
  } catch { return null; }
};

export default function Termine() {
  const { user } = useAuth();
  const chef = user?.role === "dealer";
  const [items, setItems] = useState([]);
  const [drivers, setDrivers] = useState([]);
  // M-04: am Handy zeigt die Monatsansicht in 7 Spalten praktisch keinen
  // Text — dort startet die Liste (umschaltbar wie bisher).
  const [view, setView] = useState(() => {
    try { return window.matchMedia?.("(max-width: 639px)")?.matches ? "list" : "month"; }
    catch { return "month"; }
  });          // 'month' | 'list'
  const [cursor, setCursor] = useState(new Date());   // current month for month view
  const [selectedDay, setSelectedDay] = useState(new Date());
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState(null);       // appt being edited
  const [creating, setCreating] = useState(false);

  // Runde 16 (15.09.2026): Kuerzung (X-Truncated ab 2.000) und Ladefehler
  // sichtbar machen — vorher sah beides wie "keine Termine" aus.
  const [gekuerzt, setGekuerzt] = useState(false);
  // Pruefbericht 20.09.2026 (B10/M29): Es gab weder Lade- noch Fehlerzustand.
  // Ein 500 oder Funkloch zeigte "Keine Termine" — der Sucher legte seine
  // Abholungen ein zweites Mal an. "laedt" | "ok" | "fehler".
  const [ladeZustand, setLadeZustand] = useState("laedt");
  const [ladeFehler, setLadeFehler] = useState("");
  // M20: nur die Antwort der LETZTEN Anfrage zaehlt (schneller Filterwechsel).
  const anfrageNr = useRef(0);
  const load = async () => {
    const nr = ++anfrageNr.current;
    try {
      const r = await api.get("/appointments", { params: filter ? { status: filter } : {} });
      if (nr !== anfrageNr.current) return;
      setItems(Array.isArray(r.data) ? r.data : []);
      setGekuerzt(String(r.headers?.["x-truncated"] || "") === "1");
      setLadeZustand("ok");
      setLadeFehler("");
    } catch (e) {
      if (nr !== anfrageNr.current) return;
      setLadeZustand("fehler");
      setLadeFehler(errMsg(e, "Termine konnten nicht geladen werden"));
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);
  // Rollenprüfung 22.09.2026 (RP-464, Übergabe Fahrer-App): Die Liste lud nur
  // beim Öffnen — eine Ablehnung des Fahrers (oder seine Annahme) sah der Chef
  // erst nach manuellem Neuladen. Jetzt still alle 60 s (nur sichtbar) und
  // beim Zurückkehren auf die Seite (visibilitychange/focus), wie Freigaben.jsx.
  const ladenRef = useRef(load);
  useEffect(() => { ladenRef.current = load; });
  useEffect(() => {
    let zuletzt = 0;
    const sichtbar = () => {
      // focus und visibilitychange kommen beim Tabwechsel beide — nur einmal laden.
      if (document.visibilityState !== "visible" || Date.now() - zuletzt < 2000) return;
      zuletzt = Date.now();
      ladenRef.current();
    };
    const t = setInterval(sichtbar, TERMINE_NACHLADEN_MS);
    document.addEventListener("visibilitychange", sichtbar);
    window.addEventListener("focus", sichtbar);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", sichtbar);
      window.removeEventListener("focus", sichtbar);
    };
  }, []);
  const abgelehnt = useMemo(() => vomFahrerAbgelehnt(items), [items]);
  // H21: Ob die Fahrerliste da ist — sonst darf der Dialog einen schon
  // zugeteilten Fahrer nicht als "kein Fahrer" anzeigen (und beim Speichern
  // entfernen).
  const [fahrerGeladen, setFahrerGeladen] = useState(false);
  useEffect(() => {
    api.get("/drivers")
      .then((r) => { setDrivers(Array.isArray(r.data) ? r.data : []); setFahrerGeladen(true); })
      .catch((e) => toast.error(errMsg(e, "Fahrerliste konnte nicht geladen werden")));
  }, []);

  // Rueckgabe true = gespeichert (Dialog schliesst), false = Dialog bleibt.
  const save = async (a) => {
    try {
      if (a.id) {
        // Phase 2 (2.8): geladener Stand mit — wer auf einem veralteten Stand
        // speichert, bekommt 409 "bitte neu laden" statt den Kollegen zu überschreiben.
        const { data } = await api.put(`/appointments/${a.id}`,
          { ...a, ...(a.updated_at ? { stand: a.updated_at } : {}) });
        // Verschobenes Abholdatum uebernimmt der Server automatisch in den
        // bestehenden Kaufvertrag (gleiche Vertragsnummer, PDF wird neu
        // erzeugt). Frueher entstand hier per Rueckfrage ein ZWEITER
        // Vertrag — der alte blieb mit falschem Datum liegen.
        toast.success(data.contract_updated
          ? "Termin gespeichert — Kaufvertrag trägt jetzt das neue Abholdatum"
          : "Termin gespeichert");
        // Runde 15: Fahrer wurde waehrend des Speicherns aus der Firma entfernt
        if (data?.hinweis) toast.warning(data.hinweis, { duration: 8000 });
      } else {
        const { data } = await api.post(`/appointments`, a);
        toast.success("Termin angelegt");
        if (data?.hinweis) toast.warning(data.hinweis, { duration: 8000 });
      }
      setEditing(null);
      setCreating(false);
      load();
      return true;
    } catch (err) {
      const status = err?.response?.status;
      // Pruefung 21.09.2026 (V-12): Der Server verlangt die Rueckfrage zum
      // Ausgang, die der Dialog nicht gestellt hat (z. B. Kauf abgeholt ohne
      // Protokollverweis am Termin) — der Dialog fragt jetzt nach und
      // speichert mit Bestaetigung erneut. Kein Fehler-Toast.
      if (status === 409 && a.id && !a.ausgang_bestaetigt
          && /Rückfrage im Terminplaner/i.test(errMsg(err, ""))) {
        return "ausgang";
      }
      // Rollenprüfung 22.09.2026 (RP-482/075/174): offene Protokoll-Korrektur —
      // der Hauptaccount wird gefragt, ob sie verworfen werden soll (Sucher
      // bekommen die Meldung als Fehler, der Server lässt nur den Chef zu).
      if (status === 409 && a.id && chef && !a.korrektur_verwerfen
          && /Korrektur verwerfen/i.test(errMsg(err, ""))) {
        return "korrektur";
      }
      // Pruefbericht 20.09.2026 (B11): Beim veralteten Stand (409 "bitte neu
      // laden") behielt der Dialog seinen alten Stand samt updated_at — jeder
      // weitere Versuch scheiterte identisch, ohne Ausweg. Jetzt wird der
      // Termin frisch geladen und der Dialog damit neu aufgebaut.
      if (status === 409 && a.id && /neu laden/i.test(errMsg(err, ""))) {
        try {
          const { data: frisch } = await api.get(`/appointments/${a.id}`);
          setEditing(frisch);
          toast.error("Der Termin wurde inzwischen geändert (z. B. vom Fahrer). Der aktuelle Stand "
            + "ist jetzt geladen — bitte deine Änderung noch einmal eintragen und speichern.",
            { duration: 10000 });
        } catch {
          toast.error(errMsg(err, "Fehler beim Speichern"));
        }
        load();
        return false;
      }
      toast.error(errMsg(err, "Fehler beim Speichern"), { duration: 8000 });
      // M25: auch bei 403/404/503 den aktuellen Stand der Liste holen.
      load();
      return false;
    }
  };

  // Pruefbericht 20.09.2026 (B9/F12): await ohne Fehlerbehandlung — jeder
  // Backend-Fehler ("abgeschlossene Termine loescht nur der Hauptaccount",
  // "bitte zuerst stornieren") verschwand; der Dialog blieb einfach stehen.
  // Rollenprüfung 22.09.2026 (RP-497): mit berichtOk bestätigt der Chef
  // ausdrücklich, dass der Abholbericht des Fahrers mitgelöscht wird.
  const remove = async (id, berichtOk = false) => {
    if (!berichtOk && !window.confirm("Termin löschen?")) return false;
    try {
      await api.delete(`/appointments/${id}`,
        berichtOk ? { params: { bericht_loeschen: 1 } } : undefined);
      toast.success("Gelöscht");
      setEditing(null);
      load();
      return true;
    } catch (err) {
      if (err?.response?.status === 404) {
        toast.info("Den Termin gibt es schon nicht mehr.");
        setEditing(null);
        load();
        return true;
      }
      if (chef && !berichtOk && err?.response?.status === 409
          && /Abholbericht des Fahrers/i.test(errMsg(err, ""))) {
        return window.confirm(BERICHT_LOESCHEN_FRAGE) ? remove(id, true) : false;
      }
      toast.error(errMsg(err, "Termin konnte nicht gelöscht werden"), { duration: 10000 });
      return false;
    }
  };

  // Group appointments by day (yyyy-MM-dd)
  const apptsByDay = useMemo(() => {
    const m = new Map();
    for (const a of items) {
      const key = a.pickup_date || "";
      if (!key) continue;
      if (!m.has(key)) m.set(key, []);
      m.get(key).push(a);
    }
    // sort each day by time
    for (const [k, arr] of m.entries()) {
      arr.sort((x, y) => (x.pickup_time || "").localeCompare(y.pickup_time || ""));
    }
    return m;
  }, [items]);

  const daysOfMonth = useMemo(() => {
    const start = startOfWeek(startOfMonth(cursor), { weekStartsOn: 1 });
    const end = endOfWeek(endOfMonth(cursor), { weekStartsOn: 1 });
    return eachDayOfInterval({ start, end });
  }, [cursor]);

  const selectedKey = format(selectedDay, "yyyy-MM-dd");
  const selectedAppts = apptsByDay.get(selectedKey) || [];

  // M28: "Bevorstehend" nur fuer noch offene Termine — eine heute Morgen
  // erledigte Abholung ist keine anstehende Fahrt mehr.
  // U-90: undatierte offene Termine stehen mit drin (bevorstehend).
  const upcomingAppts = useMemo(
    () => bevorstehend(items, format(new Date(), "yyyy-MM-dd")), [items]);

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-[1480px] mx-auto" data-testid="termine-page">
      {gekuerzt && (
        <div className="mb-3 rounded-sm border px-4 py-2 text-sm" data-testid="termine-gekuerzt"
             style={{ borderColor: "var(--border-default)", color: "var(--text-muted)" }}>
          Die Liste zeigt nur die neuesten 2.000 Termine — bitte nach Status filtern, um ältere zu sehen.
        </div>
      )}
      {/* Runde 30: Abholprotokolle, die auf die Freigabe des Chefs warten.
          Runde 33: Sie haben eine eigene Seite — hier nur der Hinweis. */}
      <FreigabeHinweis />
      {/* Rollenprüfung 22.09.2026 (RP-464): Ablehnungen des Fahrers mit Grund
          im Klartext — vorher nur als Tooltip am einzelnen Termin. */}
      <AbgelehntHinweis liste={abgelehnt} onEdit={setEditing} />
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <div className="overline">Terminplaner</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">Abholtermine</h1>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {/* M-20: role=tab + aria-selected — der aktive Reiter war nur optisch erkennbar */}
          <div className="apple-segment" role="tablist" aria-label="Ansicht">
            <button type="button" onClick={() => setView("month")} data-testid="view-month"
                    role="tab" aria-selected={view === "month"}
                    className={`apple-segment-item ${view === "month" ? "active" : ""}`}>Monat</button>
            <button type="button" onClick={() => setView("list")} data-testid="view-list"
                    role="tab" aria-selected={view === "list"}
                    className={`apple-segment-item ${view === "list" ? "active" : ""}`}>Liste</button>
          </div>
          <button onClick={() => setCreating(true)} data-testid="new-appt-btn"
                  className="apple-btn apple-btn-primary">
            <Plus size={15} /> Neuer Termin
          </button>
        </div>
      </div>

      {/* Filter chips (always visible) */}
      <div className="flex flex-wrap gap-1.5 mb-6">
        {["", ...STATUSES].map((s) => (
          <button key={s || "all"} onClick={() => setFilter(s)}
                  data-testid={`filter-status-${s || "all"}`}
                  className={`px-3 py-1.5 tipp-40 rounded-full text-xs font-medium transition-colors border ${
                    filter === s
                      ? "bg-white/10 text-white border-white/15"
                      : "bg-white/[0.03] text-zinc-400 border-white/[0.06] hover:bg-white/[0.06] hover:text-white"
                  }`}>
            {s ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="cal-dot" style={{ background: STATUS_META[s]?.dot }} />
                {s}
              </span>
            ) : "Alle"}
          </button>
        ))}
      </div>

      {ladeZustand === "fehler" && (
        <div className="mb-5 rounded-xl border px-4 py-3 text-sm flex flex-wrap items-center gap-3" role="alert"
             data-testid="termine-ladefehler"
             style={{ borderColor: "#ef444455", background: "#ef444414", color: "var(--text-primary)" }}>
          <span className="flex-1 min-w-0">
            {ladeFehler}
            {items.length > 0 ? " — angezeigt ist der zuletzt geladene Stand." : " — deine Termine sind nicht weg."}
          </span>
          <button type="button" onClick={load} className="apple-btn apple-btn-secondary">Erneut versuchen</button>
        </div>
      )}

      {ladeZustand === "laedt" && items.length === 0 ? (
        <div className="apple-surface-gloss p-12 text-center text-zinc-500" data-testid="termine-laedt">
          Termine werden geladen…
        </div>
      ) : ladeZustand === "fehler" && items.length === 0 ? null : view === "month" ? (
        <MonthView
          cursor={cursor} setCursor={setCursor}
          days={daysOfMonth}
          apptsByDay={apptsByDay}
          selectedDay={selectedDay} setSelectedDay={setSelectedDay}
          selectedAppts={selectedAppts}
          upcomingAppts={upcomingAppts}
          onEdit={setEditing}
        />
      ) : (
        <ListView items={items} onEdit={setEditing} gekuerzt={gekuerzt} />
      )}

      {/* Floating + button (mobile-friendly) */}
      <button onClick={() => setCreating(true)} className="apple-fab lg:hidden" aria-label="Neu">
        <Plus size={22} />
      </button>

      {(editing || creating) && (
        <EditDialog
          // B11: neuer Stand vom Server (updated_at) -> Dialog neu aufbauen
          key={editing ? `${editing.id}-${editing.updated_at || ""}` : "neu"}
          appt={editing || { pickup_date: format(selectedDay, "yyyy-MM-dd"), status: "offen", title: "" }}
          drivers={drivers}
          fahrerGeladen={fahrerGeladen}
          chef={chef}
          isNew={creating}
          onClose={() => { setEditing(null); setCreating(false); }}
          onSave={save}
          onDelete={editing ? () => remove(editing.id) : null}
        />
      )}
    </div>
  );
}

/* ───────────────────────── Month View ───────────────────────── */

function MonthView({ cursor, setCursor, days, apptsByDay, selectedDay, setSelectedDay, selectedAppts, upcomingAppts, onEdit }) {
  return (
    // Rollenprüfung 22.09.2026 (RP-466): unter lg eine feste Spalte (grid-cols-1)
    // und min-w-0 — vorher bestimmte die min-content-Breite des Seitenpanels
    // (ein langer Termintitel mit truncate) die Spaltenbreite, und am Handy
    // lagen Monatswechsel sowie Fr/Sa/So ausserhalb des Bildes.
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_380px] gap-5">
      {/* Calendar */}
      <div className="apple-surface-gloss p-4 lg:p-5 min-w-0">
        <div className="flex items-center justify-between mb-4">
          <div className="font-display font-bold text-2xl lg:text-3xl tracking-tight">
            {format(cursor, "LLLL yyyy", { locale: de })}
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => setCursor(addMonths(cursor, -1))} data-testid="cal-prev"
                    className="apple-btn apple-btn-ghost !px-2.5"><ChevronLeft size={16} /></button>
            <button onClick={() => { setCursor(new Date()); setSelectedDay(new Date()); }} data-testid="cal-today"
                    className="apple-btn apple-btn-secondary">Heute</button>
            <button onClick={() => setCursor(addMonths(cursor, 1))} data-testid="cal-next"
                    className="apple-btn apple-btn-ghost !px-2.5"><ChevronRight size={16} /></button>
          </div>
        </div>

        <div className="cal-grid">
          {["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"].map((d) => (
            <div key={d} className="cal-weekday">{d}</div>
          ))}
          {days.map((d) => {
            const key = format(d, "yyyy-MM-dd");
            const dayAppts = apptsByDay.get(key) || [];
            const muted = !isSameMonth(d, cursor);
            const isToday = isSameDay(d, new Date());
            const isSelected = isSameDay(d, selectedDay);
            const wd = d.getDay();
            const visible = dayAppts.slice(0, 3);
            const more = dayAppts.length - visible.length;
            // Pruefbericht 20.09.2026 (M-05): Tageszelle als <button> — per
            // Tastatur waehlbar, aria-pressed = ausgewaehlt, Name mit Datum und
            // Terminzahl (Stil-Reset in index.css .cal-day). Die Chips bleiben
            // Maus-Abkuerzungen; per Tastatur oeffnet man den Termin ueber die
            // Tagesliste rechts.
            return (
              <button key={key} type="button"
                      data-testid={`cal-day-${key}`}
                      onClick={() => setSelectedDay(d)}
                      aria-pressed={isSelected}
                      aria-label={`${format(d, "EEEE, d. LLLL", { locale: de })}, ${dayAppts.length} Termin${dayAppts.length === 1 ? "" : "e"}`}
                      className={`cal-day ${muted ? "muted" : ""} ${isToday ? "is-today" : ""} ${isSelected ? "selected" : ""} ${wd === 0 || wd === 6 ? "is-weekend" : ""}`}>
                <div className="flex items-center justify-between">
                  <span className="cal-daynum">{format(d, "d")}</span>
                  {dayAppts.length > 0 && !visible.length && (
                    <span className="cal-dot" style={{ background: STATUS_META[dayAppts[0].status]?.dot || "var(--st-blau)" }} />
                  )}
                </div>
                <div className="flex flex-col gap-[3px] overflow-hidden">
                  {visible.map((a) => {
                    const meta = STATUS_META[a.status] || STATUS_META.offen;
                    return (
                      <div key={a.id}
                           onClick={(e) => { e.stopPropagation(); onEdit(a); }}
                           className={`cal-event ${meta.chipClass} ${meta.text}`}
                           title={`${a.pickup_time || ""} ${a.title}`}>
                        {a.pickup_time ? <span className="opacity-70 mr-1">{a.pickup_time}</span> : null}
                        <span className="text-white/90">{a.title}</span>
                      </div>
                    );
                  })}
                  {more > 0 && <div className="cal-event-more">+{more} weitere</div>}
                </div>
                {/* Handy-Ansicht (24.09.2026): unter 640 px sind die Textchips
                    ausgeblendet (index.css) — je Termin ein farbiger Punkt, der
                    Klartext steht in der Tagesliste unter dem Kalender. */}
                {dayAppts.length > 0 && (
                  <div className="cal-punkte" aria-hidden="true">
                    {dayAppts.slice(0, 4).map((a) => (
                      <span key={a.id} className="cal-dot"
                            style={{ background: (STATUS_META[a.status] || STATUS_META.offen).dot }} />
                    ))}
                    {dayAppts.length > 4 && <span className="cal-punkte-mehr">+{dayAppts.length - 4}</span>}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Side panel: selected day + upcoming */}
      <div className="space-y-5 min-w-0">
        <div className="apple-surface-gloss p-5" data-testid="day-panel">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="overline">{isSameDay(selectedDay, new Date()) ? "Heute" : "Ausgewählt"}</div>
              <div className="font-display font-bold text-xl mt-0.5">
                {format(selectedDay, "EEEE, d. LLLL", { locale: de })}
              </div>
            </div>
            <span className="text-xs text-zinc-500">{selectedAppts.length} Termin{selectedAppts.length !== 1 ? "e" : ""}</span>
          </div>
          {selectedAppts.length === 0 ? (
            <div className="text-center text-zinc-500 py-8">
              <CalIcon className="mx-auto mb-2 opacity-40" size={28} />
              <div className="text-sm">Keine Termine an diesem Tag.</div>
            </div>
          ) : (
            <div className="space-y-2">
              {selectedAppts.map((a) => <DayApptItem key={a.id} a={a} onEdit={onEdit} mitBeweis />)}
            </div>
          )}
        </div>

        <div className="apple-surface-gloss p-5">
          <div className="overline mb-3">Bevorstehend</div>
          {upcomingAppts.length === 0 ? (
            <div className="text-sm text-zinc-500">Keine bevorstehenden Termine.</div>
          ) : (
            <div className="space-y-2">
              {upcomingAppts.map((a) => <DayApptItem key={a.id} a={a} onEdit={onEdit} compact />)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// M26: Die Beweis-Karte (eigener Abruf + Nachfragen im Takt) nur dort, wo
// wenige Termine stehen (Tagesfeld) — in der Liste mit hunderten Terminen
// waren das hunderte parallele Anfragen. Im Termin-Dialog steht sie immer.
// Pruefbericht 20.09.2026 (U-67): Die Karte war selbst ein <button> — darin
// lagen Telefon (role=link), Abholbericht (role=button) und die Knoepfe der
// Beweis-Karte: verschachtelte Bedienelemente, ungueltiges HTML. Jetzt ist die
// Karte ein <div> (Maus: Klick oeffnet), der Titel ein echter Knopf (Tastatur),
// Telefon ein <a href="tel:">, Abholbericht ein <button>; der Abholbericht-
// Dialog rendert ohnehin per Portal ausserhalb.
function DayApptItem({ a, onEdit, compact, mitBeweis = false }) {
  const meta = STATUS_META[a.status] || STATUS_META.offen;
  // Runde 21: Abholbericht samt Fahrerfotos direkt am Termin (auch fuer Sucher).
  const [bericht, setBericht] = useState(false);
  const v = a.vehicle?.data;
  const date = safeParse(a.pickup_date);
  return (
    <div onClick={() => onEdit(a)} data-testid={`appt-row-${a.id}`}
         className="w-full text-left apple-card-gloss p-3 flex gap-3 items-start cursor-pointer">
      <div className="flex flex-col items-center pt-0.5 min-w-[44px]">
        <div className="cal-dot mb-1" style={{ background: meta.dot, width: 8, height: 8 }} />
        <div className="text-[11px] font-mono font-semibold tabular-nums text-zinc-300">
          {a.pickup_time || "—:—"}
        </div>
        {compact && date && (
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mt-0.5">
            {format(date, "d. LLL", { locale: de })}
          </div>
        )}
        {/* U-90: undatierter Termin in "Bevorstehend" — braucht ein Datum */}
        {compact && !date && (
          <div className="text-[10px] uppercase tracking-wider text-amber-300 mt-0.5"
               data-testid={`ohne-datum-${a.id}`}>
            ohne Datum
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <button type="button" onClick={(e) => { e.stopPropagation(); onEdit(a); }}
                data-testid={`appt-open-${a.id}`}
                className="block w-full text-left font-medium text-sm leading-snug truncate hover:underline">
          {a.title}
        </button>
        {v && !compact && (
          <div className="text-xs text-zinc-500 mt-0.5 truncate">
            {v.first_registration} · {v.mileage?.toLocaleString("de-DE")} km · {v.power_ps} PS
          </div>
        )}
        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
          <span className={`text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded-md ${meta.chipClass} ${meta.text}`}>
            {a.status}
          </span>
          {a.driver?.name && (
            <span className="inline-flex items-center gap-1 text-[11px] text-zinc-400" data-testid={`fahrer-${a.id}`}>
              <UserIcon size={10} /> {a.driver.name}
              {a.zuteilung === "offen" && <span className="text-amber-300"> · wartet auf Annahme</span>}
              {a.zuteilung === "angenommen" && <span className="text-emerald-300"> · angenommen</span>}
            </span>
          )}
          {/* Rollenprüfung 22.09.2026 (RP-542): Telefon des Fahrers (nur der
              Hauptchef bekommt es vom Server). U-67: jetzt ein echter tel:-Link. */}
          {telHref(a.driver?.phone) && (
            <a href={telHref(a.driver.phone)} data-testid={`fahrer-tel-${a.id}`}
               title={`${a.driver.name || "Fahrer"} anrufen`}
               onClick={(e) => e.stopPropagation()}
               className="inline-flex items-center gap-1 text-[11px] text-sky-300 hover:underline min-h-[36px] px-1 -mx-1">
              <Phone size={10} /> {a.driver.phone}
            </a>
          )}
          {!a.driver?.name && a.zuteilung === "abgelehnt" && (
            <span className="inline-flex flex-wrap items-center gap-1 text-[11px] text-red-300 min-w-0 [overflow-wrap:anywhere]"
                  data-testid={`abgelehnt-zeile-${a.id}`}>
              <UserIcon size={10} /> vom Fahrer abgelehnt{a.zuteilung_abgelehnt_von ? ` (${a.zuteilung_abgelehnt_von})` : ""}
              {/* Rollenprüfung 22.09.2026 (RP-464): Grund im Klartext statt nur als Tooltip */}
              {a.zuteilung_abgelehnt_grund ? `: „${a.zuteilung_abgelehnt_grund}“` : ""} — bitte neu zuteilen
            </span>
          )}
          {a.has_pickup_report && (
            <button type="button" data-testid={`bericht-${a.id}`}
                    onClick={(e) => { e.stopPropagation(); setBericht(true); }}
                    className="inline-flex items-center gap-1 text-[11px] text-sky-300 hover:underline min-h-[36px] px-1 -mx-1">
              <Camera size={10} /> Abholbericht
              {a.deviations_count ? ` · ${a.deviations_count} Abweichung${a.deviations_count === 1 ? "" : "en"}` : ""}
            </button>
          )}
          {a.contract_id && (
            <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: "var(--st-gruen)" }}>
              <FileText size={10} /> PDF
            </span>
          )}
        </div>
        {a.vehicle_id && !compact && mitBeweis && (
          <div className="mt-2" onClick={(e) => e.stopPropagation()}>
            <BeweisCard vehicleId={a.vehicle_id} compact />
          </div>
        )}
      </div>
      {bericht && <AbholberichtDialog appt={a} onClose={() => setBericht(false)} />}
    </div>
  );
}

/* ───────────────────────── List View ───────────────────────── */

function ListView({ items, onEdit, gekuerzt = false }) {
  if (!items.length) {
    return (
      <div className="apple-surface-gloss p-12 text-center text-zinc-500">
        <CalIcon className="mx-auto mb-3 opacity-50" />
        Keine Termine. Erstelle einen Vertrag oder klicke „Neuer Termin“.
        {gekuerzt && <div className="mt-2 text-xs">Hinweis: die Liste ist auf 2.000 Termine gekürzt.</div>}
      </div>
    );
  }
  // Wunsch 09/2026: der naechste Termin steht ganz oben (Heute / Morgen /
  // Diese Woche / Spaeter), abgeschlossene und vergangene Termine darunter,
  // neueste zuerst.
  const jetzt = new Date();
  const heute = format(jetzt, "yyyy-MM-dd");
  const morgen = format(addDays(jetzt, 1), "yyyy-MM-dd");
  const wocheEnde = format(endOfWeek(jetzt, { weekStartsOn: 1 }), "yyyy-MM-dd");
  const byTime = (x, y) => (x.pickup_time || "").localeCompare(y.pickup_time || "");
  const kommend = items
    .filter((a) => a.pickup_date && a.pickup_date >= heute && !ABGESCHLOSSEN.has(a.status))
    .sort((x, y) => x.pickup_date.localeCompare(y.pickup_date) || byTime(x, y));
  const ohneDatum = items.filter((a) => !a.pickup_date && !ABGESCHLOSSEN.has(a.status));
  const kommendIds = new Set([...kommend, ...ohneDatum].map((a) => a.id));
  const vergangen = items
    .filter((a) => !kommendIds.has(a.id))
    .sort((x, y) => (y.pickup_date || "").localeCompare(x.pickup_date || "") || byTime(y, x));
  const label = (d) => (d === heute ? "Heute" : d === morgen ? "Morgen" : d <= wocheEnde ? "Diese Woche" : "Später");
  const gruppen = (list) => {
    const out = [];
    for (const a of list) {
      const k = a.pickup_date || "ohne Datum";
      if (!out.length || out[out.length - 1].k !== k) out.push({ k, items: [] });
      out[out.length - 1].items.push(a);
    }
    return out;
  };
  const Gruppe = ({ k, list, prefix }) => {
    const date = safeParse(k);
    return (
      <div>
        <div className="flex items-center gap-3 mb-2">
          {prefix && <span className="text-[11px] uppercase tracking-wide px-2 py-0.5 rounded-md bg-white/[0.08] text-zinc-300">{prefix}</span>}
          <div className="font-display font-bold text-base">
            {date ? format(date, "EEEE, d. LLLL yyyy", { locale: de }) : "ohne Datum"}
          </div>
          <div className="flex-1 h-px bg-white/[0.06]" />
          <div className="text-xs text-zinc-500">{list.length} Termin{list.length !== 1 ? "e" : ""}</div>
        </div>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
          {list.map((a) => <DayApptItem key={a.id} a={a} onEdit={onEdit} />)}
        </div>
      </div>
    );
  };
  const Abschnitt = ({ titel, anzahl, tone }) => (
    <div className="flex items-center gap-3 pt-2" data-testid={`termine-abschnitt-${tone}`}>
      <div className={`text-sm font-semibold ${tone === "kommend" ? "text-white" : "text-zinc-400"}`}>{titel}</div>
      <div className="text-xs text-zinc-500">{anzahl}</div>
      <div className="flex-1 h-px bg-white/[0.10]" />
    </div>
  );
  return (
    <div className="space-y-5">
      <Abschnitt titel="Kommende Termine" anzahl={kommend.length + ohneDatum.length} tone="kommend" />
      {kommend.length === 0 && ohneDatum.length === 0 && (
        <div className="text-sm text-zinc-500">Keine offenen Termine.</div>
      )}
      {gruppen(kommend).map((g) => <Gruppe key={`k-${g.k}`} k={g.k} list={g.items} prefix={label(g.k)} />)}
      {ohneDatum.length > 0 && <Gruppe k="ohne Datum" list={ohneDatum} prefix="Ohne Datum" />}
      {vergangen.length > 0 && (
        <>
          <Abschnitt titel="Abgeschlossen & vergangen" anzahl={vergangen.length} tone="vergangen" />
          {gruppen(vergangen).map((g) => <Gruppe key={`v-${g.k}`} k={g.k} list={g.items} />)}
        </>
      )}
    </div>
  );
}

/* ───────────────────────── Modal ───────────────────────── */

/**
 * Entscheidung Ahmad 22.09.2026: Bei einer Abholung OHNE Abholprotokoll
 * (Büro-Abholung) gibt es das Feld "vor Ort vereinbarter Preis" wieder —
 * nur bei abgeholt/erledigt und nur, solange kein Protokoll zum Termin
 * existiert (mit Protokoll kommt der Preis über Freigabe/Unterschrift; der
 * Server lehnt final_price dann mit 409 ab).
 */
export function vorOrtPreisSichtbar(status, hatProtokoll, isNew = false) {
  return !isNew && !hatProtokoll && (status === "abgeholt" || status === "erledigt");
}

function EditDialog({ appt, drivers, fahrerGeladen = true, chef = false, isNew, onClose, onSave, onDelete }) {
  const [a, setA] = useState({ ...appt });
  const set = (k, v) => setA((alt) => ({ ...alt, [k]: v }));
  const [conflict, setConflict] = useState(null);
  // H19: Doppelklick legte zwei identische Termine an — waehrend des
  // Speicherns/Loeschens sind die Knoepfe gesperrt.
  const [arbeitet, setArbeitet] = useState(false);
  // Rollenprüfung 22.09.2026 (RP-418): Kosten als getippter Text (deutsche
  // Schreibweise), erst beim Speichern über preisAusText in eine Zahl.
  const kostenStart = kostenAlsText(appt?.extra_costs);
  const [kostenText, setKostenText] = useState(kostenStart);
  // Entscheidung Ahmad 22.09.2026: vor Ort vereinbarter Preis (Abholung ohne
  // Protokoll) — als getippter Text, wie die Kosten.
  const preisStart = kostenAlsText(appt?.final_price);
  const [preisText, setPreisText] = useState(preisStart);
  const speichern = async () => {
    if (arbeitet) return;
    const kosten = kostenAusEingabe(kostenText);
    if (kosten.fehler) {
      toast.error("Sonstige Kosten: bitte einen Betrag ab 0 € eintragen, z. B. 1.200 oder 12,50.");
      return;
    }
    // Unverändertes Feld: der gespeicherte Betrag geht unverändert mit (keine
    // Rundung über die Anzeige); nur ein neu getippter Betrag wird umgerechnet.
    let mitKosten = kostenText === kostenStart ? a : { ...a, extra_costs: kosten.wert };
    if (preisText !== preisStart) {
      const p = preisText.trim() ? preisAusText(preisText) : null;
      if (preisText.trim() && !(p > 0)) {
        toast.error("Vor Ort vereinbarter Preis: bitte einen Betrag über 0 € eintragen, z. B. 15.000.");
        return;
      }
      if (p) mitKosten = { ...mitKosten, final_price: p };
    }
    // Pruefbericht 20.09.2026 (V-12): abgeholt/erledigt -> storniert / nicht
    // abgeholt nur nach Rueckfrage; Abbrechen laesst den alten Status stehen.
    // Pruefung 21.09.2026 (V-12): mit Kauf- und Fahrzeugstand des Termins,
    // damit der Text nur zusagt, was der Server tut.
    const altStatus = appt?.status || "offen";
    const ausgangOpts = { hatKauf: terminHatKauf(appt), hatFahrzeug: !!appt?.vehicle_id,
                          lifecycle: appt?.vehicle?.lifecycle || null };
    const frage = !isNew && chef
      ? ausgangFrage(altStatus, a.status, !!appt.protocol_id, ausgangOpts) : null;
    if (frage && !window.confirm(frage)) {
      set("status", altStatus);
      return;
    }
    setArbeitet(true);
    try {
      let ok = await onSave(frage ? { ...mitKosten, ausgang_bestaetigt: true } : mitKosten);
      if (ok === "ausgang") {
        // Pruefung 21.09.2026 (V-12): Der Server verlangt die Rueckfrage.
        const nachfrage = ausgangFrage(altStatus, a.status, !!appt.protocol_id,
          { ...ausgangOpts, belegt: true });
        ok = nachfrage && window.confirm(nachfrage)
          ? await onSave({ ...mitKosten, ausgang_bestaetigt: true }) : false;
        if (ok === "ausgang") ok = false;
      }
      if (ok === "korrektur") {
        // Rollenprüfung 22.09.2026 (RP-482/075/174): offene Protokoll-Korrektur
        // verwerfen (nur nach ausdrücklicher Bestätigung des Chefs).
        ok = window.confirm(KORREKTUR_VERWERFEN_FRAGE)
          ? await onSave({ ...mitKosten, korrektur_verwerfen: true }) : false;
        if (ok === "korrektur" || ok === "ausgang") ok = false;
      }
      // U-54: nach einem gescheiterten Speichern zeigt der Dialog wieder den
      // Status, der wirklich gilt — nicht den abgelehnten.
      if (ok === false && !isNew && (a.status || "offen") !== (appt.status || "offen")) {
        set("status", appt.status || "offen");
      }
    } finally { setArbeitet(false); }
  };
  const loeschen = async () => {
    if (arbeitet || !onDelete) return;
    setArbeitet(true);
    try { await onDelete(); } finally { setArbeitet(false); }
  };
  // H20: PDF-Knoepfe mit Fehlermeldung und ohne Mehrfachklick (jeder Klick
  // war sonst ein neuer, bis zu 180 s langer Abruf).
  const [pdfLaeuft, setPdfLaeuft] = useState("");
  const pdfAktion = async (name, fn, fehlerText) => {
    if (pdfLaeuft) return;
    setPdfLaeuft(name);
    try { await fn(); } catch (e) { toast.error(errMsg(e, fehlerText)); } finally { setPdfLaeuft(""); }
  };
  // H21: Ist der zugeteilte Fahrer nicht in der Liste (Liste nicht geladen,
  // Fahrer inzwischen entfernt), bleibt er als eigene Option stehen — sonst
  // zeigte das Feld "kein Fahrer" und das Speichern entfernte ihn.
  const fahrerFehlt = !!a.driver_id && !drivers.some((d) => d.id === a.driver_id);

  // Warnung: schon eine Fahrt am selben Tag?
  // Pruefbericht 20.09.2026 (U-63): count/has_more des Servers werden genutzt
  // (konfliktAuswerten), und ein Fehler der Pruefung ist sichtbar statt still.
  useEffect(() => {
    setConflict(null);
    if (!a.driver_id || !a.pickup_date) return;
    let cancelled = false;
    api.get(`/drivers/${a.driver_id}/conflicts`, { params: { date: a.pickup_date } })
      .then((r) => {
        if (cancelled) return;
        setConflict(konfliktAuswerten(r.data, a.id));
      })
      .catch((err) => {
        if (cancelled) return;
        setConflict({ fehler: konfliktFehlerText(err) });
      });
    return () => { cancelled = true; };
  }, [a.driver_id, a.pickup_date, a.id]);

  // Rollenprüfung 22.09.2026 (RP-043/142): Ein Tipp neben das Fenster schließt
  // nur, wenn nichts geändert wurde — sonst mit Rückfrage. X und „Abbrechen“
  // schließen wie bisher sofort.
  const hintergrundKlick = () => {
    if (arbeitet) return;
    const geaendert = dialogGeaendert(a, appt) || kostenText !== kostenStart
      || preisText !== preisStart;
    if (!geaendert || window.confirm("Änderungen verwerfen?")) onClose();
  };
  // Pruefbericht 20.09.2026 (M-07): role=dialog, Escape (mit derselben
  // Rueckfrage wie der Tipp daneben), Fokus (lib/useModal).
  const dialogRef = useModal(hintergrundKlick);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 apple-modal-backdrop"
         onClick={hintergrundKlick} data-testid="edit-appt-hintergrund">
      {/* Handy-Ansicht (24.09.2026): Hoehe nach dvh (iOS-Adressleiste), am
          Telefon fast randlos, die Fusszeile bricht um statt seitlich zu scrollen. */}
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="edit-appt-titel"
           className="apple-modal w-full max-w-xl modal-hoehe overflow-y-auto overflow-x-hidden"
           onClick={(e) => e.stopPropagation()}
           data-testid="edit-appt-dialog">
        <div className="flex items-center justify-between px-4 sm:px-6 py-3 sm:py-4 border-b border-white/[0.08]">
          <div>
            <div className="overline">{isNew ? "Neu" : "Bearbeiten"}</div>
            <div className="font-display font-bold text-xl mt-0.5" id="edit-appt-titel">
              {isNew ? "Neuer Termin" : "Termin bearbeiten"}
            </div>
          </div>
          <button type="button" onClick={onClose} className="apple-btn apple-btn-ghost !p-2"
                  aria-label="Schließen">
            <X size={18} />
          </button>
        </div>

        <div className="p-4 sm:p-6 space-y-5">
          {/* Title */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Titel</label>
            <input data-testid="edit-title" value={a.title || ""}
                   onChange={(e) => set("title", e.target.value)}
                   placeholder="z. B. Mercedes E 220d abholen"
                   className="apple-input" />
          </div>

          {/* Date + Time */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <CalIcon size={12} /> Datum
              </label>
              <input data-testid="edit-pickup-date" type="date" value={a.pickup_date || ""}
                     onChange={(e) => set("pickup_date", e.target.value)}
                     className="apple-input" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <Clock size={12} /> Uhrzeit
              </label>
              <input data-testid="edit-pickup-time" type="time" value={a.pickup_time || ""}
                     onChange={(e) => set("pickup_time", e.target.value)}
                     className="apple-input" />
            </div>
          </div>

          {/* Status + Driver */}
          {/* M-06: am Handy untereinander, Statusknoepfe zweispaltig */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Status</label>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
                {STATUSES.map((s) => {
                  const meta = STATUS_META[s];
                  const active = (a.status || "offen") === s;
                  const sperre = statusSperre(s, isNew ? null : (appt.status || "offen"), chef,
                    { fahrer: !!(a.driver_id || appt?.driver_id), protokoll: !!appt?.protocol_id,
                      ausgang: ausgangWirksam(appt), hatKauf: terminHatKauf(appt),
                      hatFahrzeug: !!appt?.vehicle_id, lifecycle: appt?.vehicle?.lifecycle || null });
                  return (
                    <button key={s} onClick={() => set("status", s)} data-testid={`status-${s}`}
                            type="button" disabled={!!sperre} title={sperre || undefined}
                            className={`text-[11px] font-medium px-2 py-2 rounded-lg border transition-all flex items-center justify-center gap-1.5 disabled:opacity-40 disabled:cursor-not-allowed ${
                              active ? `${meta.chipClass} ${meta.text} border-current/40` : "bg-white/[0.03] text-zinc-500 border-white/[0.06] hover:text-zinc-300"
                            }`}>
                      <span className="cal-dot" style={{ background: meta.dot }} />
                      {s}
                    </button>
                  );
                })}
              </div>
              {/* Pruefbericht 20.09.2026 (V-12): nachtraeglich geaenderter Ausgang —
                  Pruefung 21.09.2026: nur solange der Merker zum Status passt */}
              {!isNew && ausgangWirksam(appt) && (
                <div data-testid="ausgang-geaendert-hinweis"
                     className="mt-2 p-2.5 rounded-sm text-xs leading-relaxed"
                     style={{ background: "rgba(255,149,0,0.12)",
                              border: "1px solid rgba(255,149,0,0.35)",
                              color: "var(--tx-amber)" }}>
                  {ausgangHinweis(appt.ausgang_geaendert)}
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <UserIcon size={12} /> Fahrer
              </label>
              <select data-testid="edit-driver" value={a.driver_id || ""}
                      onChange={(e) => set("driver_id", e.target.value || null)}
                      className="apple-input">
                <option value="">— kein Fahrer —</option>
                {fahrerFehlt && (
                  <option value={a.driver_id}>
                    {a.driver?.name || appt.driver?.name || "zugeteilter Fahrer"}
                    {fahrerGeladen ? " (nicht mehr in deiner Fahrerliste)" : ""}
                  </option>
                )}
                {drivers.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
              {/* Rollenprüfung 22.09.2026 (RP-542): zugeteilten Fahrer direkt anrufen */}
              {a.driver_id && a.driver_id === appt?.driver_id && telHref(appt?.driver?.phone) && (
                <a href={telHref(appt.driver.phone)} data-testid="edit-driver-tel"
                   className="mt-1.5 inline-flex items-center gap-1.5 text-xs text-sky-300 hover:underline">
                  <Phone size={12} /> {appt.driver.name || "Fahrer"} anrufen: {appt.driver.phone}
                </a>
              )}
              {conflict?.fehler && (
                <div data-testid="driver-conflict-fehler" role="status"
                     className="mt-2 p-2.5 rounded-sm text-xs leading-relaxed"
                     style={{ background: "var(--wa-04)", border: "1px solid var(--border-default)",
                              color: "var(--text-secondary)" }}>
                  {conflict.fehler}
                </div>
              )}
              {conflict && !conflict.fehler && (
                <div data-testid="driver-conflict-warning"
                     className="mt-2 p-2.5 rounded-sm text-xs leading-relaxed"
                     style={{ background: "rgba(255,149,0,0.12)",
                              border: "1px solid rgba(255,149,0,0.35)",
                              color: "var(--tx-amber)" }}>
                  ⚠️ Fahrer ist am {a.pickup_date} bereits einer Fahrt zugeordnet
                  ({conflict.anzahl}×). Du kannst trotzdem zuweisen.
                </div>
              )}
            </div>
          </div>

          {/* Address + Contact */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
              <MapPin size={12} /> Abhol-Adresse
            </label>
            <input data-testid="edit-address" value={a.pickup_address || ""}
                   onChange={(e) => set("pickup_address", e.target.value)}
                   placeholder="Straße, PLZ Ort"
                   className="apple-input" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Verkäufer</label>
              <input value={a.seller_name || ""}
                     onChange={(e) => set("seller_name", e.target.value)}
                     className="apple-input" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <Phone size={12} /> Telefon
              </label>
              <input value={a.seller_phone || ""}
                     onChange={(e) => set("seller_phone", e.target.value)}
                     className="apple-input" />
            </div>
          </div>

          {/* 14.09.2026 (Entscheidung Ahmad): kein Endpreis-Feld mehr — der Preis
              kommt ueber Abholprotokoll und Freigabe in den Kaufvertrag.
              22.09.2026 (Entscheidung Ahmad): AUSSER bei einer Abholung ohne
              Protokoll (Büro) — dann gibt es das Feld wieder, nur bei
              abgeholt/erledigt (vorOrtPreisSichtbar). */}
          <div className="grid grid-cols-2 gap-3">
            {vorOrtPreisSichtbar(a.status, !!appt?.protocol_id, isNew) && (
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Vor Ort vereinbarter Preis (€)</label>
                <input data-testid="edit-final-price" type="text" inputMode="decimal"
                       value={preisText}
                       onChange={(e) => setPreisText(e.target.value)}
                       placeholder="leer = Vertragspreis"
                       className="apple-input" />
                <div className="text-[11px] text-zinc-500">
                  Nur bei Abholung ohne Abholprotokoll. Wird der Einkaufspreis dieses Kaufs.
                </div>
              </div>
            )}
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Sonstige Kosten (€)</label>
              {/* N18: "0" ist ein gueltiger Betrag (vorher wurde er verworfen),
                  negative Betraege lehnt speichern() ab.
                  Rollenprüfung 22.09.2026 (RP-418): Texteingabe in deutscher
                  Schreibweise ("1.200" = 1200 €, "12,50") statt type="number". */}
              <input data-testid="edit-extra-costs" type="text" inputMode="decimal"
                     value={kostenText}
                     onChange={(e) => setKostenText(e.target.value)}
                     placeholder="z. B. 1.200"
                     className="apple-input" />
            </div>
          </div>

          {/* Notes */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Notizen</label>
            <textarea data-testid="edit-notes" rows={3} value={a.notes || ""}
                      onChange={(e) => set("notes", e.target.value)}
                      placeholder="Zusatzinfos, Treffpunkt, Schlüsselübergabe …"
                      className="apple-input resize-none" />
          </div>

          {/* PDF link if connected — „Öffnen" behält altes Verhalten
              (neuer Tab), „Drucken" zeigt direkt den Druckdialog. */}
          {a.contract_id && (
            <div className="grid grid-cols-2 gap-2" data-testid="contract-actions">
              <button type="button" disabled={!!pdfLaeuft}
                      onClick={() => pdfAktion("vertrag", () => openContractPdf(a.contract_id),
                                               "Kaufvertrag konnte nicht geladen werden")}
                      data-testid="contract-open-btn"
                      className="apple-btn apple-btn-secondary disabled:opacity-60">
                <FileText size={14} /> {pdfLaeuft === "vertrag" ? "Lädt…" : "Kaufvertrag öffnen"}
              </button>
              <button type="button" disabled={!!pdfLaeuft}
                      onClick={() => pdfAktion("vertrag-druck", () => printContractPdf(a.contract_id),
                                               "Kaufvertrag konnte nicht geladen werden")}
                      data-testid="contract-print-btn"
                      className="apple-btn apple-btn-secondary disabled:opacity-60">
                <Printer size={14} /> Drucken
              </button>
            </div>
          )}

          {/* Abholauftrag / Übergabeprotokoll für den Fahrer.
              Steht für jeden Termin mit Fahrzeug zur Verfügung, auch ohne
              verknüpften Kaufvertrag — in dem Fall werden die Felder auf
              dem Protokoll einfach leergelassen und der Fahrer füllt sie
              vor Ort aus. Der erste Klick erzeugt das PDF on-demand,
              nachträgliche Aufrufe sind jederzeit möglich. */}
          {a.id && a.vehicle_id && (
            <div className="rounded-xl p-3"
                 style={{ background: "var(--hover-bg)", border: "1px solid var(--divider)" }}
                 data-testid="pickup-order-actions">
              <div className="flex items-center gap-2 mb-2">
                <ClipboardCheck size={14} className="text-[var(--accent-red)]" />
                <div className="text-[11px] font-bold uppercase tracking-wider"
                     style={{ color: "var(--text-primary)" }}>
                  Abholprotokoll (Fahrer)
                </div>
              </div>
              <div className="text-[10.5px] mb-2"
                   style={{ color: "var(--text-muted)" }}>
                Übergabeprotokoll mit Fahrzeugdaten-, Ausstattungs- &amp;
                Schadens-Check. Fahrer prüft alle Punkte vor Ort ab.
              </div>
              <div className="grid grid-cols-3 gap-2">
                <button type="button" disabled={!!pdfLaeuft}
                        onClick={() => pdfAktion("auftrag", () => openPickupOrderPdf(a.id),
                                                 "Abholauftrag konnte nicht geladen werden")}
                        data-testid="pickup-open-btn"
                        className="apple-btn apple-btn-primary !py-2 !text-[11px] disabled:opacity-60">
                  <FileText size={12} /> {pdfLaeuft === "auftrag" ? "Lädt…" : "Öffnen"}
                </button>
                <button type="button" disabled={!!pdfLaeuft}
                        onClick={() => pdfAktion("auftrag-druck", () => printPickupOrderPdf(a.id),
                                                 "Abholauftrag konnte nicht geladen werden")}
                        data-testid="pickup-print-btn"
                        className="apple-btn apple-btn-secondary !py-2 !text-[11px] disabled:opacity-60">
                  <Printer size={12} /> Drucken
                </button>
                <button type="button" disabled={!!pdfLaeuft}
                        onClick={() => pdfAktion("auftrag-datei", () => downloadPickupOrderPdf(
                          a.id,
                          `Abholauftrag_${(a.title || "Termin").replace(/[^\w\- ]+/g, "_")}.pdf`,
                        ), "Abholauftrag konnte nicht geladen werden")}
                        data-testid="pickup-download-btn"
                        className="apple-btn apple-btn-secondary !py-2 !text-[11px] disabled:opacity-60">
                  <Download size={12} /> Download
                </button>
              </div>
            </div>
          )}

          {a.vehicle_id && (
            <div className="mt-4">
              <BeweisCard vehicleId={a.vehicle_id} />
            </div>
          )}

          {/* Fahrzeug-Fotos aus dem Inserat (soweit beim Vergleich mit
              gescraped). Händler sieht hier exakt das, was auch der Fahrer
              später in der Fahrer-App sieht. */}
          {(() => {
            const d = a.vehicle?.data || {};
            const photos = (
              d.image_urls || d.images || d.photos || d.pictures || []
            ).filter(Boolean);
            if (photos.length === 0) return null;
            return (
              <div className="mt-4 rounded-xl p-4"
                   style={{ background: "var(--hover-bg)", border: "1px solid var(--divider)" }}>
                <PhotoGallery photos={photos} thumbs={d.images_thumbs || []} label="Inserat-Fotos" />
              </div>
            );
          })()}
        </div>

        {/* Footer */}
        <div className="px-4 sm:px-6 py-3 sm:py-4 border-t border-white/[0.08] flex flex-wrap items-center justify-between gap-2 sticky bottom-0 bg-[var(--bg-elevated)] rounded-b-[18px]"
             style={{ paddingBottom: "calc(0.75rem + env(safe-area-inset-bottom, 0px))" }}>
          <div>
            {onDelete && (
              <button onClick={loeschen} disabled={arbeitet} className="apple-btn apple-btn-danger disabled:opacity-60" data-testid="delete-appt-btn">
                <Trash2 size={14} /> Löschen
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <button onClick={onClose} className="apple-btn apple-btn-ghost">Abbrechen</button>
            <button onClick={speichern} disabled={arbeitet} data-testid="save-appt-btn" className="apple-btn apple-btn-primary disabled:opacity-60">
              {arbeitet ? "Speichert…" : isNew ? "Anlegen" : "Speichern"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Rollenprüfung 22.09.2026 (RP-464): „Vom Fahrer abgelehnt (n)“ mit Grund.
function AbgelehntHinweis({ liste, onEdit }) {
  if (!liste?.length) return null;
  return (
    <div className="mb-5 rounded-xl border px-4 py-3 text-sm" role="status"
         data-testid="termine-abgelehnt-hinweis"
         style={{ borderColor: "#ff3b3055", background: "#ff3b3014", color: "var(--tx-rot)" }}>
      <div className="font-semibold">Vom Fahrer abgelehnt ({liste.length}) — bitte neu zuteilen</div>
      <ul className="mt-2 space-y-1.5">
        {liste.map((a) => {
          const datum = safeParse(a.pickup_date);
          return (
            <li key={a.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 min-w-0"
                data-testid={`abgelehnt-${a.id}`}>
              <button type="button" onClick={() => onEdit(a)}
                      className="underline underline-offset-2 hover:opacity-80 text-left min-w-0 [overflow-wrap:anywhere]"
                      style={{ color: "var(--text-primary)" }}>
                {a.title || "Termin"}
              </button>
              <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>
                {datum ? format(datum, "d. LLL", { locale: de }) : "ohne Datum"}
                {a.pickup_time ? `, ${a.pickup_time}` : ""}
                {a.zuteilung_abgelehnt_von ? ` · ${a.zuteilung_abgelehnt_von}` : ""}
              </span>
              <span className="text-[12px] w-full [overflow-wrap:anywhere]" data-testid={`abgelehnt-grund-${a.id}`}>
                {a.zuteilung_abgelehnt_grund ? `Grund: ${a.zuteilung_abgelehnt_grund}` : "ohne Angabe eines Grundes"}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// Runde 33 (Wunsch Ahmad): Mehrere Fahrer koennen gleichzeitig warten — die
// Freigaben liegen deshalb auf einer eigenen Seite (/app/freigaben).
function FreigabeHinweis() {
  // 14.09.2026: Freigaben sind Chefsache — Sucher sehen den Hinweis nicht.
  const { user } = useAuth();
  const { wartet, freigegeben } = useFreigabeZaehler(user?.role === "dealer");
  if (!wartet && !freigegeben) return null;
  const text = wartet > 0
    ? `${wartet} Abholprotokoll${wartet === 1 ? " wartet" : "e warten"} auf deine Freigabe`
    : `${freigegeben} freigegeben — vor Ort wird unterschrieben`;
  return (
    <Link to="/app/freigaben" data-testid="termine-freigaben-hinweis"
          className="mb-5 rounded-xl border px-4 py-3 flex items-center gap-2 text-sm hover:bg-white/5 transition"
          style={{ borderColor: "#ff9f0a55", background: "#ff9f0a14", color: "var(--tx-amber)" }}>
      {text} — zu den Freigaben ›
    </Link>
  );
}
