import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { neuHinzugekommen, neueWartende, titelMitZahl, useFreigabeZaehler } from "@/lib/freigaben";
import NachladeFehler from "@/components/NachladeFehler";
import SeiteLaedt from "@/components/SeiteLaedt";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Car, FileText, Calendar, Users, Settings as SettingsIcon, ShieldCheck, Truck,
  Layers, LogOut, Activity, Search, Warehouse, Inbox, ClipboardCheck, Radar, Bell, MonitorDown,
  Menu, X, PanelLeftClose, PanelLeftOpen,
} from "lucide-react";
import { useProgramme } from "@/lib/programme";
import { useAuth } from "@/context/AuthContext";
import { startseite } from "@/lib/rollen";
import { useFeatures } from "@/lib/features";
import { verlassenBestaetigen } from "@/lib/ungespeichert";
import { useAnfragenZaehler } from "@/lib/anfragenZaehler";
import { useAbgelehntZaehler } from "@/lib/abgelehntZaehler";
import { neueMeldungen, useMeldungenZaehler } from "@/lib/meldungen";
import { lesen, lokalerSpeicher, schreiben } from "@/lib/speicher";
import ThemeToggle from "@/components/ThemeToggle";
import InstallPWAButton from "@/components/InstallPWAButton";
import RechtsLinks from "@/components/RechtsLinks";

// Wunsch Ahmad 03.10.2026: Die Leiste war nur eine Reihe Symbole — man wusste nicht, was wofür ist
// (Mitarbeiter und Fahrer sogar mit demselben Symbol). Jetzt: Bereiche nach Zweck gruppiert, am PC
// mit Namen (einklappbar auf die schmale Leiste), am Handy die schmale Leiste plus Menü mit Namen.
export const GRUPPEN = [
  { key: "einkauf", titel: "Einkauf" },
  { key: "abwicklung", titel: "Abwicklung" },
  { key: "verkauf", titel: "Verkauf" },
  { key: "team", titel: "Team" },
  { key: "allgemein", titel: "Allgemein" },
];

export const NAV = [
  { to: "/app/vergleich", label: "Vergleich", icon: Activity, gruppe: "einkauf" },
  { to: "/app/suche", label: "Manuelle Suche", icon: Search, gruppe: "einkauf" },
  // Market Intelligence (25.09.2026): Chancen aus der Marktbeobachtung (Schalter markt_chancen)
  { to: "/app/markt/chancen", label: "Markt · Chancen", icon: Radar, feature: "markt_chancen", gruppe: "einkauf" },
  { to: "/app/vertraege", label: "Verträge / PDFs", icon: FileText, gruppe: "abwicklung" },
  // Rollenprüfung 22.09.2026 (RP-464): Zahl der Fahrten, die ein Fahrer
  // abgelehnt hat und die noch keinen neuen Fahrer haben (nur Chef).
  { to: "/app/termine", label: "Terminplaner", icon: Calendar, abgelehnt: true, gruppe: "abwicklung" },
  // Runde 33: Abholprotokolle, die auf die Freigabe warten — mit Zaehler.
  // 14.09.2026 (Wunsch Ahmad): nur der Chef kommuniziert mit dem Fahrer vor Ort.
  { to: "/app/freigaben", label: "Freigaben", icon: ClipboardCheck, zaehler: true, haendlerOnly: true, gruppe: "abwicklung" },
  { to: "/app/fahrzeuge", label: "Fahrzeugpool", icon: Car, gruppe: "abwicklung" },
  { to: "/app/bestand", label: "Bestand & Verkauf", icon: Warehouse, haendlerOnly: true, gruppe: "verkauf" },
  // Rollenprüfung 22.09.2026 (RP-472): Zahl der Kaufanfragen, die eine
  // Antwort brauchen (neu oder Gegenangebot des Käufers).
  { to: "/app/anfragen", label: "Kaufanfragen", icon: Inbox, haendlerOnly: true, anfragen: true, gruppe: "verkauf" },
  { to: "/app/team", label: "Mitarbeiter / Sucher", icon: Users, haendlerOnly: true, gruppe: "team" },
  { to: "/app/fahrer", label: "Fahrer", icon: Truck, gruppe: "team" },
  // Kundenportal (29.09.2026): "Kaufvertrag bestätigt" — Chef und Sucher, mit Zähler und Hinweis
  { to: "/app/meldungen", label: "Meldungen", icon: Bell, meldungen: true, gruppe: "allgemein" },
  { to: "/app/einstellungen", label: "Einstellungen", icon: SettingsIcon, gruppe: "allgemein" },
];

