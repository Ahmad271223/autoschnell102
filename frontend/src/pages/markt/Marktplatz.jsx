import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { buyerApi, useBuyer } from "@/context/BuyerContext";
import { errMsg } from "@/lib/api";
import { ladeMakes } from "@/lib/katalog";
import RechtsLinks from "@/components/RechtsLinks";
import { toast } from "sonner";
import { Store, LogOut, Lock, Gauge, Calendar, Fuel, ShieldCheck, Phone, MapPin, X, Clock, ChevronLeft, ChevronRight, Camera, Heart, Handshake, Inbox, Check, SlidersHorizontal, Mail } from "lucide-react";
import {
  anfrageAblaufText, angebotsFrage, anfragenGesehen, anfragenGesehenMerken, anfragenStand, beendetText, betragHinweis,
  betragPruefen, einladungVergessen, entwurfLesen, entwurfLoeschen, entwurfMerken, fahrzeugTitel,
  filterParameter, gemerkteEinladung, inseratAblauf, istNeu, kmAnzeige, listeAnhaengen, markenAusInseraten,
  neuigkeitenZahl, STATUS_TEXT, unfallZeile, verlaufZeile, zugangAblauf,
} from "./marktHilfen";

const BACKEND = process.env.REACT_APP_BACKEND_URL || "";
const fmtEur = (n) => (n == null ? "Preis auf Anfrage" : `${Number(n).toLocaleString("de-DE")} €`);
const photoUrl = (u) => (!u ? null : u.startsWith("http") ? u : `${BACKEND}${u}`);
// RP-520: so oft fragt die Seite nach neuen Antworten der Händler.
const NEUIGKEITEN_MS = 60000;

const LEVEL_LABEL = {
  netzwerk: "Netzwerkpreis", b2b: "B2B-Preis", oeffentlich: "Öffentlich",
};

const fInput = "h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none focus:border-white/40";
const fStyle = { borderColor: "var(--border-default)" };
/** RP-456 (Welle 2): wie der getippte Betrag verstanden wird ("= 12.500,00 €"). */
function BetragVerstanden({ text, testid }) {
  const hinweis = betragHinweis(text);
  if (!hinweis) return null;
  return (
    <div className="mt-1 text-[11px]" data-testid={testid}
         style={{ color: hinweis.fehler ? "var(--st-rot)" : "var(--text-muted)" }}>
      {hinweis.text}
    </div>
  );
}

function FField({ label, children }) {
  return (
    <div className="w-[calc(50%-4px)] sm:w-auto">
      <label className="block text-[10px] text-zinc-500 mb-1 uppercase tracking-wide">{label}</label>
      {children}
    </div>
  );
}

