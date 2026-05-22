/**
 * Open a backend PDF in a new tab without being blocked by popup blockers.
 * Uses a temporary <a target="_blank"> click which browsers reliably allow
 * inside a user gesture handler.
 */
import { api } from "@/lib/api";

export async function openContractPdf(contractId) {
  const r = await api.get(`/contracts/${contractId}/pdf`, { responseType: "blob" });
  const blobUrl = URL.createObjectURL(r.data);
  // Use a real <a target="_blank"> click – this is the most popup-blocker-
  // resistant way and never falls back to navigating the current tab.
  const a = document.createElement("a");
  a.href = blobUrl;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }, 1000);
}

/**
 * Direktes Drucken einer Blob-URL (PDF/PNG): lädt sie in ein unsichtbares
 * iframe, wartet bis das PDF gerendert ist und öffnet dann den Browser-
 * Druckdialog. Der User kann dort Drucker wählen oder als PDF speichern –
 * ohne Zwischenschritt durch einen neuen Tab.
 *
 * Fallback: scheitert der iframe-Trick (z.B. durch Content-Disposition
 * oder CSP), öffnen wir die URL stattdessen in einem neuen Tab und
 * triggern `window.print()` nach dem Load.
 */
export function printBlobUrl(blobUrl, { label = "Dokument" } = {}) {
  try {
    const iframe = document.createElement("iframe");
    iframe.style.position = "fixed";
    iframe.style.right = "0";
    iframe.style.bottom = "0";
    iframe.style.width = "0";
    iframe.style.height = "0";
    iframe.style.border = "0";
    iframe.setAttribute("aria-hidden", "true");
    iframe.setAttribute("title", `Drucken – ${label}`);
    iframe.src = blobUrl;
    document.body.appendChild(iframe);

    const cleanup = () => {
      setTimeout(() => {
        try { document.body.removeChild(iframe); } catch { /* ignore */ }
      }, 30_000);
    };

    iframe.onload = () => {
      try {
        // Kleines Timeout, damit der PDF-Viewer wirklich gerendert hat,
        // bevor print() gerufen wird. Ohne das erscheint in manchen
        // Chromium-Versionen ein leerer Druckdialog.
        setTimeout(() => {
          try {
            iframe.contentWindow?.focus();
            iframe.contentWindow?.print();
          } catch {
            // Cross-origin / PDF-Viewer blockiert .print()?
            // -> neuen Tab als Fallback
            window.open(blobUrl, "_blank", "noopener,noreferrer");
          }
          cleanup();
        }, 400);
      } catch {
        cleanup();
        window.open(blobUrl, "_blank", "noopener,noreferrer");
      }
    };
  } catch {
    window.open(blobUrl, "_blank", "noopener,noreferrer");
  }
}

/**
 * Lädt den Kaufvertrag-PDF vom Backend und öffnet direkt den Druckdialog.
 * Alternative zu `openContractPdf` wenn der User gleich drucken will.
 */
export async function printContractPdf(contractId) {
  const r = await api.get(`/contracts/${contractId}/pdf`, { responseType: "blob" });
  const blobUrl = URL.createObjectURL(r.data);
  printBlobUrl(blobUrl, { label: "Kaufvertrag" });
  // Blob-URL bleibt für die Lebensdauer des iframe gültig – nach
  // cleanup() automatisch GC-tauglich.
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
}

/**
 * Abholauftrag (Übergabeprotokoll für den Fahrer) zu einem Termin in
 * einem neuen Tab öffnen. Enthält auto-ausgefüllte Fahrzeugdaten,
 * Ausstattungs- + Dokumenten-Check mit ○-Kreisen, Schadensskizze aus
 * dem Kaufvertrag und leere Vor-Ort-Skizze für neue Markierungen.
 */
export async function openPickupOrderPdf(appointmentId) {
  const r = await api.get(`/appointments/${appointmentId}/pickup-order.pdf`, {
    responseType: "blob",
  });
  const blobUrl = URL.createObjectURL(r.data);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }, 1000);
}

/**
 * Abholauftrag direkt drucken (Browser-Druckdialog ohne neuen Tab).
 */
export async function printPickupOrderPdf(appointmentId) {
  const r = await api.get(`/appointments/${appointmentId}/pickup-order.pdf`, {
    responseType: "blob",
  });
  const blobUrl = URL.createObjectURL(r.data);
  printBlobUrl(blobUrl, { label: "Abholauftrag" });
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
}

/**
 * Abholauftrag herunterladen (Browser-Save-Dialog).
 */
export async function downloadPickupOrderPdf(appointmentId, filename = "Abholauftrag.pdf") {
  const r = await api.get(`/appointments/${appointmentId}/pickup-order.pdf?download=1`, {
    responseType: "blob",
  });
  const blobUrl = URL.createObjectURL(r.data);
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    document.body.removeChild(a);
    URL.revokeObjectURL(blobUrl);
  }, 1000);
}

/**
 * Druckt einen Listing-Snapshot (PDF-Variante des Beweises) direkt.
 * Nutzt denselben Auth-Flow wie das &lt;a&gt;-Download: Token im Query.
 */
export async function printSnapshot(snapshotId, kind = "pdf") {
  // Snapshot-Bytes holen (respektiert Auth via axios-Interceptor).
  const r = await api.get(`/snapshots/${snapshotId}/${kind}`, { responseType: "blob" });
  const blobUrl = URL.createObjectURL(r.data);
  printBlobUrl(blobUrl, { label: "Snapshot" });
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
}

