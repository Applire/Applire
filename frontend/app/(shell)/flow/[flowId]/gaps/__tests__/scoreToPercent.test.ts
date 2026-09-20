// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * #675 / ruling B-1 (2026-09-20) — a match score of 0 is a real score.
 *
 * The backend clamp no longer holds the headline up when the candidate records a
 * denial, so an analysis in which every weighted requirement is `denied` now
 * genuinely publishes `match_score: 0.0`. All four reads on the gaps page were
 * `score ? Math.round(score * 100) : fallback`, which is a truthiness check: a
 * real 0 took the fallback branch and the page kept displaying the previous,
 * higher percentage — the same "headline contradicts the analysis behind it"
 * shape the backend fix removed, one layer up.
 */
import { describe, it, expect } from "vitest";
import { scoreToPercent } from "../page";

describe("scoreToPercent", () => {
  it("renders a genuine zero as 0, never as the fallback", () => {
    expect(scoreToPercent(0, 64)).toBe(0);
  });

  it("falls back only when there is no score at all", () => {
    expect(scoreToPercent(null, 64)).toBe(64);
    expect(scoreToPercent(undefined, 64)).toBe(64);
  });

  it("rounds a fraction to a whole percentage", () => {
    expect(scoreToPercent(0.6292, 0)).toBe(63);
    expect(scoreToPercent(0.6404, 0)).toBe(64);
    expect(scoreToPercent(1, 0)).toBe(100);
  });
});
