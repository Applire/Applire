// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  installAuthFetch,
  isApiUrl,
  markSessionKnown,
  resetAuthFetchStateForTests,
  suppressAuthRedirect,
} from "../fetch-patch";

const UNAUTH = { detail: { error_code: "unauthenticated", message: "Sign in to continue." } };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const realFetch = window.fetch;
const realLocation = window.location;
let assign: ReturnType<typeof vi.fn>;

function setLocation(pathname: string, search = "") {
  assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname, search, origin: "http://localhost:3000", assign },
  });
}

async function flush() {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
}

describe("installAuthFetch — global 401 handler (US330)", () => {
  beforeEach(() => {
    resetAuthFetchStateForTests();
    setLocation("/flow/6f1c/cv", "?tab=review");
  });
  afterEach(() => {
    window.fetch = realFetch;
    Object.defineProperty(window, "location", { configurable: true, value: realLocation });
  });

  function install(res: Response) {
    window.fetch = vi.fn(async () => res) as unknown as typeof fetch;
    installAuthFetch();
  }

  it("redirects to /login?next=<path+query> on 401 unauthenticated from an API URL", async () => {
    install(jsonResponse(401, UNAUTH));
    const res = await window.fetch("/api/profile");
    expect(res.status).toBe(401); // caller still gets the response
    await flush();
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign).toHaveBeenCalledWith("/login?next=%2Fflow%2F6f1c%2Fcv%3Ftab%3Dreview");
  });

  it("adds expired=1 once a session was known", async () => {
    markSessionKnown(true);
    install(jsonResponse(401, UNAUTH));
    await window.fetch("/api/profile");
    await flush();
    expect(assign.mock.calls[0][0]).toContain("expired=1");
  });

  it("ignores a 401 with another error code (invalid_credentials at login)", async () => {
    install(jsonResponse(401, { detail: { error_code: "invalid_credentials", message: "x" } }));
    await window.fetch("/api/profile");
    await flush();
    expect(assign).not.toHaveBeenCalled();
  });

  it("ignores non-401 statuses and non-API URLs", async () => {
    install(jsonResponse(403, UNAUTH));
    await window.fetch("/api/profile");
    window.fetch = vi.fn(async () => jsonResponse(401, UNAUTH)) as unknown as typeof fetch;
    installAuthFetch();
    await window.fetch("https://elsewhere.example/api/x");
    await window.fetch("/_next/static/x.js");
    await flush();
    expect(assign).not.toHaveBeenCalled();
  });

  it("never fires on the signed-out pages (no loop)", async () => {
    setLocation("/login", "?next=%2Fdashboard");
    install(jsonResponse(401, UNAUTH));
    await window.fetch("/api/settings");
    await flush();
    expect(assign).not.toHaveBeenCalled();
  });

  it("stays quiet while a deliberate sign-out navigates", async () => {
    suppressAuthRedirect(true);
    install(jsonResponse(401, UNAUTH));
    await window.fetch("/api/profile");
    await flush();
    expect(assign).not.toHaveBeenCalled();
  });

  it("redirects only once for a burst of 401s", async () => {
    install(jsonResponse(401, UNAUTH));
    await Promise.all([window.fetch("/api/a"), window.fetch("/api/b"), window.fetch("/api/c")]);
    await flush();
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it("is idempotent — installing twice wraps once", async () => {
    const inner = vi.fn(async () => jsonResponse(200, {}));
    window.fetch = inner as unknown as typeof fetch;
    installAuthFetch();
    const first = window.fetch;
    installAuthFetch();
    expect(window.fetch).toBe(first);
    await window.fetch("/api/x");
    expect(inner).toHaveBeenCalledTimes(1);
  });

  it("recognises relative, API_BASE-prefixed and same-origin absolute API URLs", () => {
    expect(isApiUrl("/api/x", "http://localhost:3000")).toBe(true);
    expect(isApiUrl("http://localhost:3000/api/x", "http://localhost:3000")).toBe(true);
    expect(isApiUrl("http://other:3000/api/x", "http://localhost:3000")).toBe(false);
    expect(isApiUrl("/apix", "http://localhost:3000")).toBe(false);
  });
});
