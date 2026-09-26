import MonatJahrEingabe from "@/components/MonatJahrEingabe";
import { monatJahrFehler } from "@/lib/monatJahr";
import { useUngespeichert } from "@/lib/ungespeichert";
import { useEffect, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useMarktHinweis } from "@/components/MarktdatenKarte";
import { blobOeffnen } from "@/lib/dateiOeffnen";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { X, Eye, FileText, Loader2, AlertTriangle, ExternalLink } from "lucide-react";
import DamageSelector, { damagesToText } from "./DamageSelector";
import KiSchadenKarte from "./KiSchadenKarte";
import { vorschlaegeAnwenden } from "@/lib/kiSchaden";
import { fehlendeKaeuferfelder, kaeuferAktualisieren, kaeuferAusProfil } from "@/lib/kaeuferdaten";
import { kmAusText, preisAusText, preisText } from "@/lib/preis";
import { openContractPdf } from "@/lib/pdf";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";

const YN_OPTIONS = [
  { value: "", label: "—" },
  { value: "Ja", label: "Ja" },
  { value: "Nein", label: "Nein" },
];

// Wunsch Ahmad (15.09.2026): Scheckheftgepflegt als Auswahl; bei "teilweise"
// zusaetzlich Monat/Jahr, bis zu dem das Scheckheft gefuehrt wurde.
const SCHECKHEFT_OPTIONS = [
  { value: "", label: "—" },
  { value: "ja", label: "Ja, lückenlos" },
  { value: "nein", label: "Nein" },
  { value: "teilweise", label: "Teilweise (bis Monat/Jahr)" },
];

const TIRE_OPTIONS = [
  { value: "", label: "—" },
  { value: "4-fach", label: "4-fach (1 Satz)" },
  { value: "8-fach", label: "8-fach (Sommer + Winter)" },
  { value: "keine", label: "Keine / nicht enthalten" },
];

// Runde 22 (11.09.2026): Zulassungsstatus wie auf Ahmads Vertragsvorlage.
const ZULASSUNG_OPTIONS = [
  { value: "", label: "—" },
  { value: "angemeldet", label: "Angemeldet" },
  { value: "abgemeldet", label: "Abgemeldet" },
];

// Runde 22 (11.09.2026): Zahlungsart als Auswahl statt Freitext
// "Bar / Überweisung" — leer = noch nicht gewählt (Pflicht beim Erstellen).
const PAYMENT_OPTIONS = [
  { value: "", label: "— bitte wählen —" },
  { value: "Bar", label: "Bar" },
  { value: "Überweisung", label: "Überweisung" },
  { value: "Echtzeitüberweisung", label: "Echtzeitüberweisung" },
];

// Runde 33 (Wunsch Ahmad): HU und Erstzulassung ueber MonatJahrEingabe —
// MM/JJJJ, nur Ziffern, "/" automatisch. Der fruehere formatHuDate liess den
// Monat ungeprueft, machte aus eingefuegtem "2026-06" "20/2606" und liess sich
// am "/" nicht loeschen (Analyse 12.09.2026).

// Numerische Helper für Vorhalter (nur ganze Zahlen, max 2 Stellen).
const cleanIntStr = (raw, max = 2) => {
  if (raw === undefined || raw === null) return "";
  return String(raw).replace(/\D/g, "").slice(0, max);
};

// Runde 22 (11.09.2026): heutiges LOKALES Datum als JJJJ-MM-TT für die
// Empfangsbestätigung. Bewusst nicht toISOString() — das rechnet in UTC
// und liefert nachts (vor 02:00 deutscher Zeit) noch den Vortag.
const todayLocalIso = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

