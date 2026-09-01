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
});
