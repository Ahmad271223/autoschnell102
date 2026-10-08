import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { Card, PageHeader, Spinner } from "./_ui";
import { VergleichsTabelle, VerbindungsListe, zeit } from "@/components/ProgrammVergleiche";

/**
 * Betreiber (Wunsch Ahmad 03.10.2026): wer hat wann welches Auto mit dem Programm verglichen,
 * welche PCs sind verbunden (trennen möglich). Name und Freigabe kommen vom Server.
 *
 * Wunsch Ahmad 08.10.2026: nicht alles untereinander — Blöcke zu 1.000 (Block 1 = die neuesten 1.000), darin
 * Seiten zu 100, und je Block die 20 meistverglichenen Modelle. Die Suche läuft auf dem Server im gewählten Block.
 */
const zahl = (n) => Number(n || 0).toLocaleString("de-DE");
// Pruefung 08.10.2026: auch die Browser-Erweiterung zeigen und trennen koennen (vorher nur das Windows-Programm).
// Das Programm ist der Standard des Servers — sein Kennname steht bewusst nirgends in der Oberflaeche.
const WERKZEUGE = [{ id: "", text: "Windows-Programm" }, { id: "browser-helfer", text: "Browser-Erweiterung" }];

export default function AdminProgrammVergleiche() {
  const [daten, setDaten] = useState(null);
  const [werkzeug, setWerkzeug] = useState("");
  const [downloads, setDownloads] = useState(null);
  const [block, setBlock] = useState(1);
  const [seite, setSeite] = useState(1);
  const [eingabe, setEingabe] = useState("");
  const [q, setQ] = useState("");
  const [laedt, setLaedt] = useState(false);
  const anfrageRef = useRef(0);

  // Suche erst nach einer kurzen Tipp-Pause (sonst eine Anfrage je Buchstabe); neue Suche = Seite 1
  useEffect(() => {
    const t = setTimeout(() => { setQ(eingabe.trim()); setSeite(1); }, 400);
    return () => clearTimeout(t);
  }, [eingabe]);

  const laden = useCallback(async () => {
    const nr = ++anfrageRef.current;
    setLaedt(true);
    try {
      const { data } = await api.get("/admin/werkzeug-vergleiche",
                                     { params: { block, seite, ...(q ? { q } : {}), ...(werkzeug ? { werkzeug } : {}) } });
      if (nr === anfrageRef.current) setDaten(data);       // nur die Antwort auf die letzte Anfrage zeigen
    } catch (e) {
      if (nr === anfrageRef.current) {
        toast.error(errMsg(e, "Fehler beim Laden"));
        setDaten((d) => d || { vergleiche: [], verbindungen: [], gesamt: 0, top_modelle: [] });
      }
    } finally {
      if (nr === anfrageRef.current) setLaedt(false);
    }
  }, [block, seite, q, werkzeug]);
  useEffect(() => { laden(); }, [laden]);

  // Wunsch Ahmad 06.10.2026: wer hat wann welches Programm heruntergeladen
  useEffect(() => {
    api.get("/admin/werkzeug-downloads", { params: { limit: 300 } })
      .then(({ data }) => setDownloads(data.downloads || []))
      .catch(() => setDownloads([]));
  }, []);

  const trennen = async (v) => {
    try {
      await api.delete(`/admin/werkzeug-verbindungen/${encodeURIComponent(v.user_id)}`,
                       werkzeug ? { params: { werkzeug } } : undefined);
      toast.success(werkzeug ? "Browser getrennt." : "PC getrennt.");
      laden();
    } catch (e) {
      toast.error(errMsg(e, "Trennen hat nicht geklappt"));
    }
  };

  if (!daten) return <div className="p-6"><Spinner /></div>;
  const bloecke = daten.bloecke || 1;
  const seiten = daten.seiten || 1;
  const aktSeite = daten.seite || 1;
  const aktBlock = daten.block || 1;
  const top = daten.top_modelle || [];
  const groesstes = top[0]?.anzahl || 1;
  return (
    <div data-testid="admin-programm-vergleiche">
      <PageHeader
        title={`Programm-Vergleiche${daten.name ? ` · ${daten.name}` : ""}`}
        subtitle={`${zahl(daten.gesamt)} ${daten.gesamt === 1 ? "Vergleich" : "Vergleiche"} · freigegeben für Kd.-Nr. ${(daten.freigegeben_fuer || []).join(", ") || "niemand"}`}
      />
      <div className="mb-4 flex flex-wrap gap-2" data-testid="admin-pv-werkzeug">
        {WERKZEUGE.map((w) => (
          <button key={w.id || "programm"} type="button" aria-pressed={w.id === werkzeug}
                  data-testid={`admin-pv-werkzeug-${w.id || "programm"}`}
                  onClick={() => { setWerkzeug(w.id); setBlock(1); setSeite(1); }}
                  className={`apple-btn ${w.id === werkzeug ? "apple-btn-primary" : "apple-btn-secondary"} !rounded-full !px-3 !py-1 text-xs`}>
            {w.text}
          </button>
        ))}
      </div>
      <Card className="mb-4">
        <div className="text-sm font-semibold mb-2">{werkzeug ? "Verbundene Browser" : "Verbundene PCs"}</div>
        <VerbindungsListe verbindungen={daten.verbindungen} onTrennen={trennen} mitFirma />
      </Card>
      <Card className="mb-4">
        <div className="text-sm font-semibold mb-2">Downloads{downloads ? ` (${downloads.length})` : ""}</div>
        {!downloads ? <Spinner /> : !downloads.length
          ? <div className="text-sm text-zinc-500" data-testid="admin-pv-keine-downloads">Noch kein Download.</div>
          : (
            <div className="overflow-x-auto max-h-80 overflow-y-auto" data-testid="admin-pv-downloads">
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

      {bloecke > 1 && (
        <div className="mb-4 flex flex-wrap items-center gap-2" data-testid="admin-pv-bloecke">
          <span className="text-xs text-zinc-500 mr-1">Block:</span>
          {Array.from({ length: bloecke }, (_, i) => i + 1).map((b) => (
            <button key={b} type="button" onClick={() => { setBlock(b); setSeite(1); }}
                    data-testid={`admin-pv-block-${b}`} aria-pressed={b === aktBlock}
                    className={`apple-btn ${b === aktBlock ? "apple-btn-primary" : "apple-btn-secondary"} !rounded-full !px-3 !py-1 text-xs`}>
              {zahl((b - 1) * (daten.je_block || 1000) + 1)}–{zahl(Math.min(b * (daten.je_block || 1000), daten.gesamt))}
            </button>
          ))}
        </div>
      )}

      <Card className="mb-4" data-testid="admin-pv-top">
        <div className="text-sm font-semibold">
          Die {top.length || 20} meistverglichenen Modelle
          <span className="font-normal text-zinc-500"> · Vergleiche {zahl(daten.block_von)}–{zahl(daten.block_bis)}
            {daten.top_basis != null ? ` (${zahl(daten.top_basis)} gezählt, ohne Probeläufe)` : ""}</span>
        </div>
        {!top.length ? <div className="text-sm text-zinc-500 mt-2">Noch keine Vergleiche.</div> : (
          <ol className="mt-3 grid gap-x-6 gap-y-1.5" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 22rem), 1fr))" }}>
            {top.map((t, i) => (
              <li key={t.modell} className="flex items-center gap-2 text-sm" data-testid={`admin-pv-top-${i + 1}`}>
                <span className="w-6 text-right text-xs text-zinc-500 tabular-nums">{i + 1}.</span>
                <span className="flex-1 min-w-0 truncate">{t.modell}</span>
                <span className="w-24 h-1.5 rounded-full overflow-hidden shrink-0" style={{ background: "var(--wa-08)" }}>
                  <span className="block h-full rounded-full" style={{ width: `${Math.max(4, (t.anzahl / groesstes) * 100)}%`, background: "var(--accent-red)" }} />
                </span>
                <span className="w-24 text-right tabular-nums text-xs">
                  {zahl(t.anzahl)}
                  <span className="text-zinc-500"> · {(daten.top_basis ? t.anzahl / daten.top_basis * 100 : 0)
                    .toLocaleString("de-DE", { maximumFractionDigits: 1 })} %</span>
                </span>
              </li>
            ))}
          </ol>
        )}
      </Card>

      <Card padded={false}>
        <div className="px-4 py-3 flex items-center gap-2" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <Search size={16} className="text-zinc-500" />
          <input value={eingabe} onChange={(e) => setEingabe(e.target.value)} data-testid="admin-pv-suche"
                 placeholder="Suche im gewählten Block (Konto, Firma, Fahrzeug, PC, Inserat-ID)"
                 className="flex-1 bg-transparent border-0 outline-none text-[14px] placeholder:text-zinc-500" />
          {laedt && <Spinner size={14} />}
        </div>
        <div className="p-4 space-y-3">
          <Seitenleiste seite={aktSeite} seiten={seiten} treffer={daten.treffer} suche={q} onSeite={setSeite} />
          <VergleichsTabelle vergleiche={daten.vergleiche || []} mitFirma />
          {seiten > 1 && <Seitenleiste seite={aktSeite} seiten={seiten} treffer={daten.treffer} suche={q} onSeite={setSeite} unten />}
        </div>
      </Card>
    </div>
  );
}

