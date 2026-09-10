import "@/App.css";
import { Suspense, lazy, useEffect, useState } from "react";
import { tokenLesen } from "@/lib/sitzung";

// Vite (09/2026): Seiten laden erst bei Bedarf nach — die erste Seite ist
// dadurch deutlich schneller da. Scheitert das Nachladen (z. B. kurz nach
// einem Update, wenn der Browser noch die alte Seitenliste kennt), laedt die
// Oberflaeche EINMAL neu statt einen leeren Bildschirm zu zeigen.
const NEU_GELADEN = "ah_seite_neu_geladen";
function seite(laden) {
  return lazy(() => laden().then((modul) => {
    try { sessionStorage.removeItem(NEU_GELADEN); } catch { /* egal */ }
    return modul;
  }, (fehler) => {
    let schonVersucht = false;
    try { schonVersucht = sessionStorage.getItem(NEU_GELADEN) === "1"; } catch { /* egal */ }
    if (!schonVersucht) {
      try { sessionStorage.setItem(NEU_GELADEN, "1"); } catch { /* egal */ }
      window.location.reload();
      return new Promise(() => {});
    }
    throw fehler;
  }));
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
];
function vorladen() {
  for (const laden of VORLADEN) laden().catch(() => {});
}

// Kurze Ladezeiten zeigen nichts an (kein Flackern); erst nach 250 ms ein Hinweis.
function Laedt() {
  const [zeigen, setZeigen] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setZeigen(true), 250);
    return () => clearTimeout(t);
  }, []);
  if (!zeigen) return null;
  return (
    <div className="min-h-[40vh] flex items-center justify-center text-sm"
         style={{ color: "var(--text-muted)" }} data-testid="seite-laedt">
      Lädt …
    </div>
  );
}
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";

import { AuthProvider, useAuth } from "@/context/AuthContext";
import { DriverAuthProvider } from "@/context/DriverContext";
import { BuyerAuthProvider } from "@/context/BuyerContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { startseite } from "@/lib/rollen";
import AppLayout from "@/components/AppLayout";

import Landing from "@/pages/Landing";
import Login from "@/pages/Login";
const Anfrage = seite(() => import("@/pages/Anfrage"));
const MarktZahlungErfolg = seite(() => import("@/pages/markt/ZahlungErfolg"));
const PasswortVergessen = seite(() => import("@/pages/PasswortVergessen"));
const PasswortReset = seite(() => import("@/pages/PasswortReset"));
const Impressum = seite(() => import("@/pages/legal/Impressum"));
const Datenschutz = seite(() => import("@/pages/legal/Datenschutz"));
const AGB = seite(() => import("@/pages/legal/AGB"));
const Subscription = seite(() => import("@/pages/Subscription"));
const PaymentSuccess = seite(() => import("@/pages/PaymentSuccess"));
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

import Vergleich from "@/pages/app/Vergleich";
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
const Anfragen = seite(() => import("@/pages/app/Anfragen"));

const DriverLogin = seite(() => import("@/pages/driver/DriverLogin"));
const DriverRegister = seite(() => import("@/pages/driver/DriverRegister"));
const DriverLayout = seite(() => import("@/pages/driver/DriverLayout"));
const DriverDashboard = seite(() => import("@/pages/driver/DriverDashboard"));
const DriverSettings = seite(() => import("@/pages/driver/DriverSettings"));
const DriverProtokoll = seite(() => import("@/pages/driver/Protokoll"));

const BuyerLogin = seite(() => import("@/pages/markt/BuyerLogin"));
const BuyerRegister = seite(() => import("@/pages/markt/BuyerRegister"));
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

export default function App() {
  useEffect(() => {
    if (!tokenLesen()) return undefined;
    const leerlauf = window.requestIdleCallback || ((f) => setTimeout(f, 1500));
    const abbrechen = window.cancelIdleCallback || clearTimeout;
    const id = leerlauf(vorladen);
    return () => abbrechen(id);
  }, []);
  return (
    <AuthProvider>
      <DriverAuthProvider>
       <BuyerAuthProvider>
        <BrowserRouter>
          <Toaster theme="dark" position="top-right" richColors closeButton />
          <Suspense fallback={<Laedt />}>
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/login" element={<Login />} />
            {/* Firmen registrieren sich nicht mehr selbst (09/2026) —
                der alte Registrieren-Link landet auf der Zugangs-Anfrage. */}
            <Route path="/register" element={<Navigate to="/anfrage" replace />} />
            <Route path="/anfrage" element={<Anfrage />} />
            <Route path="/passwort-vergessen" element={<PasswortVergessen />} />
            <Route path="/passwort-reset" element={<PasswortReset />} />
            <Route path="/impressum" element={<Impressum />} />
            <Route path="/datenschutz" element={<Datenschutz />} />
            <Route path="/agb" element={<AGB />} />

            <Route path="/abo" element={<ProtectedRoute requireSub={false}><Subscription /></ProtectedRoute>} />
            <Route path="/abo/erfolg" element={<ProtectedRoute requireSub={false}><PaymentSuccess /></ProtectedRoute>} />

            <Route path="/app" element={<ProtectedRoute requireSub={false}><AppHome /></ProtectedRoute>} />
            {/* Sucher-Funktionen: brauchen ein aktives (persönliches) Abo */}
            <Route path="/app/vergleich" element={<Wrap><Vergleich /></Wrap>} />
            <Route path="/app/suche" element={<Wrap><ManuelleSuche /></Wrap>} />
            <Route path="/app/fahrzeuge" element={<Wrap><Fahrzeugpool /></Wrap>} />
            {/* Verkaufen & Verwalten: kostenlos für den Händler-Hauptaccount */}
            <Route path="/app/vertraege" element={<WrapFree><PDFArchiv /></WrapFree>} />
            <Route path="/app/termine" element={<WrapFree><Termine /></WrapFree>} />
            <Route path="/app/bestand" element={<WrapFree><Bestand /></WrapFree>} />
            <Route path="/app/anfragen" element={<WrapFree><Anfragen /></WrapFree>} />
            <Route path="/app/akte/:id" element={<WrapFree><FahrzeugAkte /></WrapFree>} />
            <Route path="/app/inserat/:id" element={<WrapFree><Inserat /></WrapFree>} />
            <Route path="/app/fahrer" element={<WrapFree><Fahrer /></WrapFree>} />
            <Route path="/app/team" element={<WrapFree><Team /></WrapFree>} />
            <Route path="/app/einstellungen" element={<WrapFree><Einstellungen /></WrapFree>} />

            {/* B2B-Marktplatz (Zwischenhändler, eigenständig) */}
            <Route path="/markt/login" element={<BuyerLogin />} />
            <Route path="/markt/registrieren" element={<BuyerRegister />} />
            <Route path="/markt/zahlung-erfolg" element={<MarktZahlungErfolg />} />
            <Route path="/markt" element={<Marktplatz />} />

            {/* Fahrer-App (eigenständig) */}
            <Route path="/fahrer/login" element={<DriverLogin />} />
            <Route path="/fahrer/register" element={<DriverRegister />} />
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
              <Route path="settings" element={<AdminSettings />} />
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </Suspense>
        </BrowserRouter>
       </BuyerAuthProvider>
      </DriverAuthProvider>
    </AuthProvider>
  );
}
