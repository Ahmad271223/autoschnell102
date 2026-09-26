import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { Globe2, Flag, Loader2, ArrowLeftRight } from "lucide-react";

/**
 * Zeigt das aktuell aktive Filter-Profil (Inland / Export) und erlaubt
 * einen One-Click-Wechsel. Wird oben rechts auf der Vergleich-Seite
 * eingebunden und überall, wo man sofort sehen soll, welche Regeln
 * gerade greifen.
 */
export default function ProfileBadge({ onChange }) {
  // Rollenprüfung 22.09.2026 (RP-005/RP-104/RP-255/RP-425): Nach dem Wechsel
  // blieb der Anmelde-Kontext auf dem alten Profil. Die Einstellungen bauten
  // ihr Formular daraus, und das nächste Speichern drehte Export still auf
  // Inland zurück; die manuelle Suche zeigte das alte Profil. Jetzt wird der
  // Kontext nach dem Wechsel neu geladen.
  const { refresh } = useAuth() || {};
  const [profile, setProfile] = useState(null);
  const [busy, setBusy] = useState(false);
  // Pruefbericht 20.09.2026 (K-03/U-137): Bei einem Ladefehler zeigte das
  // Abzeichen "Inland" — geraten. Ein Klick darauf schaltete dann womoeglich
  // ungewollt auf Export um. Jetzt: "unbekannt", Klick laedt neu.
  const [unbekannt, setUnbekannt] = useState(false);
  const laden = () => {
    setUnbekannt(false);
    api.get("/dealer/settings")
      .then((r) => setProfile(r.data?.active_profile || "inland"))
      .catch(() => { setProfile(null); setUnbekannt(true); });
  };
  useEffect(() => { laden(); }, []);

  const toggle = async () => {
    if (unbekannt) { laden(); return; }
    if (busy || !profile) return;
    const next = profile === "inland" ? "export" : "inland";
    setBusy(true);
    try {
      await api.put("/dealer/active-profile", { active_profile: next });
      setProfile(next);
      onChange?.(next);
      // Ohne await: der Wechsel ist gespeichert, das Nachladen darf den
      // Knopf nicht blockieren (Fehler behandelt refresh() selbst).
      if (typeof refresh === "function") refresh();
      toast.success(
        next === "inland" ? "Filter: Inland aktiv" : "Filter: Export aktiv",
      );
    } catch (err) {
      toast.error(errMsg(err, "Wechsel fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  if (unbekannt) {
    return (
      <button onClick={toggle} data-testid="profile-badge-unbekannt"
              title="Das aktive Profil konnte nicht geladen werden. Klick lädt neu."
              className="flex items-center gap-2 px-3.5 py-2 rounded-xl shrink-0 border text-sm"
              style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
        <ArrowLeftRight size={13} /> Profil unbekannt – neu laden
      </button>
    );
  }
  if (!profile) return null;

  const isInland = profile === "inland";
  const Icon = isInland ? Flag : Globe2;
  const color = isInland ? "var(--accent-green)" : "var(--st-blau)";
  const bg = isInland ? "rgba(52,199,89,0.12)" : "rgba(10,132,255,0.12)";
  const border = isInland ? "rgba(52,199,89,0.35)" : "rgba(10,132,255,0.4)";

  return (
    <button
      onClick={toggle}
      disabled={busy}
      data-testid="profile-badge"
      title={`Aktives Profil: ${isInland ? "Inland" : "Export"}. Klick wechselt.`}
      className="group flex items-center gap-2.5 px-3.5 py-2 rounded-xl disabled:opacity-60 shrink-0 transition-colors"
      style={{ background: bg, border: `1px solid ${border}`, color }}
    >
      <Icon size={15} />
      <div className="text-left">
        <div className="text-[9px] uppercase tracking-[0.2em] font-bold opacity-70 leading-none">
          Aktiver Filter
        </div>
        <div className="text-sm font-bold leading-tight mt-0.5">
          {isInland ? "Inland" : "Export"}
        </div>
      </div>
      {busy ? (
        <Loader2 size={13} className="animate-spin" />
      ) : (
        <ArrowLeftRight size={13} className="opacity-60 group-hover:opacity-100" />
      )}
    </button>
  );
}
