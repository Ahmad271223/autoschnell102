/**
 * PortalSheet – modales Fenster zur Portal-Auswahl.
 *
 * Erscheint nach Vergleich oder manueller Suche.
 * Jeder Button ist ein direkter User-Klick → kein Popup-Blocker.
 */
import { ExternalLink, X } from "lucide-react";
import { openInPopup, openMultiple } from "@/lib/popup";
import PortalBadge from "@/components/PortalBadge";

export default function PortalSheet({ mobileUrl, autoscoutUrl, onClose }) {
  if (!mobileUrl && !autoscoutUrl) return null;

  const openMobile = () => {
    openInPopup(mobileUrl, "mobileFilterWindow");
    onClose();
  };

  const openAutoscout = () => {
    openInPopup(autoscoutUrl, "autoscoutFilterWindow");
    onClose();
  };

  const openBoth = () => {
    openMultiple([
      { url: mobileUrl,     name: "mobileFilterWindow" },
      { url: autoscoutUrl,  name: "autoscoutFilterWindow" },
    ].filter((u) => u.url));
    onClose();
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50"
        style={{ background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)" }}
        onClick={onClose}
      />

      {/* Sheet */}
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="portal-sheet-title"
        className="fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-sm mx-4 rounded-2xl shadow-2xl overflow-hidden"
        style={{ background: "var(--card-bg)", border: "1px solid var(--divider)" }}
      >
        {/* Akzent-Balken oben — visueller Anker */}
        <div style={{ height: 3, background: "var(--accent-red)" }} />

        {/* Header */}
        <div className="flex items-center justify-between px-5 pt-4 pb-3">
          <div>
            <p className="overline mb-0.5">Portal wählen</p>
            <h2
              id="portal-sheet-title"
              className="font-display font-bold text-lg tracking-tight"
              style={{ color: "var(--text-primary)" }}
            >
              Welche Seite öffnen?
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-xl transition-colors"
            style={{ color: "var(--text-muted)" }}
            onMouseEnter={(e) => e.currentTarget.style.background = "var(--hover-bg)"}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
            title="Schließen"
            aria-label="Dialog schließen"
          >
            <X size={18} />
          </button>
        </div>

        {/* Divider */}
        <div style={{ height: 1, background: "var(--hairline)", margin: "0 20px" }} />

        {/* Buttons */}
        <div className="p-4 flex flex-col gap-2.5">
          {/* mobile.de */}
          {mobileUrl && (
            <PortalActionButton
              kind="mobile"
              accent="#e8472a"
              label="mobile.de öffnen"
              url={mobileUrl}
              onClick={openMobile}
            />
          )}

          {/* AutoScout24 */}
          {autoscoutUrl && (
            <PortalActionButton
              kind="autoscout"
              accent="#ffe600"
              label="AutoScout24 öffnen"
              url={autoscoutUrl}
              onClick={openAutoscout}
            />
          )}

          {/* Beide öffnen */}
          {mobileUrl && autoscoutUrl && (
            <button
              type="button"
              onClick={openBoth}
              className="apple-btn apple-btn-primary w-full !justify-center !py-3 mt-1"
            >
              <ExternalLink size={14} />
              Beide öffnen
            </button>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 pb-4">
          <button
            type="button"
            onClick={onClose}
            className="w-full py-2 rounded-xl text-sm transition-colors"
            style={{ color: "var(--text-muted)" }}
            onMouseEnter={(e) => e.currentTarget.style.background = "var(--hover-bg)"}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
          >
            Schließen
          </button>
        </div>
      </div>
    </>
  );
}

/* ───────── Hilfs-Komponenten ───────── */

function PortalActionButton({ kind, accent, label, url, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group w-full flex items-center gap-3 px-3 py-3 rounded-xl text-left transition-all"
      style={{
        background: "var(--hover-bg)",
        border: "1px solid var(--divider)",
        color: "var(--text-primary)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "var(--input-bg)";
        e.currentTarget.style.borderColor = accent;
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "var(--hover-bg)";
        e.currentTarget.style.borderColor = "var(--divider)";
      }}
    >
      <PortalBadge kind={kind} size="sm" />
      <div className="min-w-0 flex-1">
        <div className="font-semibold text-sm">{label}</div>
        <div className="text-[11px] truncate font-mono" style={{ color: "var(--text-muted)" }}>
          {url}
        </div>
      </div>
      <ExternalLink size={14} className="shrink-0" style={{ color: "var(--text-muted)" }} />
    </button>
  );
}
