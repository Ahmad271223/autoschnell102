import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";

import { AuthProvider, useAuth } from "@/context/AuthContext";
import { DriverAuthProvider } from "@/context/DriverContext";
import { BuyerAuthProvider } from "@/context/BuyerContext";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import AppLayout from "@/components/AppLayout";

import Landing from "@/pages/Landing";
import Login from "@/pages/Login";
import Register from "@/pages/Register";
import PasswortVergessen from "@/pages/PasswortVergessen";
import PasswortReset from "@/pages/PasswortReset";
import Impressum from "@/pages/legal/Impressum";
import Datenschutz from "@/pages/legal/Datenschutz";
import Subscription from "@/pages/Subscription";
import PaymentSuccess from "@/pages/PaymentSuccess";
import AdminLayout from "@/pages/admin_v2/AdminLayout";
import AdminOverview from "@/pages/admin_v2/Overview";
import AdminUsers from "@/pages/admin_v2/Users";
import AdminUserDetail from "@/pages/admin_v2/UserDetail";
import AdminComparisons from "@/pages/admin_v2/Comparisons";
import AdminUrlStats from "@/pages/admin_v2/UrlStats";
import AdminAuditLog from "@/pages/admin_v2/AuditLog";
import AdminErrors from "@/pages/admin_v2/Errors";
import AdminFreischaltungen from "@/pages/admin_v2/Freischaltungen";
import AdminSettings from "@/pages/admin_v2/Settings";

import Vergleich from "@/pages/app/Vergleich";
import ManuelleSuche from "@/pages/app/ManuelleSuche";
import PDFArchiv from "@/pages/app/PDFArchiv";
import Termine from "@/pages/app/Termine";
import Fahrzeugpool from "@/pages/app/Fahrzeugpool";
import Bestand from "@/pages/app/Bestand";
import FahrzeugAkte from "@/pages/app/FahrzeugAkte";
import Inserat from "@/pages/app/Inserat";
import Fahrer from "@/pages/app/Fahrer";
import Team from "@/pages/app/Team";
import Einstellungen from "@/pages/app/Einstellungen";

import DriverLogin from "@/pages/driver/DriverLogin";
import DriverRegister from "@/pages/driver/DriverRegister";
import DriverLayout from "@/pages/driver/DriverLayout";
import DriverDashboard from "@/pages/driver/DriverDashboard";
import DriverSettings from "@/pages/driver/DriverSettings";

import BuyerLogin from "@/pages/markt/BuyerLogin";
import BuyerRegister from "@/pages/markt/BuyerRegister";
import Marktplatz from "@/pages/markt/Marktplatz";

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
  if (user?.role === "admin") return <Navigate to="/admin" replace />;
  return <Navigate to={user?.role === "sucher" ? "/app/vergleich" : "/app/bestand"} replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <DriverAuthProvider>
       <BuyerAuthProvider>
        <BrowserRouter>
          <Toaster theme="dark" position="top-right" richColors closeButton />
          <Routes>
            <Route path="/" element={<Landing />} />
            <Route path="/login" element={<Login />} />
            <Route path="/register" element={<Register />} />
            <Route path="/passwort-vergessen" element={<PasswortVergessen />} />
            <Route path="/passwort-reset" element={<PasswortReset />} />
            <Route path="/impressum" element={<Impressum />} />
            <Route path="/datenschutz" element={<Datenschutz />} />

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
            <Route path="/app/akte/:id" element={<WrapFree><FahrzeugAkte /></WrapFree>} />
            <Route path="/app/inserat/:id" element={<WrapFree><Inserat /></WrapFree>} />
            <Route path="/app/fahrer" element={<WrapFree><Fahrer /></WrapFree>} />
            <Route path="/app/team" element={<WrapFree><Team /></WrapFree>} />
            <Route path="/app/einstellungen" element={<WrapFree><Einstellungen /></WrapFree>} />

            {/* B2B-Marktplatz (Zwischenhändler, eigenständig) */}
            <Route path="/markt/login" element={<BuyerLogin />} />
            <Route path="/markt/registrieren" element={<BuyerRegister />} />
            <Route path="/markt" element={<Marktplatz />} />

            {/* Fahrer-App (eigenständig) */}
            <Route path="/fahrer/login" element={<DriverLogin />} />
            <Route path="/fahrer/register" element={<DriverRegister />} />
            <Route path="/fahrer" element={<DriverLayout />}>
              <Route index element={<DriverDashboard />} />
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
              <Route path="comparisons" element={<AdminComparisons />} />
              <Route path="urls" element={<AdminUrlStats />} />
              <Route path="audit" element={<AdminAuditLog />} />
              <Route path="errors" element={<AdminErrors />} />
              <Route path="freischaltungen" element={<AdminFreischaltungen />} />
              <Route path="settings" element={<AdminSettings />} />
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
       </BuyerAuthProvider>
      </DriverAuthProvider>
    </AuthProvider>
  );
}
