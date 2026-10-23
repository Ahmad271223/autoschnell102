/*
 * PDF im Browser anzeigen (Kundenportal, 29.09.2026): Der Kunde soll den Kaufvertrag auf dem
 * Handy oder Laptop DIREKT auf der Seite lesen, bevor er unterschreibt. Ein <iframe>/<object>
 * auf /api/… geht nicht (die API sendet frame-ancestors 'none', die Seite object-src 'none'),
 * deshalb rendert pdf.js jede Seite in ein <canvas>. pdf.js wird erst geladen, wenn es
 * gebraucht wird (die Firmenseite bleibt sonst klein); der Worker liegt als eigene Datei im
 * Build (gleiche Herkunft, passt zur CSP script-src 'self').
 */
// Der Worker wird von Vite als eigener JS-Chunk gebaut (?worker) — nicht als .mjs-Datei kopiert:
// nginx/der Test-Server liefern .mjs sonst als application/octet-stream aus, und der Browser lehnt ein
// Modul mit falschem MIME-Typ ab (Browser-Probe 29.09.2026: "Failed to load module script").
import PdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?worker";

let werkzeug = null;

async function pdfjsLaden() {
  if (werkzeug) return werkzeug;
  const pdfjs = await import("pdfjs-dist");
  pdfjs.GlobalWorkerOptions.workerPort = new PdfWorker();
  werkzeug = pdfjs;
  return pdfjs;
}

/**
 * Alle Seiten eines PDF-Blobs als <canvas>-Elemente (Breite `breite` CSS-Pixel, scharf auf
 * hochauflösenden Bildschirmen). Liefert [] bei einem unlesbaren Dokument — der Aufrufer zeigt
 * dann den Knopf "PDF öffnen".
 */
export async function pdfSeitenRendern(blob, breite) {
  const pdfjs = await pdfjsLaden();
  const daten = new Uint8Array(await blob.arrayBuffer());
  const dokument = await pdfjs.getDocument({ data: daten }).promise;
  const dpr = Math.min(3, (typeof window !== "undefined" && window.devicePixelRatio) || 1);
  const seiten = [];
  for (let nr = 1; nr <= dokument.numPages; nr += 1) {
    const seite = await dokument.getPage(nr);
    const grund = seite.getViewport({ scale: 1 });
    const massstab = Math.max(0.2, breite / grund.width);
    const ansicht = seite.getViewport({ scale: massstab * dpr });
    const canvas = document.createElement("canvas");
    canvas.width = Math.ceil(ansicht.width);
    canvas.height = Math.ceil(ansicht.height);
    canvas.style.width = "100%";
    canvas.style.height = "auto";
    canvas.setAttribute("aria-label", `Vertragsseite ${nr} von ${dokument.numPages}`);
    // intent "print": pdf.js zeichnet ohne requestAnimationFrame — im Hintergrund-Tab, in einer
    // gedrosselten Ansicht oder direkt nach dem Umschalten der App wartete "display" sonst ewig auf
    // den naechsten Bildaufbau (Browser-Probe 29.09.2026: Vorschau blieb bei "wird geladen").
    await seite.render({ canvasContext: canvas.getContext("2d"), viewport: ansicht, intent: "print" }).promise;
    seiten.push(canvas);
  }
  return seiten;
}
