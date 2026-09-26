import MonatJahrEingabe from "@/components/MonatJahrEingabe";
import { monatJahrFehler } from "@/lib/monatJahr";
import { useUngespeichert } from "@/lib/ungespeichert";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { driverApi, openDriverPdf } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { preisText } from "@/lib/preis";
import { protokollZustand } from "@/lib/protokollZustand";
import { toast } from "sonner";
import {
  ArrowLeft, Save, CheckCircle2, FileText, AlertTriangle, Pencil,
} from "lucide-react";
import SignaturePad from "@/components/SignaturePad";
import DamageSelector from "@/components/DamageSelector";
import KiFahrerKarte from "@/components/KiFahrerKarte";
import { alleVollstaendig, schwereOffen } from "@/lib/kiSchaden";
import {
  LEERER_ENTWURF, entwurfAusServer, entwurfZusammenfuehren, istAnnahmeFehlt, istRevisionsKonflikt,
  nutzlast, nutzlastText, preisVorschlagLesen,
} from "./protokollEntwurf";
import {
  freigabeKennung as kennungAus, sicherungLesen, sicherungLoeschen, sicherungSchreiben,
} from "./protokollSicherung";

// Rollenprüfung 22.09.2026 (RP-065/RP-164): nach einem Netzfehler selbst
// erneut speichern (statt still auf das nächste Tippen zu warten).
const NETZ_WIEDERHOLUNG_MS = 10000;
// Rollenprüfung 22.09.2026 (RP-535): Servergrenze ProtocolIn.notes.
const BEMERKUNG_MAX = 5000;
// Rollenprüfung 22.09.2026 (RP-068/RP-167): Nach dem Abschluss steht der Termin
// auf "abgeholt" — eine Korrektur-Version lehnt der Server dann immer ab (409),
// bis der Händler den Termin wieder öffnet.
const TERMIN_GESCHLOSSEN = ["abgeholt", "nicht abgeholt", "storniert", "erledigt"];

/* Section/Check sind bewusst AUSSERHALB der Seite definiert: innerhalb
 * definierte Komponenten bekommen bei jedem Render eine neue Identität —
 * React baut dann alle Eingabefelder neu auf und der Fokus/Eingaben
 * gehen beim Tippen verloren. */
const Section = ({ n, title, children, hint }) => (
  <div className="mt-4 rounded-2xl p-4" style={{ background: "var(--wa-03)", border: "1px solid var(--wa-08)" }}>
    <div className="flex items-center gap-2">
      <span className="w-6 h-6 rounded-md flex items-center justify-center text-[11px] font-bold text-white"
            style={{ background: "var(--accent-red)" }}>{n}</span>
      <div className="text-sm font-bold">{title}</div>
    </div>
    {hint && <div className="text-[11px] text-zinc-500 mt-1">{hint}</div>}
    <div className="mt-3">{children}</div>
  </div>
);


// Wunsch Ahmad 14.09.2026: jede Zeile muss beantwortet werden — Ja ODER Nein
// (vorher ein Haken, bei dem "nicht angeklickt" und "fehlt" dasselbe waren).
// Preisvorschlag, Abschnitt 5 und Nutzlast: siehe ./protokollEntwurf.js
// (Rollenprüfung 22.09.2026, RP-060/RP-067). Der frühere Einzel-Haken für
// Abschnitt 5 ist durch JaNein ersetzt.
const JaNein = ({ wert, onChange, disabled, children, testId }) => {
  const knopf = (label, ziel, farbe) => (
    <button type="button" disabled={disabled} onClick={() => onChange(ziel)}
            data-testid={testId ? `${testId}-${label.toLowerCase()}` : undefined}
            // Handy-Ansicht (24.09.2026): 40 px hoch und breit genug fuer den Daumen
            className={`px-3.5 tipp-40 min-w-[52px] rounded-lg text-xs border disabled:opacity-60 ${
              wert === ziel ? "font-semibold" : "text-zinc-400"}`}
            style={{ borderColor: wert === ziel ? farbe : "var(--border-default)",
                     color: wert === ziel ? farbe : undefined,
                     background: wert === ziel
                       ? `color-mix(in srgb, ${farbe} 18%, transparent)` : "transparent" }}>
      {label}
    </button>
  );
  return (
    <div className="w-full flex items-center justify-between gap-3 py-2 text-sm">
      <span className={typeof wert === "boolean" ? "text-white" : "text-zinc-400"}>{children}</span>
      <span className="flex gap-1.5 shrink-0">
        {knopf("Ja", true, "var(--st-gruen)")}
        {knopf("Nein", false, "var(--st-rot)")}
      </span>
    </div>
  );
};

/**
 * Digitales Abhol-Protokoll — dieselben Abschnitte wie das PDF, nur
 * ausfüllbar: abhaken, eintippen, unterschreiben. Zwischenstände werden
 * automatisch gespeichert; beim Abschließen entsteht das fertige PDF und
 * das Fahrzeug gilt als abgeholt.
 */
