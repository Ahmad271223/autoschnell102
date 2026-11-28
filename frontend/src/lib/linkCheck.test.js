/**
 * Automatisierte Tests für die kontrollierte 503-Behandlung und den
 * Job-Poll-Ablauf der Linkprüfung (Priorität 1 aus dem Review).
 * Läuft ohne Browser/axios — der Client wird gemockt.
 */
import {
  checkLink, istAbbruch, postWithRetry503, TIMEOUT_MESSAGE, WAIT_MESSAGE,
} from "./linkCheck";

const err503 = (retryAfter) => {
  const e = new Error("busy");
  e.response = { status: 503, headers: { "retry-after": String(retryAfter) } };
  return e;
};

describe("postWithRetry503", () => {
  test("503 mit Retry-After wird automatisch wiederholt und meldet die Wartemeldung", async () => {
    const calls = [];
    let n = 0;
    const noHeader = () => {           // 503 OHNE Retry-After-Header
      const e = new Error("busy");
      e.response = { status: 503, headers: {} };
      return e;
    };
    const client = {
      post: vi.fn(async () => {
        n += 1;
        calls.push(Date.now());
        if (n < 3) throw noHeader();
        return { data: { ok: true } };
      }),
    };
    const waits = [];
    // retry503Ms klein halten, damit der Test schnell laeuft; dass der
    // Retry-After-Header (ganze Sekunden) gelesen wird, prueft der
    // naechste Test ueber die Zeitbudget-Rechnung.
    const res = await postWithRetry503(client, "/mobile/compare", { url: "x" },
      { onWait: (m) => waits.push(m), maxWaitMs: 5000, retry503Ms: 10 });
    expect(res.data.ok).toBe(true);
    expect(client.post).toHaveBeenCalledTimes(3);
    // Der Nutzer bekommt die freundliche Meldung, keinen Fehler:
    expect(waits[0]).toBe(WAIT_MESSAGE);
    expect(WAIT_MESSAGE).toMatch(/wird gerade geprüft/);
  });

  test("nach Ablauf der maximalen Wartezeit kommt die verständliche Abbruchmeldung", async () => {
    const client = { post: vi.fn(async () => { throw err503(10); }) };
    await expect(
      postWithRetry503(client, "/mobile/compare", { url: "x" },
        { maxWaitMs: 50 }),
    ).rejects.toMatchObject({ code: "timeout", message: TIMEOUT_MESSAGE });
  });

  test("andere Fehler (z.B. 400) werden NICHT wiederholt", async () => {
    const e = new Error("bad");
    e.response = { status: 400 };
    const client = { post: vi.fn(async () => { throw e; }) };
    await expect(
      postWithRetry503(client, "/mobile/compare", { url: "x" }, {}),
    ).rejects.toBe(e);
    expect(client.post).toHaveBeenCalledTimes(1);
  });
});

