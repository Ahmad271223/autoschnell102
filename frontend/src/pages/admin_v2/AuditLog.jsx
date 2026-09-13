import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { RefreshCw } from "lucide-react";

// Farbe je Aktions-Präfix, damit man die Liste schnell scannen kann.
function toneFor(action = "") {
  if (action.startsWith("auth.login.fehlgeschlagen")) return "red";
  if (action.startsWith("auth.")) return "blue";
  if (action.startsWith("admin.")) return "purple";
  if (action.startsWith("pdf.")) return "green";
  if (action.startsWith("termin.")) return "orange";
  if (action.startsWith("vergleich.")) return "yellow";
  return "gray";
}

const FILTERS = [
  { key: "",        label: "Alle" },
  { key: "auth.",   label: "Anmeldungen" },
  { key: "admin.",  label: "Admin-Aktionen" },
  { key: "pdf.",    label: "Verträge" },
  { key: "termin.", label: "Termine" },
  { key: "vergleich.", label: "Vergleiche" },
];

export default function AdminAuditLog() {
  const [items, setItems] = useState(null);
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "300" });
      if (filter) params.set("action", filter);
      if (q.trim()) params.set("q", q.trim());
      const r = await api.get(`/admin/audit?${params.toString()}`);
      setItems(r.data);
    } catch (e) {
      console.warn("Audit-Log konnte nicht laden:", e?.response?.status || e);
    } finally {
      setLoading(false);
    }
  }, [filter, q]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <PageHeader
        title="Audit-Log"
        subtitle="Wer hat wann was gemacht — Anmeldungen, Verträge, Admin-Aktionen"
        action={
          <Button variant="secondary" size="sm" onClick={load}>
            <RefreshCw size={14} /> Aktualisieren
          </Button>
        }
      />

      <div className="flex flex-wrap items-center gap-2 mb-4">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            className={`px-3 py-1.5 rounded-lg text-[13px] transition-all ${
              filter === f.key
                ? "bg-white/15 text-white font-semibold"
                : "bg-white/5 text-zinc-400 hover:text-white"
            }`}
          >
            {f.label}
          </button>
        ))}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && load()}
          placeholder="Suche (E-Mail, Aktion, Ref) …"
          className="ml-auto h-9 px-3 rounded-lg text-[13px] bg-white/5 border border-white/10 text-white placeholder-zinc-500 outline-none focus:border-white/25 w-64"
        />
      </div>

      {loading && !items ? (
        <div className="flex items-center gap-2 text-zinc-500 text-sm"><Spinner /> lade…</div>
      ) : !items?.length ? (
        <EmptyState title="Keine Einträge" hint="Für diesen Filter gibt es noch keine Aktivitäten." />
      ) : (
        <Card padded={false}>
          <div className="overflow-x-auto">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                  <th className="px-4 py-3 font-medium">Zeitpunkt</th>
                  <th className="px-4 py-3 font-medium">Aktion</th>
                  <th className="px-4 py-3 font-medium">Nutzer</th>
                  <th className="px-4 py-3 font-medium">Details</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const meta = it.meta || {};
                  const detailBits = [];
                  if (meta.ip) detailBits.push(`IP ${meta.ip}`);
                  if (meta.plan) detailBits.push(`Plan: ${meta.plan}`);
                  if (meta.felder?.length) detailBits.push(`Felder: ${meta.felder.join(", ")}`);
                  if (it.ref) detailBits.push(`Ref: ${String(it.ref).slice(0, 8)}`);
                  return (
                    <tr key={it.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                      <td className="px-4 py-2.5 whitespace-nowrap text-zinc-400 tabular-nums">
                        {fmtDate(it.created_at)}
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge tone={toneFor(it.action)}>{it.action}</Badge>
                      </td>
                      <td className="px-4 py-2.5 text-white">
                        {it.email || it.username || "—"}
                        {it.role === "admin" && (
                          <span className="ml-1.5 text-[10px] text-purple-300">Admin</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 text-zinc-500">
                        {detailBits.join(" · ") || "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
