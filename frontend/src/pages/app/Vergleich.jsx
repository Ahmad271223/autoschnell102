import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { sitzungsSpeicher } from "@/lib/speicher";
import {
  abbruchFehler, checkLink, istAbbruch, postWithRetry503, TIMEOUT_MESSAGE,
} from "@/lib/linkCheck";
import { inseratsLinkAusText, zwischenablageLesen } from "@/lib/inseratsLink";
import { extensionReady, fetchViaExtension } from "@/lib/clientFetch";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import ContractDialog from "@/components/ContractDialog";
import SendDialog from "@/components/SendDialog";
import ProfileBadge from "@/components/ProfileBadge";
import { filterOeffnen, FILTER_TOAST_ID } from "@/lib/filterOeffnen";
import { INSERAT_EREIGNIS, protokollSuche, startKennung, startMelden } from "@/lib/programmStart";
import { hinweiseZeigen } from "@/lib/hinweise";
import { useAuth } from "@/context/AuthContext";
import { vergleichEntfernen, vergleichLaden, vergleichSichern } from "@/lib/vergleichSpeicher";
// 08.10.2026 (externe Pruefung "Vergleich.jsx zu gross"): Anzeige-Teile und Schalter liegen in ./vergleich/ —
// hier bleiben Zustand und Ablaeufe (Auslesen, Abbrechen, Zwischenablage, Programm-/Helfer-Uebergabe).
import AktionenSpalte from "./vergleich/AktionenSpalte";
import FahrzeugSpalte from "./vergleich/FahrzeugSpalte";
import Suchleiste from "./vergleich/Suchleiste";
import useVergleichsSchalter from "./vergleich/useVergleichsSchalter";
import {
  filterEintraege, liveZaehlerPfad, vertragErstelltMeldung, VERGLEICH_LAEUFT_HINWEIS,
} from "./vergleich/anzeige";

export {
  datenStand, liveZaehlerPfad, vertragErstelltMeldung, VERGLEICH_LAEUFT_HINWEIS,
} from "./vergleich/anzeige";


