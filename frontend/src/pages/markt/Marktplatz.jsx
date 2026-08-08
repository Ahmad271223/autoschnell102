import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { buyerApi, useBuyer } from "@/context/BuyerContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Store, LogOut, Lock, Gauge, Calendar, Fuel, ShieldCheck, Phone, MapPin, X, Clock } from "lucide-react";

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
  const apply = () => setTick((t) => t + 1);
  const models = makes.find((m) => m.name === filters.make)?.models || [];

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
      const qs = p.toString();
      const r = await buyerApi.get(`/marktplatz/listings${qs ? `?${qs}` : ""}`);
      setItems(r.data);
    } catch (e) {
      if (e?.response?.status === 402) setAccess((a) => ({ ...(a || {}), active: false }));
      else toast.error(errMsg(e, "Fahrzeuge konnten nicht geladen werden"));
    }
  }, [q, filters, sort]);

  useEffect(() => { if (ready && !buyer) nav("/markt/login"); }, [ready, buyer, nav]);
  useEffect(() => { if (buyer) loadAccess(); }, [buyer, loadAccess]);
  useEffect(() => {
    if (!buyer) return;
    buyerApi.get("/manual/makes").then((r) => setMakes(r.data)).catch(() => {});
  }, [buyer]);
  // Nur bei Zugangs-Freischaltung oder explizitem Anwenden neu laden (tick),
  // NICHT bei jedem Tastendruck in den Filterfeldern.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (active) loadItems(); }, [active, tick]);

  const requestAccess = async () => {
    setRequesting(true);
    try {
      const r = await buyerApi.post("/buyer/zugang-anfrage");
      toast.success(r.data.hinweis || "Anfrage gesendet");
    } catch (e) { toast.error(errMsg(e)); }
    finally { setRequesting(false); }
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
        <span className="text-xs text-zinc-500 hidden sm:block">{buyer?.company_name}</span>
        <button onClick={() => { logout(); nav("/markt/login"); }}
                className="text-zinc-400 hover:text-white inline-flex items-center gap-1.5 text-sm">
          <LogOut size={16} /> Abmelden
        </button>
      </div>
    </div>
  );

  if (!ready || (buyer && access === null)) {
    return <div className="min-h-screen flex items-center justify-center text-zinc-500" style={{ background: "#0a0a0a" }}>Lädt…</div>;
  }
  if (!buyer) return null;

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
                {Number(access?.price ?? 9.99).toLocaleString("de-DE", { minimumFractionDigits: 2 })} €
                <span className="text-base font-normal text-zinc-500"> / Monat</span>
              </div>
              <button onClick={requestAccess} disabled={requesting}
                      className="mt-5 w-full rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                      style={{ background: "var(--accent-red)" }}>
                {requesting ? "Wird gesendet…" : "Zugang freischalten lassen"}
              </button>
              <p className="mt-3 text-[11px] text-zinc-600">
                Freischaltung erfolgt nach Zahlungseingang durch den Betreiber.
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="overline">Marktplatz</div>
                <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
                  Angebotene Fahrzeuge
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
                    <div className="h-44 overflow-hidden bg-zinc-900">
                      {img ? <img src={img} alt="" className="w-full h-full object-cover" />
                           : <div className="w-full h-full flex items-center justify-center text-zinc-700 text-xs">kein Foto</div>}
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
                        <div className="text-right text-[11px] text-zinc-500">
                          {v.dealer?.company_name}<br />{v.dealer?.city}
                        </div>
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

      {sel && <DetailModal v={sel} onClose={() => setSel(null)} />}
    </div>
  );
}

function DetailModal({ v, onClose }) {
  const d = v.data || {};
  const phone = v.dealer?.phone;
  const photos = (v.photos || []).map(photoUrl).filter(Boolean);
  const specs = [
    ["Erstzulassung", d.first_registration],
    ["Kilometerstand", d.mileage != null ? `${Number(d.mileage).toLocaleString("de-DE")} km` : null],
    ["Kraftstoff", d.fuel_label],
    ["Getriebe", d.gearbox_label],
    ["Leistung", d.power_ps ? `${d.power_ps} PS` : null],
    ["Farbe", d.color],
    ["Vorbesitzer", d.previous_owners],
  ].filter(([, val]) => val != null && val !== "");

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.75)" }} onClick={onClose}>
      <div className="w-full sm:max-w-2xl max-h-[94vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)" }}
           onClick={(e) => e.stopPropagation()}>
        <div className="relative">
          {photos[0] ? <img src={photos[0]} alt="" className="w-full h-56 sm:h-72 object-cover" />
                     : <div className="w-full h-40 bg-zinc-900 flex items-center justify-center text-zinc-700 text-xs">kein Foto</div>}
          <button onClick={onClose} className="absolute top-3 right-3 w-9 h-9 rounded-full flex items-center justify-center text-white"
                  style={{ background: "rgba(0,0,0,0.5)" }}><X size={18} /></button>
        </div>
        {photos.length > 1 && (
          <div className="flex gap-2 p-3 overflow-x-auto">
            {photos.slice(1, 10).map((u, i) => (
              <img key={i} src={u} alt="" className="h-16 w-24 object-cover rounded-lg shrink-0" />
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
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <Store size={15} /> {v.dealer?.company_name}
                </div>
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
        </div>
      </div>
    </div>
  );
}