/** Einträge in Gruppen (Reihenfolge wie GRUPPEN); Einträge ohne Gruppe (Admin) ohne Überschrift. */
export function gruppiert(items) {
  const out = GRUPPEN.map((g) => ({ ...g, items: items.filter((it) => it.gruppe === g.key) }))
    .filter((g) => g.items.length > 0);
  const ohne = items.filter((it) => !it.gruppe || !GRUPPEN.some((g) => g.key === it.gruppe));
  return ohne.length ? [{ key: "ohne", titel: "", items: ohne }, ...out] : out;
}

export const LEISTE_KEY = "ah_leiste_schmal";

// Letzter Stand der wartenden Protokolle je Konto — ueberlebt den Wechsel
// zwischen Seiten mit eigenem Layout (sonst fiel der Hinweis dazwischen aus).
const GESEHEN = {};
// Letzter Stand der Meldungen je Konto (Kundenportal) — gleiche Rolle wie GESEHEN.
const GESEHEN_MELDUNGEN = {};

/**
 * Seitenleiste: am PC (ab 768 px) mit Namen und Gruppen, auf Wunsch eingeklappt auf die schmale
 * Symbolleiste (gemerkt im Browser). Am Handy immer die schmale Leiste (64 px) — der Knopf oben
 * öffnet das Menü mit allen Namen.
 */
