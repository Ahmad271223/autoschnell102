import { useCallback, useEffect, useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { Download, FileSignature, KeyRound, Monitor, MonitorX } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { startseite } from "@/lib/rollen";
import { groesseText, useProgramme } from "@/lib/programme";
import { VergleichsTabelle, VerbindungsListe, fahrzeugText, zeit } from "@/components/ProgrammVergleiche";

/**
 * Programme zum Herunterladen (03.10.2026). Was hier steht, liefert der Server —
 * nur für Firmen, die ein Programm freigeschaltet haben. Alle anderen landen auf
 * ihrer Startseite, als gäbe es die Seite nicht.
 *
 * Lizenz (Wunsch Ahmad 03.10.2026 nachmittags): Das Programm arbeitet nur verbunden —
 * hier gibt es den 6-stelligen Code dafür (10 Minuten, einmal, nur mit aktivem Abo).
 * Ein Konto = ein PC. Der Chef sieht, wer aus seiner Firma wann welches Auto verglichen hat.
 *
 * Wunsch Ahmad 04.10.2026: bei zwei Programmen links das erste, rechts das zweite (nicht ewig
 * runterscrollen). Spalten nach Platz, nicht nach Fensterbreite — ist es zu schmal (Handy,
 * Seitenleiste offen), stehen sie wie bisher untereinander.
 */
const SPALTEN = "repeat(auto-fit, minmax(min(100%, 26rem), 1fr))";

export default function Programme() {
  const { user } = useAuth();
  const liste = useProgramme(Boolean(user) && (user.role === "dealer" || user.role === "sucher"), user?.id);
  const [laedt, setLaedt] = useState("");
  const [codes, setCodes] = useState({});
  const [getrennt, setGetrennt] = useState({});

  if (liste === null) return <div className="text-sm text-zinc-500">Lade…</div>;
  if (liste.length === 0) return <Navigate to={startseite(user)} replace />;

  const herunterladen = async (p) => {
    setLaedt(p.id);
    try {
      const { data } = await api.get(`/werkzeuge/${encodeURIComponent(p.id)}/download`, { responseType: "blob" });
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = p.dateiname || "programm.exe";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      toast.error("Herunterladen hat nicht geklappt – bitte später noch einmal versuchen.");
    } finally {
      setLaedt("");
    }
  };

  const codeHolen = async (p) => {
    try {
      const { data } = await api.post(`/werkzeuge/${encodeURIComponent(p.id)}/code`);
      setCodes((c) => ({ ...c, [p.id]: data }));
    } catch (e) {
      toast.error(e?.response?.status === 402
        ? "Kein aktives Abo – ohne Abo funktioniert das Programm nicht."
        : errMsg(e, "Code konnte nicht erzeugt werden"));
    }
  };

  const trennen = async (p) => {
    try {
      await api.delete(`/werkzeuge/${encodeURIComponent(p.id)}/verbindung`);
      setGetrennt((g) => ({ ...g, [p.id]: true }));
      toast.success(p.art === "browser"
        ? "Browser getrennt – die Erweiterung dort öffnet nichts mehr."
        : "PC getrennt – das Programm dort öffnet nichts mehr.");
    } catch (e) {
      toast.error(errMsg(e, "Trennen hat nicht geklappt"));
    }
  };

  const nebeneinander = liste.length > 1;
  return (
    <div className={`p-3 sm:p-6 lg:p-8 mx-auto ${nebeneinander ? "max-w-[1600px] grid gap-5" : "max-w-4xl"}`}
         style={nebeneinander ? { gridTemplateColumns: SPALTEN } : undefined}
         data-testid="programme-seite">
      {liste.map((p) => {
        const verbindung = getrennt[p.id] ? null : p.verbindung;
        const code = codes[p.id];
        // 04.10.2026: Programm (Windows, ein PC je Konto) oder Erweiterung (Chrome/Edge, ein Browser je Konto)
        const browser = p.art === "browser";
        const geraet = p.geraet || "PC";
        return (
          <section key={p.id} className={`space-y-5 min-w-0 ${nebeneinander ? "rounded-2xl p-4 sm:p-5" : ""}`}
                   style={nebeneinander ? { border: "1px solid var(--border-default)" } : undefined}
                   data-testid={`programm-${p.id}`}>
            <div className="flex items-start gap-3">
              <span className="w-10 h-10 rounded-md flex items-center justify-center shrink-0"
                    style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)" }}>
                <Monitor size={20} />
              </span>
              <div>
                <h1 className="font-display font-black text-2xl tracking-tight">{p.name}</h1>
                {p.beschreibung && <p className="text-sm text-zinc-500 mt-1">{p.beschreibung}</p>}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={() => herunterladen(p)}
                disabled={!p.vorhanden || laedt === p.id}
                data-testid={`programm-download-${p.id}`}
                className="apple-btn apple-btn-primary !rounded-full !px-5 !py-2.5 text-sm inline-flex items-center gap-2 disabled:opacity-50"
              >
                <Download size={16} />
                {laedt === p.id ? "Wird geladen…" : browser ? "Für Chrome und Edge herunterladen (ZIP)" : "Für Windows herunterladen"}
              </button>
              <span className="text-xs text-zinc-500" data-testid={`programm-info-${p.id}`}>
                {p.vorhanden
                  ? [p.version && `Version ${p.version}`, groesseText(p.groesse)].filter(Boolean).join(" · ")
                  : "Wird gerade bereitgestellt – bitte später noch einmal vorbeischauen."}
              </span>
            </div>

            <div className="rounded-xl p-4 space-y-3" style={{ border: "1px solid var(--border-default)", background: "var(--bg-surface)" }}
                 data-testid={`programm-verbindung-${p.id}`}>
              <div className="font-semibold text-sm">{browser ? "Erweiterung verbinden" : "Programm verbinden"}</div>
              <div className="text-sm text-zinc-500" data-testid={`programm-pc-${p.id}`}>
                {verbindung
                  ? <>Verbunden mit {geraet} „{verbindung.pc_name || "unbekannt"}“ · seit {zeit(verbindung.verbunden_am)} · zuletzt aktiv {zeit(verbindung.zuletzt_am)}</>
                  : `Noch kein ${geraet} verbunden. Jedes Konto kann ${browser ? "in" : "auf"} einem ${geraet} verbunden sein – ein neuer ${geraet} ersetzt den alten.`}
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" onClick={() => codeHolen(p)} data-testid={`programm-code-${p.id}`}
                        className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2">
                  <KeyRound size={15} /> {verbindung ? `Anderen ${geraet} verbinden` : "Code zum Verbinden anzeigen"}
                </button>
                {verbindung && (
                  <button type="button" onClick={() => trennen(p)} data-testid={`programm-trennen-${p.id}`}
                          className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2">
                    <MonitorX size={15} /> {geraet} trennen
                  </button>
                )}
              </div>
              {code && (
                <div data-testid={`programm-code-anzeige-${p.id}`}>
                  <div className="font-mono font-black text-3xl tracking-[0.3em]">{code.code.slice(0, 3)} {code.code.slice(3)}</div>
                  <div className="text-xs text-zinc-500 mt-1">
                    {browser ? "In der Erweiterung eintippen (Symbol in der Browserleiste)" : "Im Programm eintippen"} · gültig bis {zeit(code.gueltig_bis).split(", ")[1] || zeit(code.gueltig_bis)} Uhr ({code.minuten} Minuten) · nur einmal
                  </div>
                </div>
              )}
            </div>

            {Array.isArray(p.schritte) && p.schritte.length > 0 && (
              <ol className="list-decimal pl-5 space-y-2 text-sm">
                {p.schritte.map((s) => <li key={s}>{s}</li>)}
              </ol>
            )}

            <MeineAutos programm={p} />

            {p.chef && <FirmenUebersicht programm={p} />}
          </section>
        );
      })}
    </div>
  );
}

