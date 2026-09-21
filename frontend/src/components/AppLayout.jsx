import { Suspense, useEffect, useRef } from "react";
import { toast } from "sonner";
import { neuHinzugekommen, neueWartende, titelMitZahl, useFreigabeZaehler } from "@/lib/freigaben";
import NachladeFehler from "@/components/NachladeFehler";
import SeiteLaedt from "@/components/SeiteLaedt";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Car, FileText, Calendar, Users, Settings as SettingsIcon, ShieldCheck,
  Layers, LogOut, Activity, Search, Warehouse, Inbox, ClipboardCheck,
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { startseite } from "@/lib/rollen";
import { useFeatures } from "@/lib/features";
import ThemeToggle from "@/components/ThemeToggle";
import InstallPWAButton from "@/components/InstallPWAButton";
import RechtsLinks from "@/components/RechtsLinks";

const NAV = [
  { to: "/app/vergleich", label: "Vergleich", icon: Activity },
  { to: "/app/suche", label: "Manuelle Suche", icon: Search },
  { to: "/app/vertraege", label: "Verträge / PDFs", icon: FileText },
  { to: "/app/termine", label: "Terminplaner", icon: Calendar },
  // Runde 33: Abholprotokolle, die auf die Freigabe warten — mit Zaehler.
  // 14.09.2026 (Wunsch Ahmad): nur der Chef kommuniziert mit dem Fahrer vor Ort.
  { to: "/app/freigaben", label: "Freigaben", icon: ClipboardCheck, zaehler: true, haendlerOnly: true },
  { to: "/app/fahrzeuge", label: "Fahrzeugpool", icon: Car },
  { to: "/app/bestand", label: "Bestand & Verkauf", icon: Warehouse, haendlerOnly: true },
  { to: "/app/anfragen", label: "Kaufanfragen", icon: Inbox, haendlerOnly: true },
  { to: "/app/team", label: "Mitarbeiter / Sucher", icon: Users, haendlerOnly: true },
  { to: "/app/fahrer", label: "Fahrer", icon: Users },
  { to: "/app/einstellungen", label: "Einstellungen", icon: SettingsIcon },
];

// Letzter Stand der wartenden Protokolle je Konto — ueberlebt den Wechsel
// zwischen Seiten mit eigenem Layout (sonst fiel der Hinweis dazwischen aus).
const GESEHEN = {};

/**
 * Icon-Only Rail — konstant 64px breit auf allen Bildschirmgrößen.
 * Labels sind im Tooltip (`title`) für Maus-Hover und verstecken sich
 * sonst komplett. So sieht es auf Handy und Desktop gleich aus.
 */
