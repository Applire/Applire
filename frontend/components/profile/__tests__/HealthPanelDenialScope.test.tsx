// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// G-5 / ruling V-5 (c) (Hazelnut build 4) — a parked `denial_scope` ask is
// answered in the Health hub with its own options; "Angabe zurücknehmen" IS
// the act (no second dialog). Other confirmation families keep "Beheben".
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HealthPanel, type HealthIssue, type ProfileHealth } from "../HealthPanel";
import { withIntl } from "@/lib/test-utils/with-intl";

const ASK: HealthIssue = {
  id: "confirmation:conf-1",
  thread: "confirmation",
  profile_mismatch_severity: "review",
  summary: "By your own answer, “SAP PP” does not apply to you.",
  field_ref: null,
  source_record_ref: "denial_scope",
  question_i18n: { de: "Stimmt deine frühere Angabe noch?", en: "Is your earlier answer still right?" },
  confirmation_id: "conf-1",
  confirmation_kind: "denial_scope",
  options: ["Yes, my answer is right", "No, the CV is right: withdraw my answer"],
  options_i18n: [
    { de: "Ja, meine Angabe stimmt", en: "Yes, my answer is right" },
    { de: "Nein, der Lebenslauf stimmt: Angabe zurücknehmen", en: "No, the CV is right: withdraw my answer" },
  ],
};

const health = (issue: HealthIssue): ProfileHealth => ({
  issues: [issue],
  completeness: { score: 0.9, gaps: [], field_gaps: [] },
});

describe("HealthPanel — denial_scope ask (V-5 c)", () => {
  it("offers the ask's own options in the reader's language, not Beheben", () => {
    render(withIntl(<HealthPanel health={health(ASK)} onResolve={vi.fn()} onAnswerConfirmation={vi.fn()} />, "de"));
    const options = screen.getAllByTestId("health-confirmation-option").map((b) => b.textContent);
    expect(options).toEqual(["Ja, meine Angabe stimmt", "Nein, der Lebenslauf stimmt: Angabe zurücknehmen"]);
    expect(screen.queryByTestId("health-resolve")).toBeNull();
  });

  it("posts the PLAIN option of the button pressed — the answer is the act", async () => {
    const onAnswer = vi.fn().mockResolvedValue(true);
    render(withIntl(<HealthPanel health={health(ASK)} onResolve={vi.fn()} onAnswerConfirmation={onAnswer} />, "de"));
    fireEvent.click(screen.getAllByTestId("health-confirmation-option")[1]);
    await waitFor(() => expect(onAnswer).toHaveBeenCalledTimes(1));
    expect(onAnswer.mock.calls[0][0].confirmation_id).toBe("conf-1");
    expect(onAnswer.mock.calls[0][1]).toBe("No, the CV is right: withdraw my answer");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says so when the act failed", async () => {
    const onAnswer = vi.fn().mockResolvedValue(false);
    render(withIntl(<HealthPanel health={health(ASK)} onResolve={vi.fn()} onAnswerConfirmation={onAnswer} />, "en"));
    fireEvent.click(screen.getAllByTestId("health-confirmation-option")[0]);
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("an ask without options keeps Beheben", () => {
    const other = { ...ASK, confirmation_kind: "near_duplicate_role", options: null, options_i18n: null };
    render(withIntl(<HealthPanel health={health(other)} onResolve={vi.fn()} onAnswerConfirmation={vi.fn()} />, "de"));
    expect(screen.queryByTestId("health-confirmation-option")).toBeNull();
    expect(screen.getByTestId("health-resolve")).toBeTruthy();
  });
});
