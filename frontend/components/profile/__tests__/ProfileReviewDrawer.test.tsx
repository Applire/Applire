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
import { ProfileReviewDrawer } from "../ProfileReviewDrawer";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { CurrentConflict } from "@/lib/profile-review";

vi.mock("@/lib/profile-review", async (orig) => {
  const actual = await orig<typeof import("@/lib/profile-review")>();
  return {
    ...actual,
    startReview: vi.fn(),
    answerWithOutcome: vi.fn(),
    answerWithText: vi.fn(),
    requestMergeProposal: vi.fn(),
  };
});
vi.mock("@/lib/profile-merge", () => ({ undoLastMerge: vi.fn() }));

import {
  ReviewApiError,
  answerWithOutcome,
  answerWithText,
  requestMergeProposal,
  startReview,
} from "@/lib/profile-review";
import { undoLastMerge } from "@/lib/profile-merge";

const startMock = vi.mocked(startReview);
const outcomeMock = vi.mocked(answerWithOutcome);
const textMock = vi.mocked(answerWithText);
const proposalMock = vi.mocked(requestMergeProposal);
const undoMock = vi.mocked(undoLastMerge);

const BASE = { choices: null, current_conflict: null, follow_up_used: false };

// Synthetic — the U-1 SHAPE: two different achievements of one role.
const LIST_CONFLICT: CurrentConflict = {
  conflict_id: "c-list",
  shape: "list",
  outcomes: ["keep_current", "take_new", "keep_both", "merge"],
  entity_label: "Quality Lead @ Nordwerk Logistik GmbH",
  section: "work_experience",
  field: "achievements",
  existing_value_display: "Rebuilt supplier selection for 4 sites and cut scrap by 18 %.",
  incoming_value_display: "Introduced a batch-release quality dashboard across 4 sites.",
  incoming_source: "cv_upload",
};

const ROLE_SCALAR: CurrentConflict = {
  conflict_id: "c-date",
  shape: "scalar",
  outcomes: ["keep_current", "take_new", "keep_both"],
  entity_label: "Developer @ Nordwerk Logistik GmbH",
  section: "work_experience",
  field: "start_date",
  existing_value_display: "03/2015",
  incoming_value_display: "04/2015",
  incoming_source: "cv_upload",
};

const NAME_SCALAR: CurrentConflict = {
  ...ROLE_SCALAR,
  conflict_id: "c-name",
  outcomes: ["keep_current", "take_new"],
  entity_label: null,
  section: "personal_info",
  field: "name",
  existing_value_display: "Max Muster",
  incoming_value_display: "Markus Muster",
};

function startOn(conflict: CurrentConflict | null, extra: Record<string, unknown> = {}) {
  startMock.mockResolvedValue({
    ...BASE,
    session_id: "s1",
    first_question: "Two values — which is correct?",
    gaps_total: 2,
    gaps_remaining: 2,
    current_conflict: conflict,
    ...extra,
  });
}

const reply = (extra: Record<string, unknown> = {}) => ({
  ...BASE,
  complete: false,
  question: null,
  gaps_remaining: 1,
  ...extra,
});

