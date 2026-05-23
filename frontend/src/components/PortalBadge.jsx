/**
 * PortalBadge – einheitliche Logo-Darstellung für mobile.de und AutoScout24.
 *
 * Wird sowohl in der Vergleichs-Toolbar (als Inhalt eines Toggle-Buttons)
 * als auch im PortalSheet-Dialog (als statisches Icon) verwendet. Damit
 * sind Größen, Hintergründe und der Aktiv/Inaktiv-Look überall identisch.
 *
 * Props:
 *   - kind:    "mobile" | "autoscout"
 *   - active:  Boolean — Farbe an / Graustufen aus
 *   - size:    "sm" (40h, in Dialogen) | "md" (48h, Toolbar)
 */

const SPEC = {
  mobile: {
    bg: "#ffffff",
    accent: "#e8472a",
    src: "/logos/mobile-de.png",
    alt: "mobile.de",
    pad: 6,
    // Verhältnis Bildhöhe : Boxhöhe — mobile.de Logo füllt den Frame stärker aus.
    fill: 0.62,
  },
  autoscout: {
    bg: "#2b2b2b",
    accent: "#ffe600",
    src: "/logos/autoscout24.png",
    alt: "AutoScout24",
    pad: 4,
    // AutoScout-Logo ist gestapelt (Auto/Scout24) → mehr Höhe.
    fill: 0.85,
  },
};

const SIZES = {
  sm: 40,
  md: 48,
};

export default function PortalBadge({ kind, active = true, size = "md" }) {
  const spec = SPEC[kind];
  if (!spec) return null;
  const h = SIZES[size] || SIZES.md;
  const w = Math.round(h * 1.55);
  const imgH = Math.round(h * spec.fill);

  return (
    <span
      aria-hidden="true"
      className="rounded-lg flex items-center justify-center shrink-0 overflow-hidden transition-all"
      style={{
        background: active ? spec.bg : "var(--hover-bg)",
        border: `1px solid ${active ? spec.accent : "var(--divider)"}`,
        width: w,
        height: h,
        padding: spec.pad,
        opacity: active ? 1 : 0.55,
        filter: active ? "none" : "grayscale(1)",
      }}
    >
      <img
        src={spec.src}
        alt={spec.alt}
        style={{ height: imgH, width: "auto", display: "block" }}
      />
    </span>
  );
}
