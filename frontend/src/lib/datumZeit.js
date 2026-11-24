/** "24.11.2026, 10:00" — leer bei fehlendem oder ungültigem Zeitpunkt. */
export function datumZeit(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric",
                                     hour: "2-digit", minute: "2-digit" });
}
