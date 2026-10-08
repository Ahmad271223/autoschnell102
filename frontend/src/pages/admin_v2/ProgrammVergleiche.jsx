import { useCallback, useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { Card, PageHeader, Spinner } from "./_ui";
import { VergleichsTabelle, VerbindungsListe, fahrzeugText, zeit } from "@/components/ProgrammVergleiche";

/**
 * Betreiber (Wunsch Ahmad 03.10.2026): wer hat wann welches Auto mit dem Programm verglichen,
 * welche PCs sind verbunden (trennen möglich). Name und Freigabe kommen vom Server.
 */
export default function AdminProgrammVergleiche() {
  const [daten, setDaten] = useState(null);
  const [downloads, setDownloads] = useState(null);
  const [q, setQ] = useState("");
  const laden = useCallback(async () => {
    try {
      const { data } = await api.get("/admin/werkzeug-vergleiche", { params: { limit: 500 } });
      setDaten(data);
    } catch (e) {
      toast.error(errMsg(e, "Fehler beim Laden"));
      setDaten({ vergleiche: [], verbindungen: [], gesamt: 0 });
    }
    // Wunsch Ahmad 06.10.2026: wer hat wann welches Programm heruntergeladen
    try {
      const { data } = await api.get("/admin/werkzeug-downloads", { params: { limit: 300 } });
      setDownloads(data.downloads || []);
    } catch (e) {
      setDownloads([]);
    }
  }, []);
  useEffect(() => { laden(); }, [laden]);

  const gefiltert = useMemo(() => {
    const s = q.trim().toLowerCase();
    const alle = daten?.vergleiche || [];
    if (!s) return alle;
    return alle.filter((x) => [x.name, x.konto, x.firma, x.kunden_nr, x.pc_name, fahrzeugText(x.fahrzeug),
      x.fahrzeug?.inserat_id, x.fahrzeug?.quelle].filter(Boolean).join(" ").toLowerCase().includes(s));
  }, [daten, q]);

  const trennen = async (v) => {
    try {
      await api.delete(`/admin/werkzeug-verbindungen/${encodeURIComponent(v.user_id)}`);
      toast.success("PC getrennt.");
      laden();
    } catch (e) {
      toast.error(errMsg(e, "Trennen hat nicht geklappt"));
    }
  };

  if (!daten) return <div className="p-6"><Spinner /></div>;
  return (
    <div data-testid="admin-programm-vergleiche">
      <PageHeader
        title={`Programm-Vergleiche${daten.name ? ` · ${daten.name}` : ""}`}
        subtitle={`${daten.gesamt} ${daten.gesamt === 1 ? "Vergleich" : "Vergleiche"} · verfügbar mit AutoSchnell Pro`}
      />
      <Card className="mb-4">
        <div className="text-sm font-semibold mb-2">Verbundene PCs</div>
        <VerbindungsListe verbindungen={daten.verbindungen} onTrennen={trennen} mitFirma />
      </Card>
      <Card className="mb-4">
        <div className="text-sm font-semibold mb-2">Downloads{downloads ? ` (${downloads.length})` : ""}</div>
        {!downloads ? <Spinner /> : !downloads.length
          ? <div className="text-sm text-zinc-500" data-testid="admin-pv-keine-downloads">Noch kein Download.</div>
          : (
            <div className="overflow-x-auto" data-testid="admin-pv-downloads">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-zinc-500 text-xs">
                    <th className="py-2 pr-3 font-medium">Zeit</th>
                    <th className="py-2 pr-3 font-medium">Programm</th>
                    <th className="py-2 pr-3 font-medium">Version</th>
                    <th className="py-2 pr-3 font-medium">Konto</th>
                    <th className="py-2 pr-3 font-medium">Firma</th>
                  </tr>
                </thead>
                <tbody>
                  {downloads.map((d) => (
                    <tr key={d.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                      <td className="py-2 pr-3 whitespace-nowrap">{zeit(d.am)}</td>
                      <td className="py-2 pr-3">{d.programm}</td>
                      <td className="py-2 pr-3 text-xs text-zinc-500">{d.version || "–"}</td>
                      <td className="py-2 pr-3">{d.name || "–"}{d.konto ? <div className="text-xs text-zinc-500">{d.konto}</div> : null}</td>
                      <td className="py-2 pr-3">{d.firma}{d.kunden_nr ? <div className="text-xs text-zinc-500">Kd.-Nr. {d.kunden_nr}</div> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
      </Card>
      <Card padded={false}>
        <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <Search size={16} className="text-zinc-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} data-testid="admin-pv-suche"
                 placeholder="Suche (Konto, Firma, Fahrzeug, PC, Inserat-ID)"
                 className="flex-1 bg-transparent border-0 outline-none text-[14px] placeholder:text-zinc-500" />
        </div>
        <div className="p-4">
          <VergleichsTabelle vergleiche={gefiltert} mitFirma />
        </div>
      </Card>
    </div>
  );
}
