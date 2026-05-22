import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Card, Button } from "./_ui";
import { KeyRound, Crown } from "lucide-react";

export default function AdminSettings() {
  const { user } = useAuth();
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [nw2, setNw2] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (nw.length < 8) return toast.error("Neues Passwort: mind. 8 Zeichen");
    if (nw !== nw2) return toast.error("Passwörter stimmen nicht überein");
    setBusy(true);
    try {
      await api.post("/admin/me/password", { current_password: cur, new_password: nw });
      toast.success("Passwort geändert");
      setCur(""); setNw(""); setNw2("");
    } catch (e) {
      toast.error(errMsg(e, "Fehler"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <PageHeader title="Einstellungen" subtitle="Eigenes Konto verwalten" />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            {user?.is_super_admin && <Crown size={16} className="text-amber-400" />}
            <span className="text-[15px] font-semibold text-white">Mein Konto</span>
          </div>
          <Row label="Benutzername" value={user?.username || "—"} />
          <Row label="E-Mail"       value={user?.email || "—"} />
          <Row label="Rolle"        value={user?.is_super_admin ? "Super-Admin" : user?.role || "—"} />
        </Card>

        <Card className="lg:col-span-2">
          <div className="flex items-center gap-2 mb-4">
            <KeyRound size={16} className="text-zinc-500" />
            <span className="text-[15px] font-semibold text-white">Passwort ändern</span>
          </div>
          <form onSubmit={submit} className="space-y-3 max-w-md">
            <Field label="Aktuelles Passwort" type="password" value={cur} onChange={setCur} required autoComplete="current-password" />
            <Field label="Neues Passwort"     type="password" value={nw}  onChange={setNw}  required autoComplete="new-password" />
            <Field label="Wiederholen"        type="password" value={nw2} onChange={setNw2} required autoComplete="new-password" />
            <div className="pt-2">
              <Button type="submit" disabled={busy}>{busy ? "…" : "Passwort speichern"}</Button>
            </div>
          </form>
        </Card>
      </div>
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between py-2 text-[13.5px]">
      <span className="text-zinc-400">{label}</span>
      <span className="text-white font-medium">{value}</span>
    </div>
  );
}

function Field({ label, type = "text", value, onChange, required, autoComplete }) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-zinc-400 mb-1">{label}</label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        required={required}
        autoComplete={autoComplete}
        className="w-full h-11 px-4 rounded-xl outline-none text-[14px] text-white placeholder:text-zinc-500"
        style={{
          background: "rgba(255,255,255,0.05)",
          border: "1px solid rgba(255,255,255,0.10)",
        }}
      />
    </div>
  );
}
