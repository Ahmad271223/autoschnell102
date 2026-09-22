import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";
import { lokalerSpeicher, lesen, schreiben } from "@/lib/speicher";

const STORAGE_KEY = "ah_theme";

/** Setzt das Design und fuehrt die Browserleiste mit (18.09.2026: auf dem
 *  Handy blieb der Balken ueber der Seite auch im hellen Design schwarz). */
function setzen(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const leiste = document.querySelector('meta[name="theme-color"]');
  if (leiste) leiste.setAttribute("content", theme === "light" ? "#f5f5f7" : "#0a0a0a");
}

export function applyStoredTheme() {
  // Pruefbericht 20.09.2026 (B1): Das hier laeuft auf Modulebene, BEVOR
  // React startet. Ein ungeschuetzter Speicherzugriff warf bei blockierten
  // Website-Daten (Safari/iOS, Firmenrichtlinie) — und die App startete gar
  // nicht erst: komplett weisse Seite, keine Fehlergrenze konnte greifen.
  const stored = lesen(lokalerSpeicher(), STORAGE_KEY);
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

export default function ThemeToggle({ variante = "segment" } = {}) {
  const [theme, setTheme] = useState(() => {
    if (typeof document === "undefined") return "dark";
    return document.documentElement.getAttribute("data-theme") || "dark";
  });

  useEffect(() => {
    setzen(theme);
    schreiben(lokalerSpeicher(), STORAGE_KEY, theme);
  }, [theme]);

  const toggle = () => setTheme((t) => (t === "light" ? "dark" : "light"));

  if (variante === "symbol") {
    // Rollenprüfung 22.09.2026 (RP-024/M-19): In der 64 px schmalen
    // Seitenleiste ragte der Zwei-Segment-Schalter (~90 px) heraus. Dort
    // steht jetzt ein runder Knopf mit dem Symbol des Ziel-Designs.
    const hell = theme === "light";
    return (
      <button
        type="button"
        onClick={toggle}
        data-testid="theme-toggle"
        aria-pressed={hell}
        aria-label={hell ? "Helles Design aktiv — auf dunkles wechseln" : "Dunkles Design aktiv — auf helles wechseln"}
        title={hell ? "Auf dunkles Design wechseln" : "Auf helles Design wechseln"}
        className="w-9 h-9 rounded-full flex items-center justify-center hover:bg-white/5"
        style={{ color: "var(--text-secondary)", border: "1px solid var(--border-default)" }}
      >
        {hell ? <Moon size={15} /> : <Sun size={15} />}
      </button>
    );
  }

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
