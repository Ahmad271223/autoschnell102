import { useEffect, useState, useCallback } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { RefreshCw, CheckCircle2, Trash2, ChevronDown, ChevronUp } from "lucide-react";

export default function AdminErrors() {
  const [items, setItems] = useState(null);
  const [status, setStatus] = useState("open");
  const [loading, setLoading] = useState(true);
  const [openId, setOpenId] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = status ? `?status=${status}` : "";
      const r = await api.get(`/admin/errors${params}`);
      setItems(r.data);
    } catch (e) {
      console.warn("Fehler-Liste konnte nicht laden:", e?.response?.status || e);
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => { load(); }, [load]);

  const resolve = async (id, newStatus) => {
    try {
      await api.put(`/admin/errors/${id}`, { status: newStatus });
      toast.success(newStatus === "resolved" ? "Als erledigt markiert" : "Wieder geöffnet");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const clearResolved = async () => {
    try {
      const r = await api.delete("/admin/errors");
      toast.success(`${r.data.deleted} erledigte Einträge gelöscht`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  return (
    <div>
      <PageHeader
        title="Fehler"
        subtitle="Automatisch gemeldete Server- und Frontend-Fehler"
        action={
          <div className="flex gap-2">
            <Button variant="secondary" size="sm" onClick={clearResolved}>
              <Trash2 size={14} /> Erledigte löschen
            </Button>
            <Button variant="secondary" size="sm" onClick={load}>
              <RefreshCw size={14} /> Aktualisieren
            </Button>
          </div>
        }
      />

      <div className="flex items-center gap-2 mb-4">
        {[
          { key: "open", label: "Offen" },
          { key: "resolved", label: "Erledigt" },
          { key: "", label: "Alle" },
        ].map((f) => (
          <button
            key={f.key}
            onClick={() => setStatus(f.key)}
            className={`px-3 py-1.5 rounded-lg text-[13px] transition-all ${
              status === f.key
                ? "bg-white/15 text-white font-semibold"
                : "bg-white/5 text-zinc-400 hover:text-white"
            }`}
          >
            {f.label}
          </button>
        ))}
      </div>

      {loading && !items ? (
        <div className="flex items-center gap-2 text-zinc-500 text-sm"><Spinner /> lade…</div>
      ) : !items?.length ? (
        <EmptyState
          title={status === "open" ? "Keine offenen Fehler 🎉" : "Keine Einträge"}
          hint="Unbehandelte Server-Fehler und gemeldete Frontend-Fehler erscheinen hier automatisch."
        />
      ) : (
        <div className="space-y-3">
          {items.map((it) => {
            const expanded = openId === it.id;
            return (
              <Card key={it.id} padded={false}>
                <div className="p-4">
                  <div className="flex flex-wrap items-start gap-2">
                    <Badge tone={it.status === "open" ? "red" : "green"}>
                      {it.status === "open" ? "Offen" : "Erledigt"}
                    </Badge>
                    <Badge tone={it.source === "frontend" ? "blue" : "orange"}>
                      {it.source === "frontend" ? "Frontend" : "Backend"}
                    </Badge>
                    <span className="text-[12px] text-zinc-500 tabular-nums">{fmtDate(it.created_at)}</span>
                    <span className="text-[12px] text-zinc-500">Ref: {String(it.id).slice(0, 8)}</span>
                    <div className="ml-auto flex gap-2">
                      {it.status === "open" ? (
                        <Button size="sm" variant="secondary" onClick={() => resolve(it.id, "resolved")}>
                          <CheckCircle2 size={14} /> Erledigt
                        </Button>
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => resolve(it.id, "open")}>
                          Wieder öffnen
                        </Button>
                      )}
                    </div>
                  </div>
                  <div className="mt-2 text-[14px] text-white font-medium break-words">
                    {it.error_type && <span className="text-red-300">{it.error_type}: </span>}
                    {it.message || "—"}
                  </div>
                  <div className="mt-1 text-[12px] text-zinc-500 break-all">
                    {it.method ? `${it.method} ` : ""}{it.path || "—"}
                    {it.user_email ? ` · ${it.user_email}` : ""}
                    {it.ip ? ` · IP ${it.ip}` : ""}
                  </div>
                  {it.traceback && (
                    <button
                      onClick={() => setOpenId(expanded ? null : it.id)}
                      className="mt-2 flex items-center gap-1 text-[12px] text-zinc-400 hover:text-white"
                    >
                      {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                      {expanded ? "Details ausblenden" : "Stacktrace anzeigen"}
                    </button>
                  )}
                  {expanded && it.traceback && (
                    <pre className="mt-2 p-3 rounded-lg text-[11px] leading-relaxed text-zinc-300 overflow-x-auto whitespace-pre-wrap break-all"
                         style={{ background: "rgba(0,0,0,0.4)", border: "1px solid rgba(255,255,255,0.06)" }}>
                      {it.traceback}
                    </pre>
                  )}
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
