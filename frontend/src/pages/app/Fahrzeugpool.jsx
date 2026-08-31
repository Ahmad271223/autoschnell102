import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Copy, ExternalLink } from "lucide-react";

// Der beim Vergleich eingefuegte Inserats-Link wird automatisch am
// Fahrzeug gespeichert — je nach Quelle unter kleinanzeigen_url,
// detail_url oder url.
function inseratUrl(v) {
  const u = v?.data?.kleinanzeigen_url || v?.data?.detail_url || v?.data?.url;
  return typeof u === "string" && u.startsWith("http") ? u : null;
}

export default function Fahrzeugpool() {
  const [items, setItems] = useState([]);

  useEffect(() => {
    // Array-Guard: liefert der Dev-Proxy in einem Grenzfall etwas anderes
    // als die Liste (z.B. eine Fehlerseite), soll die Seite leer bleiben
    // statt mit "items.map is not a function" abzustuerzen.
    api.get("/vehicles")
      .then((r) => setItems(Array.isArray(r.data) ? r.data : []))
      .catch(() => setItems([]));
  }, []);

  const kopieren = async (url) => {
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Link kopiert");
    } catch {
      toast.error("Kopieren nicht möglich");
    }
  };

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-7xl mx-auto" data-testid="vehicles-page">
      <div className="overline">Fahrzeugpool</div>
      <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">Verglichene & geprüfte Fahrzeuge</h1>

      <div className="mt-6 tactical-card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left overline" style={{ background: "rgba(255,255,255,0.02)" }}>
              <th className="px-4 py-3">Mobile-ID</th>
              <th className="px-4 py-3">Marke / Modell</th>
              <th className="px-4 py-3">EZ</th>
              <th className="px-4 py-3">KM</th>
              <th className="px-4 py-3">Leistung</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Inserat-Link</th>
              <th className="px-4 py-3">Aktualisiert</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && (
              <tr><td colSpan={8} className="px-4 py-10 text-center text-zinc-500">Noch keine Fahrzeuge im Pool. Starte einen Vergleich.</td></tr>
            )}
            {items.map((v) => {
              const url = inseratUrl(v);
              return (
              <tr key={v.id} className="border-t" style={{ borderColor: "var(--border-default)" }} data-testid={`pool-${v.id}`}>
                <td className="px-4 py-3 font-mono text-xs text-zinc-400">{v.mobile_ad_id}</td>
                <td className="px-4 py-3">
                  <div className="font-semibold">{v.data?.make_label} {v.data?.model_label}</div>
                  <div className="text-xs text-zinc-500">{v.data?.model_description}</div>
                </td>
                <td className="px-4 py-3">{v.data?.first_registration || "—"}</td>
                <td className="px-4 py-3">{v.data?.mileage ? `${Number(v.data.mileage).toLocaleString("de-DE")} km` : "—"}</td>
                <td className="px-4 py-3">{v.data?.power_ps ? `${v.data.power_ps} PS` : "—"}</td>
                <td className="px-4 py-3">
                  <span className="text-xs px-2 py-0.5 rounded-sm border"
                        style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}>
                    {v.status || "verglichen"}
                  </span>
                </td>
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
