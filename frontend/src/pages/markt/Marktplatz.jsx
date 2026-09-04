import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { buyerApi, useBuyer } from "@/context/BuyerContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Store, LogOut, Lock, Gauge, Calendar, Fuel, ShieldCheck, Phone, MapPin, X, Clock, ChevronLeft, ChevronRight, Camera, Heart, Handshake, Inbox, Check } from "lucide-react";

const BACKEND = process.env.REACT_APP_BACKEND_URL || "";
const fmtEur = (n) => (n == null ? "Preis auf Anfrage" : `${Number(n).toLocaleString("de-DE")} €`);
const photoUrl = (u) => (!u ? null : u.startsWith("http") ? u : `${BACKEND}${u}`);

const LEVEL_LABEL = {
  netzwerk: "Netzwerkpreis", b2b: "B2B-Preis", oeffentlich: "Öffentlich",
};

const fInput = "h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none focus:border-white/40";
const fStyle = { borderColor: "var(--border-default)" };
function FField({ label, children }) {
  return (
    <div>
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
  const [items, setItems] = useState(null);
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
  // Haendler-Ansicht: alle Fahrzeuge EINES Haendlers + Profil (Logo,
  // Oeffnungszeiten, Telefon). Filter/Sortierung gelten dort weiter.
  const [dealerView, setDealerView] = useState(null);
  const apply = () => setTick((t) => t + 1);

  // Herz-Klick: merken/entfernen — optimistisch, Server bestaetigt.
  const toggleFav = async (e, id) => {
    e.stopPropagation();
    // Merken braucht ein Konto — der Marktplatz selbst ist offen.
    if (!buyer) {
      toast.info("Zum Merken kurz kostenlos registrieren");
      nav("/markt/registrieren");
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
      setDealerView(r.data.profile);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) { toast.error(errMsg(e, "Händler-Seite konnte nicht geladen werden")); }
  };
  const closeDealer = () => setDealerView(null);

  const isBuyer = buyer?.role === "b2b_buyer";
  const active = access?.active;

  const loadAccess = useCallback(async () => {
    try {
      const r = await buyerApi.get("/marktplatz/zugang");
      setAccess(r.data);
    } catch (e) { /* ignore */ }
  }, []);

  const loadItems = useCallback(async () => {
    try {
      const p = new URLSearchParams();
      if (q.trim()) p.set("q", q.trim());
      Object.entries(filters).forEach(([k, val]) => {
        if (String(val).trim() !== "") p.set(k, val);
      });
      if (sort) p.set("sort", sort);
      if (nurFavs) p.set("nur_favoriten", "1");
      if (dealerView?.id) p.set("dealer", dealerView.id);
      const qs = p.toString();
      const r = await buyerApi.get(`/marktplatz/listings${qs ? `?${qs}` : ""}`);
      setItems(r.data);
    } catch (e) {
      if (e?.response?.status === 402) setAccess((a) => ({ ...(a || {}), active: false }));
      else toast.error(errMsg(e, "Fahrzeuge konnten nicht geladen werden"));
    }
  }, [q, filters, sort, nurFavs, dealerView]);

  // Einladungslink eines BESTANDS-Kaeufers einloesen (Review 09/2026):
  // /markt?invite=<token> — einmalig, Parameter danach aus der URL nehmen.
  const [sp, setSp] = useSearchParams();
  const inviteToken = sp.get("invite") || "";
  const redeemed = useRef(false);
  useEffect(() => {
    if (!buyer || !inviteToken || redeemed.current) return;
    redeemed.current = true;
    buyerApi.post(`/invites/${encodeURIComponent(inviteToken)}/redeem`)
      .then((r) => {
        toast.success(`Netzwerk beigetreten: ${r.data.dealer}`, { duration: 6000 });
        setTick((t) => t + 1);       // Listings neu laden (Netzwerkpreise!)
      })
      .catch((e) => toast.error(errMsg(e, "Einladung konnte nicht eingelöst werden")))
      .finally(() => {
        const neu = new URLSearchParams(sp);
        neu.delete("invite");
        setSp(neu, { replace: true });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buyer, inviteToken]);

  useEffect(() => {
    if (ready && !buyer) {
      nav(inviteToken
        ? `/markt/login?invite=${encodeURIComponent(inviteToken)}`
        : "/markt/login");
    }
  }, [ready, buyer, nav, inviteToken]);
  useEffect(() => { if (buyer) loadAccess(); }, [buyer, loadAccess]);
  useEffect(() => {
    if (!buyer) return;
    buyerApi.get("/manual/makes").then((r) => setMakes(r.data)).catch(() => {});
    buyerApi.get("/marktplatz/favoriten")
      .then((r) => setFavs(new Set(r.data.listing_ids || [])))
      .catch(() => {});
  }, [buyer]);
  // Nur bei Zugangs-Freischaltung oder explizitem Anwenden neu laden (tick),
  // NICHT bei jedem Tastendruck in den Filterfeldern.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (active) loadItems(); }, [active, tick, nurFavs, dealerView]);

  const requestAccess = async () => {
    setRequesting(true);
    try {
      const r = await buyerApi.post("/buyer/zugang-anfrage");
      toast.success(r.data.hinweis || "Anfrage gesendet");
    } catch (e) { toast.error(errMsg(e)); }
    finally { setRequesting(false); }
  };

  // Stripe-Checkout (20 €/Monat, 09/2026): zahlen -> Rueckkehr auf
  // /markt/zahlung-erfolg -> Zugang ist automatisch aktiv.
  const [paying, setPaying] = useState(false);
  // Audit 09/2026 (Blocker 6): Online-Zahlung nur anbieten, wenn Stripe auf
  // dem Server wirklich eingerichtet ist (sonst 503 -> nur Rechnung).
  const [zahlung, setZahlung] = useState({ stripe_aktiv: false });
  useEffect(() => {
    buyerApi.get("/payments/config").then((r) => setZahlung(r.data || {})).catch(() => {});
  }, []);
  const payWithStripe = async () => {
    setPaying(true);
    try {
      const { data } = await buyerApi.post("/payments/checkout", {
        plan: "marktplatz", origin_url: window.location.origin,
      });
      window.location.href = data.url;
    } catch (e) {
      toast.error(errMsg(e, "Zahlung konnte nicht gestartet werden"));
      setPaying(false);
    }
  };

  const Header = () => (
    <div className="sticky top-0 z-10 px-4 sm:px-6 py-3 flex items-center justify-between"
         style={{ background: "rgba(10,10,10,0.9)", backdropFilter: "blur(12px)",
                  borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg flex items-center justify-center text-white"
             style={{ background: "var(--accent-red)" }}><Store size={16} /></div>
        <div className="font-black tracking-tight text-white">B2B-MARKTPLATZ</div>
      </div>
      <div className="flex items-center gap-3">
        {buyer ? (
          <>
            <span className="text-xs text-zinc-500 hidden sm:block">{buyer?.company_name}</span>
            <button onClick={() => { logout(); nav("/markt/login"); }}
                    className="text-zinc-400 hover:text-white inline-flex items-center gap-1.5 text-sm">
              <LogOut size={16} /> Abmelden
            </button>
          </>
        ) : (
          <>
            <button onClick={() => nav("/markt/login")} data-testid="markt-anmelden"
                    className="text-zinc-400 hover:text-white text-sm">Anmelden</button>
            <button onClick={() => nav("/markt/registrieren")} data-testid="markt-registrieren"
                    className="rounded-lg px-3 py-1.5 text-sm font-semibold text-white"
                    style={{ background: "var(--accent-red)" }}>Kostenlos registrieren</button>
          </>
        )}
      </div>
    </div>
  );

  if (!ready || (buyer && access === null)) {
    return <div className="min-h-screen flex items-center justify-center text-zinc-500" style={{ background: "#0a0a0a" }}>Lädt…</div>;
  }
  // Ohne Anmeldung sichtbar (09/2026): oeffentlich veroeffentlichte
  // Fahrzeuge. Merken, Anfragen und Netzwerk-Bestand bleiben angemeldeten
  // Zwischenhaendlern vorbehalten.

  return (
    <div className="min-h-screen" style={{ background: "#0a0a0a", color: "#fff" }} data-theme="dark" data-testid="markt-page">
      <Header />
      <div className="px-4 sm:px-6 lg:px-10 py-6 max-w-7xl mx-auto">
        {/* Paywall */}
        {isBuyer && !active ? (
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
                 style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
              <div className="text-4xl font-black tabular-nums">
                {Number(access?.price ?? 20).toLocaleString("de-DE", { minimumFractionDigits: 2 })} €
                <span className="text-base font-normal text-zinc-500"> / 30 Tage</span>
              </div>
              {zahlung.stripe_aktiv && (
                <button onClick={payWithStripe} disabled={paying} data-testid="markt-stripe-pay"
                        className="mt-5 w-full rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                        style={{ background: "var(--accent-red)" }}>
                  {paying ? "Öffne Stripe…" : "Jetzt zahlen & sofort loslegen (Stripe)"}
                </button>
              )}
              <button onClick={requestAccess} disabled={requesting} data-testid="markt-access-request"
                      className="mt-2 w-full rounded-xl py-2.5 text-sm text-zinc-300 hover:text-white disabled:opacity-50"
                      style={{ background: "rgba(255,255,255,0.05)", border: "1px solid rgba(255,255,255,0.10)" }}>
                {requesting ? "Wird gesendet…" : "Lieber per Rechnung? Anfrage an den Betreiber"}
              </button>
              <p className="mt-3 text-[11px] text-zinc-600">
                {zahlung.stripe_aktiv
                  ? "Stripe schaltet sofort für 30 Tage frei (20 € inkl. USt); per Rechnung schaltet der Betreiber nach Zahlungseingang frei."
                  : "Der Zugang wird per Rechnung abgerechnet — der Betreiber schaltet nach Zahlungseingang für 30 Tage frei."}
              </p>
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
                           style={{ background: "rgba(255,255,255,0.06)" }}>
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
                <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
                  {dealerView ? `Fahrzeuge von ${dealerView.company_name}` : "Angebotene Fahrzeuge"}
                </h1>
              </div>
              <div className="flex items-center gap-2">
                {items && <span className="text-xs text-zinc-500">{items.length} Fahrzeuge</span>}
                <select value={sort}
                        onChange={(e) => { setSort(e.target.value); apply(); }}
                        className="h-10 px-3 rounded-xl border bg-[#141416] text-sm outline-none focus:border-white/40"
                        style={{ borderColor: "var(--border-default)" }}>
                  <option value="">Neueste zuerst</option>
                  <option value="preis_auf">Günstigste zuerst</option>
                  <option value="preis_ab">Teuerste zuerst</option>
                  <option value="km_auf">Wenigste km</option>
                  <option value="km_ab">Meiste km</option>
                </select>
                <button onClick={() => setShowAnfragen(true)}
                        data-testid="meine-anfragen-btn"
                        className="h-10 px-3.5 rounded-xl border text-sm inline-flex items-center gap-1.5 text-zinc-400 hover:text-white transition"
                        style={{ borderColor: "var(--border-default)" }}>
                  <Inbox size={14} /> Meine Anfragen
                </button>
                <button onClick={() => setNurFavs((x) => !x)}
                        data-testid="filter-favoriten"
                        className={`h-10 px-3.5 rounded-xl border text-sm inline-flex items-center gap-1.5 transition ${
                          nurFavs ? "text-red-400 border-red-500/50 bg-red-500/10" : "text-zinc-400"}`}
                        style={nurFavs ? {} : { borderColor: "var(--border-default)" }}>
                  <Heart size={14} fill={nurFavs ? "currentColor" : "none"} />
                  Favoriten{favs.size ? ` (${favs.size})` : ""}
                </button>
              </div>
            </div>

            {/* Filterleiste */}
            <div className="mt-5 rounded-2xl p-3 flex flex-wrap items-end gap-2"
                 style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
              <FField label="Marke">
                <select value={filters.make}
                        onChange={(e) => { setFilters((s) => ({ ...s, make: e.target.value, model: "" })); apply(); }}
                        className={fInput + " bg-[#141416] w-40"} style={fStyle}>
                  <option value="">Alle Marken</option>
                  {makes.map((m) => <option key={m.id} value={m.name}>{m.name}</option>)}
                </select>
              </FField>
              <FField label="Modell">
                <select value={filters.model} disabled={!filters.make}
                        onChange={(e) => { setFilters((s) => ({ ...s, model: e.target.value })); apply(); }}
                        className={fInput + " bg-[#141416] w-40 disabled:opacity-40"} style={fStyle}>
                  <option value="">{filters.make ? "Alle Modelle" : "erst Marke wählen"}</option>
                  {models.map((mm) => <option key={mm.id} value={mm.name}>{mm.name}</option>)}
                </select>
              </FField>
              <FField label="Kraftstoff">
                <select value={filters.fuel}
                        onChange={(e) => { setFilters((s) => ({ ...s, fuel: e.target.value })); apply(); }}
                        className={fInput + " bg-[#141416]"} style={fStyle}>
                  <option value="">Alle</option>
                  <option value="Benzin">Benzin</option>
                  <option value="Diesel">Diesel</option>
                  <option value="Elektro">Elektro</option>
                  <option value="Hybrid">Hybrid</option>
                  <option value="LPG">LPG / Gas</option>
                </select>
              </FField>
              <FField label="km von–bis">
                <div className="flex gap-1">
                  <input type="number" value={filters.km_min} onChange={(e) => setFilters((s) => ({ ...s, km_min: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="von" className={fInput + " w-20"} style={fStyle} />
                  <input type="number" value={filters.km_max} onChange={(e) => setFilters((s) => ({ ...s, km_max: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="bis" className={fInput + " w-20"} style={fStyle} />
                </div>
              </FField>
              <FField label="PS von–bis">
                <div className="flex gap-1">
                  <input type="number" value={filters.ps_min} onChange={(e) => setFilters((s) => ({ ...s, ps_min: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="von" className={fInput + " w-16"} style={fStyle} />
                  <input type="number" value={filters.ps_max} onChange={(e) => setFilters((s) => ({ ...s, ps_max: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="bis" className={fInput + " w-16"} style={fStyle} />
                </div>
              </FField>
              <FField label="Preis € von–bis">
                <div className="flex gap-1">
                  <input type="number" value={filters.price_min} onChange={(e) => setFilters((s) => ({ ...s, price_min: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="von" className={fInput + " w-24"} style={fStyle} />
                  <input type="number" value={filters.price_max} onChange={(e) => setFilters((s) => ({ ...s, price_max: e.target.value }))}
                         onKeyDown={(e) => e.key === "Enter" && apply()} placeholder="bis" className={fInput + " w-24"} style={fStyle} />
                </div>
              </FField>
              <button onClick={apply}
                      className="h-9 px-4 rounded-lg text-sm font-semibold text-white"
                      style={{ background: "var(--accent-red)" }}>Anwenden</button>
              <button onClick={() => { setFilters(EMPTY); setSort(""); setQ(""); apply(); }}
                      className="h-9 px-3 rounded-lg text-sm text-zinc-400 hover:text-white">Zurücksetzen</button>
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
                return (
                  <div key={v.id} onClick={() => setSel(v)}
                       className="tactical-card overflow-hidden flex flex-col cursor-pointer hover:border-white/25 transition"
                       data-testid={`markt-${v.id}`}>
                    <div className="h-44 overflow-hidden bg-zinc-900 relative">
                      {img ? <img src={img} alt="" className="w-full h-full object-cover" />
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
                      <div className="font-semibold">{d.make_label} {d.model_label}</div>
                      <div className="text-xs text-zinc-500 line-clamp-1">{d.model_description}</div>
                      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-400">
                        {d.first_registration && <span className="inline-flex items-center gap-1"><Calendar size={12} /> {d.first_registration}</span>}
                        {d.mileage != null && <span className="inline-flex items-center gap-1"><Gauge size={12} /> {Number(d.mileage).toLocaleString("de-DE")} km</span>}
                        {d.fuel_label && <span className="inline-flex items-center gap-1"><Fuel size={12} /> {d.fuel_label}</span>}
                        {d.power_ps && <span>{d.power_ps} PS</span>}
                      </div>
                      <div className="mt-3 pt-3 border-t flex items-end justify-between" style={{ borderColor: "var(--border-default)" }}>
                        <div>
                          <div className="text-lg font-black">{fmtEur(v.price)}</div>
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
          </>
        )}
      </div>

      {sel && <DetailModal v={sel} onClose={() => setSel(null)}
                           isFav={favs.has(sel.id)}
                           onFav={(e) => toggleFav(e, sel.id)}
                           onDealer={() => openDealer(sel.dealer?.id)} />}
      {showAnfragen && <MeineAnfragen onClose={() => setShowAnfragen(false)} />}
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
  const unfallfrei = d.accident_free
    || (d.accident_damaged === true ? "Nein" : d.accident_damaged === false ? "Ja" : null);
  const specs = [
    ["Erstzulassung", d.first_registration],
    ["Kilometerstand", d.mileage != null ? `${Number(d.mileage).toLocaleString("de-DE")} km` : null],
    ["Kraftstoff", d.fuel_label],
    ["Getriebe", d.gearbox_label],
    ["Leistung", d.power_ps ? `${d.power_ps} PS` : null],
    ["Farbe", d.color],
    ["Vorbesitzer", d.previous_owners],
    ["Unfallfrei", unfallfrei],
  ].filter(([, val]) => val != null && val !== "");

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.75)" }} onClick={onClose}>
      <div className="w-full sm:max-w-2xl max-h-[94vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)" }}
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
              <h2 className="text-xl font-black tracking-tight">{d.make_label} {d.model_label}</h2>
              <div className="text-sm text-zinc-500">{d.model_description}</div>
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
          <div className="mt-5 rounded-2xl p-4" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
            <div className="flex items-center gap-3">
              {v.dealer?.logo_url && (
                <div className="w-11 h-11 rounded-xl overflow-hidden flex items-center justify-center shrink-0"
                     style={{ background: "rgba(255,255,255,0.06)" }}>
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

          <InteresseForm v={v} angemeldet={!!buyer} />
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
function InteresseForm({ v, angemeldet = true }) {
  const nav = useNavigate();
  const [offen, setOffen] = useState(false);
  const [betrag, setBetrag] = useState("");
  const [nachricht, setNachricht] = useState("");
  const [busy, setBusy] = useState(false);
  const [gesendet, setGesendet] = useState(false);

  const senden = async () => {
    if (busy) return;
    // Anfragen brauchen ein Konto — Ansehen nicht.
    if (!angemeldet) {
      toast.info("Für eine Anfrage kurz kostenlos registrieren");
      nav("/markt/registrieren");
      return;
    }
    setBusy(true);
    try {
      await buyerApi.post(`/marktplatz/listings/${v.id}/interesse`, {
        offer: betrag ? Number(betrag) : undefined,
        message: nachricht,
      });
      setGesendet(true);
      toast.success("Anfrage gesendet — der Händler meldet sich");
    } catch (e) {
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
    <div className="mt-3 rounded-2xl p-4" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
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
            <input type="number" min="1" value={betrag} onChange={(e) => setBetrag(e.target.value)}
                   data-testid="interesse-betrag"
                   className={`${fInput} w-full`} style={fStyle} placeholder="z.B. 12500" />
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
 *  Gegenangebot kann hier geantwortet werden (annehmen/ablehnen). */
function MeineAnfragen({ onClose }) {
  const [items, setItems] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [gegenFor, setGegenFor] = useState(null);      // Kaeufer-Gegenangebot (09/2026)
  const [gegenVal, setGegenVal] = useState("");

  const load = () => {
    buyerApi.get("/buyer/interessen")
      .then((r) => setItems(Array.isArray(r.data) ? r.data : []))
      .catch((e) => { toast.error(errMsg(e)); setItems([]); });
  };
  useEffect(() => { load(); }, []);

  const antwort = async (it, action, extra = {}) => {
    if (busyId) return;
    setBusyId(it.id);
    try {
      await buyerApi.post(`/interessen/${it.id}/kaeufer-antwort`, { action, message: "", ...extra });
      toast.success(action === "annehmen"
        ? "Gegenangebot angenommen — das Fahrzeug ist für dich reserviert"
        : action === "ablehnen" ? "Gegenangebot abgelehnt" : "Dein Gegenangebot wurde an den Händler gesendet");
      setGegenFor(null); setGegenVal("");
      load();
    } catch (e) {
      toast.error(errMsg(e, "Antwort fehlgeschlagen"));
    } finally {
      setBusyId(null);
    }
  };

  const STATUS_FARBE = {
    offen: "#fbbf24", gegenangebot: "#60a5fa", gegenangebot_kaeufer: "#3b82f6", akzeptiert: "#34c759", abgelehnt: "#a1a1aa",
  };
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.7)" }} onClick={onClose}>
      <div className="w-full sm:max-w-2xl max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl p-5"
           style={{ background: "#141416", color: "#fff" }} onClick={(e) => e.stopPropagation()}
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
        {items === null ? (
          <div className="text-sm text-zinc-500 py-8 text-center">Lädt…</div>
        ) : items.length === 0 ? (
          <div className="text-sm text-zinc-500 py-8 text-center">
            Noch keine Anfragen — öffne ein Fahrzeug und sende „Interesse / Angebot".
          </div>
        ) : (
          <div className="space-y-3">
            {items.map((it) => (
              <div key={it.id} className="rounded-2xl p-4" data-testid={`meine-anfrage-${it.id}`}
                   style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold truncate">{it.listing_title || "Inserat"}</div>
                    <div className="text-xs text-zinc-500 mt-0.5">
                      {it.offer != null ? `Dein Angebot: ${fmtEur(it.offer)}` : "Ohne Preisangebot"}
                      {" · "}{new Date(it.created_at).toLocaleDateString("de-DE")}
                    </div>
                  </div>
                  <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full shrink-0"
                        style={{ color: STATUS_FARBE[it.status] || "#a1a1aa",
                                 border: `1px solid ${STATUS_FARBE[it.status] || "#a1a1aa"}55`,
                                 background: `${STATUS_FARBE[it.status] || "#a1a1aa"}14` }}>
                    {it.status}
                  </span>
                </div>
                {it.status === "gegenangebot_kaeufer" && (
                  <div className="mt-3 text-[12.5px]" style={{ color: "var(--text-muted)" }}
                       data-testid={`kaeufer-wartet-${it.id}`}>
                    Dein Gegenangebot ({fmtEur(it.buyer_counter_offer)}) liegt beim Händler — er kann annehmen, ablehnen oder ein neues Angebot schreiben.
                  </div>
                )}
                {it.status === "gegenangebot" && (
                  <div className="mt-3 rounded-xl p-3" style={{ background: "rgba(59,130,246,0.08)", border: "1px solid rgba(59,130,246,0.3)" }}>
                    <div className="text-sm">
                      Gegenangebot des Händlers:{" "}
                      <span className="font-bold text-sky-400">{fmtEur(it.counter_offer)}</span>
                    </div>
                    <div className="mt-2.5 flex gap-2">
                      <button onClick={() => antwort(it, "annehmen")} disabled={busyId === it.id}
                              data-testid={`gegenangebot-annehmen-${it.id}`}
                              className="flex-1 rounded-lg py-2 text-sm font-semibold text-white disabled:opacity-50"
                              style={{ background: "#34c759" }}>
                        Annehmen
                      </button>
                      <button onClick={() => antwort(it, "ablehnen")} disabled={busyId === it.id}
                              data-testid={`gegenangebot-ablehnen-${it.id}`}
                              className="flex-1 rounded-lg py-2 text-sm font-semibold border text-zinc-300 disabled:opacity-50"
                              style={{ borderColor: "var(--border-default)" }}>
                        Ablehnen
                      </button>
                      <button onClick={() => { setGegenFor(gegenFor === it.id ? null : it.id); setGegenVal(""); }} disabled={busyId === it.id}
                              data-testid={`kaeufer-gegenangebot-${it.id}`}
                              className="flex-1 rounded-lg py-2 text-sm font-semibold border text-sky-300 disabled:opacity-50"
                              style={{ borderColor: "rgba(59,130,246,0.5)" }}>
                        Gegenangebot
                      </button>
                    </div>
                    {gegenFor === it.id && (
                      <div className="mt-2.5 flex gap-2 items-center">
                        <input type="number" min="1" value={gegenVal} onChange={(e) => setGegenVal(e.target.value)}
                               placeholder="Dein Preis in €" autoFocus
                               data-testid={`kaeufer-gegenangebot-betrag-${it.id}`}
                               className="h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none w-40"
                               style={{ borderColor: "var(--border-default)" }} />
                        <button onClick={() => { const b = Number(gegenVal); if (!b || b <= 0) { toast.error("Bitte einen Betrag eingeben"); return; } antwort(it, "gegenangebot", { counter_offer: b }); }}
                                disabled={busyId === it.id}
                                data-testid={`kaeufer-gegenangebot-senden-${it.id}`}
                                className="h-9 rounded-lg px-4 text-sm font-semibold text-white disabled:opacity-50"
                                style={{ background: "var(--accent-red)" }}>
                          Senden
                        </button>
                      </div>
                    )}
                  </div>
                )}
                {it.status === "akzeptiert" && (
                  <div className="mt-2 text-[12.5px] text-emerald-400">
                    Angenommen — das Fahrzeug ist für dich reserviert. Der Händler meldet sich zur Abwicklung.
                  </div>
                )}
                {(it.history || []).length > 1 && (
                  <div className="mt-2 space-y-0.5 text-[11.5px] text-zinc-500">
                    {it.history.map((h, i) => (
                      <div key={i}>
                        {h.von === "kaeufer" ? "Du" : "Händler"} · {h.aktion}
                        {h.angebot != null ? ` · ${fmtEur(h.angebot)}` : ""}
                        {h.nachricht ? ` · „${h.nachricht}"` : ""}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
