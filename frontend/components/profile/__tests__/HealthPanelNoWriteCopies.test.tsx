// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Ruling M5.1.4 / P2-2 added three `health.notAppliedReason.*` catalog keys
 * (`no_write`, `no_write_already_known`, `no_write_question_only`) so the
 * M-1c no-write witness's own model-supplied `empty_reason` reaches the
 * candidate as a clause inside `health.notAppliedSummary`'s sentence
 * (`HealthPanel.tsx::describeNotApplied`). Neither the catalog additions nor
 * this composition were exercised by any automated test before this file
 * (grep across `components/profile/__tests__/` at WP-P2 adversarial pass
 * time, 2026-09-11, found zero references to `notAppliedReason` or
 * `no_write`) — this pins that the three new DE/EN copies actually compose
 * into a grammatical sentence, singly and joined ("; ", the same separator
 * the four pre-existing reasons already use).
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { useTranslations } from "next-intl";
import { describeNotApplied, type HealthIssue } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

function _issue(reasons: string[]): HealthIssue {
  return {
    id: "not_applied:turn-1",
    thread: "not_applied",
    profile_mismatch_severity: "review",
    summary: "fallback summary, should not be used here",
    not_applied_count: reasons.length,
    not_applied_source: "interview",
    not_applied_reasons: reasons,
    not_applied_labels: ["habe ich zuletzt bei Musterfirma gemacht"],
  };
}

function Probe({ reasons }: { reasons: string[] }) {
  const t = useTranslations("health");
  const tProfile = useTranslations("profile");
  return <div data-testid="out">{describeNotApplied(_issue(reasons), t, tProfile)}</div>;
}

describe("describeNotApplied — M5.1.4 no_write reason copies", () => {
  it.each([
    ["en", "no_write", "nothing was recorded from what you said"],
    ["en", "no_write_already_known", "that is already in your profile, as stated"],
    ["en", "no_write_question_only", "your question arrived — nothing was changed for it"],
    ["de", "no_write", "von dem, was du gesagt hast, wurde nichts übernommen"],
    ["de", "no_write_already_known", "das steht so schon in deinem Profil"],
    ["de", "no_write_question_only", "deine Frage ist angekommen"],
  ] as const)("locale=%s reason=%s renders its own copy, not the raw key", (locale, reason, expectedSubstring) => {
    render(withIntl(<Probe reasons={[reason]} />, locale));
    const text = screen.getByTestId("out").textContent ?? "";
    expect(text).toContain(expectedSubstring);
    // Never falls back to the raw enum value itself standing alone as the reason clause.
    expect(text.includes(`— ${reason}.`) || text.includes(`(${reason})`)).toBe(false);
  });

  it("joins two no_write reasons with '; ', the same separator the four pre-existing reasons use", () => {
    render(withIntl(<Probe reasons={["no_write_already_known", "no_write_question_only"]} />, "de"));
    const text = screen.getByTestId("out").textContent ?? "";
    expect(text).toContain("das steht so schon in deinem Profil; deine Frage ist angekommen");
  });

  it("an unrecognised reason value falls back to rendering the raw string (never crashes, never blank)", () => {
    render(withIntl(<Probe reasons={["some_future_reason_this_catalog_does_not_have"]} />, "en"));
    const text = screen.getByTestId("out").textContent ?? "";
    expect(text).toContain("some_future_reason_this_catalog_does_not_have");
  });
});