// Wunsch Ahmad 03.10.2026: "nur die letzten 30 anzeigen, nicht alle immer"
const FIRMA_ANZAHL = 30;

/** Chef: wer aus der Firma hat wann welches Auto verglichen, welche PCs sind verbunden. */
function FirmenUebersicht({ programm }) {
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");
  const laden = useCallback(async () => {
    try {
      const { data } = await api.get(`/werkzeuge/${encodeURIComponent(programm.id)}/firma`, { params: { limit: FIRMA_ANZAHL } });
      setDaten(data);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "Übersicht konnte nicht geladen werden"));
    }
  }, [programm.id]);
  useEffect(() => { laden(); }, [laden]);

  const trennen = async (v) => {
    try {
      await api.delete(`/werkzeuge/${encodeURIComponent(programm.id)}/verbindungen/${encodeURIComponent(v.user_id)}`);
      toast.success("PC getrennt.");
      laden();
    } catch (e) {
      toast.error(errMsg(e, "Trennen hat nicht geklappt"));
    }
  };

  return (
    <div className="space-y-4" data-testid="programm-firma">
      <h2 className="font-display font-black text-lg tracking-tight">Eure Firma</h2>
      {fehler && <div className="text-sm text-red-400">{fehler}</div>}
      {!daten && !fehler && <div className="text-sm text-zinc-500">Lade…</div>}
      {daten && (
        <>
          <div>
            <div className="text-sm font-semibold mb-2">Verbundene PCs</div>
            <VerbindungsListe verbindungen={daten.verbindungen} onTrennen={trennen} />
          </div>
          <div>
            <div className="text-sm font-semibold mb-2" data-testid="programm-firma-titel">
              {daten.gesamt > (daten.vergleiche?.length || 0)
                ? `Letzte ${daten.vergleiche.length} Vergleiche (von ${daten.gesamt})`
                : `Vergleiche (${daten.gesamt})`}
            </div>
            <VergleichsTabelle vergleiche={daten.vergleiche} />
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Die zuletzt im Programm angeklickten Autos (Wunsch Ahmad 03.10.2026): Der Server hat sie beim
 * Anklicken schon ausgelesen — „Für Kaufvertrag öffnen“ öffnet den Vergleich sofort mit Fotos.
 * Ohne Inserat-Adresse (Kennung nicht vollständig lesbar) muss der Link von Hand rein.
 */
function MeineAutos({ programm }) {
  const [liste, setListe] = useState(null);
  useEffect(() => {
    let aktiv = true;
    api.get(`/werkzeuge/${encodeURIComponent(programm.id)}/meine`, { params: { limit: 15 } })
      .then(({ data }) => { if (aktiv) setListe(Array.isArray(data?.vergleiche) ? data.vergleiche : []); })
      .catch(() => { if (aktiv) setListe([]); });
    return () => { aktiv = false; };
  }, [programm.id]);
  if (!liste || liste.length === 0) return null;
  return (
    <div className="space-y-2" data-testid="programm-meine">
      <h2 className="font-display font-black text-lg tracking-tight">Deine letzten Autos</h2>
      <p className="text-sm text-zinc-500">
        {programm.art === "browser"
          ? "Beim Öffnen im Browser liest AutoSchnell das Inserat schon aus – für den Kaufvertrag einfach öffnen (24 Stunden ohne neuen Abruf)."
          : "Beim Anklicken im Programm liest AutoSchnell das Inserat schon aus – für den Kaufvertrag einfach öffnen."}
      </p>
      <ul className="divide-y" style={{ borderColor: "var(--border-default)" }}>
        {liste.map((x) => {
          const url = x.fahrzeug?.inserat_url;
          return (
            <li key={x.id} className="py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
              <span className="text-zinc-500 whitespace-nowrap">{zeit(x.erstellt_am)}</span>
              <span className="flex-1 min-w-[12rem]">{fahrzeugText(x.fahrzeug)}</span>
              {url ? (
                <Link to={`/app/vergleich?url=${encodeURIComponent(url)}`} data-testid={`meine-vertrag-${x.id}`}
                      className="apple-btn apple-btn-secondary !rounded-full !px-3 !py-1 text-xs inline-flex items-center gap-1">
                  <FileSignature size={13} /> Für Kaufvertrag öffnen
                </Link>
              ) : (
                <span className="text-xs text-amber-500" data-testid={`meine-ohne-link-${x.id}`}>
                  Inserat-Adresse fehlt – bitte selbst kopieren und unter „Vergleich“ einfügen
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
