// Beweisdokument zum Inserat (ersetzt die Snapshots, 10.09.2026).
//
// Es entsteht EIN PDF je Inserat mit allen ausgelesenen Daten, den
// Inseratsfotos, Anzeigen-ID und Inserats-Adresse. Alle Firmen, die das
// Inserat verwenden, teilen es.
//
// Wunsch Ahmad 18.09.2026: Das Dokument entsteht NUR auf Knopfdruck —
// vorher bekam jedes angesehene Inserat automatisch eines. Gibt es noch
// keines, zeigt diese Karte den Knopf "Beweisdokument erstellen"; nach dem
// Vertragsversand fragt der Versand-Dialog danach.
//
//   <BeweisCard beweis={result.beweis} cacheKey={result.cache_key} />  — nach dem Vergleich
//   <BeweisCard vehicleId="..." compact />      — Termine, PDF-Archiv (kleine Zeile)
//   <BeweisCard vehicleId="..." />              — Fahrzeugakte, Termindetails
//
// Fahrzeuge von vor der Umstellung haben evtl. noch einen alten Snapshot —
// der wird angezeigt, solange er existiert (er verfaellt nach 60 Tagen bzw.
// mit dem Kaufvertrag).
import { useEffect, useState } from "react";
import { api, errMsg, openAuthedFile } from "@/lib/api";
import { printBlobUrl } from "@/lib/pdf";
import { AlertTriangle, CheckCircle2, ExternalLink, FileText, Loader2, Printer, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

const TAKT_MS = 3000;
const MAX_ABFRAGEN = 100; // 100 x 3 s = 5 min
const LAUFEND = ["offen", "in_arbeit"];

// Pruefbericht 20.09.2026 (U-101/H33): "wird_geloescht" zaehlte weder als
// laufend noch als geloescht — die Karte zeigte fuer ein Dokument, das gerade
// verschwindet, endlos "wird erstellt".
function anzeigeStatus(status) {
  return status === "wird_geloescht" ? "geloescht" : status;
}

// 4xx (nicht vorhanden, keine Berechtigung, abgelaufen) aendert sich durch
// Nachfragen nicht — nur Netz- und Serverfehler lohnen einen neuen Versuch
// (U-100/H32: vorher 100 Abfragen ins Leere, danach "dauert länger").
function endgueltig(err) {
  const st = err?.response?.status;
  return typeof st === "number" && st >= 400 && st < 500 && st !== 408 && st !== 429;
}

function datum(iso, mitZeit = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return mitZeit ? d.toLocaleString("de-DE") : d.toLocaleDateString("de-DE");
}

// M44: Der Server nennt den Grund (abgelaufen, noch nicht fertig, Datei
// fehlt, Pruefsumme) — der wird jetzt angezeigt statt eines Einheitssatzes
// bzw. des englischen Rohtexts beim Drucken.
async function pdfOeffnen(id) {
  try {
    await openAuthedFile(`/beweise/${id}/pdf`, "application/pdf");
  } catch (err) {
    toast.error(errMsg(err, "Beweisdokument konnte nicht geladen werden"));
  }
}

async function pdfDrucken(id) {
  try {
    const r = await api.get(`/beweise/${id}/pdf`, { responseType: "blob" });
    const blobUrl = URL.createObjectURL(r.data);
    printBlobUrl(blobUrl, { label: "Beweisdokument" });
    setTimeout(() => URL.revokeObjectURL(blobUrl), 10 * 60 * 1000);
  } catch (err) {
    toast.error(errMsg(err, "Drucken nicht möglich"));
  }
}

export default function BeweisCard({ beweis: start, beweisId, vehicleId, cacheKey,
                                    compact = false }) {
  const [beweis, setBeweis] = useState(start || null);
  const [altSnapshot, setAltSnapshot] = useState(null);
  const [geladen, setGeladen] = useState(!vehicleId);
  const [zeitUeber, setZeitUeber] = useState(false);
  const [holt, setHolt] = useState(false);
  // U-98/H30: "nicht ladbar" ist etwas anderes als "gibt es nicht" — vorher
  // sahen "kein Dokument", "keine Berechtigung" und "Server kaputt" gleich aus.
  const [ladeFehler, setLadeFehler] = useState("");
  const [nichtVerfuegbar, setNichtVerfuegbar] = useState(false);
  const [neuLaden, setNeuLaden] = useState(0);
  const id = beweis?.id || start?.id || beweisId;
  // Ohne Fahrzeug oder Inseratsschluessel kann man nichts anfordern (z. B.
  // wenn die Karte nur ueber eine Beweis-ID eingebunden ist).
  const kannAnfordern = Boolean(vehicleId || cacheKey);

  // Wunsch Ahmad 18.09.2026: erst auf Knopfdruck erzeugen. Der Server merkt
  // vor (idempotent je Inserat), der Hintergrund-Worker baut das PDF; die
  // Abfrage-Schleife unten zeigt den Fortschritt wie bisher.
  const anfordern = async () => {
    setHolt(true);
    try {
      const { data } = await api.post("/beweise/anfordern",
        vehicleId ? { vehicle_id: vehicleId } : { cache_key: cacheKey });
      setBeweis(data?.beweis || null);
      setZeitUeber(false);
      toast.success("Beweisdokument wird erstellt — das dauert meist ein paar Sekunden.");
      // Befund 154 (19.09.2026): Wurde das Inserat nach dem Vergleich noch
      // einmal abgerufen, haelt das Dokument DIESEN neueren Stand fest —
      // das muss dastehen, sonst haelt man es fuer seinen Vergleichsstand.
      if (data?.hinweis) toast.warning(data.hinweis, { duration: 12000 });
    } catch (err) {
      toast.error(errMsg(err, "Beweisdokument konnte nicht angefordert werden"));
    } finally {
      setHolt(false);
    }
  };

  // Fahrzeug-Modus: Beweisdokument zum Inserat des Fahrzeugs suchen, sonst
  // einen alten Snapshot (Altbestand) anzeigen.
  useEffect(() => {
    if (!vehicleId) return undefined;
    let aus = false;
    (async () => {
      try {
        const { data } = await api.get("/beweise", { params: { vehicle_id: vehicleId } });
        if (aus) return;
        setLadeFehler("");
        if (data?.beweis) setBeweis(data.beweis);
        // Alte Aufnahme (vor 10.09.2026) IMMER mit anzeigen: sie belegt den
        // Stand zum Vertragsschluss, ein spaeteres Beweisdokument evtl. nicht.
        try {
          const alt = await api.get("/snapshots", { params: { vehicle_id: vehicleId } });
          if (!aus) setAltSnapshot((alt.data || []).find((x) => x.status === "ready") || null);
        } catch {
          /* keine alten Snapshots */
        }
      } catch (err) {
        if (aus) return;
        // 403/404: dieses Konto darf/kann hier nichts sehen — Karte weglassen.
        if (endgueltig(err)) setNichtVerfuegbar(true);
        else setLadeFehler(errMsg(err, "Beweisdokument konnte nicht geladen werden"));
      } finally {
        if (!aus) setGeladen(true);
      }
    })();
    return () => { aus = true; };
  }, [vehicleId, neuLaden]);

  // Solange das Dokument erstellt wird: regelmaessig nachfragen.
  const status = anzeigeStatus(beweis?.status || (id ? "offen" : null));
  useEffect(() => {
    if (!id || nichtVerfuegbar || (status && !LAUFEND.includes(status))) return undefined;
    let aus = false;
    let n = 0;
    let timer;
    setZeitUeber(false);
    const tick = async () => {
      n += 1;
      try {
        const { data } = await api.get(`/beweise/${id}`);
        if (aus) return;
        setBeweis(data);
        if (!LAUFEND.includes(data?.status)) return;
      } catch (err) {
        if (aus) return;
        if (endgueltig(err)) { setNichtVerfuegbar(true); return; }
      }
      if (n >= MAX_ABFRAGEN) { setZeitUeber(true); return; }
      timer = setTimeout(tick, TAKT_MS);
    };
    timer = setTimeout(tick, n === 0 && start ? TAKT_MS : 0);
    return () => { aus = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, status, neuLaden, nichtVerfuegbar]);

  // M45: Nach der Wartezeit nicht einfach stehen bleiben — erneut nachsehen.
  const erneut = () => { setLadeFehler(""); setZeitUeber(false); setNeuLaden((x) => x + 1); };

  if (!geladen || nichtVerfuegbar) return null;
  if (ladeFehler && !id) {
    return (
      <div className={compact ? "text-[11px]" : "apple-surface p-4 text-xs"} role="alert"
           data-testid="beweis-ladefehler" style={{ color: "var(--text-muted)" }}>
        <ShieldCheck size={11} className="inline mr-1 text-[var(--accent-red)]" />
        {ladeFehler}{" "}
        <button type="button" onClick={erneut} className="underline underline-offset-2 font-semibold">
          Erneut versuchen
        </button>
      </div>
    );
  }
  if (!id) {
    if (!kannAnfordern) {
      return altSnapshot ? <AltSnapshot snap={altSnapshot} compact={compact} /> : null;
    }
    const knopf = (
      <button type="button" onClick={anfordern} disabled={holt}
              data-testid="beweis-erstellen-btn"
              className={compact
                ? "apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full disabled:opacity-50"
                : "apple-btn apple-btn-secondary !py-2.5 !text-[12px] w-full disabled:opacity-50"}>
        {holt ? <Loader2 size={compact ? 11 : 13} className="animate-spin" />
              : <ShieldCheck size={compact ? 11 : 13} />}
        {holt ? "wird angefordert …" : "Beweisdokument erstellen"}
      </button>
    );
    return (
      <div className={compact ? "flex flex-col gap-1.5" : "space-y-3"}>
        {compact ? (
          <div className="flex items-center gap-2 flex-wrap" data-testid="beweis-inline">
            <div className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider"
                 style={{ color: "var(--text-muted)" }}>
              <ShieldCheck size={10} className="text-[var(--accent-red)]" /> Beweis
            </div>
            {knopf}
          </div>
        ) : (
          <div className="apple-surface p-5" data-testid="beweis-card">
            <div className="flex items-center gap-1.5 mb-2">
              <ShieldCheck size={12} className="text-[var(--accent-red)]" />
              <span className="overline">Beweisdokument</span>
            </div>
            <div className="text-xs mb-3 leading-relaxed" style={{ color: "var(--text-muted)" }}>
              Hält alle Inseratsdaten, die Fotos, die Anzeigen-ID und die Inserats-Adresse
              als PDF fest — für den Fall, dass der Verkäufer später etwas anderes sagt.
              Es entsteht nur, wenn du es hier verlangst.
            </div>
            {knopf}
          </div>
        )}
        {altSnapshot ? <AltSnapshot snap={altSnapshot} compact={compact} /> : null}
      </div>
    );
  }
  const alt = altSnapshot ? <AltSnapshot snap={altSnapshot} compact={compact} /> : null;

  if (compact) {
    return (
      <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 flex-wrap" data-testid="beweis-inline">
        <div className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider"
             style={{ color: "var(--text-muted)" }}>
          <ShieldCheck size={10} className="text-[var(--accent-red)]" /> Beweis
        </div>
        {status === "fertig" ? (
          <>
            <button type="button" onClick={() => pdfOeffnen(id)}
                    className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full"
                    data-testid="beweis-pdf-inline">
              <FileText size={11} /> PDF
            </button>
            <button type="button" onClick={() => pdfDrucken(id)}
                    className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full"
                    data-testid="beweis-print-inline" title="Direkt drucken">
              <Printer size={11} /> Drucken
            </button>
            <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              · {datum(beweis?.fertig_am)}
            </span>
          </>
        ) : (
          <>
            <StatusBadge status={status} zeitUeber={zeitUeber} />
            {zeitUeber && (
              <button type="button" onClick={erneut} data-testid="beweis-erneut-inline"
                      className="text-[10px] underline underline-offset-2" style={{ color: "var(--text-muted)" }}>
                erneut prüfen
              </button>
            )}
          </>
        )}
      </div>
      {alt}
      </div>
    );
  }

  return (
    <div className="space-y-3">
    <div className="apple-surface p-5" data-testid="beweis-card">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <ShieldCheck size={12} className="text-[var(--accent-red)]" />
          <span className="overline">Beweisdokument</span>
        </div>
        <StatusBadge status={status} zeitUeber={zeitUeber} />
      </div>

      {status === "fertig" ? (
        <>
          <div className="text-xs mb-3 leading-relaxed" style={{ color: "var(--text-muted)" }}>
            Alle ausgelesenen Inseratsdaten
            {beweis?.fotos_gesamt ? `, ${beweis.fotos_eingebettet || 0} von ${beweis.fotos_gesamt} Fotos` : ""}
            , Anzeigen-ID und Inserats-Adresse als PDF — festgehalten beim ersten Abruf
            ({datum(beweis?.daten_abgerufen_am || beweis?.erstellt_am, true)}).
          </div>
          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => pdfOeffnen(id)}
                    className="apple-btn apple-btn-primary !py-2.5 !text-[12px]"
                    data-testid="beweis-pdf-btn">
              <FileText size={13} /> PDF öffnen <ExternalLink size={10} />
            </button>
            <button type="button" onClick={() => pdfDrucken(id)}
                    className="apple-btn apple-btn-secondary !py-2.5 !text-[12px]"
                    data-testid="beweis-print-btn" title="Direkt drucken">
              <Printer size={13} /> Drucken
            </button>
          </div>
          {beweis?.pdf_bytes ? (
            <div className="text-[10px] mt-2 text-center" style={{ color: "var(--text-muted)" }}>
              PDF {Math.max(1, Math.round(beweis.pdf_bytes / 1024))} KB · erstellt am {datum(beweis?.fertig_am, true)}
            </div>
          ) : null}
        </>
      ) : status === "fehlgeschlagen" ? (
        <div className="text-xs leading-relaxed" style={{ color: "var(--tx-rot)" }}>
          Das Beweisdokument konnte nicht erstellt werden.
          {kannAnfordern ? (
            <button type="button" onClick={anfordern} disabled={holt}
                    data-testid="beweis-nochmal-btn"
                    className="apple-btn apple-btn-secondary !py-2 !text-[12px] w-full mt-2 disabled:opacity-50">
              {holt ? <Loader2 size={13} className="animate-spin" /> : <ShieldCheck size={13} />}
              Noch einmal versuchen
            </button>
          ) : null}
          {beweis?.fehler && (
            <div className="text-[10px] font-mono leading-snug p-2 mt-2 rounded-lg max-h-24 overflow-auto"
                 style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-muted)" }}>
              {beweis.fehler}
            </div>
          )}
        </div>
      ) : status === "geloescht" ? (
        <div className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Das Beweisdokument wurde nach Ablauf der Aufbewahrungsfrist gelöscht.
        </div>
      ) : (
        <div className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Alle Inseratsdaten und Fotos werden als PDF festgehalten (Beweis bei Streitfällen).
          {zeitUeber ? (
            <>
              {" "}Das dauert heute länger als üblich.{" "}
              <button type="button" onClick={erneut} data-testid="beweis-erneut"
                      className="underline underline-offset-2 font-semibold">
                Erneut prüfen
              </button>
            </>
          ) : " Das dauert meist nur wenige Sekunden …"}
        </div>
      )}
    </div>
    {alt}
    </div>
  );
}

