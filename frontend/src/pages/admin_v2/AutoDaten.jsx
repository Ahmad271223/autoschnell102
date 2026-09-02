import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";
import { Car, ChevronLeft, ChevronRight, RefreshCw, Search, ShieldAlert } from "lucide-react";
import { PageHeader, Card, Spinner, EmptyState, Button, Badge } from "./_ui";

/**
 * Auto-Daten — NUR Super-Admin.
 *
 * Zeigt die dauerhaften, anonymen Fahrzeugdaten aus admin_vehicle_data:
 * Marke, Modell, EZ, km, Kraftstoff, PS/kW, Kaufpreis, Schäden. Es gibt
 * bewusst KEINE Verbindung zu Vertrag, Händler oder Person — der Backend-
 * Endpunkt liefert nichts dergleichen und blockt normale Admins mit 403.
 * Alle Werte werden als reiner Text gerendert (kein innerHTML).
 */

const KRAFTSTOFFE = ["Benzin", "Diesel", "Elektro", "Hybrid", "Plug-in-Hybrid", "LPG", "CNG"];

const fmtKm = (n) => (n == null ? "–" : `${Number(n).toLocaleString("de-DE")} km`);
const fmtPs = (n) => (n == null ? "–" : `${Number(n).toLocaleString("de-DE")} PS`);
const fmtKw = (n) => (n == null ? "–" : `${Number(n).toLocaleString("de-DE")} kW`);
const fmtPreis = (cents, cur = "EUR") =>
  cents == null
    ? "–"
    : (Number(cents) / 100).toLocaleString("de-DE", { style: "currency", currency: cur || "EUR" });
const fmtEz = (s) => {
  if (!s) return "–";
  const m = /^(\d{4})-(\d{2})$/.exec(s);
  return m ? `${m[2]}/${m[1]}` : s;
};
const txt = (s) => (s == null || s === "" ? "–" : String(s));

const LEER = { search: "", fuel_type: "", ez_von: "", ez_bis: "", preis_min: "", preis_max: "", km_min: "", km_max: "" };

const inputCls =
  "h-10 px-3 rounded-xl text-[14px] w-full outline-none focus:ring-2 focus:ring-red-500/40";
const inputStyle = {
  background: "#18181b",
  color: "#ffffff",
  border: "1px solid rgba(255,255,255,0.12)",
  colorScheme: "dark",
};

