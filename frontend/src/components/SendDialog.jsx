import { useEffect, useMemo, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { useNavigate } from "react-router-dom";
import { X, Send, MessageCircle, Mail, Save, Calendar as CalIcon, FileText, ShieldCheck } from "lucide-react";
import { openContractPdf } from "@/lib/pdf";
import { dateiTeilen, kannDateiTeilen, pdfDatei } from "@/lib/teilen";
import { nachKorrektur } from "@/lib/versand";
import { MODAL_ATTRIBUTE, useModal } from "@/lib/useModal";

// Pruefbericht 20.09.2026 (U-73): Fassungsnummer aus der Kopfzeile der
// PDF-Antwort (X-Vertrag-Version) — sie gehoert zu GENAU diesem Blob und
// geht beim Teilen mit, damit der Server eine inzwischen veraltete Datei
// erkennt. Unlesbar/fehlend -> null (dann prueft der Server wie bisher).
export function fassungAusKopf(headers) {
  const roh = headers?.["x-vertrag-version"] ?? headers?.["X-Vertrag-Version"];
  const n = Number.parseInt(String(roh ?? ""), 10);
  return Number.isFinite(n) && n >= 1 ? n : null;
}

/**
 * Rollenprüfung 22.09.2026 (RP-406/RP-417): Abholtermin zu einem Vertrag
 * anlegen, der KEINEN offenen Termin hat (ohne Abholdatum erstellt, oder der
 * letzte Termin ist "nicht abgeholt"). Vorher meldete "Speichern & Termin"
 * auch dann "Termin im Terminplaner angelegt", wenn nichts angelegt wurde —
 * appointment_id zeigte noch auf den geschlossenen Termin.
 *
 * Liefert { angelegt: true, hinweis } oder { angelegt: false, grund }:
 *   "offen"      — es gibt schon einen offenen Termin (nichts zu tun)
 *   "storniert"  — der Kauf ist storniert: kein neuer Termin (das hebt nur
 *                  der Chef im Terminplaner auf)
 *   "abgeholt"   — das Fahrzeug ist schon abgeholt
 * Fehler des Servers werden geworfen (Aufrufer zeigt errMsg).
 */
export async function abholterminAnlegen(contract, client = api) {
  const kv = contract?.kaufvorgang_status;
  if (kv === "storniert") return { angelegt: false, grund: "storniert" };
  if (kv === "abgeholt") return { angelegt: false, grund: "abgeholt" };
  // Ohne Angabe aus der Vertragsliste (Dialog direkt nach dem Erstellen):
  // ein frisch angelegter Vertrag mit appointment_id hat einen offenen Termin.
  const offen = typeof contract?.termin_offen === "boolean"
    ? contract.termin_offen : Boolean(contract?.appointment_id);
  if (offen) return { angelegt: false, grund: "offen" };
  const cd = contract?.contract_data || {};
  try {
    const { data } = await client.post("/appointments", {
      vehicle_id: contract.vehicle_id, contract_id: contract.id,
      seller_name: contract.seller_name, seller_phone: contract.seller_phone,
      seller_email: contract.seller_email,
      pickup_address: `${cd.seller_address || ""} ${cd.seller_zip || ""} ${cd.seller_city || ""}`.trim(),
      pickup_date: contract.pickup_date || "",
      pickup_time: contract.pickup_time || "",
      status: "offen",
    });
    return { angelegt: true, hinweis: data?.hinweis || "" };
  } catch (err) {
    // Zwischenzeitlich doch ein offener Termin (anderer Tab): kein Fehler.
    const d = err?.response?.data?.detail;
    if (err?.response?.status === 409 && typeof d === "string" && /bereits einen offenen/i.test(d)) {
      return { angelegt: false, grund: "offen" };
    }
    throw err;
  }
}

export const TERMIN_MELDUNG = {
  offen: "Der Abholtermin steht schon im Terminplaner.",
  storniert: "Der Kauf ist storniert — es wird kein neuer Abholtermin angelegt.",
  abgeholt: "Das Fahrzeug ist bereits abgeholt.",
};

export default function SendDialog({ open, contract, onClose }) {
  const { dealer, refresh } = useAuth();
  const nav = useNavigate();
  const [tab, setTab] = useState("whatsapp");
  // Pruefbericht 20.09.2026 (U-92): contract?. — und beim Wechsel auf einen
  // anderen Vertrag werden Empfaenger und Versandzustand unten zurueckgesetzt.
  const [phone, setPhone] = useState(contract?.seller_phone || "");
  const [email, setEmail] = useState(contract?.seller_email || "");
  // Pruefbericht 20.09.2026 (M-07): role=dialog, Escape, Fokus (lib/useModal).
  const dialogRef = useModal(onClose, { offen: Boolean(open) });
  // Wunsch Ahmad 21.09.2026: Ging schon eine FRUEHERE Fassung dieses
  // Vertrags raus, ist das hier der "erneute Versand (nach Korrektur)" —
  // mit eigenem Betreff und Text aus den Einstellungen.
  const korrektur = nachKorrektur(contract);
  // 19.09.2026 (sichtbarer Mangel 3): Die Einstellungen bewerben acht
  // Platzhalter, ersetzt wurde nur {händler_name} — {kunde_name}, {fahrzeug},
  // {abholdatum} usw. gingen WOERTLICH an den Verkaeufer. Jetzt werden alle
  // beworbenen Platzhalter aus Vertrag und Firmendaten gefuellt.
  // Rollenprüfung 22.09.2026 (RP-220/RP-371): zuerst die im VERTRAG
  // eingefrorenen Firmenangaben, dann die des Betrachters — verschickt der
  // Chef den Vertrag eines Suchers (Filial-Firmenname), nannte der Text sonst
  // die Chef-Firma, Vertrag und Absender aber die Filiale.
  const platzhalter = (text, d = dealer) => {
    const cd = contract?.contract_data || {};
    const datum = String(contract?.pickup_date || cd.pickup_date || "");
    const [j, m, t] = datum.split("-");
    const datumDe = (j && m && t) ? `${t}.${m}.${j}` : datum;
    const zeit = contract?.pickup_time || cd.pickup_time || "";
    // Wie im Backend (vertrag_platzhalter.abholzeitpunkt): "TT.MM.JJJJ um HH:MM Uhr".
    const abhol = datumDe && zeit ? `${datumDe} um ${zeit} Uhr` : (datumDe || zeit);
    // Rollenprüfung 22.09.2026 (RP-432): zuerst die im Vertrag bearbeiteten
    // Werte (nach einer Korrektur des Fahrers stehen sie nur dort).
    const marke = cd.vehicle_make || contract?.make || cd.make || "";
    const modell = cd.vehicle_model || contract?.model || cd.model || "";
    const roh = String(contract?.payment_method || cd.payment_method || "");
    const k = roh.toLowerCase();
    const zahlung = k === "bar" ? "Barzahlung"
      : k.includes("echtzeit") ? "Echtzeitüberweisung"
      : ["überweisung", "ueberweisung", "banküberweisung", "bankueberweisung"].includes(k)
        ? "Banküberweisung" : roh;
    const preis = Number(contract?.purchase_price ?? cd.purchase_price);
    const preisText = Number.isFinite(preis)
      ? `${preis.toLocaleString("de-DE", { minimumFractionDigits: 2 })} €` : "";
    const werte = {
      "{händler_name}": cd.dealer_company || d?.company_name || "",
      "{kunde_name}": contract?.seller_name || cd.seller_name || "",
      "{fahrzeug}": [marke, modell].filter(Boolean).join(" "),
      "{marke}": marke,
      "{modell}": modell,
      "{abholdatum}": abhol,
      "{telefon}": cd.dealer_phone || d?.phone || "",
      "{email}": cd.dealer_email || d?.email || d?.contact_email || "",
      // 20.09.2026 (Wunsch Ahmad): Übergabeort und Zahlungsart — dieselben
      // Namen wie im Backend (vertrag_platzhalter.py), damit derselbe Text
      // im Versand-Dialog und im PDF gleich aussieht.
      "{ort}": [contract?.seller_address || cd.seller_address,
                [contract?.seller_zip || cd.seller_zip,
                 contract?.seller_city || cd.seller_city]
                  .filter(Boolean).join(" ")].filter(Boolean).join(", "),
      "{zahlungsart}": zahlung,
      "{kaufpreis}": preisText,
      "{vertragsnummer}": contract?.contract_no || cd.contract_no || "",
      // Entscheidung Ahmad 22.09.2026: die Vertrags-Kundennummer, nie die
      // Anmeldenummer (kunden_nr) — dieselbe Regel wie im Backend.
      "{kundennummer}": String(cd.vertrags_kundennummer || d?.vertrags_kundennummer || ""),
      "{haendler_name}": cd.dealer_company || d?.company_name || "",
    };
    // Wie im Backend: fehlt eine Angabe, steht dort "____" — nie der
    // Platzhalter selbst. Ein Kunde darf nie "{abholdatum}" lesen.
    return Object.entries(werte).reduce(
      (s, [name, wert]) => s.replaceAll(name, String(wert || "").trim() || "____"),
      text || "");
  };
  // Rollenprüfung 22.09.2026 (RP-424): auch der BETREFF geht durch die
  // Platzhalter — "{fahrzeug}" ging sonst wörtlich an den Verkäufer (der
  // Server setzt sie zusätzlich ein, siehe send_contract).
  // (Parameter heißt bewusst `dealer`: frischer Stand nach refresh() oder
  // der aus dem Kontext.)
  const vorlagen = (dealer) => ({
    subject: platzhalter((korrektur && dealer?.email_subject_korrektur) || dealer?.email_subject
      || "Kaufvertrag für Ihr Fahrzeug", dealer),
    waMsg: platzhalter(dealer?.whatsapp_template, dealer),
    emailMsg: platzhalter((korrektur && dealer?.email_template_korrektur) || dealer?.email_template,
                          dealer),
  });
  const [subject, setSubject] = useState(() => vorlagen(dealer).subject);
  const [waMsg, setWaMsg] = useState(() => vorlagen(dealer).waMsg);
  const [emailMsg, setEmailMsg] = useState(() => vorlagen(dealer).emailMsg);
  // Rollenprüfung 22.09.2026 (RP-490): Vorlagen, die der Chef geändert hat,
  // während der Tab offen war, kamen nie an (Kontext ohne refresh). Beim
  // Öffnen frisch laden und alle Felder, die der Nutzer NICHT angefasst hat,
  // neu vorbelegen.
  const angefasst = useRef({});
  useEffect(() => {
    if (!open || !refresh) return undefined;
    angefasst.current = {};
    let aktiv = true;
    Promise.resolve(refresh())
      .then((data) => {
        if (!aktiv || !data?.dealer) return;
        const neu = vorlagen(data.dealer);
        if (!angefasst.current.subject) setSubject(neu.subject);
        if (!angefasst.current.waMsg) setWaMsg(neu.waMsg);
        if (!angefasst.current.emailMsg) setEmailMsg(neu.emailMsg);
      })
      .catch(() => {});
    return () => { aktiv = false; };
  }, [open, contract?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const [busy, setBusy] = useState(false);
  // Wunsch Ahmad 18.09.2026: Das Beweisdokument entsteht nicht mehr
  // automatisch bei jedem Vergleich. Nach dem Versand fragen wir einmal
  // nach — "Ja" merkt es beim Server vor, der Worker baut das PDF.
  const [beweisFrage, setBeweisFrage] = useState(false);
  const [beweisBusy, setBeweisBusy] = useState(false);
  const [beweisFertig, setBeweisFertig] = useState(false);
  // Nachpruefung Runde 10: EIN Schluessel je geoeffnetem Dialog. Ein erneuter
  // Klick nach Fehler oder Timeout traegt denselben Schluessel und laeuft
  // serverseitig in die Wiederaufnahme statt in eine zweite Zustellung.
  // Nach einem erfolgreichen Versand gibt es einen neuen Schluessel, damit
  // ein bewusster zweiter Versand moeglich bleibt.
  const neuerSchluessel = () => (crypto.randomUUID && crypto.randomUUID())
    || `${Date.now()}-${Math.random()}`;
  const keyRef = useRef(neuerSchluessel());
  useEffect(() => { if (open) keyRef.current = neuerSchluessel(); }, [open]);

  // WhatsApp am Handy (09/2026): Die digitale Fassung wird vorab geladen,
  // damit das Teilen direkt beim Tipp passiert — Browser verlangen dafuer
  // eine frische Nutzeraktion. Kann das Geraet Dateien teilen, haengt das
  // PDF von der eigenen Nummer des Suchers an; sonst (PC) kommt ein
  // Download-Link in die Nachricht.
  const [pdf, setPdf] = useState(null);
  // Runde 16 (15.09.2026): die vorab geladene Fassung wird beim Zurueckkehren
  // in den Tab und alle 45 s erneuert — sonst teilt das Handy nach einer
  // Terminverschiebung (Vertrag v1 -> v2) noch die alte Fassung. Ein Abruf
  // direkt vor dem Teilen ginge nicht: Browser verlangen dafuer eine frische
  // Nutzeraktion ohne Wartezeit.
  const [pdfStand, setPdfStand] = useState(0);
  useEffect(() => {
    if (!open) return undefined;
    const erneuern = () => { if (document.visibilityState === "visible") setPdfStand((n) => n + 1); };
    const timer = window.setInterval(erneuern, 45000);
    document.addEventListener("visibilitychange", erneuern);
    window.addEventListener("focus", erneuern);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", erneuern);
      window.removeEventListener("focus", erneuern);
    };
  }, [open]);
  const probe = useMemo(() => {
    try { return new File(["%PDF-1.4"], "probe.pdf", { type: "application/pdf" }); }
    catch { return null; }
  }, []);
  const geraetKannTeilen = kannDateiTeilen(pdf || probe);
  // Pruefbericht 20.09.2026 (B16/F17): Scheiterte das Vorabladen (503, 404),
  // blieb der Hauptknopf fuer immer auf "PDF wird vorbereitet…" — ohne
  // Grund, ohne neuen Versuch. Jetzt: Fehlertext + "Erneut versuchen".
  const [pdfFehler, setPdfFehler] = useState("");
  const pdfFuer = useRef(null);
  // U-73: Fassung der vorab geladenen Datei (null = unbekannt).
  const pdfFassung = useRef(null);
  useEffect(() => {
    if (!open || !contract?.id) return undefined;
    let aktiv = true;
    // Nur bei einem ANDEREN Vertrag leeren — das Auffrischen alle 45 s liess
    // den Knopf sonst jedes Mal kurz auf "wird vorbereitet" springen.
    if (pdfFuer.current !== contract.id) {
      pdfFuer.current = contract.id;
      pdfFassung.current = null;
      setPdf(null);
    }
    api.get(`/contracts/${contract.id}/pdf`, { responseType: "blob", params: { variante: "digital" } })
      .then((r) => {
        if (!aktiv) return;
        const name = contract.filename || `Kaufvertrag ${contract.make || ""} ${contract.model || ""}`.trim();
        pdfFassung.current = fassungAusKopf(r.headers);
        setPdf(pdfDatei(r.data, name));
        setPdfFehler("");
      })
      .catch((err) => {
        if (!aktiv) return;
        // Eine schon geladene Fassung bleibt nutzbar, wenn nur das
        // Auffrischen scheitert; ohne Fassung wird der Grund angezeigt.
        setPdfFehler(errMsg(err, "Die PDF-Datei konnte nicht vorbereitet werden"));
      });
    return () => { aktiv = false; };
  }, [open, contract?.id, pdfStand]); // eslint-disable-line react-hooks/exhaustive-deps

  // Befund Ahmad 10.09.2026: "die Nummer des Verkaeufers wird nie geoeffnet".
  // window.open NACH dem Server-Aufruf gilt fuer den Browser nicht mehr als
  // Nutzerklick und wird als Popup geblockt. Deshalb: das Fenster SOFORT im
  // Klick oeffnen und erst danach auf die WhatsApp-Adresse leiten. Klappt
  // auch das nicht (strenger Blocker), bleibt ein Knopf zum Nachoeffnen.
  const [waUrl, setWaUrl] = useState("");
  // Pruefbericht 20.09.2026 (M31): Nach einem erfolgreichen Versand gibt es
  // bewusst einen neuen Schluessel (ein gewollter zweiter Versand bleibt
  // moeglich) — ein versehentlicher zweiter Klick stellte damit aber sicher
  // doppelt zu. Jetzt fragt der zweite E-Mail-Versand einmal nach.
  const [emailGesendet, setEmailGesendet] = useState(false);
  // Pruefbericht 20.09.2026 (U-92): Der Dialog wird in der Vertragsliste fuer
  // verschiedene Vertraege wiederverwendet — Empfaenger und Versandzustand
  // gehoeren zum VERTRAG, nicht zum Dialog.
  useEffect(() => {
    setPhone(contract?.seller_phone || "");
    setEmail(contract?.seller_email || "");
    setWaUrl("");
    setEmailGesendet(false);
    setBeweisFrage(false);
    setBeweisFertig(false);
  }, [contract?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;

  // Pruefbericht 20.09.2026 (U-93): Der Versand IST erfolgt, aber der Vertrag
  // war beim Vermerk nicht mehr erreichbar (Loeschung begonnen) — das Archiv
  // zeigt ihn weiter als unversendet. Bisher sah der Nutzer nur "versendet".
  const vermerkPruefen = (data) => {
    if (data?.status_vermerk === "nicht_gespeichert") {
      toast.warning("Versendet, aber im Archiv nicht vermerkt — bitte den Vertrag im "
        + "Vertragsarchiv prüfen.", { duration: 15000 });
    }
  };

  const send = async (channel) => {
    if (channel === "email" && emailGesendet
        && !window.confirm("Diese E-Mail wurde gerade schon versendet. Wirklich noch einmal senden?")) {
      return;
    }
    setBusy(true);
    let fenster = null;
    if (channel === "whatsapp") {
      try { fenster = window.open("", "_blank"); } catch { fenster = null; }
      if (fenster) { try { fenster.opener = null; } catch { /* egal */ } }
    }
    try {
      const idempotency_key = keyRef.current;
      const body = channel === "whatsapp"
        ? { channel, recipient: phone, message: waMsg, idempotency_key, methode: "link" }
        : { channel, recipient: email, subject, message: emailMsg,
            idempotency_key };
      const { data } = await api.post(`/contracts/${contract.id}/send`, body);
      keyRef.current = neuerSchluessel();
      vermerkPruefen(data);
      if (channel === "whatsapp" && data.wa_url) {
        setWaUrl(data.wa_url);
        let geoeffnet = false;
        if (fenster && !fenster.closed) {
          try { fenster.location.href = data.wa_url; fenster.focus(); geoeffnet = true; } catch { geoeffnet = false; }
        }
        if (!geoeffnet) {
          const w2 = window.open(data.wa_url, "_blank", "noopener");
          geoeffnet = !!w2;
        }
        const bis = data.link_gueltig_bis
          ? new Date(data.link_gueltig_bis).toLocaleDateString("de-DE") : null;
        if (geoeffnet) {
          toast.success(bis
            ? `WhatsApp-Chat geöffnet · Download-Link zum Vertrag steht in der Nachricht (gültig bis ${bis})`
            : "WhatsApp-Chat geöffnet · Download-Link zum Vertrag steht in der Nachricht");
        } else {
          toast.warning("Dein Browser hat das WhatsApp-Fenster blockiert — bitte unten auf „WhatsApp jetzt öffnen“ tippen.");
        }
        // Der Server hat den Versand vermerkt — auch wenn der Browser das
        // WhatsApp-Fenster blockiert hat und der Nutzer es gleich per Knopf
        // oeffnet. Deshalb hier fragen, nicht nur im geoeffnet-Fall.
        if (contract.vehicle_id && !beweisFertig) setBeweisFrage(true);
      } else {
        if (fenster && !fenster.closed) { try { fenster.close(); } catch { /* egal */ } }
        const z = data?.zustellung;
        // Runde 8: "bereits registriert" hiess frueher auch dann, wenn der
        // Versand noch lief oder abgebrochen war. Jetzt sagt der Server,
        // was wirklich ist — und der Nutzer sieht es.
        if (data?.bereits_gesendet && z === "laeuft") {
          toast.info("Dieser Versand läuft gerade noch — bitte einen Moment warten.");
        } else if (z === "unklar") {
          toast.warning("Der Versand hat kein Ergebnis gemeldet. Bitte noch einmal "
            + "auf Senden klicken — es wird garantiert nicht doppelt zugestellt.");
        } else if (data?.bereits_gesendet) toast.info("Dieser Versand wurde bereits registriert.");
        else if (z === "versendet" && data?.hinweis) {
          // Rollenprüfung 22.09.2026 (RP-448): Während des Versands entstand
          // eine neue Fassung — die Mail enthielt noch die vorherige. Der
          // Server hält den Vertrag deshalb unversendet; hier stand trotzdem
          // "E-Mail mit Vertrag versendet". Jetzt die Warnung, und ein
          // zweiter Versand geht ohne Rückfrage.
          toast.warning(data.hinweis, { duration: 15000 });
        }
        else if (z === "versendet") {
          setEmailGesendet(true);
          // Der Sucher bekommt immer eine Kopie mit dem PDF (09/2026).
          toast.success(data?.kopie === "gesendet"
            ? "E-Mail mit Vertrag versendet · Kopie liegt in deinem Postfach"
            : "E-Mail mit Vertrag versendet");
          if (data?.kopie === "fehlgeschlagen") {
            toast.warning("Die Kopie an dich konnte nicht zugestellt werden — "
              + "der Vertrag ist beim Kunden angekommen.");
          }
        }
        else if (z === "mock") toast.success("Testmodus: Versand nur protokolliert, keine E-Mail");
        else toast.success("Versand registriert");
        // Rollenprüfung 22.09.2026 (RP-473): Ohne gültige E-Mail-Adresse
        // (eigene oder der Firma) landen Antworten des Verkäufers bei der
        // Plattformadresse — und eine Kopie an dich gab es auch nicht.
        if (data?.antwort_adresse_fehlt) {
          toast.warning("Antworten des Verkäufers erreichen dich nicht: Es ist keine gültige "
            + "E-Mail-Adresse hinterlegt. Bitte in den Einstellungen eine E-Mail-Adresse "
            + "eintragen.", { duration: 15000 });
        } else if (z === "versendet" && data?.kopie === "nicht_moeglich") {
          toast.info("Keine Kopie an dich: Für dein Konto ist keine eigene E-Mail-Adresse "
            + "hinterlegt (Einstellungen). Antworten gehen an die Firmenadresse.");
        }
        // RP-221: ein früherer Versuch an diese Adresse blieb ohne Ergebnis.
        if (data?.frueherer_versand_unklar) {
          toast.info("Ein früherer Versandversuch an diesen Empfänger hatte kein Ergebnis — "
            + "er wurde durch diesen Versand ersetzt.");
        }
        // Nur nach einem echten Versand fragen — nicht, wenn er noch laeuft
        // oder ohne Ergebnis blieb (dann klickt der Nutzer gleich erneut),
        // und nicht, wenn eine alte Fassung rausging (RP-448).
        if (contract.vehicle_id && !beweisFertig && z !== "unklar" && !data?.hinweis
            && !(data?.bereits_gesendet && z === "laeuft")) {
          setBeweisFrage(true);
        }
      }
    } catch (err) {
      if (fenster && !fenster.closed) { try { fenster.close(); } catch { /* egal */ } }
      toast.error(errMsg(err, "Versand fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  // Handy: PDF ueber das Teilen-Menue an WhatsApp uebergeben (eigene Nummer).
  // Erst teilen (Nutzeraktion!), dann den Versand am Vertrag vermerken.
  const teilen = async () => {
    if (!pdf) return;
    setBusy(true);
    try {
      const titel = `Kaufvertrag ${contract.make || ""} ${contract.model || ""}`.trim();
      // U-73: die Fassung GENAU dieser Datei — nicht die des Vertrags im
      // Zustand, der kann seit dem Vorabladen schon neuer sein.
      const fassung = pdfFassung.current;
      const ergebnis = await dateiTeilen({ datei: pdf, text: waMsg, titel });
      if (ergebnis === "geteilt") {
        let vermerkt = true;
        try {
          const { data } = await api.post(`/contracts/${contract.id}/send`, {
            channel: "whatsapp", recipient: phone, message: waMsg,
            idempotency_key: keyRef.current, methode: "teilen",
            ...(fassung ? { version: fassung } : {}),
          });
          keyRef.current = neuerSchluessel();
          vermerkPruefen(data);
        } catch (err) {
          const d = err?.response?.data?.detail;
          if (err?.response?.status === 409 && d?.code === "fassung_veraltet") {
            // U-73: Geteilt wurde eine inzwischen veraltete Fassung (neuer
            // Termin, neuer Preis) — kein Vermerk; die neue Datei wird
            // jetzt geladen, der Nutzer teilt noch einmal.
            vermerkt = false;
            keyRef.current = neuerSchluessel();
            setPdfStand((n) => n + 1);
            toast.warning(d.msg || "Der Vertrag hat inzwischen eine neue Fassung — bitte die neue "
              + "Fassung noch einmal teilen.", { duration: 15000 });
          } else {
            toast.warning(errMsg(err, "Der Versand konnte nicht im Archiv vermerkt werden"));
          }
        }
        if (vermerkt) {
          toast.success("An WhatsApp übergeben · Chat des Verkäufers wählen und senden");
          if (contract.vehicle_id && !beweisFertig) setBeweisFrage(true);
        }
      } else if (ergebnis === "abgebrochen") {
        toast.info("Teilen abgebrochen");
      } else if (ergebnis === "erneut") {
        // Pruefbericht 20.09.2026 (K-14): Der Browser verlangt einen frischen
        // Tipp — frueher lief das still in den Chat mit oeffentlichem Link.
        toast.warning("Bitte noch einmal auf „Per WhatsApp teilen“ tippen.");
      } else if (!phone) {
        toast.error("Teilen ist auf diesem Gerät nicht möglich — für den Chat mit Download-Link "
          + "bitte oben die Telefonnummer eintragen.");
      } else if (window.confirm("Teilen ist auf diesem Gerät nicht möglich.\n\nStattdessen den "
          + "WhatsApp-Chat mit einem Download-Link zum Vertrag öffnen? Der Link ist zeitlich "
          + "begrenzt und für jeden nutzbar, der ihn kennt.")) {
        // K-14: nur nach Rueckfrage — der Link ist oeffentlich.
        await send("whatsapp");
      }
    } finally {
      setBusy(false);
    }
  };

  const beweisAnfordern = async () => {
    setBeweisBusy(true);
    try {
      const { data } = await api.post("/beweise/anfordern",
        { vehicle_id: contract.vehicle_id });
      setBeweisFertig(true);
      setBeweisFrage(false);
      toast.success("Beweisdokument wird erstellt — es liegt gleich in der Fahrzeugakte.");
      if (data?.hinweis) toast.warning(data.hinweis, { duration: 12000 });
    } catch (err) {
      toast.error(errMsg(err, "Beweisdokument konnte nicht angefordert werden"));
    } finally {
      setBeweisBusy(false);
    }
  };

  // Pruefbericht 20.09.2026 (U-89): Der Knopf hiess "PDF speichern" und
  // meldete "PDF gespeichert" — gespeichert war der Vertrag laengst, der
  // Knopf fuehrt nur ins Archiv. Jetzt sagt er das.
  const saveOnly = () => {
    toast.success("Der Vertrag liegt im Vertragsarchiv");
    onClose();
    nav("/app/vertraege");
  };

  const saveAndSchedule = async () => {
    setBusy(true);
    try {
      // Termin wird beim PDF-Erstellen bereits automatisch angelegt, wenn ein
      // Abholdatum gesetzt war. Gibt es keinen OFFENEN Termin (ohne
      // pickup_date erstellt, oder "nicht abgeholt"), legen wir hier einen an.
      // Rollenprüfung 22.09.2026 (RP-406): Erfolgsmeldung nur, wenn wirklich
      // ein Termin entstand — sonst sagt die Meldung, was ist.
      const erg = await abholterminAnlegen(contract);
      if (erg.angelegt) {
        toast.success("Termin im Terminplaner angelegt");
        if (erg.hinweis) toast.warning(erg.hinweis, { duration: 10000 });
      } else if (erg.grund === "storniert") {
        toast.info(TERMIN_MELDUNG.storniert);
        return;
      } else {
        toast.info(TERMIN_MELDUNG[erg.grund] || TERMIN_MELDUNG.offen);
      }
      onClose();
      nav("/app/termine");
    } catch (err) {
      toast.error(errMsg(err, "Termin konnte nicht erstellt werden"));
    } finally {
      setBusy(false);
    }
  };
  // Rollenprüfung 22.09.2026 (RP-216/RP-367): Nach Storno / "nicht abgeholt"
  // verschickt der Server den Vertrag nicht mehr — der Dialog sagt es vorab.
  const kaufBeendet = ["storniert", "nicht_abgeholt"].includes(contract?.kaufvorgang_status);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4 bg-black/70 backdrop-blur-sm">
      {/* M-07: Rahmen ist der Dialog (role/aria-modal/Fokus); M-11: Kopfzeile
          bleibt beim Scrollen oben, Schliessen-Knopf mit 44-px-Trefferflaeche.
          Handy-Ansicht (24.09.2026): Hoehe nach dvh (iOS-Adressleiste). */}
      <div ref={dialogRef} {...MODAL_ATTRIBUTE} aria-labelledby="send-dialog-titel"
           className="bg-[var(--bg-surface)] border w-full max-w-2xl rounded-md modal-hoehe overflow-y-auto"
           style={{ borderColor: "var(--border-default)" }} data-testid="send-dialog">
        <div className="flex items-center justify-between px-6 py-3 border-b sticky top-0 bg-[var(--bg-surface)] z-10"
             style={{ borderColor: "var(--border-default)" }}>
          <div>
            <div className="overline">Vertrag versenden</div>
            <div className="font-display font-bold text-lg" id="send-dialog-titel">{contract.make} {contract.model}</div>
          </div>
          <button type="button" onClick={onClose} data-testid="close-send" aria-label="Schließen"
                  className="w-11 h-11 -mr-2 flex items-center justify-center rounded-full text-zinc-400 hover:text-white hover:bg-white/10">
            <X size={20} />
          </button>
        </div>

        <div className="px-6 pt-4">
          {/* M-20: echte Reiter-Semantik (role=tablist/tab, aria-selected) */}
          <div className="flex border rounded-sm overflow-hidden w-full" role="tablist" aria-label="Versandweg"
               style={{ borderColor: "var(--border-default)" }}>
            <TabBtn active={tab === "whatsapp"} onClick={() => setTab("whatsapp")} icon={MessageCircle} label="WhatsApp" testid="tab-whatsapp" />
            <TabBtn active={tab === "email"} onClick={() => setTab("email")} icon={Mail} label="E-Mail" testid="tab-email" />
          </div>
        </div>

        <div className="p-6 space-y-4">
          {kaufBeendet && (
            <div className="text-[12px] rounded-sm border px-3 py-2" role="alert"
                 data-testid="send-kauf-beendet"
                 style={{ borderColor: "rgba(255,159,10,0.35)", background: "rgba(255,159,10,0.10)",
                          color: "var(--text-primary)" }}>
              {contract.kaufvorgang_status === "storniert"
                ? "Der Kauf ist storniert — der Vertrag wird nicht mehr verschickt."
                : "Das Fahrzeug wurde nicht abgeholt — der Vertrag wird erst wieder verschickt, "
                  + "wenn ein neuer Abholtermin angelegt ist („Speichern & Termin“)."}
            </div>
          )}
          {tab === "whatsapp" ? (
            <>
              {/* M-12: Telefon-Tastatur am Handy */}
              <Field label="Telefonnummer (international, z.B. +49…)" value={phone} onChange={setPhone} testid="wa-phone"
                     type="tel" inputMode="tel" autoComplete="tel" />
              <div>
                <label className="text-xs text-zinc-400">Nachricht</label>
                <textarea data-testid="wa-message" rows={5} className="input-base w-full mt-1"
                          value={waMsg}
                          onChange={(e) => { angefasst.current.waMsg = true; setWaMsg(e.target.value); }} />
              </div>
              {geraetKannTeilen ? (
                <>
                  <div className="text-[11px] text-zinc-500" data-testid="wa-hinweis-teilen">
                    Dein Handy hängt das PDF direkt an: Tippe auf „Per WhatsApp teilen", wähle WhatsApp und
                    dann den Chat des Verkäufers. Der Vertrag geht von deiner eigenen Nummer raus.
                  </div>
                  <button type="button" data-testid="wa-share-btn" onClick={teilen} disabled={busy || !pdf}
                          className="kinetic-button w-full py-3 rounded-sm flex items-center justify-center gap-2 font-bold disabled:opacity-50">
                    <Send size={15} /> {pdf ? "Per WhatsApp teilen (PDF anhängen)"
                      : pdfFehler ? "PDF gerade nicht verfügbar" : "PDF wird vorbereitet…"}
                  </button>
                  {!pdf && pdfFehler && (
                    <div className="text-[11.5px] rounded-sm border px-3 py-2 flex flex-wrap items-center gap-2"
                         role="alert" data-testid="wa-pdf-fehler"
                         style={{ borderColor: "rgba(255,159,10,0.35)", background: "rgba(255,159,10,0.10)",
                                  color: "var(--text-primary)" }}>
                      <span className="flex-1 min-w-0">
                        {pdfFehler} — du kannst stattdessen den Chat mit Download-Link öffnen.
                      </span>
                      <button type="button" onClick={() => setPdfStand((n) => n + 1)}
                              className="underline underline-offset-2 font-semibold">
                        Erneut versuchen
                      </button>
                    </div>
                  )}
                  <button type="button" data-testid="send-wa-btn" onClick={() => send("whatsapp")} disabled={busy || !phone}
                          className="w-full py-2.5 rounded-sm flex items-center justify-center gap-2 text-sm font-semibold border disabled:opacity-50"
                          style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
                    <MessageCircle size={15} /> Stattdessen Chat mit Download-Link öffnen
                  </button>
                </>
              ) : (
                <>
                  <div className="text-[11px] text-zinc-500" data-testid="wa-hinweis-link">
                    WhatsApp am PC kann kein PDF anhängen. Die Nachricht bekommt deshalb automatisch einen
                    zeitlich begrenzten Download-Link zur digitalen Vertragsfassung. Am Handy hängt AutoSchnell
                    das PDF direkt an.
                  </div>
                  <button data-testid="send-wa-btn" onClick={() => send("whatsapp")} disabled={busy || !phone}
                          className="kinetic-button w-full py-3 rounded-sm flex items-center justify-center gap-2 font-bold disabled:opacity-50">
                    <Send size={15} /> WhatsApp-Chat öffnen (mit Download-Link)
                  </button>
                </>
              )}
              {waUrl && (
                <a href={waUrl} target="_blank" rel="noopener noreferrer" data-testid="wa-open-again"
                   className="w-full py-2.5 rounded-sm flex items-center justify-center gap-2 text-sm font-semibold border"
                   style={{ borderColor: "var(--accent-green, #22c55e)", color: "var(--text-primary)" }}>
                  <MessageCircle size={15} /> WhatsApp jetzt öffnen (Chat mit Verkäufer)
                </a>
              )}
              <button type="button" data-testid="wa-digital-pdf-btn"
                      onClick={async () => {
                        try { await openContractPdf(contract.id, { variante: "digital" }); }
                        catch (err) { toast.error(errMsg(err, "PDF konnte nicht geöffnet werden")); }
                      }}
                      className="w-full py-2 rounded-sm flex items-center justify-center gap-2 text-xs border"
                      style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                <FileText size={13} /> Digitale Fassung ansehen
              </button>
            </>
          ) : (
            <>
              <Field label="E-Mail-Empfänger" value={email} onChange={setEmail} type="email" testid="email-to" />
              {korrektur && (
                <div className="text-[11px] rounded-sm border px-2.5 py-2" data-testid="email-korrektur-hinweis"
                     style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                  Erneuter Versand nach Korrektur: Betreff und Text für die korrigierte Fassung
                  (änderbar in den Einstellungen unter „Versand“).
                </div>
              )}
              <Field label="Betreff" value={subject}
                     onChange={(v) => { angefasst.current.subject = true; setSubject(v); }}
                     testid="email-subject" />
              <div className="text-[11px] text-zinc-500 mt-1">Versand über AutoSchnell mit deinem Firmennamen. Antwortet der Verkäufer, landet die Antwort in deinem Postfach — du bekommst zusätzlich eine Kopie mit PDF.</div>
              <div>
                <label className="text-xs text-zinc-400">Nachricht</label>
                <textarea data-testid="email-message" rows={5} className="input-base w-full mt-1"
                          value={emailMsg}
                          onChange={(e) => { angefasst.current.emailMsg = true; setEmailMsg(e.target.value); }} />
              </div>
              <div className="text-[11px] text-zinc-500">
                Die E-Mail wird mit der digitalen Vertragsfassung im Anhang versendet (ohne Unterschriftsfelder —
                unter „Unterschriften" steht: „Dieser Vertrag ist ohne Unterschrift gültig.") und im Archiv protokolliert.
              </div>
              <button data-testid="send-email-btn" onClick={() => send("email")} disabled={busy || !email}
                      className="kinetic-button w-full py-3 rounded-sm flex items-center justify-center gap-2 font-bold disabled:opacity-50">
                <Send size={15} /> E-Mail senden
              </button>
            </>
          )}

          {beweisFrage && contract.vehicle_id && (
            <div className="rounded-xl border p-4" data-testid="beweis-frage"
                 style={{ borderColor: "var(--border-default)", background: "var(--wa-03)" }}>
              <div className="flex items-center gap-2">
                <ShieldCheck size={15} className="text-[var(--accent-red)]" />
                <span className="text-sm font-semibold">Beweisdokument erstellen lassen?</span>
              </div>
              <div className="text-[11.5px] leading-relaxed mt-1.5" style={{ color: "var(--text-muted)" }}>
                Hält alle Inseratsdaten, die Fotos, die Anzeigen-ID und die Inserats-Adresse
                als PDF fest — für den Fall, dass der Verkäufer später etwas anderes sagt.
                Du findest es danach in der Fahrzeugakte.
              </div>
              <div className="flex gap-2 mt-3">
                <button type="button" onClick={beweisAnfordern} disabled={beweisBusy}
                        data-testid="beweis-ja-btn"
                        className="flex-1 px-4 py-2.5 rounded-sm font-semibold text-white disabled:opacity-50"
                        style={{ background: "var(--st-gruen)" }}>
                  {beweisBusy ? "Wird angefordert …" : "Ja, erstellen"}
                </button>
                <button type="button" onClick={() => setBeweisFrage(false)} disabled={beweisBusy}
                        data-testid="beweis-nein-btn"
                        className="flex-1 px-4 py-2.5 rounded-sm border disabled:opacity-50"
                        style={{ borderColor: "var(--border-default)" }}>
                  Nein, danke
                </button>
              </div>
            </div>
          )}

          {/* Handy-Ansicht (24.09.2026): am Telefon untereinander (die Texte
              brachen sonst dreizeilig um) */}
          <div className="border-t pt-4 flex flex-col sm:flex-row gap-3" style={{ borderColor: "var(--border-default)" }}>
            <button onClick={saveOnly} data-testid="save-only-btn"
                    className="flex-1 px-4 py-3 rounded-sm border hover:bg-white/5 flex items-center justify-center gap-2"
                    style={{ borderColor: "var(--border-default)" }}>
              <Save size={14} /> Fertig — zum Vertragsarchiv
            </button>
            <button onClick={saveAndSchedule} data-testid="save-and-schedule-btn" disabled={busy}
                    className="flex-1 px-4 py-3 rounded-sm flex items-center justify-center gap-2 disabled:opacity-50"
                    style={{ background: "rgba(0,122,255,0.15)", color: "var(--accent-blue)", border: "1px solid rgba(0,122,255,0.4)" }}>
              <CalIcon size={14} /> Speichern & Termin
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// M-20: role=tab + aria-selected — der aktive Reiter war nur optisch erkennbar.
const TabBtn = ({ active, onClick, icon: Icon, label, testid }) => (
  <button type="button" onClick={onClick} data-testid={testid} role="tab" aria-selected={active}
          className={`flex-1 px-4 py-2.5 text-sm flex items-center justify-center gap-2 transition-colors ${
            active ? "bg-white/5 text-white" : "text-zinc-400 hover:text-white"
          }`}>
    <Icon size={14} /> {label}
  </button>
);

// M-12: inputMode/autoComplete durchreichen (Telefonnummer -> Zifferntastatur).
const Field = ({ label, value, onChange, type = "text", testid, inputMode, autoComplete }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    <input data-testid={testid} type={type} value={value} onChange={(e) => onChange(e.target.value)}
           inputMode={inputMode} autoComplete={autoComplete}
           className="input-base w-full mt-1" />
  </div>
);