const neuerIdempotenzSchluessel = () =>
  (typeof crypto !== "undefined" && crypto.randomUUID)
    ? crypto.randomUUID()
    : `k${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;

// Pruefbericht 20.09.2026 (U-83): Der Server meldet, dass unter diesem
// Idempotenz-Schluessel schon ein ANDERER Vertrag liegt (z. B. Entwurf aus
// dem sessionStorage nach geaendertem Preis). Vorher: 409 "bitte neu laden"
// als Fehler — und der Entwurf trug den alten Schluessel weiter, der naechste
// Versuch scheiterte identisch. Jetzt die Rueckfrage mit dem Preis des
// vorhandenen Vertrags; das Formular bleibt stehen.
export function idempotenzKonfliktFrage(detail) {
  const preis = Number(detail?.purchase_price);
  const preisSatz = Number.isFinite(preis) && preis > 0
    ? ` (Kaufpreis ${preisText(preis)})` : "";
  return `Aus diesem Vorgang wurde schon ein Kaufvertrag angelegt${preisSatz}.\n\n`
    + "OK = jetzt trotzdem einen NEUEN Kaufvertrag mit deinen Eingaben anlegen.\n"
    + "Abbrechen = nichts anlegen — den vorhandenen Vertrag kannst du dann über den Hinweis öffnen.";
}

// Rollenprüfung 22.09.2026 (RP-412): Am Handy verwirft der Browser eine Seite
// im Hintergrund ohne Nachfrage (beforeunload kommt dort nicht zuverlässig) —
// ein halb ausgefüllter Vertrag war weg. Der Entwurf liegt jetzt im
// sessionStorage DIESES Tabs (nicht dauerhaft auf dem Gerät: Verkäuferdaten),
// je Konto und Fahrzeug, höchstens einen Tag alt.
const ENTWURF_PRAEFIX = "ah_vertragsentwurf:";
const ENTWURF_MAX_MS = 24 * 60 * 60 * 1000;
export const entwurfSchluessel = (userId, vehicleId) =>
  `${ENTWURF_PRAEFIX}${userId || "?"}:${vehicleId || "?"}`;

export function entwurfLesen(schluessel, jetzt = Date.now()) {
  try {
    const roh = window.sessionStorage.getItem(schluessel);
    if (!roh) return null;
    const e = JSON.parse(roh);
    if (!e || typeof e !== "object" || !e.form || typeof e.form !== "object") return null;
    if (!(jetzt - Number(e.gespeichert || 0) < ENTWURF_MAX_MS)) return null;
    return e;
  } catch {
    return null;
  }
}

export function entwurfSpeichern(schluessel, daten, jetzt = Date.now()) {
  try {
    window.sessionStorage.setItem(schluessel, JSON.stringify({ ...daten, gespeichert: jetzt }));
  } catch { /* Speicher voll/gesperrt: dann eben ohne Entwurf */ }
}

export function entwurfLoeschen(schluessel) {
  try { window.sessionStorage.removeItem(schluessel); } catch { /* egal */ }
}

// Rollenprüfung 22.09.2026 (RP-402): Der Kaufpreis war ein Zahlenfeld mit
// Number() — aus "15.000" (übliche deutsche Schreibweise) wurden 15 € im
// Kaufvertrag. Jetzt Textfeld + preisAusText (lib/preis.js): "15.000" ->
// 15000, "15.000,50" -> 15000.5, Unlesbares -> null (blockiert).
export function kaufpreisPruefen(text) {
  const roh = String(text ?? "").trim();
  if (!roh) return { betrag: null, fehler: "Bitte Kaufpreis eingeben" };
  const betrag = preisAusText(roh);
  if (betrag === null) {
    return { betrag: null, fehler: "Kaufpreis nicht lesbar — bitte z. B. 15.000 oder 15.000,50 eingeben" };
  }
  if (!(betrag > 0)) return { betrag: null, fehler: "Der Kaufpreis muss größer als 0 sein" };
  return { betrag, fehler: "" };
}

// Kilometerstand für den Vertrag (Rollenprüfung 22.09.2026): deutsche
// Schreibweise über kmAusText — "85.120" / "150 Tkm" / "85.120 km".
// Liefert den Text, der an den Server geht ("" = leer), oder null, wenn der
// Eintrag nicht lesbar ist.
export function kmFuerVertrag(text) {
  const km = kmAusText(text);
  if (km === null) return "";
  if (Number.isNaN(km)) return null;
  return String(km);
}

// Rollenprüfung 22.09.2026 (RP-440/RP-444): Bei privaten Kleinanzeigen-
// Anbietern ist der angezeigte Name ein frei gewähltes Pseudonym ("vnightx")
// — der Parser legt ihn nur noch in seller_alias ab, das Pflichtfeld
// "Name / Firma" bleibt leer. Hier als Hinweis unter dem Feld, NIE als Wert.
// Bei AutoScout24-Händlern ist seller_name jetzt die Firma, die Person steht
// in seller_ansprechpartner (nur zur Info).
export function verkaeuferNameHinweise(vehicle, eingabe = "") {
  const v = vehicle || {};
  const hinweise = [];
  const alias = String(v.seller_alias || "").trim();
  const name = String(v.seller_name || "").trim();
  const getippt = String(eingabe || "").trim();
  // Nur solange das Feld leer ist oder das Pseudonym selbst eingetragen wurde.
  if (!name && alias && (!getippt || getippt.toLowerCase() === alias.toLowerCase())) {
    hinweise.push(`Kleinanzeigen-Name: ${alias} — ein frei gewähltes Pseudonym, bitte den `
      + "echten Namen des Verkäufers eintragen.");
  }
  const ansprechpartner = String(v.seller_ansprechpartner || "").trim();
  if (ansprechpartner && ansprechpartner.toLowerCase() !== name.toLowerCase()) {
    hinweise.push(`Ansprechpartner laut Inserat: ${ansprechpartner}`);
  }
  return hinweise;
}

// Wunsch Ahmad 26.09.2026 abends: Der Server meldet eine schon vergebene
// Vertragsnummer als 409 mit Klartext ("Vertragsnummer bereits vergeben: …").
// Der Text steht dann direkt unter dem Feld — nicht nur als Toast.
export function nummernFehlerAusAntwort(err) {
  const d = err?.response?.data?.detail;
  if (err?.response?.status === 409 && typeof d === "string" && d.startsWith("Vertragsnummer")) {
    return d;
  }
  return "";
}

export function anfangsFormular(v, dealer, heute) {
  return {
    // Wunsch Ahmad 26.09.2026 abends: Vertrags- und Kundennummer selbst
    // vergeben. Leer = automatisch (KV-<Datum>-…) bzw. die Firmen-Kundennummer.
    contract_no: "",
    kundennummer: dealer?.vertrags_kundennummer || "",
    seller_name: v.seller_name || "",
    seller_address: v.seller_address || "",
    seller_zip: v.seller_zip || "",
    seller_city: v.seller_city || "",
    seller_phone: v.seller_phone || "",
    seller_email: v.seller_email || "",
    purchase_price: "",
    payment_method: "",
    pickup_date: "",
    pickup_time: "",
    // 20.09.2026: die WIRKSAME Fassung — Standardsatz (falls eingeschaltet)
    // plus eigener Text. Nicht das Freitextfeld allein, sonst fehlte der
    // Standardsatz. Platzhalter bleiben stehen; sie werden erst beim
    // Erzeugen des PDF gefuellt, mit dem dann gueltigen Abholdatum.
    additional_terms: dealer?.sondervereinbarungen_effektiv
      ?? (dealer?.default_special_agreements || ""),
    agb_text: dealer?.default_terms || "",
    // Wunsch Ahmad 18.09.2026: Der Text, der wirklich im Vertrag landet
    // ("Allgemeine Vertragsbedingungen"), steht jetzt sichtbar im Dialog.
    // Leer gespeichert heisst Standardtext — den zeigen wir genauso an.
    digital_vertragstext: (dealer?.digital_vertragstext || "").trim()
      || dealer?.digital_vertragstext_standard || "",
    notes: "",
    id_document: "",
    tires: "",
    hu_valid: "",
    hu_until: "",
    service_book: "",
    service_book_until: "",
    accident_free: "",
    accident_location: "",
    eu_import: "",
    drivable: "",
    commercial_since_ez: "",
    previous_owners: cleanIntStr(v.previous_owners ?? ""),
    vehicle_description: v.description || "",
    damages: [],
    damages_text: "",
    show_vat: false,

    // Runde 22 (11.09.2026): Zulassungsstatus + Empfangsbestätigung wie auf
    // der Papiervorlage ("Käufer bestätigt Empfang von … / Verkäufer
    // bestätigt Empfang von …", jeweils Datum und Ort). Nicht angehakte
    // Kästchen erscheinen im PDF leer zum Ankreuzen von Hand.
    zulassung: "",
    empfang_zulassungsbescheinigung: false,
    empfang_schluessel: false,
    schluessel_anzahl: "",
    empfang_kaufpreis: false,
    empfang_datum: heute,
    empfang_ort_kaeufer: dealer?.city || "",
    empfang_ort_verkaeufer: v.seller_city || "",

    // Fahrzeugdaten — vom Inserat vorbefüllt, vor Vertrags-Erstellung
    // editierbar (z.B. wenn Verkäufer abweichende Angaben macht).
    vehicle_make: v.make_label || v.make || "",
    vehicle_model: v.model_label || v.model_description || v.model || "",
    vehicle_category: v.category_label || v.category || "",
    vehicle_first_registration: v.first_registration || v.ezl || "",
    vehicle_mileage: v.mileage || v.km || "",
    vehicle_fuel: v.fuel_label || v.fuel_type || v.fuel || "",
    vehicle_gearbox: v.gearbox_label || v.transmission || v.gearbox || "",
    vehicle_power_kw: v.power_kw || "",
    vehicle_power_ps: v.power_ps || (v.power_kw ? Math.round(v.power_kw * 1.36) : ""),
    vehicle_displacement: v.displacement || v.cubic_capacity || "",
    vehicle_color: v.exterior_color || v.color || "",
    vehicle_doors: v.door_count || v.doors || "",
    vehicle_seats: v.seat_count || v.seats || "",
    vehicle_vin: v.vin || v.fin || "",
    // Wunsch Ahmad (15.09.2026): kein Kennzeichen mehr im Vertrag (Feld entfernt).
    vehicle_damage_note: v.damage_unrepaired ? "Motorschaden / Unfallschaden vorhanden"
                       : (v.accident_damaged ? "Unfallschaden" : ""),

    // Händler-Profil — pre-filled, kann pro Vertrag überschrieben werden
    // (z.B. abweichende Telefonnummer im Vertretungsfall).
    ...kaeuferAusProfil(dealer),
  };
}

export default function ContractDialog({ open, onClose, vehicle, vehicleId, onCreated }) {
  const { dealer, refresh, user } = useAuth();
  const v = vehicle || {};
  // Market Intelligence (25.09.2026): optionaler Hinweis, blockiert nichts
  const markt = useMarktHinweis(vehicleId, open);
  // Runde 22 (11.09.2026, Nachprüfung): Vorgabe fürs Empfangsdatum einmal
  // beim Öffnen festhalten — set() vergleicht damit (siehe unten).
  const [heute] = useState(todayLocalIso);
  const [form, setForm] = useState(() => anfangsFormular(v, dealer, heute));
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  // Wunsch Ahmad 26.09.2026 abends: 409 "Vertragsnummer bereits vergeben"
  // steht unter dem Feld, bis die Nummer geaendert wird.
  const [nummernFehler, setNummernFehler] = useState("");
  // Runde 31: rund 60 Felder ohne Zwischenspeicher — solange der Dialog offen
  // ist, fragt der Browser vor dem Neuladen oder Schliessen nach.
  useUngespeichert(Boolean(open));
  // Pruefbericht 20.09.2026 (U-78): X und "Abbrechen" schlossen ohne
  // Rueckfrage — rund 60 Felder waren mit einem Klick weg. Gefragt wird nur,
  // wenn der Nutzer selbst etwas eingegeben hat (das automatische Nachfuellen
  // aus den Einstellungen zaehlt nicht).
  const bearbeitet = useRef(false);
  useEffect(() => { if (open) bearbeitet.current = false; }, [open]);
  // Wunsch Ahmad 18.09.2026 / Rollenprüfung 22.09.2026 (RP-490): Felder, die
  // der Nutzer selbst angefasst hat — nur die bleiben beim Nachladen der
  // Einstellungen stehen.
  const beruehrt = useRef({});
  const entwurfKey = entwurfSchluessel(user?.id, vehicleId);
  const schliessen = () => {
    if (bearbeitet.current
        && !window.confirm("Eingaben im Kaufvertrag verwerfen? Sie sind noch nicht gespeichert.")) {
      return;
    }
    // RP-412: bewusst geschlossen = Entwurf weg.
    entwurfLoeschen(entwurfKey);
    onClose?.();
  };
  // Pruefbericht 20.09.2026 (M-07): role=dialog, Fokus, Escape — Escape geht
  // ueber schliessen(), also nur nach Rueckfrage bei eigenen Eingaben.
  const dialogRef = useModal(schliessen, { offen: Boolean(open) });
  // Runde 24 (11.09.2026): Käuferdaten sind Pflicht (Wunsch Ahmad). Der
  // Hinweis sagt, was die EINSTELLUNGEN offen lassen — daher aus dem Profil
  // abgeleitet, nicht aus dem Formular: er bleibt stehen, während der
  // Sucher tippt.
  const fehltInEinstellungen = fehlendeKaeuferfelder(kaeuferAusProfil(dealer));
  // RP-440/RP-444: Pseudonym/Ansprechpartner als Hinweis unter "Name / Firma".
  const namensHinweise = verkaeuferNameHinweise(v, form.seller_name);
  const kaeuferRef = useRef(null);
  // Pruefung 14.09.2026: ein Idempotenz-Schluessel je geoeffnetem Dialog.
  const idempotenz = useRef(neuerIdempotenzSchluessel());
  useEffect(() => { if (open) idempotenz.current = neuerIdempotenzSchluessel(); }, [open]);

  // Rollenprüfung 22.09.2026 (RP-412): Entwurf beim Öffnen wiederherstellen
  // (nach den beiden Effekten oben — sonst setzten sie ihn gleich zurück).
  // Der Idempotenz-Schlüssel kommt mit: ging die Antwort auf "PDF erstellen"
  // verloren, bekommt die Wiederholung denselben Vertrag statt eines zweiten.
  const entwurfGeprueft = useRef(null);
  useEffect(() => {
    if (!open) { entwurfGeprueft.current = null; return; }
    if (entwurfGeprueft.current === entwurfKey) return;
    entwurfGeprueft.current = entwurfKey;
    const e = entwurfLesen(entwurfKey);
    if (!e) return;
    setForm((f) => ({ ...f, ...e.form }));
    beruehrt.current = { ...(e.beruehrt || {}) };
    bearbeitet.current = true;
    if (e.idempotenz) idempotenz.current = e.idempotenz;
    toast.info("Dein angefangener Kaufvertrag wurde wiederhergestellt.", {
      duration: 12000,
      action: {
        label: "Verwerfen",
        onClick: () => {
          entwurfLoeschen(entwurfKey);
          beruehrt.current = {};
          bearbeitet.current = false;
          idempotenz.current = neuerIdempotenzSchluessel();
          setForm(anfangsFormular(v, dealer, heute));
        },
      },
    });
  }, [open, entwurfKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // RP-412: Entwurf mitschreiben — entprellt bei jeder Änderung und sofort,
  // wenn die Seite in den Hintergrund geht (dort verwirft das Handy sie).
  const formRef = useRef(form);
  formRef.current = form;
  useEffect(() => {
    if (!open) return undefined;
    const sichern = () => {
      if (!bearbeitet.current) return;
      entwurfSpeichern(entwurfKey, { form: formRef.current, beruehrt: beruehrt.current,
                                     idempotenz: idempotenz.current });
    };
    const timer = window.setTimeout(sichern, 800);
    const versteckt = () => { if (document.visibilityState === "hidden") sichern(); };
    document.addEventListener("visibilitychange", versteckt);
    window.addEventListener("pagehide", sichern);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", versteckt);
      window.removeEventListener("pagehide", sichern);
    };
  }, [open, form, entwurfKey]);

  // Stufe 3 KI (Wunsch Ahmad 25.09.2026): eindeutige Angaben aus dem Inserat
  // (Schlüssel, HU, Scheckheft nur bei "lückenlos"/"kein", Unfallfrei …)
  // füllen NUR leere, nicht angefasste Felder — sichtbar mit Fundstelle.
  // Dazu die KI-Schadenbewertung, die der Sucher vor dem Erstellen sah
  // (Steuerfeld ki_bewertung_id für den Lernfall).
  const [inseratVorschlaege, setInseratVorschlaege] = useState(null);
  const kiBewertungRef = useRef(null);
  const schaedenRef = useRef(null);
  useEffect(() => {
    if (!open || !vehicleId) { setInseratVorschlaege(null); kiBewertungRef.current = null; return undefined; }
    let aktiv = true;
    api.get(`/contracts/vorschlaege/${vehicleId}`)
      .then((r) => {
        if (!aktiv || !r?.data) return;
        const erg = vorschlaegeAnwenden(formRef.current, r.data, beruehrt.current);
        setInseratVorschlaege({ uebernommen: erg.uebernommen, hinweise: erg.hinweise });
        if (erg.uebernommen.length) {
          setForm((f) => vorschlaegeAnwenden(f, r.data, beruehrt.current).form);
        }
      })
      .catch(() => {});
    return () => { aktiv = false; };
  }, [open, vehicleId]);

  // Runde 24 (11.09.2026, Gegenprüfung): useAuth().dealer wird nur beim
  // App-Start/Login geladen. Speichert der Sucher seine Käuferdaten über den
  // Link im Hinweis in einem ANDEREN Tab (oder ergänzt der Chef die
  // Firmenadresse), wäre das Profil hier veraltet: der Hinweis stünde
  // wieder da und die Pflicht blockierte "PDF erstellen", obwohl die Daten
  // gespeichert sind. Deshalb beim Öffnen frisch laden. refresh() behält bei
  // Netzfehlern den geladenen Stand und hängt die Seite nicht aus.
  // Rollenprüfung 22.09.2026 (RP-490): nicht mehr nur LEERE Felder füllen —
  // alle Käuferfelder, die der Nutzer nicht selbst angefasst hat, bekommen
  // den frischen Stand (vorher ging eine veraltete Firmenanschrift in den
  // Vertrag). Getipptes bleibt.
  useEffect(() => {
    if (!open || !refresh) return undefined;
    let aktiv = true;
    Promise.resolve(refresh())
      .then((data) => {
        if (aktiv && data?.dealer) {
          setForm((f) => kaeuferAktualisieren(f, data.dealer, beruehrt.current));
        }
      })
      .catch(() => {});
    return () => { aktiv = false; };
  }, [open, refresh]);

  // Wunsch Ahmad 18.09.2026: Kommen die Einstellungen erst nach dem Oeffnen
  // (frisch geladene Seite), werden die Textfelder nachgetragen.
  // Rollenprüfung 22.09.2026 (RP-490): Hat der Chef die Vorlagen/Texte
  // geändert, während der Dialog offen war, bekommen alle UNBERÜHRTEN
  // Textfelder den neuen Stand (vorher nur leere).
  useEffect(() => {
    if (!dealer) return;
    const vorgaben = {
      digital_vertragstext: (dealer.digital_vertragstext || "").trim()
        || dealer.digital_vertragstext_standard || "",
      additional_terms: dealer.sondervereinbarungen_effektiv
        ?? (dealer.default_special_agreements || ""),
      agb_text: dealer.default_terms || "",
      // 26.09.2026: die Firmen-Kundennummer folgt dem frischen Stand, solange
      // der Sucher das Feld nicht selbst angefasst hat.
      kundennummer: dealer.vertrags_kundennummer || "",
    };
    setForm((f) => {
      const kaeufer = kaeuferAktualisieren(f, dealer, beruehrt.current);
      const neu = { ...kaeufer };
      let geaendert = kaeufer !== f;
      for (const [feld, wert] of Object.entries(vorgaben)) {
        if (wert && !beruehrt.current[feld] && (f[feld] || "") !== wert) {
          neu[feld] = wert;
          geaendert = true;
        }
      }
      return geaendert ? neu : f;
    });
  }, [dealer]);

  // RP-218: das Feld "Zusätzlicher AGB-Abschnitt" verschwand, sobald man es
  // leerte — obwohl der Server den Text aus den Einstellungen wieder einsetzt.
  // Einmal gezeigt, bleibt es sichtbar (mit Hinweis "leer = Einstellungen").
  const agbGezeigt = useRef(false);
  if ((form.agb_text || "").trim()) agbGezeigt.current = true;

  if (!open) return null;

  // Runde 22 (11.09.2026): funktional updaten — sonst überschreiben sich
  // schnell aufeinanderfolgende Änderungen (Checkbox + abhängiges Feld)
  // mit einem veralteten form-Stand.
  //
  // Runde 22 (11.09.2026, Nachprüfung): Die Vorgaben der Empfangsbestätigung
  // folgen ihrer Quelle, solange der Sucher sie nicht von Hand abweichend
  // geändert hat:
  //  - Datum folgt dem Abholdatum (= Übergabetag; der Vertrag wird meist
  //    Tage vorher angelegt). Ohne Abholdatum gilt wieder "heute".
  //  - Ort (Verkäufer) folgt "Verkäufer / Halter → Ort", Ort (Käufer)
  //    folgt "Käufer → Ort" (z.B. Ort im Inserat fehlte und wird nachgetragen).

  const set = (k, v) => {
    // 26.09.2026: eine andere Vertragsnummer loescht den 409-Hinweis.
    if (k === "contract_no") setNummernFehler("");
    setForm((f) => {
    bearbeitet.current = true;
    beruehrt.current[k] = true;
    const next = { ...f, [k]: v };
    if (k === "pickup_date" && f.empfang_datum === (f.pickup_date || heute)) {
      next.empfang_datum = v || heute;
    }
    if (k === "seller_city" && f.empfang_ort_verkaeufer === f.seller_city) {
      next.empfang_ort_verkaeufer = v;
    }
    if (k === "dealer_city" && f.empfang_ort_kaeufer === f.dealer_city) {
      next.empfang_ort_kaeufer = v;
    }
    // Rollenprüfung 22.09.2026 (RP-405): Das Datumsfeld wurde bei "HU: Nein"
    // nur gesperrt, nicht geleert — im Vertrag stand "Nein, gültig bis
    // 05/2027" (und die Monat/Jahr-Prüfung lief auf das gesperrte Feld).
    // Dasselbe beim Scheckheft ("bis" nur bei "teilweise").
    if (k === "hu_valid" && v !== "Ja") next.hu_until = "";
    if (k === "service_book" && v !== "teilweise") next.service_book_until = "";
    return next;
    });
  };

  // Eintippen einer Schlüsselanzahl hakt "KFZ mit __ Schlüssel(n)" gleich an.
  // Nachprüfung: führende Nullen fallen weg — "0" ist keine Anzahl und darf
  // nicht "KFZ mit 0 Schlüssel(n)" angekreuzt ins PDF bringen.
  const setSchluesselAnzahl = (raw) => {
    const n = cleanIntStr(raw).replace(/^0+/, "");
    bearbeitet.current = true;
    setForm((f) => ({
      ...f,
      schluessel_anzahl: n,
      empfang_schluessel: n ? true : f.empfang_schluessel,
    }));
  };

  // Rollenprüfung 22.09.2026 (RP-402): Kaufpreis und Kilometerstand in
  // deutscher Schreibweise lesen (lib/preis.js) — nie Number() auf
  // getippten Text ("15.000" wurde zu 15 €).
  const preis = kaufpreisPruefen(form.purchase_price);
  const kmText = kmFuerVertrag(form.vehicle_mileage);
  const buildPayload = () => ({
    vehicle_id: vehicleId,
    ...form,
    purchase_price: preis.betrag ?? 0,
    // Stufe 3 KI: welche Schadenbewertung vorher zu sehen war (nur Lernfall)
    ki_bewertung_id: kiBewertungRef.current || undefined,
    // Unlesbarer km-Text geht nur in die Vorschau unverändert; "PDF
    // erstellen" blockiert vorher (siehe submit).
    vehicle_mileage: kmText ?? form.vehicle_mileage,
  });

  const openPreview = async () => {
    if (preis.fehler) {
      toast.error(`${preis.fehler} (auch für die Vorschau erforderlich)`);
      return;
    }
    setPreviewing(true);
    const startMs = Date.now();
    try {
      const res = await api.post("/contracts/preview", buildPayload(), {
        responseType: "blob",
      });
      // Pruefbericht 20.09.2026 (U-76): window.open nach dem await verwarf
      // ein Popup-Blocker still. blobOeffnen oeffnet direkt, solange der
      // Klick frisch ist — sonst ein Hinweis mit "Öffnen"-Knopf.
      blobOeffnen(new Blob([res.data], { type: "application/pdf" }),
                  { startMs, titel: "Die Vorschau", mime: "application/pdf" });
    } catch (err) {
      toast.error(errMsg(err, "Vorschau konnte nicht erzeugt werden"));
    } finally {
      setPreviewing(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    // Runde 24 (11.09.2026): Käuferdaten Pflicht beim Erstellen (die Vorschau
    // geht weiterhin ohne). Leere Felder hält schon required auf; hier
    // zusätzlich nur-Leerzeichen — mit Feldnamen und Sprung zum Abschnitt.
    const fehlend = fehlendeKaeuferfelder(form);
    if (fehlend.length > 0) {
      toast.error(`Bitte Käuferdaten ergänzen: ${fehlend.map((f) => f.label).join(", ")}`);
      const abschnitt = kaeuferRef.current;
      abschnitt?.scrollIntoView({ behavior: "smooth", block: "start" });
      abschnitt
        ?.querySelector(`[data-testid="contract-${fehlend[0].key.replace("_", "-")}"]`)
        ?.focus({ preventScroll: true });
      return;
    }
    if (preis.fehler) {
      toast.error(preis.fehler);
      return;
    }
    // RP-402: kleine Beträge sind fast immer ein Tippfehler ("15.000" als 15
    // gelesen, Komma vergessen) — einmal nachfragen.
    if (preis.betrag < 100
        && !window.confirm(`Kaufpreis ${preisText(preis.betrag)} — ist das richtig?`)) {
      return;
    }
    if (kmText === null) {
      toast.error("Kilometerstand nicht lesbar — bitte z. B. 85.120 oder 150 Tkm eingeben");
      return;
    }
    // Runde 22 (11.09.2026): Zahlungsart ist Pflicht beim Erstellen
    // (die Vorschau geht weiterhin ohne).
    if (!form.payment_method) {
      toast.error("Bitte Zahlungsart wählen");
      return;
    }
    // Gegenpruefung 12.09.2026: ein halb getipptes "05/2" landete sonst im Vertrag.
    for (const [feld, label] of [["vehicle_first_registration", "Erstzulassung"], ["hu_until", "HU gültig bis"],
                                 ["service_book_until", "Scheckheft gepflegt bis"]]) {
      const fehler = monatJahrFehler(form[feld]);
      if (fehler) {
        toast.error(`${label}: ${fehler}`);
        return;
      }
    }
    setLoading(true);
    try {
      // Pruefung 14.09.2026: Idempotenz — Doppelklick oder Wiederholung nach
      // Netzabbruch legt keinen zweiten Vertrag an (Schluessel je Dialog).
      const senden = (extra = {}) => api.post("/contracts",
        { ...buildPayload(), idempotency_key: idempotenz.current, ...extra });
      const anlegen = async () => {
        try {
          return (await senden()).data;
        } catch (err) {
          // Rollenprüfung 22.09.2026 (RP-416): Derselbe Sucher hat für dieses
          // Fahrzeug schon einen offenen Vertrag — erst nachfragen, dann
          // bewusst einen zweiten anlegen (z. B. nachverhandelter Preis).
          const d = err?.response?.data?.detail;
          if (err?.response?.status === 409 && d?.code === "vertrag_vorhanden"
              && window.confirm(`${d.msg}\n\nTrotzdem einen zweiten Kaufvertrag anlegen?`)) {
            return (await senden({ zweiter_vertrag_bestaetigt: true })).data;
          }
          throw err;
        }
      };
      let data;
      try {
        data = await anlegen();
      } catch (err) {
        const d = err?.response?.data?.detail;
        if (!(err?.response?.status === 409 && d?.code === "idempotenz_konflikt")) throw err;
        // U-83: neuer Schluessel — auch im Entwurf, sonst kaeme derselbe
        // Konflikt nach einem Neuladen wieder.
        idempotenz.current = neuerIdempotenzSchluessel();
        if (bearbeitet.current) {
          entwurfSpeichern(entwurfKey, { form: formRef.current, beruehrt: beruehrt.current,
                                         idempotenz: idempotenz.current });
        }
        if (!window.confirm(idempotenzKonfliktFrage(d))) {
          const preis = Number(d?.purchase_price);
          toast.info(`Vorhandener Kaufvertrag${Number.isFinite(preis) && preis > 0
            ? ` (Kaufpreis ${preisText(preis)})` : ""} — deine Eingaben bleiben stehen.`, {
            duration: 15000,
            action: d?.contract_id ? {
              label: "Öffnen",
              onClick: () => openContractPdf(d.contract_id)
                .catch((e) => toast.error(errMsg(e, "Vertrag konnte nicht geöffnet werden"))),
            } : undefined,
          });
          return;
        }
        data = await anlegen();
      }
      // RP-412: gespeichert — der Entwurf wird nicht mehr gebraucht.
      entwurfLoeschen(entwurfKey);
      bearbeitet.current = false;
      // Runde 15: der Vertrag ist gespeichert, auch wenn der automatische
      // Abholtermin nicht angelegt werden konnte — der Server sagt es.
      if (data?.termin_hinweis) toast.warning(data.termin_hinweis, { duration: 8000 });
      // Pruefbericht 20.09.2026 (U-82): Fahrzeugstatus/Protokoll konnten nicht
      // nachgezogen werden (der Aufraeumjob holt es nach) — und "bereits
      // vorhanden" heisst: dieselbe Anfrage war schon angekommen, es gibt
      // keinen zweiten Vertrag. Beides wurde bisher verworfen.
      if (data?.nacharbeit_hinweis) toast.warning(data.nacharbeit_hinweis, { duration: 10000 });
      if (data?.bereits_vorhanden) {
        toast.info("Dieser Kaufvertrag war schon angelegt — es wurde kein zweiter erstellt.");
      }
      onCreated?.(data);
    } catch (err) {
      // 26.09.2026: vergebene Vertragsnummer auch unter dem Feld zeigen.
      setNummernFehler(nummernFehlerAusAntwort(err));
      toast.error(errMsg(err, "PDF konnte nicht erstellt werden"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-black/70 backdrop-blur-sm">
      {/* M-07: Rahmen ist der Dialog (role/aria-modal/Fokus, lib/useModal).
          Handy-Ansicht (24.09.2026): Hoehe nach dvh (iOS-Adressleiste), am
          Telefon fast randlos. */}
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="contract-dialog-titel"
           className="bg-[var(--bg-surface)] border w-full max-w-4xl modal-hoehe overflow-y-auto rounded-2xl"
           style={{ borderColor: "var(--border-default)" }} data-testid="contract-dialog">
        <div className="flex items-center justify-between px-6 py-3 border-b sticky top-0 bg-[var(--bg-surface)] z-10"
             style={{ borderColor: "var(--border-default)" }}>
          <div>
            <div className="overline">Kaufvertrag</div>
            <div className="font-display font-bold text-lg" id="contract-dialog-titel">
              {vehicle?.make_label} {vehicle?.model_label}
            </div>
          </div>
          {/* M-11: 44-px-Trefferflaeche statt des nackten 20-px-Symbols */}
          <button type="button" onClick={schliessen} data-testid="close-contract"
                  aria-label="Kaufvertrag schließen"
                  className="w-11 h-11 -mr-2 flex items-center justify-center rounded-full text-zinc-400 hover:text-white hover:bg-white/10">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={submit} className="p-4 sm:p-6 space-y-6 vertrag-formular">
          {/* Wunsch Ahmad 26.09.2026 abends: Vertragsnummer und Kundennummer
              selbst vergeben. Leer = automatisch (KV-<Datum>-…) bzw. die
              Firmen-Kundennummer aus den Einstellungen. Eine schon vergebene
              Vertragsnummer meldet der Server (409) — Text unter dem Feld. */}
          <Section title="Nummern" subtitle="Beide Felder dürfen leer bleiben — dann vergibt die App die Vertragsnummer selbst und nimmt die Kundennummer aus den Einstellungen.">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Vertragsnummer (leer = automatisch KV-…)" value={form.contract_no}
                     onChange={(v) => set("contract_no", v)} testid="contract-vertragsnummer"
                     maxLength={40} placeholder="z. B. AH-2026-0042"
                     helper="3–40 Zeichen: Buchstaben, Ziffern, Leerzeichen und - _ / . — je Firma nur einmal vergebbar." />
              <Field label="Kundennummer (Vorbelegung: Firmenwert)" value={form.kundennummer}
                     onChange={(v) => set("kundennummer", v)} testid="contract-kundennummer"
                     maxLength={30} placeholder="z. B. 482913"
                     helper="Steht im Vertrag („nur unter Vorlage der Kundennummer“), in den Vorlagen als {kundennummer} und im Abholauftrag. Darf in mehreren Verträgen gleich sein." />
            </div>
            {nummernFehler && (
              <div role="alert" data-testid="contract-nummern-fehler"
                   className="text-[12px] leading-snug" style={{ color: "var(--accent-red)" }}>
                {nummernFehler}
              </div>
            )}
          </Section>

          {/* Verkäufer + Käufer side-by-side on lg, stacked on small */}
          <div className="grid lg:grid-cols-2 gap-5">
            <Section title="Verkäufer / Halter">
              <Field label="Name / Firma *" required value={form.seller_name} onChange={(v) => set("seller_name", v)} testid="contract-seller-name"
                     helper={namensHinweise.length > 0 && (
                       <span data-testid="contract-seller-name-hinweis">
                         {namensHinweise.map((h) => <span key={h} className="block">{h}</span>)}
                       </span>
                     )} />
              {/* M-13: am Handy einspaltig; M-12: Telefon-/Zifferntastatur */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="Telefon" type="tel" autoComplete="tel" value={form.seller_phone} onChange={(v) => set("seller_phone", v)} testid="contract-seller-phone" />
                <Field label="E-Mail" type="email" value={form.seller_email} onChange={(v) => set("seller_email", v)} testid="contract-seller-email" />
              </div>
              <Field label="Adresse" value={form.seller_address} onChange={(v) => set("seller_address", v)} testid="contract-seller-address" />
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <Field label="PLZ" inputMode="numeric" value={form.seller_zip} onChange={(v) => set("seller_zip", v)} testid="contract-seller-zip" />
                <Field label="Ort" value={form.seller_city} onChange={(v) => set("seller_city", v)} testid="contract-seller-city" />
                <Field label="Ausweis-Nr." value={form.id_document} onChange={(v) => set("id_document", v)} testid="contract-id-doc" />
              </div>
            </Section>

            {/* Runde 24 (11.09.2026): Firma/Adresse/PLZ/Ort sind Pflicht —
                Käufer im Kaufvertrag, Auftraggeber im Abholprotokoll. */}
            <div ref={kaeuferRef} style={{ scrollMarginTop: "5rem" }} data-testid="contract-kaeufer">
            <Section title="Käufer (Händler — du)">
              {fehltInEinstellungen.length > 0 && (
                <div role="alert" data-testid="contract-kaeufer-fehlt"
                     className="flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm leading-snug"
                     style={{
                       borderColor: "var(--accent-red)",
                       background: "color-mix(in srgb, var(--accent-red) 12%, transparent)",
                       color: "var(--text-primary)",
                     }}>
                  <AlertTriangle size={16} className="shrink-0 mt-0.5" style={{ color: "var(--accent-red)" }} />
                  <div>
                    <div className="font-semibold">Käuferdaten fehlen in deinen Einstellungen</div>
                    <div className="text-[12px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
                      Fehlt: {fehltInEinstellungen.map((f) => f.label).join(", ")}. Bitte hier eintragen —
                      ohne diese Angaben wird kein Kaufvertrag erstellt.
                    </div>
                    <a href="/app/einstellungen" target="_blank" rel="noopener noreferrer"
                       data-testid="contract-kaeufer-einstellungen"
                       className="inline-flex items-center gap-1 mt-1.5 text-[12px] font-semibold underline"
                       style={{ color: "var(--accent-blue)" }}>
                      Dauerhaft in den Einstellungen speichern <ExternalLink size={12} />
                    </a>
                    <span className="text-[11px] ml-1" style={{ color: "var(--text-secondary)" }}>
                      (neuer Tab — deine Eingaben hier bleiben erhalten)
                    </span>
                  </div>
                </div>
              )}
              <Field label="Firma *" required value={form.dealer_company} onChange={(v) => set("dealer_company", v)} testid="contract-dealer-company" />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="Ansprechpartner" value={form.dealer_contact} onChange={(v) => set("dealer_contact", v)} testid="contract-dealer-contact" />
                <Field label="Telefon" type="tel" autoComplete="tel" value={form.dealer_phone} onChange={(v) => set("dealer_phone", v)} testid="contract-dealer-phone" />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="WhatsApp" type="tel" autoComplete="tel" value={form.dealer_whatsapp} onChange={(v) => set("dealer_whatsapp", v)} testid="contract-dealer-wa" />
                <Field label="E-Mail" type="email" value={form.dealer_email} onChange={(v) => set("dealer_email", v)} testid="contract-dealer-email" />
              </div>
              <Field label="Adresse *" required value={form.dealer_address} onChange={(v) => set("dealer_address", v)} testid="contract-dealer-address" />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Field label="PLZ *" required inputMode="numeric" value={form.dealer_zip} onChange={(v) => set("dealer_zip", v)} testid="contract-dealer-zip" />
                <Field label="Ort *" required value={form.dealer_city} onChange={(v) => set("dealer_city", v)} testid="contract-dealer-city" />
              </div>
              <div className="text-[11px] text-zinc-500 leading-relaxed">
                Erscheint als Käufer im Kaufvertrag und als Auftraggeber im Abholprotokoll.
                Aus deinen Einstellungen vorbefüllt — fehlt etwas, hier eintragen (dauerhaft unter Einstellungen).
              </div>
            </Section>
            </div>
          </div>

          {/* Fahrzeugdaten — direkt aus dem Inserat übernommen, vor
              Vertrags-Erstellung anpassbar. */}
          {/* Rollenprüfung 22.09.2026 (RP-404): ein geleertes Feld kommt nicht
              mehr still aus dem Inserat zurück. */}
          <Section title="Fahrzeugdaten" subtitle="Aus dem Inserat übernommen — bei Bedarf korrigieren. Ein geleertes Feld bleibt im Vertrag leer.">

            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="Marke" value={form.vehicle_make} onChange={(v) => set("vehicle_make", v)} testid="contract-veh-make" />
              <Field label="Modell" value={form.vehicle_model} onChange={(v) => set("vehicle_model", v)} testid="contract-veh-model" />
              <Field label="Kategorie" value={form.vehicle_category} onChange={(v) => set("vehicle_category", v)} testid="contract-veh-cat" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-zinc-400">Erstzulassung (MM/JJJJ)</label>
                <MonatJahrEingabe value={form.vehicle_first_registration}
                                  onChange={(v) => set("vehicle_first_registration", v)}
                                  art="ez" testid="contract-veh-ez" className="input-base w-full mt-1" />
              </div>
              {/* Rollenprüfung 22.09.2026: "85.120" / "150 Tkm" werden richtig
                  gelesen (kmAusText); Unlesbares blockiert "PDF erstellen". */}
              <Field label="Kilometerstand" value={form.vehicle_mileage} onChange={(v) => set("vehicle_mileage", v)} testid="contract-veh-km"
                     inputMode="numeric"
                     helper={kmText === null ? "Nicht lesbar — bitte z. B. 85.120 eingeben." : undefined} />
              {/* M-12: Zifferntastatur fuer reine Zahlenfelder */}
              <Field label="Hubraum (ccm)" inputMode="numeric" value={form.vehicle_displacement} onChange={(v) => set("vehicle_displacement", v)} testid="contract-veh-ccm" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Field label="Kraftstoff" value={form.vehicle_fuel} onChange={(v) => set("vehicle_fuel", v)} testid="contract-veh-fuel" />
              <Field label="Getriebe" value={form.vehicle_gearbox} onChange={(v) => set("vehicle_gearbox", v)} testid="contract-veh-gear" />
              <Field label="Leistung (kW)" inputMode="numeric" value={form.vehicle_power_kw} onChange={(v) => set("vehicle_power_kw", v)} testid="contract-veh-kw" />
              <Field label="Leistung (PS)" inputMode="numeric" value={form.vehicle_power_ps} onChange={(v) => set("vehicle_power_ps", v)} testid="contract-veh-ps" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Field label="Farbe" value={form.vehicle_color} onChange={(v) => set("vehicle_color", v)} testid="contract-veh-color" />
              <Field label="Türen" inputMode="numeric" value={form.vehicle_doors} onChange={(v) => set("vehicle_doors", v)} testid="contract-veh-doors" />
              <Field label="Sitze" inputMode="numeric" value={form.vehicle_seats} onChange={(v) => set("vehicle_seats", v)} testid="contract-veh-seats" />
              {/* Rollenprüfung 22.09.2026 (RP-430): Die Portale liefern die
                  ANZAHL DER FAHRZEUGHALTER (der jetzige mitgezählt, "2. Hand"
                  = 2) — als "Vorhalter" war das um eins zu hoch. */}
              <Field
                label="Fahrzeughalter (Anzahl)"
                type="number"
                value={form.previous_owners}
                onChange={(v) => set("previous_owners", cleanIntStr(v))}
                testid="contract-veh-prev"
                inputMode="numeric"
                placeholder="z.B. 2"
                helper="Anzahl der Halter laut Inserat bzw. Fahrzeugbrief, der jetzige mitgezählt („2. Hand“ = 2). Wird aus dem Inserat übernommen — bitte prüfen."
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Wunsch Ahmad (15.09.2026): kein Kennzeichen im Kaufvertrag */}
              <Field label="FIN" value={form.vehicle_vin} onChange={(v) => set("vehicle_vin", v)} testid="contract-veh-fin" />
            </div>
            <Field label="Sonstige Schäden / Hinweis (erscheint im Vertrag)" value={form.vehicle_damage_note} onChange={(v) => set("vehicle_damage_note", v)} testid="contract-veh-damage" placeholder="z.B. Motorschaden, Hagelschaden" />
          </Section>

          {/* Zusicherungen & Zustand */}
          <Section title="Zusicherungen & Zustand">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <SelectField
                label="Bereifung"
                value={form.tires}
                onChange={(v) => set("tires", v)}
                options={TIRE_OPTIONS}
                testid="contract-tires"
              />
              <SelectField
                label="HU/AU vorhanden"
                value={form.hu_valid}
                onChange={(v) => set("hu_valid", v)}
                options={YN_OPTIONS}
                testid="contract-hu-valid"
              />
              <div>
                <label className="text-xs text-zinc-400">HU gültig bis (MM/JJJJ)</label>
                <MonatJahrEingabe value={form.hu_until} onChange={(v) => set("hu_until", v)}
                                  art="hu" testid="contract-hu-until" disabled={form.hu_valid !== "Ja"}
                                  className="input-base w-full mt-1 disabled:opacity-50" />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <SelectField
                label="Scheckheftgepflegt"
                value={form.service_book}
                onChange={(v) => set("service_book", v)}
                options={SCHECKHEFT_OPTIONS}
                testid="contract-service-book"
              />
              <div>
                <label className="text-xs text-zinc-400">Scheckheft gepflegt bis (MM/JJJJ)</label>
                <MonatJahrEingabe value={form.service_book_until} onChange={(v) => set("service_book_until", v)}
                                  art="ez" testid="contract-service-book-until"
                                  disabled={form.service_book !== "teilweise"}
                                  className="input-base w-full mt-1 disabled:opacity-50" />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <SelectField
                label="Unfallfrei"
                value={form.accident_free}
                onChange={(v) => set("accident_free", v)}
                options={YN_OPTIONS}
                testid="contract-accident-free"
              />
              <Field
                label="Wenn nicht unfallfrei: wo / Beschreibung"
                value={form.accident_location}
                onChange={(v) => set("accident_location", v)}
                testid="contract-accident-loc"
                placeholder="z.B. Heckschaden rechts"
                disabled={form.accident_free !== "Nein"}
              />
              <SelectField
                label="EU-Import"
                value={form.eu_import}
                onChange={(v) => set("eu_import", v)}
                options={YN_OPTIONS}
                testid="contract-eu-import"
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <SelectField
                label="Fahrtauglich"
                value={form.drivable}
                onChange={(v) => set("drivable", v)}
                options={YN_OPTIONS}
                testid="contract-drivable"
              />
              <SelectField
                label="Gewerblich genutzt seit EZ"
                value={form.commercial_since_ez}
                onChange={(v) => set("commercial_since_ez", v)}
                options={YN_OPTIONS}
                testid="contract-commercial"
              />
              <SelectField
                label="Zulassung"
                value={form.zulassung}
                onChange={(v) => set("zulassung", v)}
                options={ZULASSUNG_OPTIONS}
                testid="contract-zulassung"
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {/* Entscheidung Ahmad 26.09.2026: Schlüsselanzahl gehört in den Vertrag —
                  der Fahrer gleicht vor Ort dagegen ab (Abholprotokoll, KI). Fehlt sie,
                  nur ein Hinweis, kein Blockieren. */}
              <div>
                <Field
                  label="Schlüssel (Anzahl laut Vertrag)"
                  type="number"
                  inputMode="numeric"
                  value={form.schluessel_anzahl}
                  onChange={setSchluesselAnzahl}
                  testid="contract-schluessel-anzahl"
                  placeholder="z.B. 2"
                />
                {!String(form.schluessel_anzahl ?? "").trim() && (
                  <div className="text-[11px] mt-1 leading-snug" style={{ color: "var(--st-amber)" }}
                       data-testid="contract-schluessel-hinweis">
                    Bitte Schlüsselanzahl eintragen — sonst kann der Fahrer fehlende Schlüssel nicht abgleichen.
                  </div>
                )}
              </div>
            </div>
            {inseratVorschlaege && (inseratVorschlaege.uebernommen.length > 0 || inseratVorschlaege.hinweise.length > 0) && (
              <div className="rounded-lg px-3 py-2 text-[11px] leading-snug" data-testid="contract-inserat-vorschlaege"
                   style={{ background: "var(--wa-03)", color: "var(--text-secondary)" }}>
                {inseratVorschlaege.uebernommen.length > 0 && (
                  <div>
                    <span className="font-semibold">Aus dem Inserat übernommen (bitte prüfen):</span>{" "}
                    {inseratVorschlaege.uebernommen
                      .map((u) => `${u.label}: ${u.wert}${u.fund ? ` („${u.fund}“)` : ""}`).join(" · ")}
                  </div>
                )}
                {inseratVorschlaege.hinweise.map((h, i) => <div key={i} className="mt-0.5">{h}</div>)}
              </div>
            )}
          </Section>

          <Section title="Schäden / Beschädigungen">
            <div className="lg:flex lg:gap-4 lg:items-start">
              <div className="flex-1 min-w-0" ref={schaedenRef}>
                <DamageSelector
                  damages={form.damages}
                  onChange={(list, text) => {
                    bearbeitet.current = true;
                    setForm((f) => ({ ...f, damages: list, damages_text: text }));
                  }}
                />
              </div>
              {/* Stufe 3 KI (Wunsch Ahmad 25.09.2026): "Sind das alle Schäden?" →
                  ein Aufruf, Karte rechts neben der Skizze; rein beratend. */}
              <div className="lg:w-[330px] lg:shrink-0 mt-3 lg:mt-0">
                <KiSchadenKarte
                  vehicleId={vehicleId}
                  damages={form.damages}
                  onDamagesChange={(list) => {
                    bearbeitet.current = true;
                    setForm((f) => ({ ...f, damages: list, damages_text: damagesToText(list) }));
                  }}
                  preisBetrag={preis.fehler ? null : (preis.betrag || null)}
                  onPreis={(p) => { set("purchase_price", preisText(p)); toast.info("Preis ins Feld übernommen — bitte prüfen."); }}
                  onBewertungId={(id) => { kiBewertungRef.current = id; }}
                  onWeitere={() => schaedenRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })}
                  disabled={loading || previewing}
                />
              </div>
            </div>
          </Section>

          <Section title="Konditionen">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {/* Rollenprüfung 22.09.2026 (RP-402): Textfeld mit deutscher
                  Schreibweise; darunter steht, welcher Betrag erkannt wurde. */}
              <div>
                <Field
                  label="Kaufpreis (€) *" required inputMode="decimal"
                  value={form.purchase_price} onChange={(v) => set("purchase_price", v)}
                  testid="contract-price" placeholder="z.B. 8.900"
                />
                {String(form.purchase_price || "").trim() !== "" && (
                  <div className="text-[11px] mt-1 leading-snug" data-testid="contract-price-erkannt"
                       style={{ color: preis.fehler ? "var(--accent-red)" : "var(--text-secondary)" }}>
                    {preis.fehler ? preis.fehler : `= ${preisText(preis.betrag)}`}
                  </div>
                )}
                {markt?.median_top20_price != null && (
                  <div className="text-[11px] mt-1 leading-snug" data-testid="contract-markt-hinweis" style={{ color: "var(--text-secondary)" }}>
                    Top-20-Median aktuell {preisText(markt.median_top20_price)} ({markt.km_label}{markt.ez_label ? `, ${markt.ez_label}` : ""}) · günstigstes {preisText(markt.min_price)} · nur Orientierung
                  </div>
                )}
              </div>
              <SelectField
                label="Zahlungsart *"
                required
                value={form.payment_method}
                onChange={(v) => set("payment_method", v)}
                options={PAYMENT_OPTIONS}
                testid="contract-payment"
              />
            </div>
            <label className="flex items-start gap-2 rounded-lg border px-3 py-2.5 cursor-pointer"
                   style={{ borderColor: "var(--border-default)" }}
                   data-testid="contract-show-vat">
              <input type="checkbox" checked={!!form.show_vat}
                     onChange={(e) => set("show_vat", e.target.checked)}
                     className="mt-0.5 accent-red-500" />
              <span className="text-sm">
                <span className="font-semibold">MwSt (19 %) im Vertrag ausweisen</span>
                <span className="block text-[11px] text-zinc-500">
                  Für gewerbliche Verkäufe (Regelbesteuerung): der Kaufpreis gilt
                  als Brutto, der Vertrag zeigt Netto und Steuer.
                  {form.show_vat && preis.betrag > 0 && (
                    <> {" "}Netto {(preis.betrag / 1.19).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} € ·
                    MwSt {(preis.betrag - preis.betrag / 1.19).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €</>
                  )}
                </span>
              </span>
            </label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Field label="Abholdatum" type="date" value={form.pickup_date} onChange={(v) => set("pickup_date", v)} testid="contract-pickup-date" />
              <Field label="Abholuhrzeit (nur Terminplaner)" type="time" value={form.pickup_time} onChange={(v) => set("pickup_time", v)} testid="contract-pickup-time"
                     helper="Steht nicht im Vertrag — nur für den Termin und die Fahrer-App." />
            </div>
            {/* Rollenprüfung 22.09.2026 (RP-218): Der Hilfetext sagt jetzt, was
                beim Leeren passiert — wie bei den Vertragsbedingungen ("leer =
                Standard", Entscheidung 09.09.). Ob "leer" künftig "weglassen"
                heißen soll, entscheidet Ahmad. */}
            <Field label="Besondere Vereinbarungen" value={form.additional_terms} onChange={(v) => set("additional_terms", v)} multiline rows={4} testid="contract-terms"
                   helper="Aus deinen Einstellungen vorausgefüllt — hier nur für diesen Vertrag anpassbar. Leerst du das Feld, gilt wieder der Text aus den Einstellungen. Platzhalter in geschweiften Klammern (z. B. {abholdatum}) werden beim Erstellen des PDF automatisch eingesetzt." />
            {/* Wunsch Ahmad (15.09.2026): Sucher schreiben interne Notizen nicht beim
                Vertrag, sondern spaeter im Terminplaner am Termin.
                Wunsch Ahmad 21.09.2026: die Notiz steht nicht mehr im Vertrags-PDF
                (beide Fassungen gehen an den Verkaeufer). */}
            {user?.role !== "sucher" && (
              <Field label="Notizen (intern)" value={form.notes} onChange={(v) => set("notes", v)} multiline testid="contract-notes"
                     helper="Steht nicht im Vertrag — nur intern sichtbar (im Vertragsarchiv)." />
            )}
          </Section>

          {/* Wunsch Ahmad 24.09.2026: Die Übergabe & Empfangsbestätigung
              wird beim Erstellen nicht mehr abgefragt. Ob der Block im
              gedruckten Vertrag steht, schaltet der Chef unter Einstellungen →
              Vertragstexte (empfang_drucken); Datum und Orte kommen von selbst
              aus Abholdatum, Firmensitz und Verkäuferort, die Kästchen bleiben
              zum Ankreuzen von Hand leer. */}

          <Section title="Fahrzeugbeschreibung (vom Inserat)">
            <Field
              label="Beschreibungstext"
              value={form.vehicle_description}
              onChange={(v) => set("vehicle_description", v)}
              multiline
              rows={6}
              testid="contract-vehicle-description"
              helper="Wurde automatisch aus dem Inserat übernommen und landet im PDF. Frei editierbar — leerst du das Feld, steht keine Beschreibung im Vertrag."
            />
          </Section>

          {/* Wunsch Ahmad 18.09.2026: Die Bedingungen aus den Einstellungen
              standen bisher nur im PDF — jetzt stehen sie hier, und wer will,
              ueberarbeitet sie fuer genau diesen Vertrag. */}
          <Section title="Vertragsbedingungen & AGB">
            <Field
              label="Vertragsbedingungen & AGB (stehen in diesem Kaufvertrag)"
              value={form.digital_vertragstext}
              onChange={(v) => set("digital_vertragstext", v)}
              multiline
              rows={12}
              testid="contract-vertragsbedingungen"
              helper="Aus deinen Einstellungen geladen. Änderungen hier gelten nur für diesen einen Vertrag; leerst du das Feld, gilt wieder der Text aus den Einstellungen."
            />
            {agbGezeigt.current ? (
              <Field
                label="Zusätzlicher AGB-Abschnitt (aus älteren Einstellungen)"
                value={form.agb_text}
                onChange={(v) => set("agb_text", v)}
                multiline
                rows={6}
                testid="contract-agb-text"
                helper="Steht im PDF als eigener Abschnitt „Allgemeine Geschäftsbedingungen“ vor den Vertragsbedingungen. Leerst du das Feld, gilt wieder der AGB-Text aus den Einstellungen."
              />
            ) : null}
          </Section>

          {/* Handy-Ansicht (24.09.2026): Rand wie das Formular (4/6), Polster
              bis ueber den Home-Balken der installierten App. */}
          <div className="flex flex-wrap items-center justify-end gap-2 sm:gap-3 pt-2 sticky bottom-0 bg-[var(--bg-surface)] py-3 -mx-4 px-4 sm:-mx-6 sm:px-6 border-t"
               style={{ borderColor: "var(--border-default)",
                        paddingBottom: "calc(0.75rem + env(safe-area-inset-bottom, 0px))" }}>
            <button type="button" onClick={schliessen}
                    className="apple-btn apple-btn-secondary" data-testid="cancel-contract">
              Abbrechen
            </button>
            <button type="button" onClick={openPreview} disabled={previewing || loading}
                    className="apple-btn apple-btn-secondary disabled:opacity-60" data-testid="preview-contract-btn">
              {previewing ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
              {previewing ? "Erzeuge…" : "Vorschau"}
            </button>
            <button type="submit" disabled={loading || previewing} data-testid="submit-contract"
                    className="apple-btn apple-btn-primary disabled:opacity-60">
              {loading ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
              {loading ? "Erstelle PDF…" : "PDF erstellen"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const Section = ({ title, subtitle, children }) => (
  <div>
    <div className="overline mb-1">{title}</div>
    {subtitle && <div className="text-[11px] text-zinc-500 mb-3">{subtitle}</div>}
    {!subtitle && <div className="mb-3" />}
    <div className="space-y-3">{children}</div>
  </div>
);

// M-12: autoComplete durchreichen (type="tel" autoComplete="tel" an den Telefonfeldern).
const Field = ({ label, value, onChange, type = "text", multiline, rows = 2, required, testid, placeholder, disabled, helper, inputMode, maxLength, autoComplete }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    {multiline ? (
      <textarea data-testid={testid} value={value} onChange={(e) => onChange(e.target.value)} required={required}
                rows={rows} className="input-base w-full mt-1" placeholder={placeholder} disabled={disabled} />
    ) : (
      <input data-testid={testid} type={type} value={value} onChange={(e) => onChange(e.target.value)}
             required={required} className="input-base w-full mt-1 disabled:opacity-50"
             placeholder={placeholder} disabled={disabled}
             inputMode={inputMode} maxLength={maxLength} autoComplete={autoComplete} />
    )}
    {helper && <div className="text-[11px] text-zinc-500 mt-1 leading-snug">{helper}</div>}
  </div>
);

// Runde 22 (11.09.2026): optionales required (Zahlungsart ist Pflicht).
const SelectField = ({ label, value, onChange, options, testid, required }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    <select data-testid={testid} value={value} onChange={(e) => onChange(e.target.value)}
            required={required}
            className="input-base w-full mt-1 appearance-none">
      {options.map((o) => (
        <option key={o.value} value={o.value} className="bg-zinc-900">{o.label}</option>
      ))}
    </select>
  </div>
);

// Runde 22 (11.09.2026): Kästchen der Empfangsbestätigung — gestaltet wie
// der MwSt-Kasten, die ganze Zeile ist klickbar. Ein Zahlenfeld innerhalb
// der Zeile (Schlüsselanzahl) schaltet das Kästchen beim Hineinklicken
// nicht um (Browser-Regel für interaktive Elemente in einem label).
const CheckRow = ({ checked, onChange, testid, children }) => (
  <label className="flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2.5 cursor-pointer text-sm"
         style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
    <input type="checkbox" checked={!!checked}
           onChange={(e) => onChange(e.target.checked)}
           data-testid={testid}
           className="h-4 w-4 shrink-0 accent-red-500 cursor-pointer" />
    {children}
  </label>
);
