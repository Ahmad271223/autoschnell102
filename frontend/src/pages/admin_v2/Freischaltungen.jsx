import { useCallback, useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { RefreshCw, Check, X, Store, UserSearch } from "lucide-react";

/**
 * Manuelle Freischaltungen (Bezahlung läuft aktuell außerhalb der App):
 * - Offene Anfragen: Sucher-Abo (160/1800) und Marktplatz-Zugang (9,99).
 * - Zwischenhändler-Liste mit Zugang aktivieren/sperren.
 */
export default function AdminFreischaltungen() {
  const [requests, setRequests] = useState(null);
  const [buyers, setBuyers] = useState(null);
  const [loading, setLoading] = useState(true);

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
                  return (
                    <Card key={r.id} padded={false}>
                      <div className="p-4 flex flex-wrap items-center gap-3">
                        <Badge tone={isSucher ? "purple" : isBuyer ? "blue" : "gray"}>
                          {isSucher ? "Sucher-Abo" : isBuyer ? "Marktplatz-Zugang" : (r.type || "Paket")}
                        </Badge>
                        <div className="min-w-0">
                          <div className="text-[14px] text-white font-medium">
                            {isSucher ? (r.sucher_name || r.sucher_email) : r.company_name}
                            <span className="text-zinc-500 font-normal"> · {r.wanted}</span>
                          </div>
                          <div className="text-[12px] text-zinc-500">
                            {r.company_name}{r.contact_email ? ` · ${r.contact_email}` : ""} · {fmtDate(r.created_at)}
                          </div>
                        </div>
                        <div className="ml-auto flex gap-2">
                          {isSucher && <Button size="sm" onClick={() => grantSucher(r)}><Check size={14} /> Abo aktivieren</Button>}
                          {isBuyer && <Button size="sm" onClick={() => grantBuyer(r)}><Check size={14} /> Zugang aktivieren</Button>}
                          <Button size="sm" variant="ghost" onClick={() => { closeReq(r.id, "abgelehnt").then(load); }}>
                            <X size={14} /> Ablehnen
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
                              <Button size="sm" onClick={() => setBuyerAccess(b, true)}>Freischalten (9,99 €)</Button>
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
    </div>
  );
}
