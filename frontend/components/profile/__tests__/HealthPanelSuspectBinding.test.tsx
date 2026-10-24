// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// FS-5 (Hazelnut build 4) — a recorded skill binding that crosses
// technical↔soft is ONE question with the pair's own two acts.
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HealthPanel, type ProfileHealth } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const HEALTH: ProfileHealth = {
  issues: [
    {
      id: "suspect_binding:sk-1:rest apis",
      thread: "suspect_binding",
      profile_mismatch_severity: "review",
      summary: "Is REST APIs the same as Mentoring?",
      field_ref: "skills.name",
      source_record_ref: "rec-1",
      section: "skills",
      skill_id: "sk-1",
      skill_name: "Mentoring",
      incoming_name: "REST APIs",
    },
  ],
  completeness: { score: 0.9, gaps: [], field_gaps: [] },
};

describe("HealthPanel — suspect_binding (FS-5)", () => {
  it("asks the pair question in German, never the English summary", () => {
    render(withIntl(<HealthPanel health={HEALTH} onResolve={vi.fn()} onAnswerBinding={vi.fn()} />, "de"));
    const card = screen.getByTestId("health-issue-suspect-binding");
    expect(card.textContent).toContain("Ist REST APIs dasselbe wie Mentoring?");
    expect(screen.queryByText(/Is REST APIs the same/)).toBeNull();
    expect(screen.getByText("Zuordnung")).toBeTruthy();
    expect(screen.queryByTestId("health-resolve")).toBeNull();
  });

  it.each([
    ["health-binding-same", true],
    ["health-binding-separate", false],
  ])("%s answers with same=%s", async (testId, same) => {
    const onAnswer = vi.fn().mockResolvedValue(true);
    render(withIntl(<HealthPanel health={HEALTH} onResolve={vi.fn()} onAnswerBinding={onAnswer} />, "de"));
    fireEvent.click(screen.getByTestId(testId));
    await waitFor(() => expect(onAnswer).toHaveBeenCalledTimes(1));
    expect(onAnswer.mock.calls[0][0].skill_id).toBe("sk-1");
    expect(onAnswer.mock.calls[0][1]).toBe(same);
  });

  it("says so when the act failed", async () => {
    const onAnswer = vi.fn().mockResolvedValue(false);
    render(withIntl(<HealthPanel health={HEALTH} onResolve={vi.fn()} onAnswerBinding={onAnswer} />, "en"));
    fireEvent.click(screen.getByTestId("health-binding-same"));
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("degrades to Resolve without a handler", () => {
    const onResolve = vi.fn();
    render(withIntl(<HealthPanel health={HEALTH} onResolve={onResolve} />, "en"));
    expect(screen.queryByTestId("health-binding-same")).toBeNull();
    fireEvent.click(screen.getByTestId("health-resolve"));
    expect(onResolve).toHaveBeenCalledTimes(1);
  });
});
