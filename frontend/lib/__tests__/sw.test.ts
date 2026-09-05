// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { readFileSync } from "node:fs";
import path from "node:path";
import vm from "node:vm";

import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * US229 (E040) — the service worker's cache allowlist is the entire privacy
 * guard of ADR-050 amendment clause 4a, and it is the credited control on
 * System-FMEA `SF-FE.3` / `SF-FE.4`.
 *
 * The OQ lane cannot prove it: the worker registers in production builds only
 * (clause 4d) and the 390x844 lane runs the dev image. So this suite evaluates
 * `public/sw.js` in a fake `ServiceWorkerGlobalScope` and drives its own
 * handlers — the file is plain JS with no build step, so what runs here is
 * byte-for-byte what ships.
 */

const SW_PATH = path.resolve(__dirname, "../../public/sw.js");
const ORIGIN = "https://applire.example.test";

type Listener = (event: Record<string, unknown>) => void;

interface Harness {
  listeners: Record<string, Listener[]>;
  cacheOpen: ReturnType<typeof vi.fn>;
  cacheMatch: ReturnType<typeof vi.fn>;
  cacheDelete: ReturnType<typeof vi.fn>;
  cachePut: ReturnType<typeof vi.fn>;
  fetchMock: ReturnType<typeof vi.fn>;
  skipWaiting: ReturnType<typeof vi.fn>;
  clientsClaim: ReturnType<typeof vi.fn>;
  cacheKeys: string[];
}

function loadWorker(overrides: { cacheHit?: unknown; cacheNames?: string[] } = {}): Harness {
  const listeners: Record<string, Listener[]> = {};
  const cachePut = vi.fn(async () => undefined);
  const cacheOpen = vi.fn(async () => ({ put: cachePut, addAll: vi.fn(async () => undefined) }));
  const cacheMatch = vi.fn(async () => overrides.cacheHit);
  const cacheDelete = vi.fn(async () => true);
  const cacheKeys = overrides.cacheNames ?? [];
  const fetchMock = vi.fn(async () => ({
    ok: true,
    type: "basic",
    clone: () => ({ body: "clone" }),
    body: "network",
  }));
  const skipWaiting = vi.fn();
  const clientsClaim = vi.fn();

  const scope: Record<string, unknown> = {
    location: { origin: ORIGIN },
    addEventListener: (type: string, fn: Listener) => {
      (listeners[type] ??= []).push(fn);
    },
    skipWaiting,
    clients: { claim: clientsClaim },
    caches: {
      open: cacheOpen,
      match: cacheMatch,
      delete: cacheDelete,
      keys: vi.fn(async () => cacheKeys),
    },
    fetch: fetchMock,
    URL,
    Response,
    Promise,
    console,
  };
  scope.self = scope;
  vm.createContext(scope);
  vm.runInContext(readFileSync(SW_PATH, "utf8"), scope);

  return {
    listeners,
    cacheOpen,
    cacheMatch,
    cacheDelete,
    cachePut,
    fetchMock,
    skipWaiting,
    clientsClaim,
    cacheKeys,
  };
}

function fireFetch(
  h: Harness,
  url: string,
  init: { method?: string; mode?: string } = {},
): { responded: boolean; promise: Promise<unknown> | null } {
  let responded = false;
  let promise: Promise<unknown> | null = null;
  const event = {
    request: { url, method: init.method ?? "GET", mode: init.mode ?? "no-cors" },
    respondWith: (p: Promise<unknown>) => {
      responded = true;
      promise = p;
    },
  };
  for (const fn of h.listeners.fetch ?? []) fn(event);
  return { responded, promise };
}

describe("service worker — what it must never cache (SF-FE.3, ADR-050 cl. 4a)", () => {
  let h: Harness;
  beforeEach(() => {
    h = loadWorker();
  });

  it.each([
    ["the profile vault", `${ORIGIN}/api/profile`],
    ["a generated CV's PDF", `${ORIGIN}/api/cv/2b0f/pdf`],
    ["an ATS report", `${ORIGIN}/api/cv/2b0f/ats-report`],
    ["the settings payload", `${ORIGIN}/api/settings`],
    ["a backend static asset", `${ORIGIN}/static/templates/classic.png`],
    ["the share-target handler itself", `${ORIGIN}/share-target?url=https%3A%2F%2Fx.test`],
  ])("leaves %s entirely to the browser", (_label, url) => {
    const { responded } = fireFetch(h, url);
    expect(responded).toBe(false);
    expect(h.cacheOpen).not.toHaveBeenCalled();
    expect(h.cacheMatch).not.toHaveBeenCalled();
  });

  // One case per NEVER_TOUCH_PREFIXES entry, and `mode: "navigate"` on purpose.
  // Without it these paths are protected only by the handler's fall-through
  // default (not on the allowlist, not a navigation -> untouched), so deleting
  // the exclusion list would change nothing and every other assertion here would
  // stay green. Measured: gutting NEVER_TOUCH_PREFIXES reddens exactly these
  // three and nothing else. This is the seam test for the list itself.
  it.each([
    ["a document a user opened directly", `${ORIGIN}/api/cv/2b0f/pdf`],
    ["a backend static asset", `${ORIGIN}/static/templates/classic.png`],
    ["the share-target landing", `${ORIGIN}/share-target?url=https%3A%2F%2Fx.test`],
  ])("leaves a NAVIGATION to %s alone too", (_label, url) => {
    const { responded } = fireFetch(h, url, { mode: "navigate" });
    expect(responded).toBe(false);
    expect(h.cacheMatch).not.toHaveBeenCalled();
  });

  it("ignores every non-GET request", () => {
    for (const method of ["POST", "PATCH", "DELETE", "PUT"]) {
      const { responded } = fireFetch(h, `${ORIGIN}/dashboard`, { method, mode: "navigate" });
      expect(responded, method).toBe(false);
    }
    expect(h.cacheOpen).not.toHaveBeenCalled();
  });

  it("ignores cross-origin requests", () => {
    const { responded } = fireFetch(h, "https://fonts.googleapis.com/css2?family=X");
    expect(responded).toBe(false);
    expect(h.cacheOpen).not.toHaveBeenCalled();
  });

  it("never writes a successful navigation response to the cache", async () => {
    const { responded, promise } = fireFetch(h, `${ORIGIN}/dashboard`, { mode: "navigate" });
    expect(responded).toBe(true);
    await promise;
    expect(h.fetchMock).toHaveBeenCalled();
    expect(h.cachePut).not.toHaveBeenCalled();
  });

  it("does not handle a same-origin sub-resource that is not on the allowlist", () => {
    // e.g. a future /uploads/ path — network only, and no cache involvement.
    const { responded } = fireFetch(h, `${ORIGIN}/uploads/anna-cv.pdf`);
    expect(responded).toBe(false);
    expect(h.cacheOpen).not.toHaveBeenCalled();
  });
});

