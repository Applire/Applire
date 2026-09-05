// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { existsSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { SHARE_TARGET_ACTION } from "@/lib/share-target";

import manifest from "../manifest";

const PUBLIC_DIR = path.resolve(__dirname, "../../public");

// US229 (E040, ADR-050 amendment 2026-09-05).
describe("web app manifest", () => {
  const m = manifest();

  it("declares the fields Chrome requires for an install prompt", () => {
    expect(m.name).toBeTruthy();
    expect(m.short_name).toBeTruthy();
    expect(m.start_url).toBe("/dashboard");
    expect(m.display).toBe("standalone");
    expect(m.theme_color).toBe("#003399");
    expect(m.background_color).toBe("#F5F7FA");
  });

  it("ships a 192 and a 512 icon plus a maskable pair", () => {
    const any = (m.icons ?? []).filter((i) => i.purpose === "any");
    const maskable = (m.icons ?? []).filter((i) => i.purpose === "maskable");
    expect(any.map((i) => i.sizes)).toContain("192x192");
    expect(any.map((i) => i.sizes)).toContain("512x512");
    expect(maskable.map((i) => i.sizes)).toEqual(["192x192", "512x512"]);
  });

  it("every declared icon exists on disk", () => {
    // A manifest that names a missing icon still parses, still validates, and
    // silently loses the install prompt — the one failure mode no schema check
    // can see. Assert against the file system, not the object.
    for (const icon of m.icons ?? []) {
      expect(existsSync(path.join(PUBLIC_DIR, icon.src)), `missing ${icon.src}`).toBe(true);
    }
  });

  it("declares a GET share target pointing at the route handler's own path", () => {
    // ADR-050 amendment clause 4b: GET, so the FastAPI surface stays untouched
    // (clause 3). A POST target would need a body-consuming endpoint.
    expect(m.share_target?.method).toBe("GET");
    expect(m.share_target?.action).toBe(SHARE_TARGET_ACTION);
    expect(m.share_target?.params).toEqual({ title: "title", text: "text", url: "url" });
  });

  it("does not declare a share target that could submit files", () => {
    // Clause 4c is "prefill only". A `files` param would mean multipart, which
    // means POST, which means a body-consuming endpoint — the exact shape the
    // amendment rules out.
    expect(m.share_target?.params).not.toHaveProperty("files");
    expect(m.share_target?.enctype).toBeUndefined();
  });
});