export default function AdminAutoDaten() {
  const { user } = useAuth();
  const [filter, setFilter] = useState(LEER);
  const [angewandt, setAngewandt] = useState(LEER);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [cursor, setCursor] = useState(null);
  const [stapel, setStapel] = useState([]); // Cursor-Stapel für "Zurück"
  const [offen, setOffen] = useState(null);
  const reqId = useRef(0);

  const params = useCallback((f, cur) => {
    const p = { limit: 50 };
    if (f.search.trim()) p.search = f.search.trim().slice(0, 80);
    if (f.fuel_type) p.fuel_type = f.fuel_type;
    if (/^\d{4}$/.test(f.ez_von)) p.ez_von = f.ez_von;
    if (/^\d{4}$/.test(f.ez_bis)) p.ez_bis = f.ez_bis;
    if (f.preis_min !== "") p.preis_min = Math.max(0, Math.round(Number(f.preis_min) * 100));
    if (f.preis_max !== "") p.preis_max = Math.max(0, Math.round(Number(f.preis_max) * 100));
    if (f.km_min !== "") p.km_min = Math.max(0, Math.round(Number(f.km_min)));
    if (f.km_max !== "") p.km_max = Math.max(0, Math.round(Number(f.km_max)));
    if (cur) p.cursor = cur;
    return p;
  }, []);

  const laden = useCallback(async (f, cur) => {
    const id = ++reqId.current;
    setLoading(true);
    setError(null);
    try {
      const r = await api.get("/admin/vehicle-data", { params: params(f, cur) });
      if (id !== reqId.current) return;
      setData(r.data);
    } catch (e) {
      if (id !== reqId.current) return;
      const st = e?.response?.status;
      setError(
        st === 403
          ? "Kein Zugriff — Auto-Daten sieht nur der Super-Admin."
          : st === 422
            ? "Ungültige Filterwerte."
            : "Auto-Daten konnten nicht geladen werden."
      );
      setData(null);
    } finally {
      if (id === reqId.current) setLoading(false);
    }
  }, [params]);

  useEffect(() => {
    if (!user?.is_super_admin) { setLoading(false); return; }
    laden(angewandt, cursor);
  }, [angewandt, cursor, laden, user?.is_super_admin]);

  const anwenden = (e) => {
    e?.preventDefault?.();
    setStapel([]);
    setCursor(null);
    setAngewandt({ ...filter });
  };
  const zuruecksetzen = () => {
    setFilter(LEER);
    setStapel([]);
    setCursor(null);
    setAngewandt(LEER);
  };
  const weiter = () => {
    if (!data?.next_cursor) return;
    setStapel((s) => [...s, cursor]);
    setCursor(data.next_cursor);
  };
  const zurueck = () => {
    if (!stapel.length) return;
    const s = [...stapel];
    const prev = s.pop();
    setStapel(s);
    setCursor(prev || null);
  };

  if (!user?.is_super_admin) {
    return (
      <div>
        <PageHeader title="Auto-Daten" />
        <Card>
          <div className="flex items-start gap-3 text-zinc-300">
            <ShieldAlert size={20} className="text-amber-400 shrink-0 mt-0.5" />
            <div>
              <div className="font-semibold text-white">Nur für den Super-Admin</div>
              <div className="text-[13px] text-zinc-400 mt-1">
                Dieser Bereich ist normalen Admin-Konten nicht zugänglich.
              </div>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  const items = data?.items || [];
  const seite = stapel.length + 1;
  const aktiveFilter = Object.entries(angewandt).filter(([, v]) => v !== "").length;

  return (
    <div>
      <PageHeader
        title="Auto-Daten"
        subtitle="Dauerhafte, anonyme Fahrzeugdaten aus Kaufverträgen — ohne Bezug zu Vertrag, Händler oder Person"
        action={
          <div className="flex items-center gap-2 text-[12px] text-zinc-400">
            {data && <span>{Number(data.total).toLocaleString("de-DE")} Datensätze</span>}
            <button
              onClick={() => laden(angewandt, cursor)}
              className="ml-2 inline-flex items-center gap-1 hover:text-white"
              title="Neu laden"
            >
              <RefreshCw size={13} /> aktualisieren
            </button>
          </div>
        }
      />

      <Card className="mb-4">
        <form onSubmit={anwenden} className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <div className="col-span-2 relative">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              className={`${inputCls} pl-9`}
              style={inputStyle}
              placeholder="Marke oder Modell"
              value={filter.search}
              maxLength={80}
              onChange={(e) => setFilter({ ...filter, search: e.target.value })}
            />
          </div>
          <select
            className={inputCls}
            style={inputStyle}
            value={filter.fuel_type}
            onChange={(e) => setFilter({ ...filter, fuel_type: e.target.value })}
          >
            <option value="">Kraftstoff: alle</option>
            {KRAFTSTOFFE.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <input className={inputCls} style={inputStyle} inputMode="numeric" placeholder="EZ ab (Jahr)"
            value={filter.ez_von} maxLength={4}
            onChange={(e) => setFilter({ ...filter, ez_von: e.target.value.replace(/\D/g, "") })} />
          <input className={inputCls} style={inputStyle} inputMode="numeric" placeholder="EZ bis (Jahr)"
            value={filter.ez_bis} maxLength={4}
            onChange={(e) => setFilter({ ...filter, ez_bis: e.target.value.replace(/\D/g, "") })} />
          <div className="hidden lg:block" />
          <input className={inputCls} style={inputStyle} type="number" min="0" step="1" placeholder="Preis ab (€)"
            value={filter.preis_min} onChange={(e) => setFilter({ ...filter, preis_min: e.target.value })} />
          <input className={inputCls} style={inputStyle} type="number" min="0" step="1" placeholder="Preis bis (€)"
            value={filter.preis_max} onChange={(e) => setFilter({ ...filter, preis_max: e.target.value })} />
          <input className={inputCls} style={inputStyle} type="number" min="0" step="1" placeholder="km ab"
            value={filter.km_min} onChange={(e) => setFilter({ ...filter, km_min: e.target.value })} />
          <input className={inputCls} style={inputStyle} type="number" min="0" step="1" placeholder="km bis"
            value={filter.km_max} onChange={(e) => setFilter({ ...filter, km_max: e.target.value })} />
          <div className="col-span-2 flex gap-2 justify-end">
            <Button type="button" variant="ghost" onClick={zuruecksetzen}>Zurücksetzen</Button>
            <Button type="submit">Filtern</Button>
          </div>
        </form>
        {aktiveFilter > 0 && (
          <div className="mt-3 text-[12px] text-zinc-500">{aktiveFilter} Filter aktiv</div>
        )}
      </Card>

      {error && (
        <Card className="mb-4">
          <div className="text-[13px] text-red-400">{error}</div>
        </Card>
      )}

      <Card padded={false}>
        {loading ? (
          <div className="flex items-center gap-2 text-zinc-500 text-sm p-6"><Spinner /> lade…</div>
        ) : !items.length ? (
          <EmptyState
            title={aktiveFilter ? "Keine Treffer" : "Noch keine Auto-Daten"}
            hint={aktiveFilter ? "Filter anpassen oder zurücksetzen." : "Datensätze entstehen automatisch mit jedem Kaufvertrag."}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[13.5px]">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wide text-zinc-500" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
                  <th className="px-4 py-3">Marke</th>
                  <th className="px-4 py-3">Modell</th>
                  <th className="px-4 py-3">EZ</th>
                  <th className="px-4 py-3 text-right">km</th>
                  <th className="px-4 py-3">Kraftstoff</th>
                  <th className="px-4 py-3 text-right">Leistung</th>
                  <th className="px-4 py-3 text-right">Kaufpreis</th>
                  <th className="px-4 py-3">Schäden</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it, i) => {
                  const key = `${cursor || "p0"}-${i}`;
                  const anz = (it.damages || []).length;
                  return (
                    <tr key={key} className="align-top hover:bg-white/[0.03]" style={{ borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                      <td className="px-4 py-3 font-medium text-white whitespace-nowrap">
                        <span className="inline-flex items-center gap-2"><Car size={14} className="text-zinc-500" />{txt(it.brand)}</span>
                      </td>
                      <td className="px-4 py-3 text-zinc-200">{txt(it.model)}</td>
                      <td className="px-4 py-3 text-zinc-300 whitespace-nowrap">{fmtEz(it.first_registration)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-zinc-200 whitespace-nowrap">{fmtKm(it.mileage_km)}</td>
                      <td className="px-4 py-3 text-zinc-300">{txt(it.fuel_type)}</td>
                      <td className="px-4 py-3 text-right tabular-nums text-zinc-200 whitespace-nowrap">
                        {fmtPs(it.power_ps)}
                        <div className="text-[11px] text-zinc-500">{fmtKw(it.power_kw)}</div>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums font-semibold text-white whitespace-nowrap">{fmtPreis(it.purchase_price_cents, it.currency)}</td>
                      <td className="px-4 py-3 min-w-[160px] max-w-[360px]">
                        {anz === 0 ? (
                          <span className="text-zinc-500">–</span>
                        ) : (
                          <div>
                            <button
                              type="button"
                              onClick={() => setOffen(offen === key ? null : key)}
                              className="text-left"
                            >
                              <Badge tone={anz > 3 ? "orange" : "yellow"}>{anz} {anz === 1 ? "Eintrag" : "Einträge"}</Badge>
                            </button>
                            {offen === key && (
                              <ul className="mt-2 space-y-1 text-[12.5px] text-zinc-300 list-disc pl-4">
                                {it.damages.map((s, j) => (
                                  <li key={j} className="break-words">{s}</li>
                                ))}
                              </ul>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {!loading && (items.length > 0 || stapel.length > 0) && (
          <div className="flex items-center justify-between px-4 py-3 text-[12px] text-zinc-400" style={{ borderTop: "1px solid rgba(255,255,255,0.08)" }}>
            <span>Seite {seite}{data ? ` · ${items.length} von ${Number(data.total).toLocaleString("de-DE")}` : ""}</span>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={zurueck} disabled={!stapel.length}>
                <ChevronLeft size={14} /> Zurück
              </Button>
              <Button size="sm" variant="secondary" onClick={weiter} disabled={!data?.next_cursor}>
                Weiter <ChevronRight size={14} />
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
