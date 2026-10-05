// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, expect, it } from "vitest";

import { isAuthPage, isSharePrefillNext, loginPathFor, readErrorCode, safeNextPath } from "../api";

describe("safeNextPath — open-redirect guard for ?next=", () => {
  it.each([
    ["/flow/6f1c/cv", "/flow/6f1c/cv"],
    ["/dashboard?jd_url=https%3A%2F%2Fx", "/dashboard?jd_url=https%3A%2F%2Fx"],
    ["//evil.example/x", "/"],
    ["/\\evil.example", "/"],
    ["https://evil.example/", "/"],
    ["javascript:alert(1)", "/"],
    ["", "/"],
    [null, "/"],
    ["/login?next=/x", "/"],
    ["/setup", "/"],
    ["/dash\nboard", "/"],
  ])("%s → %s", (raw, expected) => {
    expect(safeNextPath(raw as string | null)).toBe(expected);
  });
});

describe("loginPathFor", () => {
  it("carries path + query, never a fragment", () => {
    expect(loginPathFor({ pathname: "/flow/1/cv", search: "?a=1" })).toBe("/login?next=%2Fflow%2F1%2Fcv%3Fa%3D1");
  });
  it("no next for the root", () => {
    expect(loginPathFor({ pathname: "/", search: "" })).toBe("/login");
  });
  it("expired flag", () => {
    expect(loginPathFor({ pathname: "/", search: "" }, { expired: true })).toBe("/login?expired=1");
  });
});

describe("isAuthPage", () => {
  it("matches the five signed-out pages only", () => {
    for (const p of ["/login", "/setup", "/invite", "/reset", "/forgot"]) expect(isAuthPage(p)).toBe(true);
    expect(isAuthPage("/loginx")).toBe(false);
    expect(isAuthPage("/dashboard")).toBe(false);
  });
});

describe("isSharePrefillNext", () => {
  it("recognises the share-target deep link", () => {
    expect(isSharePrefillNext("/dashboard?jd_url=https%3A%2F%2Fx")).toBe(true);
    expect(isSharePrefillNext("/dashboard?jd_text=hello")).toBe(true);
    expect(isSharePrefillNext("/share-target?url=x")).toBe(true);
    expect(isSharePrefillNext("/dashboard")).toBe(false);
    expect(isSharePrefillNext("/flow/1/cv")).toBe(false);
  });
});

describe("readErrorCode", () => {
  it("reads detail.error_code and leaves the body readable", async () => {
    const res = new Response(JSON.stringify({ detail: { error_code: "setup_done", message: "m" } }), { status: 409 });
    expect(await readErrorCode(res)).toBe("setup_done");
    expect((await res.json()).detail.message).toBe("m");
  });
  it("null for a plain-string detail or no JSON", async () => {
    expect(await readErrorCode(new Response(JSON.stringify({ detail: "cv not found" })))).toBeNull();
    expect(await readErrorCode(new Response("oops"))).toBeNull();
  });
});
