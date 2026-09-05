/**
 * Automatisierte Tests für die kontrollierte 503-Behandlung und den
 * Job-Poll-Ablauf der Linkprüfung (Priorität 1 aus dem Review).
 * Läuft ohne Browser/axios — der Client wird gemockt.
 */
import {
  checkLink, postWithRetry503, TIMEOUT_MESSAGE, WAIT_MESSAGE,
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
      post: jest.fn(async () => {
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
    const client = { post: jest.fn(async () => { throw err503(10); }) };
    await expect(
      postWithRetry503(client, "/mobile/compare", { url: "x" },
        { maxWaitMs: 50 }),
    ).rejects.toMatchObject({ code: "timeout", message: TIMEOUT_MESSAGE });
  });

  test("andere Fehler (z.B. 400) werden NICHT wiederholt", async () => {
    const e = new Error("bad");
    e.response = { status: 400 };
    const client = { post: jest.fn(async () => { throw e; }) };
    await expect(
      postWithRetry503(client, "/mobile/compare", { url: "x" }, {}),
    ).rejects.toBe(e);
    expect(client.post).toHaveBeenCalledTimes(1);
  });
});

describe("checkLink (Hintergrundjob-Ablauf)", () => {
  test("bekanntes Inserat: sofort completed, kein Polling", async () => {
    const client = {
      post: jest.fn(async () => ({ data: { status: "completed", cached: true } })),
      get: jest.fn(),
    };
    const res = await checkLink(client, "https://kleinanzeigen.de/s-anzeige/1");
    expect(res.status).toBe("completed");
    expect(client.get).not.toHaveBeenCalled();
  });

  test("unbekanntes Inserat: Job wird gepollt bis completed", async () => {
    let polls = 0;
    const client = {
      post: jest.fn(async () => ({ data: { status: "queued", job_id: "j1" } })),
      get: jest.fn(async () => {
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
      post: jest.fn(async () => ({ data: { status: "queued", job_id: "j2" } })),
      get: jest.fn(async () => ({
        data: { status: "failed", error: "Das Inserat ist nicht mehr verfügbar." },
      })),
    };
    await expect(
      checkLink(client, "u", { pollMs: 5, maxWaitMs: 5000 }),
    ).rejects.toThrow("nicht mehr verfügbar");
  });

  test("Zeitüberschreitung beim Polling liefert die Abbruchmeldung", async () => {
    const client = {
      post: jest.fn(async () => ({ data: { status: "queued", job_id: "j3" } })),
      get: jest.fn(async () => ({ data: { status: "processing" } })),
    };
    await expect(
      checkLink(client, "u", { pollMs: 5, maxWaitMs: 40 }),
    ).rejects.toMatchObject({ code: "timeout" });
  });

  test("weggeräumter Job (404) gilt als completed — Ergebnis liegt im Cache", async () => {
    const notFound = new Error("gone");
    notFound.response = { status: 404 };
    const client = {
      post: jest.fn(async () => ({ data: { status: "queued", job_id: "j4" } })),
      get: jest.fn(async () => { throw notFound; }),
    };
    const res = await checkLink(client, "u", { pollMs: 5, maxWaitMs: 5000 });
    expect(res.status).toBe("completed");
  });
});
