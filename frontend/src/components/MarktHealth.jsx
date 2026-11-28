import { HEALTH_REIHE, healthInfo, healthStil } from "@/lib/markt";

/**
 * Master-Auftrag Phase F (27.09.2026): Segment-Health als eigene Kennzeichnung — getrennt von der technischen
 * Datenqualität (MarktQualitaet). Ein technisch sauberer, aber dünner Markt ist „Qualität gut“ und „THIN“.
 * Farben nur über CSS-Variablen (helle und dunkle Ansicht), HOT deutlich hervorgehoben.
 */
export function HealthBadge({ health, klein = false, titel, testid = "markt-health" }) {
  if (!health) return null;
  const i = healthInfo(health);
  const cls = `${klein ? "text-[10px] px-1.5" : "text-[11px] px-2 py-0.5"} rounded-full border whitespace-nowrap inline-flex items-center`;
  return (
    <span className={cls} style={healthStil(health)} title={titel || i.text} data-testid={testid} data-health={health}>
      {klein ? i.kurz : `${i.kurz === "—" ? "UNKNOWN" : i.kurz} · ${i.text}`}
    </span>
  );
}

/** Zähler je Health-Status ("12 HEALTHY · 3 THIN · 2 EMPTY") in den Statusfarben. */
export function HealthZaehler({ zaehler, testid = "markt-health-zaehler" }) {
  const teile = HEALTH_REIHE.filter((k) => zaehler?.[k]).map((k) => (
    <span key={k} style={{ color: healthInfo(k).farbe, fontWeight: healthInfo(k).hervorheben ? 700 : 400 }}>
      {zaehler[k]} {k === "UNKNOWN" ? "offen" : k}
    </span>
  ));
  if (!teile.length) return <span className="text-zinc-500" data-testid={testid}>—</span>;
  return <span className="inline-flex flex-wrap gap-x-1.5 text-[11px]" data-testid={testid}>{teile}</span>;
}

export default HealthBadge;
