/**
 * CookieConsent — DSGVO-konformes Zustimmungsbanner.
 *
 * Zeigt sich beim ersten Besuch. Nach Zustimmung wird PostHog initialisiert
 * und die Entscheidung in localStorage gespeichert (key: "ah_analytics_consent").
 * Ablehnen deaktiviert Analytics dauerhaft (gespeichert als "0").
 */
import { useState, useEffect } from "react";

const CONSENT_KEY = "ah_analytics_consent";

export default function CookieConsent() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(CONSENT_KEY);
    if (stored === null) {
      // First visit — show banner after a short delay so the app renders first.
      const t = setTimeout(() => setVisible(true), 800);
      return () => clearTimeout(t);
    }
    if (stored === "1") {
      // Previously accepted — init PostHog immediately.
      if (typeof window.__initPosthog === "function") window.__initPosthog();
    }
  }, []);

  const accept = () => {
    localStorage.setItem(CONSENT_KEY, "1");
    setVisible(false);
    if (typeof window.__initPosthog === "function") window.__initPosthog();
  };

  const decline = () => {
    localStorage.setItem(CONSENT_KEY, "0");
    setVisible(false);
    // If PostHog was somehow already loaded, opt out.
    if (window.posthog && typeof window.posthog.opt_out_capturing === "function") {
      window.posthog.opt_out_capturing();
    }
  };

  if (!visible) return null;

  return (
    <div
      role="dialog"
      aria-live="polite"
      aria-label="Cookie-Zustimmung"
      className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[9999] w-full max-w-lg mx-4 rounded-2xl shadow-2xl overflow-hidden"
      style={{
        background: "var(--card-bg)",
        border: "1px solid var(--divider)",
      }}
    >
      <div className="p-5">
        <p className="font-semibold text-sm mb-1" style={{ color: "var(--text-primary)" }}>
          🍪 Cookies &amp; Analyse
        </p>
        <p className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Wir verwenden PostHog, um die App zu verbessern (anonyme Nutzungsstatistiken).
          Keine Weitergabe an Dritte. Gemäß DSGVO bitten wir um deine Zustimmung.
        </p>
        <div className="flex gap-2 mt-4">
          <button
            onClick={accept}
            className="apple-btn apple-btn-primary flex-1 !py-2 !text-xs !justify-center"
          >
            Akzeptieren
          </button>
          <button
            onClick={decline}
            className="apple-btn flex-1 !py-2 !text-xs !justify-center"
            style={{ color: "var(--text-muted)" }}
          >
            Ablehnen
          </button>
        </div>
      </div>
    </div>
  );
}
