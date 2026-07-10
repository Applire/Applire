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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MarkAppliedPrompt } from "../MarkAppliedPrompt";
import { patchApplicationStatus } from "@/lib/api/applications";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

vi.mock("@/lib/api/applications", () => ({
  patchApplicationStatus: vi.fn().mockResolvedValue({ user_status: "applied" }),
}));

const mockPatch = vi.mocked(patchApplicationStatus);

describe("MarkAppliedPrompt (E039/US218 — post-download natural moment)", () => {
  beforeEach(() => {
    mockPatch.mockClear();
  });

  it("renders the prompt question", () => {
    render(<MarkAppliedPrompt applicationId="app-1" stampAppliedAt onClose={() => {}} />);
    expect(screen.getByTestId("mark-applied-prompt")).toBeInTheDocument();
    expect(screen.getByText("markAppliedTitle")).toBeInTheDocument();
  });

  it("confirm marks the application as applied (stamping applied_at) and closes", async () => {
    const onClose = vi.fn();
    render(<MarkAppliedPrompt applicationId="app-1" stampAppliedAt onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: "markAppliedConfirm" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockPatch).toHaveBeenCalledWith("app-1", "applied", { stampAppliedAt: true });
  });

  it("does not re-stamp applied_at when the application already has one", async () => {
    const onClose = vi.fn();
    render(<MarkAppliedPrompt applicationId="app-1" stampAppliedAt={false} onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: "markAppliedConfirm" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockPatch).toHaveBeenCalledWith("app-1", "applied", { stampAppliedAt: false });
  });

  it("pins the downloaded CV version on confirm when submittedCvId is provided (US219)", async () => {
    const onClose = vi.fn();
    render(
      <MarkAppliedPrompt
        applicationId="app-1"
        stampAppliedAt
        submittedCvId="cv-9"
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "markAppliedConfirm" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mockPatch).toHaveBeenCalledWith("app-1", "applied", {
      stampAppliedAt: true,
      submittedCvId: "cv-9",
    });
  });

  it("mentions the version pin in the body only when a CV is being pinned", () => {
    const { rerender } = render(
      <MarkAppliedPrompt applicationId="app-1" stampAppliedAt submittedCvId="cv-9" onClose={() => {}} />,
    );
    expect(screen.getByText("markAppliedPinHint")).toBeInTheDocument();

    rerender(<MarkAppliedPrompt applicationId="app-1" stampAppliedAt onClose={() => {}} />);
    expect(screen.queryByText("markAppliedPinHint")).not.toBeInTheDocument();
  });

  it("decline closes without any PATCH", () => {
    const onClose = vi.fn();
    render(<MarkAppliedPrompt applicationId="app-1" stampAppliedAt onClose={onClose} />);

    fireEvent.click(screen.getByRole("button", { name: "markAppliedDecline" }));

    expect(onClose).toHaveBeenCalled();
    expect(mockPatch).not.toHaveBeenCalled();
  });
});