describe("service worker — what it may cache", () => {
  it("serves a hashed build asset from the cache when it has one", async () => {
    const h = loadWorker({ cacheHit: { body: "cached" } });
    const { responded, promise } = fireFetch(h, `${ORIGIN}/_next/static/chunks/abc123.js`);
    expect(responded).toBe(true);
    await expect(promise).resolves.toEqual({ body: "cached" });
    expect(h.fetchMock).not.toHaveBeenCalled();
  });

  it("fetches and stores a hashed build asset on a miss", async () => {
    const h = loadWorker();
    const { promise } = fireFetch(h, `${ORIGIN}/_next/static/chunks/abc123.js`);
    await promise;
    expect(h.fetchMock).toHaveBeenCalled();
    expect(h.cachePut).toHaveBeenCalledTimes(1);
  });

  it("stores an icon on a miss", async () => {
    const h = loadWorker();
    const { promise } = fireFetch(h, `${ORIGIN}/icons/icon-192.png`);
    await promise;
    expect(h.cachePut).toHaveBeenCalledTimes(1);
  });

  it("does not store a non-ok or opaque response", async () => {
    const h = loadWorker();
    h.fetchMock.mockResolvedValueOnce({ ok: false, type: "basic", clone: () => ({}) });
    await fireFetch(h, `${ORIGIN}/_next/static/chunks/x.js`).promise;
    h.fetchMock.mockResolvedValueOnce({ ok: true, type: "opaque", clone: () => ({}) });
    await fireFetch(h, `${ORIGIN}/_next/static/chunks/y.js`).promise;
    expect(h.cachePut).not.toHaveBeenCalled();
  });
});

describe("service worker — offline fallback and version hygiene (SF-FE.4)", () => {
  it("falls back to the static offline page when the network is gone", async () => {
    const h = loadWorker({ cacheHit: { body: "offline page" } });
    h.fetchMock.mockRejectedValueOnce(new Error("offline"));
    const { promise } = fireFetch(h, `${ORIGIN}/dashboard`, { mode: "navigate" });
    await expect(promise).resolves.toEqual({ body: "offline page" });
    expect(h.cacheMatch).toHaveBeenCalledWith("/offline.html");
  });

  it("still answers when even the offline page is missing", async () => {
    const h = loadWorker({ cacheHit: undefined });
    h.fetchMock.mockRejectedValueOnce(new Error("offline"));
    const { promise } = fireFetch(h, `${ORIGIN}/dashboard`, { mode: "navigate" });
    const response = (await promise) as Response;
    expect(response.status).toBe(503);
  });

  it("takes over immediately and evicts every cache but the current one", async () => {
    const h = loadWorker({ cacheNames: ["applire-shell-v0", "applire-shell-v1", "something-else"] });
    let waited: Promise<unknown> | null = null;
    for (const fn of h.listeners.activate ?? []) {
      fn({ waitUntil: (p: Promise<unknown>) => (waited = p) });
    }
    await waited;
    expect(h.cacheDelete).toHaveBeenCalledWith("applire-shell-v0");
    expect(h.cacheDelete).toHaveBeenCalledWith("something-else");
    expect(h.cacheDelete).not.toHaveBeenCalledWith("applire-shell-v1");
    expect(h.clientsClaim).toHaveBeenCalled();
  });

  it("precaches only the offline page and the icon set, and skips waiting", async () => {
    const h = loadWorker();
    let waited: Promise<unknown> | null = null;
    let addAllArg: string[] = [];
    h.cacheOpen.mockResolvedValueOnce({
      addAll: vi.fn(async (urls: string[]) => {
        addAllArg = urls;
      }),
      put: h.cachePut,
    });
    for (const fn of h.listeners.install ?? []) {
      fn({ waitUntil: (p: Promise<unknown>) => (waited = p) });
    }
    await waited;
    expect(addAllArg).toEqual([
      "/offline.html",
      "/icons/icon-192.png",
      "/icons/icon-512.png",
      "/icons/icon-maskable-192.png",
      "/icons/icon-maskable-512.png",
      "/icons/apple-touch-icon.png",
    ]);
    // Nothing under /api or /static may ever appear in the precache list.
    expect(addAllArg.some((u) => u.startsWith("/api") || u.startsWith("/static/"))).toBe(false);
    expect(h.skipWaiting).toHaveBeenCalled();
  });
});
