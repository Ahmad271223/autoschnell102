import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";
import { Plus, Trash2, Copy, User, Mail, KeyRound } from "lucide-react";

export default function Fahrer() {
  const [items, setItems] = useState([]);
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);

  const load = () => api.get("/drivers").then((r) => setItems(r.data)).catch(() => {});
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    const c = code.trim().toUpperCase();
    if (!c) return;
    setLoading(true);
    try {
      await api.post("/drivers/add", { driver_code: c });
      toast.success("Fahrer hinzugefügt");
      setCode("");
      load();
    } catch (err) {
      toast.error(errMsg(err, "Fahrer konnte nicht hinzugefügt werden"));
    } finally {
      setLoading(false);
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Fahrer aus deiner Liste entfernen?")) return;
    await api.delete(`/drivers/${id}`);
    toast.success("Entfernt");
    load();
  };

  const copy = async (c) => {
    try { await navigator.clipboard.writeText(c); toast.success("Kopiert"); } catch {}
  };

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="drivers-page">
      <div className="overline">Fahrer</div>
      <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
        Fahrer-Verwaltung
      </h1>
      <p className="text-sm text-zinc-400 mt-2 max-w-2xl">
        Jeder Fahrer hat einen eigenen Account in der Fahrer-App und bekommt dort
        eine Fahrer-ID (z.B. <code className="px-1 rounded-sm bg-white/5">FD-A7K3M9X2</code>).
        Gib dir diese ID vom Fahrer geben – und füge ihn hier hinzu.
      </p>

      <form onSubmit={add} className="tactical-card p-5 mt-6 flex flex-col sm:flex-row gap-3 items-end">
        <div className="flex-1 w-full">
          <label className="text-xs text-zinc-400 flex items-center gap-2">
            <KeyRound size={11} /> Fahrer-ID
          </label>
          <input data-testid="driver-code-input" value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="FD-XXXXXXXX"
            className="input-base w-full mt-1 font-mono tracking-[0.1em]" />
        </div>
        <button type="submit" data-testid="add-driver-btn" disabled={loading}
          className="kinetic-button px-5 py-2.5 rounded-sm font-bold flex items-center justify-center gap-2 w-full sm:w-auto disabled:opacity-50">
          <Plus size={15} /> Hinzufügen
        </button>
      </form>

      <div className="mt-6 tactical-card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left overline" style={{ background: "rgba(255,255,255,0.02)" }}>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Fahrer-ID</th>
              <th className="px-4 py-3">E-Mail</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Aktion</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-zinc-500">
                <User size={22} className="mx-auto mb-2 opacity-50" />
                Noch keine Fahrer. Frag deine Fahrer nach ihrer Fahrer-ID.
              </td></tr>
            )}
            {items.map((d) => (
              <tr key={d.id} className="border-t" style={{ borderColor: "var(--border-default)" }}
                  data-testid={`driver-row-${d.id}`}>
                <td className="px-4 py-3 font-semibold">{d.name}</td>
                <td className="px-4 py-3">
                  <button onClick={() => copy(d.driver_code)}
                    className="inline-flex items-center gap-1.5 font-mono text-xs px-2 py-1 rounded-sm bg-white/5 hover:bg-white/10"
                    title="Kopieren">
                    {d.driver_code} <Copy size={10} />
                  </button>
                </td>
                <td className="px-4 py-3 text-zinc-400 text-xs">
                  <span className="inline-flex items-center gap-1">
                    <Mail size={11} />{d.email || "—"}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-xs px-2 py-0.5 rounded-sm"
                    style={{ background: d.active ? "rgba(52,199,89,0.12)" : "rgba(255,255,255,0.04)",
                             color: d.active ? "var(--accent-green)" : "var(--text-muted)" }}>
                    {d.active ? "aktiv" : "inaktiv"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <button onClick={() => remove(d.id)} data-testid={`del-driver-${d.id}`}
                    className="p-2 hover:bg-white/5 rounded-sm">
                    <Trash2 size={14} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
