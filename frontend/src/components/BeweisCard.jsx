// Beweisdokument zum Inserat (ersetzt die Snapshots, 10.09.2026).
//
// Beim ersten Gebrauch eines Inserats-Links (egal von wem) entsteht EIN
// PDF mit allen ausgelesenen Daten, den Inseratsfotos, Anzeigen-ID und
// Inserats-Adresse. Alle Firmen, die das Inserat verwenden, teilen es.
//
//   <BeweisCard beweis={result.beweis} />       — nach dem Vergleich (wartet, bis fertig)
//   <BeweisCard vehicleId="..." compact />      — Termine, PDF-Archiv (kleine Zeile)
//   <BeweisCard vehicleId="..." />              — Fahrzeugakte, Termindetails
//
// Fahrzeuge von vor der Umstellung haben evtl. noch einen alten Snapshot —
// der wird angezeigt, solange er existiert (er verfaellt nach 60 Tagen bzw.
// mit dem Kaufvertrag).
import { useEffect, useState } from "react";
import { api, openAuthedFile } from "@/lib/api";
import { printBlobUrl } from "@/lib/pdf";
import { AlertTriangle, CheckCircle2, ExternalLink, FileText, Loader2, Printer, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

const TAKT_MS = 3000;
const MAX_ABFRAGEN = 100; // 100 x 3 s = 5 min
const LAUFEND = ["offen", "in_arbeit"];

function datum(iso, mitZeit = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return mitZeit ? d.toLocaleString("de-DE") : d.toLocaleDateString("de-DE");
}

async function pdfOeffnen(id) {
  try {
    await openAuthedFile(`/beweise/${id}/pdf`, "application/pdf");
  } catch {
    toast.error("Beweisdokument konnte nicht geladen werden");
  }
}

async function pdfDrucken(id) {
  try {
    const r = await api.get(`/beweise/${id}/pdf`, { responseType: "blob" });
    const blobUrl = URL.createObjectURL(r.data);
    printBlobUrl(blobUrl, { label: "Beweisdokument" });
    setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
  } catch (err) {
    toast.error("Drucken nicht möglich: " + (err?.message || "Fehler"));
  }
}

export default function BeweisCard({ beweis: start, beweisId, vehicleId, compact = false }) {
  const [beweis, setBeweis] = useState(start || null);
  const [altSnapshot, setAltSnapshot] = useState(null);
  const [geladen, setGeladen] = useState(!vehicleId);
  const [zeitUeber, setZeitUeber] = useState(false);
  const id = beweis?.id || start?.id || beweisId;

  // Fahrzeug-Modus: Beweisdokument zum Inserat des Fahrzeugs suchen, sonst
  // einen alten Snapshot (Altbestand) anzeigen.
  useEffect(() => {
    if (!vehicleId) return undefined;
    let aus = false;
    (async () => {
      try {
        const { data } = await api.get("/beweise", { params: { vehicle_id: vehicleId } });
        if (aus) return;
        if (data?.beweis) setBeweis(data.beweis);
        // Alte Aufnahme (vor 10.09.2026) IMMER mit anzeigen: sie belegt den
        // Stand zum Vertragsschluss, ein spaeteres Beweisdokument evtl. nicht.
        try {
          const alt = await api.get("/snapshots", { params: { vehicle_id: vehicleId } });
          if (!aus) setAltSnapshot((alt.data || []).find((x) => x.status === "ready") || null);
        } catch {
          /* keine alten Snapshots */
        }
      } catch {
        /* nichts anzeigen */
      } finally {
        if (!aus) setGeladen(true);
      }
    })();
    return () => { aus = true; };
  }, [vehicleId]);

  // Solange das Dokument erstellt wird: regelmaessig nachfragen.
  const status = beweis?.status || (id ? "offen" : null);
  useEffect(() => {
    if (!id || (status && !LAUFEND.includes(status))) return undefined;
    let aus = false;
    let n = 0;
    let timer;
    const tick = async () => {
      n += 1;
      try {
        const { data } = await api.get(`/beweise/${id}`);
        if (aus) return;
        setBeweis(data);
        if (!LAUFEND.includes(data?.status)) return;
      } catch {
        if (aus) return;
      }
      if (n >= MAX_ABFRAGEN) { setZeitUeber(true); return; }
      timer = setTimeout(tick, TAKT_MS);
    };
    timer = setTimeout(tick, n === 0 && start ? TAKT_MS : 0);
    return () => { aus = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, status]);

  if (!geladen) return null;
  if (!id) {
    return altSnapshot ? <AltSnapshot snap={altSnapshot} compact={compact} /> : null;
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
          <StatusBadge status={status} zeitUeber={zeitUeber} />
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
        <div className="text-xs leading-relaxed" style={{ color: "#ff6b6b" }}>
          Das Beweisdokument konnte nicht erstellt werden. Beim nächsten Vergleich
          dieses Inserats wird es automatisch erneut versucht.
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
          Das dauert meist nur wenige Sekunden …
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
        <CheckCircle2 size={9} style={{ color: "#34c759" }} />
        <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "#34c759" }}>fertig</span>
      </span>
    );
  }
  if (status === "fehlgeschlagen" || status === "geloescht") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-red-500/10 border border-red-500/20"
            data-testid={`beweis-status-${status}`}>
        <AlertTriangle size={9} style={{ color: "#ff6b6b" }} />
        <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "#ff6b6b" }}>
          {status === "geloescht" ? "gelöscht" : "Fehler"}
        </span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20"
          data-testid="beweis-status-laeuft">
      <Loader2 size={9} className={zeitUeber ? "" : "animate-spin"} style={{ color: "#f5a524" }} />
      <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "#f5a524" }}>
        {zeitUeber ? "dauert länger" : "wird erstellt"}
      </span>
    </span>
  );
}
