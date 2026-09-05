import { useState } from "react";
import { useDriver, driverApi } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Copy, Check, Building2, User, KeyRound } from "lucide-react";

export default function DriverSettings() {
  const { driver, refresh, logout } = useDriver();
  const [name, setName] = useState(driver?.display_name || "");
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "", repeat: "" });
  const [pwBusy, setPwBusy] = useState(false);

  const changePassword = async (e) => {
    e.preventDefault();
    if (pw.next.length < 8) return toast.error("Neues Passwort: mindestens 8 Zeichen");
    if (pw.next !== pw.repeat) return toast.error("Die Wiederholung stimmt nicht überein");
    setPwBusy(true);
    try {
      await driverApi.put("/driver/password", { current_password: pw.current, new_password: pw.next });
      toast.success("Passwort geändert – bitte neu anmelden");
      // Der Server hat alle Sitzungen beendet (Single-Session): sauber abmelden.
      logout?.();
      window.location.href = "/fahrer/login";
    } catch (err) {
      toast.error(errMsg(err, "Passwort konnte nicht geändert werden"));
    } finally {
      setPwBusy(false);
    }
  };

  const save = async (e) => {
    e.preventDefault();
    const n = name.trim();
    if (n.length < 2) return toast.error("Name zu kurz");
    if (n === driver.display_name) return;
    setSaving(true);
    try {
      await driverApi.put("/driver/me", { display_name: n });
      await refresh();
      toast.success("Name gespeichert");
    } catch (err) {
      toast.error(errMsg(err, "Speichern fehlgeschlagen"));
    } finally {
      setSaving(false);
    }
  };

  const copy = async () => {
    if (!driver?.driver_code) return;
    try {
      await navigator.clipboard.writeText(driver.driver_code);
      setCopied(true);
      toast.success("Fahrer-ID kopiert");
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Konnte nicht kopieren");
    }
  };

  if (!driver) return null;

  return (
    <div data-testid="driver-settings">
      <div className="overline">Einstellungen</div>
      <h1 className="font-display font-black text-2xl tracking-tighter mb-5">Mein Profil</h1>

      {/* Fahrer-ID Block */}
      <div className="tactical-card p-5">
        <div className="flex items-center gap-2 text-zinc-400 text-xs">
          <KeyRound size={12} /> DEINE FAHRER-ID
        </div>
        <div className="mt-3 flex items-center gap-3">
          <code data-testid="driver-code-display"
                className="flex-1 px-4 py-3 rounded-sm font-mono text-lg tracking-[0.15em] font-bold text-center"
                style={{ background: "rgba(255,59,48,0.08)", border: "1px solid rgba(255,59,48,0.25)",
                         color: "var(--accent-red)" }}>
            {driver.driver_code}
          </code>
          <button onClick={copy} data-testid="copy-code-btn"
            className="px-3 py-3 rounded-sm bg-white/5 hover:bg-white/10">
            {copied ? <Check size={16} className="text-emerald-400" /> : <Copy size={16} />}
          </button>
        </div>
        <p className="text-xs text-zinc-500 mt-3 leading-relaxed">
          Gib diese ID deinem Händler. Er fügt dich damit in seinen Einstellungen
          hinzu und kann dir Fahrten zuweisen.
        </p>
      </div>

      {/* Profilname */}
      <form onSubmit={save} className="tactical-card p-5 mt-4">
        <label className="flex items-center gap-2 text-zinc-400 text-xs">
          <User size={12} /> ANGEZEIGTER NAME (FÜR HÄNDLER)
        </label>
        <input data-testid="driver-name-input"
          value={name} onChange={(e) => setName(e.target.value)}
          className="input-base w-full mt-2" minLength={2} required />
        <p className="text-xs text-zinc-500 mt-2">
          So erscheint dein Name in der Fahrer-Liste bei jedem Händler, der dich hinzugefügt hat.
        </p>
        <button type="submit" disabled={saving || name.trim() === driver.display_name}
          data-testid="save-name-btn"
          className="kinetic-button mt-4 px-5 py-2.5 rounded-sm text-sm font-bold disabled:opacity-40">
          {saving ? "Speichere …" : "Speichern"}
        </button>
      </form>

      {/* Passwort ändern */}
      <form onSubmit={changePassword} className="tactical-card p-5 mt-4" data-testid="driver-password-form">
        <label className="flex items-center gap-2 text-zinc-400 text-xs">
          <KeyRound size={12} /> PASSWORT ÄNDERN
        </label>
        <input type="password" autoComplete="current-password" placeholder="Aktuelles Passwort"
          value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })}
          className="input-base w-full mt-2" required data-testid="driver-pw-current" />
        <input type="password" autoComplete="new-password" placeholder="Neues Passwort (min. 8 Zeichen)"
          value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })}
          className="input-base w-full mt-2" minLength={8} required data-testid="driver-pw-next" />
        <input type="password" autoComplete="new-password" placeholder="Neues Passwort wiederholen"
          value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })}
          className="input-base w-full mt-2" minLength={8} required data-testid="driver-pw-repeat" />
        <p className="text-xs text-zinc-500 mt-2">
          Nach der Änderung wirst du auf allen Geräten abgemeldet. Passwort vergessen? Über
          „Passwort vergessen" auf der Anmeldeseite bekommst du einen Link per E-Mail.
        </p>
        <button type="submit" disabled={pwBusy} data-testid="driver-pw-submit"
          className="kinetic-button mt-4 px-5 py-2.5 rounded-sm text-sm font-bold disabled:opacity-40">
          {pwBusy ? "Ändere …" : "Passwort ändern"}
        </button>
      </form>

      {/* Verknüpfte Händler */}
      <div className="tactical-card p-5 mt-4">
        <div className="flex items-center gap-2 text-zinc-400 text-xs">
          <Building2 size={12} /> VERKNÜPFTE HÄNDLER ({(driver.dealers || []).length})
        </div>
        <div className="mt-3 space-y-2">
          {(driver.dealers || []).length === 0 && (
            <div className="text-xs text-zinc-500 py-2">
              Noch kein Händler hat dich hinzugefügt.
            </div>
          )}
          {(driver.dealers || []).map((d) => (
            <div key={d.id} data-testid={`dealer-${d.id}`}
                 className="flex items-center justify-between py-2 px-3 rounded-sm"
                 style={{ background: "rgba(255,255,255,0.02)" }}>
              <div>
                <div className="font-semibold text-sm">{d.name}</div>
                {d.phone && <div className="text-xs text-zinc-500">{d.phone}</div>}
              </div>
              {d.phone && (
                <a href={`tel:${d.phone}`}
                  className="text-xs px-3 py-1.5 rounded-sm bg-white/5 hover:bg-white/10">
                  Anrufen
                </a>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-5 text-xs text-zinc-600 text-center">
        Eingeloggt als {driver.email}
      </div>
    </div>
  );
}