export default function Vergleich() {
  // Runde 27 (12.09.2026, Pruefbefund P0): Der zuletzt angezeigte Vergleich
  // haengt am KONTO. Vorher lag er unter einem festen Schluessel — meldete
  // sich am selben Browser ein anderer Sucher an, sah er Fahrzeug,
  // Verkaeuferdaten und den letzten Vertrag seines Kollegen.
  const { user, refresh, setDealer } = useAuth();
  const nav = useNavigate();
  const kontoId = user?.id || null;
  // Rollenprüfung 22.09.2026 (RP-023/RP-122/RP-273): Der gespeicherte Stand
  // wird nur EINMAL gelesen (Startwert). Vorher lief der Zugriff samt
  // JSON.parse des ganzen Ergebnisses bei jedem Render — jedem Tastendruck,
  // jeder Wartemeldung, jedem Zähler-Update.
  // Pruefbericht 20.09.2026 (B3): Nur ein Stand MIT Fahrzeug wird
  // wiederhergestellt — eine aeltere Fassung konnte eine Antwort ohne
  // Fahrzeug ("needs_client_fetch") ablegen, und die liess die Seite beim
  // Rendern abstuerzen, bei jedem Neuladen erneut.
  const [restored] = useState(() => {
    const gespeichert = vergleichLaden(sitzungsSpeicher(), kontoId);
    return gespeichert?.result?.vehicle ? gespeichert : null;
  });

  const [url, setUrl] = useState(restored?.url || "");
  const [loading, setLoading] = useState(false);
  const [waitMsg, setWaitMsg] = useState(null);
  const [result, setResult] = useState(restored?.result || null);
  const [counter, setCounter] = useState(restored?.counter || null);
  const [showContract, setShowContract] = useState(false);
  const [contract, setContract] = useState(restored?.contract || null);
  const [showSend, setShowSend] = useState(false);

  // RP-023: Ein wiederhergestellter Kaufvertrag kann inzwischen gelöscht
  // sein (Chef löscht, Übergabe). Vorher standen "PDF öffnen"/"Versenden"
  // weiter da, erst der Klick brachte 404. Einmal im Hintergrund nachfragen.
  useEffect(() => {
    const id = restored?.contract?.id;
    if (!id) return undefined;
    let aktiv = true;
    api.get(`/contracts/${id}`).then(({ data }) => {
      // Prüfbericht 20.09. U-85: den Schnappschuss durch den Serverstand
      // ersetzen — der Versand-Dialog bekam sonst z. B. den Stand vor einer
      // Verkäufer-Korrektur.
      if (aktiv && data?.id === id) setContract((c) => (c?.id === id ? { ...c, ...data } : c));
    }).catch((err) => {
      if (aktiv && err?.response?.status === 404) {
        setContract((c) => (c?.id === id ? null : c));
      }
    });
    return () => { aktiv = false; };
  }, [restored]);

  // Rollenprüfung 22.09.2026 (RP-443): Der Fahrzeugpool behält je Konto nur
  // die neuesten Vergleiche. Ein wiederhergestellter Vergleich kann deshalb
  // auf ein Fahrzeug zeigen, das es nicht mehr gibt — "Kaufvertrag
  // erstellen" endete dann mit 404. Einmal nachsehen; fehlt es, bietet die
  // Seite "Neu vergleichen" an (kostet nichts, der Link liegt im Speicher).
  useEffect(() => {
    const vid = restored?.result?.vehicle_id;
    if (!vid) return undefined;
    let aktiv = true;
    api.get(`/vehicles/${vid}`).catch((err) => {
      if (aktiv && err?.response?.status === 404) {
        setResult((r) => (r?.vehicle_id === vid ? { ...r, fahrzeug_weg: true } : r));
      }
    });
    return () => { aktiv = false; };
  }, [restored]);
  // Portale, Filter automatisch, Filter daneben (./vergleich/useVergleichsSchalter.js); schalterRef = aktuelle
  // Staende fuer einen laufenden Vergleich
  const schalterZustand = useVergleichsSchalter(user);
  const { schalterRef } = schalterZustand;

  // Runde 22 (11.09.2026, Gegenpruefung): Seite verlassen -> ein noch
  // laufender Vergleich oeffnet danach keine Filter-Tabs mehr. Im Effekt auf
  // true setzen (nicht nur im Aufraeumen auf false): React.StrictMode spielt
  // im Dev-Modus Einhaengen/Aushaengen/Einhaengen durch.
  const aktivRef = useRef(true);
  useEffect(() => {
    aktivRef.current = true;
    return () => { aktivRef.current = false; };
  }, []);

  // Runde 24 (11.09.2026): doppelte Hinweis-Toasts (Befund Ahmad).
  //  - laeuftRef: Sperre gegen einen zweiten gleichzeitigen Lauf. "loading"
  //    allein reicht nicht — es stammt aus dem Render, in dem der Aufrufer
  //    entstand. ProfileBadge ruft onChange erst NACH dem await seines PUT
  //    auf, mit der Funktion vom Klick-Zeitpunkt; ein inzwischen per
  //    Einfuegen gestarteter Vergleich sah dort noch loading=false.
  //  - hinweisIdsRef: ids der gezeigten Hinweise, damit der naechste Lauf
  //    denselben Text ersetzt statt stapelt und veraltete schliesst.
  const laeuftRef = useRef(false);
  const hinweisIdsRef = useRef([]);

  // Persist on every meaningful state change. Ohne Ergebnis (neuer Lauf
  // gestartet oder gescheitert) wird der alte Stand entfernt (H8) — sonst
  // kam nach dem Neuladen das vorherige Auto zurueck.
  // Prüfbericht 20.09. U-12: der Link kommt per Ref mit, nicht als
  // Abhängigkeit — sonst wurde bei jedem Tastendruck im Linkfeld das ganze
  // Ergebnis serialisiert und in den Sitzungsspeicher geschrieben.
  const urlRef = useRef(url);
  urlRef.current = url;
  useEffect(() => {
    try {
      if (result) {
        vergleichSichern(sitzungsSpeicher(), kontoId, { url: urlRef.current, result, counter, contract });
      } else {
        vergleichEntfernen(sitzungsSpeicher(), kontoId);
      }
    } catch { /* quota/private mode — silent */ }
  }, [result, counter, contract, kontoId]);

  // Wunsch Ahmad 18.09.2026: Dauert ein Abruf zu lange, bricht das "X" ihn
  // ab — die Seite ist sofort wieder eingabebereit (derselbe oder ein neuer
  // Link). Der Server erfaehrt es ueber die Job-Nummer: wartet dann niemand
  // mehr und hat der Abruf noch nicht begonnen, faellt er ganz weg.
  const abbruchRef = useRef(null);
  const jobRef = useRef(null);

  const abbrechen = (art) => {
    const job = jobRef.current;
    try { abbruchRef.current?.abort(); } catch { /* egal */ }
    // Rollenprüfung 22.09.2026 (RP-004/RP-103/RP-254): Der abgebrochene Lauf
    // ist ab jetzt nicht mehr "der aktuelle" — sein finally (das oft erst
    // Sekunden später kommt) räumt dann nichts mehr ab, auch nicht den
    // Zustand eines inzwischen neu gestarteten Laufs.
    abbruchRef.current = null;
    if (job) {
      api.post(`/listings/check/${job}/abbrechen`).catch(() => { /* egal */ });
      jobRef.current = null;
    }
    laeuftRef.current = false;
    setLoading(false);
    setWaitMsg(null);
    // Runde 24: das alte Ergebnis ist schon weg — seine Hinweise auch.
    hinweisIdsRef.current = hinweiseZeigen(toast, [], hinweisIdsRef.current);
    if (!art?.still) toast.info("Abgebrochen — du kannst sofort einen neuen Link einfügen.");
  };
  const abbrechenRef = useRef(abbrechen);
  abbrechenRef.current = abbrechen;

  // Wunsch Ahmad 18.09.2026: "nur reinklicken, dann ist der kopierte Link
  // automatisch drin" — kein Rechtsklick, kein Strg+V. Nur wenn das Feld leer
  // ist, nur bei echten Inserats-Links, und nur solange der Browser die
  // Zwischenablage hergibt (sonst nie wieder fragen).
  // Rollenprüfung 22.09.2026 (RP-261): Der Kommentar sagte hier "gestartet
  // wird NICHT automatisch" — seit dem Wunsch Ahmads ("nach dem Einfügen soll
  // er auch loslaufen") startet der Vergleich aber gleich mit. Damit ein
  // alter Link nicht bei jedem Klick erneut abgerufen wird, merkt sich
  // `zuletzt` den zuletzt übernommenen Text: derselbe Link kommt nicht zweimal.
  const zwischenablageRef = useRef({ zuletzt: "", moeglich: true });

  // Rueckmeldung Ahmad 18.09.2026: Der Start haing frueher am paste-Ereignis.
  // Das kommt nicht ueberall an (Rechtsklick-Menue, Ziehen und Ablegen,
  // Einfuegen per Klick). Jetzt zaehlt das Ergebnis: Steht auf einen Schlag
  // ein gueltiger Inserats-Link im Feld, laeuft der Vergleich los. Beim
  // Tippen (Zeichen fuer Zeichen) passiert weiterhin nichts.
  const SPRUNG = 12;   // so viele Zeichen auf einmal = eingefuegt, nicht getippt

  const vielleichtStarten = (text, vorher = "") => {
    const neu = (text || "").trim();
    if (!neu) return false;
    // RP-409: geteilter Text ("Schau mal: https://…") -> nur der Link
    const link = inseratsLinkAusText(neu);
    if (!link) return false;
    if (neu.length - (vorher || "").trim().length < SPRUNG) return false;
    // Prüfbericht 20.09. U-15: läuft noch ein Vergleich, wurde der neue Link
    // still ins Feld übernommen und das alte Ergebnis stand darunter — jetzt
    // ein Hinweis auf das X (Abbrechen), der Lauf bleibt unangetastet.
    if (loading || laeuftRef.current) {
      toast.info(VERGLEICH_LAEUFT_HINWEIS, { id: "vergleich-laeuft" });
      return false;
    }
    if (link !== neu) setUrl(link);
    startCompare(null, link);
    return true;
  };

  const ausZwischenablage = async () => {
    if (loading || url.trim() || !zwischenablageRef.current.moeglich) return;
    const { text, moeglich } = await zwischenablageLesen();
    zwischenablageRef.current.moeglich = moeglich;
    if (!text || text === zwischenablageRef.current.zuletzt) return;
    const link = inseratsLinkAusText(text);           // RP-409
    if (!link) return;
    zwischenablageRef.current.zuletzt = text;
    setUrl(link);
    // Wunsch Ahmad: nach dem Einfuegen soll er auch loslaufen.
    if (!vielleichtStarten(link)) {
      toast.success("Link aus der Zwischenablage eingefügt — jetzt „Auslesen“.");
    }
  };

  // ohneFilter (Wunsch Ahmad 03.10.2026): kommt der Sucher aus dem Windows-Programm, hat das die
  // Vergleiche schon geöffnet — dann nur auslesen (für den Kaufvertrag), keine Filter-Tabs ein zweites Mal.
  // vertragOeffnen (Wunsch Ahmad 04.10.2026): aus dem Browser-Helfer ("Kaufvertrag") gleich den Vertrag öffnen —
  // die Daten hat die Erweiterung aus der Inseratsseite gelesen, kein Abruf, kein zweiter Klick.
  const startCompare = async (e, direktUrl, { behalteVertrag = false, ohneFilter = false, vertragOeffnen = false } = {}) => {
    e?.preventDefault?.();
    const roh = (direktUrl ?? url).trim();
    // RP-409: steht im Feld ein geteilter Text, zählt nur der Link darin.
    const ziel = inseratsLinkAusText(roh) || roh;
    if (!ziel) return;
    if (loading || laeuftRef.current) return;   // Mehrfachklicks abfangen
    laeuftRef.current = true;          // Runde 24: sofort, nicht erst nach dem Render
    const steuerung = new AbortController();
    abbruchRef.current = steuerung;
    // Rollenprüfung 22.09.2026 (RP-004/RP-103/RP-254): Jeder Lauf hat seine
    // eigene Kennung (seine Steuerung). Er darf den Zustand nur ändern,
    // solange er der aktuelle ist — nach dem "X" oder einem neuen Lauf hängt
    // er oft noch in einem Schritt, der nicht abbricht (Erweiterung, Ingest).
    // Vorher räumte sein finally danach den NEUEN Lauf ab (kein X mehr,
    // Ladeanzeige weg) und sein Ergebnis überschrieb die Anzeige.
    const aktuell = () => abbruchRef.current === steuerung && !steuerung.signal.aborted;
    const nochAktuell = () => { if (!aktuell()) throw abbruchFehler(); };
    const wartemeldung = (m) => { if (aktuell()) setWaitMsg(m); };
    jobRef.current = null;
    if (ziel !== roh) setUrl(ziel);
    setLoading(true);
    setWaitMsg(null);
    setResult(null);
    setCounter(null);
    // M7/U-06: Der Profilwechsel laeuft denselben Link neu — der eben
    // erstellte Kaufvertrag gehoert weiter dazu und darf nicht verschwinden.
    if (!behalteVertrag) setContract(null);
    // Runde 22 (11.09.2026, Gegenpruefung): ein stehender Blockade-Hinweis
    // traegt die Links des vorherigen Ergebnisses — mit dem alten Ergebnis weg.
    toast.dismiss(FILTER_TOAST_ID);
    try {
      const t0 = Date.now();

      // Schritt 1: Vorab-Check. Bekannte Inserate sind sofort da; neue
      // laufen als Hintergrundjob — wir zeigen die Wartemeldung und
      // fragen den Status ab, statt die Anfrage minutenlang zu halten.
      const zusatz = {
        signal: steuerung.signal,
        onJob: (id) => { if (aktuell()) jobRef.current = id; },
      };
      const check = await checkLink(api, ziel, { onWait: wartemeldung, ...zusatz });
      nochAktuell();
      let data;
      if (check.status === "needs_client_fetch") {
        data = { needs_client_fetch: true, url: check.url };
      } else {
        // Schritt 2: eigentlicher Vergleich (trifft jetzt den Cache).
        // Ein 503 (Rueckstau) wird automatisch wiederholt — der Nutzer
        // sieht nur die Wartemeldung, keine technische Fehlermeldung.
        ({ data } = await postWithRetry503(api, "/mobile/compare",
                                           { url: ziel },
                                           { onWait: wartemeldung, ...zusatz }));
        nochAktuell();
      }

      // Client-seitiges Abrufen (nur Kleinanzeigen, wenn serverseitig aktiv):
      // Der Server kennt den Link noch nicht und bittet den Browser des
      // Nutzers, die Seite zu holen. Wir laden sie über die Erweiterung,
      // schicken das HTML an den Server und fragen erneut ab.
      if (data?.needs_client_fetch) {
        const ready = await extensionReady();
        nochAktuell();
        if (!ready) {
          // Rueckfall (09/2026): ohne Abruf-Helfer holt der Server das
          // Inserat selbst — vorher blockierte hier "Erweiterung installieren".
          const check2 = await checkLink(api, ziel,
                                         { onWait: wartemeldung, ohneErweiterung: true, ...zusatz });
          nochAktuell();
          if (check2.status === "needs_client_fetch") {
            throw new Error("Abruf ohne Erweiterung nicht möglich — bitte später erneut versuchen.");
          }
          ({ data } = await postWithRetry503(api, "/mobile/compare",
                                             { url: ziel, ohne_erweiterung: true },
                                             { onWait: wartemeldung, ...zusatz }));
          nochAktuell();
        } else {
          try {
            // Die Erweiterung kennt kein Abbruchsignal — danach nachsehen.
            const html = await fetchViaExtension(data.url || ziel);
            nochAktuell();
            await api.post("/listings/ingest", { url: data.url || ziel, html },
                           { signal: steuerung.signal });
            nochAktuell();
            ({ data } = await postWithRetry503(api, "/mobile/compare",
                                               { url: ziel },
                                               { onWait: wartemeldung, ...zusatz }));
            nochAktuell();
          } catch (fe) {
            if (istAbbruch(fe) || !aktuell()) throw fe;
            toast.error(errMsg(fe, "Abruf über die Erweiterung fehlgeschlagen"));
            return;                  // finally räumt diesen Lauf auf
          }
        }
      }

      nochAktuell();          // RP-254: kein Ergebnis eines überholten Laufs anzeigen
      // Pruefbericht 20.09.2026 (B3): Auch der zweite Vergleich kann noch
      // "needs_client_fetch" liefern — das Tageskontingent fuer Abrufe ohne
      // Erweiterung ist zwischen Link-Pruefung und Vergleich aufgebraucht
      // worden (ein zweiter Tab genuegt). Ohne Fahrzeug nichts anzeigen:
      // vorher griff die Seite auf result.vehicle zu und stuerzte mit der
      // falschen Meldung "kurz nach einem Update" ab.
      if (!data?.vehicle) {
        throw new Error(data?.needs_client_fetch
          ? "Dieses Kleinanzeigen-Inserat lässt sich heute nicht mehr ohne Browser-Erweiterung laden "
            + "(Tageskontingent aufgebraucht). Bitte die Erweiterung nutzen oder morgen erneut versuchen."
          : "Zu diesem Link kam kein Fahrzeug zurück — bitte erneut versuchen.");
      }
      const t1 = Date.now();
      setResult({ ...data, ms: t1 - t0, link: ziel });
      if (vertragOeffnen && aktuell() && data.vehicle_id && !data.fahrzeug_geloescht) setShowContract(true);
      // Runde 22 (11.09.2026): Filter der aktiven Portale gleich mit oeffnen.
      // Nur hier (echter Vergleichslauf), nie beim Wiederherstellen aus der
      // sessionStorage. Benannte Fenster -> derselbe Tab wird wiederverwendet;
      // blockt der Browser (Klick-Erlaubnis abgelaufen), erklaert ein Hinweis
      // mit Knopf den Rest. Schalter erst JETZT lesen (schalterRef) und nur,
      // solange die Vergleichsseite noch offen ist (aktivRef).
      const schalter = schalterRef.current;
      if (aktivRef.current && schalter.auto && !ohneFilter) {
        const eintraege = filterEintraege(data, { mobile: schalter.mobile, autoscout: schalter.autoscout });
        if (eintraege.length > 0) filterOeffnen(eintraege, { automatisch: true });
      }
      // Runde 11: Firmenregeln, die der AutoScout-Link nicht umsetzt (z.B.
      // Land CH, Hubraum, Navi) — vorher sahen beide Links "gleich" aus.
      // Runde 16: Fahrzeug gehoert einem Kollegen -> Ergebnis ja, Vertrag nein
      // Runde 24 (11.09.2026): feste id je Text — ein weiterer Lauf ersetzt
      // denselben Hinweis, statt ihn ein zweites Mal darunter zu setzen.
      hinweisIdsRef.current = hinweiseZeigen(toast, data.hinweise, hinweisIdsRef.current);
      try {
        const pfad = liveZaehlerPfad(data);                 // RP-207
        if (pfad) {
          const { data: cnt } = await api.get(pfad);
          if (aktuell()) setCounter(cnt);
        }
      } catch (_) { /* ignore */ }
    } catch (err) {
      // RP-254: Ein abgebrochener oder überholter Lauf fasst nichts mehr an —
      // weder Hinweise noch Meldungen (die Abbruch-Meldung kam beim Klick).
      if (istAbbruch(err) || !aktuell()) return;
      // Runde 24: das alte Ergebnis ist schon weg — seine Hinweise auch.
      hinweisIdsRef.current = hinweiseZeigen(toast, [], hinweisIdsRef.current);
      if (err?.code === "timeout" || err?.code === "ECONNABORTED" || err?.code === "ETIMEDOUT") {
        toast.info(TIMEOUT_MESSAGE);
      } else if (err?.response?.status === 402) {
        // H2/M11: Abo abgelaufen — Kontext neu laden (die Routensperre greift
        // dann) und den Weg zur Abo-Seite zeigen statt drei Worten.
        refresh?.();
        toast.error("Für den Vergleich brauchst du ein aktives persönliches Sucher-Abo.", {
          duration: 12000, action: { label: "Zum Abo", onClick: () => nav("/abo") },
        });
      } else {
        toast.error(errMsg(err, "Vergleich fehlgeschlagen"));
      }
    } finally {
      // RP-254: nur den EIGENEN Lauf aufräumen. Nach "X" (abbruchRef = null)
      // oder einem neuen Lauf gehört der Zustand schon jemand anderem.
      if (abbruchRef.current === steuerung) {
        laeuftRef.current = false;
        abbruchRef.current = null;
        jobRef.current = null;
        setLoading(false);
        setWaitMsg(null);
      }
    }
  };

  // Programme zum Herunterladen (03.10.2026): Das Windows-Programm schickt den Sucher mit
  // ?url=<Inserat> hierher. Der Server hat das Inserat beim Anklicken schon im Hintergrund
  // ausgelesen — der Vergleich steht sofort mit Fotos da, der Kaufvertrag geht ohne Link-Einfügen.
  // Die Filter (mobile.de/AutoScout24) hat das Programm schon geöffnet: hier nur auslesen, den
  // Kaufvertrag startet der Sucher selbst (Wunsch Ahmad 03.10.2026).
  const adresseGestartet = useRef(false);
  useEffect(() => {
    if (adresseGestartet.current) return;
    // ?protokoll=web+autoschnell:vertrag?url=… (App aus dem Browser-Helfer gestartet) wird zu ?url=…&vertrag=1
    const suche = protokollSuche(window.location.search);
    const param = suche.get("url");
    if (!param) return;
    adresseGestartet.current = true;
    // Browser-Helfer (04.10.2026): "&vertrag=1" = Kaufvertrag gleich öffnen (vor dem nav lesen — der leert die Adresse)
    const vertrag = suche.get("vertrag") === "1";
    // Pruefbericht 03.10.2026 (Nr. 12): dem Programm melden, dass die App das Auto uebernommen hat
    const start = startKennung(window.location.href);
    if (start) startMelden(api, start);
    nav("/app/vergleich", { replace: true });
    const link = inseratsLinkAusText(param);
    if (!link) {
      toast.error("In der Adresse steht kein gültiger Inserats-Link.");
      return;
    }
    setUrl(link);
    startCompare(null, link, { ohneFilter: true, vertragOeffnen: vertrag });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Wunsch Ahmad 03.10.2026: Die installierte App ist schon offen und das Programm schickt ein neues Auto
  // (lib/programmStart). Ein laufender Lauf wird still abgebrochen, dann liest die Seite das neue Auto aus — wie
  // beim Öffnen über ?url= ohne die Filter ein zweites Mal.
  const [nachLink, setNachLink] = useState(null);
  useEffect(() => {
    const uebernehmen = (e) => {
      // detail: der Link (Programm) oder { link, vertrag } (Browser-Helfer "Kaufvertrag", 04.10.2026)
      const d = e?.detail;
      const link = inseratsLinkAusText((typeof d === "string" ? d : d?.link) || "");
      if (!link) return;
      if (laeuftRef.current) abbrechenRef.current?.({ still: true });
      nav("/app/vergleich", { replace: true });
      setNachLink({ link, vertrag: typeof d === "object" && d?.vertrag === true });
    };
    window.addEventListener(INSERAT_EREIGNIS, uebernehmen);
    return () => window.removeEventListener(INSERAT_EREIGNIS, uebernehmen);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!nachLink || loading || laeuftRef.current) return;
    setNachLink(null);
    setUrl(nachLink.link);
    startCompare(null, nachLink.link, { ohneFilter: true, vertragOeffnen: nachLink.vertrag });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nachLink, loading]);

  // RP-207: Zähler über den Inserats-Schlüssel (siehe liveZaehlerPfad)
  const zaehlerPfad = liveZaehlerPfad(result);
  useEffect(() => {
    if (!zaehlerPfad) return undefined;
    const t = setInterval(async () => {
      try {
        const { data } = await api.get(zaehlerPfad);
        setCounter(data);
      } catch (_) { /* ignore */ }
    }, 30000);
    return () => clearInterval(t);
  }, [zaehlerPfad]);

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-[1480px] mx-auto" data-testid="vergleich-page">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="overline">Vergleich · Hauptansicht</div>
          <h1 className="font-display font-black text-3xl lg:text-5xl tracking-tighter mt-2">
            URL einfügen. <span style={{ color: "var(--accent-red)" }}>Vergleich starten.</span>
          </h1>
          <p className="mt-3 max-w-2xl" style={{ color: "var(--text-secondary)" }}>
            Kleinanzeigen-, mobile.de- oder AutoScout24-Link einfügen — Daten laden, Regeln anwenden, mobile.de &amp; AutoScout24 mit fertigem Filter öffnen.
          </p>
        </div>
        {/* Profilwechsel: die Portal-Links werden serverseitig aus dem
            aktiven Regelwerk gebaut — deshalb den Vergleich neu laufen
            lassen, statt nur das Badge umzuschalten (die alten Links
            truegen sonst die Filter des vorherigen Profils). */}
        <ProfileBadge onChange={(p) => {
          // Rollenprüfung 22.09.2026 (RP-123): das neue Profil auch im
          // gemeinsamen Anmeldezustand — sonst zeigte z. B. die manuelle
          // Suche bis zum Neuladen weiter das alte Profil an.
          setDealer?.((d) => (d ? { ...d, active_profile: p } : d));
          if (result && url.trim() && !loading) {
            startCompare(null, url, { behalteVertrag: true });
          } else {
            setResult((r) => r ? { ...r, active_profile: p } : r);
          }
        }} />
      </div>

      <Suchleiste url={url} setUrl={setUrl} loading={loading} laeuftRef={laeuftRef} result={result}
                  schalter={schalterZustand} onSubmit={startCompare} onAbbrechen={abbrechen}
                  vielleichtStarten={vielleichtStarten} ausZwischenablage={ausZwischenablage} />

      {/* Loading skeleton */}
      {waitMsg && loading && (
        <div className="mt-4 rounded-xl border px-4 py-3 text-sm flex items-center gap-2"
             style={{ borderColor: "var(--border-default)", color: "var(--text-muted)" }}
             data-testid="linkcheck-wait">
          <Loader2 size={15} className="animate-spin shrink-0" />
          {waitMsg}
        </div>
      )}

      {loading && !result && (
        <div className="mt-10 grid lg:grid-cols-12 gap-5">
          <div className="lg:col-span-8 space-y-5">
            <div className="apple-surface p-6 animate-pulse">
              <div className="h-4 w-24 rounded mb-3" style={{ background: "var(--apple-btn-secondary-bg)" }} />
              <div className="h-8 w-2/3 rounded mb-2" style={{ background: "var(--apple-btn-secondary-bg)" }} />
              <div className="h-4 w-1/2 rounded" style={{ background: "var(--apple-btn-secondary-bg)" }} />
              <div className="grid grid-cols-3 gap-3 mt-6">
                {[...Array(6)].map((_, i) => (
                  <div key={i} className="h-14 rounded-lg" style={{ background: "var(--apple-btn-secondary-bg)" }} />
                ))}
              </div>
            </div>
          </div>
          <div className="lg:col-span-4 space-y-5">
            <div className="apple-surface p-5 h-32 animate-pulse" />
            <div className="apple-surface p-5 h-40 animate-pulse" />
          </div>
        </div>
      )}

      {/* RESULT */}
      {result?.vehicle && (
        <div className="mt-10 grid lg:grid-cols-12 gap-5">
          <FahrzeugSpalte result={result} url={url} setResult={setResult} />

          <AktionenSpalte result={result} counter={counter} contract={contract} loading={loading}
                          onNeuVergleichen={() => startCompare(null, url, { behalteVertrag: true })}
                          onVertragErstellen={() => setShowContract(true)}
                          onVersenden={() => setShowSend(true)} />
        </div>
      )}

      {showContract && result?.vehicle && (
        <ContractDialog
          open={showContract}
          onClose={() => setShowContract(false)}
          vehicle={result.vehicle}
          vehicleId={result.vehicle_id}
          onCreated={(c) => {
            setContract(c);
            toast.success(vertragErstelltMeldung(c));
            setShowContract(false);
            setShowSend(true);
          }}
        />
      )}

      {showSend && contract && (
        <SendDialog
          open={showSend}
          contract={contract}
          onClose={() => setShowSend(false)}
        />
      )}
    </div>
  );
}
