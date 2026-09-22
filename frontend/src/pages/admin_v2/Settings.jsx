import { useUngespeichert } from "@/lib/ungespeichert";
import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { tokenSetzen, TOKEN_APP } from "@/lib/sitzung";
import { PageHeader, Card, Button } from "./_ui";
import { KeyRound, Crown, ShieldCheck } from "lucide-react";
import { useEffect } from "react";

function MfaKarte() {
  const [st, setSt] = useState(null);
  const [setup, setSetup] = useState(null);      // {secret, otpauth_uri}
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState(null);      // Wiederherstellungscodes (einmalig)
  const [busy, setBusy] = useState(false);
  // Wunsch Ahmad 21.09.2026 (Pruefbericht AD-06): nur die Notfall-Codes neu
  // erzeugen — vorher ging das nur ueber Abschalten und Neu-Einrichten.
  const [neuOffen, setNeuOffen] = useState(false);
  const [neuCode, setNeuCode] = useState("");
  // Runde 31: Die Codes werden genau einmal gezeigt — ein Neuladen haette den
  // Notzugang des einzigen Super-Admins vernichtet.
  useUngespeichert(Boolean(codes?.length));
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
      // Runde 15: die Aktivierung beendet die alte Sitzung ohne zweiten Faktor —
      // dieser Tab bekommt sein neues Token gleich mit.
      if (r.data.token) tokenSetzen(TOKEN_APP, r.data.token, { nurSitzung: true });
      setCodes(r.data.wiederherstellungscodes || []); setSetup(null); setCode("");
      toast.success("Zwei-Faktor-Anmeldung ist aktiv"); load();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };
  const deaktivieren = async () => {
    // Rollenpruefung 22.09.2026 (RP-556): In Produktion ist der zweite Faktor
    // fuer den Betreiber Pflicht — wer abschaltet und sich abmeldet, kam
    // vorher nie wieder herein. Jetzt: deutliche Rueckfrage, der Server laesst
    // 30 Minuten Gnadenfrist, und die Neu-Einrichtung oeffnet sich sofort.
    if (st?.pflicht && !window.confirm(
      "Achtung: Für den Betreiber ist die Zwei-Faktor-Anmeldung Pflicht.\n\n"
      + "Nach dem Abschalten bleiben 30 Minuten, um sie (z. B. auf dem neuen Handy) neu "
      + "einzurichten. Danach ist nach dem Abmelden KEINE Anmeldung mehr möglich "
      + "(nur noch über scripts/mfa_pruefen.py auf dem Server).\n\nTrotzdem abschalten?")) return;
    const c = window.prompt("Zum Abschalten den aktuellen Code aus der App eingeben:");
    if (!c) return;
    setBusy(true);
    let pflicht = false;
    try {
      const r = await api.post("/admin/me/mfa/deaktivieren", { code: c });
      pflicht = !!r.data?.pflicht;
      if (pflicht) toast.warning(r.data?.hinweis || "Bitte die Zwei-Faktor-Anmeldung jetzt neu einrichten.", { duration: 15000 });
      else toast.success("Zwei-Faktor abgeschaltet");
      setCodes(null); setNeuOffen(false); load();
    }
    catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
    if (pflicht) await einrichten();
  };
  const codesNeu = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/me/mfa/codes-neu", { code: neuCode.trim() });
      setCodes(r.data.wiederherstellungscodes || []);
      setNeuOffen(false); setNeuCode("");
      toast.success("Neue Notfall-Codes erzeugt — die alten gelten nicht mehr");
      load();
    } catch (e) { toast.error(errMsg(e)); setNeuCode(""); } finally { setBusy(false); }
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
      {st && !st.aktiv && st.neu_einrichten_bis && (
        <div className="rounded-lg p-3 mb-3 text-[12.5px] text-amber-200" role="alert" data-testid="mfa-gnadenfrist"
             style={{ background: "rgba(245,158,11,0.08)", border: "1px solid rgba(245,158,11,0.35)" }}>
          Zwei-Faktor ist abgeschaltet, für den Betreiber aber Pflicht: bitte bis{" "}
          {new Date(st.neu_einrichten_bis).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} Uhr
          neu einrichten — danach ist nach dem Abmelden keine Anmeldung mehr möglich.
        </div>
      )}
      {st && !st.aktiv && !setup && (
        <Button size="sm" onClick={einrichten} disabled={busy} data-testid="mfa-einrichten">Einrichten</Button>
      )}
      {setup && (
        <div className="rounded-lg p-3 mb-3" style={{ background: "var(--wa-03)", border: "1px solid var(--wa-08)" }}>
          <div className="text-[12px] text-zinc-400 mb-1">1. In der App „Konto hinzufügen“ und diesen Schlüssel eingeben (oder den Link öffnen):</div>
          <div className="font-mono text-[13px] text-white break-all select-all" data-testid="mfa-secret">{setup.secret}</div>
          <a href={setup.otpauth_uri} className="text-[12px] text-sky-400 underline break-all">{setup.otpauth_uri}</a>
          <div className="text-[12px] text-zinc-400 mt-3 mb-1">2. Den angezeigten 6-stelligen Code eingeben:</div>
          <div className="flex gap-2">
            <input value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" placeholder="123456" data-testid="mfa-aktivieren-code"
                   className="h-9 px-3 rounded-lg bg-transparent border text-sm outline-none w-40" style={{ borderColor: "var(--border-default)" }} />
            <Button size="sm" onClick={aktivieren} disabled={busy || code.length < 6} data-testid="mfa-aktivieren">Aktivieren</Button>
            <Button size="sm" variant="ghost" onClick={() => setSetup(null)}>Abbrechen</Button>
          </div>
        </div>
      )}
      {codes && (
        <div className="rounded-lg p-3 mb-3" style={{ background: "rgba(52,199,89,0.08)", border: "1px solid rgba(52,199,89,0.3)" }} data-testid="mfa-wiederherstellung">
          <div className="text-[12.5px] text-emerald-200 mb-1">Wiederherstellungscodes — jetzt sicher aufbewahren (werden nur einmal angezeigt, jeder gilt einmal):</div>
          <div className="font-mono text-[13px] text-white grid grid-cols-2 gap-x-6 select-all">{codes.map((c) => <div key={c}>{c}</div>)}</div>
          {/* Pruefbericht 20.09.2026 (AD-06/O3): Die Codes standen nur als Text
              da — ein Klick in die Seitenleiste, und der einzige Notzugang des
              einzigen Super-Admins war weg. Jetzt: kopieren, als Datei
              speichern, und erst "sicher abgelegt" gibt die Seite frei. */}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" data-testid="mfa-codes-kopieren"
                    onClick={async () => {
                      try { await navigator.clipboard.writeText(codes.join("\n")); toast.success("Codes kopiert"); }
                      catch { window.prompt("Codes kopieren:", codes.join(" ")); }
                    }}>Kopieren</Button>
            <Button size="sm" variant="secondary" data-testid="mfa-codes-datei"
                    onClick={() => {
                      const text = "AutoSchnell — Wiederherstellungscodes (Zwei-Faktor)\n"
                        + `erstellt ${new Date().toLocaleString("de-DE")} — jeder Code gilt einmal\n\n`
                        + codes.join("\n") + "\n";
                      const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
                      const a = document.createElement("a");
                      a.href = url; a.download = "autoschnell-wiederherstellungscodes.txt";
                      document.body.appendChild(a); a.click(); a.remove();
                      setTimeout(() => URL.revokeObjectURL(url), 60000);
                    }}>Als Datei speichern</Button>
            <Button size="sm" data-testid="mfa-codes-abgelegt"
                    onClick={() => {
                      if (window.confirm("Codes wirklich sicher abgelegt? Danach werden sie nie wieder angezeigt.")) setCodes(null);
                    }}>Codes sicher abgelegt</Button>
          </div>
        </div>
      )}
      {st && st.aktiv && neuOffen && !codes && (
        <div className="rounded-lg p-3 mb-3" style={{ background: "var(--wa-03)", border: "1px solid var(--wa-08)" }} data-testid="mfa-codes-neu-box">
          <div className="text-[12px] text-zinc-400 mb-1">
            Neue Notfall-Codes (Wiederherstellungscodes) erzeugen: Die bisherigen Codes gelten danach sofort nicht mehr.
            Der Eintrag in der Authenticator-App bleibt unverändert.
          </div>
          <div className="text-[12px] text-zinc-400 mb-1">Aktuellen 6-stelligen Code aus der App eingeben (nicht den Code vom Anmelden):</div>
          <div className="flex flex-wrap gap-2">
            <input value={neuCode} onChange={(e) => setNeuCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code"
                   placeholder="123456" maxLength={7} data-testid="mfa-codes-neu-code"
                   onKeyDown={(e) => { if (e.key === "Enter" && !busy && neuCode.replace(/\s/g, "").length === 6) codesNeu(); }}
                   className="h-9 px-3 rounded-lg bg-transparent border text-sm outline-none w-40" style={{ borderColor: "var(--border-default)" }} />
            <Button size="sm" onClick={codesNeu} disabled={busy || neuCode.replace(/\s/g, "").length !== 6} data-testid="mfa-codes-neu-bestaetigen">Codes erzeugen</Button>
            <Button size="sm" variant="ghost" onClick={() => { setNeuOffen(false); setNeuCode(""); }}>Abbrechen</Button>
          </div>
        </div>
      )}
      {st && st.aktiv && (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-[12px] text-zinc-500">
            aktiv seit {st.aktiviert_am ? new Date(st.aktiviert_am).toLocaleDateString("de-DE") : "—"} · {st.wiederherstellungscodes_uebrig} Wiederherstellungscodes übrig
            {st.codes_erneuert_am ? ` · neu erzeugt am ${new Date(st.codes_erneuert_am).toLocaleDateString("de-DE")}` : ""}
          </span>
          {!codes && !neuOffen && (
            <Button size="sm" variant="secondary" onClick={() => { setNeuOffen(true); setNeuCode(""); }} disabled={busy}
                    data-testid="mfa-codes-neu">Neue Notfall-Codes erzeugen</Button>
          )}
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
          background: "var(--wa-05)",
          border: "1px solid var(--wa-10)",
        }}
      />
    </div>
  );
}
