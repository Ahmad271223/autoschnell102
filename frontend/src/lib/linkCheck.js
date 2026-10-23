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
  // Kurzer Abstand ZWISCHEN den Statusabfragen: der Server haelt jede
  // Abfrage selbst bis ~2,4 s offen (Long-Poll) und antwortet in dem
  // Moment, in dem der Abruf fertig ist. Frueher wartete das Frontend
  // hier blind 2 s VOR der ersten Abfrage — das kostete bei jedem
  // neuen Link rund 1-2 Sekunden.
  pollMs: 250,
  retry503Ms: 5_000,    // Fallback, wenn kein Retry-After-Header kommt
};

// Wunsch Ahmad 18.09.2026: Der Nutzer bricht das Warten mit "X" ab. Ein
// AbortSignal geht durch alle Schritte; wer abbricht, bekommt einen Fehler
// mit code "abgebrochen" — die Oberflaeche zeigt dafuer keine Fehlermeldung.
export const ABBRUCH = "abgebrochen";

export function abbruchFehler() {
  const e = new Error("Abgebrochen");
  e.code = ABBRUCH;
  return e;
}

/**
 * Pause, die ein Abbruch sofort beendet.
 *
 * Rollenprüfung 22.09.2026 (RP-004/RP-103/RP-254): Die Pausen zwischen den
 * Statusabfragen und vor der 503-Wiederholung beachteten das Signal nicht.
 * Ein abgebrochener Lauf hing darin bis zu 5 Sekunden weiter und räumte
 * danach den Zustand eines inzwischen neu gestarteten Laufs ab. Jetzt endet
 * die Pause beim Abbruch sofort mit dem Abbruch-Fehler.
 */
export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(abbruchFehler()); return; }
    let weg = null;
    const t = setTimeout(() => {
      if (weg) signal?.removeEventListener?.("abort", weg);
      resolve();
    }, ms);
    if (signal?.addEventListener) {
      weg = () => { clearTimeout(t); reject(abbruchFehler()); };
      signal.addEventListener("abort", weg, { once: true });
    }
  });
}

/** true, wenn der Fehler vom Abbrechen kommt (axios meldet ERR_CANCELED). */
export function istAbbruch(err) {
  return err?.code === ABBRUCH || err?.code === "ERR_CANCELED"
    || err?.name === "CanceledError" || err?.name === "AbortError";
}

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
  const { maxWaitMs, retry503Ms, signal } = { ...DEFAULTS, ...opts };
  const deadline = Date.now() + maxWaitMs;
  for (;;) {
    if (signal?.aborted) throw abbruchFehler();
    try {
      return await client.post(path, body, signal ? { signal } : undefined);
    } catch (err) {
      if (istAbbruch(err)) throw abbruchFehler();
      if (err?.response?.status !== 503) throw err;
      opts.onWait?.(WAIT_MESSAGE);
      const wait = retryAfterMs(err, retry503Ms);
      if (Date.now() + wait > deadline) {
        const e = new Error(TIMEOUT_MESSAGE);
        e.code = "timeout";
        throw e;
      }
      await sleep(wait, signal);          // RP-254: Abbruch beendet die Pause
    }
  }
}

/**
 * Kompletter Prüf-Ablauf: /listings/check → ggf. Job pollen.
 * Rückgabe: { status: "completed" } oder { status: "needs_client_fetch", url }.
 * failed → Error mit Backend-Meldung; Zeitüberschreitung → Error code "timeout".
 */
export async function checkLink(client, url, opts = {}) {
  const { maxWaitMs, pollMs, signal } = { ...DEFAULTS, ...opts };
  const deadline = Date.now() + maxWaitMs;
  if (signal?.aborted) throw abbruchFehler();

  const { data: first } = await postWithRetry503(
    client, "/listings/check",
    opts.ohneErweiterung ? { url, ohne_erweiterung: true } : { url },
    { ...opts, maxWaitMs: Math.max(1, deadline - Date.now()) });
  if (first.status === "completed") return first;
  if (first.status === "needs_client_fetch") return first;

  opts.onWait?.(WAIT_MESSAGE);
  const jobId = first.job_id;
  // Die Job-Nummer nach draussen geben: nur damit kann das "X" dem Server
  // sagen, dass hier niemand mehr wartet.
  opts.onJob?.(jobId);
  let ersteAbfrage = true;
  for (;;) {
    if (signal?.aborted) throw abbruchFehler();
    if (Date.now() >= deadline) {
      const e = new Error(TIMEOUT_MESSAGE);
      e.code = "timeout";
      throw e;
    }
    // Erste Abfrage SOFORT — der Server long-pollt und antwortet, sobald
    // der Abruf fertig ist. Nur zwischen weiteren Abfragen kurz pausieren.
    if (!ersteAbfrage) await sleep(pollMs, signal);   // RP-254
    ersteAbfrage = false;
    let data;
    try {
      ({ data } = await client.get(`/listings/check/${jobId}`,
                                   signal ? { signal } : undefined));
    } catch (err) {
      if (istAbbruch(err)) throw abbruchFehler();
      if (err?.response?.status === 404) {
        // Runde 19: Job bereits weggeräumt — nicht raten, sondern einmal neu
        // prüfen: ein Cache-Treffer kommt als "completed", sonst ein frischer Job.
        if (!opts.nochmalNach404) {
          return checkLink(client, url, {
            ...opts, nochmalNach404: true, maxWaitMs: Math.max(1, deadline - Date.now()),
          });
        }
        throw new Error("Der Link konnte nicht geprüft werden — bitte erneut versuchen.");
      }
      throw err;
    }
    if (data.status === "completed") return data;
    if (data.status === "failed") {
      throw new Error(data.error || "Der Link konnte nicht geprüft werden.");
    }
  }
}