export default function Marktplatz() {
  const { buyer, ready, logout, refresh } = useBuyer();
  const nav = useNavigate();
  const [showAnfragen, setShowAnfragen] = useState(false);
  const [access, setAccess] = useState(null);
  // Rollenprüfung 22.09.2026 (RP-097/RP-347): Fehler beim Laden des Zugangs
  // wurde geschluckt — die Seite stand dauerhaft auf "Lädt…".
  const [accessFehler, setAccessFehler] = useState("");
  // RP-520: Zähler am Knopf "Meine Anfragen" (Gegenangebote, Annahmen …).
  const [neuigkeiten, setNeuigkeiten] = useState(0);
  const [items, setItems] = useState(null);
  // Rollenprüfung 22.09.2026 (RP-098 Nr. 9): Die Liste endete still nach 300
  // Fahrzeugen. Der Server meldet jetzt per X-Truncated, dass es weitere gibt.
  const [seite, setSeite] = useState(1);
  const [mehrDa, setMehrDa] = useState(false);
  const [mehrLaedt, setMehrLaedt] = useState(false);
  const letzteAbfrage = useRef("");
  const [q, setQ] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [sel, setSel] = useState(null);
  const EMPTY = { make: "", model: "", fuel: "", km_min: "", km_max: "", ps_min: "", ps_max: "", price_min: "", price_max: "" };
  const [filters, setFilters] = useState(EMPTY);
  const [sort, setSort] = useState("");
  const [tick, setTick] = useState(0);
  const [makes, setMakes] = useState([]);
  const [favs, setFavs] = useState(() => new Set());
  const [nurFavs, setNurFavs] = useState(false);
  // Handy (Befund Ahmad 10.09.2026): die Filterleiste nahm den ganzen
  // Bildschirm ein, bevor das erste Auto kam. Wie bei mobile.de: kompakte
  // Leiste, Filter erst auf Tipp. Auf grossen Bildschirmen wie bisher.
  const [filterOffen, setFilterOffen] = useState(false);
  const aktiveFilter = Object.values(filters || {}).filter((v) => v !== "" && v != null).length;
  // Haendler-Ansicht: alle Fahrzeuge EINES Haendlers + Profil (Logo,
  // Oeffnungszeiten, Telefon). Filter/Sortierung gelten dort weiter.
  const [dealerView, setDealerView] = useState(null);
  const apply = () => setTick((t) => t + 1);

  // Herz-Klick: merken/entfernen — optimistisch, Server bestaetigt.
  const toggleFav = async (e, id) => {
    e.stopPropagation();
    // Merken braucht ein Konto — der Marktplatz selbst ist offen.
    if (!buyer) {
      // Kontonummer (13.09.2026): Kaeuferkonten legt der Betreiber nach Anfrage an.
      toast.info("Zum Merken brauchst du einen kostenlosen Zugang");
      nav("/anfrage?art=kaeufer");
      return;
    }
    const was = favs.has(id);
    setFavs((f) => { const n = new Set(f); was ? n.delete(id) : n.add(id); return n; });
    try {
      const r = await buyerApi.post(`/marktplatz/favoriten/${id}`);
      if (r.data.favorit !== !was) {
        setFavs((f) => { const n = new Set(f); r.data.favorit ? n.add(id) : n.delete(id); return n; });
      }
      toast.success(r.data.favorit ? "Gemerkt" : "Aus Favoriten entfernt", { duration: 1200 });
    } catch (err) {
      setFavs((f) => { const n = new Set(f); was ? n.add(id) : n.delete(id); return n; });
      toast.error(errMsg(err));
    }
  };
  const models = makes.find((m) => m.name === filters.make)?.models || [];

  const openDealer = async (dealerId) => {
    if (!dealerId) return;
    setSel(null);
    try {
      const r = await buyerApi.get(`/marktplatz/haendler/${dealerId}`);
      // RP-534: "alle Fahrzeuge" dieses Händlers — der Favoriten-Filter blieb
      // sonst an, und es erschienen nur die gemerkten Autos.
      setNurFavs(false);
      setDealerView(r.data.profile);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) { toast.error(errMsg(e, "Händler-Seite konnte nicht geladen werden")); }
  };
  const closeDealer = () => setDealerView(null);

  const isBuyer = buyer?.role === "b2b_buyer";
  const active = access?.active;

  const loadAccess = useCallback(async () => {
    setAccessFehler("");
    try {
      const r = await buyerApi.get("/marktplatz/zugang");
      setAccess(r.data);
    } catch (e) {
      // RP-097/RP-347: nicht mehr schlucken. 401 erledigt der Abfänger in
      // BuyerContext (Abmelden + Grund); alles andere zeigt die Seite mit
      // "Erneut versuchen" statt endlos "Lädt…".
      if (e?.response?.status !== 401) {
        setAccessFehler(errMsg(e, "Dein Marktplatz-Zugang konnte nicht geladen werden"));
      }
    }
  }, []);

  const loadItems = useCallback(async () => {
    try {
      const p = new URLSearchParams();
      if (q.trim()) p.set("q", q.trim());
      // RP-501/RP-512: Zahlenfelder deutsch lesen ("150.000" km, "15.000" €)
      // statt roh zu senden; bei Unsinn Meldung statt stiller Fehlfilterung.
      const { params, fehler } = filterParameter(filters);
      if (fehler) { toast.error(fehler); return; }
      Object.entries(params).forEach(([k, val]) => p.set(k, val));
      const gefiltert = p.toString() !== "" || Boolean(dealerView?.id);
      if (sort) p.set("sort", sort);
      if (nurFavs) p.set("nur_favoriten", "1");
      if (dealerView?.id) p.set("dealer", dealerView.id);
      const qs = p.toString();
      const r = await buyerApi.get(`/marktplatz/listings${qs ? `?${qs}` : ""}`);
      setItems(r.data);
      // RP-098 Nr. 9: "Weitere laden" fragt genau diese Suche weiter ab
      // (nicht die inzwischen getippten, noch nicht angewendeten Filter).
      letzteAbfrage.current = qs;
      setSeite(1);
      setMehrDa(r.headers?.["x-truncated"] === "1");
      // RP-098 Nr. 3: ohne Anmeldung gibt es keine Katalog-Markenliste —
      // Marken/Modelle aus der ungefilterten Liste bilden.
      if (!buyer && !gefiltert) setMakes(markenAusInseraten(r.data));
    } catch (e) {
      const st = e?.response?.status;
      if (st === 402) setAccess((a) => ({ ...(a || {}), active: false }));
      // Bezahlmodus (MARKTPLATZ_KOSTENLOS=false): ohne Anmeldung ist nichts
      // öffentlich — dann wie früher zur Anmeldung.
      else if (st === 401 && !buyer) nav("/markt/login");
      else toast.error(errMsg(e, "Fahrzeuge konnten nicht geladen werden"));
    }
  }, [q, filters, sort, nurFavs, dealerView, buyer, nav]);

  const mehrLaden = async () => {
    if (mehrLaedt) return;
    const p = new URLSearchParams(letzteAbfrage.current);
    p.set("page", String(seite + 1));
    setMehrLaedt(true);
    try {
      const r = await buyerApi.get(`/marktplatz/listings?${p.toString()}`);
      setItems((alt) => listeAnhaengen(alt, r.data));
      setSeite((s) => s + 1);
      setMehrDa(r.headers?.["x-truncated"] === "1");
    } catch (e) {
      toast.error(errMsg(e, "Weitere Fahrzeuge konnten nicht geladen werden"));
    } finally {
      setMehrLaedt(false);
    }
  };

  // Einladungslink eines BESTANDS-Kaeufers einloesen (Review 09/2026):
  // /markt?invite=<token> — einmalig, Parameter danach aus der URL nehmen.
  const [sp, setSp] = useSearchParams();
  const inviteToken = sp.get("invite") || "";
  const redeemed = useRef(false);
  useEffect(() => {
    // RP-511: auch eine auf diesem Gerät gemerkte Einladung (Partner ohne
    // Konto, der über /anfrage kam und sich z. B. über /login angemeldet hat).
    const einladung = inviteToken || (buyer ? gemerkteEinladung() : "");
    if (!buyer || !einladung || redeemed.current) return;
    redeemed.current = true;
    buyerApi.post(`/invites/${encodeURIComponent(einladung)}/redeem`)
      .then((r) => {
        einladungVergessen();
        toast.success(`Netzwerk beigetreten: ${r.data.dealer}`, { duration: 6000 });
        setTick((t) => t + 1);       // Listings neu laden (Netzwerkpreise!)
      })
      .catch((e) => {
        const st = e?.response?.status;
        if (st && st < 500) einladungVergessen();
        toast.error(errMsg(e, "Einladung konnte nicht eingelöst werden"));
      })
      .finally(() => {
        if (!inviteToken) return;
        const neu = new URLSearchParams(sp);
        neu.delete("invite");
        setSp(neu, { replace: true });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buyer, inviteToken]);

  useEffect(() => {
    // Rollenprüfung 22.09.2026 (RP-098 Nr. 3 / RP-348): Besucher ohne
    // Anmeldung wurden hier immer zur Anmeldung geschickt — obwohl der
    // Marktplatz seit dem Beschluss 09/2026 öffentlich ist (Server:
    // marktplatz_besucher) und Kopfzeile, Merken und Anfrage den Fall "ohne
    // Konto" längst kennen. Jetzt sehen sie die öffentlichen Fahrzeuge; nur
    // ein Einladungslink führt weiter zur Anmeldung (dort wird er eingelöst).
    if (ready && !buyer && inviteToken) {
      nav(`/markt/login?invite=${encodeURIComponent(inviteToken)}`);
    }
  }, [ready, buyer, nav, inviteToken]);
  useEffect(() => { if (buyer) loadAccess(); }, [buyer, loadAccess]);
  useEffect(() => {
    if (!buyer) return;
    // RP-099/RP-349: Fehler nicht mehr schlucken — ohne Markenliste bleiben
    // die Filter leer, ohne Merkliste fehlen die Herzen; beides sagen.
    ladeMakes(buyerApi).then(setMakes)
      .catch((e) => toast.error(errMsg(e, "Markenliste konnte nicht geladen werden — Filter nach Marke fehlt")));
    buyerApi.get("/marktplatz/favoriten")
      .then((r) => setFavs(new Set(r.data.listing_ids || [])))
      .catch((e) => {
        if (e?.response?.status !== 401) toast.error(errMsg(e, "Deine Merkliste konnte nicht geladen werden"));
      });
  }, [buyer]);
  // Nur bei Zugangs-Freischaltung oder explizitem Anwenden neu laden (tick),
  // NICHT bei jedem Tastendruck in den Filterfeldern.
  // RP-098 Nr. 3: ohne Anmeldung (und ohne Einladungslink) die öffentliche Liste.
  const oeffentlich = ready && !buyer && !inviteToken;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (active || oeffentlich) loadItems(); }, [active, oeffentlich, tick, nurFavs, dealerView]);

  // RP-520: Der Käufer erfuhr nie von Gegenangebot, Annahme oder Ablehnung.
  // Zähler beim Laden und jede Minute (nur bei sichtbarer Seite); als gesehen
  // gilt der Stand der zuletzt in "Meine Anfragen" angezeigten Liste.
  const neuigkeitenLaden = useCallback(async () => {
    try {
      const seit = anfragenGesehen();
      const r = await buyerApi.get("/buyer/interessen/zaehler", { params: seit ? { seit } : {} });
      setNeuigkeiten(neuigkeitenZahl(r.data));
    } catch { /* Zähler ist Zusatz — die Liste selbst meldet Fehler */ }
  }, []);
  const gesperrt = Boolean(access?.gesperrt);
  useEffect(() => {
    if (!isBuyer || access === null || gesperrt) return undefined;
    neuigkeitenLaden();
    const id = window.setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState !== "hidden") neuigkeitenLaden();
    }, NEUIGKEITEN_MS);
    return () => window.clearInterval(id);
  }, [isBuyer, access, gesperrt, neuigkeitenLaden]);
  // Stand der VORIGEN Anzeige — damit "Meine Anfragen" markieren kann, was neu ist.
  // Rollenprüfung 22.09.2026 (Review): Öffnen und Schließen merken NICHT mehr
  // "jetzt" (Geräteuhr) als gesehen — eine Annahme/Ablehnung, die bei offenem
  // Fenster kam, galt sonst als gesehen, ohne je gezeigt worden zu sein. Den
  // Stand merkt MeineAnfragen nach jedem erfolgreichen Laden (anfragenStand:
  // jüngstes updated_at der angezeigten Liste, Serverzeit).
  const [gesehenVorher, setGesehenVorher] = useState("");
  const anfragenOeffnen = () => {
    setGesehenVorher(anfragenGesehen());
    setNeuigkeiten(0);
    setShowAnfragen(true);
  };
  const anfragenSchliessen = () => {
    setShowAnfragen(false);
    neuigkeitenLaden();
  };

  // RP-531: Nach einer beendeten Sitzung (401 mitten im Senden) den
  // gesicherten Entwurf wiederherstellen: das Fahrzeug bzw. "Meine Anfragen"
  // öffnen, Betrag und Nachricht stehen wieder im Formular.
  // Rollenprüfung 22.09.2026 (Review): nur noch EINMAL je gesichertem Entwurf.
  // Das Formular übernimmt den Entwurf und löscht ihn aus dem Speicher
  // (InteresseForm/MeineAnfragen); jeder Fehler außer 401 löscht ihn beim
  // Senden. Vorher öffnete jeder neue Seitenaufbau 12 Stunden lang wieder
  // das Fahrzeug bzw. "Meine Anfragen" — auch nach einem 409 oder wenn der
  // Käufer bewusst aufgegeben hatte.
  const entwurfGeoeffnet = useRef(false);
  useEffect(() => {
    if (entwurfGeoeffnet.current || access === null) return;
    const anfrage = entwurfLesen("anfrage");
    const gegen = entwurfLesen("gegenangebot");
    if (anfrage) {
      if (!items) return;                    // erst die Liste, dann das Fahrzeug öffnen
      const v = items.find((x) => x.id === anfrage.listing_id);
      entwurfGeoeffnet.current = true;
      if (v) {
        setSel(v);
        toast.info("Deine angefangene Anfrage wurde wiederhergestellt — bitte prüfen und senden.");
      } else {
        // Fahrzeug nicht (mehr) in der Liste: nicht bei jedem Aufbau erneut suchen.
        entwurfLoeschen();
      }
    } else if (gegen) {
      // "Meine Anfragen" geht auch ohne aktiven Zugang (RP-510) — nicht auf die Liste warten.
      entwurfGeoeffnet.current = true;
      setGesehenVorher(anfragenGesehen());
      setShowAnfragen(true);
    }
  }, [items, access]);

  const requestAccess = async () => {
    setRequesting(true);
    try {
      const r = await buyerApi.post("/buyer/zugang-anfrage");
      toast.success(r.data.hinweis || "Anfrage gesendet");
    } catch (e) { toast.error(errMsg(e)); }
    finally { setRequesting(false); }
  };
  // Rollenprüfung 22.09.2026 (RP-509): Ablaufdatum des bezahlten Zugangs und —
  // ab 7 Tagen vorher — "Verlängerung anfragen" (vorher gab es weder das
  // Datum noch einen Weg vor dem Ablauf). Im Kostenlos-Modus null.
  const ablauf = isBuyer ? zugangAblauf(access) : null;

  // 14.09.2026 (Entscheidung Ahmad): keine Online-Zahlung mehr — der Betreiber
  // stellt eine Rechnung und schaltet nach Zahlungseingang frei.

  const Header = () => (
    <div className="glass-nav sticky top-0 z-10 px-4 sm:px-6 py-3 flex items-center justify-between">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center text-white"
             style={{ background: "var(--accent-red)" }}><Store size={16} /></div>
        <div className="font-black tracking-tight text-white">B2B-MARKTPLATZ</div>
      </div>
      <div className="flex items-center gap-3">
        {buyer ? (
          <>
            <span className="text-xs text-zinc-500 hidden sm:block">{buyer?.company_name}</span>
            {ablauf && (
              <span data-testid="markt-zugang-bis"
                    className={`text-xs ${ablauf.bald ? "text-amber-400" : "text-zinc-500 hidden sm:block"}`}>
                Zugang bis {ablauf.bis}
              </span>
            )}
            {ablauf?.bald && (
              <button onClick={requestAccess} disabled={requesting} data-testid="markt-verlaengern"
                      className="text-xs rounded-lg px-2.5 py-1 border text-zinc-200 hover:text-white disabled:opacity-50"
                      style={{ borderColor: "var(--border-default)" }}>
                {requesting ? "Wird gesendet…" : "Verlängerung anfragen"}
              </button>
            )}
            <button onClick={() => { logout(); nav("/markt/login"); }}
                    className="text-zinc-400 hover:text-white inline-flex items-center gap-1.5 text-sm">
              <LogOut size={16} /> Abmelden
            </button>
          </>
        ) : (
          <>
            <button onClick={() => nav("/markt/login")} data-testid="markt-anmelden"
                    className="text-zinc-400 hover:text-white text-sm">Anmelden</button>
            <button onClick={() => nav("/anfrage?art=kaeufer")} data-testid="markt-anfrage"
                    className="rounded-lg px-3 py-1.5 text-sm font-semibold text-white"
                    style={{ background: "var(--accent-red)" }}>Zugang anfragen</button>
          </>
        )}
      </div>
    </div>
  );

  if (buyer && access === null && accessFehler) {
    // RP-097/RP-347: Fehler sichtbar, mit neuem Versuch — nicht endlos "Lädt…".
    return (
      <div className="min-h-screen flex items-center justify-center p-6" style={{ background: "var(--bg-app)", color: "var(--text-primary)" }}>
        <div className="max-w-sm text-center" role="alert" data-testid="markt-zugang-fehler">
          <div className="font-semibold">Der Marktplatz konnte nicht geladen werden</div>
          <div className="mt-2 text-sm text-zinc-400">{accessFehler}</div>
          <button type="button" onClick={loadAccess} data-testid="markt-zugang-erneut"
                  className="mt-5 rounded-xl px-5 py-2.5 text-sm font-semibold text-white"
                  style={{ background: "var(--accent-red)" }}>
            Erneut versuchen
          </button>
        </div>
      </div>
    );
  }
  if (!ready || (buyer && access === null)) {
    return <div className="min-h-screen flex items-center justify-center text-zinc-500" style={{ background: "var(--bg-app)" }}>Lädt…</div>;
  }
  // Ohne Anmeldung sichtbar (09/2026): oeffentlich veroeffentlichte
  // Fahrzeuge. Merken, Anfragen und Netzwerk-Bestand bleiben angemeldeten
  // Zwischenhaendlern vorbehalten.

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-app)", color: "var(--text-primary)" }}
         data-testid="markt-page">
      <Header />
      <div className="px-4 sm:px-6 lg:px-10 py-6 max-w-7xl mx-auto">
        {/* RP-510: vom Betreiber gesperrt — eigener Text statt Preis-Paywall
            ("0,00 € / 30 Tage – per Rechnung anfragen" passte nicht). */}
        {isBuyer && !active && gesperrt ? (
          <div className="max-w-lg mx-auto text-center py-16" data-testid="markt-gesperrt">
            <div className="w-14 h-14 rounded-2xl mx-auto flex items-center justify-center mb-5"
                 style={{ background: "rgba(255,59,48,0.12)", border: "1px solid rgba(255,59,48,0.3)" }}>
              <Lock size={26} className="text-red-400" />
            </div>
            <h1 className="font-display font-black text-3xl tracking-tighter">Zugang gesperrt</h1>
            <p className="text-zinc-400 mt-3">
              Dein Marktplatz-Zugang wurde vom Betreiber gesperrt. Bitte wende dich an den Betreiber.
            </p>
          </div>
        ) : isBuyer && !active ? (
          <div className="max-w-lg mx-auto text-center py-16">
            <div className="w-14 h-14 rounded-2xl mx-auto flex items-center justify-center mb-5"
                 style={{ background: "rgba(255,59,48,0.12)", border: "1px solid rgba(255,59,48,0.3)" }}>
              <Lock size={26} className="text-red-400" />
            </div>
            <h1 className="font-display font-black text-3xl tracking-tighter">Zugang erforderlich</h1>
            <p className="text-zinc-400 mt-3">
              Um die zum Verkauf angebotenen Fahrzeuge zu sehen, benötigst du einen
              aktiven Marktplatz-Zugang.
            </p>
            <div className="mt-6 rounded-2xl p-6 inline-block"
                 style={{ background: "var(--wa-03)", border: "1px solid var(--wa-08)" }}>
              <div className="text-4xl font-black tabular-nums">
                {Number(access?.price ?? 20).toLocaleString("de-DE", { minimumFractionDigits: 2 })} €
                <span className="text-base font-normal text-zinc-500"> / 30 Tage</span>
              </div>
              <button onClick={requestAccess} disabled={requesting} data-testid="markt-access-request"
                      className="mt-5 w-full rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                      style={{ background: "var(--accent-red)" }}>
                {requesting ? "Wird gesendet…" : "Zugang per Rechnung anfragen"}
              </button>
              <p className="mt-3 text-[11px] text-zinc-600">
                Der Zugang wird per Rechnung abgerechnet — der Betreiber schaltet nach Zahlungseingang für 30 Tage frei.
              </p>
            </div>
            {/* RP-510: laufende Anfragen und Reservierungen bleiben auch ohne
                aktiven Zugang sichtbar — ablehnen und zurückziehen gehen weiter. */}
            <div className="mt-6">
              <button onClick={anfragenOeffnen} data-testid="meine-anfragen-paywall"
                      className="h-10 px-4 rounded-xl border text-sm inline-flex items-center gap-1.5 text-zinc-300 hover:text-white"
                      style={{ borderColor: "var(--border-default)" }}>
                <Inbox size={14} /> Meine Anfragen{neuigkeiten ? ` (${neuigkeiten})` : ""}
              </button>
            </div>
          </div>
        ) : (
          <>
            {/* ---- Haendler-Seite: Profilkarte (Logo, Kontakt, Zeiten) ---- */}
            {dealerView && (
              <div className="mb-6">
                <button onClick={closeDealer}
                        data-testid="dealer-back"
                        className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white mb-3">
                  <ChevronLeft size={14} /> Zurück zu allen Fahrzeugen
                </button>
                <div className="tactical-card p-5" data-testid="dealer-header">
                  <div className="flex flex-wrap items-start gap-4">
                    {dealerView.logo_url ? (
                      <div className="w-16 h-16 rounded-2xl overflow-hidden flex items-center justify-center shrink-0"
                           style={{ background: "var(--wa-06)" }}>
                        <img src={photoUrl(dealerView.logo_url)} alt="" className="w-full h-full object-contain" />
                      </div>
                    ) : (
                      <div className="w-16 h-16 rounded-2xl flex items-center justify-center shrink-0 text-white"
                           style={{ background: "var(--accent-red)" }}><Store size={26} /></div>
                    )}
                    <div className="flex-1 min-w-0">
                      <h1 className="font-display font-black text-2xl tracking-tighter">
                        {dealerView.company_name}
                      </h1>
                      <div className="mt-1 text-xs text-zinc-500 flex flex-wrap gap-x-4 gap-y-1">
                        {dealerView.contact_person && <span>Ansprechpartner: {dealerView.contact_person}</span>}
                        {(dealerView.city || dealerView.address) && (
                          <span className="inline-flex items-center gap-1">
                            <MapPin size={11} /> {dealerView.address || dealerView.city}
                          </span>
                        )}
                        <span>{dealerView.vehicle_count} Fahrzeug{dealerView.vehicle_count === 1 ? "" : "e"} im Angebot</span>
                      </div>
                      {dealerView.description && (
                        <p className="mt-2 text-sm text-zinc-400">{dealerView.description}</p>
                      )}
                      {dealerView.opening_hours && (
                        <div className="mt-2 flex items-start gap-1.5 text-xs text-zinc-400">
                          <Clock size={13} className="mt-0.5 shrink-0" />
                          <div className="whitespace-pre-line">{dealerView.opening_hours}</div>
                        </div>
                      )}
                    </div>
                    {dealerView.phone && (
                      <a href={`tel:${dealerView.phone.replace(/\s/g, "")}`}
                         className="inline-flex items-center gap-2 rounded-xl px-4 py-3 font-semibold text-white shrink-0"
                         style={{ background: "var(--accent-red)" }}>
                        <Phone size={16} /> {dealerView.phone}
                      </a>
                    )}
                  </div>
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="overline">{dealerView ? "Händler-Angebot" : "Marktplatz"}</div>
                <h1 className="font-display font-black text-2xl sm:text-3xl lg:text-4xl tracking-tighter mt-1">
                  {dealerView ? `Fahrzeuge von ${dealerView.company_name}` : "Angebotene Fahrzeuge"}
                </h1>
                {items && <div className="text-xs text-zinc-500 mt-1 sm:hidden">{items.length}{mehrDa ? "+" : ""} Fahrzeuge</div>}
              </div>
              <div className="flex items-center gap-2 w-full sm:w-auto overflow-x-auto pb-1 -mx-1 px-1"
                   data-testid="markt-toolbar">
                {items && <span className="text-xs text-zinc-500 hidden sm:inline">{items.length}{mehrDa ? "+" : ""} Fahrzeuge</span>}
                <button onClick={() => setFilterOffen((x) => !x)}
                        data-testid="filter-toggle"
                        className={`sm:hidden h-10 px-3.5 rounded-xl border text-sm inline-flex items-center gap-1.5 shrink-0 transition ${
                          filterOffen || aktiveFilter ? "text-white border-white/40 bg-white/5" : "text-zinc-400"}`}
                        style={filterOffen || aktiveFilter ? {} : { borderColor: "var(--border-default)" }}>
                  <SlidersHorizontal size={14} /> Filter{aktiveFilter ? ` (${aktiveFilter})` : ""}
                </button>
                <select value={sort}
                        onChange={(e) => { setSort(e.target.value); apply(); }}
                        className="h-10 px-3 rounded-xl border bg-[color:var(--bg-input-solid)] text-sm outline-none focus:border-white/40 shrink-0"
                        style={{ borderColor: "var(--border-default)" }}>
                  <option value="">Neueste zuerst</option>
                  <option value="preis_auf">Günstigste zuerst</option>
                  <option value="preis_ab">Teuerste zuerst</option>
                  <option value="km_auf">Wenigste km</option>
                  <option value="km_ab">Meiste km</option>
                </select>
                {/* RP-098 Nr. 3: "Meine Anfragen" und Favoriten nur mit Konto */}
                {buyer && (
                <button onClick={anfragenOeffnen}
                        data-testid="meine-anfragen-btn"
                        title={neuigkeiten ? "Neue Antworten der Händler" : undefined}
                        className="h-10 px-3.5 rounded-xl border text-sm inline-flex items-center gap-1.5 text-zinc-400 hover:text-white transition shrink-0 whitespace-nowrap"
                        style={{ borderColor: "var(--border-default)" }}>
                  <Inbox size={14} /> Meine Anfragen
                  {neuigkeiten > 0 && (
                    <span data-testid="meine-anfragen-zaehler"
                          className="ml-0.5 min-w-[18px] h-[18px] px-1 rounded-full text-[11px] font-bold text-white inline-flex items-center justify-center"
                          style={{ background: "var(--accent-red)" }}>
                      {neuigkeiten}
                    </span>
                  )}
                </button>
                )}
                {buyer && (
                <button onClick={() => setNurFavs((x) => !x)}
                        data-testid="filter-favoriten"
                        className={`h-10 px-3.5 rounded-xl border text-sm inline-flex items-center gap-1.5 transition shrink-0 whitespace-nowrap ${
                          nurFavs ? "text-red-400 border-red-500/50 bg-red-500/10" : "text-zinc-400"}`}
                        style={nurFavs ? {} : { borderColor: "var(--border-default)" }}>
                  <Heart size={14} fill={nurFavs ? "currentColor" : "none"} />
                  Favoriten{favs.size ? ` (${favs.size})` : ""}
                </button>
                )}
              </div>
            </div>

            {/* Filterleiste */}
            <div className={`${filterOffen ? "flex" : "hidden"} sm:flex mt-4 sm:mt-5 rounded-2xl p-3 flex-wrap items-end gap-2`}
                 data-testid="markt-filter"
                 style={{ background: "var(--wa-03)", border: "1px solid var(--wa-08)" }}>
              <FField label="Marke">
                <select value={filters.make}
                        onChange={(e) => { setFilters((s) => ({ ...s, make: e.target.value, model: "" })); apply(); }}
                        className={fInput + " bg-[color:var(--bg-input-solid)] w-full sm:w-40"} style={fStyle}>
                  <option value="">Alle Marken</option>
                  {makes.map((m) => <option key={m.id} value={m.name}>{m.name}</option>)}
                </select>
              </FField>
              <FField label="Modell">
                <select value={filters.model} disabled={!filters.make}
                        onChange={(e) => { setFilters((s) => ({ ...s, model: e.target.value })); apply(); }}
                        className={fInput + " bg-[color:var(--bg-input-solid)] w-full sm:w-40 disabled:opacity-40"} style={fStyle}>
                  <option value="">{filters.make ? "Alle Modelle" : "erst Marke wählen"}</option>
                  {models.map((mm) => <option key={mm.id} value={mm.name}>{mm.name}</option>)}
                </select>
              </FField>
              <FField label="Kraftstoff">
                <select value={filters.fuel}
                        onChange={(e) => { setFilters((s) => ({ ...s, fuel: e.target.value })); apply(); }}
                        className={fInput + " bg-[color:var(--bg-input-solid)] w-full sm:w-auto"} style={fStyle}>
                  <option value="">Alle</option>
                  <option value="Benzin">Benzin</option>
                  <option value="Diesel">Diesel</option>
                  <option value="Elektro">Elektro</option>
                  <option value="Hybrid">Hybrid</option>
                  {/* RP-506: "Gas" = Autogas (LPG) UND Erdgas (CNG); vorher traf "LPG" kein CNG. */}
                  <option value="Gas">LPG / Gas</option>
                </select>
              </FField>
              {/* RP-501/RP-512: Textfelder statt type="number" — im deutschen Browser
                  wurde aus "150.000" sonst 150. Gelesen wird in filterParameter(). */}
              <FField label="km von–bis">
                <div className="flex gap-1">
                  <input type="text" inputMode="numeric" value={filters.km_min} onChange={(e) => setFilters((s) => ({ ...s, km_min: e.target.value }))}
                         data-testid="filter-km-min"
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="von" className={fInput + " w-full sm:w-20 min-w-0"} style={fStyle} />
                  <input type="text" inputMode="numeric" value={filters.km_max} onChange={(e) => setFilters((s) => ({ ...s, km_max: e.target.value }))}
                         data-testid="filter-km-max"
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="bis" className={fInput + " w-full sm:w-20 min-w-0"} style={fStyle} />
                </div>
              </FField>
              <FField label="PS von–bis">
                <div className="flex gap-1">
                  <input type="text" inputMode="numeric" value={filters.ps_min} onChange={(e) => setFilters((s) => ({ ...s, ps_min: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="von" className={fInput + " w-full sm:w-16 min-w-0"} style={fStyle} />
                  <input type="text" inputMode="numeric" value={filters.ps_max} onChange={(e) => setFilters((s) => ({ ...s, ps_max: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="bis" className={fInput + " w-full sm:w-16 min-w-0"} style={fStyle} />
                </div>
              </FField>
              <FField label="Preis € von–bis">
                <div className="flex gap-1">
                  <input type="text" inputMode="decimal" value={filters.price_min} onChange={(e) => setFilters((s) => ({ ...s, price_min: e.target.value }))}
                         data-testid="filter-preis-min"
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="von" className={fInput + " w-full sm:w-24 min-w-0"} style={fStyle} />
                  <input type="text" inputMode="decimal" value={filters.price_max} onChange={(e) => setFilters((s) => ({ ...s, price_max: e.target.value }))}
                         data-testid="filter-preis-max"
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="bis" className={fInput + " w-full sm:w-24 min-w-0"} style={fStyle} />
                </div>
              </FField>
              <button onClick={() => { apply(); setFilterOffen(false); }}
                      data-testid="filter-anwenden"
                      className="h-10 sm:h-9 px-4 rounded-lg text-sm font-semibold text-white w-full sm:w-auto"
                      style={{ background: "var(--accent-red)" }}>Anwenden</button>
              <button onClick={() => { setFilters(EMPTY); setSort(""); setQ(""); apply(); }}
                      className="h-9 px-3 rounded-lg text-sm text-zinc-400 hover:text-white w-full sm:w-auto">Zurücksetzen</button>
            </div>

            <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {items === null && <div className="col-span-full text-zinc-500 text-sm">Lädt…</div>}
              {items && items.length === 0 && (
                <div className="col-span-full text-center py-16 text-zinc-500 text-sm">
                  Aktuell sind keine Fahrzeuge für dich sichtbar.
                </div>
              )}
              {items && items.map((v) => {
                const d = v.data || {};
                const img = photoUrl((v.photos || [])[0]);
                const phone = v.dealer?.phone;
                const { titel, unterzeile } = fahrzeugTitel(v);
                const km = kmAnzeige(d.mileage);
                return (
                  <div key={v.id} onClick={() => setSel(v)}
                       className="tactical-card overflow-hidden flex flex-col cursor-pointer hover:border-white/25 transition"
                       data-testid={`markt-${v.id}`}>
                    <div className="h-52 sm:h-44 overflow-hidden bg-zinc-900 relative">
                      {img ? <img src={img} alt="" loading="lazy" referrerPolicy="no-referrer"
                                  className="w-full h-full object-cover"
                                  onError={(e) => { e.currentTarget.style.display = "none"; }} />
                           : <div className="w-full h-full flex items-center justify-center text-zinc-700 text-xs">kein Foto</div>}
                      <button onClick={(e) => toggleFav(e, v.id)}
                              data-testid={`fav-${v.id}`}
                              title={favs.has(v.id) ? "Aus Favoriten entfernen" : "Fahrzeug merken"}
                              className="absolute top-2 right-2 w-9 h-9 rounded-full flex items-center justify-center transition"
                              style={{ background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)" }}>
                        <Heart size={17}
                               className={favs.has(v.id) ? "text-red-500" : "text-white/80"}
                               fill={favs.has(v.id) ? "currentColor" : "none"} />
                      </button>
                    </div>
                    <div className="p-4 flex-1 flex flex-col">
                      {/* RP-505: der Titel, den der Händler pflegt (vorher nie sichtbar). */}
                      <div className="font-semibold text-lg sm:text-base leading-tight line-clamp-2">{titel}</div>
                      {unterzeile && <div className="text-xs text-zinc-500 line-clamp-1">{unterzeile}</div>}
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-400">
                        {d.first_registration && <span className="inline-flex items-center gap-1"><Calendar size={12} /> {d.first_registration}</span>}
                        {/* RP-507: leerer km-Stand erschien als "0 km". */}
                        {km && <span className="inline-flex items-center gap-1"><Gauge size={12} /> {km}</span>}
                        {d.fuel_label && <span className="inline-flex items-center gap-1"><Fuel size={12} /> {d.fuel_label}</span>}
                        {d.power_ps && <span>{d.power_ps} PS</span>}
                      </div>
                      <div className="mt-3 pt-3 border-t flex items-end justify-between" style={{ borderColor: "var(--border-default)" }}>
                        <div>
                          <div className="text-xl sm:text-lg font-black">{fmtEur(v.price)}</div>
                          <div className="text-[10px] text-zinc-500 inline-flex items-center gap-1">
                            <ShieldCheck size={11} /> {LEVEL_LABEL[v.price_level] || v.price_level}
                          </div>
                        </div>
                        <button onClick={(e) => { e.stopPropagation(); openDealer(v.dealer?.id); }}
                                data-testid={`dealer-link-${v.id}`}
                                title="Alle Fahrzeuge dieses Händlers ansehen"
                                className="text-right text-[11px] text-zinc-500 hover:text-white hover:underline">
                          {v.dealer?.company_name}<br />{v.dealer?.city}
                        </button>
                      </div>
                      {phone ? (
                        <a href={`tel:${phone.replace(/\s/g, "")}`} onClick={(e) => e.stopPropagation()}
                           className="mt-3 inline-flex items-center justify-center gap-2 rounded-xl py-2.5 text-sm font-semibold text-white"
                           style={{ background: "var(--accent-red)" }}>
                          <Phone size={15} /> {phone}
                        </a>
                      ) : (
                        <div className="mt-3 text-center text-[11px] text-zinc-600 py-2">Keine Telefonnummer hinterlegt</div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            {/* RP-098 Nr. 9: weitere Seite statt stiller 300er-Grenze */}
            {items && mehrDa && (
              <div className="mt-6 text-center">
                <button onClick={mehrLaden} disabled={mehrLaedt} data-testid="markt-mehr-laden"
                        className="h-10 px-5 rounded-xl border text-sm font-semibold text-zinc-200 hover:text-white disabled:opacity-50"
                        style={{ borderColor: "var(--border-default)" }}>
                  {mehrLaedt ? "Lädt…" : "Weitere Fahrzeuge laden"}
                </button>
              </div>
            )}
          </>
        )}
      </div>
      {/* RP-563: Impressum, Datenschutz und AGB auch im Marktplatz erreichbar. */}
      <RechtsLinks className="py-6" />

      {sel && <DetailModal v={sel} onClose={() => setSel(null)}
                           isFav={favs.has(sel.id)}
                           onFav={(e) => toggleFav(e, sel.id)}
                           onDealer={() => openDealer(sel.dealer?.id)} />}
      {showAnfragen && <MeineAnfragen onClose={anfragenSchliessen} gesehenVorher={gesehenVorher} />}
    </div>
  );
}

function DetailModal({ v, onClose, isFav, onFav, onDealer }) {
  const d = v.data || {};
  const phone = v.dealer?.phone;
  const photos = (v.photos || []).map(photoUrl).filter(Boolean);
  // Vom Händler nachträglich hochgeladene Bilder (z.B. Schäden) — separat
  // ausgewiesen "zum genauen Hinschauen".
  const dealerPhotos = (v.dealer_photos || []).map(photoUrl).filter(Boolean)
    .filter((u) => !photos.includes(u));
  const allPhotos = [...photos, ...dealerPhotos];
  const [lb, setLb] = useState(-1);          // Lightbox-Index (-1 = zu)
  const lbPrev = (e) => { e.stopPropagation(); setLb((i) => (i - 1 + allPhotos.length) % allPhotos.length); };
  const lbNext = (e) => { e.stopPropagation(); setLb((i) => (i + 1) % allPhotos.length); };
  // RP-504: "Unfallfrei: Ja" kam vorher aus der ungeprüften Portal-Angabe
  // accident_damaged=false, ohne dass der Händler etwas zugesichert hatte.
  // Jetzt nur die Angabe des Händlers; ein Portal-Unfallschaden nur als Hinweis.
  const unfall = unfallZeile(d);
  const { titel, unterzeile } = fahrzeugTitel(v);
  // Rollenprüfung 22.09.2026 (RP-519): bis wann das Inserat online ist — mit
  // ihm enden laufende Anfragen (der Server liefert es nur für veröffentlichte).
  const ablauf = inseratAblauf(v.laeuft_ab_am);
  const specs = [
    ["Erstzulassung", d.first_registration],
    ["Kilometerstand", kmAnzeige(d.mileage)],
    ["Kraftstoff", d.fuel_label],
    ["Getriebe", d.gearbox_label],
    ["Leistung", d.power_ps ? `${d.power_ps} PS` : null],
    ["Farbe", d.color],
    ["Vorbesitzer", d.previous_owners],
    ...(unfall ? [unfall] : []),
  ].filter(([, val]) => val != null && val !== "");

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.75)" }} onClick={onClose}>
      <div className="w-full sm:max-w-2xl max-h-[94vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)" }}
           onClick={(e) => e.stopPropagation()}>
        <div className="relative">
          {photos[0] ? (
            <img src={photos[0]} alt="" onClick={() => setLb(0)}
                 className="w-full h-56 sm:h-72 object-cover cursor-zoom-in" title="Zum Vergrößern klicken" />
          ) : <div className="w-full h-40 bg-zinc-900 flex items-center justify-center text-zinc-700 text-xs">kein Foto</div>}
          <button onClick={onClose} className="absolute top-3 right-3 w-9 h-9 rounded-full flex items-center justify-center text-white"
                  style={{ background: "rgba(0,0,0,0.5)" }}><X size={18} /></button>
          <button onClick={onFav}
                  data-testid="fav-modal"
                  title={isFav ? "Aus Favoriten entfernen" : "Fahrzeug merken"}
                  className="absolute top-3 right-14 w-9 h-9 rounded-full flex items-center justify-center"
                  style={{ background: "rgba(0,0,0,0.5)" }}>
            <Heart size={17} className={isFav ? "text-red-500" : "text-white/80"}
                   fill={isFav ? "currentColor" : "none"} />
          </button>
        </div>
        {photos.length > 1 && (
          <div className="flex gap-2 p-3 overflow-x-auto">
            {photos.slice(1, 40).map((u, i) => (
              <img key={i} src={u} alt="" onClick={() => setLb(i + 1)}
                   className="h-16 w-24 object-cover rounded-lg hover:opacity-80 cursor-zoom-in shrink-0"
                   title="Zum Vergrößern klicken" />
            ))}
          </div>
        )}
        <div className="p-5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-xl font-black tracking-tight">{titel}</h2>
              {unterzeile && <div className="text-sm text-zinc-500">{unterzeile}</div>}
            </div>
            <div className="text-right shrink-0">
              <div className="text-2xl font-black">{fmtEur(v.price)}</div>
              <div className="text-[10px] text-zinc-500 inline-flex items-center gap-1">
                <ShieldCheck size={11} /> {LEVEL_LABEL[v.price_level] || v.price_level}
              </div>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-2 text-sm">
            {specs.map(([k, val]) => (
              <div key={k}>
                <div className="text-[11px] text-zinc-500">{k}</div>
                <div>{val}</div>
              </div>
            ))}
          </div>
          {ablauf && (
            <div className="mt-3 text-xs inline-flex items-center gap-1.5" data-testid="detail-ablauf"
                 style={{ color: ablauf.bald ? "var(--tx-amber)" : "var(--text-muted)" }}>
              <Clock size={12} className="shrink-0" />
              {ablauf.abgelaufen
                ? "Inserat abgelaufen — es wird in Kürze entfernt."
                : `Inserat online bis ${ablauf.bis} — danach enden offene Anfragen.`}
            </div>
          )}

          {v.description && (
            <div className="mt-4 text-sm text-zinc-300 whitespace-pre-line">{v.description}</div>
          )}
          {(v.known_defects || []).length > 0 && (
            <div className="mt-4">
              <div className="text-[11px] text-zinc-500 mb-1">Bekannte Mängel</div>
              {v.known_defects.map((m, i) => <div key={i} className="text-amber-400 text-sm">• {m}</div>)}
            </div>
          )}

          {/* Weitere Bilder vom Händler (z.B. Schäden) — zum genauen Hinschauen */}
          {dealerPhotos.length > 0 && (
            <div className="mt-4">
              <div className="text-[11px] text-zinc-500 mb-2 inline-flex items-center gap-1.5">
                <Camera size={12} /> Weitere Bilder vom Händler ({dealerPhotos.length})
              </div>
              <div className="grid grid-cols-4 sm:grid-cols-6 gap-2">
                {dealerPhotos.map((u, i) => (
                  <img key={i} src={u} alt="" onClick={() => setLb(photos.length + i)}
                       className="aspect-square object-cover rounded-lg hover:opacity-80 cursor-zoom-in"
                       title="Zum Vergrößern klicken" />
                ))}
              </div>
            </div>
          )}

          {/* Händler-Kontakt */}
          <div className="mt-5 rounded-2xl p-4" style={{ background: "var(--wa-03)", border: "1px solid var(--wa-08)" }}>
            <div className="flex items-center gap-3">
              {v.dealer?.logo_url && (
                <div className="w-11 h-11 rounded-xl overflow-hidden flex items-center justify-center shrink-0"
                     style={{ background: "var(--wa-06)" }}>
                  <img src={photoUrl(v.dealer.logo_url)} alt="" className="w-full h-full object-contain" />
                </div>
              )}
              <div>
                <button onClick={onDealer}
                        data-testid="dealer-link-modal"
                        title="Alle Fahrzeuge dieses Händlers ansehen"
                        className="flex items-center gap-2 text-sm font-semibold hover:underline text-left">
                  <Store size={15} /> {v.dealer?.company_name}
                  <span className="text-[10px] font-normal text-zinc-500">alle Fahrzeuge ›</span>
                </button>
                <div className="mt-0.5 text-xs text-zinc-500 space-y-0.5">
                  {v.dealer?.contact_person && <div>Ansprechpartner: {v.dealer.contact_person}</div>}
                  {v.dealer?.city && <div className="inline-flex items-center gap-1"><MapPin size={11} /> {v.dealer.city}</div>}
                </div>
              </div>
            </div>
            {v.dealer?.opening_hours && (
              <div className="mt-3 flex items-start gap-1.5 text-xs text-zinc-400">
                <Clock size={13} className="mt-0.5 shrink-0" />
                <div className="whitespace-pre-line">{v.dealer.opening_hours}</div>
              </div>
            )}
            {phone ? (
              <a href={`tel:${phone.replace(/\s/g, "")}`}
                 className="mt-3 w-full inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white"
                 style={{ background: "var(--accent-red)" }}>
                <Phone size={17} /> {phone} — jetzt anrufen
              </a>
            ) : (
              <div className="mt-3 text-center text-xs text-zinc-600">Keine Telefonnummer hinterlegt.</div>
            )}
          </div>

          <InteresseForm v={v} />
        </div>
      </div>

      {/* Lightbox: Bild vergrößert, mit ‹ › durch ALLE Bilder blättern */}
      {lb >= 0 && allPhotos[lb] && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center"
             style={{ background: "rgba(0,0,0,0.92)" }}
             onClick={(e) => { e.stopPropagation(); setLb(-1); }}>
          <img src={allPhotos[lb]} alt=""
               onClick={(e) => e.stopPropagation()}
               className="max-h-[88vh] max-w-[92vw] object-contain rounded-lg" />
          {allPhotos.length > 1 && (
            <>
              <button onClick={lbPrev}
                      className="absolute left-3 sm:left-6 w-11 h-11 rounded-full flex items-center justify-center text-white"
                      style={{ background: "rgba(255,255,255,0.12)" }} title="Vorheriges Bild">
                <ChevronLeft size={26} />
              </button>
              <button onClick={lbNext}
                      className="absolute right-3 sm:right-6 w-11 h-11 rounded-full flex items-center justify-center text-white"
                      style={{ background: "rgba(255,255,255,0.12)" }} title="Nächstes Bild">
                <ChevronRight size={26} />
              </button>
            </>
          )}
          <button onClick={(e) => { e.stopPropagation(); setLb(-1); }}
                  className="absolute top-4 right-4 w-10 h-10 rounded-full flex items-center justify-center text-white"
                  style={{ background: "rgba(255,255,255,0.12)" }} title="Schließen">
            <X size={20} />
          </button>
          <div className="absolute bottom-4 text-xs text-zinc-400 px-3 py-1 rounded-full"
               style={{ background: "rgba(0,0,0,0.5)" }}>
            {lb + 1} / {allPhotos.length}
            {lb >= photos.length ? " · Weitere Bilder vom Händler" : ""}
          </div>
        </div>
      )}
    </div>
  );
}


/** Interesse / Angebot zu einem Inserat senden (Review 09/2026: der
 *  Backend-Endpunkt existierte, der Marktplatz bot nur den Telefon-Link). */
function InteresseForm({ v }) {
  // Anmeldezustand aus dem Kontext holen: dieses Bauteil steckt in
  // DetailModal und bekommt "buyer" nicht durchgereicht.
  const { buyer } = useBuyer();
  const nav = useNavigate();
  // RP-531: gesicherter Entwurf zu GENAU diesem Fahrzeug (Sitzung endete beim Senden)
  const [entwurf] = useState(() => {
    const e = entwurfLesen("anfrage");
    return e && e.listing_id === v.id ? e : null;
  });
  const [offen, setOffen] = useState(Boolean(entwurf));
  const [betrag, setBetrag] = useState(entwurf?.betrag || "");
  const [nachricht, setNachricht] = useState(entwurf?.nachricht || "");
  const [busy, setBusy] = useState(false);
  const [gesendet, setGesendet] = useState(false);
  // Rollenprüfung 22.09.2026 (Review): übernommen = verbraucht. Betrag und
  // Nachricht stehen jetzt im Formular; der Speicher darf das Fahrzeug beim
  // nächsten Seitenaufbau nicht erneut öffnen.
  useEffect(() => { if (entwurf) entwurfLoeschen(); }, [entwurf]);

  const senden = async () => {
    if (busy) return;
    // Anfragen brauchen ein Konto — Ansehen nicht.
    if (!buyer) {
      toast.info("Für eine Anfrage brauchst du einen kostenlosen Zugang");
      nav("/anfrage?art=kaeufer");
      return;
    }
    // RP-501: deutsch lesen ("12.500" = zwölftausendfünfhundert, vorher 12,50 €;
    // "20.900,00" fiel still weg) und den gelesenen Betrag bestätigen lassen.
    const { betrag: zahl, fehler } = betragPruefen(betrag, { optional: true });
    if (fehler) { toast.error(fehler); return; }
    if (zahl !== null && !window.confirm(angebotsFrage(zahl))) return;
    // RP-531: vor dem Senden sichern — endet die Sitzung genau jetzt (401,
    // Weiterleitung zur Anmeldung), stehen Betrag und Nachricht danach wieder da.
    entwurfMerken("anfrage", { listing_id: v.id, betrag, nachricht });
    setBusy(true);
    try {
      await buyerApi.post(`/marktplatz/listings/${v.id}/interesse`, {
        offer: zahl ?? undefined,
        message: nachricht,
      });
      entwurfLoeschen();
      setGesendet(true);
      toast.success("Anfrage gesendet — die Antwort des Händlers findest du unter „Meine Anfragen“");
    } catch (e) {
      // Rollenprüfung 22.09.2026 (Review): der Entwurf ist nur für die
      // beendete Sitzung da (401 → Weiterleitung zur Anmeldung). Bei jedem
      // anderen Fehler (409 "läuft bereits", 402, 404, Netz) bleibt die Seite
      // stehen und das Formular behält die Eingaben — Entwurf weg.
      if (e?.response?.status !== 401) entwurfLoeschen();
      toast.error(errMsg(e, "Anfrage konnte nicht gesendet werden"));
    } finally {
      setBusy(false);
    }
  };

  if (gesendet) {
    return (
      <div className="mt-3 rounded-2xl p-4 text-sm text-emerald-400 flex items-center gap-2"
           style={{ background: "rgba(52,199,89,0.08)", border: "1px solid rgba(52,199,89,0.25)" }}
           data-testid="interesse-gesendet">
        <Check size={16} /> Anfrage gesendet. Antworten findest du unter „Meine Anfragen".
      </div>
    );
  }
  return (
    <div className="mt-3 rounded-2xl p-4" style={{ background: "var(--wa-03)", border: "1px solid var(--wa-08)" }}>
      {!offen ? (
        <button onClick={() => setOffen(true)} data-testid={`interesse-btn-${v.id}`}
                className="w-full inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold border text-white hover:bg-white/5"
                style={{ borderColor: "var(--border-default)" }}>
          <Handshake size={17} /> Interesse / Angebot senden
        </button>
      ) : (
        <div className="space-y-2.5">
          <div className="text-sm font-semibold">Interesse / Angebot senden</div>
          <FField label="Dein Angebot in € (optional)">
            <input type="text" inputMode="decimal" value={betrag} onChange={(e) => setBetrag(e.target.value)}
                   data-testid="interesse-betrag"
                   aria-invalid={Boolean(betragHinweis(betrag)?.fehler)}
                   className={`${fInput} w-full`} style={fStyle} placeholder="z. B. 12.500" />
            <BetragVerstanden text={betrag} testid="interesse-betrag-verstanden" />
          </FField>
          <FField label="Nachricht (optional)">
            <textarea value={nachricht} onChange={(e) => setNachricht(e.target.value)}
                      maxLength={2000} rows={3} data-testid="interesse-nachricht"
                      className="w-full px-2.5 py-2 rounded-lg border bg-transparent text-sm outline-none focus:border-white/40"
                      style={fStyle} placeholder="Kurze Nachricht an den Händler…" />
          </FField>
          <button onClick={senden} disabled={busy} data-testid="interesse-senden"
                  className="w-full rounded-xl py-2.5 font-semibold text-white disabled:opacity-50"
                  style={{ background: "var(--accent-red)" }}>
            {busy ? "Wird gesendet…" : "Anfrage senden"}
          </button>
        </div>
      )}
    </div>
  );
}

/** Eigene Anfragen des Kaeufers samt Haendler-Antworten. Auf ein
 *  Gegenangebot kann hier geantwortet werden (annehmen/ablehnen).
 *  Rollenprüfung 22.09.2026: laufende Anfragen lassen sich ändern und
 *  zurückziehen (RP-502), der vereinbarte Preis steht groß da (RP-477), bei
 *  einer Reservierung stehen Händler und Kontakt dabei (RP-503), und ist das
 *  Auto für jemand anderen reserviert, sagt die Liste das (RP-478). */
function MeineAnfragen({ onClose, gesehenVorher = "" }) {
  const [items, setItems] = useState(null);
  const [ladeFehler, setLadeFehler] = useState("");
  const [busyId, setBusyId] = useState(null);
  // Kaeufer-Gegenangebot bzw. eigenes Angebot aendern (09/2026, RP-502).
  // RP-531: ein gesicherter Entwurf (Sitzung endete beim Senden) steht wieder da.
  const [entwurf] = useState(() => entwurfLesen("gegenangebot"));
  const [gegenFor, setGegenFor] = useState(entwurf?.interest_id || null);
  const [gegenVal, setGegenVal] = useState(entwurf?.betrag || "");
  // Rollenprüfung 22.09.2026 (Review): übernommen = verbraucht (wie InteresseForm).
  useEffect(() => { if (entwurf) entwurfLoeschen(); }, [entwurf]);

  const load = useCallback(() => {
    buyerApi.get("/buyer/interessen")
      .then((r) => {
        const liste = Array.isArray(r.data) ? r.data : [];
        setItems(liste);
        setLadeFehler("");
        // Rollenprüfung 22.09.2026 (Review zu RP-520): gesehen ist, was diese
        // Liste zeigt — ihr jüngstes updated_at (Serverzeit), nicht der
        // Zeitpunkt des Öffnens/Schließens. Ein Ladefehler merkt nichts.
        const stand = anfragenStand(liste);
        if (stand) anfragenGesehenMerken(stand);
      })
      // Ein Ladefehler ist nicht "keine Anfragen" (sonst verpasst der Käufer Antworten).
      .catch((e) => { setLadeFehler(errMsg(e, "Deine Anfragen konnten nicht geladen werden")); setItems((alt) => alt || []); });
  }, []);
  useEffect(() => { load(); }, [load]);

  const ERFOLG = {
    annehmen: "Gegenangebot angenommen — das Fahrzeug ist für dich reserviert",
    ablehnen: "Gegenangebot abgelehnt",
    gegenangebot: "Dein Angebot wurde an den Händler gesendet",
    zurueckziehen: "Anfrage zurückgezogen",
  };
  const antwort = async (it, action, extra = {}) => {
    if (busyId) return;
    setBusyId(it.id);
    try {
      await buyerApi.post(`/interessen/${it.id}/kaeufer-antwort`, { action, message: "", ...extra });
      entwurfLoeschen();
      toast.success(ERFOLG[action] || "Gespeichert");
      setGegenFor(null); setGegenVal("");
      load();
    } catch (e) {
      // Rollenprüfung 22.09.2026 (Review): Entwurf nur für 401 (beendete
      // Sitzung) behalten — sonst öffnete er "Meine Anfragen" bei jedem
      // Seitenaufbau erneut (z. B. nach 409 "Händler hat inzwischen geantwortet").
      if (e?.response?.status !== 401) entwurfLoeschen();
      toast.error(errMsg(e, "Antwort fehlgeschlagen"));
      // RP-495: hat der Händler inzwischen geändert, den neuen Stand zeigen.
      if (e?.response?.status === 409) load();
    } finally {
      setBusyId(null);
    }
  };
  const gegenSenden = (it) => {
    // RP-501: deutsch lesen ("12.500" war vorher 12,50 €) und bestätigen lassen.
    const { betrag, fehler } = betragPruefen(gegenVal);
    if (fehler) { toast.error(fehler); return; }
    const art = it.status === "gegenangebot" ? "Gegenangebot" : "neues Angebot";
    if (!window.confirm(angebotsFrage(betrag, art))) return;
    entwurfMerken("gegenangebot", { interest_id: it.id, betrag: gegenVal });
    antwort(it, "gegenangebot", { counter_offer: betrag });
  };
  const zurueckziehen = (it, titel) => {
    if (!window.confirm(`Anfrage zu „${titel}“ zurückziehen?\n\nDer Händler sieht, dass du sie beendet hast.`)) return;
    antwort(it, "zurueckziehen");
  };
  const formularUmschalten = (it) => {
    setGegenFor(gegenFor === it.id ? null : it.id);
    setGegenVal("");
  };

  const STATUS_FARBE = {
    offen: "var(--tx-amber)", gegenangebot: "var(--tx-blau)",
    gegenangebot_kaeufer: "var(--st-blau)", akzeptiert: "var(--st-gruen)",
    abgelehnt: "var(--text-dim)",
  };
  const laufend = (it) => ["offen", "gegenangebot", "gegenangebot_kaeufer"].includes(it.status);
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.7)" }} onClick={onClose}>
      <div className="w-full sm:max-w-2xl max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl p-5"
           style={{ background: "var(--bg-elevated)", color: "var(--text-primary)" }} onClick={(e) => e.stopPropagation()}
           data-testid="meine-anfragen-modal">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="overline">Marktplatz</div>
            <div className="font-display font-black text-2xl tracking-tighter">Meine Anfragen</div>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white" data-testid="close-meine-anfragen">
            <X size={20} />
          </button>
        </div>
        {ladeFehler && (
          <div className="mb-3 rounded-xl border px-3 py-2.5 text-sm flex flex-wrap items-center gap-3" role="alert"
               data-testid="meine-anfragen-fehler"
               style={{ borderColor: "#ef444455", background: "#ef444414" }}>
            <span className="flex-1 min-w-0">{ladeFehler}</span>
            <button type="button" onClick={load} className="rounded-lg px-3 py-1.5 text-xs border font-semibold"
                    style={{ borderColor: "var(--border-default)" }}>Erneut versuchen</button>
          </div>
        )}
        {items === null ? (
          <div className="text-sm text-zinc-500 py-8 text-center">Lädt…</div>
        ) : items.length === 0 ? (
          ladeFehler ? null : (
            <div className="text-sm text-zinc-500 py-8 text-center">
              Noch keine Anfragen — öffne ein Fahrzeug und sende „Interesse / Angebot".
            </div>
          )
        ) : (
          <div className="space-y-3">
            {items.map((it) => {
              const titel = it.inserat?.title || it.listing_title || "Inserat";
              const foto = photoUrl(it.inserat?.foto);
              const kontakt = it.haendler?.kontakt;
              const reserviertFremd = Boolean(it.anderweitig_reserviert);
              const beendet = beendetText(it);
              // RP-519: Ablauf des Inserats unter laufenden Anfragen.
              const ablaufText = anfrageAblaufText(it);
              const neu = istNeu(it, gesehenVorher);
              const eigenesAngebot = it.status === "gegenangebot_kaeufer" ? it.buyer_counter_offer : it.offer;
              return (
                <div key={it.id} className="rounded-2xl p-4" data-testid={`meine-anfrage-${it.id}`}
                     style={{ background: "var(--wa-03)", border: `1px solid ${neu ? "var(--accent-red)" : "var(--wa-08)"}` }}>
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex items-start gap-3">
                      {foto && (
                        <img src={foto} alt="" className="w-14 h-14 rounded-lg object-cover shrink-0"
                             onError={(e) => { e.currentTarget.style.display = "none"; }} />
                      )}
                      <div className="min-w-0">
                        <div className="font-semibold truncate">{titel}</div>
                        <div className="text-xs text-zinc-500 mt-0.5">
                          {it.haendler?.company_name && <>{it.haendler.company_name}{" · "}</>}
                          {it.offer != null ? `Dein Angebot: ${fmtEur(it.offer)}` : "Ohne Preisangebot"}
                          {" · "}{new Date(it.created_at).toLocaleDateString("de-DE")}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {neu && (
                        <span className="text-[10px] font-bold px-2 py-0.5 rounded-full text-white"
                              data-testid={`meine-anfrage-neu-${it.id}`}
                              style={{ background: "var(--accent-red)" }}>Neu</span>
                      )}
                      <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
                            style={{ "--st": STATUS_FARBE[it.status] || "var(--text-dim)",
                                     color: "var(--st)",
                                     border: "1px solid color-mix(in srgb, var(--st) 40%, transparent)",
                                     background: "color-mix(in srgb, var(--st) 12%, transparent)" }}>
                        {STATUS_TEXT[it.status] || it.status}
                      </span>
                    </div>
                  </div>

                  {reserviertFremd && laufend(it) && (
                    <div className="mt-3 rounded-xl px-3 py-2 text-[12.5px]" data-testid={`anderweitig-reserviert-${it.id}`}
                         style={{ background: "rgba(245,158,11,0.10)", border: "1px solid rgba(245,158,11,0.35)", color: "var(--tx-amber)" }}>
                      Das Fahrzeug ist inzwischen für einen anderen Käufer reserviert — deine Anfrage ruht.
                      Hebt der Händler die Reservierung auf, kannst du hier weiter verhandeln.
                    </div>
                  )}

                  {it.status === "offen" && !reserviertFremd && (
                    <div className="mt-3 text-[12.5px]" style={{ color: "var(--text-muted)" }}>
                      Deine Anfrage liegt beim Händler — er kann annehmen, ablehnen oder ein Gegenangebot schreiben.
                    </div>
                  )}
                  {it.status === "gegenangebot_kaeufer" && !reserviertFremd && (
                    <div className="mt-3 text-[12.5px]" style={{ color: "var(--text-muted)" }}
                         data-testid={`kaeufer-wartet-${it.id}`}>
                      Dein Gegenangebot ({fmtEur(it.buyer_counter_offer)}) liegt beim Händler — er kann annehmen, ablehnen oder ein neues Angebot schreiben.
                    </div>
                  )}
                  {ablaufText && (
                    <div className="mt-2 text-[12px] flex items-start gap-1.5" data-testid={`meine-anfrage-ablauf-${it.id}`}
                         style={{ color: "var(--text-muted)" }}>
                      <Clock size={12} className="mt-0.5 shrink-0" /> <span>{ablaufText}</span>
                    </div>
                  )}
                  {/* RP-502: laufende eigene Anfrage ändern oder zurückziehen */}
                  {["offen", "gegenangebot_kaeufer"].includes(it.status) && (
                    <div className="mt-2.5 flex flex-wrap gap-2">
                      {!reserviertFremd && (
                        <button onClick={() => formularUmschalten(it)} disabled={busyId === it.id}
                                data-testid={`anfrage-aendern-${it.id}`}
                                className="rounded-lg px-3 py-1.5 text-sm font-semibold border text-sky-300 disabled:opacity-50"
                                style={{ borderColor: "rgba(59,130,246,0.5)" }}>
                          {eigenesAngebot != null ? "Angebot ändern" : "Angebot machen"}
                        </button>
                      )}
                      <button onClick={() => zurueckziehen(it, titel)} disabled={busyId === it.id}
                              data-testid={`anfrage-zurueckziehen-${it.id}`}
                              className="rounded-lg px-3 py-1.5 text-sm border text-zinc-300 disabled:opacity-50"
                              style={{ borderColor: "var(--border-default)" }}>
                        Zurückziehen
                      </button>
                    </div>
                  )}
                  {it.status === "gegenangebot" && (
                    <div className="mt-3 rounded-xl p-3" style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.3)" }}>
                      <div className="text-sm">
                        Gegenangebot des Händlers:{" "}
                        <span className="font-bold text-sky-400">{fmtEur(it.counter_offer)}</span>
                      </div>
                      <div className="mt-2.5 flex gap-2">
                        {!reserviertFremd && (
                          <button onClick={() => antwort(it, "annehmen", { erwarteter_betrag: it.counter_offer })}
                                  disabled={busyId === it.id}
                                  data-testid={`gegenangebot-annehmen-${it.id}`}
                                  className="flex-1 rounded-lg py-2 text-sm font-semibold text-white disabled:opacity-50"
                                  style={{ background: "var(--st-gruen)" }}>
                            Annehmen
                          </button>
                        )}
                        <button onClick={() => antwort(it, "ablehnen")} disabled={busyId === it.id}
                                data-testid={`gegenangebot-ablehnen-${it.id}`}
                                className="flex-1 rounded-lg py-2 text-sm font-semibold border text-zinc-300 disabled:opacity-50"
                                style={{ borderColor: "var(--border-default)" }}>
                          Ablehnen
                        </button>
                        {!reserviertFremd && (
                          <button onClick={() => formularUmschalten(it)} disabled={busyId === it.id}
                                  data-testid={`kaeufer-gegenangebot-${it.id}`}
                                  className="flex-1 rounded-lg py-2 text-sm font-semibold border text-sky-300 disabled:opacity-50"
                                  style={{ borderColor: "rgba(59,130,246,0.5)" }}>
                            Gegenangebot
                          </button>
                        )}
                      </div>
                    </div>
                  )}
                  {gegenFor === it.id && laufend(it) && !reserviertFremd && (
                    <div className="mt-2.5 flex gap-2 items-start">
                      <div>
                        <input type="text" inputMode="decimal" value={gegenVal} onChange={(e) => setGegenVal(e.target.value)}
                               placeholder="Dein Preis in €, z. B. 12.500" autoFocus
                               data-testid={`kaeufer-gegenangebot-betrag-${it.id}`}
                               aria-invalid={Boolean(betragHinweis(gegenVal)?.fehler)}
                               onKeyDown={(e) => e.key === "Enter" && gegenSenden(it)}
                               className="h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none w-48"
                               style={{ borderColor: "var(--border-default)" }} />
                        <BetragVerstanden text={gegenVal} testid={`kaeufer-gegenangebot-verstanden-${it.id}`} />
                      </div>
                      <button onClick={() => gegenSenden(it)}
                              disabled={busyId === it.id}
                              data-testid={`kaeufer-gegenangebot-senden-${it.id}`}
                              className="h-9 rounded-lg px-4 text-sm font-semibold text-white disabled:opacity-50"
                              style={{ background: "var(--accent-red)" }}>
                        Senden
                      </button>
                    </div>
                  )}
                  {it.status === "akzeptiert" && (
                    <div className="mt-3 rounded-xl p-3" data-testid={`vereinbart-${it.id}`}
                         style={{ background: "rgba(52,199,89,0.08)", border: "1px solid rgba(52,199,89,0.3)" }}>
                      {/* RP-477: der vereinbarte Preis — nicht das ursprüngliche Angebot */}
                      <div className="text-lg font-black">
                        Vereinbarter Preis: {it.agreed_price != null ? fmtEur(it.agreed_price) : "ohne Betrag"}
                      </div>
                      {it.offer != null && it.agreed_price != null && Number(it.offer) !== Number(it.agreed_price) && (
                        <div className="text-[11.5px] text-zinc-500">Dein ursprüngliches Angebot: {fmtEur(it.offer)}</div>
                      )}
                      <div className="mt-1 text-[12.5px] text-emerald-400">
                        {it.inserat_status === "verkauft"
                          ? "Das Fahrzeug ist verkauft."
                          : "Das Fahrzeug ist für dich reserviert."}
                        {!kontakt && " Der Händler meldet sich zur Abwicklung."}
                      </div>
                      {/* RP-503: bei wem und wie erreichbar */}
                      {kontakt && (
                        <div className="mt-2 text-[12.5px] space-y-1" data-testid={`haendler-kontakt-${it.id}`}>
                          <div className="font-semibold inline-flex items-center gap-1.5">
                            <Store size={13} /> {it.haendler.company_name}
                            {kontakt.city ? <span className="font-normal text-zinc-500"> · {kontakt.city}</span> : null}
                          </div>
                          {kontakt.contact_person && <div className="text-zinc-400">Ansprechpartner: {kontakt.contact_person}</div>}
                          <div className="flex flex-wrap gap-2 pt-1">
                            {kontakt.phone && (
                              <a href={`tel:${kontakt.phone.replace(/\s/g, "")}`}
                                 className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 font-semibold text-white"
                                 style={{ background: "var(--accent-red)" }}>
                                <Phone size={13} /> {kontakt.phone}
                              </a>
                            )}
                            {kontakt.email && (
                              <a href={`mailto:${kontakt.email}`}
                                 className="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 border"
                                 style={{ borderColor: "var(--border-default)" }}>
                                <Mail size={13} /> {kontakt.email}
                              </a>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                  {beendet && (
                    <div className="mt-2 text-[12.5px]" style={{ color: "var(--text-muted)" }}
                         data-testid={`beendet-${it.id}`}>
                      {beendet}
                    </div>
                  )}
                  {(it.history || []).length > 1 && (
                    <div className="mt-2 space-y-0.5 text-[11.5px] text-zinc-500">
                      {it.history.map((h, i) => (
                        <div key={i}>{verlaufZeile(h)}</div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