export default function AppLayout({ children }) {
  const { pathname } = useLocation();
  const nav = useNavigate();
  const { user, dealer, subscription, logout } = useAuth();
  const features = useFeatures();          // Go-Live-Schalter: Kaufanfragen nur mit Marktplatz
  const [schmal, setSchmal] = useState(() => lesen(lokalerSpeicher(), LEISTE_KEY) === "1");
  const [menueOffen, setMenueOffen] = useState(false);
  const breit = !schmal;
  const umschalten = () => {
    setSchmal((s) => {
      schreiben(lokalerSpeicher(), LEISTE_KEY, s ? "0" : "1");
      return !s;
    });
  };

  // Sucher-Unteraccounts sehen keine Händler-Funktionen (Bestand, Team).
  // Strikte Rollentrennung: Admin-Konten verwalten nur — die Händler-/
  // Sucher-Funktionen würden im Backend ohnehin blockiert.
  const items = user?.role === "admin"
    ? [{ to: "/admin", label: "Admin", icon: ShieldCheck }]
    : NAV.filter((it) => !(it.haendlerOnly && user?.role === "sucher")
                         && (it.to !== "/app/anfragen" || features.marktplatz)
                         && (!it.feature || features[it.feature]));
  // Programme zum Herunterladen (03.10.2026): nur wenn der Server fuer DIESE Firma
  // welche freigeschaltet hat — Name vom Server, sonst kein Menuepunkt.
  const programme = useProgramme(user?.role === "dealer" || user?.role === "sucher", user?.id);
  if (programme && programme.length > 0) {
    const vorEinstellungen = items.findIndex((it) => it.to === "/app/einstellungen");
    items.splice(vorEinstellungen < 0 ? items.length : vorEinstellungen, 0, {
      to: "/app/programme", label: programme.length === 1 ? programme[0].name : "Programme", icon: MonitorDown,
      gruppe: "allgemein",
    });
  }
  const gruppen = gruppiert(items);

  // Runde 33 (Wunsch Ahmad): Warten Fahrer beim Verkaeufer auf die Freigabe,
  // soll man das auf JEDER Seite merken — Zahl im Menue, im Tab-Titel und ein
  // Hinweis, sobald ein neues Protokoll dazukommt.
  // 14.09.2026: Freigaben sind Chefsache — Sucher fragen den Zaehler nicht ab
  // (das Backend antwortet ihnen mit 403).
  const freigabe = useFreigabeZaehler(Boolean(user) && user.role === "dealer");
  const anfragenWarten = useAnfragenZaehler(
    Boolean(user) && user.role === "dealer" && Boolean(features.marktplatz), user?.id);
  // RP-464: vom Fahrer abgelehnte Zuteilungen — Fahrer teilt nur der Chef zu.
  const abgelehnt = useAbgelehntZaehler(Boolean(user) && user.role === "dealer", user?.id);
  // Kundenportal (29.09.2026): Meldungen "Kaufvertrag bestätigt" — Chef UND Sucher; neue Meldung
  // = neue ID (nicht nur eine höhere Zahl), erster Abruf meldet den Altbestand nicht.
  const meldungen = useMeldungenZaehler(Boolean(user) && user.role !== "admin", user?.id);
  useEffect(() => {
    if (!meldungen.geladen || !Array.isArray(meldungen.ids)) return;
    const neu = neueMeldungen(GESEHEN_MELDUNGEN, user?.id, meldungen.ids);
    if (neu.length > 0 && !pathname.startsWith("/app/meldungen")) {
      toast.success(neu.length === 1 ? "Kaufvertrag bestätigt — ein Kunde hat digital unterschrieben"
        : `${neu.length} Kaufverträge wurden digital unterschrieben`, {
        action: { label: "Öffnen", onClick: () => { if (verlassenBestaetigen()) nav("/app/meldungen"); } },
        duration: 15000,
      });
    }
  }, [meldungen, pathname, nav, user?.id]);
  // Rollenprüfung 22.09.2026 (RP-143): Wechsel innerhalb der App fragt nach,
  // solange eine Seite ungespeicherte Eingaben gemeldet hat (vorher schützte
  // nur beforeunload beim Neuladen — ein Klick in die Leiste verwarf still).
  const wegBestaetigen = (ziel) => (e) => {
    if (ziel && ziel === pathname) return;
    if (!verlassenBestaetigen()) e.preventDefault();
  };
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
        action: { label: "Öffnen", onClick: () => { if (verlassenBestaetigen()) nav("/app/freigaben"); } },
        duration: 15000,
      });
    }
  }, [freigabe, pathname, nav, user?.id]);
  // Beim Abmelden keine Zahl eines anderen Kontos im Tab-Titel stehen lassen.
  useEffect(() => () => { document.title = titelMitZahl(document.title, 0); }, []);
  // Menü (Handy) schließt beim Seitenwechsel und mit Escape
  useEffect(() => { setMenueOffen(false); }, [pathname]);
  useEffect(() => {
    if (!menueOffen) return undefined;
    const taste = (e) => { if (e.key === "Escape") setMenueOffen(false); };
    document.addEventListener("keydown", taste);
    return () => document.removeEventListener("keydown", taste);
  }, [menueOffen]);

  // Zahl + Beschriftung je Eintrag (Freigaben, Anfragen, abgelehnte Fahrten, Meldungen)
  const zahlFuer = (it) => (it.zaehler ? freigabe.wartet
    : it.anfragen ? anfragenWarten : it.abgelehnt ? abgelehnt : it.meldungen ? meldungen.ungelesen : 0);
  const zahlText = (it) => (it.anfragen ? "brauchen eine Antwort"
    : it.abgelehnt ? "vom Fahrer abgelehnt, bitte neu zuteilen" : it.meldungen ? "ungelesen" : "warten");
  const zaehlerId = (it) => (it.anfragen ? "nav-anfragen-zaehler"
    : it.abgelehnt ? "nav-termine-zaehler" : it.meldungen ? "nav-meldungen-zaehler" : "nav-freigaben-zaehler");

  const firmaName = (dealer?.company_name || "").trim();
  const rolleText = user?.role === "dealer" ? "Chef" : user?.role === "sucher" ? "Sucher"
    : user?.role === "admin" ? "Admin" : "";

  return (
    <div className="min-h-screen flex" style={{ background: "var(--bg-app)", color: "var(--text-primary)" }}>
      {/* Pruefbericht 20.09.2026 (M-01): 100vh ist auf iOS hoeher als der
          sichtbare Bereich (unterste Knoepfe verdeckt) — 100dvh, wo bekannt
          (sonst bleibt h-screen), und Abstand fuer Notch/Home-Leiste. */}
      <aside
        data-testid="app-sidebar"
        data-breit={breit ? "ja" : "nein"}
        className={`w-16 ${breit ? "md:w-60" : ""} border-r flex flex-col shrink-0 sticky top-0 h-screen transition-[width] duration-150`}
        style={{ borderColor: "var(--border-default)", background: "var(--bg-surface)",
                 height: "100dvh", paddingTop: "env(safe-area-inset-top)",
                 paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {/* U-144: das Logo fuehrt zur eigenen Startseite (Chef ohne Abo sonst
            direkt auf die Abo-Sperre des Vergleichs) */}
        <Link to={startseite(user)} onClick={wegBestaetigen(startseite(user))}
              className={`h-16 border-b flex items-center justify-center gap-3 shrink-0 ${breit ? "md:justify-start md:px-4" : ""}`}
              style={{ borderColor: "var(--border-default)" }}
              title="Startseite">
          <span className="w-9 h-9 rounded-md flex items-center justify-center shrink-0"
                style={{ background: "var(--accent-red)" }}>
            <Layers size={18} className="text-white" />
          </span>
          <span className={`hidden ${breit ? "md:block" : ""} min-w-0`}>
            <span className="block text-[13px] font-semibold truncate" style={{ color: "var(--text-primary)" }}
                  data-testid="leiste-firma">
              {firmaName || "Startseite"}
            </span>
            {rolleText && (
              <span className="block text-[11px]" style={{ color: "var(--text-muted)" }}>{rolleText}</span>
            )}
          </span>
        </Link>

        {/* Handy: Menü mit allen Namen */}
        <button type="button" onClick={() => setMenueOffen(true)} data-testid="leiste-menue-oeffnen"
                aria-label="Menü mit allen Bereichen öffnen" aria-expanded={menueOffen}
                className="md:hidden mx-auto mt-2 w-10 h-10 rounded-md flex items-center justify-center sidebar-link">
          <Menu size={20} />
        </button>

        <nav className={`flex-1 py-2 px-1.5 ${breit ? "md:px-2.5" : ""} overflow-y-auto`} aria-label="Bereiche">
          {gruppen.map((g, gi) => (
            <div key={g.key} role="group" aria-label={g.titel || undefined}>
              {g.titel && (
                <div className={`hidden ${breit ? "md:block" : ""} px-3 pt-3 pb-1 text-[10.5px] font-semibold uppercase tracking-wider`}
                     style={{ color: "var(--text-muted)" }}>
                  {g.titel}
                </div>
              )}
              {gi > 0 && (
                <div className={`mx-3 my-1.5 border-t ${breit ? "md:hidden" : ""}`}
                     style={{ borderColor: "var(--border-default)" }} />
              )}
              <div className="space-y-1">
                {g.items.map((it) => {
                  const Active = pathname.startsWith(it.to);
                  const Icon = it.icon;
                  const zahl = zahlFuer(it);
                  // Pruefbericht 20.09.2026 (M-02): aria-label nennt den Bereich samt
                  // Zahl wartender Freigaben (der sichtbare Zaehler ist ausgeblendet).
                  return (
                    <Link
                      key={it.to}
                      to={it.to}
                      onClick={wegBestaetigen(it.to)}
                      data-testid={`nav-${it.to.split("/").pop()}`}
                      title={it.label}
                      aria-label={zahl > 0 ? `${it.label} — ${zahl} ${zahlText(it)}` : it.label}
                      aria-current={Active ? "page" : undefined}
                      className={`relative flex items-center justify-center gap-3 w-full py-2.5 rounded-lg sidebar-link ${
                        breit ? "md:justify-start md:px-3" : ""} ${Active ? "sidebar-link-active" : ""}`}
                    >
                      <Icon size={20} className={`shrink-0 ${Active ? "text-[var(--accent-red)]" : ""}`} />
                      <span className={`hidden ${breit ? "md:block" : ""} flex-1 min-w-0 truncate text-[13.5px] font-medium`}>
                        {it.label}
                      </span>
                      {zahl > 0 && (
                        <span data-testid={zaehlerId(it)} aria-hidden="true"
                              className={`absolute top-1 right-1.5 ${breit ? "md:static" : ""} min-w-[18px] h-[18px] px-1 rounded-full text-[10px] font-bold flex items-center justify-center text-white`}
                              style={{ background: "var(--accent-red)" }}>
                          {zahl > 9 ? "9+" : zahl}
                        </span>
                      )}
                      {Active && (
                        <span className="absolute right-0.5 top-1/2 -translate-y-1/2 w-1 h-5 rounded-full"
                              style={{ background: "var(--accent-red)" }} />
                      )}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>

        {/* Handy-Ansicht (24.09.2026): alle vier Knoepfe der Leiste 40 px
            gross (vorher 20-36 px) — in der 64-px-Leiste bleibt das Platz.
            Am PC mit Namen stehen sie nebeneinander. */}
        <div className={`flex flex-col items-center gap-1 pb-1 border-t pt-1 ${breit ? "md:flex-row md:flex-wrap md:justify-center md:px-2" : ""}`}
             style={{ borderColor: "var(--border-default)" }}>
          <InstallPWAButton variante="symbol" />
          {/* Rollenprüfung 22.09.2026 (RP-024/M-19): der Zwei-Segment-Schalter
              ist ~90 px breit und ragte aus der 64-px-Leiste — hier als
              runder Symbolknopf. */}
          <ThemeToggle variante="symbol" />
          {/* U-143: der Abo-Punkt fuehrt zum Abo-Bereich in den Einstellungen */}
          <Link to="/app/einstellungen" onClick={wegBestaetigen("/app/einstellungen")}
                className="w-10 h-10 rounded-md hover:bg-white/5 flex items-center justify-center"
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
            onClick={async () => {
              if (!verlassenBestaetigen()) return;
              await logout();
              nav("/");
            }}
            data-testid="logout-btn"
            className="w-10 h-10 rounded-md hover:bg-white/5 flex items-center justify-center"
            style={{ color: "var(--text-secondary)" }}
            title="Abmelden"
            aria-label="Abmelden"
          >
            <LogOut size={16} />
          </button>
          {/* PC: Leiste mit Namen ein-/ausklappen (gemerkt im Browser) */}
          <button type="button" onClick={umschalten} data-testid="leiste-umschalten"
                  className="hidden md:flex w-10 h-10 rounded-md hover:bg-white/5 items-center justify-center"
                  style={{ color: "var(--text-secondary)" }}
                  title={breit ? "Leiste einklappen (nur Symbole)" : "Leiste mit Namen zeigen"}
                  aria-label={breit ? "Leiste einklappen" : "Leiste mit Namen zeigen"}>
            {breit ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
          </button>
        </div>
      </aside>

      {/* Handy: Menü mit Namen (über der Seite) */}
      {menueOffen && (
        <div className="fixed inset-0 z-50 md:hidden" data-testid="leiste-menue">
          <button type="button" aria-label="Menü schließen" onClick={() => setMenueOffen(false)}
                  className="absolute inset-0 w-full h-full" style={{ background: "rgba(0,0,0,0.45)" }} />
          <div role="dialog" aria-modal="true" aria-label="Menü"
               className="absolute left-0 top-0 h-full w-[min(280px,85vw)] flex flex-col shadow-2xl"
               style={{ background: "var(--bg-surface)", borderRight: "1px solid var(--border-default)",
                        paddingTop: "env(safe-area-inset-top)", paddingBottom: "env(safe-area-inset-bottom)" }}>
            <div className="h-16 px-4 flex items-center gap-3 border-b shrink-0"
                 style={{ borderColor: "var(--border-default)" }}>
              <span className="w-9 h-9 rounded-md flex items-center justify-center shrink-0"
                    style={{ background: "var(--accent-red)" }}>
                <Layers size={18} className="text-white" />
              </span>
              <span className="flex-1 min-w-0">
                <span className="block text-[13px] font-semibold truncate">{firmaName || "Menü"}</span>
                {rolleText && <span className="block text-[11px]" style={{ color: "var(--text-muted)" }}>{rolleText}</span>}
              </span>
              <button type="button" onClick={() => setMenueOffen(false)} data-testid="leiste-menue-schliessen"
                      aria-label="Menü schließen"
                      className="w-10 h-10 rounded-md flex items-center justify-center sidebar-link">
                <X size={18} />
              </button>
            </div>
            <nav className="flex-1 overflow-y-auto px-2.5 py-2" aria-label="Bereiche">
              {gruppen.map((g) => (
                <div key={g.key}>
                  {g.titel && (
                    <div className="px-3 pt-3 pb-1 text-[10.5px] font-semibold uppercase tracking-wider"
                         style={{ color: "var(--text-muted)" }}>{g.titel}</div>
                  )}
                  <div className="space-y-1">
                    {g.items.map((it) => {
                      const Active = pathname.startsWith(it.to);
                      const Icon = it.icon;
                      const zahl = zahlFuer(it);
                      return (
                        <Link key={it.to} to={it.to}
                              onClick={(e) => { wegBestaetigen(it.to)(e); if (!e.defaultPrevented) setMenueOffen(false); }}
                              data-testid={`menue-${it.to.split("/").pop()}`}
                              aria-label={zahl > 0 ? `${it.label} — ${zahl} ${zahlText(it)}` : it.label}
                              aria-current={Active ? "page" : undefined}
                              className={`flex items-center gap-3 px-3 py-3 rounded-lg sidebar-link ${Active ? "sidebar-link-active" : ""}`}>
                          <Icon size={18} className={`shrink-0 ${Active ? "text-[var(--accent-red)]" : ""}`} />
                          <span className="flex-1 min-w-0 truncate text-[14px] font-medium">{it.label}</span>
                          {zahl > 0 && (
                            <span aria-hidden="true"
                                  className="min-w-[20px] h-[20px] px-1.5 rounded-full text-[11px] font-bold flex items-center justify-center text-white"
                                  style={{ background: "var(--accent-red)" }}>
                              {zahl > 9 ? "9+" : zahl}
                            </span>
                          )}
                        </Link>
                      );
                    })}
                  </div>
                </div>
              ))}
            </nav>
          </div>
        </div>
      )}

      {/* M-03: zu breite Inhalte scrollen statt abgeschnitten zu werden.
          Handy-Ansicht (24.09.2026): als installierte App auf dem iPhone beginnt
          die Seite unter der Statusleiste (viewport-fit=cover) — sonst lag die
          Ueberschrift hinter Uhrzeit und Akku. */}
      <main className="flex-1 overflow-x-auto min-w-0 flex flex-col kopf-sicher">
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