describe("checkLink (Hintergrundjob-Ablauf)", () => {
  test("bekanntes Inserat: sofort completed, kein Polling", async () => {
    const client = {
      post: vi.fn(async () => ({ data: { status: "completed", cached: true } })),
      get: vi.fn(),
    };
    const res = await checkLink(client, "https://kleinanzeigen.de/s-anzeige/1");
    expect(res.status).toBe("completed");
    expect(client.get).not.toHaveBeenCalled();
  });

  test("unbekanntes Inserat: Job wird gepollt bis completed", async () => {
    let polls = 0;
    const client = {
      post: vi.fn(async () => ({ data: { status: "queued", job_id: "j1" } })),
      get: vi.fn(async () => {
        polls += 1;
        return { data: { status: polls < 3 ? "processing" : "completed" } };
      }),
    };
    const waits = [];
    const res = await checkLink(client, "https://kleinanzeigen.de/s-anzeige/2",
      { pollMs: 5, onWait: (m) => waits.push(m), maxWaitMs: 5000 });
    expect(res.status).toBe("completed");
    expect(polls).toBe(3);
    expect(waits).toContain(WAIT_MESSAGE);
  });

  test("failed-Job wirft die Backend-Meldung", async () => {
    const client = {
      post: vi.fn(async () => ({ data: { status: "queued", job_id: "j2" } })),
      get: vi.fn(async () => ({
        data: { status: "failed", error: "Das Inserat ist nicht mehr verfügbar." },
      })),
    };
    await expect(
      checkLink(client, "u", { pollMs: 5, maxWaitMs: 5000 }),
    ).rejects.toThrow("nicht mehr verfügbar");
  });

  test("Zeitüberschreitung beim Polling liefert die Abbruchmeldung", async () => {
    const client = {
      post: vi.fn(async () => ({ data: { status: "queued", job_id: "j3" } })),
      get: vi.fn(async () => ({ data: { status: "processing" } })),
    };
    await expect(
      checkLink(client, "u", { pollMs: 5, maxWaitMs: 40 }),
    ).rejects.toMatchObject({ code: "timeout" });
  });

  test("weggeräumter Job (404) wird einmal neu geprüft — Cache-Treffer ist completed", async () => {
    const notFound = new Error("gone");
    notFound.response = { status: 404 };
    let posts = 0;
    const client = {
      post: vi.fn(async () => {
        posts += 1;
        return { data: posts === 1 ? { status: "queued", job_id: "j4" } : { status: "completed", cached: true } };
      }),
      get: vi.fn(async () => { throw notFound; }),
    };
    const res = await checkLink(client, "u", { pollMs: 5, maxWaitMs: 5000 });
    expect(res.status).toBe("completed");
    expect(posts).toBe(2);
  });

  test("weggeräumter Job (404) ohne Cache-Treffer wird nicht als completed geraten", async () => {
    const notFound = new Error("gone");
    notFound.response = { status: 404 };
    const client = {
      post: vi.fn(async () => ({ data: { status: "queued", job_id: "j5" } })),
      get: vi.fn(async () => { throw notFound; }),
    };
    await expect(checkLink(client, "u", { pollMs: 5, maxWaitMs: 5000 })).rejects.toThrow(/erneut/);
  });
});


// Wunsch Ahmad 18.09.2026: "X" waehrend des Abrufs — sofort raus aus dem
// Warten, ohne Fehlermeldung, und der Server erfaehrt die Job-Nummer.
describe("Abbrechen", () => {
  test("abgebrochenes Signal bricht sofort ab, ohne die Anfrage zu senden", async () => {
    const client = { post: vi.fn(), get: vi.fn() };
    const ctrl = new AbortController();
    ctrl.abort();
    await expect(checkLink(client, "https://x/1", { signal: ctrl.signal }))
      .rejects.toSatisfy((e) => istAbbruch(e));
    expect(client.post).not.toHaveBeenCalled();
  });

  test("Abbruch waehrend des Wartens beendet die Schleife und meldet die Job-Nummer", async () => {
    const ctrl = new AbortController();
    const client = {
      post: vi.fn(async () => ({ data: { status: "queued", job_id: "job-42" } })),
      get: vi.fn(async () => {
        ctrl.abort();                       // der Nutzer drueckt "X"
        return { data: { status: "queued" } };
      }),
    };
    const jobs = [];
    await expect(checkLink(client, "https://x/2",
      { signal: ctrl.signal, onJob: (id) => jobs.push(id), pollMs: 1 }))
      .rejects.toSatisfy((e) => istAbbruch(e));
    expect(jobs).toEqual(["job-42"]);       // ohne die Nummer kein Stopp am Server
  });

  test("ein abgebrochener axios-Fehler wird als Abbruch erkannt", () => {
    const e = new Error("canceled");
    e.code = "ERR_CANCELED";
    expect(istAbbruch(e)).toBe(true);
    expect(istAbbruch(new Error("anderes"))).toBe(false);
  });
});
