import "@/App.css";
import { Suspense, lazy, useEffect, useRef, useState } from "react";
import NachladeFehler from "@/components/NachladeFehler";
import SeiteLaedt from "@/components/SeiteLaedt";
import FassungsHinweis from "@/components/FassungsHinweis";
import { nachladenGescheitert } from "@/lib/fassung";
import { hatUngespeichert } from "@/lib/ungespeichert";
import {
  erweiterungZieleVerfolgen, startMelden, startZieleVerfolgen, zielGemerkt, zielUebernehmen, zielVergessen,
} from "@/lib/programmStart";
import StartZielDialog, { zielBeschreibung } from "@/components/StartZielDialog";
import { api } from "@/lib/api";
import { istFirmenHostKandidat } from "@/lib/firmenHost";

// Vite (09/2026): Seiten laden erst bei Bedarf nach — die erste Seite ist
// dadurch deutlich schneller da.
//
// Runde 31 (12.09.2026, Vorfall Fahrer-App/Super-Admin): Die Oberflaeche
// lieferte fehlende Dateien mit "ein Jahr, immutable" aus. Wer im Rollout-
// Fenster einen 404 bekam, dessen Browser hielt ihn fest und fragte den
// Server nie wieder — bei JEDER Anmeldung dieselbe tote Seite, obwohl beide
// Server laengst sauber waren (in Chromium nachgestellt). Deshalb:
//   1. Scheitert das Nachladen, holt fetch(..., {cache: "reload"}) genau
//      diese Datei am Browser-Zwischenspeicher vorbei und ersetzt dort den
//      festgehaltenen Fehler. Dann ein zweiter Versuch.
//   2. Klappt der nicht (Chromium merkt sich den Fehlschlag im laufenden
//      Tab), wird EINMAL neu geladen — jetzt mit erneuertem Speicher.
//      Hoechstens alle 30 Sekunden (Zeitstempel statt Merker: sonst loeste
//      ein erfolgreiches Layout mit fehlender Unterseite eine Endlosschleife
//      aus) und NIE, solange ungespeicherte Eingaben offen sind.
//   3. Sonst zeigt die Fehlergrenze "Neu laden".
const NEU_GELADEN = "ah_seite_neu_geladen_um";
const DATEI_IN_FEHLER = /(https?:[/][/][^ "')]+[.](?:js|css))/i;
// Pruefbericht 20.09.2026 (K-11): laden() wartete ohne Zeitlimit. Hing die
// Verbindung, griffen weder zweiter Versuch noch Neuladen noch Fehlergrenze,
// und "Lade…" stand fuer immer. Ein Versuch gilt jetzt nach 20 s als
// gescheitert (SeiteLaedt bietet ab 10 s selbst "Neu laden" an).
const LADEN_ZEITLIMIT_MS = 20000;

function mitZeitlimit(versprechen, ms = LADEN_ZEITLIMIT_MS) {
  return new Promise((erfuellen, ablehnen) => {
    const t = setTimeout(() => ablehnen(new Error("Zeitüberschreitung beim Laden der Seite")), ms);
    versprechen.then(
      (wert) => { clearTimeout(t); erfuellen(wert); },
      (fehler) => { clearTimeout(t); ablehnen(fehler); },
    );
  });
}

async function zwischenspeicherErneuern(fehler) {
  const treffer = DATEI_IN_FEHLER.exec(String(fehler?.message || ""));
  if (!treffer) return;
  try {
    const url = new URL(treffer[1]);
    if (url.origin !== window.location.origin) return;
    await fetch(url.href, { cache: "reload", credentials: "same-origin" });
  } catch { /* offline — dann hilft nur spaeter ein Neuladen */ }
}

function seite(laden) {
  return lazy(async () => {
    let letzter;
    for (const warten of [0, 700]) {
      if (warten) await new Promise((weiter) => { setTimeout(weiter, warten); });
      try {
        return await mitZeitlimit(laden());
      } catch (fehler) {
        letzter = fehler;
        await zwischenspeicherErneuern(fehler);
      }
    }
    nachladenGescheitert();
    let zuletzt = 0;
    try { zuletzt = Number(sessionStorage.getItem(NEU_GELADEN)) || 0; } catch { /* egal */ }
    if (!hatUngespeichert() && Date.now() - zuletzt > 30000) {
      try { sessionStorage.setItem(NEU_GELADEN, String(Date.now())); } catch { /* egal */ }
      window.location.reload();
      return new Promise(() => {});
    }
    throw letzter;
  });
}

// Angemeldete Nutzer: die Arbeitsseiten im Leerlauf vorladen — der Klick im
// Menue oeffnet sie dann ohne Wartezeit.
const VORLADEN = [
  () => import("@/pages/app/ManuelleSuche"),
  () => import("@/pages/app/PDFArchiv"),
  () => import("@/pages/app/Termine"),
  () => import("@/pages/app/Fahrzeugpool"),
  () => import("@/pages/app/Bestand"),
  () => import("@/pages/app/FahrzeugAkte"),
  () => import("@/pages/app/Inserat"),
  () => import("@/pages/app/Fahrer"),
  () => import("@/pages/app/Team"),
  () => import("@/pages/app/Einstellungen"),
  () => import("@/pages/app/Anfragen"),
  () => import("@/pages/app/Freigaben"),
];
function vorladen() {
  for (const laden of VORLADEN) laden().catch(() => {});
}

// Vorladen, sobald jemand angemeldet ist (auch direkt nach der Anmeldung) —
// nicht fuer den Admin, der die Haendlerseiten nie sieht.
// Wunsch Ahmad 03.10.2026: Das Windows-Programm öffnet den Kaufvertrag in der installierten App. Ist sie schon
// offen, kommt das Ziel hier an (launchQueue) — mit ungespeicherter Arbeit nur als Hinweis mit Knopf.
// Prüfung 09.10.2026 (Befund Ahmad "Kaufvertrag aus dem Programm hängt, wenn in der App schon einer arbeitet"):
// statt des kleinen 20-Sekunden-Hinweises ein Dialog, der stehen bleibt (StartZielDialog); das Ziel bleibt gemerkt
// (zielMerken) und ein Hinweis mit Knopf, bis es geöffnet oder verworfen ist. Gemeldet wird IMMER (der Server nimmt
// die Meldung auch ohne Anmeldung an) — mit Zustand: offen / nachgefragt / anmeldung / abo. Das Programm (ab 1.5.15)
// sagt dem Sucher dann, was in der App zu tun ist, statt nach 10 s zusätzlich den Browser zu öffnen (dessen
// Anmeldung warf die App per Single-Session raus).
function AppStartZiele() {
  const nav = useNavigate();
  const { user, subscription } = useAuth();
  const zustandRef = useRef(() => null);
  zustandRef.current = () => {
    if (!user || user.is_super_admin || user.role === "admin") return "anmeldung";
    if (subscription && !subscription.active) return "abo";
    return null;
  };
  const [frage, setFrage] = useState(null);                  // { ziel, ausfuehren } — Dialog offen
  const [gemerkt, setGemerkt] = useState(() => zielGemerkt());
  useEffect(() => {
    const nachfragen = (ausfuehren, ziel) => { setFrage({ ziel, ausfuehren }); setGemerkt(ziel); };
    const melden = (start, zustand) => startMelden(api, start, zustandRef.current() || zustand);
    startZieleVerfolgen((ziel) => nav(ziel), { beschaeftigt: hatUngespeichert, melden, nachfragen });
    // Browser-Helfer (04.10.2026): "Kaufvertrag" aus der Erweiterung im schon offenen App-Fenster
    return erweiterungZieleVerfolgen((ziel) => nav(ziel), {
      beschaeftigt: hatUngespeichert, nachfragen, zustand: () => zustandRef.current(),
    });
  }, [nav]);

  const hier = () => {
    const f = frage;
    setFrage(null);
    setGemerkt(null);
    f?.ausfuehren?.();
  };
  const neuesFenster = () => {
    const f = frage;
    setFrage(null);
    setGemerkt(null);
    zielVergessen();
    let fenster = null;
    try { fenster = window.open(f?.ziel, "_blank"); } catch { fenster = null; }
    if (!fenster) f?.ausfuehren?.();                   // blockiert: dann doch hier
  };
  const hinweisOeffnen = () => {
    const stand = zielUebernehmen(gemerkt, {
      fenster: window, navigieren: (z) => nav(z), beschaeftigt: hatUngespeichert,
      nachfragen: (ausfuehren, ziel) => setFrage({ ziel, ausfuehren }),
    });
    if (stand === "uebernommen") setGemerkt(null);
  };
  return (
    <>
      <StartZielDialog ziel={frage?.ziel || null} onHier={hier} onNeuesFenster={neuesFenster} onSpaeter={() => setFrage(null)} />
      {gemerkt && !frage && (
        <div className="fixed top-2 left-1/2 -translate-x-1/2 z-[55] max-w-[95vw] rounded-full px-4 py-2 text-sm shadow-lg flex flex-wrap items-center gap-2"
             style={{ background: "var(--bg-elevated)", border: "1px solid var(--border-default)", color: "var(--text-primary)" }}
             data-testid="start-ziel-hinweis">
          <span>Kaufvertrag aus dem Programm wartet{zielBeschreibung(gemerkt, window.location.origin) ? ` (${zielBeschreibung(gemerkt, window.location.origin)})` : ""}</span>
          <button type="button" onClick={hinweisOeffnen} data-testid="start-ziel-hinweis-oeffnen"
                  className="apple-btn apple-btn-primary !rounded-full !px-3 !py-0.5 text-xs">Öffnen</button>
          <button type="button" onClick={() => { zielVergessen(); setGemerkt(null); }} data-testid="start-ziel-hinweis-weg"
                  className="apple-btn apple-btn-secondary !rounded-full !px-3 !py-0.5 text-xs">Verwerfen</button>
        </div>
      )}
    </>
  );
}

function Vorladen() {
  const { user } = useAuth();
  useEffect(() => {
    if (!user || user.role === "admin" || user.is_super_admin) return undefined;
    const leerlauf = window.requestIdleCallback || ((f) => setTimeout(f, 1500));
    const abbrechen = window.cancelIdleCallback || clearTimeout;
    const id = leerlauf(vorladen);
    return () => abbrechen(id);
  }, [user]);
  return null;
}
import { BrowserRouter, Routes, Route, Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Toaster, toast } from "sonner";
import { useTheme } from "@/components/ThemeToggle";

import { AuthProvider, useAuth } from "@/context/AuthContext";
import { DriverAuthProvider } from "@/context/DriverContext";
import { BuyerAuthProvider } from "@/context/BuyerContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { nichtGefundenZiel, startseite } from "@/lib/rollen";
import AppLayout from "@/components/AppLayout";
import FeatureGate from "@/components/FeatureGate";

import Landing from "@/pages/Landing";
import Login from "@/pages/Login";
import AppStart from "@/pages/AppStart";
const Anfrage = seite(() => import("@/pages/Anfrage"));
const PasswortVergessen = seite(() => import("@/pages/PasswortVergessen"));
const Impressum = seite(() => import("@/pages/legal/Impressum"));
const Datenschutz = seite(() => import("@/pages/legal/Datenschutz"));
const AGB = seite(() => import("@/pages/legal/AGB"));
const Subscription = seite(() => import("@/pages/Subscription"));
const AdminLayout = seite(() => import("@/pages/admin_v2/AdminLayout"));
const AdminOverview = seite(() => import("@/pages/admin_v2/Overview"));
const AdminUsers = seite(() => import("@/pages/admin_v2/Users"));
const AdminUserDetail = seite(() => import("@/pages/admin_v2/UserDetail"));
const AdminComparisons = seite(() => import("@/pages/admin_v2/Comparisons"));
const AdminUrlStats = seite(() => import("@/pages/admin_v2/UrlStats"));
const AdminAuditLog = seite(() => import("@/pages/admin_v2/AuditLog"));
const AdminErrors = seite(() => import("@/pages/admin_v2/Errors"));
const AdminFreischaltungen = seite(() => import("@/pages/admin_v2/Freischaltungen"));
const AdminSettings = seite(() => import("@/pages/admin_v2/Settings"));
const AdminAutoDaten = seite(() => import("@/pages/admin_v2/AutoDaten"));
const AdminFahrer = seite(() => import("@/pages/admin_v2/Fahrer"));
const AdminBetrieb = seite(() => import("@/pages/admin_v2/Betrieb"));
const AdminMarkt = seite(() => import("@/pages/admin_v2/Markt"));
const AdminMarktModell = seite(() => import("@/pages/admin_v2/MarktModell"));
const AdminMarktChancen = seite(() => import("@/pages/admin_v2/MarktChancen"));
const AdminMarktAuftraege = seite(() => import("@/pages/admin_v2/MarktAuftraege"));
const AdminMarktPrivateDeals = seite(() => import("@/pages/admin_v2/MarktPrivateDeals"));
const AdminMarktHotDeals = seite(() => import("@/pages/admin_v2/MarktHotDeals"));
const AdminMarktBerichte = seite(() => import("@/pages/admin_v2/MarktBerichte"));
const AdminMarktBericht = seite(() => import("@/pages/admin_v2/MarktBericht"));
const AdminMarktOptimierung = seite(() => import("@/pages/admin_v2/MarktOptimierung"));

import Vergleich from "@/pages/app/Vergleich";
import Vorgang from "@/pages/app/Vorgang";
const ManuelleSuche = seite(() => import("@/pages/app/ManuelleSuche"));
const PDFArchiv = seite(() => import("@/pages/app/PDFArchiv"));
const Termine = seite(() => import("@/pages/app/Termine"));
const Fahrzeugpool = seite(() => import("@/pages/app/Fahrzeugpool"));
const Bestand = seite(() => import("@/pages/app/Bestand"));
const FahrzeugAkte = seite(() => import("@/pages/app/FahrzeugAkte"));
const Inserat = seite(() => import("@/pages/app/Inserat"));
const Fahrer = seite(() => import("@/pages/app/Fahrer"));
const Team = seite(() => import("@/pages/app/Team"));
const Einstellungen = seite(() => import("@/pages/app/Einstellungen"));
// Kundenportal (29.09.2026): Meldungen "Kaufvertrag bestätigt" und die öffentliche Firmenseite
const Meldungen = seite(() => import("@/pages/app/Meldungen"));
const FirmenSeite = seite(() => import("@/pages/firma/FirmenSeite"));
const Anfragen = seite(() => import("@/pages/app/Anfragen"));
const Freigaben = seite(() => import("@/pages/app/Freigaben"));
const Chancen = seite(() => import("@/pages/app/Chancen"));
// Programme zum Herunterladen (03.10.2026): Inhalte nur vom Server, je Firma freigeschaltet
const Programme = seite(() => import("@/pages/app/Programme"));
const AdminProgrammVergleiche = seite(() => import("@/pages/admin_v2/ProgrammVergleiche"));

const DriverLogin = seite(() => import("@/pages/driver/DriverLogin"));
const DriverLayout = seite(() => import("@/pages/driver/DriverLayout"));
const DriverDashboard = seite(() => import("@/pages/driver/DriverDashboard"));
const DriverSettings = seite(() => import("@/pages/driver/DriverSettings"));
const DriverProtokoll = seite(() => import("@/pages/driver/Protokoll"));

const BuyerLogin = seite(() => import("@/pages/markt/BuyerLogin"));
const Marktplatz = seite(() => import("@/pages/markt/Marktplatz"));

const Wrap = ({ children }) => (
  <ProtectedRoute>
    <AppLayout>{children}</AppLayout>
  </ProtectedRoute>
);

// Verkaufs-/Verwaltungsseiten: der Händler-Hauptaccount ist KOSTENLOS —
// nur die Sucher-Funktionen (Vergleich/Suche/Pool) brauchen ein Abo.
const WrapFree = ({ children }) => (
  <ProtectedRoute requireSub={false}>
    <AppLayout>{children}</AppLayout>
  </ProtectedRoute>
);

// Start-Seite nach Login: Sucher landen im Vergleich, der Chef im Bestand
// (der auch ohne Abo funktioniert — sonst würde ein kostenloser Händler
// direkt auf die Abo-Seite umgeleitet).
function AppHome() {
  const { user } = useAuth();
  return <Navigate to={startseite(user)} replace />;
}

// Kontonummer (13.09.2026): Konten legt nur noch der Betreiber an. Alte
// Links landen auf der Anmeldung — MIT Query, damit ein schon verschickter
// Einladungslink (?invite=…) nach der Anmeldung eingeloest wird.
function WeiterleitungMitQuery({ nach }) {
  const { search } = useLocation();
  return <Navigate to={`${nach}${search}`} replace />;
}

// Pruefbericht 20.09.2026 (U-148): Unbekannte Adresse. Vorher <Navigate to="/">:
// ein Angemeldeter mit Tippfehler in der Adresse landete auf der Werbeseite.
// Jetzt eine kurze Meldung mit dem passenden Ausweg (lib/rollen.nichtGefundenZiel).
/**
 * Kundenportal (29.09.2026): Läuft die App unter der Adresse einer Firma (kfz-mueller.auto-schnellkauf.de
 * oder eine eigene Kundendomain per DNS), zeigt sie NUR die Firmenseite. Die Hauptadresse (app.…,
 * localhost, IP) fragt nie nach; eine unbekannte Adresse fällt auf die normalen Seiten zurück.
 */
function HostWeiche({ children }) {
  const [stand, setStand] = useState(() => (
    typeof window !== "undefined" && istFirmenHostKandidat(window.location.hostname) ? "pruefen" : "app"));
  useEffect(() => {
    if (stand !== "pruefen") return undefined;
    let aktiv = true;
    api.get("/public/firma", { params: { host: window.location.hostname } })
      .then(() => { if (aktiv) setStand("firma"); })
      .catch(() => { if (aktiv) setStand("app"); });
    return () => { aktiv = false; };
  }, [stand]);
  if (stand === "pruefen") return <SeiteLaedt ganzeSeite />;
  if (stand === "firma") {
    return (
      <Routes>
        <Route path="*" element={<FirmenSeite />} />
      </Routes>
    );
  }
  return children;
}

function NichtGefunden() {
  const { user, loading } = useAuth();
  const { pathname } = useLocation();
  if (loading) return <SeiteLaedt ganzeSeite />;
  const ziel = nichtGefundenZiel(user, pathname);
  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-3 px-6 text-center"
         style={{ background: "var(--bg-app)", color: "var(--text-primary)" }} data-testid="nicht-gefunden">
      <div className="text-lg font-bold tracking-tight">Seite nicht gefunden</div>
      <div className="text-[13px] max-w-sm" style={{ color: "var(--text-secondary)" }}>
        Unter dieser Adresse gibt es nichts{user ? " – vielleicht ein Tippfehler." : "."}
      </div>
      <Link to={ziel} replace className="apple-btn apple-btn-primary" data-testid="nicht-gefunden-link">
        {ziel === "/login" ? "Zur Anmeldung" : "Zur Startseite"}
      </Link>
    </div>
  );
}

export default function App() {
  // 18.09.2026: Die Meldungen waren fest dunkel und standen im hellen
  // Design als schwarzer Kasten auf der hellen Seite.
  const design = useTheme();
  return (
    <AuthProvider>
      <DriverAuthProvider>
       <BuyerAuthProvider>
        <BrowserRouter>
          <Vorladen />
          <AppStartZiele />
          <Toaster theme={design} position="top-right" richColors closeButton />
          <FassungsHinweis />
          <NachladeFehler>
          <Suspense fallback={<SeiteLaedt ganzeSeite />}>
          <HostWeiche>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/login" element={<Login />} />
            {/* Einstieg der installierten App (manifest.json: start_url) */}
            <Route path="/start" element={<AppStart />} />
            {/* Einstieg des frueheren "Fahrer-Portals" (alte Installationen,
                iPhone-Symbole aendern ihn nie) — die Route fehlte, das Symbol
                landete auf der Werbe-Startseite. */}
            <Route path="/driver-login" element={<Navigate to="/start" replace />} />
            {/* Firmen registrieren sich nicht mehr selbst (09/2026) —
                der alte Registrieren-Link landet auf der Zugangs-Anfrage. */}
            <Route path="/register" element={<Navigate to="/anfrage" replace />} />
            <Route path="/anfrage" element={<Anfrage />} />
            <Route path="/passwort-vergessen" element={<PasswortVergessen />} />
            {/* Kontonummer (13.09.2026): kein Reset-Link per E-Mail mehr —
                alte Links zeigen den Hinweis "Passwort vergibt der Betreiber". */}
            <Route path="/passwort-reset" element={<Navigate to="/passwort-vergessen" replace />} />
            <Route path="/impressum" element={<Impressum />} />
            <Route path="/datenschutz" element={<Datenschutz />} />
            <Route path="/agb" element={<AGB />} />
            {/* Kundenportal (29.09.2026): Firmenseite auch über den Pfad (Vorschau, lokal ohne Unterdomains) */}
            <Route path="/firma/:slug" element={<FirmenSeite />} />

            <Route path="/abo" element={<ProtectedRoute requireSub={false}><Subscription /></ProtectedRoute>} />

            <Route path="/app" element={<ProtectedRoute requireSub={false}><AppHome /></ProtectedRoute>} />
            {/* Sucher-Funktionen: brauchen ein aktives (persönliches) Abo */}
            <Route path="/app/vergleich" element={<Wrap><Vergleich /></Wrap>} />
            {/* Vorgangsnummer (08.10.2026): Seite des Windows-Programms — die Erweiterung uebernimmt sie meist vorher */}
            <Route path="/app/vorgang/:id" element={<Wrap><Vorgang /></Wrap>} />
            <Route path="/app/suche" element={<Wrap><ManuelleSuche /></Wrap>} />
            <Route path="/app/fahrzeuge" element={<Wrap><Fahrzeugpool /></Wrap>} />
            {/* Verkaufen & Verwalten: kostenlos für den Händler-Hauptaccount */}
            <Route path="/app/vertraege" element={<WrapFree><PDFArchiv /></WrapFree>} />
            <Route path="/app/termine" element={<WrapFree><Termine /></WrapFree>} />
            {/* Runde 33 (Wunsch Ahmad): alle wartenden Abholprotokolle auf einer Seite */}
            <Route path="/app/freigaben" element={<WrapFree><Freigaben /></WrapFree>} />
            <Route path="/app/bestand" element={<WrapFree><Bestand /></WrapFree>} />
            {/* Go-Live-Schalter (15.09.2026): Marktplatz + Inserieren zeigen "Demnaechst verfuegbar" */}
            <Route path="/app/anfragen" element={<WrapFree><FeatureGate bereich="Der Bereich Kaufanfragen" zurueck="/app/bestand"><Anfragen /></FeatureGate></WrapFree>} />
            <Route path="/app/akte/:id" element={<WrapFree><FahrzeugAkte /></WrapFree>} />
            {/* Market Intelligence (25.09.2026): Chancen nur mit Schalter MARKT_CHANCEN_AKTIV */}
            <Route path="/app/markt/chancen" element={<Wrap><FeatureGate feature="markt_chancen" bereich="Der Bereich Markt-Chancen" zurueck="/app/vergleich"><Chancen /></FeatureGate></Wrap>} />
            <Route path="/app/inserat/:id" element={<WrapFree><FeatureGate bereich="Das Inserieren" zurueck="/app/bestand"><Inserat /></FeatureGate></WrapFree>} />
            <Route path="/app/fahrer" element={<WrapFree><Fahrer /></WrapFree>} />
            {/* Kundenportal (29.09.2026): Bestätigungen "Kaufvertrag digital unterschrieben" */}
            <Route path="/app/meldungen" element={<WrapFree><Meldungen /></WrapFree>} />
            <Route path="/app/team" element={<WrapFree><Team /></WrapFree>} />
            <Route path="/app/einstellungen" element={<WrapFree><Einstellungen /></WrapFree>} />
            <Route path="/app/programme" element={<WrapFree><Programme /></WrapFree>} />

            {/* B2B-Marktplatz (Zwischenhändler, eigenständig) */}
            <Route path="/markt/login" element={<FeatureGate bereich="Der B2B-Marktplatz"><BuyerLogin /></FeatureGate>} />
            <Route path="/markt/registrieren" element={<WeiterleitungMitQuery nach="/markt/login" />} />
            <Route path="/markt" element={<FeatureGate bereich="Der B2B-Marktplatz"><Marktplatz /></FeatureGate>} />

            {/* Fahrer-App (eigenständig) */}
            <Route path="/fahrer/login" element={<DriverLogin />} />
            <Route path="/fahrer/register" element={<Navigate to="/anfrage?art=fahrer" replace />} />
            <Route path="/fahrer" element={<DriverLayout />}>
              <Route index element={<DriverDashboard />} />
              <Route path="protokoll/:id" element={<DriverProtokoll />} />
              <Route path="einstellungen" element={<DriverSettings />} />
            </Route>

            <Route path="/admin" element={
              <ProtectedRoute adminOnly requireSub={false}>
                <AdminLayout />
              </ProtectedRoute>
            }>
              <Route index element={<AdminOverview />} />
              <Route path="users" element={<AdminUsers />} />
              <Route path="users/:id" element={<AdminUserDetail />} />
              <Route path="fahrer" element={<AdminFahrer />} />
              <Route path="comparisons" element={<AdminComparisons />} />
              <Route path="urls" element={<AdminUrlStats />} />
              <Route path="audit" element={<AdminAuditLog />} />
              <Route path="errors" element={<AdminErrors />} />
              <Route path="freischaltungen" element={<AdminFreischaltungen />} />
              <Route path="auto-daten" element={<AdminAutoDaten />} />
              <Route path="betrieb" element={<AdminBetrieb />} />
              <Route path="markt" element={<AdminMarkt />} />
              <Route path="markt/chancen" element={<AdminMarktChancen />} />
              <Route path="markt/auftraege" element={<AdminMarktAuftraege />} />
              {/* Private Deals (Ahmad 26.09.2026 abends): nur Super-Admin — vor markt/:modell, sonst faengt der Modellpfad den Namen */}
              <Route path="markt/private-deals" element={<AdminMarktPrivateDeals />} />
              {/* Master-Auftrag Phase D (27.09.2026): Hot Deals — nur Super-Admin (Server: current_super_admin) */}
              <Route path="markt/hot-deals" element={<AdminMarktHotDeals />} />
              {/* Master-Auftrag Phase E (27.09.2026): Berichte 5 Tage / 15 Tage / Monat — Übersicht und Modellbericht */}
              <Route path="markt/berichte" element={<AdminMarktBerichte />} />
              <Route path="markt/berichte/:modell" element={<AdminMarktBericht />} />
              {/* Master-Auftrag Phase F (27.09.2026): Segment-Optimierung — Health, Frequenz-Zuordnung, Vorschläge */}
              <Route path="markt/optimierung" element={<AdminMarktOptimierung />} />
              <Route path="markt/:modell" element={<AdminMarktModell />} />
              <Route path="programm-vergleiche" element={<AdminProgrammVergleiche />} />
              <Route path="settings" element={<AdminSettings />} />
            </Route>

            {/* U-148: kein blindes <Navigate to="/"> mehr */}
            <Route path="*" element={<NichtGefunden />} />
          </Routes>
          </HostWeiche>
          </Suspense>
          </NachladeFehler>
        </BrowserRouter>
       </BuyerAuthProvider>
      </DriverAuthProvider>
    </AuthProvider>
  );
}
