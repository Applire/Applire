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

import { describe, it, expect } from "vitest";
import { resolveFlowRedirect } from "../flow-routing";

const FLOW = "f1";

describe("resolveFlowRedirect", () => {
  // ── URL matches the backend step → no redirect ───────────────────────────

  it("returns null when the URL already matches the step", () => {
    expect(resolveFlowRedirect(FLOW, "/flow/f1/import", "cv_import")).toBeNull();
    expect(resolveFlowRedirect(FLOW, "/flow/f1/gaps", "gap_analysis")).toBeNull();
    expect(resolveFlowRedirect(FLOW, "/flow/f1/interview", "interview")).toBeNull();
    expect(resolveFlowRedirect(FLOW, "/flow/f1/cv", "cv_generation")).toBeNull();
    expect(resolveFlowRedirect(FLOW, "/flow/f1/cv", "complete")).toBeNull();
  });

  it("returns null on the flow index while at jd_analysis", () => {
    expect(resolveFlowRedirect(FLOW, "/flow/f1", "jd_analysis")).toBeNull();
  });

  // ── Mismatched URL → redirect to the step's route ────────────────────────

  it("redirects a mismatched sub-route to the backend step", () => {
    expect(resolveFlowRedirect(FLOW, "/flow/f1/interview", "cv_import")).toBe(
      "/flow/f1/import",
    );
    expect(resolveFlowRedirect(FLOW, "/flow/f1", "interview")).toBe(
      "/flow/f1/interview",
    );
    expect(resolveFlowRedirect(FLOW, "/flow/f1", "complete")).toBe("/flow/f1/cv");
  });

  // ── jd_analysis hole (UAT bug): sub-routes must bounce to the index ──────

  it("redirects any sub-route to the flow index while at jd_analysis", () => {
    expect(resolveFlowRedirect(FLOW, "/flow/f1/interview", "jd_analysis")).toBe(
      "/flow/f1",
    );
    expect(resolveFlowRedirect(FLOW, "/flow/f1/import", "jd_analysis")).toBe(
      "/flow/f1",
    );
    expect(resolveFlowRedirect(FLOW, "/flow/f1/cv", "jd_analysis")).toBe("/flow/f1");
  });

  // ── Side routes are valid at any step ────────────────────────────────────

  it("never redirects side routes", () => {
    expect(resolveFlowRedirect(FLOW, "/flow/f1/cover-letter", "jd_analysis")).toBeNull();
    expect(resolveFlowRedirect(FLOW, "/flow/f1/cover-letter", "interview")).toBeNull();
    expect(resolveFlowRedirect(FLOW, "/flow/f1/cover-letter", "complete")).toBeNull();
  });

  // ── Unknown steps: fail open (no redirect loop) ──────────────────────────

  it("returns null for unknown steps", () => {
    expect(resolveFlowRedirect(FLOW, "/flow/f1/interview", "some_future_step")).toBeNull();
  });
});