function AltSnapshot({ snap, compact }) {
  const oeffnen = () =>
    openAuthedFile(`/snapshots/${snap.id}/pdf`, "application/pdf")
      .catch(() => toast.error("Datei konnte nicht geladen werden"));
  if (compact) {
    return (
      <div className="flex items-center gap-2 flex-wrap" data-testid="beweis-alt-inline">
        <div className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider"
             style={{ color: "var(--text-muted)" }}>
          <ShieldCheck size={10} className="text-[var(--accent-red)]" /> Beweis (alt)
        </div>
        <button type="button" onClick={oeffnen}
                className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full"
                data-testid="beweis-alt-pdf-inline">
          <FileText size={11} /> PDF
        </button>
      </div>
    );
  }
  return (
    <div className="apple-surface p-5" data-testid="beweis-alt-card">
      <div className="flex items-center gap-1.5 mb-2">
        <ShieldCheck size={12} className="text-[var(--accent-red)]" />
        <span className="overline">Beweis (Aufnahme vor der Umstellung)</span>
      </div>
      <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
        Aufnahme der Inseratsseite vom {datum(snap.completed_at, true)}.
      </div>
      <button type="button" onClick={oeffnen}
              className="apple-btn apple-btn-secondary !py-2.5 !text-[12px] w-full"
              data-testid="beweis-alt-pdf-btn">
        <FileText size={13} /> PDF öffnen <ExternalLink size={10} />
      </button>
    </div>
  );
}

function StatusBadge({ status, zeitUeber }) {
  if (status === "fertig") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20"
            data-testid="beweis-status-fertig">
        <CheckCircle2 size={9} style={{ color: "var(--st-gruen)" }} />
        <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "var(--st-gruen)" }}>fertig</span>
      </span>
    );
  }
  if (status === "fehlgeschlagen" || status === "geloescht") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-red-500/10 border border-red-500/20"
            data-testid={`beweis-status-${status}`}>
        <AlertTriangle size={9} style={{ color: "var(--tx-rot)" }} />
        <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "var(--tx-rot)" }}>
          {status === "geloescht" ? "gelöscht" : "Fehler"}
        </span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20"
          data-testid="beweis-status-laeuft">
      <Loader2 size={9} className={zeitUeber ? "" : "animate-spin"} style={{ color: "var(--tx-amber)" }} />
      <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "var(--tx-amber)" }}>
        {zeitUeber ? "dauert länger" : "wird erstellt"}
      </span>
    </span>
  );
}
