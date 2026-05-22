import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Globe2, Flag, Loader2, ArrowLeftRight } from "lucide-react";

/**
 * Zeigt das aktuell aktive Filter-Profil (Inland / Export) und erlaubt
 * einen One-Click-Wechsel. Wird oben rechts auf der Vergleich-Seite
 * eingebunden und überall, wo man sofort sehen soll, welche Regeln
 * gerade greifen.
 */
export default function ProfileBadge({ onChange }) {
  const [profile, setProfile] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get("/dealer/settings")
      .then((r) => setProfile(r.data?.active_profile || "inland"))
      .catch(() => setProfile("inland"));
  }, []);

  const toggle = async () => {
    if (busy || !profile) return;
    const next = profile === "inland" ? "export" : "inland";
    setBusy(true);
    try {
      await api.put("/dealer/active-profile", { active_profile: next });
      setProfile(next);
      onChange?.(next);
      toast.success(
        next === "inland" ? "Filter: Inland aktiv" : "Filter: Export aktiv",
      );
    } catch (err) {
      toast.error(errMsg(err, "Wechsel fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  if (!profile) return null;

  const isInland = profile === "inland";
  const Icon = isInland ? Flag : Globe2;
  const color = isInland ? "var(--accent-green)" : "#0a84ff";
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
