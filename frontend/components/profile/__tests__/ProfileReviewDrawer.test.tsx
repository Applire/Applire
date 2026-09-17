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

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProfileReviewDrawer, keepBothApplies } from "../ProfileReviewDrawer";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("@/lib/api/profileReview", () => ({
  startProfileReview: vi.fn(),
  sendProfileReviewMessage: vi.fn(),
}));

import {
  startProfileReview,
  sendProfileReviewMessage,
} from "@/lib/api/profileReview";

const startMock = vi.mocked(startProfileReview);
const sendMock = vi.mocked(sendProfileReviewMessage);

describe("ProfileReviewDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("launches the session and shows the first conflict question with choices", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Your profile has two values for personal_info.name. Which is correct?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: ["Keep current: Max Muster", "Use imported: Markus Brandt"],
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));

    await waitFor(() =>
      expect(screen.getByText(/two values for personal_info\.name/)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /Keep current: Max Muster/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Use imported: Markus Brandt/ })).toBeInTheDocument();
  });

  it("sends the chosen value and renders the next question", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Which name is correct?",
      gaps_total: 2,
      gaps_remaining: 2,
      choices: ["Keep current: Max", "Use imported: Markus"],
    });
    sendMock.mockResolvedValue({
      complete: false,
      question: "Which email is correct?",
      choices: ["Keep current: a@x", "Use imported: b@x"],
      gaps_remaining: 1,
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByRole("button", { name: /Keep current: Max/ }));

    fireEvent.click(screen.getByRole("button", { name: /Keep current: Max/ }));

    await waitFor(() =>
      expect(screen.getByText("Which email is correct?")).toBeInTheDocument(),
    );
    expect(sendMock).toHaveBeenCalledWith("s1", "Keep current: Max");
  });

  it("shows a done state when the review completes", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Which name is correct?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: ["Keep current: Max", "Use imported: Markus"],
    });
    sendMock.mockResolvedValue({
      complete: true,
      question: null,
      choices: null,
      gaps_remaining: 0,
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByRole("button", { name: /Use imported: Markus/ }));

    fireEvent.click(screen.getByRole("button", { name: /Use imported: Markus/ }));

    await waitFor(() =>
      expect(screen.getByTestId("profile-review-done")).toBeInTheDocument(),
    );
  });

  it("renders an all-clear done state when there is nothing to review", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "No open issues to review — your Master Profile is in good shape!",
      gaps_total: 0,
      gaps_remaining: 0,
      choices: null,
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));

    await waitFor(() =>
      expect(screen.getByTestId("profile-review-done")).toBeInTheDocument(),
    );
  });

  // F4 (#73): the resolver must let the user say "these are two roles, keep both"
  // instead of being forced into an either/or pick. Pairs with backend #71.
  it("offers a 'keep both / two roles' affordance alongside the choices and sends a distinct answer", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question:
        "start_date '2020-03' vs '2023-01' — which is correct for Senior Software Engineer at Logivia?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: ["Keep current: 2020-03", "Use imported: 2023-01"],
    });
    sendMock.mockResolvedValue({
      complete: true,
      question: null,
      choices: null,
      gaps_remaining: 0,
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("profile-review-keep-both"));

    fireEvent.click(screen.getByTestId("profile-review-keep-both"));

    await waitFor(() => expect(sendMock).toHaveBeenCalledTimes(1));
    const sentMessage = sendMock.mock.calls[0][1];
    // A substantive, recognisable "two roles" intent — not one of the binary picks.
    expect(sentMessage.toLowerCase()).toContain("separate");
    expect(sentMessage).not.toBe("Keep current: 2020-03");
  });

  it("does not show the 'keep both' affordance once the review is done", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "No open issues — you're in good shape!",
      gaps_total: 0,
      gaps_remaining: 0,
      choices: null,
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-review-done")).toBeInTheDocument());
    expect(screen.queryByTestId("profile-review-keep-both")).not.toBeInTheDocument();
  });

  // F3b (run3): a merge-loss/accuracy issue has no conflicts to walk
  // (gaps_total 0). Resolve must NOT dead-end on a generic "All done" — it must
  // surface the real flagged issue and an action.
  it("surfaces the flagged issue + an action when there are no conflicts to walk", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Nothing to review",
      gaps_total: 0,
      gaps_remaining: 0,
      choices: null,
    });

    const onAction = vi.fn();
    const issue = {
      id: "accuracy:skills",
      thread: "accuracy" as const,
      profile_mismatch_severity: "critical" as const,
      summary: "Merge from cv_upload did not retain 17 extracted item(s) (skills)",
      field_ref: "skills",
      source_record_ref: "rec-1",
    };

    render(
      withIntl(
        <ProfileReviewDrawer open onClose={vi.fn()} issue={issue} onAction={onAction} />,
        "en",
      ),
    );

    await waitFor(() =>
      expect(screen.getByTestId("profile-review-issue")).toBeInTheDocument(),
    );
    // The real problem is shown, not a generic all-clear.
    expect(screen.getByText(/did not retain 17 extracted/)).toBeInTheDocument();
    expect(screen.queryByTestId("profile-review-done")).not.toBeInTheDocument();

    // The action routes back to the affected section with the issue.
    fireEvent.click(screen.getByTestId("profile-review-action"));
    expect(onAction).toHaveBeenCalledWith(issue);
  });

  // #626 — the no-conflicts-to-walk state can also carry a `conflict`-thread
  // issue (defensive: today's `conflict`-walk normally handles these itself,
  // but nothing guarantees `gaps_total` and this prop always agree). It must
  // get the SAME localized composition HealthPanel uses, never the raw
  // backend summary.
  it("composes a conflict issue's entity + values instead of showing the raw summary", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Nothing to review",
      gaps_total: 0,
      gaps_remaining: 0,
      choices: null,
    });

    const issue = {
      id: "conflict:w-1",
      thread: "conflict" as const,
      profile_mismatch_severity: "review" as const,
      summary: "work_experience.end_date: '2019-12' vs '2020-01'",
      field_ref: "end_date",
      source_record_ref: "cv_upload",
      entity_label: "Senior Developer @ Acme Corp",
      section: "work_experience",
      field: "end_date",
      existing_value_display: "2019-12",
      incoming_value_display: "2020-01",
      existing_source: null,
      incoming_source: "cv_upload",
    };

    render(
      withIntl(
        <ProfileReviewDrawer open onClose={vi.fn()} issue={issue} onAction={vi.fn()} />,
        "en",
      ),
    );

    await waitFor(() =>
      expect(screen.getByTestId("profile-review-issue")).toBeInTheDocument(),
    );
    expect(screen.getByText(/Senior Developer @ Acme Corp/)).toBeInTheDocument();
    expect(screen.getByText(/End date/)).toBeInTheDocument();
    expect(screen.queryByText(/work_experience\.end_date/)).not.toBeInTheDocument();
    expect(screen.getByText(/2019-12/)).toBeInTheDocument();
    expect(screen.getByText(/2020-01/)).toBeInTheDocument();
  });

  // A genuinely-resolved conflict walk still ends on the all-clear, even if an
  // issue was passed in (the walk had conflicts, so it's not the merge-loss case).
  it("shows the all-clear done state after resolving real conflicts even with an issue prop", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Which name is correct?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: ["Keep current: Max", "Use imported: Markus"],
    });
    sendMock.mockResolvedValue({
      complete: true,
      question: null,
      choices: null,
      gaps_remaining: 0,
    });

    const issue = {
      id: "conflict:name",
      thread: "conflict" as const,
      profile_mismatch_severity: "review" as const,
      summary: "name conflict",
      field_ref: "name",
      source_record_ref: "rec-1",
    };

    render(
      withIntl(<ProfileReviewDrawer open onClose={vi.fn()} issue={issue} onAction={vi.fn()} />, "en"),
    );
    await waitFor(() => screen.getByRole("button", { name: /Keep current: Max/ }));
    fireEvent.click(screen.getByRole("button", { name: /Keep current: Max/ }));

    await waitFor(() =>
      expect(screen.getByTestId("profile-review-done")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("profile-review-issue")).not.toBeInTheDocument();
  });

  // #705 (founder UAT, 2026-09-15) — a `not_applied` issue carrying the FULL
  // per-item receipt (`not_applied_items`) renders one group per section with
  // its own action, instead of the single "Review the affected section"
  // button that "just randomly moves to one of the affected sections".
  it("groups a not_applied issue's full item list by section, with one action per section", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Nothing to review",
      gaps_total: 0,
      gaps_remaining: 0,
      choices: null,
    });

    const onSectionAction = vi.fn();
    const items = [
      { section: "work_experience", label: "Acme Corp", reason: "no_op_carried_entry" },
      { section: "work_experience", label: "Beta GmbH", reason: "no_op_carried_entry" },
      { section: "skills", label: "Rust", reason: "op_rejected" },
    ];
    const issue = {
      id: "not_applied:rec-1",
      thread: "not_applied" as const,
      profile_mismatch_severity: "review" as const,
      summary: "3 items from your cv_upload did not reach your profile (…) — …",
      field_ref: "skills, work_experience",
      source_record_ref: "rec-1",
      not_applied_count: items.length,
      not_applied_source: "cv_upload",
      not_applied_reasons: ["no_op_carried_entry", "op_rejected"],
      not_applied_labels: ["Acme Corp", "Beta GmbH", "Rust"],
      not_applied_items: items,
    };

    render(
      withIntl(
        <ProfileReviewDrawer
          open
          onClose={vi.fn()}
          issue={issue}
          onAction={vi.fn()}
          onSectionAction={onSectionAction}
        />,
        "en",
      ),
    );

    await waitFor(() =>
      expect(screen.getByTestId("profile-review-not-applied-groups")).toBeInTheDocument(),
    );
    const groups = screen.getAllByTestId("not-applied-group");
    expect(groups).toHaveLength(2);
    expect(screen.getByText(/Acme Corp, Beta GmbH/)).toBeInTheDocument();
    expect(screen.getByText("Rust")).toBeInTheDocument();
    // The old single generic action is gone — replaced by per-section ones.
    expect(screen.queryByTestId("profile-review-action")).not.toBeInTheDocument();

    // Groups render in the backend's item order (sorted by section) — the
    // fixture lists work_experience's two items before skills' one.
    const sectionButtons = screen.getAllByTestId("profile-review-section-action");
    expect(sectionButtons).toHaveLength(2);
    fireEvent.click(sectionButtons[0]);
    expect(onSectionAction).toHaveBeenCalledWith("work_experience", ["Acme Corp", "Beta GmbH"]);
    fireEvent.click(sectionButtons[1]);
    expect(onSectionAction).toHaveBeenCalledWith("skills", ["Rust"]);
  });

  // Backward compatibility: a `not_applied` issue from a backend predating
  // #705 (no `not_applied_items`) keeps the old single summary + one action.
  it("falls back to the single summary + action for a not_applied issue with no item list", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Nothing to review",
      gaps_total: 0,
      gaps_remaining: 0,
      choices: null,
    });

    const onAction = vi.fn();
    const issue = {
      id: "not_applied:rec-2",
      thread: "not_applied" as const,
      profile_mismatch_severity: "review" as const,
      summary: "2 items from your cv_upload did not reach your profile (A, B) — no change carried it",
      field_ref: "skills",
      source_record_ref: "rec-2",
    };

    render(
      withIntl(
        <ProfileReviewDrawer open onClose={vi.fn()} issue={issue} onAction={onAction} />,
        "en",
      ),
    );

    await waitFor(() => expect(screen.getByTestId("profile-review-issue")).toBeInTheDocument());
    expect(screen.queryByTestId("profile-review-not-applied-groups")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("profile-review-action"));
    expect(onAction).toHaveBeenCalledWith(issue);
  });

  it("renders German chrome under the de locale", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question: "Welcher Name stimmt?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: null,
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "de"));

    await waitFor(() => expect(screen.getByText("Profil prüfen")).toBeInTheDocument());
  });

  // #685 (refute-v.md §2) — keepBothApplies is exported from THIS module
  // (ProfileReviewDrawer.tsx:66-69), not from lib/conflict-display.ts as the
  // issue's own acceptance text names it; verified against the source before
  // writing this suite. Same PROFILE_LEVEL_SECTIONS gate either way: a
  // profile-level dispute (professional_summary, personal_info) has no
  // second entry to keep, so the "keep both" escape hatch (F4/#73) must not
  // be offered for one.
  describe("keepBothApplies", () => {
    it("is false for a professional_summary dispute", () => {
      expect(keepBothApplies({ section: "professional_summary" })).toBe(false);
    });

    it("is false for a personal_info dispute", () => {
      expect(keepBothApplies({ section: "personal_info" })).toBe(false);
    });

    it("is true for a work_experience dispute", () => {
      expect(keepBothApplies({ section: "work_experience" })).toBe(true);
    });

    it("is true when no issue is passed", () => {
      expect(keepBothApplies(undefined)).toBe(true);
    });

    it("is true when the issue is null", () => {
      expect(keepBothApplies(null)).toBe(true);
    });
  });

  // #685 — the drawer render itself: a profile-level conflict (professional_
  // summary) must not offer "keep both" even though it has real choices to
  // walk, because the summary has one slot per language, not a second entry
  // to keep. Mirrors the #73 "offers a 'keep both'" test's setup (gaps_total
  // 1, real choices) with an issue prop added.
  it("hides the 'keep both' affordance for a professional_summary conflict", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question:
        "Your profile has two values for professional_summary.en. Which is correct?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: ["Keep current: Senior Engineer.", "Use imported: 15 years in GMP manufacturing."],
    });

    const issue = {
      id: "conflict:summary-en",
      thread: "conflict" as const,
      profile_mismatch_severity: "review" as const,
      summary: "professional_summary.en: 'Senior Engineer.' vs '15 years in GMP manufacturing.'",
      field_ref: "en",
      source_record_ref: "cv_upload",
      section: "professional_summary",
      field: "en",
    };

    render(
      withIntl(
        <ProfileReviewDrawer open onClose={vi.fn()} issue={issue} onAction={vi.fn()} />,
        "en",
      ),
    );

    await waitFor(() =>
      screen.getByRole("button", { name: /Keep current: Senior Engineer\./ }),
    );
    expect(screen.queryByTestId("profile-review-keep-both")).not.toBeInTheDocument();
  });

  // #685 counterpart: an entity-level dispute (work_experience) keeps the
  // affordance — the gate is scoped to the two profile-level sections only.
  it("still shows the 'keep both' affordance for a work_experience conflict", async () => {
    startMock.mockResolvedValue({
      session_id: "s1",
      first_question:
        "start_date '2020-03' vs '2023-01' — which is correct for Senior Software Engineer at Logivia?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: ["Keep current: 2020-03", "Use imported: 2023-01"],
    });

    const issue = {
      id: "conflict:w-1",
      thread: "conflict" as const,
      profile_mismatch_severity: "review" as const,
      summary: "work_experience.start_date: '2020-03' vs '2023-01'",
      field_ref: "start_date",
      source_record_ref: "cv_upload",
      entity_label: "Senior Software Engineer @ Logivia",
      section: "work_experience",
      field: "start_date",
    };

    render(
      withIntl(
        <ProfileReviewDrawer open onClose={vi.fn()} issue={issue} onAction={vi.fn()} />,
        "en",
      ),
    );

    await waitFor(() => screen.getByTestId("profile-review-keep-both"));
    expect(screen.getByTestId("profile-review-keep-both")).toBeInTheDocument();
  });
});