export default function AppLayout({ children }) {
  const { pathname } = useLocation();
  const nav = useNavigate();
  const { user, subscription, logout } = useAuth();
  const features = useFeatures();          // Go-Live-Schalter: Kaufanfragen nur mit Marktplatz

  // Sucher-Unteraccounts sehen keine Händler-Funktionen (Bestand, Team).
  // Strikte Rollentrennung: Admin-Konten verwalten nur — die Händler-/
  // Sucher-Funktionen würden im Backend ohnehin blockiert.
  const items = user?.role === "admin"
    ? [{ to: "/admin", label: "Admin", icon: ShieldCheck }]
    : NAV.filter((it) => !(it.haendlerOnly && user?.role === "sucher")
                         && (it.to !== "/app/anfragen" || features.marktplatz));

  // Runde 33 (Wunsch Ahmad): Warten Fahrer beim Verkaeufer auf die Freigabe,
  // soll man das auf JEDER Seite merken — Zahl im Menue, im Tab-Titel und ein
  // Hinweis, sobald ein neues Protokoll dazukommt.
  // 14.09.2026: Freigaben sind Chefsache — Sucher fragen den Zaehler nicht ab
  // (das Backend antwortet ihnen mit 403).
  const freigabe = useFreigabeZaehler(Boolean(user) && user.role === "dealer");
  const vorherWartend = useRef(null);
  useEffect(() => {
    if (!freigabe.geladen) {
      document.title = titelMitZahl(document.title, 0);
      return;
    }
    document.title = titelMitZahl(document.title, freigabe.wartet);
    // Neu ist eine neue Protokoll-ID — auch wenn die Zahl gleich bleibt.
    const neu = Array.isArray(freigabe.ids)
      ? neueWartende(GESEHEN, user?.id, freigabe.ids).length
      : neuHinzugekommen(vorherWartend.current, freigabe.wartet);
    vorherWartend.current = freigabe.wartet;
    if (neu > 0 && !pathname.startsWith("/app/freigaben")) {
      toast.message(neu === 1 ? "Ein Fahrer wartet auf deine Freigabe"
        : `${neu} Fahrer warten auf deine Freigabe`, {
        action: { label: "Öffnen", onClick: () => nav("/app/freigaben") },
        duration: 15000,
      });
    }
  }, [freigabe, pathname, nav, user?.id]);
  // Beim Abmelden keine Zahl eines anderen Kontos im Tab-Titel stehen lassen.
  useEffect(() => () => { document.title = titelMitZahl(document.title, 0); }, []);

  return (
    <div className="min-h-screen flex" style={{ background: "var(--bg-app)", color: "var(--text-primary)" }}>
      {/* Pruefbericht 20.09.2026 (M-01): 100vh ist auf iOS hoeher als der
          sichtbare Bereich (unterste Knoepfe verdeckt) — 100dvh, wo bekannt
          (sonst bleibt h-screen), und Abstand fuer Notch/Home-Leiste. */}
      <aside
        data-testid="app-sidebar"
        className="w-16 border-r flex flex-col shrink-0 sticky top-0 h-screen"
        style={{ borderColor: "var(--border-default)", background: "var(--bg-surface)",
                 height: "100dvh", paddingTop: "env(safe-area-inset-top)",
                 paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {/* U-144: das Logo fuehrt zur eigenen Startseite (Chef ohne Abo sonst
            direkt auf die Abo-Sperre des Vergleichs) */}
        <Link to={startseite(user)}
              className="h-16 border-b flex items-center justify-center shrink-0"
              style={{ borderColor: "var(--border-default)" }}
              title="Autohandel">
          <span className="w-9 h-9 rounded-md flex items-center justify-center"
                style={{ background: "var(--accent-red)" }}>
            <Layers size={18} className="text-white" />
          </span>
        </Link>

        <nav className="flex-1 py-3 px-1.5 space-y-1.5 overflow-y-auto">
          {items.map((it) => {
            const Active = pathname.startsWith(it.to);
            const Icon = it.icon;
            // Pruefbericht 20.09.2026 (M-02): aria-label nennt den Bereich samt
            // Zahl wartender Freigaben (der sichtbare Zaehler ist ausgeblendet).
            return (
              <Link
                key={it.to}
                to={it.to}
                data-testid={`nav-${it.to.split("/").pop()}`}
                title={it.label}
                aria-label={it.zaehler && freigabe.wartet > 0
                  ? `${it.label} — ${freigabe.wartet} warten` : it.label}
                className={`relative flex items-center justify-center w-full py-3 rounded-lg sidebar-link ${
                  Active ? "sidebar-link-active" : ""
                }`}
              >
                <Icon size={20} className={Active ? "text-[var(--accent-red)]" : ""} />
                {it.zaehler && freigabe.wartet > 0 && (
                  <span data-testid="nav-freigaben-zaehler" aria-hidden="true"
                        className="absolute top-1 right-1.5 min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold flex items-center justify-center text-white"
                        style={{ background: "var(--accent-red)" }}>
                    {freigabe.wartet > 9 ? "9+" : freigabe.wartet}
                  </span>
                )}
                {Active && (
                  <span className="absolute right-0.5 top-1/2 -translate-y-1/2 w-1 h-5 rounded-full"
                        style={{ background: "var(--accent-red)" }} />
                )}
              </Link>
            );
          })}
        </nav>

        <div className="flex flex-col items-center gap-2 pb-2 border-t pt-2"
             style={{ borderColor: "var(--border-default)" }}>
          <InstallPWAButton variante="symbol" />
          <ThemeToggle />
          {/* U-143: der Abo-Punkt fuehrt zum Abo-Bereich in den Einstellungen */}
          <Link to="/app/einstellungen" className="p-1.5 rounded-md hover:bg-white/5"
                aria-label={`Abo: ${subscription?.plan === "lifetime" ? "Lifetime"
                  : subscription?.active ? "aktiv" : "kein Abo"} — zu den Einstellungen`}>
            <span
              className="block w-2 h-2 rounded-full"
              style={{
                background: subscription?.active ? "var(--accent-green)" : "var(--accent-red)",
              }}
              title={subscription?.plan === "lifetime" ? "Lifetime"
                     : subscription?.active ? "Aktiv" : "Kein Abo"}
              data-testid="sub-status-badge"
            />
          </Link>
          <button
            onClick={async () => { await logout(); nav("/"); }}
            data-testid="logout-btn"
            className="p-2 rounded-md hover:bg-white/5"
            style={{ color: "var(--text-secondary)" }}
            title="Abmelden"
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>

      {/* M-03: zu breite Inhalte scrollen statt abgeschnitten zu werden */}
      <main className="flex-1 overflow-x-auto min-w-0 flex flex-col">
        <div className="flex-1">
          <NachladeFehler>
            <Suspense fallback={<SeiteLaedt />}>
              {children}
            </Suspense>
          </NachladeFehler>
        </div>
        {/* Pruefbericht 20.09.2026 (RE-01): Impressum und Datenschutz von
            jeder Seite des angemeldeten Bereichs aus erreichbar. */}
        <RechtsLinks className="py-4" />
      </main>

    </div>
  );
}
