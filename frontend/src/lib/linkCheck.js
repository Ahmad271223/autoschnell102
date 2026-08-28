/**
 * Linkprüfung mit Hintergrundjob + kontrollierter 503-Behandlung.
 *
 * Ablauf (siehe Backend /listings/check):
 *  1. Vorab-Check: bekannt → sofort fertig; unbekannt → Job-ID.
 *  2. Jobstatus pollen, bis completed/failed oder die Wartezeit endet.
 *  3. Ein 503 mit Retry-After ist KEIN Fehler, sondern Rückstau: wir
 *     warten die angegebene Zeit und versuchen es automatisch erneut —
 *     der Nutzer sieht nur die freundliche Wartemeldung.
 *
 * Alle Funktionen sind bewusst pur (Client wird injiziert), damit sie
 * ohne Browser/axios automatisiert testbar sind.
 */

export const WAIT_MESSAGE =
  "Das Fahrzeug wird gerade geprüft. Aufgrund hoher Auslastung kann dies kurz dauern.";
export const TIMEOUT_MESSAGE =
  "Die Prüfung dauert gerade ungewöhnlich lange. Bitte versuche es in ein paar Minuten erneut — dein Link ist vorgemerkt.";

const DEFAULTS = {
  maxWaitMs: 120_000,   // maximale Gesamtwartezeit
  pollMs: 2_000,        // Abstand der Statusabfragen
  retry503Ms: 5_000,    // Fallback, wenn kein Retry-After-Header kommt
};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function retryAfterMs(err, fallbackMs) {
  const h = err?.response?.headers?.["retry-after"];
  const s = parseInt(h, 10);
  return Number.isFinite(s) && s > 0 ? s * 1000 : fallbackMs;
}

/**
 * POST mit automatischer 503-Wiederholung. onWait(message) wird beim
 * ersten Rückstau aufgerufen (Anzeige der Wartemeldung). Wirft nach
 * Ablauf von maxWaitMs einen Error mit `code: "timeout"`.
 */
export async function postWithRetry503(client, path, body, opts = {}) {
  const { maxWaitMs, retry503Ms } = { ...DEFAULTS, ...opts };
  const deadline = Date.now() + maxWaitMs;
  for (;;) {
    try {
      return await client.post(path, body);
    } catch (err) {
      if (err?.response?.status !== 503) throw err;
      opts.onWait?.(WAIT_MESSAGE);
      const wait = retryAfterMs(err, retry503Ms);
      if (Date.now() + wait > deadline) {
        const e = new Error(TIMEOUT_MESSAGE);
        e.code = "timeout";
        throw e;
      }
      await sleep(wait);
    }
  }
}

/**
 * Kompletter Prüf-Ablauf: /listings/check → ggf. Job pollen.
 * Rückgabe: { status: "completed" } oder { status: "needs_client_fetch", url }.
 * failed → Error mit Backend-Meldung; Zeitüberschreitung → Error code "timeout".
 */
export async function checkLink(client, url, opts = {}) {
  const { maxWaitMs, pollMs } = { ...DEFAULTS, ...opts };
  const deadline = Date.now() + maxWaitMs;

  const { data: first } = await postWithRetry503(
    client, "/listings/check", { url },
    { ...opts, maxWaitMs: Math.max(1, deadline - Date.now()) });
  if (first.status === "completed") return first;
  if (first.status === "needs_client_fetch") return first;

  opts.onWait?.(WAIT_MESSAGE);
  const jobId = first.job_id;
  for (;;) {
    if (Date.now() >= deadline) {
      const e = new Error(TIMEOUT_MESSAGE);
      e.code = "timeout";
      throw e;
    }
    await sleep(pollMs);
    let data;
    try {
      ({ data } = await client.get(`/listings/check/${jobId}`));
    } catch (err) {
      if (err?.response?.status === 404) {
        // Job bereits weggeräumt — Ergebnis liegt dann im Cache.
        return { status: "completed", job_id: jobId };
      }
      throw err;
    }
    if (data.status === "completed") return data;
    if (data.status === "failed") {
      throw new Error(data.error || "Der Link konnte nicht geprüft werden.");
    }
  }
}
