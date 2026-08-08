import { useCallback, useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Plus, X, KeyRound, Trash2, TrendingUp } from "lucide-react";

/**
 * Mitarbeiter / Sucher-Verwaltung (Phase 2) + Weiterverkaufsplan.
 * Der Händleraccount ist kostenlos; jeder Sucher braucht ein eigenes
 * Sucher-Abo (Vergabe aktuell durch den Admin). Das Verkaufspaket zählt
 * nur tatsächlich veröffentlichte Fahrzeuge pro Abrechnungszeitraum.
 */

export default function Team() {
  const [sucher, setSucher] = useState([]);
  const [plan, setPlan] = useState(null);
  const [sucherPlans, setSucherPlans] = useState(null);
  const [showAdd, setShowAdd] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, p, sp] = await Promise.all([
        api.get("/dealer/sucher"),
        api.get("/dealer/sale-plan"),
        api.get("/dealer/sucher-plans"),
      ]);
      setSucher(s.data);
      setPlan(p.data);
      setSucherPlans(sp.data.plans);
    } catch (e) {
      toast.error(errMsg(e, "Team konnte nicht geladen werden"));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const requestAbo = async (s, planKey) => {
    try {
      await api.post(`/dealer/sucher/${s.id}/abo-anfrage`, { plan: planKey });
      toast.success("Abo-Anfrage an den Administrator gesendet");
    } catch (e) { toast.error(errMsg(e)); }
  };

  const toggleActive = async (s) => {
    try {
      await api.put(`/dealer/sucher/${s.id}`, { active: !s.active });
      toast.success(s.active ? "Sucher deaktiviert" : "Sucher aktiviert");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const resetPassword = async (s) => {
    const pw = window.prompt(`Neues Passwort für ${s.email} (min. 8 Zeichen):`);
    if (!pw) return;
    try {
      await api.put(`/dealer/sucher/${s.id}`, { password: pw });
      toast.success("Passwort gesetzt — Sucher muss sich neu anmelden");
    } catch (e) { toast.error(errMsg(e)); }
  };

  const remove = async (s) => {
    if (!window.confirm(`Sucher ${s.email} wirklich löschen?`)) return;
    try {
      await api.delete(`/dealer/sucher/${s.id}`);
      toast.success("Sucher gelöscht");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const requestUpgrade = async (tier) => {
    try {
      await api.post("/dealer/sale-plan/upgrade-request", { wanted_tier: tier, message: "" });
      toast.success("Anfrage an den Administrator gesendet");
    } catch (e) { toast.error(errMsg(e)); }
  };

  const pct = plan?.quota ? Math.min(100, (plan.used / plan.quota) * 100) : 0;

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-6xl mx-auto" data-testid="team-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="overline">Mitarbeiter</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
            Sucher & Verkaufsplan
          </h1>
        </div>
        <button onClick={() => setShowAdd(true)}
                className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                style={{ background: "var(--accent-red)" }}>
          <Plus size={16} /> Neuen Sucher hinzufügen
        </button>
      </div>

      {/* Weiterverkaufsplan */}
      <div className="mt-6 tactical-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-bold uppercase tracking-wide inline-flex items-center gap-2">
              <TrendingUp size={15} /> Weiterverkaufsplan
            </div>
            {plan?.active ? (
              <div className="mt-1 text-xs text-zinc-500">
                Abrechnungszeitraum {plan.period_start?.slice(0, 10)} – {plan.period_end?.slice(0, 10)}
              </div>
            ) : (
              <div className="mt-1 text-xs text-zinc-500">
                Kein Verkaufspaket aktiv — zum Veröffentlichen von Fahrzeugen wird eines benötigt.
              </div>
            )}
          </div>
          {plan?.active ? (
            <div className="text-right">
              <div className="text-2xl font-black tabular-nums">
                {plan.used} / {plan.quota ?? "∞"}
              </div>
              <div className="text-[11px] text-zinc-500">Fahrzeuge veröffentlicht ({plan.label})</div>
            </div>
          ) : null}
        </div>
        {plan?.active && plan.quota ? (
          <div className="mt-3 h-2 rounded-full overflow-hidden" style={{ background: "rgba(255,255,255,0.08)" }}>
            <div className="h-full rounded-full" style={{ width: `${pct}%`, background: pct >= 100 ? "#ff3b30" : "var(--accent-red)" }} />
          </div>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(plan?.plans || {}).map(([tier, p]) => (
            <button key={tier} onClick={() => requestUpgrade(tier)}
                    className={`px-3 py-1.5 rounded-lg text-[12px] border ${plan?.tier === tier ? "bg-white/10 font-semibold" : "text-zinc-400 hover:text-white"}`}
                    style={{ borderColor: "var(--border-default)" }}
                    title={tier === "enterprise" ? "Individuelle Anfrage" : `${p.quota} Fahrzeuge / Monat`}>
              {p.label}{p.price != null ? ` · ${p.price.toFixed(2).replace(".", ",")} €` : " · anfragen"}
            </button>
          ))}
        </div>
        <div className="mt-2 text-[10px] text-zinc-600">
          Paketwechsel erfolgt aktuell über den Administrator — dein Klick sendet eine Anfrage.
          Gezählt werden nur neu veröffentlichte Fahrzeuge im Zeitraum; Entwürfe zählen nie.
        </div>
      </div>

      {/* Sucher-Liste */}
      <div className="mt-6 tactical-card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left overline" style={{ background: "rgba(255,255,255,0.02)" }}>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">E-Mail</th>
              <th className="px-4 py-3">Sucher-Abo</th>
              <th className="px-4 py-3">Käufe (Monat)</th>
              <th className="px-4 py-3">Vergleiche</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Aktionen</th>
            </tr>
          </thead>
          <tbody>
            {sucher.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-zinc-500">
                Noch keine Sucher angelegt. Der Händleraccount selbst kann weiterhin suchen (gilt als erster Sucher).
              </td></tr>
            )}
            {sucher.map((s) => (
              <tr key={s.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                <td className="px-4 py-3 font-semibold">
                  {s.first_name} {s.last_name}
                  {s.employee_id && <span className="ml-2 text-[10px] text-zinc-500">#{s.employee_id}</span>}
                </td>
                <td className="px-4 py-3 text-zinc-400">{s.email}</td>
                <td className="px-4 py-3">
                  {s.subscription?.active ? (
                    <span className="text-emerald-400 text-xs">aktiv ({s.subscription.plan})</span>
                  ) : (
                    <div className="flex flex-col gap-1">
                      <span className="text-amber-400 text-xs">kein Abo</span>
                      <div className="flex gap-1">
                        {Object.entries(sucherPlans || {}).map(([k, p]) => (
                          <button key={k} onClick={() => requestAbo(s, k)}
                                  title={`${p.label} — Anfrage an Admin`}
                                  className="px-2 py-0.5 rounded-md border text-[10px] text-zinc-300 hover:text-white"
                                  style={{ borderColor: "var(--border-default)" }}>
                            {p.price.toLocaleString("de-DE")} €{k === "monthly" ? "/M" : "/J"}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 tabular-nums">{s.stats_month?.kaeufe ?? 0}</td>
                <td className="px-4 py-3 tabular-nums">{s.stats_month?.vergleiche ?? 0}</td>
                <td className="px-4 py-3">
                  <button onClick={() => toggleActive(s)}
                          className={`text-xs px-2 py-0.5 rounded-md border ${s.active ? "text-emerald-400" : "text-zinc-500"}`}
                          style={{ borderColor: "var(--border-default)" }}>
                    {s.active ? "aktiv" : "deaktiviert"}
                  </button>
                </td>
                <td className="px-4 py-3">
                  <div className="flex justify-end gap-2">
                    <button onClick={() => resetPassword(s)} title="Passwort zurücksetzen"
                            className="text-zinc-500 hover:text-white"><KeyRound size={15} /></button>
                    <button onClick={() => remove(s)} title="Löschen"
                            className="text-zinc-500 hover:text-red-400"><Trash2 size={15} /></button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showAdd && <AddSucherDialog onClose={() => setShowAdd(false)} onDone={() => { setShowAdd(false); load(); }} />}
    </div>
  );
}

function AddSucherDialog({ onClose, onDone }) {
  const [f, setF] = useState({ first_name: "", last_name: "", email: "", password: "", phone: "", employee_id: "" });
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async () => {
    if (!f.first_name || !f.last_name || !f.email || f.password.length < 8) {
      toast.error("Bitte Name, E-Mail und Passwort (min. 8 Zeichen) angeben"); return;
    }
    setBusy(true);
    try {
      const r = await api.post("/dealer/sucher", f);
      toast.success(r.data.hinweis || "Sucher angelegt");
      onDone?.();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };

  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.7)" }}>
      <div className="w-full max-w-md rounded-2xl p-5" style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)" }}>
        <div className="flex items-center justify-between mb-3">
          <div className="text-lg font-bold">Neuen Sucher hinzufügen</div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <input value={f.first_name} onChange={set("first_name")} placeholder="Vorname *" className={inputCls} style={st} />
          <input value={f.last_name} onChange={set("last_name")} placeholder="Nachname *" className={inputCls} style={st} />
          <div className="col-span-2"><input value={f.email} onChange={set("email")} placeholder="E-Mail *" className={inputCls} style={st} /></div>
          <div className="col-span-2"><input type="password" value={f.password} onChange={set("password")} placeholder="Start-Passwort (min. 8 Zeichen) *" className={inputCls} style={st} /></div>
          <input value={f.phone} onChange={set("phone")} placeholder="Telefon" className={inputCls} style={st} />
          <input value={f.employee_id} onChange={set("employee_id")} placeholder="Mitarbeiter-ID" className={inputCls} style={st} />
        </div>
        <div className="mt-3 text-[11px] text-zinc-500">
          Der Sucher meldet sich mit E-Mail + Passwort in der normalen App an.
          Zum Suchen/Vergleichen benötigt er ein aktives Sucher-Abo.
        </div>
        <button onClick={submit} disabled={busy}
                className="mt-4 w-full rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                style={{ background: "var(--accent-red)" }}>
          {busy ? "Wird angelegt…" : "Sucher anlegen"}
        </button>
      </div>
    </div>
  );
}
