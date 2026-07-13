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

/**
 * US222 / issue #158 — Cancel an application from the flow (journey Branch I).
 *
 * The button is the flow-side walk-away: confirm dialog → PATCH
 * user_status=cancelled → back to the dashboard. Cancel must never fire
 * without the confirm step (an accidental click mid-flow discards real work).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CancelApplicationButton } from "../CancelApplicationButton";
import { patchApplicationStatus } from "@/lib/api/applications";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key,
}));

vi.mock("@/lib/api/applications", () => ({
  patchApplicationStatus: vi.fn().mockResolvedValue({ user_status: "cancelled" }),
}));

describe("CancelApplicationButton", () => {
  beforeEach(() => {
    mockPush.mockReset();
    vi.mocked(patchApplicationStatus).mockClear();
  });

  it("renders the trigger and no dialog initially", () => {
    render(<CancelApplicationButton applicationId="app-1" />);
    expect(screen.getByRole("button", { name: "cancelApplication" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the confirm dialog on click without PATCHing yet", () => {
    render(<CancelApplicationButton applicationId="app-1" />);
    fireEvent.click(screen.getByRole("button", { name: "cancelApplication" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(patchApplicationStatus).not.toHaveBeenCalled();
  });

  it("confirming PATCHes cancelled and navigates to the dashboard", async () => {
    render(<CancelApplicationButton applicationId="app-1" />);
    fireEvent.click(screen.getByRole("button", { name: "cancelApplication" }));
    fireEvent.click(screen.getByRole("button", { name: "cancelDialogConfirm" }));
    await waitFor(() =>
      expect(patchApplicationStatus).toHaveBeenCalledWith("app-1", "cancelled")
    );
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/dashboard"));
  });

  it("keep-working closes the dialog without PATCHing", () => {
    render(<CancelApplicationButton applicationId="app-1" />);
    fireEvent.click(screen.getByRole("button", { name: "cancelApplication" }));
    fireEvent.click(screen.getByRole("button", { name: "cancelDialogKeep" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(patchApplicationStatus).not.toHaveBeenCalled();
  });
});