export default function Protokoll() {
  const { id } = useParams();          // appointment id
  const nav = useNavigate();
  const [data, setData] = useState(null);
  // RP-067: Abschnitt 5 startet unbeantwortet (null), nicht als "Nein".
  const [f, setF] = useState(() => ({ ...LEERER_ENTWURF }));
  const [sigDriver, setSigDriver] = useState(null);
  const [sigSeller, setSigSeller] = useState(null);
  const [sellerName, setSellerName] = useState("");
  const [busy, setBusy] = useState(false);
  const [savedAt, setSavedAt] = useState(null);
  const saveTimer = useRef(null);
  // Rollenprüfung 22.09.2026 (RP-065/RP-164): Autosave-Fehler sichtbar
  // machen ({ grund, netz }) statt sie still zu verschlucken.
  const [speicherFehler, setSpeicherFehler] = useState(null);
  // Gibt es Eingaben, die noch nicht beim Server sind? (Schutz beim Verlassen)
  const [ungesichert, setUngesichert] = useState(false);
  const wiederholTimer = useRef(null);
  const letzteFehlerMeldung = useRef("");
  // RP-061/RP-160: alle Speicher-Aufrufe laufen nacheinander (jeder mit der
  // Revision der vorigen Antwort) — und der Serverstand, auf dem die lokalen
  // Eingaben beruhen, für die Zusammenführung nach einem Konflikt.
  const kette = useRef(Promise.resolve());
  const basisRef = useRef(null);
  const zuletztGesendet = useRef("");

  // Gegenpruefung 12.09.2026: Ort und Verkaeufername tippt der Fahrer oft,
  // waehrend er auf die Freigabe wartet — gespeichert werden sie erst beim
  // Abschluss. Das automatische Nachladen setzte sie alle 15 s zurueck.
  const ortGetippt = useRef(false);
  // Phase 2 (2.9): Revision des Entwurfs (vom Server) — zwei Tabs desselben
  // Fahrers überschreiben sich nicht mehr gegenseitig.
  const revRef = useRef(null);
  const nameGetippt = useRef(false);
  // Rollenprüfung 22.09.2026 (RP-058/157/173): Kam der Verkäufername aus dem
  // ENTWURF (vom Fahrer gespeichert)? Dann geht er beim Speichern weiter mit;
  // ein nur aus dem Termin vorbelegter Name wird nicht gespeichert.
  const nameImEntwurf = useRef(false);
  // Rollenprüfung 22.09.2026 (Review): Steht im Feld der Name aus dem ENTWURF
  // (z. B. von der Korrektur-Version übernommen) und hat der Chef den Namen am
  // Termin inzwischen geändert, kam die Korrektur nie an. Die App zeigt den
  // Namen am Termin jetzt daneben und übernimmt ihn auf Tipp.
  const [nameAusEntwurf, setNameAusEntwurf] = useState(false);
  // Erstes Laden gescheitert (z. B. Fahrt nicht mehr angenommen): Grund zeigen
  // statt endlos "lade…".
  const [ladeFehler, setLadeFehler] = useState(null);
  // RP-546: Sicherung im Tab — einmal je Öffnen der Seite prüfen und
  // wiederherstellen, erst danach laufend sichern.
  const sicherungGeprueft = useRef(false);
  const sicherungStand = useRef(null);          // was gerade zu sichern ist (oder null)
  const wiederherstellenRef = useRef(null);
  const [sigStart, setSigStart] = useState(null);
  const idRef = useRef(id);
  useEffect(() => { idRef.current = id; });

  const load = useCallback(async ({ still = false } = {}) => {
    try {
      const r = await driverApi.get(`/driver/appointments/${id}/protocol`);
      setData(r.data);
      const p = r.data.protocol;
      revRef.current = p?.revision ?? null;
      if (p) {
        const server = entwurfAusServer(p);
        basisRef.current = server;
        // Nach dem Laden gilt der Serverstand — der nächste Autosave vergleicht
        // nicht mehr mit einem älteren eigenen PUT.
        zuletztGesendet.current = "";
        setF((s) => ({
          ...s,
          ...server,
          place: ortGetippt.current ? s.place : server.place,
        }));
      }
      // RP-058/157/173: zuerst der im Entwurf gespeicherte Name, sonst der vom Termin.
      if (!nameGetippt.current) {
        const entwurfName = String(p?.seller_name || "").trim();
        nameImEntwurf.current = Boolean(entwurfName);
        setNameAusEntwurf(Boolean(entwurfName));
        setSellerName(entwurfName || r.data.appointment?.seller_name || "");
      }
      setLadeFehler(null);
      if (!sicherungGeprueft.current) {
        wiederherstellenRef.current?.(r.data);
        sicherungGeprueft.current = true;
      }
    } catch (e) {
      // Beim automatischen Nachladen kein roter Hinweis alle 15 s im Funkloch.
      if (!still) toast.error(errMsg(e, "Protokoll konnte nicht geladen werden"));
      setLadeFehler({ grund: errMsg(e, "Protokoll konnte nicht geladen werden"),
                      annehmen: istAnnahmeFehlt(e?.response?.status, errMsg(e, "")) });
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  // Runde 30 (12.09.2026, Wunsch Ahmad): Der Fahrer schickt das ausgefuellte
  // Protokoll erst zur Freigabe an den Chef. Der prueft die Abweichungen,
  // verhandelt ggf. nach und gibt mit dem neuen Preis frei — DANN wird
  // unterschrieben. Solange etwas beim Chef liegt, sind alle Eingaben
  // gesperrt: er soll genau das sehen, was am Ende unterschrieben wird.
  // Go-Live 13.09.2026 (N1): auch "wird_abgeschlossen" sperrt (protokollZustand).
  const {
    isFinal, wartetAufFreigabe, freigegeben, wirdAbgeschlossen, gesperrt, nachladen, unterschriften,
    unbekannt,
  } = protokollZustand(data?.protocol?.status);
  const neuerPreis = data?.protocol?.neuer_preis ?? null;
  const rueckfrage = data?.protocol?.rueckfrage || "";
  // Stufe 3 KI (26.09.2026): strukturierte Rückfrage (Frage + Antwortknöpfe).
  // Review 26.09.2026 (Nr. 57/60-62): Zuordnung über die Server-ID der Frage
  // (frage_id); ältere Fragen ohne ID über source_id + question.
  const rueckfrageFrage = data?.protocol?.rueckfrage_frage || null;
  const istAntwortZu = (a) => !!rueckfrageFrage && (rueckfrageFrage.frage_id
    ? a.frage_id === rueckfrageFrage.frage_id
    : a.source_id === (rueckfrageFrage.source_id || "") && a.question === rueckfrageFrage.question);
  const rueckfrageAntwort = rueckfrageFrage
    ? ((f.rueckfrage_antworten || []).find(istAntwortZu)?.answer || "") : "";
  // Nr. 125: keine erfundenen Knöpfe — ohne Optionen antwortet der Fahrer als Text.
  const rueckfrageOptionen = rueckfrageFrage?.options?.length ? rueckfrageFrage.options : null;
  const rueckfrageVerlauf = Array.isArray(data?.protocol?.rueckfrage_verlauf) ? data.protocol.rueckfrage_verlauf : [];
  // Review 26.09.2026 (Nr. 101-105): "davon vereinbart" kommt vom Server (Vertrag).
  const schluesselVereinbart = data?.protocol?.keys_expected ?? data?.template?.keys_expected ?? null;
  // Rollenprüfung 22.09.2026 (RP-059/RP-158): Ort und Verkäufername friert
  // der Server beim Abschicken ein, der Abschluss nimmt genau diese Werte
  // (protocols.py: doc.place/seller_name vor dem Wert aus der App). Vorher
  // blieben beide Felder danach editierbar — der Bildschirm zeigte beim
  // Unterschreiben etwas anderes als das PDF. Ab "zur Freigabe" also gesperrt
  // und mit genau dem Wert, den der Server drucken wird. Korrektur nur über
  // eine Rückfrage des Händlers (dann wieder Entwurf).
  const ortAnzeige = gesperrt ? (data?.protocol?.place || f.place || "") : f.place;
  // Entscheidung Ahmad 22.09.2026 (Ausweisnummer): gesperrt wie Ort und Name.
  const ausweisAnzeige = gesperrt
    ? (data?.protocol?.seller_id_document || f.seller_id_document || "")
    : (f.seller_id_document || "");
  const nameAnzeige = gesperrt
    ? (data?.protocol?.seller_name || sellerName || data?.appointment?.seller_name || "")
    : sellerName;
  // Rollenprüfung 22.09.2026 (Review): Name am Termin (für den Hinweis am Feld).
  const terminName = String(data?.appointment?.seller_name || "").trim();
  // Runde 31: Die Unterschriften liegen bis zum Abschluss NUR im Speicher —
  // der Auto-Save schickt sie nicht mit. Ein Neuladen haette sie ersatzlos
  // geloescht, und der Verkaeufer steht oft schon am Auto.
  // RP-065: ebenso, solange getippte Eingaben noch nicht beim Server sind.
  useUngespeichert(Boolean(((sigDriver || sigSeller) && !isFinal) || (ungesichert && !gesperrt)));
  // Runde 33: Waehrend das Protokoll beim Chef liegt, selbst nachsehen —
  // vorher merkte der Fahrer die Freigabe erst, wenn er auf Aktualisieren
  // tippte. Eingaben sind in dieser Zeit ohnehin gesperrt.
  // Gegenpruefung 12.09.2026: auch NACH der Freigabe — der Chef kann Preis
  // oder Vermerk noch aendern. Ort und Verkaeufername bleiben unangetastet.
  // Go-Live 13.09.2026 (N1): ebenso waehrend "wird_abgeschlossen".
  useEffect(() => {
    if (!nachladen) return undefined;
    const takt = setInterval(() => {
      if (document.visibilityState === "visible") load({ still: true });
    }, 15000);
    return () => clearInterval(takt);
  }, [nachladen, load]);

  // Aendert der Chef nach der Freigabe Preis oder Vermerk, gelten Unterschriften,
  // die schon auf dem Handy stehen, nicht mehr: loeschen und deutlich sagen.
  // Go-Live 13.09.2026: die Kennung bleibt auch waehrend des Abschlusses stehen —
  // sonst fiele eine Aenderung nach einem gescheiterten Abschluss nicht auf.
  const freigabeKennung = unterschriften ? kennungAus(data?.protocol) : null;
  const [sigRunde, setSigRunde] = useState(0);
  const vorigeKennung = useRef(null);
  // Go-Live 13.09.2026 (P4): Stehen gerade Unterschriften im Speicher? (Effekt VOR
  // dem Kennungs-Effekt, damit er beim Wechsel noch den alten Stand liest.)
  const hatUnterschriften = useRef(false);
  useEffect(() => { hatUnterschriften.current = Boolean(sigDriver || sigSeller); });
  useEffect(() => {
    // Go-Live 13.09.2026 (P4): Bei null (Rueckfrage -> Entwurf, erneut zur Freigabe)
    // die letzte Kennung NICHT vergessen. Sonst loeschte der Wechsel null -> neuer
    // Stand nichts, und die unter dem ALTEN Preis geleisteten Unterschriften gingen
    // mit dem neuen Preis ab. Rueckfrage und jede Freigabe erzeugen einen neuen
    // Stand; ein gescheiterter Abschluss (gleicher Stand) behaelt die Unterschriften.
    if (freigabeKennung === null) return;
    if (vorigeKennung.current !== null && vorigeKennung.current !== freigabeKennung) {
      const warenDa = hatUnterschriften.current;
      setSigDriver(null);
      setSigSeller(null);
      setSigRunde((n) => n + 1);
      // Hinweis nur, wenn wirklich Unterschriften verworfen wurden.
      if (warenDa) {
        toast.warning("Der Händler hat Preis oder Vermerk geändert — bitte dem Verkäufer den neuen Stand "
                      + "zeigen und neu unterschreiben lassen.", { duration: 15000 });
      }
    }
    vorigeKennung.current = freigabeKennung;
  }, [freigabeKennung]);

  // Immer den AKTUELLEN Stand speichern (nie einen veralteten Klick-Zustand):
  // fRef spiegelt f nach jedem Render, der Auto-Save liest daraus.
  const fRef = useRef(f);
  useEffect(() => { fRef.current = f; });
  const sellerRef = useRef(sellerName);
  useEffect(() => { sellerRef.current = sellerName; });
  // Immer die aktuelle Fassung der Speicher-Funktionen (Timer, Aufräumen).
  const autoSpeichernRef = useRef(null);
  const speichernRef = useRef(null);

  // Ein einzelner PUT mit dem AKTUELLEN Stand (fRef) und der zuletzt vom
  // Server bestätigten Revision.
  const sendeEinmal = useCallback(async ({ nurWennGeaendert = false } = {}) => {
    const stand = fRef.current;
    const mitName = () => (nameGetippt.current || nameImEntwurf.current
      ? sellerRef.current : undefined);
    const nutz = nutzlast(stand, mitName());
    const text = nutzlastText(nutz);
    // Rollenprüfung 22.09.2026 (Review): "gesichert" nach INHALT, nicht nach
    // Objektidentität. Nach einem zusammengeführten Revisionskonflikt legt
    // setF ein inhaltsgleiches NEUES Objekt ab, der Effekt spiegelt es während
    // des PUT in fRef — der Vergleich fRef.current === stand schlug dann fehl,
    // die Seite blieb auf "wird gespeichert …" samt Verlassen-Warnung stehen.
    const nochAktuell = () => nutzlastText(nutzlast(fRef.current, mitName())) === text;
    // Autosave ohne Änderung seit dem letzten erfolgreichen Speichern: kein
    // weiterer PUT (jeder zählt die Revision hoch).
    if (nurWennGeaendert && text === zuletztGesendet.current) {
      if (nochAktuell()) setUngesichert(false);
      return null;
    }
    const r = await driverApi.put(`/driver/appointments/${id}/protocol`,
      { ...nutz, ...(revRef.current != null ? { revision: revRef.current } : {}) });
    if (r?.data?.revision != null) revRef.current = r.data.revision;
    basisRef.current = stand;
    zuletztGesendet.current = text;
    // Nur "gesichert", wenn seit dem Absenden nichts mehr getippt wurde.
    if (nochAktuell()) setUngesichert(false);
    return r;
  }, [id]);

  // Rollenprüfung 22.09.2026 (RP-061/RP-160): Vorher liefen Autosave,
  // "Speichern" und "Zur Freigabe" ohne Sperre parallel — der zweite PUT trug
  // die alte Revision, bekam 409 "anderer Tab", und load() überschrieb die
  // zuletzt getippten Antworten mit dem Serverstand. Jetzt:
  //  * alle PUTs nacheinander (Kette), jeder mit der Revision der vorigen
  //    Antwort — der eigene Wettlauf entsteht gar nicht mehr;
  //  * kommt trotzdem ein Revisionskonflikt (zweiter Tab, oder ein PUT kam an,
  //    dessen Antwort im Funkloch verloren ging): Serverstand holen, die
  //    LOKALEN Änderungen darüberlegen und EINMAL neu speichern — nichts
  //    Getipptes geht verloren.
  const speichernIntern = useCallback(async (opts) => {
    try {
      return await sendeEinmal(opts);
    } catch (e) {
      if (!istRevisionsKonflikt(e?.response?.status, errMsg(e, ""))) throw e;
      const r = await driverApi.get(`/driver/appointments/${id}/protocol`);
      const p = r?.data?.protocol;
      if (!p || (p.status || "entwurf") !== "entwurf") {
        // Inzwischen abgeschickt/abgeschlossen (anderes Gerät): der Server
        // gilt, die Seite zeigt den neuen Stand.
        setData(r.data);
        throw e;
      }
      revRef.current = p.revision ?? null;
      const server = entwurfAusServer(p);
      const lokal = fRef.current;
      const zusammen = entwurfZusammenfuehren(server, lokal, basisRef.current || server);
      basisRef.current = server;
      fRef.current = zusammen;
      // Was während des Abrufs noch getippt wurde, gewinnt ebenfalls.
      setF((s) => entwurfZusammenfuehren(zusammen, s, lokal));
      setData(r.data);
      toast.warning("Der Entwurf wurde auch auf einem anderen Gerät oder in einem anderen Tab "
                    + "gespeichert — beide Stände wurden zusammengeführt.");
      return await sendeEinmal();
    }
  }, [id, sendeEinmal]);

  const speichern = useCallback((opts) => {
    const lauf = kette.current.catch(() => {}).then(() => speichernIntern(opts));
    kette.current = lauf;
    return lauf;
  }, [speichernIntern]);

  // RP-065/RP-164: Fehler sichtbar machen. Netzfehler -> nach einer Pause
  // selbst erneut versuchen; Serverablehnung (409/403/422) -> Grund zeigen,
  // einmal als Toast, dauerhaft als Leiste mit "Erneut speichern".
  const fehlerZeigen = useCallback((e) => {
    const netz = !e?.response;
    const grund = errMsg(e, "Nicht gespeichert");
    // RP-062/RP-161: Fahrt nicht mehr angenommen -> eigener Hinweis mit Weg zur Annahme.
    setSpeicherFehler({ grund, netz, annehmen: istAnnahmeFehlt(e?.response?.status, grund) });
    if (!netz && letzteFehlerMeldung.current !== grund) {
      letzteFehlerMeldung.current = grund;
      toast.error(`Nicht gespeichert: ${grund}`);
    }
  }, []);

  const autoSpeichern = useCallback(async () => {
    saveTimer.current = null;
    clearTimeout(wiederholTimer.current);
    wiederholTimer.current = null;
    try {
      await speichern({ nurWennGeaendert: true });
      setSavedAt(new Date());
      setSpeicherFehler(null);
      letzteFehlerMeldung.current = "";
    } catch (e) {
      fehlerZeigen(e);
      if (!e?.response) {
        wiederholTimer.current = setTimeout(() => { autoSpeichernRef.current?.(); },
                                            NETZ_WIEDERHOLUNG_MS);
      } else if (e.response.status === 409
                 && !istAnnahmeFehlt(409, errMsg(e, ""))) {
        // Kein Revisionskonflikt (der ist oben zusammengeführt), sondern ein
        // geänderter Stand: abgeschickt, freigegeben, abgeschlossen, Termin
        // geschlossen. Speichern geht dann ohnehin nicht mehr — die Seite
        // zeigt den Stand, der jetzt gilt; der Grund steht im Hinweis.
        load({ still: true });
      }
    }
  }, [speichern, fehlerZeigen, load]);
  useEffect(() => { autoSpeichernRef.current = autoSpeichern; });
  useEffect(() => { speichernRef.current = speichern; });

  // Automatisch speichern (1,2 s nach der letzten Änderung)
  const queueSave = useCallback(() => {
    if (gesperrt) return;
    setUngesichert(true);
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { autoSpeichernRef.current?.(); }, 1200);
  }, [gesperrt]);

  // Beim Verlassen der Seite einen noch wartenden Autosave sofort abschicken
  // (vorher lief er nach dem Timer ins Leere oder ging ganz verloren).
  useEffect(() => () => {
    clearTimeout(wiederholTimer.current);
    if (saveTimer.current) {
      clearTimeout(saveTimer.current);
      saveTimer.current = null;
      speichernRef.current?.({ nurWennGeaendert: true }).catch(() => {});
    }
    // RP-546: was noch nicht beim Server ist (oder Unterschriften), sofort im
    // Tab sichern — das nächste Öffnen prüft, ob es noch gilt.
    if (sicherungGeprueft.current && sicherungStand.current) {
      sicherungSchreiben(idRef.current, sicherungStand.current);
    }
  }, []);

  // Rollenprüfung 22.09.2026 (RP-546): offenen Stand laufend im Tab sichern —
  // getippte Antworten, bis der Server sie hat, und Unterschriften bis zum
  // Abschluss. Endet die Sitzung mitten vor Ort (401 -> Anmeldung), stellt
  // das nächste Öffnen beides wieder her (wiederherstellen unten).
  useEffect(() => {
    const mitSig = Boolean(sigDriver || sigSeller) && unterschriften;
    const mitForm = ungesichert && !gesperrt;
    sicherungStand.current = (mitSig || mitForm) ? {
      f, basis: basisRef.current, sellerName,
      nameGetippt: nameGetippt.current, ortGetippt: ortGetippt.current,
      sigDriver: mitSig ? sigDriver : null, sigSeller: mitSig ? sigSeller : null,
      kennung: mitSig ? freigabeKennung : null,
    } : null;
    if (!sicherungGeprueft.current) return undefined;
    if (!sicherungStand.current) { sicherungLoeschen(id); return undefined; }
    const t = setTimeout(() => {
      if (sicherungStand.current) sicherungSchreiben(id, sicherungStand.current);
    }, 300);
    return () => clearTimeout(t);
  }, [id, f, sellerName, sigDriver, sigSeller, ungesichert, gesperrt, unterschriften, freigabeKennung]);

  // RP-546: Sicherung beim ersten Laden zurückholen — nur, was noch gilt.
  const wiederherstellen = (daten) => {
    const s = sicherungLesen(id);
    if (!s) return;
    const p = daten?.protocol;
    const status = p?.status || "entwurf";
    let formDa = false;
    let sigDa = false;
    if (status === "entwurf" && s.f) {
      const server = p ? entwurfAusServer(p) : { ...LEERER_ENTWURF };
      const zusammen = entwurfZusammenfuehren(server, s.f, s.basis || server);
      const name = s.nameGetippt && typeof s.sellerName === "string" ? s.sellerName : null;
      const nameAnders = name !== null && name.trim() !== String(p?.seller_name || "").trim();
      if (nameAnders || JSON.stringify(zusammen) !== JSON.stringify(server)) {
        basisRef.current = server;
        setF(zusammen);
        if (s.ortGetippt) ortGetippt.current = true;
        if (name !== null) { nameGetippt.current = true; setNameAusEntwurf(false); setSellerName(name); }
        setUngesichert(true);
        clearTimeout(saveTimer.current);
        saveTimer.current = setTimeout(() => { autoSpeichernRef.current?.(); }, 1200);
        formDa = true;
      }
    }
    // Unterschriften nur unter GENAU dem Stand, unter dem sie geleistet wurden.
    if ((status === "freigegeben" || status === "wird_abgeschlossen")
        && (s.sigDriver || s.sigSeller) && s.kennung === kennungAus(p)) {
      setSigDriver(s.sigDriver || null);
      setSigSeller(s.sigSeller || null);
      setSigStart({ runde: sigRunde, driver: s.sigDriver || null, seller: s.sigSeller || null });
      sigDa = true;
    }
    if (!formDa && !sigDa) { sicherungLoeschen(id); return; }
    toast.info(sigDa
      ? "Unterschriften aus der unterbrochenen Sitzung wiederhergestellt — bitte vor dem Abschließen prüfen."
      : "Nicht gespeicherte Eingaben wiederhergestellt — sie werden jetzt gespeichert.",
    { duration: 9000 });
  };
  useEffect(() => { wiederherstellenRef.current = wiederherstellen; });

  // RP-062/RP-161: zur Startseite, dort die geänderte Fahrt erneut annehmen.
  // Der offene Stand bleibt im Tab gesichert und kommt beim Zurückkehren wieder.
  const zurAnnahme = () => {
    if (sicherungStand.current) sicherungSchreiben(id, sicherungStand.current);
    nav(`/fahrer?fahrt=${encodeURIComponent(id)}`);
  };

  // patch darf ein Objekt ODER eine Funktion (voriger Stand -> Teilupdate)
  // sein — die Funktionsform verhindert, dass schnelle Klicks hintereinander
  // sich gegenseitig überschreiben (stale state).
  // Rollenprüfung 22.09.2026 (Review): Namen vom Termin übernehmen — gilt als
  // getippt, geht also mit dem nächsten Speichern in den Entwurf.
  const nameVomTermin = () => {
    nameGetippt.current = true;
    setNameAusEntwurf(false);
    setSellerName(terminName);
    queueSave();
  };

  const upd = (patch) => {
    setF((s) => ({ ...s, ...(typeof patch === "function" ? patch(s) : patch) }));
    queueSave();
  };
  const setDoc = (name, wert) =>
    upd((s) => ({ documents: { ...s.documents, [name]: wert } }));
  // Stufe 3 KI: Antwort auf die Rückfrage des Chefs — gespeichert wie jede
  // andere Eingabe (Autosave), danach schickt der Fahrer erneut zur Freigabe.
  // Review 26.09.2026 (Nr. 57/127/134): genau eine Antwort zur aktuellen Frage,
  // keine stille Grenze (.slice) mehr, den Zeitstempel setzt der Server.
  const rueckfrageAntworten = (o) => {
    if (!rueckfrageFrage) return;
    upd((s) => ({
      rueckfrage_antworten: [
        ...(s.rueckfrage_antworten || []).filter((a) => !istAntwortZu(a)),
        { frage_id: rueckfrageFrage.frage_id || "", source_id: rueckfrageFrage.source_id || "",
          question: rueckfrageFrage.question, answer: o },
      ],
    }));
  };
  const setFeat = (name, wert) =>
    upd((s) => ({ features: { ...s.features, [name]: wert } }));
  const setCond = (k, v) =>
    upd((s) => ({ condition: { ...s.condition, [k]: v } }));
  // Abschnitt 1: pro Zeile Status (stimmt/weicht ab) bzw. Korrekturwert
  const setVCheck = (key, feld, wert) =>
    upd((s) => ({ vehicle_check: { ...s.vehicle_check,
      [key]: { ...(s.vehicle_check?.[key] || {}), [feld]: wert } } }));

  // RP-061: einen wartenden Autosave-Timer vorher auflösen — sein Inhalt geht
  // mit diesem Speichern mit (fRef ist immer der aktuelle Stand).
  const wartendenAutosaveAbbrechen = () => {
    clearTimeout(saveTimer.current);
    saveTimer.current = null;
    clearTimeout(wiederholTimer.current);
    wiederholTimer.current = null;
  };

  const saveNow = async () => {
    wartendenAutosaveAbbrechen();
    setBusy(true);
    try {
      await speichern();
      setSavedAt(new Date());
      setSpeicherFehler(null);
      letzteFehlerMeldung.current = "";
      toast.success("Zwischenstand gespeichert");
    } catch (e) {
      const grund = errMsg(e, "Nicht gespeichert");
      const annehmen = istAnnahmeFehlt(e?.response?.status, grund);
      setSpeicherFehler({ grund, netz: !e?.response, annehmen });
      toast.error(errMsg(e));
      if (e?.response?.status === 409 && !annehmen) load({ still: true });   // Stand hat sich geändert
    }
    finally { setBusy(false); }
  };

  // Runde 30: Schritt 1 — ausgefülltes Protokoll an den Händler schicken.
  // Er prüft die Abweichungen, ruft ggf. den Verkäufer an und gibt frei.
  const zurFreigabe = async () => {
    // Review 26.09.2026 (Nr. 59): erst die Rückfrage des Chefs beantworten
    // (der Server lehnt sonst mit 400 ab).
    if (rueckfrageFrage && !String(rueckfrageAntwort || "").trim()) {
      toast.error("Bitte zuerst die Rückfrage des Chefs beantworten (oben).");
      return;
    }
    // Umbau KI 26.09.2026: jeder neue Schaden braucht alle Angaben (Groesse,
    // Lack, Tiefe ... — "unbekannt" ist erlaubt); die KI stellt keine
    // Rueckfragen mehr, sie bekommt alles aus dem Formular.
    if (!alleVollstaendig(f.new_damages || [])) {
      const offen = (f.new_damages || []).filter((d) => schwereOffen(d).length > 0)
        .map((d) => `${d.type_label || d.type_key} ${d.zone || ""}: ${schwereOffen(d).join(", ")}`.trim());
      toast.error(`Bitte bei jedem neuen Schaden alle Angaben wählen („unbekannt“ geht auch): ${offen.join(" · ")}`,
                  { duration: 9000 });
      return;
    }
    // Gegenpruefung 12.09.2026: halb getippte Daten ("06/20") nicht abschicken —
    // sie wurden sonst als 06/2020 gelesen oder unvollstaendig gedruckt.
    const vorlage = data?.template || {};
    const halb = (vorlage.vehicle_check_fields || []).filter((fld) => {
      const art = (vorlage.vehicle_check_art || {})[fld.key];
      const e = f.vehicle_check?.[fld.key] || {};
      return (art === "monat_jahr" || art === "hu") && e.status === "weicht ab" && monatJahrFehler(e.value);
    });
    if (halb.length) {
      toast.error(`Bitte vollständig eingeben (MM/JJJJ): ${halb.map((x) => x.label).join(", ")}`);
      return;
    }
    // RP-060/RP-159: ein unlesbarer Preisvorschlag darf nicht still als
    // "kein Vorschlag" (oder falscher Betrag) beim Händler landen.
    if (!preisVorschlagLesen(f.preis_vorschlag).lesbar) {
      toast.error("Der vor Ort vereinbarte Preis ist nicht lesbar — bitte z. B. 15.000 "
                  + "oder 15000,50 eingeben (oder das Feld leeren).");
      return;
    }
    if (!window.confirm("Protokoll an den Händler schicken?\n\nEr prüft die "
                        + "Abweichungen und gibt frei — danach unterschreibt "
                        + "ihr vor Ort. Bis dahin sind keine Änderungen mehr "
                        + "möglich.")) return;
    wartendenAutosaveAbbrechen();
    setBusy(true);
    try {
      await speichern();
      setSpeicherFehler(null);
      await driverApi.post(`/driver/appointments/${id}/protocol/submit`);
      toast.success("Abgeschickt — der Händler prüft jetzt");
      load();
    } catch (e) {
      toast.error(errMsg(e, "Abschicken fehlgeschlagen"));
      // RP-062: Fahrt nicht mehr angenommen -> Hinweis mit Weg zur Annahme.
      if (istAnnahmeFehlt(e?.response?.status, errMsg(e, ""))) {
        setSpeicherFehler({ grund: errMsg(e, ""), netz: false, annehmen: true });
      }
    }
    finally { setBusy(false); }
  };

  const finalize = async () => {
    if (!sigDriver || !sigSeller) {
      toast.error("Bitte beide Unterschriften erfassen"); return;
    }
    if (!window.confirm("Protokoll abschließen?\n\nDas Fahrzeug gilt danach als "
                        + "abgeholt und das PDF wird erstellt.")) return;
    setBusy(true);
    try {
      const fin = await driverApi.post(`/driver/appointments/${id}/protocol/finalize`, {
        signature_driver_b64: sigDriver,
        signature_seller_b64: sigSeller,
        // RP-059: genau die angezeigten (eingefrorenen) Werte.
        seller_name: nameAnzeige,
        place: ortAnzeige,
        // Gegenprüfung 12.09.2026: Der Händler kann den Preis ändern,
        // während vor Ort unterschrieben wird. Wir schicken den Preis mit,
        // den DIESE Ansicht gezeigt hat — weicht er ab, lehnt der Server ab,
        // statt einen anderen Betrag über die Unterschriften zu drucken.
        neuer_preis_gesehen: neuerPreis,
        // Gegenpruefung 12.09.2026: auch Vermerk und Freigabe-Stand, nicht nur den Preis.
        freigabe_stand_gesehen: data?.protocol?.freigabe_stand ?? "",
      });
      // Runde 17: der Termin kann inzwischen vom Haendler geschlossen sein —
      // das Protokoll bleibt als Beweis final, der Server sagt es.
      if (fin?.data?.hinweis) toast.warning(fin.data.hinweis, { duration: 9000 });
      else toast.success("Protokoll abgeschlossen — Fahrzeug ist abgeholt");
      // RP-546: die Unterschriften sind verbraucht — nicht erneut sichern.
      setSigDriver(null);
      setSigSeller(null);
      sicherungLoeschen(id);
      load();
    } catch (e) {
      toast.error(errMsg(e, "Abschließen fehlgeschlagen"));
      // Stand geaendert (Preis, Vermerk, zurueckgezogen): neu laden, damit
      // die App zeigt, was jetzt gilt.
      if (e?.response?.status === 409) load({ still: true });
      // Rollentest 19.09.2026: Kam die Unterschrift beschaedigt beim Server an
      // (abgebrochene Uebertragung), half ein zweiter Versuch mit demselben
      // Bild nicht — die Felder werden geleert, damit sofort neu
      // unterschrieben werden kann.
      if (e?.response?.status === 400
          && /Unterschrift/i.test(errMsg(e, ""))) {
        setSigDriver(null);
        setSigSeller(null);
        setSigRunde((n) => n + 1);
      }
    }
    finally { setBusy(false); }
  };

  const startCorrection = async () => {
    if (!window.confirm("Korrektur starten?\n\nDas bisherige Protokoll bleibt als "
                        + "Nachweis erhalten, du erstellst Version "
                        + ((data?.protocol?.version || 1) + 1) + ".")) return;
    try {
      await driverApi.post(`/driver/appointments/${id}/protocol/correction`);
      setSigDriver(null); setSigSeller(null);
      toast.success("Korrektur-Version gestartet");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  if (!data) {
    if (!ladeFehler) return <div className="p-8 text-zinc-500 text-sm">lade…</div>;
    return (
      <div className="p-4 max-w-2xl mx-auto" data-testid="protokoll-ladefehler">
        <button onClick={() => nav("/fahrer")} className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
          <ArrowLeft size={14} /> Zurück zu den Fahrten
        </button>
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2" role="alert"
             style={{ borderColor: "#ff3b3055", background: "#ff3b3014", color: "var(--st-rot)" }}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            {ladeFehler.annehmen
              ? "Der Händler hat diese Fahrt geändert (Datum, Adresse, Fahrzeug oder Verkäufer). "
                + "Bitte die Fahrt auf der Startseite prüfen und erneut annehmen — danach geht es hier weiter."
              : ladeFehler.grund}
            <div className="mt-2">
              {ladeFehler.annehmen ? (
                <button type="button" onClick={zurAnnahme} data-testid="protokoll-fahrt-annehmen"
                        className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border font-semibold"
                        style={{ borderColor: "#ff3b3088" }}>
                  Fahrt erneut annehmen
                </button>
              ) : (
                <button type="button" onClick={() => load()} data-testid="protokoll-erneut-laden"
                        className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border"
                        style={{ borderColor: "#ff3b3088" }}>
                  Erneut laden
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const oeffnePdf = (path) =>
    openDriverPdf(path).catch((e) => toast.error(errMsg(e)));

  const tpl = data.template || {};
  const preisErkannt = preisVorschlagLesen(f.preis_vorschlag);
  // RP-068/RP-167: Korrektur nur bei (wieder) offenem Termin anbieten.
  const terminGeschlossen = TERMIN_GESCHLOSSEN.includes(data.appointment?.status || "");
  const veh = data.vehicle || {};
  const appt = data.appointment || {};
  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };

  return (
    <div className="p-4 max-w-2xl mx-auto" data-testid="protokoll-page"
         style={{ paddingBottom: "calc(var(--fahrer-tabs, 3.75rem) + 7.5rem + env(safe-area-inset-bottom, 0px))" }}>
      {/* Handy-Ansicht (24.09.2026): Zurueck-Link und Papier-PDF 44 px hoch,
          lange Fahrzeugnamen/Adressen brechen um statt die Zeile zu sprengen. */}
      <button onClick={() => nav("/fahrer")}
              className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white tipp-h -ml-1 px-1">
        <ArrowLeft size={14} /> Zurück zu den Fahrten
      </button>

      <div className="mt-1 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="overline">Abhol-Protokoll{data.protocol?.version > 1 ? ` · Version ${data.protocol.version}` : ""}</div>
          <h1 className="font-display font-black text-2xl tracking-tighter mt-1 break-words">
            {veh.make_label} {veh.model_label}
          </h1>
          <div className="text-xs text-zinc-500 mt-0.5 break-words">
            {appt.pickup_date} {appt.pickup_time} · {appt.pickup_address}
          </div>
        </div>
        <button onClick={() => oeffnePdf(`/driver/appointments/${id}/pickup-order.pdf`)}
                className="shrink-0 inline-flex items-center gap-1.5 rounded-lg px-3 tipp-h text-xs border"
                style={st}>
          <FileText size={13} /> Papier-PDF
        </button>
      </div>

      {/* Runde 30: Wo steht das Protokoll gerade? */}
      {wartetAufFreigabe && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2"
             data-testid="protokoll-wartet"
             style={{ borderColor: "#ff9f0a55", background: "#ff9f0a14", color: "var(--st-amber)" }}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            Beim Händler zur Freigabe — er prüft die Abweichungen und meldet sich.
            <div className="mt-2">
              <button onClick={() => load()}
                      className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border"
                      style={{ borderColor: "#ff9f0a55" }}>
                Aktualisieren
              </button>
            </div>
          </div>
        </div>
      )}
      {freigegeben && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2"
             data-testid="protokoll-freigegeben"
             style={{ borderColor: "#34c75955", background: "#34c75914", color: "var(--st-gruen)" }}>
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            Freigegeben{neuerPreis != null ? ` — neuer Preis ${preisText(neuerPreis)}` : ""}.
            Jetzt unterschreiben lassen (Abschnitt 8).
            {data?.protocol?.preis_notiz && (
              <div className="mt-1 text-[11px] opacity-80">{data.protocol.preis_notiz}</div>
            )}
          </div>
        </div>
      )}
      {/* Wunsch Ahmad 25.09.2026 (abends): ab dem Abschicken sieht auch der
          Fahrer die KI-Auswertung (Nachlass je Abweichung und gesamt) — nur
          lesend, die Bewertung laeuft seit dem Abschicken im Hintergrund. */}
      {(wartetAufFreigabe || freigegeben || wirdAbgeschlossen || isFinal) && (
        <KiFahrerKarte apptId={id} preisVertrag={data?.preis_vertrag}
                       preisVorschlag={data?.protocol?.preis_vorschlag ?? null} />
      )}
      {wirdAbgeschlossen && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2"
             data-testid="protokoll-wird-abgeschlossen"
             style={{ borderColor: "#0a84ff55", background: "#0a84ff14", color: "var(--tx-blau)" }}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            Wird gerade abgeschlossen — bitte einen Moment warten. Die Ansicht
            aktualisiert sich von selbst.
          </div>
        </div>
      )}
      {unbekannt && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2"
             data-testid="protokoll-unbekannt"
             style={{ borderColor: "#ff9f0a55", background: "#ff9f0a14", color: "var(--st-amber)" }}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            {/* RP-071/RP-170: fail-closed — unbekannter Stand ist nicht bearbeitbar. */}
            Das Protokoll hat einen Stand, den diese App nicht kennt — Eingaben sind gesperrt.
            Die Ansicht aktualisiert sich von selbst; sonst bitte die App neu laden.
          </div>
        </div>
      )}
      {!!rueckfrage && !gesperrt && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2"
             data-testid="protokoll-rueckfrage"
             style={{ borderColor: "#ff3b3055", background: "#ff3b3014", color: "var(--st-rot)" }}>
          <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            Der Händler bittet um eine Ergänzung: {rueckfrage}
            {/* Stufe 3 KI (26.09.2026): Antwort per Knopf statt Freitext */}
            {rueckfrageFrage && (
              <div className="mt-2" data-testid="protokoll-rueckfrage-frage">
                <div className="font-semibold">{rueckfrageFrage.question}</div>
                {rueckfrageOptionen ? (
                  <div className="mt-1.5 flex flex-wrap gap-2">
                    {rueckfrageOptionen.map((o) => {
                      const aktiv = rueckfrageAntwort === o;
                      return (
                        <button key={o} type="button" onClick={() => rueckfrageAntworten(o)}
                                data-testid={`protokoll-rueckfrage-antwort-${o}`}
                                className="min-h-[40px] px-3 rounded-lg text-sm font-semibold border"
                                style={aktiv
                                  ? { background: "var(--accent-red)", color: "#fff", borderColor: "var(--accent-red)" }
                                  : { borderColor: "var(--border-default)", color: "var(--text-primary)", background: "var(--wa-03)" }}>
                          {o}
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  // Nr. 125: Freitext-Frage des Chefs (oder ältere Frage ohne Optionen)
                  <input value={rueckfrageAntwort} maxLength={300}
                         onChange={(e) => rueckfrageAntworten(e.target.value)}
                         data-testid="protokoll-rueckfrage-freitext"
                         className="mt-1.5 w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none"
                         style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}
                         placeholder="Deine Antwort" />
                )}
                {rueckfrageAntwort && (
                  <div className="mt-1.5 text-[12px]" data-testid="protokoll-rueckfrage-gespeichert"
                       style={{ color: "var(--text-primary)" }}>
                    Deine Antwort: <b>{rueckfrageAntwort}</b> — danach unten erneut „Zur Freigabe schicken“.
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
      {/* Review 26.09.2026 (Nr. 60-62/127): frühere Rückfrage-Runden — alle, aufklappbar */}
      {rueckfrageVerlauf.length > 0 && (
        <details className="mt-3 rounded-xl border px-4 py-2 text-sm" data-testid="protokoll-rueckfrage-verlauf"
                 style={{ borderColor: "var(--border-default)" }}>
          <summary className="cursor-pointer text-xs text-zinc-500">
            Frühere Rückfragen des Händlers ({rueckfrageVerlauf.length})
          </summary>
          <ul className="mt-2 space-y-1 text-[12px]">
            {rueckfrageVerlauf.map((r, i) => (
              <li key={r?.frage?.frage_id || i}>
                {r?.frage?.question}{" "}
                <b>{(r?.antworten || []).map((a) => a?.answer).filter(Boolean).join(", ") || "—"}</b>
              </li>
            ))}
          </ul>
        </details>
      )}
      {isFinal && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-start gap-2"
             style={{ borderColor: "#34c75955", background: "#34c75914", color: "var(--st-gruen)" }}>
          <CheckCircle2 size={16} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            Protokoll abgeschlossen — Fahrzeug gilt als abgeholt.
            <div className="mt-2 flex flex-wrap gap-2">
              <button onClick={() => oeffnePdf(`/driver/appointments/${id}/protocol.pdf`)}
                      className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                      style={{ background: "var(--accent-red)" }}>
                <FileText size={13} /> Ausgefülltes PDF öffnen
              </button>
              {terminGeschlossen ? (
                <span className="self-center text-[11px] opacity-80" data-testid="protokoll-korrektur-hinweis">
                  Korrektur nur nach Wiederöffnen durch den Händler.
                </span>
              ) : (
                <button onClick={startCorrection} data-testid="protokoll-korrektur-starten"
                        className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs border text-zinc-200"
                        style={st}>
                  <Pencil size={13} /> Korrektur starten
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* 1 Fahrzeugdaten — alle 12 Zeilen wie im PDF, einzeln ankreuzbar */}
      <Section n="1" title="Fahrzeugdaten"
               hint="Jede Zeile mit dem Vertrag abgleichen: stimmt oder weicht ab. Bei Abweichung den richtigen Wert eintippen.">
        <div className="space-y-3">
          {(tpl.vehicle_check_fields || []).map((fld) => {
            const istWert = (tpl.vehicle_check_values || {})[fld.key];
            const entry = f.vehicle_check?.[fld.key] || {};
            // Runde 33: Korrekturfeld nur bei "weicht ab" — nicht bei Ja/Nein.
            const abweichend = entry.status === "weicht ab";
            const art = (tpl.vehicle_check_art || {})[fld.key] || "text";
            return (
              <div key={fld.key} className="pb-2 border-b" style={{ borderColor: "var(--wa-06)" }}>
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[11px] text-zinc-500">{fld.label}</span>
                  <span className="text-sm text-right">
                    <span className="text-[10px] text-zinc-500 mr-1">laut Vertrag</span>{istWert || "—"}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1.5 mt-1.5">
                  {fld.options.map((o) => (
                    <button key={o} type="button" disabled={gesperrt}
                            data-testid={`vc-${fld.key}-${o}`}
                            onClick={() => {
                              setVCheck(fld.key, "status", o);
                              // Runde 33: Zurueck auf "stimmt" — der alte Korrekturwert
                              // landete sonst trotzdem im unterschriebenen PDF.
                              if (o !== "weicht ab" && entry.value) setVCheck(fld.key, "value", "");
                            }}
                            className={`px-3.5 tipp-40 rounded-lg text-xs border disabled:opacity-60 ${
                              entry.status === o ? "bg-white/15 font-semibold text-white" : "text-zinc-400"}`}
                            style={st}>
                      {o}
                    </button>
                  ))}
                </div>
                {abweichend && (art === "monat_jahr" || art === "hu" ? (
                  // Wunsch Ahmad: nur Ziffern, der "/" kommt von selbst (MM/JJJJ).
                  <div className="mt-1.5">
                    <MonatJahrEingabe value={entry.value || ""} disabled={gesperrt}
                                      art={art === "hu" ? "hu" : "ez"}
                                      onChange={(v) => setVCheck(fld.key, "value", v)}
                                      className={inputCls} style={st}
                                      testid={`vc-${fld.key}-wert`} />
                    {art === "hu" && (
                      // Gegenpruefung: "keine HU" vor Ort liess sich mit Ziffern nicht erfassen.
                      <button type="button" disabled={gesperrt}
                              data-testid={`vc-${fld.key}-keine`}
                              onClick={() => setVCheck(fld.key, "value", "keine HU")}
                              className={`mt-1.5 px-3.5 tipp-40 rounded-lg text-xs border disabled:opacity-60 ${
                                entry.value === "keine HU" ? "bg-white/15 font-semibold text-white" : "text-zinc-400"}`}
                              style={st}>
                        keine HU
                      </button>
                    )}
                  </div>
                ) : (
                  <input value={entry.value || ""} disabled={gesperrt}
                         inputMode={art === "km" || art === "anzahl" ? "numeric" : undefined}
                         data-testid={`vc-${fld.key}-wert`}
                         onChange={(e) => setVCheck(fld.key, "value",
                           art === "km" || art === "anzahl" ? e.target.value.replace(/[^0-9]/g, "") : e.target.value)}
                         className={`${inputCls} mt-1.5`} style={st}
                         placeholder={art === "km" ? "Kilometerstand vor Ort, z. B. 86000"
                           : art === "anzahl" ? "Anzahl laut Schein" : "Richtiger Wert vor Ort …"} />
                ))}
              </div>
            );
          })}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
          {[["Modellbezeichnung", veh.model_description],
            ["Getriebe", veh.gearbox_label]].map(([k, v]) => (
            <div key={k}>
              <div className="text-[11px] text-zinc-500">{k}</div>
              <div>{v || "—"}</div>
            </div>
          ))}
        </div>
      </Section>

      {/* 2 Dokumente */}
      <Section n="2" title="Dokumente & Zubehör" hint="Vor Ort einsammeln — bei jedem Punkt Ja oder Nein.">
        {(tpl.documents || []).map((doc) => (
          <JaNein key={doc} disabled={gesperrt} wert={f.documents[doc]}
                  onChange={(w) => setDoc(doc, w)}>{doc}</JaNein>
        ))}
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-zinc-500">Schlüssel erhalten</label>
            <input type="number" inputMode="numeric" value={f.keys_count} disabled={gesperrt}
                   onChange={(e) => upd({ keys_count: e.target.value })}
                   className={inputCls} style={st} placeholder="z.B. 2" />
          </div>
          <div>
            {/* Review 26.09.2026 (Nr. 101-105): Sollwert aus dem Kaufvertrag — nur Anzeige */}
            <label className="text-[11px] text-zinc-500">laut Vertrag vereinbart</label>
            <div className={inputCls} style={st} data-testid="protokoll-schluessel-vereinbart">
              {schluesselVereinbart === null || schluesselVereinbart === "" ? "—" : String(schluesselVereinbart)}
            </div>
          </div>
        </div>
      </Section>

      {/* 3 Ausstattung */}
      {(tpl.features || []).length > 0 && (
        <Section n="3" title="Ausstattung laut Inserat" hint="Vorhanden? Bei jedem Punkt Ja oder Nein.">
          {tpl.features.map((ft) => {
            const wert = f.features[ft];
            const nein = wert === false || typeof wert === "string";
            const art = typeof wert === "string" ? wert : (wert === false ? "fehlt" : "");
            return (
              <div key={ft}>
                <JaNein disabled={gesperrt} wert={nein ? false : wert}
                        onChange={(w) => setFeat(ft, w ? true : "fehlt")}>{ft}</JaNein>
                {/* Umbau KI 26.09.2026: bei "Nein" die Art — fehlt komplett, vorhanden
                    aber defekt, anders als beschrieben — fuer den Geldwert entscheidend. */}
                {nein && !gesperrt && (
                  <div className="flex flex-wrap gap-1.5 pb-2 -mt-1" data-testid={`protokoll-ausstattung-art-${ft}`}>
                    {[["fehlt", "fehlt komplett"], ["defekt", "vorhanden, defekt"], ["anders", "anders als beschrieben"]].map(([k, l]) => (
                      <button key={k} type="button" onClick={() => setFeat(ft, k)}
                              data-testid={`protokoll-ausstattung-${ft}-${k}`}
                              className="px-2.5 min-h-[32px] rounded-lg text-[11px] border"
                              style={art === k
                                ? { borderColor: "var(--st-rot)", color: "var(--st-rot)", background: "color-mix(in srgb, var(--st-rot) 18%, transparent)" }
                                : { borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                        {l}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </Section>
      )}

      {/* 4 Technischer Zustand */}
      <Section n="4" title="Technischer Zustand">
        <div className="space-y-3">
          {(tpl.condition_fields || []).map((fld) => (
            <div key={fld.key}>
              <label className="text-[11px] text-zinc-500">{fld.label}</label>
              {fld.options ? (
                <div className="flex flex-wrap gap-1.5 mt-1">
                  {fld.options.map((o) => (
                    <button key={o} type="button" disabled={gesperrt}
                            onClick={() => setCond(fld.key, o)}
                            className={`px-3.5 tipp-40 rounded-lg text-xs border disabled:opacity-60 ${
                              f.condition[fld.key] === o ? "bg-white/15 font-semibold text-white" : "text-zinc-400"}`}
                            style={st}>
                      {o}
                    </button>
                  ))}
                </div>
              ) : (
                <input value={f.condition[fld.key] ?? ""} disabled={gesperrt}
                       inputMode={fld.key === "mileage" ? "numeric" : undefined}
                       onChange={(e) => setCond(fld.key, fld.key === "mileage"
                         ? e.target.value.replace(/[^0-9]/g, "") : e.target.value)}
                       className={inputCls} style={st}
                       placeholder={fld.key === "mileage" ? "z.B. 85120" : ""} />
              )}
            </div>
          ))}
        </div>
      </Section>

      {/* 5 Vorbestehende Schäden */}
      <Section n="5" title="Vorbestehende Schäden" hint="Laut Kaufvertrag dokumentiert.">
        {(data.damages || []).length === 0 ? (
          <div className="text-sm text-zinc-500">Keine Schäden im Vertrag vermerkt.</div>
        ) : (
          <div className="space-y-1 text-sm">
            {data.damages.map((d, i) => (
              <div key={i} className="text-amber-400">
                • {[d.type_label || d.label || d.type, d.zone].filter(Boolean).join(" — ") || "Schaden"}
              </div>
            ))}
          </div>
        )}
        {/* Rollenprüfung 22.09.2026 (RP-067/RP-166): Ja/Nein statt Einzel-Haken.
            Vorher startete die Frage als "Nein" (false) und galt damit für den
            Server immer als beantwortet — die Pflichtprüfung griff nie. */}
        <JaNein disabled={gesperrt} wert={f.damages_confirmed} testId="protokoll-schaeden-bestaetigt"
                onChange={(w) => upd({ damages_confirmed: w })}>
          Zustand entspricht der Dokumentation
        </JaNein>
      </Section>

      {/* 6 Vor-Ort-Aufnahme: neue Schäden per Tipp auf die Skizze markieren —
          dieselbe Skizze wie im Kaufvertrag. Landet im PDF (Abschnitt 6). */}
      <Section n="6" title="Vor-Ort-Aufnahme"
               hint="Neue Schäden? Art wählen und auf die Fahrzeug-Skizze tippen — genau wie im Kaufvertrag.">
        {/* Gegenprüfung 12.09.2026: Abschnitt 6 war in den gesperrten
            Zuständen weiter bedienbar — Tipps auf die Skizze wurden nie
            gespeichert und gingen still verloren. Jetzt nur noch Anzeige. */}
        {gesperrt ? (
          (f.new_damages || []).length === 0 ? (
            <div className="text-sm text-zinc-500">Keine neuen Schäden erfasst.</div>
          ) : (
            <div className="space-y-1 text-sm">
              {f.new_damages.map((d, i) => (
                <div key={d.id || i} className="text-red-400">
                  • {[d.type_label, d.zone].filter(Boolean).join(" — ")}
                </div>
              ))}
            </div>
          )
        ) : (
          <DamageSelector
            damages={f.new_damages || []}
            onChange={(list) => upd({ new_damages: list })}
          />
        )}
        <div className="text-xs text-zinc-500 mt-3">
          Fotos zu Abweichungen machst du zusätzlich im Abhol-Check —
          sie erscheinen automatisch in der Fahrzeugakte des Händlers.
        </div>
      </Section>

      {/* 7 Bemerkungen */}
      <Section n="7" title="Bemerkungen">
        {/* Rollenprüfung 22.09.2026 (RP-535): Servergrenze sichtbar (vorher 422
            erst beim Speichern), mit Zähler. */}
        <textarea value={f.notes} disabled={gesperrt} rows={4} data-testid="protokoll-bemerkungen"
                  maxLength={BEMERKUNG_MAX}
                  onChange={(e) => upd({ notes: e.target.value })}
                  className={inputCls} style={st}
                  placeholder="Auffälligkeiten, Absprachen, Zustand …" />
        {!gesperrt && (
          <div className="mt-1 text-right text-[10px] text-zinc-500" data-testid="protokoll-bemerkungen-zaehler"
               style={{ color: String(f.notes || "").length >= BEMERKUNG_MAX ? "var(--st-rot)" : undefined }}>
            {String(f.notes || "").length} / {BEMERKUNG_MAX}
          </div>
        )}
      </Section>

      {/* 8 Kaufpreis & Übergabe */}
      <Section n="8" title="Kaufpreis & Übergabe-Bestätigung"
               hint="Erst nach der Freigabe des Händlers unterschreiben — dann gilt der freigegebene Preis.">
        <div className="grid grid-cols-2 gap-3 mb-3">
          <div className="rounded-lg border px-3 py-2" style={st}>
            <div className="text-[11px] text-zinc-500">Preis laut Vertrag</div>
            <div className="text-sm">{preisText(data?.preis_vertrag)}</div>
          </div>
          <div className="rounded-lg border px-3 py-2"
               style={{ borderColor: neuerPreis != null ? "#34c75988" : "var(--border-default)" }}>
            <div className="text-[11px] text-zinc-500">Neuer Preis (nach Verhandlung)</div>
            <div className="text-sm" data-testid="protokoll-neuer-preis"
                 style={{ color: neuerPreis != null ? "var(--st-gruen)" : undefined }}>
              {neuerPreis != null ? preisText(neuerPreis) : "unverändert"}
            </div>
          </div>
        </div>
        {data?.protocol?.preis_notiz && (
          <div className="text-[11px] text-zinc-400 mb-3">
            Vermerk des Händlers: {data.protocol.preis_notiz}
          </div>
        )}
        {/* Wunsch Ahmad 14.09.2026: Preis und Sondervereinbarung auch vom Fahrer vor Ort.
            Der Händler sieht beides bei der Freigabe; gibt er ohne eigenen Preis frei,
            gilt der hier eingetragene. */}
        <div className="grid grid-cols-1 gap-3 mb-3">
          <div>
            <label className="text-[11px] text-zinc-500">Vor Ort vereinbarter Preis (optional, Vorschlag an den Händler)</label>
            <input type="text" inputMode="decimal" disabled={gesperrt}
                   data-testid="protokoll-preis-vorschlag"
                   value={f.preis_vorschlag ?? ""}
                   onChange={(e) => upd({ preis_vorschlag: e.target.value.replace(/[^0-9.,]/g, "") })}
                   className={inputCls} style={st} placeholder="z. B. 15.000" />
            {/* RP-060/RP-159: zeigen, welcher Betrag erkannt wurde ("15.000" = 15.000 €). */}
            {String(f.preis_vorschlag ?? "").trim() !== "" && (
              preisErkannt.lesbar ? (
                <div className="mt-1 text-[11px] text-zinc-400" data-testid="protokoll-preis-erkannt">
                  erkannt: {preisText(preisErkannt.wert)}
                </div>
              ) : (
                <div className="mt-1 text-[11px]" style={{ color: "var(--st-rot)" }}
                     data-testid="protokoll-preis-unlesbar">
                  Betrag nicht lesbar — bitte z. B. 15.000 oder 15000,50 eingeben.
                </div>
              )
            )}
          </div>
          <div>
            <label className="text-[11px] text-zinc-500">Sondervereinbarung vor Ort (erscheint im Protokoll)</label>
            <textarea value={f.sondervereinbarung || ""} disabled={gesperrt} rows={2}
                      data-testid="protokoll-sondervereinbarung"
                      onChange={(e) => upd({ sondervereinbarung: e.target.value })}
                      className={inputCls} style={st}
                      placeholder="z. B. Verkäufer liefert Winterreifen nach" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-zinc-500">Ort</label>
            {/* RP-059/RP-158: ab "zur Freigabe" gesperrt, Wert wie im PDF. */}
            <input value={ortAnzeige} disabled={gesperrt} data-testid="protokoll-ort"
                   onChange={(e) => { ortGetippt.current = true; upd({ place: e.target.value }); }}
                   className={inputCls} style={st} placeholder="z.B. Hannover" />
          </div>
          <div>
            <label className="text-[11px] text-zinc-500">Name Verkäufer</label>
            <input value={nameAnzeige} disabled={gesperrt} data-testid="protokoll-verkaeufer"
                   onChange={(e) => {
                     nameGetippt.current = true; setNameAusEntwurf(false);
                     setSellerName(e.target.value); queueSave();
                   }}
                   className={inputCls} style={st} />
          </div>
        </div>
        {!gesperrt && nameAusEntwurf && terminName && sellerName.trim() !== terminName && (
          <div className="mt-1.5 text-[11px] text-zinc-400" data-testid="protokoll-name-termin">
            Am Termin steht „{terminName}“.{" "}
            <button type="button" onClick={nameVomTermin} className="underline font-semibold"
                    data-testid="protokoll-name-termin-uebernehmen">
              Übernehmen
            </button>
          </div>
        )}
        {/* Entscheidung Ahmad 22.09.2026 (Ausweisnummer): vor Ort nachtragen, Autosave
            wie der Name (Teil des Entwurfs), ab "zur Freigabe" gesperrt. Steht im
            Protokoll-PDF neben dem Verkäufer und in der neuen Vertragsfassung. */}
        <div className="mt-3">
          <label className="text-[11px] text-zinc-500">Ausweisnummer des Verkäufers (optional)</label>
          <input value={ausweisAnzeige} disabled={gesperrt} data-testid="protokoll-ausweis"
                 maxLength={60} autoComplete="off" spellCheck={false}
                 onChange={(e) => upd({ seller_id_document: e.target.value })}
                 className={inputCls} style={st} placeholder="z. B. L01X00T47" />
        </div>
        {gesperrt && !isFinal && (
          <div className="mt-1.5 text-[11px] text-zinc-500">
            Ort, Name und Ausweisnummer stehen so im Protokoll. Für eine Korrektur muss der
            Händler das Protokoll zurückschicken.
          </div>
        )}
        {unterschriften && (
          // Go-Live 13.09.2026 (N1): waehrend des Abschlusses sichtbar, aber gesperrt.
          <div className={`mt-4 space-y-4 ${wirdAbgeschlossen ? "pointer-events-none opacity-50" : ""}`}
               aria-disabled={wirdAbgeschlossen || undefined}>
            {/* RP-546: nach einer Neuanmeldung die gesicherten Unterschriften zeigen. */}
            <SignaturePad key={`v${sigRunde}`} label="Unterschrift Verkäufer" onChange={setSigSeller}
                          startBild={sigStart?.runde === sigRunde ? sigStart.seller : null} />
            <SignaturePad key={`f${sigRunde}`} label="Unterschrift Fahrer" onChange={setSigDriver}
                          startBild={sigStart?.runde === sigRunde ? sigStart.driver : null} />
          </div>
        )}
        {!isFinal && !unterschriften && (
          <div className="mt-4 text-[11px] text-zinc-500">
            Die Unterschriftsfelder erscheinen, sobald der Händler freigegeben hat.
          </div>
        )}
      </Section>

      {/* Fixe Aktionsleiste — Runde 30: in JEDEM Zustand sichtbar.
          Vorher verschwand sie beim fertigen Protokoll ganz, und auf dem
          Handy lag sie unter der Systemleiste (fehlendes safe-area).
          Die Knoepfe richten sich nach dem Stand des Protokolls. */}
      {/* Gegenpruefung 12.09.2026: Die Leiste hatte KEINEN z-index und lag
          damit unter der Fahrer-Tableiste (z-40) — genau der gemeldete
          Fehler 'die Knoepfe unten sind nicht sichtbar'. Jetzt z-50 UND
          oberhalb der Tabs, damit beide bedienbar bleiben. */}
      <div className="fixed left-0 right-0 px-3 py-3 flex gap-2 z-50"
           data-testid="protokoll-aktionen"
           style={{ background: "var(--bg-elevated)",
                    borderTop: "1px solid var(--wa-08)",
                    bottom: "calc(var(--fahrer-tabs, 3.75rem) + env(safe-area-inset-bottom, 0px))" }}>
        {isFinal ? (
          <>
            <button onClick={() => oeffnePdf(`/driver/appointments/${id}/protocol.pdf`)}
                    data-testid="protokoll-pdf-unten"
                    className="flex-1 rounded-xl py-3 text-sm font-semibold text-white inline-flex items-center justify-center gap-2"
                    style={{ background: "var(--accent-red)" }}>
              <FileText size={15} /> PDF öffnen
            </button>
            {!terminGeschlossen && (
              <button onClick={startCorrection} data-testid="protokoll-korrektur-unten"
                      className="flex-1 rounded-xl py-3 text-sm border inline-flex items-center justify-center gap-2"
                      style={st}>
                <Pencil size={15} /> Korrektur
              </button>
            )}
          </>
        ) : wartetAufFreigabe ? (
          <button onClick={load} disabled={busy}
                  data-testid="protokoll-warten-aktualisieren"
                  className="flex-1 rounded-xl py-3 text-sm border inline-flex items-center justify-center gap-2 disabled:opacity-50"
                  style={{ ...st, color: "var(--st-amber)" }}>
            <AlertTriangle size={15} /> Wartet auf Freigabe · aktualisieren
          </button>
        ) : unbekannt ? (
          <button onClick={() => load()} disabled={busy}
                  data-testid="protokoll-unbekannt-aktualisieren"
                  className="flex-1 rounded-xl py-3 text-sm border inline-flex items-center justify-center gap-2 disabled:opacity-50"
                  style={{ ...st, color: "var(--st-amber)" }}>
            <AlertTriangle size={15} /> Stand unbekannt · aktualisieren
          </button>
        ) : wirdAbgeschlossen ? (
          <button onClick={() => load()} disabled={busy}
                  data-testid="protokoll-abschluss-aktualisieren"
                  className="flex-1 rounded-xl py-3 text-sm border inline-flex items-center justify-center gap-2 disabled:opacity-50"
                  style={{ ...st, color: "var(--tx-blau)" }}>
            <AlertTriangle size={15} /> Wird abgeschlossen · aktualisieren
          </button>
        ) : freigegeben ? (
          <button onClick={finalize} disabled={busy}
                  data-testid="protokoll-abschliessen"
                  className="flex-1 rounded-xl py-3 text-sm font-semibold text-white inline-flex items-center justify-center gap-2 disabled:opacity-50"
                  style={{ background: "var(--accent-red)" }}>
            <CheckCircle2 size={16} /> Unterschrieben — abschließen
          </button>
        ) : (
          <>
            <button onClick={saveNow} disabled={busy} data-testid="protokoll-speichern"
                    className="flex-1 rounded-xl py-3 text-sm border inline-flex items-center justify-center gap-2 disabled:opacity-50"
                    style={st}>
              <Save size={15} /> Speichern
            </button>
            <button onClick={zurFreigabe} disabled={busy}
                    data-testid="protokoll-zur-freigabe"
                    className="flex-1 rounded-xl py-3 text-sm font-semibold text-white inline-flex items-center justify-center gap-2 disabled:opacity-50"
                    style={{ background: "var(--accent-red)" }}>
              <CheckCircle2 size={16} /> Zur Freigabe senden
            </button>
          </>
        )}
      </div>
      {/* Rollenprüfung 22.09.2026 (RP-065/RP-164): Vorher blieb hier nach einem
          gescheiterten Autosave "gespeichert HH:MM" vom letzten Erfolg stehen —
          der Fahrer hielt ungespeicherte Antworten für sicher. */}
      {speicherFehler && !gesperrt ? (
        <div className="fixed left-3 right-3 z-50 rounded-lg px-3 py-2 text-xs flex items-center gap-2"
             role="alert" data-testid="protokoll-nicht-gespeichert"
             style={{ bottom: "calc(var(--fahrer-tabs, 3.75rem) + 4.6rem + env(safe-area-inset-bottom, 0px))",
                      background: "var(--bg-elevated)", border: "1px solid #ff3b3088",
                      color: "var(--st-rot)" }}>
          <AlertTriangle size={14} className="shrink-0" />
          {speicherFehler.annehmen ? (
            <>
              {/* Rollenprüfung 22.09.2026 (RP-062/RP-161): vorher nur der Servertext
                  und "Erneut speichern" — das half nie, die Fahrt muss erst
                  wieder angenommen werden. */}
              <span className="flex-1" data-testid="protokoll-annahme-fehlt">
                Nicht gespeichert — der Händler hat die Fahrt geändert. Bitte auf der Startseite
                prüfen und erneut annehmen; deine Eingaben bleiben auf diesem Gerät gesichert.
              </span>
              <button type="button" onClick={zurAnnahme}
                      data-testid="protokoll-fahrt-annehmen"
                      className="shrink-0 rounded-md border px-2 py-1 font-semibold"
                      style={{ borderColor: "#ff3b3088" }}>
                Fahrt erneut annehmen
              </button>
            </>
          ) : (
            <>
              <span className="flex-1">
                Nicht gespeichert — {speicherFehler.grund}
                {speicherFehler.netz ? " Wird gleich automatisch erneut versucht." : ""}
              </span>
              <button type="button" onClick={saveNow} disabled={busy}
                      data-testid="protokoll-erneut-speichern"
                      className="shrink-0 rounded-md border px-2 py-1 font-semibold disabled:opacity-50"
                      style={{ borderColor: "#ff3b3088" }}>
                Erneut speichern
              </button>
            </>
          )}
        </div>
      ) : savedAt && !gesperrt && (
        <div className="fixed right-4 text-[10px] text-zinc-600 z-50" data-testid="protokoll-gespeichert"
             style={{ bottom: "calc(var(--fahrer-tabs, 3.75rem) + 4.6rem + env(safe-area-inset-bottom, 0px))" }}>
          {ungesichert ? "wird gespeichert …" : `gespeichert ${savedAt.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}`}
        </div>
      )}
      {freigegeben && (!sigDriver || !sigSeller) && (
        <div className="mt-3 text-[11px] text-amber-400/80 inline-flex items-center gap-1.5">
          <AlertTriangle size={12} /> Zum Abschließen werden beide Unterschriften benötigt.
        </div>
      )}
    </div>
  );
}
