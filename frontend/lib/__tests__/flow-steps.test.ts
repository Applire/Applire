// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/** Ruling UX-3 (M7) — a skipped interview is never drawn as done. */
import { describe, expect, it } from "vitest";
import { FLOW_STEP_ORDER, flowStepHref, flowStepState } from "../flow-steps";

const states = (flow: Parameters<typeof flowStepState>[1], segment: string) =>
  FLOW_STEP_ORDER.map((s) => flowStepState(s, flow, segment));

describe("flowStepState", () => {
  it("Generate CV Now from the gaps page: the interview is skipped, not done", () => {
    expect(states({ current_step: "cv_generation", interview_summary: null }, "cv")).toEqual([
      "done",
      "done",
      "skipped",
      "active",
    ]);
  });

  it("an interview started and left open, then Generate CV Now: still skipped", () => {
    expect(
      flowStepState("interview", { current_step: "cv_generation", interview_summary: { status: "active" } }, "cv"),
    ).toBe("skipped");
  });

  it("a completed interview (any ending, incl. Ich bin fertig) is done", () => {
    expect(states({ current_step: "cv_generation", interview_summary: { status: "complete" } }, "cv")).toEqual([
      "done",
      "done",
      "done",
      "active",
    ]);
  });

  it("steps not reached yet stay pending; the current one is active", () => {
    expect(states({ current_step: "gap_analysis" }, "gaps")).toEqual(["done", "active", "pending", "pending"]);
  });

  it("a finished flow (current_step complete) lies past every step", () => {
    expect(
      states({ current_step: "complete", interview_summary: { status: "complete" } }, "cover-letter"),
    ).toEqual(["done", "done", "done", "active"]);
  });

  it("before the flow state loaded nothing is done", () => {
    expect(states(null, "gaps")).toEqual(["pending", "active", "pending", "pending"]);
  });
});

/** R2-3 (ruling G-1): which passed pill links where. */
describe("flowStepHref", () => {
  const hrefs = (flow: Parameters<typeof flowStepHref>[2]) =>
    FLOW_STEP_ORDER.map((s) => flowStepHref("f1", s, flow));

  it("a finished flow: gaps + the (skipped) interview lead to the gap view, the CV to the documents", () => {
    expect(hrefs({ current_step: "complete", interview_summary: null })).toEqual([
      undefined,
      "/flow/f1/gaps",
      "/flow/f1/gaps",
      "/flow/f1/cv",
    ]);
  });

  it("a DONE interview opens its summary (783:195)", () => {
    expect(hrefs({ current_step: "cv_generation", interview_summary: { status: "complete" } })[2]).toBe(
      "/flow/f1/interview",
    );
  });

  it("during the interview only the passed gap view links", () => {
    expect(hrefs({ current_step: "interview", interview_summary: { status: "active" } })).toEqual([
      undefined,
      "/flow/f1/gaps",
      undefined,
      undefined,
    ]);
  });

  it("nothing links before the gap analysis exists, or without a flow", () => {
    expect(hrefs({ current_step: "cv_import" })).toEqual([undefined, undefined, undefined, undefined]);
    expect(hrefs(null)).toEqual([undefined, undefined, undefined, undefined]);
  });
});