describe("ProfileReviewDrawer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("U-2: starts the walk on the issue the user clicked", async () => {
    startOn(LIST_CONFLICT);
    const issue = {
      id: "conflict:c-list",
      thread: "conflict" as const,
      profile_mismatch_severity: "review" as const,
      summary: "x",
    };
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} issue={issue} />, "en"));
    await waitFor(() => expect(screen.getByTestId("conflict-card")).toBeInTheDocument());
    expect(startMock).toHaveBeenCalledWith("conflict:c-list");
    expect(screen.getByTestId("profile-review-progress")).toHaveTextContent(
      "Point 1 of 2 – the item you clicked",
    );
  });

  it("starts the generic walk with no issue in hand", async () => {
    startOn(LIST_CONFLICT);
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => expect(screen.getByTestId("conflict-card")).toBeInTheDocument());
    expect(startMock).toHaveBeenCalledWith(null);
  });

  it("UAT-1: a list conflict offers four ways out with both values in full", async () => {
    startOn(LIST_CONFLICT);
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "de"));
    await waitFor(() => expect(screen.getByTestId("conflict-card")).toBeInTheDocument());
    for (const o of ["keep_current", "take_new", "keep_both", "merge"]) {
      expect(screen.getByTestId(`conflict-outcome-${o}`)).toBeInTheDocument();
    }
    expect(screen.getByTestId("conflict-outcome-keep_both")).toHaveTextContent(
      "Beide behalten – zwei verschiedene Einträge",
    );
    // U-3b — the reader's quotation marks, never English '…' inside German text.
    expect(screen.getByTestId("conflict-card-current")).toHaveTextContent(
      "„Rebuilt supplier selection for 4 sites and cut scrap by 18 %.“",
    );
    // The backend's question sentence is not repeated as a bubble.
    expect(screen.queryByText("Two values — which is correct?")).not.toBeInTheDocument();
  });

  it("SF-PROFILE.22: 'keep both' sends the structured outcome, never text through the matcher", async () => {
    startOn(LIST_CONFLICT);
    outcomeMock.mockResolvedValue(reply({ complete: true, gaps_remaining: 0 }));
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-outcome-keep_both"));
    fireEvent.click(screen.getByTestId("conflict-outcome-keep_both"));
    await waitFor(() => expect(screen.getByTestId("profile-review-done")).toBeInTheDocument());
    expect(outcomeMock).toHaveBeenCalledWith("s1", "keep_both", undefined);
    expect(textMock).not.toHaveBeenCalled();
  });

  it("each outcome chip sends its own outcome", async () => {
    for (const o of ["keep_current", "take_new"] as const) {
      vi.clearAllMocks();
      startOn(LIST_CONFLICT);
      outcomeMock.mockResolvedValue(reply({ complete: true, gaps_remaining: 0 }));
      const { unmount } = render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
      await waitFor(() => screen.getByTestId(`conflict-outcome-${o}`));
      fireEvent.click(screen.getByTestId(`conflict-outcome-${o}`));
      await waitFor(() => expect(outcomeMock).toHaveBeenCalledWith("s1", o, undefined));
      unmount();
    }
  });

  it("a role entry's scalar conflict keeps the 'two different positions' escape, now as keep_both", async () => {
    startOn(ROLE_SCALAR);
    outcomeMock.mockResolvedValue(reply({ complete: true, gaps_remaining: 0 }));
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-card"));
    expect(screen.queryByTestId("conflict-outcome-merge")).not.toBeInTheDocument();
    const both = screen.getByTestId("conflict-outcome-keep_both");
    expect(both).toHaveTextContent(/two separate roles/i);
    fireEvent.click(both);
    await waitFor(() => expect(outcomeMock).toHaveBeenCalledWith("s1", "keep_both", undefined));
  });

  it("any other scalar conflict offers exactly the backend's two outcomes", async () => {
    startOn(NAME_SCALAR);
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-card"));
    expect(screen.getByTestId("conflict-outcome-keep_current")).toBeInTheDocument();
    expect(screen.getByTestId("conflict-outcome-take_new")).toBeInTheDocument();
    expect(screen.queryByTestId("conflict-outcome-keep_both")).not.toBeInTheDocument();
    expect(screen.queryByTestId("conflict-outcome-merge")).not.toBeInTheDocument();
  });

  it("merge: the proposal is shown for confirm/edit, nothing is written before confirm, the edit is sent", async () => {
    startOn(LIST_CONFLICT);
    proposalMock.mockResolvedValue({ proposal: "Rebuilt supplier selection and introduced a dashboard.", verifier: "passed" });
    outcomeMock.mockResolvedValue(
      reply({
        interpreted: { outcome: "merge", verifier: "passed" },
        profile_updated_at: "2026-10-28T10:00:00Z",
        current_conflict: NAME_SCALAR,
      }),
    );
    undoMock.mockResolvedValue({ restored: true });
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-outcome-merge"));
    fireEvent.click(screen.getByTestId("conflict-outcome-merge"));
    const box = await screen.findByTestId("conflict-merge-text");
    expect(box).toHaveValue("Rebuilt supplier selection and introduced a dashboard.");
    expect(proposalMock).toHaveBeenCalledWith("s1", "c-list");
    expect(outcomeMock).not.toHaveBeenCalled();
    fireEvent.change(box, { target: { value: "Rebuilt supplier selection for 4 sites and introduced a dashboard." } });
    fireEvent.click(screen.getByTestId("conflict-merge-confirm"));
    await waitFor(() =>
      expect(outcomeMock).toHaveBeenCalledWith(
        "s1",
        "merge_confirm",
        "Rebuilt supplier selection for 4 sites and introduced a dashboard.",
      ),
    );
    // One undo, from the notice.
    fireEvent.click(await screen.findByTestId("profile-review-undo"));
    await waitFor(() => expect(undoMock).toHaveBeenCalledWith("2026-10-28T10:00:00Z"));
    expect(await screen.findByText("Undone.")).toBeInTheDocument();
  });

  async function confirmMergeWith(replyExtra: Record<string, unknown>) {
    startOn(LIST_CONFLICT);
    proposalMock.mockResolvedValue({ proposal: "One sentence.", verifier: "passed" });
    outcomeMock.mockResolvedValue(reply({ current_conflict: NAME_SCALAR, ...replyExtra }));
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-outcome-merge"));
    fireEvent.click(screen.getByTestId("conflict-outcome-merge"));
    await screen.findByTestId("conflict-merge-text");
    fireEvent.click(screen.getByTestId("conflict-merge-confirm"));
    await waitFor(() => expect(outcomeMock).toHaveBeenCalled());
  }

  it("ADV-8: no Undo for a merge reply that wrote nothing", async () => {
    await confirmMergeWith({ interpreted: null, profile_updated_at: "2026-10-28T10:00:00Z" });
    await waitFor(() => expect(screen.getByTestId("conflict-card")).toHaveTextContent("Name"));
    expect(screen.queryByTestId("profile-review-undo")).not.toBeInTheDocument();
  });

  it("ADV-8: no Undo without the merge's basis", async () => {
    await confirmMergeWith({ interpreted: { outcome: "merge", verifier: "passed" } });
    await waitFor(() => expect(screen.getByTestId("conflict-card")).toHaveTextContent("Name"));
    expect(screen.queryByTestId("profile-review-undo")).not.toBeInTheDocument();
  });

  it("ADV-8: the next answer removes the Undo; a 409 says the profile changed", async () => {
    await confirmMergeWith({
      interpreted: { outcome: "merge", verifier: "passed" },
      profile_updated_at: "2026-10-28T10:00:00Z",
    });
    undoMock.mockRejectedValue(new ReviewApiError(409, "undo_stale", "undo_stale"));
    fireEvent.click(await screen.findByTestId("profile-review-undo"));
    expect(await screen.findByText("Your profile has changed since then – undo is no longer possible.")).toBeInTheDocument();
  });

  it("ADV-8: the Undo is gone once the next answer is written", async () => {
    await confirmMergeWith({
      interpreted: { outcome: "merge", verifier: "passed" },
      profile_updated_at: "2026-10-28T10:00:00Z",
    });
    await screen.findByTestId("profile-review-undo");
    outcomeMock.mockResolvedValue(reply({ complete: false, current_conflict: NAME_SCALAR }));
    fireEvent.click(screen.getByTestId("conflict-outcome-keep_current"));
    await waitFor(() => expect(outcomeMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId("profile-review-undo")).not.toBeInTheDocument();
  });

  it("merge: 'back' returns to the options without writing", async () => {
    startOn(LIST_CONFLICT);
    proposalMock.mockResolvedValue({ proposal: "One sentence.", verifier: "passed" });
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-outcome-merge"));
    fireEvent.click(screen.getByTestId("conflict-outcome-merge"));
    fireEvent.click(await screen.findByTestId("conflict-merge-back"));
    expect(screen.getByTestId("conflict-outcome-keep_both")).toBeInTheDocument();
    expect(outcomeMock).not.toHaveBeenCalled();
  });

  it("merge fails closed: a proposal that failed the fact check leaves the other three", async () => {
    startOn(LIST_CONFLICT);
    proposalMock.mockResolvedValue({ proposal: null, verifier: "failed" });
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-outcome-merge"));
    fireEvent.click(screen.getByTestId("conflict-outcome-merge"));
    await waitFor(() =>
      expect(screen.getByTestId("conflict-merge-unavailable")).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("conflict-outcome-merge")).not.toBeInTheDocument();
    expect(screen.getByTestId("conflict-outcome-keep_both")).toBeInTheDocument();
    expect(screen.queryByTestId("conflict-merge-text")).not.toBeInTheDocument();
  });

  it("an edited merge that failed the server re-check is announced as kept-both + own statement", async () => {
    startOn(LIST_CONFLICT);
    proposalMock.mockResolvedValue({ proposal: "One sentence.", verifier: "passed" });
    outcomeMock.mockResolvedValue(
      reply({ interpreted: { outcome: "manual", verifier: "failed" }, current_conflict: NAME_SCALAR }),
    );
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-outcome-merge"));
    fireEvent.click(screen.getByTestId("conflict-outcome-merge"));
    fireEvent.change(await screen.findByTestId("conflict-merge-text"), {
      target: { value: "One sentence, and 30 % faster." },
    });
    fireEvent.click(screen.getByTestId("conflict-merge-confirm"));
    const notice = await screen.findByTestId("profile-review-notice");
    expect(notice).toHaveTextContent(/your own statement. It replaces the two points/);
    expect(screen.queryByTestId("profile-review-undo")).not.toBeInTheDocument();
  });

  it("U-4/U-5: a typed answer read as an outcome says so", async () => {
    startOn(LIST_CONFLICT);
    textMock.mockResolvedValue(
      reply({ interpreted: { outcome: "keep_both" }, current_conflict: NAME_SCALAR }),
    );
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-card"));
    fireEvent.change(screen.getByTestId("profile-review-answer"), {
      target: { value: "both are right, two different achievements" },
    });
    fireEvent.click(screen.getByTestId("profile-review-send"));
    expect(await screen.findByTestId("profile-review-notice")).toHaveTextContent(
      "Understood: Keep both – two different entries.",
    );
    expect(textMock).toHaveBeenCalledWith("s1", "both are right, two different achievements");
    expect(outcomeMock).not.toHaveBeenCalled();
  });

  it("U-4/U-5 + T-bound: ONE follow-up, then only the outcome buttons — never the same question again", async () => {
    startOn(LIST_CONFLICT);
    textMock.mockResolvedValue(
      reply({
        interpreted: { follow_up: "Do you mean both belong in your profile?" },
        current_conflict: LIST_CONFLICT,
        follow_up_used: true,
        gaps_remaining: 2,
      }),
    );
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-card"));
    fireEvent.change(screen.getByTestId("profile-review-answer"), { target: { value: "hmm" } });
    fireEvent.click(screen.getByTestId("profile-review-send"));
    expect(await screen.findByText("Do you mean both belong in your profile?")).toBeInTheDocument();
    expect(screen.queryByTestId("profile-review-answer")).not.toBeInTheDocument();
    expect(screen.getByTestId("profile-review-outcome-required")).toBeInTheDocument();
    expect(screen.getByTestId("conflict-outcome-keep_both")).toBeInTheDocument();
  });

  it("a 409 outcome_required locks the text field instead of erroring", async () => {
    startOn(LIST_CONFLICT);
    textMock.mockRejectedValue(new ReviewApiError(409, "outcome_required", "outcome_required"));
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-card"));
    fireEvent.change(screen.getByTestId("profile-review-answer"), { target: { value: "again" } });
    fireEvent.click(screen.getByTestId("profile-review-send"));
    expect(await screen.findByTestId("profile-review-outcome-required")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("a typed corrected value is confirmed back to the user", async () => {
    startOn(ROLE_SCALAR);
    textMock.mockResolvedValue(
      reply({ interpreted: { outcome: "manual", corrected_value: "05/2015" }, complete: true, gaps_remaining: 0 }),
    );
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => screen.getByTestId("conflict-card"));
    fireEvent.change(screen.getByTestId("profile-review-answer"), { target: { value: "it was 05/2015" } });
    fireEvent.click(screen.getByTestId("profile-review-send"));
    expect(await screen.findByTestId("profile-review-notice")).toHaveTextContent('Understood – "05/2015" is saved.');
    expect(screen.getByTestId("profile-review-done")).toBeInTheDocument();
  });

  it("a non-conflict question keeps its choices, answered in words", async () => {
    startOn(null, { first_question: "Which role does 'SQL' belong to?", choices: ["Developer", "Analyst"] });
    textMock.mockResolvedValue(reply({ complete: true, gaps_remaining: 0 }));
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    expect(await screen.findByText("Which role does 'SQL' belong to?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Analyst" }));
    await waitFor(() => expect(textMock).toHaveBeenCalledWith("s1", "Analyst"));
    expect(screen.queryByTestId("conflict-card")).not.toBeInTheDocument();
  });

  it("an item closed meanwhile (404 issue_not_found) falls back to the generic walk", async () => {
    startMock
      .mockRejectedValueOnce(new ReviewApiError(404, "issue_not_found: conflict:gone", "issue_not_found"))
      .mockResolvedValueOnce({
        ...BASE,
        session_id: "s1",
        first_question: "x",
        gaps_total: 1,
        gaps_remaining: 1,
        current_conflict: LIST_CONFLICT,
      });
    const issue = { id: "conflict:gone", thread: "conflict" as const, profile_mismatch_severity: "review" as const, summary: "x" };
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} issue={issue} />, "en"));
    await waitFor(() => expect(screen.getByTestId("conflict-card")).toBeInTheDocument());
    expect(startMock).toHaveBeenNthCalledWith(1, "conflict:gone");
    expect(startMock).toHaveBeenNthCalledWith(2, null);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("a follow-up on a plain question is shown once (the question carries it)", async () => {
    startOn(null, { first_question: "Which role does 'SQL' belong to?", choices: ["Developer", "Analyst"] });
    textMock.mockResolvedValue(
      reply({
        interpreted: { follow_up: "Do you mean the analyst role?" },
        question: "Do you mean the analyst role?\n\nWhich role does 'SQL' belong to?",
        choices: ["Developer", "Analyst"],
        follow_up_used: true,
      }),
    );
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await screen.findByText("Which role does 'SQL' belong to?");
    fireEvent.change(screen.getByTestId("profile-review-answer"), { target: { value: "hm" } });
    fireEvent.click(screen.getByTestId("profile-review-send"));
    await waitFor(() => expect(screen.getAllByText(/Do you mean the analyst role\?/)).toHaveLength(1));
  });

  it("renders an all-clear done state when there is nothing to review", async () => {
    startOn(null, { gaps_total: 0, gaps_remaining: 0 });
    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "en"));
    await waitFor(() => expect(screen.getByTestId("profile-review-done")).toBeInTheDocument());
  });

  it("surfaces the flagged issue + an action when there are no conflicts to walk", async () => {
    startMock.mockResolvedValue({
      ...BASE,
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
      ...BASE,
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

  // #705 (founder UAT, 2026-09-15) — a `not_applied` issue carrying the FULL
  // per-item receipt (`not_applied_items`) renders one group per section with
  // its own action, instead of the single "Review the affected section"
  // button that "just randomly moves to one of the affected sections".
  it("groups a not_applied issue's full item list by section, with one action per section", async () => {
    startMock.mockResolvedValue({
      ...BASE,
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
      ...BASE,
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
      ...BASE,
      session_id: "s1",
      first_question: "Welcher Name stimmt?",
      gaps_total: 1,
      gaps_remaining: 1,
      choices: null,
    });

    render(withIntl(<ProfileReviewDrawer open onClose={vi.fn()} />, "de"));

    await waitFor(() => expect(screen.getByText("Profil prüfen")).toBeInTheDocument());
  });

});
