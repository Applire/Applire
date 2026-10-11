// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi, afterEach } from "vitest";
import { loadActiveSchemeVars } from "../active-theme";

// US357: the request's cookies. Default: no request scope (signed out).
const cookieJar = vi.hoisted(() => ({ value: null as string | null, throws: true }));
vi.mock("next/headers", () => ({
  cookies: async () => {
    if (cookieJar.throws) throw new Error("cookies() outside a request scope");
    return { get: (name: string) => (name === "applire_session" && cookieJar.value ? { value: cookieJar.value } : undefined) };
  },
}));

afterEach(() => {
  vi.restoreAllMocks();
  cookieJar.value = null;
  cookieJar.throws = true;
});

describe("loadActiveSchemeVars — server-side pre-paint fetch", () => {
  it("returns the active scheme's derived vars on success", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: true,
      status: 200,
      json: () =>
        Promise.resolve({
          id: "x",
          name: "EU Blue",
          derived: { "--color-primary": "#003399", "--color-gold": "#fecb00" },
        }),
    } as Response);

    const vars = await loadActiveSchemeVars();

    expect(vars).toEqual({ "--color-primary": "#003399", "--color-gold": "#fecb00" });
  });

  it("returns null when no scheme is active (404) so globals.css is used", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: "No active color scheme found" }),
    } as Response);

    expect(await loadActiveSchemeVars()).toBeNull();
  });

  it("returns null when the backend is unreachable (network error)", async () => {
    vi.spyOn(global, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));

    expect(await loadActiveSchemeVars()).toBeNull();
  });
});

describe("loadActiveSchemeVars — the signed-in person's scheme (US357)", () => {
  const ok = (primary: string) =>
    ({ ok: true, status: 200, json: () => Promise.resolve({ derived: { "--color-primary": primary } }) }) as Response;

  it("with a session cookie: asks /effective and forwards ONLY the session cookie", async () => {
    cookieJar.throws = false;
    cookieJar.value = "tok123";
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(ok("#2e7d32"));
    expect(await loadActiveSchemeVars()).toEqual({ "--color-primary": "#2e7d32" });
    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/color-schemes/effective");
    expect(init.headers).toEqual({ Cookie: "applire_session=tok123" });
    expect(init.cache).toBe("no-store");
  });

  it("a stale session: /effective fails, the public default is painted without the cookie", async () => {
    cookieJar.throws = false;
    cookieJar.value = "stale";
    const spy = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce({ ok: false, status: 401, json: () => Promise.resolve({}) } as Response)
      .mockResolvedValueOnce(ok("#1b4f72"));
    expect(await loadActiveSchemeVars()).toEqual({ "--color-primary": "#1b4f72" });
    const [url, init] = spy.mock.calls[1] as [string, RequestInit];
    expect(url).toContain("/api/admin/color-schemes/active");
    expect(init.headers).toBeUndefined();
  });

  it("no session cookie: one call, to the public default", async () => {
    cookieJar.throws = false;
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(ok("#1b4f72"));
    await loadActiveSchemeVars();
    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0][0])).toContain("/api/admin/color-schemes/active");
  });
});
