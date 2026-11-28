import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { Card, PageHeader, Spinner } from "./_ui";
import { blobOeffnen } from "@/lib/dateiOeffnen";
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
  // Wunsch Ahmad 09.10.2026: Bilder der Anzeigen, die das Programm nicht erkannt hat (Lesebilder)
  const [lesebilder, setLesebilder] = useState(null);
  // Pruefung 09.10.2026 (Befund Mokka-e): Inseratsseiten, die die Erweiterung nicht lesen konnte (Leseseiten)
  const [leseseiten, setLeseseiten] = useState(null);
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

  const lesebilderLaden = useCallback(() => {
    api.get("/admin/werkzeug-lesebilder", { params: { limit: 60 } })
      .then(({ data }) => setLesebilder(data))
      .catch(() => setLesebilder({ lesebilder: [], gesamt: 0 }));
  }, []);
  useEffect(() => { if (!werkzeug) lesebilderLaden(); }, [werkzeug, lesebilderLaden]);

  const lesebildOeffnen = async (b) => {
    const startMs = Date.now();
    try {
      const r = await api.get(`/admin/werkzeug-lesebilder/${encodeURIComponent(b.id)}/bild`, { responseType: "blob" });
      blobOeffnen(r.data, { startMs, titel: "Das Lesebild", mime: "image/png", dateiname: `lesebild-${b.id}.png` });
    } catch (e) {
      toast.error(errMsg(e, "Das Bild ließ sich nicht öffnen"));
    }
  };
  const lesebildLoeschen = async (b) => {
    try {
      await api.delete(`/admin/werkzeug-lesebilder/${encodeURIComponent(b.id)}`);
      toast.success("Lesebild gelöscht.");
      lesebilderLaden();
    } catch (e) {
      toast.error(errMsg(e, "Löschen hat nicht geklappt"));
    }
  };

  const leseseitenLaden = useCallback(() => {
    api.get("/admin/werkzeug-leseseiten", { params: { limit: 60 } })
      .then(({ data }) => setLeseseiten(data))
      .catch(() => setLeseseiten({ leseseiten: [], gesamt: 0 }));
  }, []);
  useEffect(() => { if (werkzeug === "browser-helfer") leseseitenLaden(); }, [werkzeug, leseseitenLaden]);

  const leseseiteLaden = async (s) => {
    try {
      const r = await api.get(`/admin/werkzeug-leseseiten/${encodeURIComponent(s.id)}/seite`, { responseType: "blob" });
      // nie im Browser anzeigen (fremde Seite, fremde Skripte) — nur als Textdatei speichern
      const url = URL.createObjectURL(new Blob([r.data], { type: "text/plain" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = `leseseite-${s.id}.html.txt`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) {
      toast.error(errMsg(e, "Die Seite ließ sich nicht laden"));
    }
  };
  const leseseiteLoeschen = async (s) => {
    try {
      await api.delete(`/admin/werkzeug-leseseiten/${encodeURIComponent(s.id)}`);
      toast.success("Seite gelöscht.");
      leseseitenLaden();
    } catch (e) {
      toast.error(errMsg(e, "Löschen hat nicht geklappt"));
    }
  };

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

      {!werkzeug && (
        <Card className="mb-4" data-testid="admin-pv-lesebilder">
          <div className="text-sm font-semibold">
            Nicht erkannt – Bilder der Anzeige{lesebilder ? ` (${zahl(lesebilder.gesamt)})` : ""}
            <span className="font-normal text-zinc-500"> · schickt das Programm ab 1.5.13 von selbst, {lesebilder?.tage || 30} Tage</span>
          </div>
          {!lesebilder ? <Spinner /> : !lesebilder.lesebilder.length
            ? <div className="text-sm text-zinc-500 mt-2" data-testid="admin-pv-keine-lesebilder">Keine nicht erkannten Anzeigen.</div>
            : (
              <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 19rem), 1fr))" }}>
                {lesebilder.lesebilder.map((b) => (
                  <div key={b.id} className="rounded-lg border p-2 text-xs" style={{ borderColor: "var(--border-default)" }}
                       data-testid={`admin-pv-lesebild-${b.id}`}>
                    <button type="button" onClick={() => lesebildOeffnen(b)} className="block w-full" title="In voller Größe öffnen">
                      <img src={`data:image/jpeg;base64,${b.vorschau_b64}`} alt={b.grund_text}
                           className="w-full rounded" style={{ maxHeight: "11rem", objectFit: "contain", background: "var(--wa-08)" }} />
                    </button>
                    <div className="mt-1.5 font-medium" style={{ color: "var(--accent-red)" }}>{b.grund_text}</div>
                    {!!(b.fehlt || []).length && <div className="text-zinc-500">fehlt: {b.fehlt.join(", ")}</div>}
                    <div className="text-zinc-500">{zeit(b.erstellt_am)} · {b.name || "–"} ({b.konto || "–"}) · {b.firma}{b.kunden_nr ? ` · Kd.-Nr. ${b.kunden_nr}` : ""}</div>
                    {b.fahrzeug?.marke_modell_text && <div>Gelesen: „{b.fahrzeug.marke_modell_text}“{b.fahrzeug.inserat_id ? ` · ID ${b.fahrzeug.inserat_id}` : ""}</div>}
                    {b.rohtext && <div className="text-zinc-500 truncate" title={b.rohtext}>{b.rohtext.slice(0, 120)}</div>}
                    <div className="mt-1.5 flex gap-2">
                      <button type="button" onClick={() => lesebildOeffnen(b)} className="apple-btn apple-btn-secondary !rounded-full !px-2.5 !py-0.5 text-xs">Groß öffnen</button>
                      <button type="button" onClick={() => lesebildLoeschen(b)} className="apple-btn apple-btn-secondary !rounded-full !px-2.5 !py-0.5 text-xs"
                              data-testid={`admin-pv-lesebild-loeschen-${b.id}`}>Löschen</button>
                    </div>
                  </div>
                ))}
              </div>
            )}
        </Card>
      )}

      {werkzeug === "browser-helfer" && (
        <Card className="mb-4" data-testid="admin-pv-leseseiten">
          <div className="text-sm font-semibold">
            Nicht lesbar – Inseratsseiten{leseseiten ? ` (${zahl(leseseiten.gesamt)})` : ""}
            <span className="font-normal text-zinc-500"> · Seiten, auf denen die Erweiterung keine Inseratsdaten fand, {leseseiten?.tage || 14} Tage</span>
          </div>
          {!leseseiten ? <Spinner /> : !leseseiten.leseseiten.length
            ? <div className="text-sm text-zinc-500 mt-2" data-testid="admin-pv-keine-leseseiten">Keine nicht lesbaren Seiten.</div>
            : (
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-zinc-500">
                      <th className="py-1 pr-3">Wann</th><th className="py-1 pr-3">Inserat</th><th className="py-1 pr-3">Grund</th>
                      <th className="py-1 pr-3">Konto</th><th className="py-1 pr-3">Firma</th><th className="py-1"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {leseseiten.leseseiten.map((s) => (
                      <tr key={s.id} className="border-t" style={{ borderColor: "var(--border-default)" }}
                          data-testid={`admin-pv-leseseite-${s.id}`}>
                        <td className="py-2 pr-3 text-xs text-zinc-500 whitespace-nowrap">{zeit(s.erstellt_am)}</td>
                        <td className="py-2 pr-3">
                          <a href={s.url} target="_blank" rel="noreferrer" className="underline break-all">{s.portal || "–"} {s.item_id || ""}</a>
                        </td>
                        <td className="py-2 pr-3 text-xs">{s.grund}</td>
                        <td className="py-2 pr-3">{s.name || "–"}{s.konto ? <div className="text-xs text-zinc-500">{s.konto}</div> : null}</td>
                        <td className="py-2 pr-3">{s.firma}{s.kunden_nr ? <div className="text-xs text-zinc-500">Kd.-Nr. {s.kunden_nr}</div> : null}</td>
                        <td className="py-2 whitespace-nowrap">
                          <button type="button" onClick={() => leseseiteLaden(s)}
                                  className="apple-btn apple-btn-secondary !rounded-full !px-2.5 !py-0.5 text-xs mr-1">Seite herunterladen</button>
                          <button type="button" onClick={() => leseseiteLoeschen(s)}
                                  className="apple-btn apple-btn-secondary !rounded-full !px-2.5 !py-0.5 text-xs"
                                  data-testid={`admin-pv-leseseite-loeschen-${s.id}`}>Löschen</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </Card>
      )}

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
