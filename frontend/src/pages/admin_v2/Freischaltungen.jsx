import { useCallback, useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { RefreshCw, Check, X, Store, Building2 } from "lucide-react";

/**
 * Freischaltungen (Betreiber-Modell 09/2026):
 * - Zugangs-Anfragen neuer Firmen (Startseite) -> Firma direkt anlegen.
 * - Sucher-Abo-Anfragen (150/1500, Rechnung) -> freischalten (erfasst die Zahlung).
 * - Marktplatz-Zugang: 20 EUR via Stripe automatisch; hier manuell aktivieren/sperren.
 */
export default function AdminFreischaltungen() {
  const [requests, setRequests] = useState(null);
  const [buyers, setBuyers] = useState(null);
  const [loading, setLoading] = useState(true);
  const [firmaReq, setFirmaReq] = useState(null); // Zugangs-Anfrage -> Dialog

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [r, b] = await Promise.all([
        api.get("/admin/plan-requests?status=offen"),
        api.get("/admin/buyers"),
      ]);
      setRequests(r.data);
      setBuyers(b.data);
    } catch (e) {
      console.warn("Freischaltungen laden:", e?.response?.status || e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // USt-IdNr. beim EU-Dienst VIES pruefen (Audit 09/2026, Punkt 40)
  const pruefeUstId = async (b) => {
    try {
      const r = await api.post(`/admin/buyers/${b.id}/ustid-pruefen`);
      const e = r.data || {};
      const txt = e.status === "gueltig"
        ? `USt-IdNr. ${e.ust_id} gültig${e.name ? ` · ${e.name}` : ""}${e.adresse ? ` · ${e.adresse}` : ""}`
        : (e.hinweis || e.status);
      (e.status === "gueltig" ? toast.success : e.status === "ungueltig" ? toast.error : toast.warning)(txt, { duration: 9000 });
      load();
    } catch (e) { toast.error(errMsg(e, "Prüfung fehlgeschlagen")); }
  };

  const closeReq = async (id, status = "erledigt") => {
    try { await api.put(`/admin/plan-requests/${id}`, { status }); } catch (e) { /* egal */ }
  };

  const grantSucher = async (req) => {
    try {
      await api.post(`/admin/sucher/${req.subject_user_id}/abo`,
        { plan: req.wanted_plan || "monthly" });
      await closeReq(req.id);
      toast.success(`Sucher-Abo aktiviert (${req.sucher_name || ""})`);
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const grantPlan = async (req) => {
    try {
      await api.put(`/admin/dealers/${req.dealer_id}/sale-plan`, { tier: req.wanted_tier });
      await closeReq(req.id);
      toast.success(`Verkaufspaket ${req.wanted_tier} aktiviert (${req.company_name || ""})`);
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const grantBuyer = async (req) => {
    try {
      await api.post(`/admin/buyers/${req.buyer_user_id}/access`, { plan: "monthly" });
      await closeReq(req.id);
      toast.success("Marktplatz-Zugang aktiviert");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const setBuyerAccess = async (buyer, activate) => {
    try {
      await api.post(`/admin/buyers/${buyer.id}/access`, { plan: activate ? "monthly" : null });
      toast.success(activate ? "Zugang aktiviert" : "Zugang gesperrt");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <div>
      <PageHeader
        title="Freischaltungen"
        subtitle="Sucher-Abos, Marktplatz-Zugänge & offene Anfragen — manuell freigeben"
        action={<Button variant="secondary" size="sm" onClick={load}><RefreshCw size={14} /> Aktualisieren</Button>}
      />

      {loading && !requests ? (
        <div className="flex items-center gap-2 text-zinc-500 text-sm"><Spinner /> lade…</div>
      ) : (
        <div className="space-y-8">
          {/* Offene Anfragen */}
          <div>
            <div className="text-[13px] font-semibold text-zinc-300 mb-3 uppercase tracking-wide">
              Offene Anfragen {requests?.length ? `(${requests.length})` : ""}
            </div>
            {!requests?.length ? (
              <EmptyState title="Keine offenen Anfragen" hint="Neue Sucher-Abo- und Zugangsanfragen erscheinen hier." />
            ) : (
              <div className="space-y-3">
                {requests.map((r) => {
                  const isSucher = r.type === "sucher_abo";
                  const isBuyer = r.type === "buyer_access";
                  const isZugang = r.type === "zugang";
                  return (
                    <Card key={r.id} padded={false}>
                      <div className="p-4 flex flex-wrap items-center gap-3">
                        <Badge tone={isZugang ? "green" : isSucher ? "purple" : isBuyer ? "blue" : "gray"}>
                          {isZugang ? "Neue Firma" : isSucher ? "Sucher-Abo" : isBuyer ? "Marktplatz-Zugang" : (r.type || "Paket")}
                        </Badge>
                        <div className="min-w-0">
                          <div className="text-[14px] text-white font-medium">
                            {isSucher ? (
                              <>
                                {r.subject_role === "dealer" ? "Chef " : "Sucher "}
                                {r.sucher_name || r.sucher_email}
                                {r.company_name ? ` von Firma ${r.company_name}` : ""}
                                {r.kunden_nr != null ? ` (#${r.kunden_nr})` : ""}
                                {" möchte das Sucher-Abo verlängern"}
                                <span className="text-zinc-500 font-normal">
                                  {" · "}{r.wanted_plan === "yearly" ? "1 Jahr · 1.500 €" : "1 Monat · 150 €"}
                                </span>
                              </>
                            ) : (
                              <>
                                {r.company_name}
                                <span className="text-zinc-500 font-normal"> · {r.wanted || (r.wanted_tier ? `Verkaufspaket ${r.wanted_tier}` : "")}</span>
                              </>
                            )}
                          </div>
                          <div className="text-[12px] text-zinc-500">
                            {isZugang ? (r.contact_person || "") : r.company_name}
                            {r.contact_email ? ` · ${r.contact_email}` : ""}
                            {isZugang && r.contact_phone ? ` · ${r.contact_phone}` : ""}
                            {" · "}{fmtDate(r.created_at)}
                          </div>
                          {isZugang && r.message ? (
                            <div className="text-[12px] text-zinc-400 mt-1 max-w-xl whitespace-pre-wrap">{r.message}</div>
                          ) : null}
                        </div>
                        <div className="ml-auto flex gap-2">
                          {isZugang && (
                            <Button size="sm" onClick={() => setFirmaReq(r)}>
                              <Building2 size={14} /> Firma anlegen
                            </Button>
                          )}
                          {isSucher && <Button size="sm" onClick={() => grantSucher(r)} data-testid={`abo-ja-${r.id}`}><Check size={14} /> Ja, freischalten</Button>}
                          {isBuyer && <Button size="sm" onClick={() => grantBuyer(r)}><Check size={14} /> Zugang aktivieren</Button>}
                          {!isZugang && !isSucher && !isBuyer && r.wanted_tier && r.dealer_id && (
                            <Button size="sm" onClick={() => grantPlan(r)}><Check size={14} /> Paket aktivieren</Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => { closeReq(r.id, "abgelehnt").then(load); }} data-testid={`abo-nein-${r.id}`}>
                            <X size={14} /> {isSucher ? "Nein, ablehnen" : "Ablehnen"}
                          </Button>
                        </div>
                      </div>
                    </Card>
                  );
                })}
              </div>
            )}
          </div>

          {/* Zwischenhändler */}
          <div>
            <div className="text-[13px] font-semibold text-zinc-300 mb-3 uppercase tracking-wide inline-flex items-center gap-1.5">
              <Store size={14} /> Zwischenhändler {buyers?.length ? `(${buyers.length})` : ""}
            </div>
            {!buyers?.length ? (
              <EmptyState title="Noch keine Zwischenhändler" hint="Registrierte B2B-Käufer erscheinen hier." />
            ) : (
              <Card padded={false}>
                <div className="overflow-x-auto">
                  <table className="w-full text-[13px]">
                    <thead>
                      <tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                        <th className="px-4 py-3 font-medium">Firma</th>
                        <th className="px-4 py-3 font-medium">E-Mail</th>
                        <th className="px-4 py-3 font-medium">USt-IdNr.</th>
                        <th className="px-4 py-3 font-medium">Zugang</th>
                        <th className="px-4 py-3 font-medium">Läuft ab</th>
                        <th className="px-4 py-3 font-medium text-right">Aktion</th>
                      </tr>
                    </thead>
                    <tbody>
                      {buyers.map((b) => (
                        <tr key={b.id} className="border-t border-white/5">
                          <td className="px-4 py-2.5 text-white font-medium">
                            {b.company_name}
                            <div className="text-[11px] text-zinc-500 font-normal">{b.contact_name}</div>
                          </td>
                          <td className="px-4 py-2.5 text-zinc-400">{b.email}</td>
                          <td className="px-4 py-2.5 text-zinc-400" data-testid={`ustid-${b.id}`}>
                            {b.ust_id ? (
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-[12px]">{b.ust_id}</span>
                                {b.ust_id_pruefung && (
                                  <span title={b.ust_id_pruefung.hinweis || b.ust_id_pruefung.name || ""}>
                                    <Badge tone={b.ust_id_pruefung.status === "gueltig" ? "green" : b.ust_id_pruefung.status === "ungueltig" ? "red" : "yellow"}>
                                      {b.ust_id_pruefung.status === "gueltig" ? "VIES ok" : b.ust_id_pruefung.status === "ungueltig" ? "VIES ungültig" : "nicht prüfbar"}
                                    </Badge>
                                  </span>
                                )}
                                <Button size="sm" variant="ghost" onClick={() => pruefeUstId(b)} title="Beim EU-Dienst VIES prüfen">Prüfen</Button>
                              </div>
                            ) : <span className="text-zinc-600">—</span>}
                          </td>
                          <td className="px-4 py-2.5">
                            <Badge tone={b.access?.active ? "green" : "red"}>
                              {b.access?.active ? "aktiv" : "gesperrt"}
                            </Badge>
                          </td>
                          <td className="px-4 py-2.5 text-zinc-500 tabular-nums">{fmtDate(b.access?.expires_at)}</td>
                          <td className="px-4 py-2.5 text-right">
                            {b.access?.active ? (
                              <Button size="sm" variant="ghost" onClick={() => setBuyerAccess(b, false)}>Sperren</Button>
                            ) : (
                              <Button size="sm" onClick={() => setBuyerAccess(b, true)}>Freischalten (20 €)</Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        </div>
      )}

      {firmaReq && (
        <FirmaAnlegenDialog
          request={firmaReq}
          onClose={() => setFirmaReq(null)}
          onDone={async () => { await closeReq(firmaReq.id); setFirmaReq(null); load(); }}
        />
      )}
    </div>
  );
}


/** Firmen-Konto direkt aus einer Zugangs-Anfrage anlegen (plan_type "none":
 *  der Hauptaccount ist kostenlos, Sucher-Abos werden separat freigeschaltet). */
function FirmaAnlegenDialog({ request, onClose, onDone }) {
  const [f, setF] = useState({
    company_name: request.company_name || "",
    email: request.contact_email || "",
    password: "",
  });
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async () => {
    if (!f.company_name || !f.email || f.password.length < 8) {
      toast.error("Firma, E-Mail und Passwort (min. 8 Zeichen) angeben"); return;
    }
    setBusy(true);
    try {
      await api.post("/admin/users", {
        email: f.email, password: f.password,
        company_name: f.company_name, plan_type: "none",
      });
      toast.success("Firmen-Konto angelegt — Zugangsdaten an den Kontakt geben");
      onDone?.();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };

  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)" }}>
      <div className="w-full max-w-md rounded-2xl p-5 bg-white dark:bg-zinc-900 border border-black/10 dark:border-white/10">
        <div className="flex items-center justify-between mb-1">
          <div className="text-lg font-bold">Firma anlegen</div>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-200"><X size={20} /></button>
        </div>
        <div className="text-[12px] text-zinc-500 mb-4">
          Aus Anfrage: {request.contact_person || "—"}
          {request.sucher_anzahl ? ` · gewünschte Sucher: ${request.sucher_anzahl}` : ""}
          {" — Sucher danach über die Nutzer-Detailseite anlegen."}
        </div>
        <div className="space-y-3">
          <input value={f.company_name} onChange={set("company_name")} placeholder="Firmenname *" className={inputCls} />
          <input value={f.email} onChange={set("email")} placeholder="Login-E-Mail des Chefs *" className={inputCls} />
          <input value={f.password} onChange={set("password")} placeholder="Start-Passwort (min. 8 Zeichen) *" className={inputCls} />
        </div>
        <Button className="mt-4 w-full" onClick={submit} disabled={busy}>
          {busy ? "Wird angelegt…" : "Firmen-Konto anlegen"}
        </Button>
      </div>
    </div>
  );
}
