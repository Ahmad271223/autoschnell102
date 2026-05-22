import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Plus, Trash2, Lock, Unlock, ShieldCheck } from "lucide-react";

export default function AdminDashboard() {
  const [stats, setStats] = useState(null);
  const [users, setUsers] = useState([]);
  const [contracts, setContracts] = useState([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ email: "", password: "", company_name: "", plan_type: "monthly", expires_at: "", active: true });

  const load = async () => {
    const [s, u, c] = await Promise.all([
      api.get("/admin/stats"), api.get("/admin/users"), api.get("/admin/contracts"),
    ]);
    setStats(s.data); setUsers(u.data); setContracts(c.data);
  };
  useEffect(() => { load(); }, []);

  const create = async (e) => {
    e.preventDefault();
    try {
      await api.post("/admin/users", { ...form, expires_at: form.expires_at || null });
      toast.success("Account erstellt");
      setCreating(false);
      setForm({ email: "", password: "", company_name: "", plan_type: "monthly", expires_at: "", active: true });
      load();
    } catch (err) {
      toast.error(errMsg(err, "Fehler"));
    }
  };

  const toggleActive = async (u) => {
    await api.put(`/admin/users/${u.id}`, { active: !u.active });
    load();
  };
  const setLifetime = async (u) => {
    await api.put(`/admin/users/${u.id}`, { plan_type: "lifetime" });
    toast.success("Auf Lifetime gesetzt");
    load();
  };
  const remove = async (u) => {
    if (!window.confirm(`Account ${u.email} löschen?`)) return;
    await api.delete(`/admin/users/${u.id}`);
    load();
  };

  return (
    <div className="p-6 lg:p-10 max-w-7xl mx-auto" data-testid="admin-page">
      <div className="overline flex items-center gap-2"><ShieldCheck size={12} /> Admin Dashboard</div>
      <h1 className="font-display font-black text-3xl lg:text-5xl tracking-tighter mt-1">Plattform-Übersicht</h1>

      <div className="mt-6 grid grid-cols-2 md:grid-cols-5 gap-3">
        <KPI label="Nutzer" value={stats?.users ?? "—"} />
        <KPI label="Aktive Abos" value={stats?.active_subs ?? "—"} />
        <KPI label="Verträge" value={stats?.contracts ?? "—"} />
        <KPI label="Termine" value={stats?.appointments ?? "—"} />
        <KPI label="Vergleiche heute" value={stats?.comparisons_today ?? "—"} />
      </div>

      <div className="mt-8 flex items-center justify-between">
        <h2 className="font-display font-bold text-2xl">Accounts</h2>
        <button onClick={() => setCreating(!creating)} data-testid="admin-new-account"
                className="kinetic-button px-4 py-2 rounded-sm flex items-center gap-2 font-bold">
          <Plus size={15} /> Neuer Account
        </button>
      </div>

      {creating && (
        <form onSubmit={create} className="tactical-card p-5 mt-4 grid md:grid-cols-3 gap-3" data-testid="admin-create-form">
          <Field label="E-Mail" type="email" required value={form.email} onChange={(v) => setForm({ ...form, email: v })} testid="adm-email" />
          <Field label="Passwort" type="password" required value={form.password} onChange={(v) => setForm({ ...form, password: v })} testid="adm-password" />
          <Field label="Firmenname" required value={form.company_name} onChange={(v) => setForm({ ...form, company_name: v })} testid="adm-company" />
          <div>
            <label className="text-xs text-zinc-400">Zugriffstyp</label>
            <select data-testid="adm-plan" value={form.plan_type} onChange={(e) => setForm({ ...form, plan_type: e.target.value })}
                    className="input-base w-full mt-1">
              <option value="monthly">Monatlich</option>
              <option value="yearly">Jährlich</option>
              <option value="lifetime">Lifetime</option>
              <option value="trial">Testzugang (14 Tage)</option>
            </select>
          </div>
          <Field label="Ablaufdatum (optional ISO)" value={form.expires_at} onChange={(v) => setForm({ ...form, expires_at: v })} testid="adm-expires" />
          <div className="flex items-end">
            <button type="submit" data-testid="adm-create-submit" className="kinetic-button w-full py-2.5 rounded-sm font-bold">Erstellen</button>
          </div>
        </form>
      )}

      <div className="tactical-card overflow-hidden mt-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left overline" style={{ background: "rgba(255,255,255,0.02)" }}>
              <th className="px-4 py-3">E-Mail</th>
              <th className="px-4 py-3">Firma</th>
              <th className="px-4 py-3">Rolle</th>
              <th className="px-4 py-3">Plan</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Aktion</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                <td className="px-4 py-3 font-mono text-xs">{u.email}</td>
                <td className="px-4 py-3">{u.company_name || "—"}</td>
                <td className="px-4 py-3 text-zinc-400">{u.role}</td>
                <td className="px-4 py-3">
                  <span className="text-xs px-2 py-0.5 rounded-sm border" style={{ borderColor: "var(--border-default)" }}>
                    {u.subscription?.plan || "—"}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-xs px-2 py-0.5 rounded-sm"
                        style={{ background: u.active ? "rgba(52,199,89,0.12)" : "rgba(255,59,48,0.12)",
                                 color: u.active ? "var(--accent-green)" : "var(--accent-red)" }}>
                    {u.active ? "aktiv" : "deaktiviert"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="inline-flex gap-1">
                    <button onClick={() => toggleActive(u)} className="p-2 hover:bg-white/5 rounded-sm" title="Aktiv/Inaktiv"
                            data-testid={`adm-toggle-${u.id}`}>
                      {u.active ? <Lock size={13} /> : <Unlock size={13} />}
                    </button>
                    {u.subscription?.plan !== "lifetime" && (
                      <button onClick={() => setLifetime(u)} className="px-2 py-1 text-[10px] uppercase tracking-wider rounded-sm border hover:bg-white/5"
                              data-testid={`adm-lifetime-${u.id}`} style={{ borderColor: "var(--border-default)" }}>
                        Lifetime
                      </button>
                    )}
                    {u.role !== "admin" && (
                      <button onClick={() => remove(u)} className="p-2 hover:bg-white/5 rounded-sm" data-testid={`adm-del-${u.id}`}>
                        <Trash2 size={13} className="text-red-400" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="font-display font-bold text-2xl mt-10">Alle Verträge (lesend)</h2>
      <div className="tactical-card overflow-hidden mt-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left overline" style={{ background: "rgba(255,255,255,0.02)" }}>
              <th className="px-4 py-3">Datum</th>
              <th className="px-4 py-3">Händler-ID</th>
              <th className="px-4 py-3">Fahrzeug</th>
              <th className="px-4 py-3">Verkäufer</th>
              <th className="px-4 py-3">Preis</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {contracts.map((c) => (
              <tr key={c.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                <td className="px-4 py-3 font-mono text-xs text-zinc-400">{new Date(c.created_at).toLocaleString("de-DE")}</td>
                <td className="px-4 py-3 font-mono text-xs">{c.dealer_id?.slice(0, 8)}…</td>
                <td className="px-4 py-3">{c.make} {c.model}</td>
                <td className="px-4 py-3">{c.seller_name}</td>
                <td className="px-4 py-3">{c.purchase_price ? `${Number(c.purchase_price).toLocaleString("de-DE")} €` : "—"}</td>
                <td className="px-4 py-3">{c.status}</td>
              </tr>
            ))}
            {contracts.length === 0 && (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-zinc-500">Noch keine Verträge.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const KPI = ({ label, value }) => (
  <div className="tactical-card p-4">
    <div className="overline">{label}</div>
    <div className="font-display font-black text-3xl mt-1">{value}</div>
  </div>
);

const Field = ({ label, value, onChange, type = "text", required, testid }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    <input data-testid={testid} type={type} required={required} value={value} onChange={(e) => onChange(e.target.value)}
           className="input-base w-full mt-1" />
  </div>
);
