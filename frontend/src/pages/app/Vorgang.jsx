import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ExternalLink, FileSignature, Monitor } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { fahrzeugText, sichererLink } from "@/components/ProgrammVergleiche";

/**
 * Vorgangsseite (Wunsch Ahmad 08.10.2026, Vorgangsnummer). Das Windows-Programm (ab 1.5.8) öffnet bei Konten mit
 * Browser-Erweiterung nur /app/vorgang/<id>. Ist die Erweiterung in diesem Browser aktiv, übernimmt sie den Vorgang,
 * bevor diese Seite überhaupt lädt (Vergleiche + Inserat öffnen, der Tab wird zum Inserat). Steht die Seite doch da,
 * fehlt die Erweiterung hier (oder sie ist nicht verbunden) — dann öffnet das Programm die Vergleiche nach wenigen
 * Sekunden selbst. Diese Seite sagt das und bietet die Links zum selbst Öffnen und den Kaufvertrag an.
 */
export { sichererLink };

export default function Vorgang() {
  const { id } = useParams();
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");
  // content.js der Erweiterung meldet, ob sie übernommen hat (bleibt die Seite stehen: App-Fenster)
  const [erweiterung, setErweiterung] = useState(null);

  useEffect(() => {
    let aktiv = true;
    api.get(`/werkzeuge/vorgang/${encodeURIComponent(id || "")}`)
      .then((r) => { if (aktiv) setDaten(r.data); })
      .catch((e) => {
        if (aktiv) setFehler(e?.response?.status === 404 ? "Diesen Vorgang gibt es nicht (mehr)." : errMsg(e));
      });
    return () => { aktiv = false; };
  }, [id]);

  useEffect(() => {
    const hoeren = (ev) => {
      const d = ev.data;
      if (ev.source !== window || !d || d.__autoschnell !== true || d.type !== "VORGANG_ERGEBNIS" || d.id !== id) return;
      setErweiterung(!!d.ok);
    };
    window.addEventListener("message", hoeren);
    return () => window.removeEventListener("message", hoeren);
  }, [id]);

  const links = (daten?.links || []).map((l) => ({ ...l, url: sichererLink(l.url) })).filter((l) => l.url);
  const inserat = sichererLink(daten?.inserat_url);
  const uebernommen = erweiterung === true || daten?.uebernommen;

  return (
    <div className="max-w-2xl mx-auto px-4 sm:px-0 py-6 space-y-5" data-testid="vorgang-seite">
      <div className="flex items-start gap-3">
        <span className="w-10 h-10 rounded-md flex items-center justify-center shrink-0"
              style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)" }}>
          <Monitor size={20} />
        </span>
        <div className="min-w-0">
          <h1 className="font-display font-black text-2xl tracking-tight">Auto aus dem Vergleichs-Programm</h1>
          {daten && (
            <p className="text-sm text-zinc-500 mt-1 break-words" data-testid="vorgang-fahrzeug">
              {fahrzeugText(daten.fahrzeug) || "Fahrzeug"}
            </p>
          )}
        </div>
      </div>

      {fehler && <div className="text-sm" style={{ color: "var(--accent-red)" }} data-testid="vorgang-fehler">{fehler}</div>}

      {daten && (
        <>
          <div className="rounded-xl p-4 text-sm" data-testid="vorgang-hinweis"
               style={{ border: "1px solid var(--border-default)", background: "var(--bg-surface)" }}>
            {uebernommen
              ? "Die Browser-Erweiterung hat die Vergleiche und das Inserat geöffnet."
              : daten.programm_selbst
                ? "Das Vergleichs-Programm hat die Vergleiche direkt geöffnet – die Browser-Erweiterung hat in diesem "
                  + "Browser nicht übernommen (nicht installiert, abgeschaltet oder nicht verbunden). Diese Seite kann "
                  + "geschlossen werden."
              : "Die Browser-Erweiterung ist in diesem Browser nicht aktiv (nicht installiert oder nicht verbunden). "
                + "Das Vergleichs-Programm öffnet die Vergleiche deshalb gleich selbst – ohne Ampel. "
                + "Für Ampel und Kaufvertrag mit einem Klick: die Erweiterung in dem Browser installieren und verbinden, "
                + "den das Programm öffnet."}
          </div>

          <div className="flex flex-wrap gap-3">
            {links.map((l) => (
              <a key={l.portal + l.url} href={l.url} target="_blank" rel="noopener noreferrer"
                 data-testid={`vorgang-link-${l.portal}`}
                 className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2">
                <ExternalLink size={15} /> {l.portal}-Vergleich öffnen
              </a>
            ))}
            {inserat && (
              <a href={inserat} target="_blank" rel="noopener noreferrer" data-testid="vorgang-inserat"
                 className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2">
                <ExternalLink size={15} /> Inserat öffnen
              </a>
            )}
            {inserat && (
              <Link to={`/app/vergleich?url=${encodeURIComponent(inserat)}&vertrag=1`} data-testid="vorgang-vertrag"
                    className="apple-btn apple-btn-primary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2">
                <FileSignature size={15} /> Kaufvertrag
              </Link>
            )}
          </div>
          {!inserat && (
            <p className="text-xs text-zinc-500" data-testid="vorgang-ohne-inserat">
              Die Inserat-Adresse hat das Programm nicht lesen können – für den Kaufvertrag die Adresse in AutoSchnell
              unter „Vergleich“ einfügen.
            </p>
          )}
        </>
      )}
    </div>
  );
}
