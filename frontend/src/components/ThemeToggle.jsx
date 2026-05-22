import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";

const STORAGE_KEY = "ah_theme";

export function applyStoredTheme() {
  const stored = localStorage.getItem(STORAGE_KEY);
  const theme = stored === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", theme);
  return theme;
}

export default function ThemeToggle() {
  const [theme, setTheme] = useState(() => {
    if (typeof document === "undefined") return "dark";
    return document.documentElement.getAttribute("data-theme") || "dark";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
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
