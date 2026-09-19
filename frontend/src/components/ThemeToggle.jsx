import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";

const STORAGE_KEY = "ah_theme";

/** Setzt das Design und fuehrt die Browserleiste mit (18.09.2026: auf dem
 *  Handy blieb der Balken ueber der Seite auch im hellen Design schwarz). */
function setzen(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const leiste = document.querySelector('meta[name="theme-color"]');
  if (leiste) leiste.setAttribute("content", theme === "light" ? "#f5f5f7" : "#0a0a0a");
}

export function applyStoredTheme() {
  const stored = localStorage.getItem(STORAGE_KEY);
  const theme = stored === "light" ? "light" : "dark";
  setzen(theme);
  return theme;
}

/** Aktuelles Design fuer Bauteile, die ihre Farben selbst waehlen (Meldungen).
 *  Haengt am Attribut, damit es auch zieht, wenn der Schalter woanders steht. */
export function useTheme() {
  const [theme, setTheme] = useState(() =>
    (typeof document === "undefined"
      ? "dark" : document.documentElement.getAttribute("data-theme") || "dark"));
  useEffect(() => {
    const beobachter = new MutationObserver(() =>
      setTheme(document.documentElement.getAttribute("data-theme") || "dark"));
    beobachter.observe(document.documentElement,
                       { attributes: true, attributeFilter: ["data-theme"] });
    return () => beobachter.disconnect();
  }, []);
  return theme;
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState(() => {
    if (typeof document === "undefined") return "dark";
    return document.documentElement.getAttribute("data-theme") || "dark";
  });

  useEffect(() => {
    setzen(theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "light" ? "dark" : "light"));

  return (
    <button
      onClick={toggle}
      data-testid="theme-toggle"
      title={theme === "light" ? "Auf dunkles Design wechseln" : "Auf helles Design wechseln"}
      className="apple-segment"
      style={{ padding: 3 }}
    >
      <span
        className={`apple-segment-item ${theme === "light" ? "active" : ""}`}
        aria-label="Light"
      >
        <Sun size={13} />
      </span>
      <span
        className={`apple-segment-item ${theme === "dark" ? "active" : ""}`}
        aria-label="Dark"
      >
        <Moon size={13} />
      </span>
    </button>
  );
}
