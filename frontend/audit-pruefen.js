#!/usr/bin/env node
/* Release-Gate fuer `yarn audit` (Audit 09/2026, Punkt 49).
 *
 * Laeuft in CI im Ordner frontend/: `yarn audit --json --groups dependencies`
 * wird ausgewertet; jede Schwachstelle ab Schweregrad "high" blockiert —
 * ausser sie steht mit Begruendung UND Ablaufdatum in audit-ausnahmen.json.
 * Abgelaufene Ausnahmen blockieren wieder.
 */
const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const MIN = { low: 0, moderate: 1, high: 2, critical: 3 };
const SCHWELLE = MIN[process.env.AUDIT_MIN_SEVERITY || "high"];

const ausnahmenPfad = path.join(__dirname, "audit-ausnahmen.json");
const ausnahmen = fs.existsSync(ausnahmenPfad)
  ? JSON.parse(fs.readFileSync(ausnahmenPfad, "utf8")).ausnahmen || []
  : [];
const heute = new Date().toISOString().slice(0, 10);

const res = spawnSync("yarn", ["audit", "--json", "--groups", "dependencies"], {
  encoding: "utf8", shell: true, maxBuffer: 64 * 1024 * 1024,
});
const zeilen = (res.stdout || "").split("\n").filter(Boolean);
const funde = new Map();
for (const z of zeilen) {
  let obj;
  try { obj = JSON.parse(z); } catch { continue; }
  if (obj.type !== "auditAdvisory") continue;
  const a = obj.data.advisory;
  if (MIN[a.severity] === undefined || MIN[a.severity] < SCHWELLE) continue;
  funde.set(String(a.id), a);
}

let blockierend = 0;
for (const [id, a] of funde) {
  const aus = ausnahmen.find((x) => String(x.id) === id || x.modul === a.module_name);
  if (aus && aus.bis >= heute && aus.grund) {
    console.log(`AUSNAHME  ${a.severity.padEnd(8)} ${a.module_name.padEnd(24)} #${id} bis ${aus.bis} — ${aus.grund}`);
    continue;
  }
  blockierend += 1;
  console.log(`BLOCKIERT ${a.severity.padEnd(8)} ${a.module_name.padEnd(24)} #${id} ${a.title} (patched: ${a.patched_versions})${aus ? " [Ausnahme abgelaufen]" : ""}`);
}
console.log(`\n${funde.size} Schwachstellen >= ${process.env.AUDIT_MIN_SEVERITY || "high"}, davon ${blockierend} blockierend.`);
process.exit(blockierend ? 1 : 0);
