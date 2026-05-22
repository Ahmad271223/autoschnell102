import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Search, ChevronDown, ChevronUp, ExternalLink, Users as UsersIcon } from "lucide-react";
import { PageHeader, Card, Badge, Spinner, EmptyState, fmtDate, fmtNum } from "./_ui";

const SOURCE_TONE = {
  mobile: "blue",
  kleinanzeigen: "green",
  autoscout: "orange",
  autoscout24: "orange",
  other: "gray",
};

export default function AdminComparisons() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState({});

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/comparisons", { params: { limit: 500 } });
      setItems(data.items || []);
    } catch (e) {
      toast.error(errMsg(e, "Fehler beim Laden"));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return items;
    return items.filter((it) => {
      const v = it.vehicle || {};
      const txt = [it.ad_id, v.make, v.model, v.vin, ...(it.users || []).map((u) => u.email + " " + (u.company_name || ""))].join(" ").toLowerCase();
      return txt.includes(s);
    });
  }, [items, q]);

  return (
    <div>
      <PageHeader
        title="Vergleiche"
        subtitle={`${items.length} Inserate · sortiert nach Häufigkeit`}
      />

      <Card padded={false}>
        <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
          <Search size={16} className="text-zinc-500" />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Suche (Marke, Modell, VIN, Nutzer, Anzeigen-ID)"
            className="flex-1 bg-transparent border-0 outline-none text-[14px] text-white placeholder:text-zinc-500"
          />
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-zinc-500 text-sm"><Spinner /> lade…</div>
        ) : filtered.length === 0 ? (
          <EmptyState title="Keine Vergleiche" hint="Es wurden noch keine Fahrzeuge verglichen." />
        ) : (
          <ul className="divide-y" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
            {filtered.map((it) => {
              const v = it.vehicle || {};
              const isOpen = open[it.ad_id];
              return (
                <li key={it.ad_id} className="px-4 py-3">
                  <button
                    className="w-full flex items-center gap-3 text-left"
                    onClick={() => setOpen((o) => ({ ...o, [it.ad_id]: !o[it.ad_id] }))}
                  >
                    <div className="w-12 h-12 rounded-xl flex items-center justify-center text-zinc-200 font-semibold text-[12px] shrink-0" style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.08)" }}>
                      {(v.make?.[0] || "?").toUpperCase()}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[14.5px] font-semibold text-white truncate">
                          {v.make ? `${v.make} ${v.model || ""}`.trim() : `Inserat ${it.ad_id}`}
                        </span>
                        {it.sources.map((s) => (
                          <Badge key={s} tone={SOURCE_TONE[s] || "gray"}>{s}</Badge>
                        ))}
                      </div>
                      <div className="text-[12.5px] text-zinc-400 truncate mt-0.5">
                        {[v.ez, v.mileage ? `${fmtNum(v.mileage)} km` : null, v.fuel, v.price ? `${fmtNum(v.price)} €` : null].filter(Boolean).join(" · ") || `ID: ${it.ad_id}`}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="flex items-center gap-1.5 justify-end">
                        <UsersIcon size={13} className="text-zinc-500" />
                        <span className="text-[14px] font-bold tabular-nums text-white">{it.count}</span>
                      </div>
                      <div className="text-[11px] text-zinc-500 mt-0.5">{fmtDate(it.last_at)}</div>
                    </div>
                    {isOpen ? <ChevronUp size={16} className="text-zinc-500" /> : <ChevronDown size={16} className="text-zinc-500" />}
                  </button>

                  {isOpen && (
                    <div className="mt-3 pt-3" style={{ paddingLeft: 60, borderTop: "1px solid rgba(255,255,255,0.06)" }}>
                      {v.url && (
                        <a href={v.url} target="_blank" rel="noreferrer"
                           className="inline-flex items-center gap-1 text-[12.5px] text-blue-400 hover:underline mb-2">
                          <ExternalLink size={12} /> Original-Inserat öffnen
                        </a>
                      )}
                      <div className="text-[12px] text-zinc-400 mb-1.5">Verglichen von:</div>
                      <div className="flex flex-wrap gap-1.5">
                        {(it.users || []).length === 0 ? (
                          <span className="text-[12px] text-zinc-500">— keine Nutzer-Zuordnung —</span>
                        ) : (
                          it.users.map((u) => (
                            <span key={u.id} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[12px]" style={{ background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.08)" }}>
                              <span className="font-medium text-white">{u.company_name || u.username || u.email}</span>
                              {u.email && u.email !== (u.company_name || u.username) && <span className="text-zinc-400">{u.email}</span>}
                              {u.active === false && <Badge tone="red">gesperrt</Badge>}
                            </span>
                          ))
                        )}
                      </div>
                      <div className="text-[11px] text-zinc-500 mt-2">
                        Erster Vergleich: {fmtDate(it.first_at)} · Letzter: {fmtDate(it.last_at)}
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
