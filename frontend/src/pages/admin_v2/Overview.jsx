import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader, StatCard, Card, Spinner, fmtNum } from "./_ui";

export default function AdminOverview() {
  const [stats, setStats] = useState(null);
  const [urls, setUrls] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const [s, u] = await Promise.all([
          api.get("/admin/stats"),
          api.get("/admin/url-stats"),
        ]);
        if (stopped) return;
        setStats(s.data);
        setUrls(u.data);
      } catch (e) {
        // 401/403 (Token abgelaufen / kein Admin) etc. nicht als uncaught
        // Runtime-Error hochblubbern lassen — sonst React-Error-Overlay.
        // Auth-Interceptor / ProtectedRoute kuemmern sich um Redirect.
        if (!stopped) console.warn("Admin-Übersicht konnte nicht laden:", e?.response?.status || e);
      } finally {
        if (!stopped) setLoading(false);
      }
    };
    load();
    const t = setInterval(load, 60000);
    return () => { stopped = true; clearInterval(t); };
  }, []);

  return (
    <div>
      <PageHeader
        title="Übersicht"
        subtitle="Live-Snapshot der Plattform-Aktivität"
      />

      {loading ? (
        <div className="flex items-center gap-2 text-zinc-500 text-sm"><Spinner /> lade…</div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
            <StatCard label="Nutzer"           value={fmtNum(stats?.users)}            color="blue" />
            <StatCard label="Aktive Abos"      value={fmtNum(stats?.active_subs)}      color="green" />
            <StatCard label="Verträge gesamt"  value={fmtNum(stats?.contracts)}        color="purple" />
            <StatCard label="Vergleiche heute" value={fmtNum(stats?.comparisons_today)} color="orange" />
          </div>

          {urls && (
            <Card className="mt-6">
              <div className="flex items-center justify-between mb-4">
                <div>
                  <div className="text-[15px] font-semibold text-white">URL-Aufrufe nach Quelle</div>
                  <div className="text-[12px] text-zinc-500">Letzte 24 Stunden</div>
                </div>
              </div>
              <UrlBars data={urls.last_24h} />
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function UrlBars({ data }) {
  const sources = [
    { key: "mobile",        label: "mobile.de",       color: "#0a84ff" },
    { key: "kleinanzeigen", label: "kleinanzeigen.de", color: "#34c759" },
    { key: "autoscout",     label: "autoscout24.de",   color: "#ff9500" },
    { key: "other",         label: "sonstige",         color: "#8e8e93" },
  ];
  const max = Math.max(1, ...sources.map((s) => data?.[s.key] || 0));
  return (
    <div className="space-y-3">
      {sources.map((s) => {
        const v = data?.[s.key] || 0;
        const pct = (v / max) * 100;
        return (
          <div key={s.key} className="flex items-center gap-3">
            <div className="w-32 text-[13px] text-zinc-300 truncate">{s.label}</div>
            <div className="flex-1 h-2 rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.08)" }}>
              <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: s.color }} />
            </div>
            <div className="w-14 text-right tabular-nums text-[13px] font-semibold text-white">{v}</div>
          </div>
        );
      })}
      <div className="pt-2 flex items-center justify-between text-[13px]" style={{ borderTop: "1px solid rgba(255,255,255,0.06)" }}>
        <span className="text-zinc-400">Gesamt</span>
        <span className="font-semibold tabular-nums text-white">{data?.total || 0}</span>
      </div>
    </div>
  );
}
