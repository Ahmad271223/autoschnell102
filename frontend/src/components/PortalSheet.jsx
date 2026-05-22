/**
 * PortalSheet – modales Fenster zur Portal-Auswahl.
 *
 * Erscheint nach Vergleich oder manueller Suche.
 * Jeder Button ist ein direkter User-Klick → kein Popup-Blocker.
 */
import { ExternalLink, X } from "lucide-react";
import { openInPopup, openMultiple } from "@/lib/popup";

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
        className="fixed z-50 left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-full max-w-sm mx-4 rounded-2xl shadow-2xl overflow-hidden"
        style={{ background: "var(--card-bg)", border: "1px solid var(--divider)" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 pt-5 pb-3">
          <div>
            <p className="overline mb-0.5">PORTAL WÄHLEN</p>
            <h2 className="font-display font-bold text-lg tracking-tight" style={{ color: "var(--text-primary)" }}>
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
          >
            <X size={18} />
          </button>
        </div>

        {/* Divider */}
        <div style={{ height: 1, background: "var(--divider)", margin: "0 20px" }} />

        {/* Buttons */}
        <div className="p-4 flex flex-col gap-2.5">
          {/* mobile.de */}
          {mobileUrl && (
            <button
              type="button"
              onClick={openMobile}
              className="w-full flex items-center gap-3 px-4 py-3.5 rounded-xl text-left transition-all"
              style={{
                background: "var(--hover-bg)",
                border: "1px solid var(--divider)",
                color: "var(--text-primary)",
              }}
              onMouseEnter={(e) => e.currentTarget.style.background = "var(--input-bg)"}
              onMouseLeave={(e) => e.currentTarget.style.background = "var(--hover-bg)"}
            >
              <span
                className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-black shrink-0"
                style={{ background: "#e8472a", color: "white" }}
              >
                m
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-semibold text-sm">mobile.de öffnen</div>
                <div className="text-[11px] truncate" style={{ color: "var(--text-muted)" }}>
                  {mobileUrl}
                </div>
              </div>
              <ExternalLink size={14} style={{ color: "var(--text-muted)", shrink: 0 }} />
            </button>
          )}

          {/* AutoScout24 */}
          {autoscoutUrl && (
            <button
              type="button"
              onClick={openAutoscout}
              className="w-full flex items-center gap-3 px-4 py-3.5 rounded-xl text-left transition-all"
              style={{
                background: "var(--hover-bg)",
                border: "1px solid var(--divider)",
                color: "var(--text-primary)",
              }}
              onMouseEnter={(e) => e.currentTarget.style.background = "var(--input-bg)"}
              onMouseLeave={(e) => e.currentTarget.style.background = "var(--hover-bg)"}
            >
              <span
                className="w-8 h-8 rounded-lg flex items-center justify-center text-xs font-black shrink-0"
                style={{ background: "#ff6600", color: "white" }}
              >
                AS
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-semibold text-sm">AutoScout24 öffnen</div>
                <div className="text-[11px] truncate" style={{ color: "var(--text-muted)" }}>
                  {autoscoutUrl}
                </div>
              </div>
              <ExternalLink size={14} style={{ color: "var(--text-muted)" }} />
            </button>
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
