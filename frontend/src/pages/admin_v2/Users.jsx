import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { Search, KeyRound, Lock, Unlock, ChevronRight, Crown, UserPlus, Trash2 } from "lucide-react";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";


// Haendler-Hauptaccount: das Backend verlangt eine ausdrueckliche
// Bestaetigung (?firma_loeschen=true), weil dabei die KOMPLETTE Firma
// entfernt wird. Vorher zeigen wir die Loeschvorschau des Backends.
async function deleteUserSmart(u) {
  try {
    await api.delete(`/admin/users/${u.id}`);
    return true;
  } catch (e) {
    if (e?.response?.status !== 409) throw e;
  }
  let vorschau = "";
  try {
    const { data } = await api.get(`/admin/dealers/${u.dealer_id}/loeschvorschau`);
    const w = data?.wuerde_loeschen || {};
    vorschau = Object.entries(w).filter(([, n]) => n > 0)
      .map(([k, n]) => `${n} × ${k}`).join(", ") || "keine weiteren Daten";
  } catch { vorschau = "Vorschau nicht verfügbar"; }
  const ok = window.confirm(
    `ACHTUNG: "${u.company_name || u.email}" ist ein Händler-Hauptaccount.\n` +
    `Die KOMPLETTE Firma wird gelöscht (${vorschau}).\n\n` +
    `Wirklich unwiderruflich löschen?`);
  if (!ok) return false;
  await api.delete(`/admin/users/${u.id}?firma_loeschen=true`);
  return true;
}

