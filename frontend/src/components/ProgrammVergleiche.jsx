import { ExternalLink, MonitorX } from "lucide-react";

/**
 * Programme zum Herunterladen (03.10.2026): wer hat wann welches Auto mit dem Programm verglichen,
 * welche PCs sind verbunden. Gemeinsam fuer die Chef-Ansicht (eigene Firma) und den Betreiber (alle).
 * Keine Programmnamen hier — die kommen vom Server.
 */
export function zeit(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "–";
  return d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fahrzeugText(f = {}) {
  const teile = [];
  const name = [f.marke, f.modell].filter(Boolean).join(" ");
  if (name) teile.push(name);
  if (f.ez_jahr) teile.push(`EZ ${f.ez_monat ? `${String(f.ez_monat).padStart(2, "0")}/` : ""}${f.ez_jahr}`);
  if (f.kilometer != null) teile.push(`${Number(f.kilometer).toLocaleString("de-DE")} km`);
  if (f.ps) teile.push(`${f.ps} PS`);
  if (f.preis) teile.push(`${Number(f.preis).toLocaleString("de-DE")} €`);
  return teile.join(" · ");
}

function kontoText(x) {
  return [x.name, x.konto && `(${x.konto})`].filter(Boolean).join(" ") || "–";
}

export function VerbindungsListe({ verbindungen = [], onTrennen, mitFirma = false }) {
  if (!verbindungen.length) return <div className="text-sm text-zinc-500" data-testid="pv-keine-pcs">Kein PC verbunden.</div>;
  return (
    <ul className="space-y-2" data-testid="pv-pcs">
      {verbindungen.map((v) => (
        <li key={v.id || v.user_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="font-semibold">{kontoText(v)}</span>
          {mitFirma && <span className="text-zinc-500">{v.firma}{v.kunden_nr ? ` · Kd.-Nr. ${v.kunden_nr}` : ""}</span>}
          <span className="text-zinc-500">PC „{v.pc_name || "unbekannt"}“ · seit {zeit(v.verbunden_am)} · zuletzt {zeit(v.zuletzt_am)}</span>
          {onTrennen && (
            <button type="button" onClick={() => onTrennen(v)} data-testid={`pv-trennen-${v.user_id}`}
                    className="apple-btn apple-btn-secondary !rounded-full !px-3 !py-1 text-xs inline-flex items-center gap-1">
              <MonitorX size={13} /> Trennen
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

export function VergleichsTabelle({ vergleiche = [], mitFirma = false }) {
  if (!vergleiche.length) return <div className="text-sm text-zinc-500" data-testid="pv-keine-vergleiche">Noch keine Vergleiche.</div>;
  return (
    <div className="overflow-x-auto" data-testid="pv-vergleiche">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-zinc-500 text-xs">
            <th className="py-2 pr-3 font-medium">Zeit</th>
            <th className="py-2 pr-3 font-medium">Konto</th>
            {mitFirma && <th className="py-2 pr-3 font-medium">Firma</th>}
            <th className="py-2 pr-3 font-medium">Fahrzeug</th>
            <th className="py-2 pr-3 font-medium">Inserat</th>
            <th className="py-2 pr-3 font-medium">Vergleiche</th>
          </tr>
        </thead>
        <tbody>
          {vergleiche.map((x) => (
            <tr key={x.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
              <td className="py-2 pr-3 whitespace-nowrap">{zeit(x.erstellt_am)}</td>
              <td className="py-2 pr-3">{kontoText(x)}<div className="text-xs text-zinc-500">PC „{x.pc_name || "–"}“</div></td>
              {mitFirma && <td className="py-2 pr-3">{x.firma}{x.kunden_nr ? <div className="text-xs text-zinc-500">Kd.-Nr. {x.kunden_nr}</div> : null}</td>}
              <td className="py-2 pr-3">{fahrzeugText(x.fahrzeug)}</td>
              <td className="py-2 pr-3 text-xs text-zinc-500">{[x.fahrzeug?.quelle, x.fahrzeug?.inserat_id].filter(Boolean).join(" · ") || "–"}</td>
              <td className="py-2 pr-3">
                <div className="flex flex-wrap gap-2">
                  {(x.links || []).map((l) => (
                    <a key={l.portal} href={l.url} target="_blank" rel="noopener noreferrer"
                       className="inline-flex items-center gap-1 text-xs underline">
                      {l.portal} <ExternalLink size={11} />
                    </a>
                  ))}
                  {!(x.links || []).length && <span className="text-xs text-zinc-500">keine ({(x.hinweise || [])[0] || "–"})</span>}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
