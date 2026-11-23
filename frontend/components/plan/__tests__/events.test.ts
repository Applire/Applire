// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ALLOWANCE_REFUSED_EVENT, handleAllowanceRefusal, type AllowanceRefusalDetail } from "../events";

const body = (status: number, detail: object) => new Response(JSON.stringify({ detail }), { status });
let seen: AllowanceRefusalDetail[];
const listener = (e: Event) => seen.push((e as CustomEvent<AllowanceRefusalDetail>).detail);

beforeEach(() => {
  seen = [];
  window.addEventListener(ALLOWANCE_REFUSED_EVENT, listener);
});
afterEach(() => window.removeEventListener(ALLOWANCE_REFUSED_EVENT, listener));

describe("handleAllowanceRefusal", () => {
  it("maps allowance_exhausted with its action and renewal date", async () => {
    const res = body(409, { error_code: "allowance_exhausted", action: "profile_import", renews_on: "2026-12-01", message: "x" });
    expect(await handleAllowanceRefusal(res, "/api/job/analyze")).toBe(true);
    expect(seen).toEqual([{ code: "allowance_exhausted", action: "profile_import", renews_on: "2026-12-01" }]);
  });

  it.each([
    [409, "plan_required"],
    [409, "instance_daily_limit"],
    [503, "allowance_unavailable"],
  ])("maps %s %s", async (status, code) => {
    expect(await handleAllowanceRefusal(body(status, { error_code: code }), "/api/x")).toBe(true);
    expect(seen[0]).toEqual({ code, action: null, renews_on: null });
  });

  it("leaves the caller's response unread (reads a clone)", async () => {
    const res = body(409, { error_code: "plan_required" });
    await handleAllowanceRefusal(res, "/api/x");
    expect(res.bodyUsed).toBe(false);
  });

  it("ignores other 409s, a 503 with another code and non-API URLs", async () => {
    expect(await handleAllowanceRefusal(body(409, { error_code: "provider_key_required" }), "/api/x")).toBe(false);
    expect(await handleAllowanceRefusal(body(503, { error_code: "something_else" }), "/api/x")).toBe(false);
    expect(await handleAllowanceRefusal(body(409, { error_code: "plan_required" }), "https://example.org/other")).toBe(false);
    expect(await handleAllowanceRefusal(body(503, { error_code: "plan_required" }), "/api/x")).toBe(false);
    expect(await handleAllowanceRefusal(new Response("nope", { status: 409 }), "/api/x")).toBe(false);
    expect(seen).toEqual([]);
  });

});
