// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { ALLOWANCE_REFUSED_EVENT, handleAllowanceRefusal, isAllowanceRefusal, type AllowanceRefusalDetail } from "../events";

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

  it("carries the instance refusal's wording flag (adv/plans N5)", async () => {
    expect(await handleAllowanceRefusal(body(409, { error_code: "instance_daily_limit", purchasing_available: true }), "/api/x")).toBe(true);
    expect(await handleAllowanceRefusal(body(503, { error_code: "allowance_unavailable", purchasing_available: false }), "/api/x")).toBe(true);
    expect(seen.map((d) => d.purchasing_available)).toEqual([true, false]);
  });

  it.each([
    [409, "plan_required"],
    [409, "instance_daily_limit"],
    [503, "allowance_unavailable"],
  ])("maps %s %s", async (status, code) => {
    expect(await handleAllowanceRefusal(body(status, { error_code: code }), "/api/x")).toBe(true);
    expect(seen[0]).toEqual({ code, action: null, renews_on: null });
  });

  // adv/plans F8: a caller that reads its body at once (GenerateCoverLetterModal
  // does `await res.json()`) must not cost the event its action and date.
  it("keeps action and renews_on when the caller reads the body at once", async () => {
    const res = body(409, { error_code: "allowance_exhausted", action: "profile_import", renews_on: "2026-12-01" });
    const raised = handleAllowanceRefusal(res, "/api/profile/import-jobs");
    await res.json(); // the caller, without waiting for the event
    expect(await raised).toBe(true);
    expect(seen).toEqual([{ code: "allowance_exhausted", action: "profile_import", renews_on: "2026-12-01" }]);
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

describe("isAllowanceRefusal (adv/plans F9: callers stay silent on these)", () => {
  it("is true for the four refusal codes and leaves the body unread", async () => {
    for (const [status, code] of [[409, "allowance_exhausted"], [409, "plan_required"], [409, "instance_daily_limit"], [503, "allowance_unavailable"]] as const) {
      const res = body(status, { error_code: code });
      expect(await isAllowanceRefusal(res)).toBe(true);
      expect(res.bodyUsed).toBe(false);
    }
  });

  it("is false for other failures", async () => {
    expect(await isAllowanceRefusal(body(409, { error_code: "provider_key_required" }))).toBe(false);
    expect(await isAllowanceRefusal(body(500, { error_code: "internal_error" }))).toBe(false);
    expect(await isAllowanceRefusal(new Response("nope", { status: 409 }))).toBe(false);
  });
});
