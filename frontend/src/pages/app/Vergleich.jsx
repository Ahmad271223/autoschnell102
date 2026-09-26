import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import MarktdatenKarte from "@/components/MarktdatenKarte";
import { lokalerSpeicher, sitzungsSpeicher } from "@/lib/speicher";
import { thumbSrc } from "@/lib/bilder";
import {
  abbruchFehler, checkLink, istAbbruch, postWithRetry503, TIMEOUT_MESSAGE,
} from "@/lib/linkCheck";
import { inseratsLinkAusText, zwischenablageLesen } from "@/lib/inseratsLink";
import { extensionReady, fetchViaExtension } from "@/lib/clientFetch";
import { toast } from "sonner";
import {
  ArrowRight, ExternalLink, Activity, Gauge, Calendar as CalendarIcon, Fuel,
  Cog, Hash, FileText, Send, Loader2, MapPin, Sparkles, Eye, Image as ImageIcon,
  X as XIcon,
} from "lucide-react";
import ContractDialog from "@/components/ContractDialog";
import SendDialog from "@/components/SendDialog";
import BeweisCard from "@/components/BeweisCard";
import ProfileBadge from "@/components/ProfileBadge";
import PortalBadge from "@/components/PortalBadge";
import { openContractPdf } from "@/lib/pdf";
import { filterOeffnen, FILTER_TOAST_ID } from "@/lib/filterOeffnen";
import { fensterDanebenSetzen, zweitenBildschirmAnfragen } from "@/lib/popup";
import { hinweiseZeigen } from "@/lib/hinweise";
import { useAuth } from "@/context/AuthContext";
import {
  einstellungLesen, einstellungSchreiben, vergleichEntfernen, vergleichLaden, vergleichSichern,
} from "@/lib/vergleichSpeicher";


// Pruefbericht 20.09.2026 (A-03/V5): Stand der Inseratsdaten zeigen — aus dem
// gemeinsamen Zwischenspeicher koennen Preis und km bis zu 14 Tage alt sein.
// Aelter als 24 Stunden wird hervorgehoben.
export function datenStand(result, jetzt = Date.now()) {
  const roh = result?.abgerufen_am;
  if (!roh) return null;
  const t = new Date(roh).getTime();
  if (!Number.isFinite(t)) return null;
  const stunden = (jetzt - t) / 3600000;
  const datum = new Date(t).toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
  if (stunden < 1) return { text: "Daten eben abgerufen", alt: false };
  return { text: `Daten vom ${datum}${stunden >= 24 ? " — Preis/km ggf. veraltet" : ""}`, alt: stunden >= 24 };
}

// Rollenprüfung 22.09.2026 (RP-207/RP-358): Der LIVE-Zähler zählt je
// Inserats-Schlüssel (cache_key = "quelle:id"). Gefragt wurde mit ad_id —
// bei AutoScout24 ist das die Anzeigen-Nummer des Anbieters (uniqueRef), die
// von der ID in der Adresse abweichen kann; der Zähler stand dann immer auf 0.
// Jetzt zählt der Schlüssel, unter dem der Vergleich gespeichert wurde.
export function liveZaehlerPfad(r) {
  const key = r?.cache_key || "";
  const i = key.indexOf(":");
  if (i > 0 && i < key.length - 1) {
    return `/mobile/live-counter/${encodeURIComponent(key.slice(i + 1))}`
      + `?quelle=${encodeURIComponent(key.slice(0, i))}`;
  }
  if (!r?.ad_id) return null;
  return `/mobile/live-counter/${encodeURIComponent(r.ad_id)}?quelle=${encodeURIComponent(r.source || "")}`;
}

