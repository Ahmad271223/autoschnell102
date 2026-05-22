import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { Activity, RefreshCw } from "lucide-react";
import { PageHeader, Card, Spinner, fmtNum } from "./_ui";

const SOURCES = [
  { key: "mobile",        label: "mobile.de",        color: "#0a84ff" },
  { key: "kleinanzeigen", label: "kleinanzeigen.de", color: "#34c759" },
  { key: "autoscout",     label: "autoscout24.de",   color: "#ff9500" },
  { key: "other",         label: "sonstige",         color: "#8e8e93" },
];

const WINDOWS = [
  { key: "today",    label: "Heute" },
  { key: "last_24h", label: "24 Std" },
  { key: "last_7d",  label: "7 Tage" },
  { key: "all_time", label: "Gesamt" },
];

export default function AdminUrlStats() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [pulse, setPulse] = useState(false);
  const timer = useRef(null);

  const fetchOnce = async () => {
    try {
      setError(null);
      const r = await api.get("/admin/url-stats");
      setData(r.data);
      setPulse(true);
      setTimeout(() => setPulse(false), 600);
    } catch (e) {
      setError("Aktualisierung fehlgeschlagen");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOnce();
    timer.current = setInterval(fetchOnce, 60000);
    return () => clearInterval(timer.current);
  }, []);

  return (
    <div>
      <PageHeader
        title="URL-Statistik"
        subtitle="Live-Aktualisierung alle 60 Sekunden"
        action={
          <div className="flex items-center gap-2 text-[12px] text-zinc-400">
            <span className={`w-2 h-2 rounded-full bg-emerald-400 ${pulse ? "animate-ping" : ""}`} />
            Live
            <button onClick={fetchOnce} className="ml-2 inline-flex items-center gap-1 hover:text-white">
              <RefreshCw size={13} /> aktualisieren
            </button>
          </div>
        }
      />

      {loading ? (
        <div className="flex items-center gap-2 text-zinc-500 text-sm"><Spinner /> lade…</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {WINDOWS.map((w) => (
            <Card key={w.key}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Activity size={14} className="text-zinc-500" />
                  <span className="text-[15px] font-semibold text-white">{w.label}</span>
                </div>
                <span className="text-[20px] font-bold tabular-nums text-white">{fmtNum(data?.[w.key]?.total)}</span>
              </div>
              <div className="space-y-2.5">
                {SOURCES.map((s) => {
                  const v = data?.[w.key]?.[s.key] || 0;
                  const total = data?.[w.key]?.total || 1;
                  const pct = (v / Math.max(1, total)) * 100;
                  return (
                    <div key={s.key} className="flex items-center gap-3">
                      <div className="w-28 text-[13px] text-zinc-300 truncate">{s.label}</div>
                      <div className="flex-1 h-1.5 rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.08)" }}>
                        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: s.color }} />
                      </div>
                      <div className="w-12 text-right tabular-nums text-[13px] font-semibold text-white">{v}</div>
                    </div>
                  );
                })}
              </div>
            </Card>
          ))}
        </div>
      )}
      {error && <div className="mt-3 text-[12px] text-red-400">{error}</div>}
      {data?.now && (
        <div className="mt-4 text-[11px] text-zinc-500">
          Stand: {new Date(data.now).toLocaleString("de-DE")}
        </div>
      )}
    </div>
  );
}
