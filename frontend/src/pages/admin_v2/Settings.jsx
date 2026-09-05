import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { PageHeader, Card, Button } from "./_ui";
import { KeyRound, Crown, ShieldCheck } from "lucide-react";
import { useEffect } from "react";

function MfaKarte() {
  const [st, setSt] = useState(null);
  const [setup, setSetup] = useState(null);      // {secret, otpauth_uri}
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState(null);      // Wiederherstellungscodes (einmalig)
  const [busy, setBusy] = useState(false);
  const load = () => api.get("/admin/me/mfa").then((r) => setSt(r.data)).catch(() => setSt({ aktiv: false }));
  useEffect(() => { load(); }, []);
  const einrichten = async () => {
    setBusy(true);
    try { const r = await api.post("/admin/me/mfa/einrichten"); setSetup(r.data); setCode(""); }
    catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  const aktivieren = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/me/mfa/aktivieren", { code });
      setCodes(r.data.wiederherstellungscodes || []); setSetup(null); setCode("");
      toast.success("Zwei-Faktor-Anmeldung ist aktiv"); load();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  const deaktivieren = async () => {
    const c = window.prompt("Zum Abschalten den aktuellen Code aus der App eingeben:");
    if (!c) return;
    setBusy(true);
    try { await api.post("/admin/me/mfa/deaktivieren", { code: c }); toast.success("Zwei-Faktor abgeschaltet"); setCodes(null); load(); }
    catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  return (
    <Card className="lg:col-span-2" data-testid="mfa-karte">
      <div className="flex items-center gap-2 mb-2">
        <ShieldCheck size={16} className="text-zinc-500" />
        <span className="text-[15px] font-semibold text-white">Zwei-Faktor-Anmeldung (Authenticator-App)</span>
        {st && (st.aktiv ? <span className="text-[11px] px-2 py-0.5 rounded-md bg-emerald-500/15 text-emerald-300">aktiv</span>
                         : <span className="text-[11px] px-2 py-0.5 rounded-md bg-amber-500/15 text-amber-300">nicht aktiv</span>)}
      </div>
      <p className="text-[12.5px] text-zinc-400 mb-3">
        Beim Anmelden wird zusätzlich zum Passwort ein 6-stelliger Code aus einer Authenticator-App
        (z.B. Google Authenticator, Microsoft Authenticator, Aegis) verlangt. Für den Super-Admin dringend empfohlen.
      </p>
      {st && !st.aktiv && !setup && (
        <Button size="sm" onClick={einrichten} disabled={busy} data-testid="mfa-einrichten">Einrichten</Button>
      )}
      {setup && (
        <div className="rounded-lg p-3 mb-3" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.08)" }}>
          <div className="text-[12px] text-zinc-400 mb-1">1. In der App „Konto hinzufügen“ und diesen Schlüssel eingeben (oder den Link öffnen):</div>
          <div className="font-mono text-[13px] text-white break-all select-all" data-testid="mfa-secret">{setup.secret}</div>
          <a href={setup.otpauth_uri} className="text-[12px] text-sky-400 underline break-all">{setup.otpauth_uri}</a>
          <div className="text-[12px] text-zinc-400 mt-3 mb-1">2. Den angezeigten 6-stelligen Code eingeben:</div>
          <div className="flex gap-2">
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" placeholder="123456" data-testid="mfa-aktivieren-code"
                   className="h-9 px-3 rounded-lg bg-transparent border text-sm outline-none w-40" style={{ borderColor: "rgba(255,255,255,0.15)" }} />
            <Button size="sm" onClick={aktivieren} disabled={busy || code.length < 6} data-testid="mfa-aktivieren">Aktivieren</Button>
            <Button size="sm" variant="ghost" onClick={() => setSetup(null)}>Abbrechen</Button>
          </div>
        </div>
      )}
      {codes && (
        <div className="rounded-lg p-3 mb-3" style={{ background: "rgba(52,199,89,0.08)", border: "1px solid rgba(52,199,89,0.3)" }} data-testid="mfa-wiederherstellung">
          <div className="text-[12.5px] text-emerald-200 mb-1">Wiederherstellungscodes — jetzt sicher aufbewahren (werden nur einmal angezeigt, jeder gilt einmal):</div>
          <div className="font-mono text-[13px] text-white grid grid-cols-2 gap-x-6 select-all">{codes.map((c) => <div key={c}>{c}</div>)}</div>
        </div>
      )}
      {st && st.aktiv && (
        <div className="flex items-center gap-3">
          <span className="text-[12px] text-zinc-500">aktiv seit {st.aktiviert_am ? new Date(st.aktiviert_am).toLocaleDateString("de-DE") : "—"} · {st.wiederherstellungscodes_uebrig} Wiederherstellungscodes übrig</span>
          <Button size="sm" variant="ghost" onClick={deaktivieren} disabled={busy} data-testid="mfa-deaktivieren">Abschalten</Button>
        </div>
      )}
    </Card>
  );
}

export default function AdminSettings() {
  const { user } = useAuth();
  const [cur, setCur] = useState("");
  const [nw, setNw] = useState("");
  const [nw2, setNw2] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (nw.length < 10) return toast.error("Neues Passwort: mind. 10 Zeichen, mit Ziffer oder Sonderzeichen");
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
        <MfaKarte />

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