// Runde 22 (11.09.2026): Eintraege fuer filterOeffnen aus den Ergebnisdaten
// und den Portal-Toggles — ein Ort fuer "Filter öffnen", die Einzel-Knoepfe
// und das automatische Oeffnen nach dem Auslesen.
function filterEintraege(data, { mobile = true, autoscout = true } = {}) {
  return [
    mobile    && data?.search_url    && { url: data.search_url,    name: "mobileFilterWindow",    label: "mobile.de" },
    autoscout && data?.autoscout_url && { url: data.autoscout_url, name: "autoscoutFilterWindow", label: "AutoScout24" },
  ].filter(Boolean);
}

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
  const [pdfLaeuft, setPdfLaeuft] = useState(false);

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
  // Portal-Toggles — Zustand wird in localStorage gespeichert
  const [portalMobile, setPortalMobile] = useState(() => {
    return einstellungLesen(lokalerSpeicher(),
                            "ah_portal_mobile", user?.id, true);
  });
  const [portalAutoscout, setPortalAutoscout] = useState(() => {
    return einstellungLesen(lokalerSpeicher(),
                            "ah_portal_autoscout", user?.id, true);
  });

  // Runde 22 (11.09.2026): Filter nach dem Auslesen automatisch oeffnen —
  // Standard AN (Wunsch Ahmad: Einfuegen genuegt, alles geht von selbst auf).
  const [filterAuto, setFilterAuto] = useState(() => {
    return einstellungLesen(lokalerSpeicher(),
                            "ah_filter_automatisch", user?.id, true);
  });
  // Runde 22 (11.09.2026, Gegenpruefung): aktuelle Schalter-Staende fuer das
  // automatische Oeffnen. Ein Lauf kann Minuten dauern — die Werte aus dem
  // Moment des Starts waeren veraltet, wenn der Sucher inzwischen umschaltet.
  const schalterRef = useRef({ mobile: portalMobile, autoscout: portalAutoscout, auto: filterAuto });

  const toggleMobile = (v) => {
    setPortalMobile(v);
    schalterRef.current.mobile = v;
    einstellungSchreiben(lokalerSpeicher(), "ah_portal_mobile", kontoId, v);
  };
  const toggleAutoscout = (v) => {
    setPortalAutoscout(v);
    schalterRef.current.autoscout = v;
    einstellungSchreiben(lokalerSpeicher(), "ah_portal_autoscout", kontoId, v);
  };
  const toggleFilterAuto = (v) => {
    setFilterAuto(v);
    schalterRef.current.auto = v;
    einstellungSchreiben(lokalerSpeicher(), "ah_filter_automatisch", kontoId, v);
  };
  // 15.09.2026 (Wunsch Ahmad): Filter-Fenster neben der App bzw. auf dem
  // zweiten Bildschirm statt ueber der Seite. Die Bildschirm-Berechtigung
  // fragt der Browser beim Einschalten (Klick) ab; ist sie schon erteilt,
  // reicht das stille Nachfragen beim Laden.
  const [fensterDaneben, setFensterDaneben] = useState(() => {
    return einstellungLesen(lokalerSpeicher(),
                            "ah_fenster_daneben", user?.id, false);
  });
  useEffect(() => {
    fensterDanebenSetzen(fensterDaneben);
    if (fensterDaneben) zweitenBildschirmAnfragen().catch(() => {});
  }, [fensterDaneben]);
  const toggleFensterDaneben = async (v) => {
    setFensterDaneben(v);
    einstellungSchreiben(lokalerSpeicher(), "ah_fenster_daneben", kontoId, v);
    fensterDanebenSetzen(v);
    if (!v) return;
    const r = await zweitenBildschirmAnfragen();
    if (r.ok && r.anzahl > 1) toast.success("Zweiter Bildschirm erkannt — die Filter öffnen dort.");
    else if (r.ok) toast.info("Nur ein Bildschirm erkannt — die Filter öffnen neben der App, wenn Platz ist.");
    else toast.info("Ohne Bildschirm-Berechtigung öffnen die Filter neben dem App-Fenster (gleicher Bildschirm).");
  };

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

  const abbrechen = () => {
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
    toast.info("Abgebrochen — du kannst sofort einen neuen Link einfügen.");
  };

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

  const startCompare = async (e, direktUrl, { behalteVertrag = false } = {}) => {
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
      setResult({ ...data, ms: t1 - t0 });
      // Runde 22 (11.09.2026): Filter der aktiven Portale gleich mit oeffnen.
      // Nur hier (echter Vergleichslauf), nie beim Wiederherstellen aus der
      // sessionStorage. Benannte Fenster -> derselbe Tab wird wiederverwendet;
      // blockt der Browser (Klick-Erlaubnis abgelaufen), erklaert ein Hinweis
      // mit Knopf den Rest. Schalter erst JETZT lesen (schalterRef) und nur,
      // solange die Vergleichsseite noch offen ist (aktivRef).
      const schalter = schalterRef.current;
      if (aktivRef.current && schalter.auto) {
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

      {/* Search bar */}
      <form onSubmit={startCompare} className="mt-8">
        {/* Zeile 1: URL-Input + Buttons */}
        <div className="apple-surface !rounded-2xl !p-1.5 max-w-4xl flex items-stretch gap-1.5 flex-wrap">
          {/* URL-Input */}
          <div className="flex-1 min-w-0 flex items-center pl-4" style={{ minWidth: 200 }}>
            <Sparkles size={15} className="text-[var(--accent-red)] shrink-0 mr-2.5" />
            <input
              data-testid="vergleich-url-input"
              required
              value={url}
              onChange={(e) => {
                const vorher = url;
                setUrl(e.target.value);
                vielleichtStarten(e.target.value, vorher);
              }}
              onClick={ausZwischenablage}
              onPaste={(e) => {
                // Einfuegen genuegt: erkennt der Text einen gueltigen
                // Inserats-Link (Kleinanzeigen ODER mobile.de), startet das
                // Auslesen sofort — der Knopf bleibt fuers manuelle
                // Wiederholen. Nur echte Inserats-URLs, keine Suchseiten.
                const text = (e.clipboardData?.getData("text") || "").trim();
                // RP-409: aus "Schau mal: https://…" nur den Link übernehmen
                const link = inseratsLinkAusText(text);
                if (link && (loading || laeuftRef.current)) {
                  // U-15: während eines Laufs nicht einfügen (das Ergebnis
                  // gehörte sonst zum falschen Link), sondern hinweisen.
                  e.preventDefault();
                  toast.info(VERGLEICH_LAEUFT_HINWEIS, { id: "vergleich-laeuft" });
                } else if (link) {
                  e.preventDefault();
                  setUrl(link);
                  vielleichtStarten(link);
                }
              }}
              placeholder="Ins Feld klicken — kopierter Link wird eingefügt (Kleinanzeigen, mobile.de, AutoScout24)"
              className="flex-1 bg-transparent py-3 text-base font-mono outline-none truncate"
              style={{ color: "var(--text-primary)" }}
              autoFocus
            />
            {url && (
              <button
                type="button"
                onClick={() => setUrl("")}
                data-testid="vergleich-url-clear"
                title="URL löschen"
                className="tipp mr-0.5 flex items-center justify-center rounded-md hover:bg-white/5 text-zinc-400 hover:text-white shrink-0"
              >
                <XIcon size={16} />
              </button>
            )}
          </div>

          {/* Trennlinie */}
          <div className="w-px self-stretch my-1" style={{ background: "var(--divider)" }} />

          {/* Auslesen */}
          <button
            data-testid="vergleich-start-btn"
            type="submit"
            disabled={loading}
            className="apple-btn apple-btn-primary !px-5 !py-2.5 disabled:opacity-60 disabled:cursor-not-allowed shrink-0"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
            <span>{loading ? "Lade…" : "Auslesen"}</span>
          </button>

          {/* Wunsch Ahmad 18.09.2026: Abbrechen, wenn es zu lange dauert. */}
          {loading && (
            <button
              type="button"
              onClick={abbrechen}
              data-testid="vergleich-abbrechen-btn"
              title="Abruf abbrechen"
              className="apple-btn apple-btn-secondary !px-3 !py-2.5 shrink-0"
            >
              <XIcon size={15} />
              <span className="hidden sm:inline">Abbrechen</span>
            </button>
          )}

          {/* Trennlinie */}
          <div className="w-px self-stretch my-1" style={{ background: "var(--divider)" }} />

          {/* Portal-Toggles — PortalBadge sorgt für konsistenten Look in allen Dialogen */}
          <button
            type="button"
            data-testid="toggle-mobile"
            onClick={() => toggleMobile(!portalMobile)}
            title={portalMobile ? "mobile.de aktiv — klicken zum Deaktivieren" : "mobile.de aktivieren"}
            aria-label="mobile.de ein-/ausschalten"
            aria-pressed={portalMobile}
            className="shrink-0 p-1.5 rounded-xl bg-transparent border-0 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-red)]"
          >
            <PortalBadge kind="mobile" active={portalMobile} size="md" />
          </button>

          <button
            type="button"
            data-testid="toggle-autoscout"
            onClick={() => toggleAutoscout(!portalAutoscout)}
            title={portalAutoscout ? "AutoScout24 aktiv — klicken zum Deaktivieren" : "AutoScout24 aktivieren"}
            aria-label="AutoScout24 ein-/ausschalten"
            aria-pressed={portalAutoscout}
            className="shrink-0 p-1.5 rounded-xl bg-transparent border-0 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-red)]"
          >
            <PortalBadge kind="autoscout" active={portalAutoscout} size="md" />
          </button>

          {/* Filter öffnen */}
          <button
            type="button"
            data-testid="open-filter-btn"
            disabled={!result
              || filterEintraege(result, { mobile: portalMobile, autoscout: portalAutoscout }).length === 0}
            onClick={() => {
              // Runde 22: je Klick laesst der Browser nur EIN Fenster zu — den
              // Rest holt der Hinweis-Knopf nach (oder Pop-ups erlauben).
              filterOeffnen(filterEintraege(result, { mobile: portalMobile, autoscout: portalAutoscout }));
            }}
            className="shrink-0 apple-btn apple-btn-secondary !px-4 !py-2.5 disabled:opacity-40 disabled:cursor-not-allowed"
            title={result ? "Filter der aktiven Portale öffnen" : "Erst Vergleich auslesen"}
          >
            <Eye size={14} />
            <span>Filter öffnen</span>
            <ExternalLink size={11} />
          </button>
        </div>

        <div className="mt-3 text-xs flex flex-wrap gap-2 items-center" style={{ color: "var(--text-muted)" }}>
          {/* Runde 22 (11.09.2026): Filter nach dem Auslesen automatisch oeffnen */}
          <label
            className="inline-flex items-center gap-2 sm:ml-3 cursor-pointer select-none min-h-[40px]"
            title="Nach dem Auslesen die Filter der aktiven Portale (mobile.de / AutoScout24) automatisch öffnen"
          >
            <input
              type="checkbox"
              data-testid="toggle-filter-auto"
              checked={filterAuto}
              onChange={(e) => toggleFilterAuto(e.target.checked)}
              style={{ accentColor: "var(--accent-red)" }}
            />
            <span style={{ color: "var(--text-secondary)" }}>Filter nach dem Auslesen automatisch öffnen</span>
          </label>
          {/* 15.09.2026 (Wunsch Ahmad): daneben statt darueber — zweiter Bildschirm */}
          <label
            className="inline-flex items-center gap-2 sm:ml-3 cursor-pointer select-none min-h-[40px]"
            title="Filter-Fenster neben der App öffnen — auf dem zweiten Bildschirm, wenn der Browser es erlaubt"
          >
            <input
              type="checkbox"
              data-testid="toggle-fenster-daneben"
              checked={fensterDaneben}
              onChange={(e) => toggleFensterDaneben(e.target.checked)}
              style={{ accentColor: "var(--accent-red)" }}
            />
            <span style={{ color: "var(--text-secondary)" }}>Filter daneben öffnen (zweiter Bildschirm)</span>
          </label>
        </div>
      </form>

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
          {/* Left — vehicle */}
          <div className="lg:col-span-8 space-y-5">
            <div className="apple-surface p-6">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div className="min-w-0">
                  <div className="overline">Fahrzeug erkannt</div>
                  <h2 className="font-display font-bold text-2xl lg:text-3xl tracking-tight mt-1" data-testid="vehicle-title">
                    {result.vehicle.make_label} {result.vehicle.model_label}
                  </h2>
                  <div className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
                    {result.vehicle.model_description}
                  </div>
                  {(result.vehicle.seller_zip || result.vehicle.seller_city || result.vehicle.location) && (
                    <div className="text-xs mt-2 inline-flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
                      <MapPin size={11} className="text-[var(--accent-red)]" />
                      Standort: {result.vehicle.location || [result.vehicle.seller_zip, result.vehicle.seller_city].filter(Boolean).join(" ")}
                    </div>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <div className="font-display font-black text-3xl">
                    {result.vehicle.list_price ? `${result.vehicle.list_price.toLocaleString("de-DE")} €` : "—"}
                  </div>
                  <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>Listenpreis · nicht im Vertrag</div>
                  {/* Prüfbericht 20.09.2026 (S-09): Preisart (VB) und MwSt-Ausweis aus dem Inserat */}
                  {(result.vehicle.price_negotiable || result.vehicle.mwst_ausweisbar === true) && (
                    <div className="text-[11px] font-semibold" data-testid="vergleich-preisart"
                         style={{ color: "var(--text-secondary)" }}>
                      {[result.vehicle.price_negotiable ? "VB (Verhandlungsbasis)" : null,
                        result.vehicle.mwst_ausweisbar === true ? "MwSt. ausweisbar" : null]
                        .filter(Boolean).join(" · ")}
                    </div>
                  )}
                  {datenStand(result) && (
                    <div className={`text-[11px] ${datenStand(result).alt ? "font-semibold" : ""}`}
                         data-testid="vergleich-datenstand"
                         style={{ color: datenStand(result).alt ? "var(--tx-amber)" : "var(--text-muted)" }}>
                      {datenStand(result).text}
                    </div>
                  )}
                </div>
              </div>

              <div className="mt-6 grid grid-cols-2 md:grid-cols-3 gap-3">
                <Stat icon={CalendarIcon} label="Erstzulassung" value={result.vehicle.first_registration} />
                <Stat icon={Gauge} label="Kilometer" value={result.vehicle.mileage ? `${result.vehicle.mileage.toLocaleString("de-DE")} km` : "—"} />
                <Stat icon={Activity} label="Leistung" value={result.vehicle.power_kw ? `${result.vehicle.power_kw} kW · ${result.vehicle.power_ps} PS` : "—"} />
                <Stat icon={Fuel} label="Kraftstoff" value={result.vehicle.fuel_label} />
                <Stat icon={Cog} label="Getriebe" value={result.vehicle.gearbox_label} />
                <Stat icon={Hash} label="Hubraum" value={result.vehicle.displacement ? `${result.vehicle.displacement} ccm` : "—"} />
              </div>

              {result.vehicle.images?.length > 0 && (
                <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
                  <div className="overline mb-3 flex items-center gap-1.5">
                    <ImageIcon size={11} /> Fotos vom Inserat ({result.vehicle.images.length})
                  </div>
                  <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-2" data-testid="kleinanzeigen-gallery">
                    {/* Prüfbericht 20.09. U-14: Index im Schlüssel — doppelte
                        Bildadressen ergaben doppelte React-Schlüssel. */}
                    {result.vehicle.images.slice(0, 10).map((src, idx) => (
                      <a key={`${idx}-${src}`} href={src} target="_blank" rel="noopener noreferrer"
                         className="block aspect-[4/3] rounded-lg overflow-hidden border hover:opacity-80 transition"
                         style={{ borderColor: "var(--hairline)" }}
                         data-testid={`gallery-thumb-${idx}`}>
                        {/* 10.09.2026: Vorschaubild ueber den eigenen Bild-Proxy (klein,
                            zwischengespeichert); schlaegt es fehl, das Portalbild direkt. */}
                        <img src={thumbSrc(result.vehicle.images_thumbs?.[idx], src)} alt="" loading="lazy"
                             referrerPolicy="no-referrer" className="w-full h-full object-cover"
                             onError={(e) => { if (e.currentTarget.src !== src) e.currentTarget.src = src; }} />
                      </a>
                    ))}
                    {result.vehicle.images.length > 10 && (
                      <div className="aspect-[4/3] rounded-lg flex items-center justify-center text-xs font-semibold"
                           style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-secondary)" }}>
                        +{result.vehicle.images.length - 10} weitere
                      </div>
                    )}
                  </div>
                </div>
              )}

              {result.vehicle.features?.length > 0 && (
                <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
                  <div className="overline mb-3">Ausstattung ({result.vehicle.features.length})</div>
                  <div className="flex flex-wrap gap-1.5">
                    {result.vehicle.features.map((f, i) => (
                      <span key={`${i}-${f}`}
                            className="text-[11px] px-2.5 py-1 rounded-full"
                            style={{
                              background: "var(--apple-btn-secondary-bg)",
                              border: "1px solid var(--apple-btn-secondary-border)",
                              color: "var(--text-secondary)",
                            }}>
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {result.vehicle.description && (
                <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
                  <div className="overline mb-3">Beschreibung</div>
                  <p className="text-sm leading-relaxed whitespace-pre-line" data-testid="vehicle-description"
                     style={{ color: "var(--text-secondary)" }}>
                    {result.vehicle.description}
                  </p>
                </div>
              )}
            </div>

            {/* RP-439/RP-419: ohne erkannte Marke gibt es keinen mobile.de-Link
                (er hätte über alle Marken gesucht) — der Grund steht im Hinweis. */}
            {result.search_url && (
            <div className="apple-surface p-6">
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="flex items-start gap-3">
                  <PortalBadge kind="mobile" size="sm" />
                  <div>
                    <div className="overline">Mobile.de Filter</div>
                    <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>Generierter Such-Link auf Basis deiner Vergleichsregeln</div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => filterOeffnen(filterEintraege(result, { autoscout: false }))}
                  data-testid="open-mobile-btn"
                  className="apple-btn apple-btn-primary"
                >
                  <Eye size={14} /> Öffnen <ExternalLink size={12} />
                </button>
              </div>
            </div>
            )}

            {result.autoscout_url && (
              <div className="apple-surface p-6">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="flex items-start gap-3">
                    <PortalBadge kind="autoscout" size="sm" />
                    <div>
                      <div className="overline">AutoScout24 Filter</div>
                      <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                        Gleicher Filter, zweite Plattform — doppelte Reichweite
                      </div>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => filterOeffnen(filterEintraege(result, { mobile: false }))}
                    data-testid="open-autoscout-btn"
                    className="apple-btn apple-btn-secondary"
                  >
                    <Eye size={14} /> Öffnen <ExternalLink size={12} />
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Right — actions */}
          <div className="lg:col-span-4 space-y-5">
            <div className="apple-surface p-5" data-testid="live-counter-card">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  {/* M8/U-07: den Live-Punkt nur mit echtem Zaehlerstand */}
                  {counter && <span className="live-dot" />}
                  <span className="overline">{counter ? "live" : "Zähler nicht verfügbar"}</span>
                </div>
                <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>aktualisiert alle 30s</span>
              </div>
              <div className="font-display font-black text-4xl mt-3 tracking-tight">
                {counter ? (counter.active_now ?? 0) : "—"}
              </div>
              {/* Runde 27: Gezaehlt werden VERGLEICHE, nicht Haendler — ein
                  Sucher kann mehrfach vergleichen. Und das Fenster steht dabei. */}
              <div className="text-sm mt-0.5 font-medium" style={{ color: "var(--text-primary)" }}>
                {counter?.active_now
                  ? `${counter.active_now === 1 ? "Vergleich" : "Vergleiche"} in den letzten ${counter?.fenster_minuten ?? 10} Minuten`
                  : `Keine Vergleiche in den letzten ${counter?.fenster_minuten ?? 10} Minuten`}
              </div>
              <div className="text-[11px] mt-3 pt-3 border-t" style={{ color: "var(--text-muted)", borderColor: "var(--hairline)" }}>
                Heute insg.: <span className="font-semibold" style={{ color: "var(--text-primary)" }}>{counter ? (counter.today ?? 0) : "—"}</span> Vergleiche
              </div>
            </div>

            <div className="apple-surface p-5">
              <div className="overline mb-3">Aktionen</div>
              {/* Rollenprüfung 22.09.2026 (RP-048/RP-147): Den Hinweis bekommt
                  nur noch der Chef (Sucher erfahren seit Runde 29 keine
                  Kollegen) — deshalb in seiner Sicht formuliert. */}
              {result.kollege && (
                <div className="text-sm rounded-xl p-3 mb-3" data-testid="kollege-hinweis"
                     style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
                  Dieses Fahrzeug bearbeitet bereits <b>{result.kollege.name}</b>.
                  Legst du selbst einen Kaufvertrag an, bekommt er einen eigenen
                  Abholtermin — der Vorgang von {result.kollege.name} bleibt unberührt.
                </div>
              )}
              {/* RP-210/RP-361: in der Firma gelöschtes Fahrzeug — kein neuer
                  Vertrag (vorher endete der Klick mit 404). */}
              {result.fahrzeug_geloescht ? (
                <div className="text-sm rounded-xl p-3" data-testid="fahrzeug-geloescht-hinweis"
                     style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
                  Dieses Fahrzeug wurde in deiner Firma gelöscht. Ein neuer
                  Kaufvertrag ist dafür nicht möglich — die Vergleichslinks
                  funktionieren trotzdem.
                </div>
              ) : result.fahrzeug_weg ? (
                <div className="space-y-2" data-testid="fahrzeug-weg-hinweis">
                  <div className="text-sm rounded-xl p-3"
                       style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
                    Dieses Fahrzeug ist nicht mehr in deinem Fahrzeugpool (ältere
                    Vergleiche werden aussortiert). Bitte den Link neu vergleichen —
                    das kostet nichts.
                  </div>
                  <button type="button" data-testid="neu-vergleichen-btn"
                          disabled={loading}
                          onClick={() => startCompare(null, url, { behalteVertrag: true })}
                          className="apple-btn apple-btn-primary w-full !py-3 disabled:opacity-60">
                    <ArrowRight size={15} /> Neu vergleichen
                  </button>
                </div>
              ) : (
                <>
                  {/* Rollenprüfung 22.09.2026 (RP-416): Steht der eben erstellte
                      Vertrag schon da, sagt die Seite das VOR dem Formular —
                      vorher kam die Rückfrage (409 vertrag_vorhanden) erst nach
                      dem Ausfüllen. Verträge aus früheren Sitzungen fängt
                      weiter ContractDialog mit seiner Rückfrage ab. */}
                  {contract && (
                    <div className="text-sm rounded-xl p-3 mb-3" data-testid="vertrag-vorhanden-hinweis"
                         style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
                      Für dieses Fahrzeug hast du schon einen Kaufvertrag erstellt
                      {contract.contract_no ? <> (Nr. <b>{contract.contract_no}</b>)</> : null}.
                      Ein weiterer Vertrag ergibt einen zweiten Kauf mit eigenem
                      Abholtermin — der erste läuft mit seinem Preis weiter.
                    </div>
                  )}
                  <button onClick={() => setShowContract(true)} data-testid="create-contract-btn"
                          className={`apple-btn ${contract ? "apple-btn-secondary" : "apple-btn-primary"} w-full !py-3`}>
                    <FileText size={15} /> {contract ? "Weiteren Kaufvertrag erstellen" : "Kaufvertrag erstellen"}
                  </button>
                </>
              )}
              {contract && (
                <div className="mt-3 space-y-2">
                  <button
                    onClick={async () => {
                      if (pdfLaeuft) return;
                      setPdfLaeuft(true);
                      try { await openContractPdf(contract.id); }
                      catch (err) { toast.error(errMsg(err, "Kaufvertrag konnte nicht geladen werden")); }
                      finally { setPdfLaeuft(false); }
                    }}
                    disabled={pdfLaeuft}
                    data-testid="open-pdf-btn"
                    className="apple-btn apple-btn-secondary w-full disabled:opacity-60"
                  >
                    <FileText size={14} /> {pdfLaeuft ? "Lädt…" : "PDF öffnen"}
                  </button>
                  <button onClick={() => setShowSend(true)} data-testid="send-pdf-btn"
                          className="apple-btn apple-btn-secondary w-full">
                    <Send size={14} /> Versenden
                  </button>
                </div>
              )}
            </div>

            {/* 18.09.2026: Gibt es zum Inserat noch kein Dokument, steht hier
                der Knopf "Beweisdokument erstellen" (frueher entstand es
                automatisch bei jedem Vergleich). */}
            {/* Prüfbericht 20.09. U-11: beweis_moeglich === false (Daten aus der
                Browser-Erweiterung) — die Karte zeigt statt des Knopfs den Hinweis. */}
            {(result.beweis?.id || result.cache_key) && (
              <BeweisCard key={result.beweis?.id || result.cache_key}
                          beweis={result.beweis} cacheKey={result.cache_key}
                          moeglich={result.beweis_moeglich !== false} />
            )}
            {/* Market Intelligence (25.09.2026): laedt NACH dem fertigen Vergleich
                getrennt, kurzes Zeitlimit, verschwindet still ohne Daten. */}
            {result.vehicle_id && (
              <MarktdatenKarte key={`markt-${result.cache_key || result.vehicle_id}`}
                               vehicleId={result.vehicle_id} preis={result.vehicle.list_price} />
            )}

            <div className="text-[11px] leading-relaxed px-1" style={{ color: "var(--text-muted)" }}>
              <strong style={{ color: "var(--text-primary)" }}>Hinweis:</strong> Der Kaufpreis wird nie automatisch übernommen.
              Verhandelten Preis im nächsten Schritt manuell eintragen.
            </div>
          </div>
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

/** Prüfbericht 20.09. U-82: der Server meldet bereits_vorhanden, wenn der
 *  Vertrag schon angelegt war (Doppelklick, zweiter Tab) — dann nicht
 *  "PDF erstellt" behaupten. */
export function vertragErstelltMeldung(c) {
  if (c?.bereits_vorhanden) return "Dieser Vertrag war schon angelegt — es wurde kein neuer erstellt";
  if (c?.appointment_id) return "PDF erstellt – Termin automatisch im Terminplaner angelegt";
  return "PDF erstellt";
}

/** U-15: Hinweis, wenn während eines laufenden Vergleichs ein neuer Link kommt. */
export const VERGLEICH_LAEUFT_HINWEIS =
  "Es läuft noch ein Vergleich – mit dem X abbrechen, dann den neuen Link einfügen.";

function Stat({ icon: Icon, label, value }) {
  return (
    <div className="apple-card p-3">
      <div className="flex items-center gap-1.5 text-[10px] uppercase font-bold tracking-wider"
           style={{ color: "var(--text-muted)" }}>
        <Icon size={11} className="text-[var(--accent-red)]" /> {label}
      </div>
      <div className="text-base font-semibold mt-1.5 truncate">{value || "—"}</div>
    </div>
  );
}