function Seitenleiste({ seite, seiten, treffer, suche, onSeite, unten = false }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs" data-testid={unten ? "admin-pv-seiten-unten" : "admin-pv-seiten"}>
      <button type="button" onClick={() => onSeite(seite - 1)} disabled={seite <= 1} aria-label="Vorige Seite"
              className="apple-btn apple-btn-secondary !rounded-full !px-2 !py-1 disabled:opacity-40">
        <ChevronLeft size={14} />
      </button>
      {Array.from({ length: seiten }, (_, i) => i + 1).map((s) => (
        <button key={s} type="button" onClick={() => onSeite(s)} aria-pressed={s === seite}
                data-testid={unten ? undefined : `admin-pv-seite-${s}`}
                className={`apple-btn ${s === seite ? "apple-btn-primary" : "apple-btn-secondary"} !rounded-full !px-2.5 !py-1 tabular-nums`}>
          {s}
        </button>
      ))}
      <button type="button" onClick={() => onSeite(seite + 1)} disabled={seite >= seiten} aria-label="Nächste Seite"
              className="apple-btn apple-btn-secondary !rounded-full !px-2 !py-1 disabled:opacity-40">
        <ChevronRight size={14} />
      </button>
      <span className="text-zinc-500 ml-1">
        Seite {seite} von {seiten} · {zahl(treffer)} {suche ? "Treffer" : "Vergleiche"} in diesem Block, je Seite 100
      </span>
    </div>
  );
}
