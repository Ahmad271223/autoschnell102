import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Copy, ExternalLink } from "lucide-react";
import StatusSchild from "@/components/StatusSchild";
import { lifecycleText } from "@/lib/fahrzeugStatus";

// N17: Altdaten mit Text ergaben "NaN km".
function kmText(n) {
  const zahl = typeof n === "number" ? n : Number(n);
  return Number.isFinite(zahl) && zahl > 0 ? `${zahl.toLocaleString("de-DE")} km` : "—";
}

// Der beim Vergleich eingefuegte Inserats-Link wird automatisch am
// Fahrzeug gespeichert — je nach Quelle unter kleinanzeigen_url,
// detail_url oder url.
function inseratUrl(v) {
  const u = v?.data?.kleinanzeigen_url || v?.data?.detail_url || v?.data?.url;
  return typeof u === "string" && u.startsWith("http") ? u : null;
}

// Entscheidung Ahmad 22.09.2026: Die Seite zeigt nur die 60 zuletzt
// bearbeiteten Fahrzeuge (der Pool selbst haelt bis zu 300 je Konto).
export const POOL_ANZEIGE_MAX = 60;

export function neuesteFahrzeuge(liste, max = POOL_ANZEIGE_MAX) {
  const zeit = (v) => Date.parse(v?.updated_at || v?.created_at || "") || 0;
  return [...(Array.isArray(liste) ? liste : [])]
    .sort((a, b) => zeit(b) - zeit(a))
    .slice(0, max);
}

export default function Fahrzeugpool() {
  const [items, setItems] = useState([]);
  const [gesamt, setGesamt] = useState(0);
  const [ladeFehler, setLadeFehler] = useState(false);

  useEffect(() => {
    // Array-Guard: liefert der Dev-Proxy in einem Grenzfall etwas anderes
    // als die Liste (z.B. eine Fehlerseite), soll die Seite leer bleiben
    // statt mit "items.map is not a function" abzustuerzen.
    // Runde 16 (15.09.2026): Ladefehler und Kuerzung (X-Truncated ab 500)
    // sichtbar machen statt "Noch keine Fahrzeuge".
    api.get("/vehicles")
      .then((r) => {
        const alle = Array.isArray(r.data) ? r.data : [];
        setGesamt(alle.length);
        setItems(neuesteFahrzeuge(alle));
      })
      .catch((e) => { setItems([]); setLadeFehler(true); toast.error(errMsg(e, "Fahrzeuge konnten nicht geladen werden")); });
  }, []);

  const kopieren = async (url) => {
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Link kopiert");
    } catch {
      toast.error("Kopieren nicht möglich");
    }
  };

  // Runde 16: der Chef sieht, welcher Sucher das Fahrzeug fuehrt; Sucher
  // bekommen nur eigene Fahrzeuge (kein owner_name im Datensatz).
  const mitBearbeiter = items.some((v) => "owner_name" in v);

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-7xl mx-auto" data-testid="vehicles-page">
      <div className="overline">Fahrzeugpool</div>
      <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">Verglichene & geprüfte Fahrzeuge</h1>

      <div className="mt-6 tactical-card overflow-x-auto">
        <table className="w-full text-sm min-w-[720px]">
          <thead>
            <tr className="text-left overline" style={{ background: "var(--wa-02)" }}>
              <th className="px-4 py-3">Mobile-ID</th>
              <th className="px-4 py-3">Marke / Modell</th>
              <th className="px-4 py-3">EZ</th>
              <th className="px-4 py-3">KM</th>
              <th className="px-4 py-3">Leistung</th>
              <th className="px-4 py-3">Status</th>
              {mitBearbeiter && <th className="px-4 py-3">Bearbeiter</th>}
              <th className="px-4 py-3">Inserat-Link</th>
              <th className="px-4 py-3">Aktualisiert</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr><td colSpan={mitBearbeiter ? 9 : 8} className="px-4 py-10 text-center text-zinc-500" data-testid="pool-leer">
                {ladeFehler ? "Fahrzeuge konnten nicht geladen werden — bitte neu laden." : "Noch keine Fahrzeuge im Pool. Starte einen Vergleich."}
              </td></tr>
            )}
            {gesamt > items.length && (
              <tr><td colSpan={mitBearbeiter ? 9 : 8} className="px-4 py-2 text-center text-xs text-zinc-500" data-testid="pool-gekuerzt">
                Die Liste zeigt die neuesten {POOL_ANZEIGE_MAX} von {gesamt} Fahrzeugen.
              </td></tr>
            )}
            {items.map((v) => {
              const url = inseratUrl(v);
              return (
              <tr key={v.id} className="border-t" style={{ borderColor: "var(--border-default)" }} data-testid={`pool-${v.id}`}>
                <td className="px-4 py-3 font-mono text-xs text-zinc-400">{v.mobile_ad_id}</td>
                <td className="px-4 py-3">
                  {/* Weg zur Fahrzeugakte — fuer Sucher ist dies die einzige
                      Fahrzeugliste (der Bestand ist Chefsache). */}
                  <Link to={`/app/akte/${v.id}`} className="font-semibold hover:underline underline-offset-2"
                        data-testid={`pool-akte-${v.id}`}>
                    {v.data?.make_label} {v.data?.model_label}
                  </Link>
                  <div className="text-xs text-zinc-500">{v.data?.model_description}</div>
                </td>
                <td className="px-4 py-3">{v.data?.first_registration || "—"}</td>
                <td className="px-4 py-3">{kmText(v.data?.mileage)}</td>
                <td className="px-4 py-3">{v.data?.power_ps ? `${v.data.power_ps} PS` : "—"}</td>
                <td className="px-4 py-3">
                  {/* Pruefbericht 20.09.2026 (U-40/H16): der aktuelle Lebenszyklus
                      statt des alten Freitext-Status — sonst stand ein laengst
                      abgeholtes Fahrzeug hier weiter auf "Vertrag erstellt". */}
                  <StatusSchild status={v.lifecycle || "verglichen"}
                                text={lifecycleText(v.lifecycle || "verglichen")} />
                </td>
                {mitBearbeiter && (
                  <td className="px-4 py-3 text-xs text-zinc-300" data-testid={`pool-owner-${v.id}`}>
                    {v.owner_name || <span className="text-zinc-600">—</span>}
                    {v.mitbearbeiter_namen?.length > 0 && (
                      <span className="text-zinc-500"> · mit {v.mitbearbeiter_namen.join(", ")}</span>
                    )}
                  </td>
                )}
                <td className="px-4 py-3">
                  {url ? (
                    <div className="inline-flex items-center gap-1">
                      <a href={url} target="_blank" rel="noopener noreferrer"
                         className="inline-flex items-center gap-1 text-xs text-zinc-300 hover:text-white underline underline-offset-2 decoration-zinc-600"
                         title={url}
                         data-testid={`pool-link-${v.id}`}>
                        <ExternalLink size={12} /> öffnen
                      </a>
                      <button type="button" onClick={() => kopieren(url)}
                              className="p-1.5 rounded-sm hover:bg-white/5 text-zinc-400 hover:text-white"
                              title="Link kopieren"
                              data-testid={`pool-copy-${v.id}`}>
                        <Copy size={12} />
                      </button>
                    </div>
                  ) : (
                    <span className="text-xs text-zinc-600">—</span>
                  )}
                </td>
                <td className="px-4 py-3 text-zinc-500 font-mono text-xs">{v.updated_at && new Date(v.updated_at).toLocaleString("de-DE")}</td>
              </tr>
            );})}
          </tbody>
        </table>
      </div>
    </div>
  );
}
