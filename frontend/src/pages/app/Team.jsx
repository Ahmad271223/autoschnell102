import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { TrendingUp, UserPlus, Info } from "lucide-react";

/**
 * Mitarbeiter / Sucher-Übersicht + Weiterverkaufsplan.
 *
 * Seit 09/2026 legt der BETREIBER die Sucher-Konten an (inkl. Anmeldename
 * und Passwort), schaltet Abos nach Rechnungszahlung frei und kann sperren/
 * löschen. Diese Seite zeigt dem Chef sein Team, den Abo-Stand je Sucher
 * (inkl. nächster Zahlung) und sendet Anfragen an den Betreiber.
 */

const fmtDatum = (iso) => {
  if (!iso) return "—";
  try { return new Date(iso).toLocaleDateString("de-DE"); } catch { return iso.slice(0, 10); }
};

export default function Team() {
  const [sucher, setSucher] = useState([]);
  const [plan, setPlan] = useState(null);
  const [sucherPlans, setSucherPlans] = useState(null);

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
      toast.success("Anfrage an den Betreiber gesendet — nach Zahlungseingang wird freigeschaltet");
    } catch (e) { toast.error(errMsg(e)); }
  };

  const requestUpgrade = async (tier) => {
    try {
      await api.post("/dealer/sale-plan/upgrade-request", { wanted_tier: tier, message: "" });
      toast.success("Anfrage an den Betreiber gesendet");
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
        <a href={"mailto:support@autohandel.app?subject=Weitere%20Sucher-Zug%C3%A4nge&body=Hallo%2C%20wir%20brauchen%20weitere%20Sucher-Zug%C3%A4nge%20f%C3%BCr%20unsere%20Firma."}
           data-testid="team-request-sucher"
           className="inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
           style={{ background: "var(--accent-red)" }}>
          <UserPlus size={16} /> Weitere Sucher anfragen
        </a>
      </div>

      {/* Hinweis: Verwaltung durch den Betreiber */}
      <div className="mt-6 rounded-xl border px-4 py-3 flex items-start gap-3 text-sm"
           style={{ borderColor: "var(--border-default)", background: "rgba(255,255,255,0.02)" }}
           data-testid="team-betreiber-hinweis">
        <Info size={16} className="mt-0.5 shrink-0" style={{ color: "var(--accent-red)" }} />
        <div className="text-zinc-300">
          <b>Sucher-Konten legt der Betreiber für dich an</b>{" "}
          <span className="text-zinc-500">
            — Anmeldename und Passwort bekommt jeder Sucher direkt von uns.
            Sag uns einfach, wie viele Zugänge du brauchst (jederzeit erweiterbar).
            Freischaltung nach Rechnungszahlung: 150&nbsp;€/Monat oder
            1.500&nbsp;€/Jahr je Sucher. Willst du selbst suchen &amp; vergleichen,
            gilt dasselbe für deinen eigenen Zugang (<Link to="/abo" className="underline hover:text-white">Details</Link>).
          </span>
        </div>
      </div>

      {/* Weiterverkaufsplan */}
      <div className="mt-6 tactical-card p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-sm font-bold uppercase tracking-wide inline-flex items-center gap-2">
              <TrendingUp size={15} /> Weiterverkaufsplan
            </div>
            {plan?.kostenlos ? (
              <div className="mt-1 text-xs text-zinc-500">
                Fahrzeuge verkaufen ist kostenlos — du kannst beliebig viele veröffentlichen.
              </div>
            ) : plan?.active ? (
              <div className="mt-1 text-xs text-zinc-500">
                Abrechnungszeitraum {plan.period_start?.slice(0, 10)} – {plan.period_end?.slice(0, 10)}
              </div>
            ) : (
              <div className="mt-1 text-xs text-zinc-500">
                Kein Verkaufspaket aktiv — zum Veröffentlichen von Fahrzeugen wird eines benötigt.
              </div>
            )}
          </div>
          {plan?.kostenlos ? (
            <div className="text-right">
              <div className="text-2xl font-black">kostenlos</div>
              <div className="text-[11px] text-zinc-500">unbegrenzt viele Fahrzeuge</div>
            </div>
          ) : plan?.active ? (
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
          {plan?.kostenlos
            ? "Das Veröffentlichen und Verkaufen von Fahrzeugen kostet derzeit nichts und ist nicht begrenzt."
            : "Paketwechsel erfolgt über den Betreiber — dein Klick sendet eine Anfrage. Gezählt werden nur neu veröffentlichte Fahrzeuge im Zeitraum; Entwürfe zählen nie."}
        </div>
      </div>

      {/* Sucher-Liste (read-only) */}
      <div className="mt-6 tactical-card overflow-hidden">
        <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr className="text-left overline" style={{ background: "rgba(255,255,255,0.02)" }}>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">E-Mail</th>
              <th className="px-4 py-3">Sucher-Abo</th>
              <th className="px-4 py-3">Nächste Zahlung</th>
              <th className="px-4 py-3">Käufe (Monat)</th>
              <th className="px-4 py-3">Vergleiche</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {sucher.length === 0 && (
              <tr><td colSpan={7} className="px-4 py-10 text-center text-zinc-500">
                Noch keine Sucher angelegt — melde dich beim Betreiber, wir richten die Zugänge für dich ein.
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
                    <span className="text-emerald-400 text-xs">aktiv ({s.subscription.plan === "yearly" ? "jährlich" : "monatlich"})</span>
                  ) : (
                    <div className="flex flex-col gap-1">
                      <span className="text-amber-400 text-xs">nicht freigeschaltet</span>
                      <div className="flex gap-1">
                        {Object.entries(sucherPlans || {}).map(([k, p]) => (
                          <button key={k} onClick={() => requestAbo(s, k)}
                                  title={`${p.label} anfragen — Freischaltung nach Rechnungszahlung`}
                                  className="px-2 py-0.5 rounded-md border text-[10px] text-zinc-300 hover:text-white"
                                  style={{ borderColor: "var(--border-default)" }}>
                            {p.price.toLocaleString("de-DE")} €{k === "monthly" ? "/M" : "/J"} anfragen
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 text-zinc-400 tabular-nums">
                  {s.subscription?.active ? fmtDatum(s.subscription.expires_at) : "—"}
                </td>
                <td className="px-4 py-3 tabular-nums">{s.stats_month?.kaeufe ?? 0}</td>
                <td className="px-4 py-3 tabular-nums">{s.stats_month?.vergleiche ?? 0}</td>
                <td className="px-4 py-3">
                  <span className={`text-xs px-2 py-0.5 rounded-md border ${s.active ? "text-emerald-400" : "text-zinc-500"}`}
                        style={{ borderColor: "var(--border-default)" }}>
                    {s.active ? "aktiv" : "gesperrt"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
        <div className="px-4 py-3 text-[11px] text-zinc-600 border-t" style={{ borderColor: "var(--border-default)" }}>
          Passwörter, Sperrungen und Löschungen laufen über den Betreiber — so bleibt
          nachvollziehbar, wer Zugriff hat und was bezahlt wurde.
        </div>
      </div>
    </div>
  );
}
