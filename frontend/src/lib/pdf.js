/**
 * PDFs vom Server laden und öffnen, drucken oder speichern.
 *
 * Prüfbericht 20.09.2026 (B15): Das Öffnen läuft über lib/dateiOeffnen.js —
 * dauert das Laden länger als der Browser einen Klick gelten lässt, kommt ein
 * Hinweis mit Knopf statt eines still verworfenen Tabs. Fehler (404, 503 …)
 * werfen die Funktionen weiter; jeder Aufrufer zeigt sie mit errMsg an.
 */
import { api } from "@/lib/api";
import { blobOeffnen, blobUrlImTab } from "@/lib/dateiOeffnen";

/**
 * variante: "druck" (Standard, mit Unterschriftslinien) oder "digital"
 * (Ausfertigung für E-Mail/WhatsApp — Vertragstext statt Unterschriftslinien).
 */
export async function openContractPdf(contractId, { variante = "druck" } = {}) {
  const startMs = Date.now();
  const params = variante === "digital" ? { variante: "digital" } : undefined;
  const r = await api.get(`/contracts/${contractId}/pdf`, { responseType: "blob", params });
  return blobOeffnen(r.data, { startMs, titel: "Der Kaufvertrag", mime: "application/pdf" });
}

/**
 * Direktes Drucken einer Blob-URL (PDF/PNG): lädt sie in ein unsichtbares
 * iframe, wartet bis das PDF gerendert ist und öffnet dann den Browser-
 * Druckdialog. Der User kann dort Drucker wählen oder als PDF speichern –
 * ohne Zwischenschritt durch einen neuen Tab.
 *
 * Fallback: scheitert der iframe-Trick (z.B. durch Content-Disposition
 * oder CSP), öffnen wir die URL stattdessen in einem neuen Tab. Nicht mehr
 * mit "noopener": ein so abgekoppelter Tab darf die Blob-Adresse in neueren
 * Chrome-Versionen nicht laden und bleibt weiß (siehe api.openAuthedFile).
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
            blobUrlImTab(blobUrl);
          }
          cleanup();
        }, 400);
      } catch {
        cleanup();
        blobUrlImTab(blobUrl);
      }
    };
  } catch {
    blobUrlImTab(blobUrl);
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
  // Blob-URL bleibt für die Lebensdauer des iframe gültig — danach frei.
  setTimeout(() => URL.revokeObjectURL(blobUrl), 10 * 60 * 1000);
}

/**
 * Abholauftrag (Übergabeprotokoll für den Fahrer) zu einem Termin in
 * einem neuen Tab öffnen. Enthält auto-ausgefüllte Fahrzeugdaten,
 * Ausstattungs- + Dokumenten-Check mit ○-Kreisen, Schadensskizze aus
 * dem Kaufvertrag und leere Vor-Ort-Skizze für neue Markierungen.
 */
export async function openPickupOrderPdf(appointmentId) {
  const startMs = Date.now();
  const r = await api.get(`/appointments/${appointmentId}/pickup-order.pdf`, {
    responseType: "blob",
  });
  return blobOeffnen(r.data, { startMs, titel: "Der Abholauftrag", mime: "application/pdf" });
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
  setTimeout(() => URL.revokeObjectURL(blobUrl), 10 * 60 * 1000);
}

/**
 * Abholauftrag herunterladen (Browser-Save-Dialog).
 */
export async function downloadPickupOrderPdf(appointmentId, filename = "Abholauftrag.pdf") {
  const startMs = Date.now();
  const r = await api.get(`/appointments/${appointmentId}/pickup-order.pdf?download=1`, {
    responseType: "blob",
  });
  return blobOeffnen(r.data, { startMs, titel: "Der Abholauftrag", dateiname: filename,
                               mime: "application/pdf" });
}