export default function AdminUsers() {
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;   // Betreiber-Funktionen (Audit 09/2026)
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [resetUser, setResetUser] = useState(null);
  const [newPw, setNewPw] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleteUser, setDeleteUser] = useState(null);
  const [deleting, setDeleting] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/users");
      setUsers(data.users || data || []);
    } catch (e) {
      toast.error(errMsg(e, "Fehler beim Laden"));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return users;
    return users.filter((u) => [u.email, u.username, u.company_name,
      u.kunden_nr != null ? `#${u.kunden_nr}` : "", String(u.kunden_nr ?? "")]
      .join(" ").toLowerCase().includes(s));
  }, [users, q]);

  const toggleActive = async (u) => {
    if (u.active) {
      const firma = u.role === "dealer";
      const text = firma
        ? `Firma "${u.company_name || u.email}" komplett sperren?\n\nDer Chef UND alle Sucher dieser Firma werden sofort abgemeldet und koennen sich nicht mehr anmelden (auch die kostenlosen Bereiche). Das ist etwas anderes als "Abo aufheben" (nur Suche/Vergleich).`
        : `Konto "${u.email}" sperren?\n\nAnmeldung wird sofort unmoeglich, die Sitzung beendet. "Abo aufheben" (nur Suche/Vergleich) findest du in der Firmenansicht.`;
      if (!window.confirm(text)) return;
    }
    try {
      await api.post(`/admin/users/${u.id}/active`, { active: !u.active });
      toast.success(u.active ? "Account gesperrt" : "Account entsperrt");
      load();
    } catch (e) { toast.error(errMsg(e, "Fehler")); }
  };

  const submitReset = async () => {
    if (!resetUser || !newPw || newPw.length < 8) {
      toast.error("Passwort muss mind. 8 Zeichen haben");
      return;
    }
    try {
      await api.post(`/admin/users/${resetUser.id}/password`, { new_password: newPw });
      toast.success("Passwort aktualisiert");
      setResetUser(null); setNewPw("");
    } catch (e) { toast.error(errMsg(e, "Fehler")); }
  };

  const submitDelete = async () => {
    if (!deleteUser) return;
    setDeleting(true);
    try {
      const done = await deleteUserSmart(deleteUser);
      if (done) {
        toast.success(`Account "${deleteUser.company_name || deleteUser.email}" dauerhaft gelöscht`);
        setDeleteUser(null);
        load();
      }
    } catch (e) {
      toast.error(errMsg(e, "Löschen fehlgeschlagen"));
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div>
      <PageHeader
        title="Nutzer"
        subtitle={`${users.length} Konten insgesamt`}
        action={
          <Button disabled={!superAdmin} title={superAdmin ? "" : "Nur der Super-Admin legt Firmen an"}
            data-testid="admin-create-user-btn"
            onClick={() => setCreating(true)}
            variant="primary"
          >
            <UserPlus size={14} /> Neuer Nutzer
          </Button>
        }
      />

      <Card padded={false}>
        <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
          <Search size={16} className="text-zinc-500" />
          <input
            data-testid="admin-users-search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Firma suchen (Name, #Kundennummer, E-Mail)"
            className="flex-1 bg-transparent border-0 outline-none text-[14px] text-white placeholder:text-zinc-500"
          />
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-zinc-500 text-sm"><Spinner /> lade…</div>
        ) : filtered.length === 0 ? (
          <EmptyState title="Keine Nutzer gefunden" hint="Versuche es mit einer anderen Suche." />
        ) : (
          <ul className="divide-y" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
            {filtered.map((u) => (
              <li key={u.id} data-testid={`user-row-${u.id}`} className="px-4 py-3 hover:bg-white/5 transition-colors">
                <div className="flex items-center gap-3">
                  <Avatar text={u.company_name || u.email || u.username} />
                  <Link to={`/admin/users/${u.id}`} className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[14.5px] font-semibold text-white truncate">
                        {u.company_name || u.username || u.email}
                      </span>
                      {u.kunden_nr != null && <Badge tone="blue">#{u.kunden_nr}</Badge>}
                      {u.role === "admin" && <Badge tone="purple">Admin</Badge>}
                      {u.is_super_admin && <Crown size={13} className="text-amber-400" />}
                      {u.active === false ? <Badge tone="red">Gesperrt</Badge> : <Badge tone="green">Aktiv</Badge>}
                      {u.subscription?.plan && (
                        <Badge tone={u.subscription.plan === "lifetime" ? "yellow" : "blue"}>
                          {u.subscription.plan}
                        </Badge>
                      )}
                    </div>
                    <div className="text-[12.5px] text-zinc-400 truncate mt-0.5">
                      {u.email}{u.username ? ` · ${u.username}` : ""}
                    </div>
                    <div className="text-[11.5px] text-zinc-500 mt-0.5">Erstellt: {fmtDate(u.created_at)}</div>
                  </Link>
                  <div className="flex items-center gap-1.5 flex-wrap justify-end">
                    {!superAdmin && <span className="text-[11px] text-zinc-500">nur lesen</span>}
                    {superAdmin && (<>
                    <Button data-testid={`user-pw-btn-${u.id}`} variant="outline" size="sm" onClick={() => setResetUser(u)} title="Passwort setzen">
                      <KeyRound size={14} /> Passwort
                    </Button>
                    </>)}
                    {superAdmin && (<>
                    <Button
                      data-testid={`user-toggle-active-btn-${u.id}`}
                      variant={u.active ? "outline" : "primary"}
                      size="sm"
                      onClick={() => toggleActive(u)}
                      title={u.active ? "Sperren" : "Entsperren"}
                    >
                      {u.active ? <><Lock size={14}/>Sperren</> : <><Unlock size={14}/>Entsperren</>}
                    </Button>
                    </>)}
                    {superAdmin && u.role === "admin" && u.mfa_aktiv && (

                      <Button variant="outline" size="sm" data-testid={`user-mfa-reset-${u.id}`}

                              title="Zwei-Faktor zurücksetzen (ausgesperrter Admin richtet neu ein)"

                              onClick={async () => { if (!window.confirm(`Zwei-Faktor von ${u.email} zurücksetzen?`)) return; try { await api.post(`/admin/users/${u.id}/mfa-zuruecksetzen`); toast.success("Zwei-Faktor zurückgesetzt"); load(); } catch (e) { toast.error(errMsg(e)); } }}>

                        2FA zurücksetzen

                      </Button>

                    )}
                    {superAdmin && !u.is_super_admin && (
                      <Button
                        data-testid={`user-delete-btn-${u.id}`}
                        variant="danger"
                        size="sm"
                        onClick={() => setDeleteUser(u)}
                        title="Account dauerhaft löschen"
                      >
                        <Trash2 size={14} />
                      </Button>
                    )}
                    <Link to={`/admin/users/${u.id}`} className="text-zinc-500 hover:text-white ml-1">
                      <ChevronRight size={18} />
                    </Link>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Passwort-Reset-Modal */}
      {resetUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={() => setResetUser(null)}>
          <div
            className="rounded-2xl shadow-2xl w-full max-w-md p-6"
            style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.10)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="text-[18px] font-semibold tracking-tight text-white">Passwort neu setzen</div>
            <div className="text-[13px] text-zinc-400 mt-1">
              für {resetUser.company_name || resetUser.email}
            </div>
            <input
              data-testid="admin-pw-reset-input"
              type="text"
              autoFocus
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              className="mt-4 w-full h-11 px-4 rounded-xl outline-none text-[14px] text-white placeholder:text-zinc-500"
              style={{
                background: "rgba(255,255,255,0.05)",
                border: "1px solid rgba(255,255,255,0.10)",
              }}
              placeholder="Neues Passwort (mind. 8 Zeichen)"
            />
            <div className="flex gap-2 mt-5 justify-end">
              <Button data-testid="admin-pw-reset-cancel" variant="ghost" onClick={() => { setResetUser(null); setNewPw(""); }}>Abbrechen</Button>
              <Button data-testid="admin-pw-reset-submit" onClick={submitReset}>Setzen</Button>
            </div>
          </div>
        </div>
      )}

      {creating && (
        <CreateUserModal
          onClose={() => setCreating(false)}
          onCreated={() => { setCreating(false); load(); }}
        />
      )}

      {deleteUser && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
          onClick={() => !deleting && setDeleteUser(null)}
          data-testid="admin-delete-user-modal"
        >
          <div
            className="rounded-2xl shadow-2xl w-full max-w-md p-6"
            style={{ background: "#141416", border: "1px solid rgba(255,69,58,0.30)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3">
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
                style={{ background: "rgba(255,69,58,0.15)", border: "1px solid rgba(255,69,58,0.30)" }}
              >
                <Trash2 size={18} className="text-red-400" />
              </div>
              <div className="flex-1">
                <div className="text-[18px] font-semibold tracking-tight text-white">
                  Account dauerhaft löschen?
                </div>
                <div className="text-[13px] text-zinc-400 mt-1">
                  Du löschst <b className="text-white">{deleteUser.company_name || deleteUser.email}</b>{" "}
                  ({deleteUser.email}) inklusive Händler-Profil und allen Abos.
                  <br />
                  <span className="text-red-300/80">Dieser Vorgang ist nicht umkehrbar.</span>
                </div>
              </div>
            </div>

            <div className="flex gap-2 mt-6 justify-end">
              <Button
                data-testid="admin-delete-user-cancel"
                variant="ghost"
                onClick={() => setDeleteUser(null)}
                disabled={deleting}
              >
                Abbrechen
              </Button>
              <Button
                data-testid="admin-delete-user-confirm"
                variant="danger"
                onClick={submitDelete}
                disabled={deleting}
              >
                {deleting ? "Lösche…" : "Endgültig löschen"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function CreateUserModal({ onClose, onCreated }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [planType, setPlanType] = useState("monthly");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!email || !password || !companyName) {
      toast.error("Bitte alle Pflichtfelder ausfüllen");
      return;
    }
    if (password.length < 8) {
      toast.error("Passwort muss mind. 8 Zeichen haben");
      return;
    }
    setBusy(true);
    try {
      await api.post("/admin/users", {
        email: email.trim(),
        password,
        company_name: companyName.trim(),
        plan_type: planType,
      });
      toast.success(`Nutzer "${companyName}" angelegt`);
      onCreated?.();
    } catch (e) {
      toast.error(errMsg(e, "Fehler beim Anlegen"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
      data-testid="admin-create-user-modal"
    >
      <div
        className="rounded-2xl shadow-2xl w-full max-w-md p-6"
        style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.10)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="text-[18px] font-semibold tracking-tight text-white">Neuen Nutzer anlegen</div>
        <div className="text-[13px] text-zinc-400 mt-1">
          Der Händler kann sich danach direkt mit E-Mail und Passwort anmelden und die Plattform nutzen.
        </div>

        <div className="mt-5 space-y-3">
          <Field label="Firma" testid="create-user-company"
                 value={companyName} onChange={setCompanyName}
                 placeholder="z.B. Cash Car Hannover GmbH" />
          <Field label="E-Mail" testid="create-user-email" type="email"
                 value={email} onChange={setEmail}
                 placeholder="haendler@firma.de" />
          <Field label="Passwort (mind. 8 Zeichen)" testid="create-user-password"
                 value={password} onChange={setPassword}
                 placeholder="initiales Passwort" />

          <div>
            <label className="block text-[12px] font-medium text-zinc-400 mb-1.5">Abo-Plan</label>
            <div className="grid grid-cols-4 gap-1.5">
              {[
                { v: "trial",    l: "Test (14 T)" },
                { v: "monthly",  l: "Monat" },
                { v: "yearly",   l: "Jahr" },
                { v: "lifetime", l: "Lifetime" },
              ].map((p) => (
                <button
                  key={p.v}
                  type="button"
                  data-testid={`create-user-plan-${p.v}`}
                  onClick={() => setPlanType(p.v)}
                  className={`px-2 py-2 rounded-lg text-[12.5px] font-medium transition-all ${
                    planType === p.v
                      ? "bg-white/10 text-white border border-white/20"
                      : "text-zinc-400 hover:text-white"
                  }`}
                  style={planType !== p.v ? {
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.08)",
                  } : {}}
                >
                  {p.l}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="flex gap-2 mt-6 justify-end">
          <Button data-testid="admin-create-user-cancel" variant="ghost" onClick={onClose}>
            Abbrechen
          </Button>
          <Button data-testid="admin-create-user-submit" onClick={submit} disabled={busy}>
            {busy ? "Lege an…" : "Nutzer anlegen"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, type = "text", testid }) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-zinc-400 mb-1">{label}</label>
      <input
        data-testid={testid}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full h-11 px-4 rounded-xl outline-none text-[14px] text-white placeholder:text-zinc-500"
        style={{
          background: "rgba(255,255,255,0.05)",
          border: "1px solid rgba(255,255,255,0.10)",
        }}
      />
    </div>
  );
}

function Avatar({ text }) {
  const letters = (text || "?").trim().split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
  // deterministischer pastell-Hue auf Basis des Namens
  let h = 0; for (let i = 0; i < (text || "").length; i++) h = (h * 31 + (text.charCodeAt(i) || 0)) & 255;
  return (
    <div
      className="w-10 h-10 rounded-xl flex items-center justify-center text-white font-bold text-[13px] shrink-0"
      style={{ background: `linear-gradient(135deg, hsl(${h},70%,55%), hsl(${(h + 40) % 360},70%,45%))` }}
    >
      {letters}
    </div>
  );
}
