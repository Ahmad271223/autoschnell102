// Listing-snapshot card: shows live capture status, then download/preview
// buttons once the proof-of-listing artefacts are stored.
//
// Two modes:
//   <SnapshotCard snapshotId="..." />           — live polling for a job
//   <SnapshotCard vehicleId="..." compact />    — show latest ready snapshot for a car
import { useEffect, useState } from "react";
import { api, openAuthedFile } from "@/lib/api";
import { Camera, FileText, ImageIcon, Loader2, AlertTriangle, CheckCircle2, ExternalLink, Printer } from "lucide-react";
import { printSnapshot } from "@/lib/pdf";
import { toast } from "sonner";

const POLL_INTERVAL_MS = 4000;
const MAX_POLL = 30;  // ~2 minutes total

export default function SnapshotCard({ snapshotId, vehicleId, compact = false }) {
  const [snap, setSnap] = useState(null);
  const [polls, setPolls] = useState(0);

  // Mode 1: poll a specific snapshot job until it's ready or failed.
  useEffect(() => {
    if (!snapshotId) return;
    let cancelled = false;
    setSnap(null);
    setPolls(0);

    const tick = async () => {
      try {
        const { data } = await api.get(`/snapshots/${snapshotId}`);
        if (cancelled) return;
        setSnap(data);
        setPolls((p) => p + 1);
        if (data.status === "ready" || data.status === "failed") return;
      } catch {
        if (cancelled) return;
        setPolls((p) => p + 1);
      }
      if (!cancelled) setTimeout(tick, POLL_INTERVAL_MS);
    };
    tick();
    return () => { cancelled = true; };
  }, [snapshotId]);

  // Mode 2: look up the most recent snapshot for a given vehicle (used in
  // contract / appointment views, where the vehicle is the only stable id).
  useEffect(() => {
    if (snapshotId || !vehicleId) return;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await api.get(`/snapshots`, { params: { vehicle_id: vehicleId } });
        if (cancelled) return;
        // Newest first → take the first 'ready' snapshot, fall back to first overall.
        const ready = data.find((x) => x.status === "ready");
        setSnap(ready || data[0] || null);
      } catch {
        // ignore
      }
    })();
    return () => { cancelled = true; };
  }, [vehicleId, snapshotId]);

  if (!snapshotId && !vehicleId) return null;
  if (vehicleId && !snap) return null;  // nothing archived for this car
  const status = snap?.status || "pending";
  const id = snap?.id || snapshotId;
  const openFile = (kind) =>
    openAuthedFile(`/snapshots/${id}/${kind}`,
                   kind === "pdf" ? "application/pdf" : "image/png")
      .catch(() => toast.error("Datei konnte nicht geladen werden"));

  const handlePrint = async () => {
    if (!id) return;
    try {
      await printSnapshot(id, "pdf");
    } catch (err) {
      toast.error("Drucken nicht möglich: " + (err?.message || "Fehler"));
    }
  };

  if (compact) {
    return (
      <div className="flex items-center gap-2 flex-wrap" data-testid="snapshot-inline">
        <div className="inline-flex items-center gap-1 text-[10px] uppercase font-bold tracking-wider"
             style={{ color: "var(--text-muted)" }}>
          <Camera size={10} className="text-[var(--accent-red)]" /> Beweis
        </div>
        {status === "ready" ? (
          <>
            <button type="button" onClick={() => openFile("pdf")}
               className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full"
               data-testid="snapshot-pdf-inline">
              <FileText size={11} /> PDF
            </button>
            <button type="button" onClick={() => openFile("png")}
               className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full"
               data-testid="snapshot-png-inline">
              <ImageIcon size={11} /> Foto
            </button>
            <button type="button" onClick={handlePrint}
                    className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full"
                    data-testid="snapshot-print-inline"
                    title="Direkt drucken">
              <Printer size={11} /> Drucken
            </button>
            {snap?.completed_at && (
              <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                · {new Date(snap.completed_at).toLocaleDateString("de-DE")}
              </span>
            )}
          </>
        ) : (
          <StatusBadge status={status} polls={polls} />
        )}
      </div>
    );
  }

  return (
    <div className="apple-surface p-5" data-testid="snapshot-card">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <Camera size={12} className="text-[var(--accent-red)]" />
          <span className="overline">Beweis-Archiv</span>
        </div>
        <StatusBadge status={status} polls={polls} />
      </div>

      {status === "ready" ? (
        <>
          <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
            {snap?.art === "datenblatt"
              ? "Mobile Rebuild — nachgebaute Inserats-Ansicht aus den automatisch ausgelesenen Daten (mobile.de blockt Seiten-Screenshots) — erstellt am "
              : "Vollständiges Snapshot der Inserat-Seite — datiert auf "}
            {snap?.completed_at ? new Date(snap.completed_at).toLocaleString("de-DE") : "—"}.
          </div>
          <div className="grid grid-cols-3 gap-2">
            <button type="button" onClick={() => openFile("pdf")}
               className="apple-btn apple-btn-primary !py-2.5 !text-[12px]"
               data-testid="snapshot-pdf-btn">
              <FileText size={13} /> PDF <ExternalLink size={10} />
            </button>
            <button type="button" onClick={() => openFile("png")}
               className="apple-btn apple-btn-secondary !py-2.5 !text-[12px]"
               data-testid="snapshot-png-btn">
              <ImageIcon size={13} /> Foto <ExternalLink size={10} />
            </button>
            <button type="button" onClick={handlePrint}
                    className="apple-btn apple-btn-secondary !py-2.5 !text-[12px]"
                    data-testid="snapshot-print-btn"
                    title="Direkt drucken">
              <Printer size={13} /> Drucken
            </button>
          </div>
          {(snap?.pdf_bytes || snap?.png_bytes) && (
            <div className="text-[10px] mt-2 text-center" style={{ color: "var(--text-muted)" }}>
              PDF {Math.round((snap?.pdf_bytes || 0) / 1024)} KB · PNG {Math.round((snap?.png_bytes || 0) / 1024)} KB
            </div>
          )}
        </>
      ) : status === "failed" ? (
        <>
          <div className="text-xs leading-relaxed mb-2" style={{ color: "#ff6b6b" }}>
            Snapshot fehlgeschlagen.
          </div>
          {snap?.error && (
            <div className="text-[10px] font-mono leading-snug p-2 rounded-lg max-h-24 overflow-auto"
                 style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-muted)" }}>
              {snap.error}
            </div>
          )}
        </>
      ) : (
        <div className="text-xs leading-relaxed" style={{ color: "var(--text-muted)" }}>
          Inserat-Seite wird im Hintergrund als PDF + Foto archiviert
          (Beweis bei Streitfällen). Das dauert in der Regel 5–30 Sekunden…
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status, polls }) {
  if (status === "ready") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-emerald-500/10 border border-emerald-500/20"
            data-testid="snapshot-status-ready">
        <CheckCircle2 size={9} style={{ color: "#34c759" }} />
        <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "#34c759" }}>fertig</span>
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-red-500/10 border border-red-500/20"
            data-testid="snapshot-status-failed">
        <AlertTriangle size={9} style={{ color: "#ff6b6b" }} />
        <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "#ff6b6b" }}>Fehler</span>
      </span>
    );
  }
  const timedOut = polls >= MAX_POLL;
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/20"
          data-testid="snapshot-status-pending">
      <Loader2 size={9} className={timedOut ? "" : "animate-spin"} style={{ color: "#f5a524" }} />
      <span className="text-[9px] uppercase font-bold tracking-wider" style={{ color: "#f5a524" }}>
        {timedOut ? "läuft…" : "läuft"}
      </span>
    </span>
  );
}
