import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { KeyRound, Lock, LockOpen, Search, Trash2, Truck } from "lucide-react";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";

/**
 * Fahrer-Verwaltung (Review 09/2026: fehlte komplett).
 *
 * Fahrer-Konten sind firmenneutral — die Liste ist plattformweit; je Fahrer
 * werden die verknüpften Händler und die Terminzahl angezeigt. Aktionen:
 * sperren/entsperren (beendet die Sitzung), Passwort zurücksetzen,
 * löschen (DSGVO: Verknüpfungen weg, offene Termine getrennt).
 */
export default function AdminFahrer() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [resetDriver, setResetDriver] = useState(null);
  const [newPw, setNewPw] = useState("");
  const [deleteDriver, setDeleteDriver] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/drivers");
      setRows(Array.isArray(data) ? data : []);
    } catch (e) {
      toast.error(errMsg(e, "Fahrer konnten nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return rows;
    return rows.filter((r) =>
      (r.display_name || "").toLowerCase().includes(t)
      || (r.email || "").toLowerCase().includes(t)
      || (r.driver_code || "").toLowerCase().includes(t)
      || (r.firmen || []).some((f) => (f || "").toLowerCase().includes(t)));
  }, [rows, q]);

  const toggleActive = async (r) => {
    try {
      await api.post(`/admin/drivers/${r.id}/active`, { active: !r.active });
      toast.success(r.active ? "Fahrer gesperrt" : "Fahrer entsperrt");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const submitReset = async () => {
    if ((newPw || "").length < 8) { toast.error("Mindestens 8 Zeichen"); return; }
    try {
      await api.post(`/admin/drivers/${resetDriver.id}/password`, { new_password: newPw });
      toast.success("Passwort gesetzt — alle Sitzungen des Fahrers wurden beendet");
      setResetDriver(null); setNewPw("");
    } catch (e) { toast.error(errMsg(e)); }
  };

  const submitDelete = async () => {
    setDeleting(true);
    try {
      const { data } = await api.delete(`/admin/drivers/${deleteDriver.id}`);
      toast.success(`Fahrer gelöscht (${data.verknuepfungen_entfernt} Verknüpfung(en) entfernt, `
        + `${data.offene_termine_getrennt} offene(r) Termin(e) getrennt)`);
      setDeleteDriver(null);
      load();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setDeleting(false); }
  };

  return (
    <div>
      <PageHeader
        title="Fahrer"
        subtitle="Alle Fahrer-Konten der Plattform — Verknüpfungen laufen über die Händler"
        action={<span className="text-[12px] text-zinc-400">{rows.length} Konten</span>}
      />

      <Card padded={false}>
        <div className="px-4 py-3" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
          <div className="relative max-w-sm">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Name, E-Mail, Fahrer-Code oder Firma"
              data-testid="fahrer-suche"
              className="h-10 pl-9 pr-3 rounded-xl text-[14px] w-full outline-none focus:ring-2 focus:ring-red-500/40"
              style={{ background: "#18181b", color: "#fff", border: "1px solid rgba(255,255,255,0.12)" }}
            />
          </div>
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-zinc-500 text-sm p-6"><Spinner /> lade…</div>
        ) : filtered.length === 0 ? (
          <EmptyState title={q ? "Keine Treffer" : "Noch keine Fahrer"}
                      hint={q ? "Suche anpassen." : "Fahrer registrieren sich selbst über die Fahrer-App."} />
        ) : (
          <ul className="divide-y divide-white/5">
            {filtered.map((r) => (
              <li key={r.id} className="px-4 py-3 flex items-center gap-3" data-testid={`fahrer-row-${r.id}`}>
                <div className="w-9 h-9 rounded-full flex items-center justify-center shrink-0 text-zinc-300"
                     style={{ background: "rgba(255,255,255,0.06)" }}>
                  <Truck size={16} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[14px] font-semibold text-white truncate">{r.display_name || "—"}</span>
                    <Badge tone="blue">{r.driver_code}</Badge>
                    <Badge tone={r.active ? "green" : "red"}>{r.active ? "Aktiv" : "Gesperrt"}</Badge>
                  </div>
                  <div className="text-[12px] text-zinc-500 truncate">
                    {r.email} · seit {fmtDate(r.created_at)}
                    {" · "}{r.verknuepfungen || 0} Händler{(r.firmen || []).length ? ` (${r.firmen.join(", ")})` : ""}
                    {" · "}{r.termine || 0} Termine{r.termine_offen ? ` (${r.termine_offen} offen)` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button size="sm" variant="secondary" data-testid={`fahrer-pw-btn-${r.id}`}
                          onClick={() => { setResetDriver(r); setNewPw(""); }}>
                    <KeyRound size={13} /> Passwort
                  </Button>
                  <Button size="sm" variant={r.active ? "outline" : "primary"}
                          data-testid={`fahrer-toggle-active-btn-${r.id}`}
                          onClick={() => toggleActive(r)}>
                    {r.active ? <><Lock size={13} /> Sperren</> : <><LockOpen size={13} /> Entsperren</>}
                  </Button>
                  <Button size="sm" variant="danger" data-testid={`fahrer-delete-btn-${r.id}`}
                          onClick={() => setDeleteDriver(r)}>
                    <Trash2 size={13} />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {resetDriver && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
             onClick={() => setResetDriver(null)}>
          <div className="w-full max-w-sm rounded-2xl p-5" style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)" }}
               onClick={(e) => e.stopPropagation()}>
            <div className="text-[15px] font-semibold text-white">Passwort setzen</div>
            <div className="text-[12.5px] text-zinc-500 mt-1">
              {resetDriver.display_name} · {resetDriver.email}. Alle Sitzungen des Fahrers werden beendet.
            </div>
            <input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)}
                   placeholder="Neues Passwort (min. 8, Ziffer oder Sonderzeichen)"
                   data-testid="fahrer-pw-input" autoFocus
                   className="mt-3 h-10 px-3 rounded-xl text-[14px] w-full outline-none"
                   style={{ background: "#18181b", color: "#fff", border: "1px solid rgba(255,255,255,0.12)" }} />
            <div className="mt-4 flex gap-2 justify-end">
              <Button variant="ghost" onClick={() => setResetDriver(null)}>Abbrechen</Button>
              <Button data-testid="fahrer-pw-submit" onClick={submitReset}>Setzen</Button>
            </div>
          </div>
        </div>
      )}

      {deleteDriver && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
             onClick={() => !deleting && setDeleteDriver(null)}>
          <div className="w-full max-w-sm rounded-2xl p-5" style={{ background: "#141416", border: "1px solid rgba(239,68,68,0.4)" }}
               onClick={(e) => e.stopPropagation()}>
            <div className="text-[15px] font-semibold text-white">Fahrer löschen?</div>
            <div className="text-[12.5px] text-zinc-400 mt-2 leading-relaxed">
              {deleteDriver.display_name} ({deleteDriver.email}) wird endgültig gelöscht.
              Händler-Verknüpfungen werden entfernt, offene Termine vom Fahrer getrennt.
              Abgeschlossene Abholungen behalten ihre Historie.
            </div>
            <div className="mt-4 flex gap-2 justify-end">
              <Button variant="ghost" disabled={deleting} onClick={() => setDeleteDriver(null)}>Abbrechen</Button>
              <Button variant="danger" disabled={deleting} data-testid="fahrer-delete-confirm"
                      onClick={submitDelete}>
                {deleting ? "Löscht…" : "Endgültig löschen"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
