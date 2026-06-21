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
