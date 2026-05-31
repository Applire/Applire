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

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { CVPageActionBar } from "../CVPageActionBar";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("@/lib/profile-roles", () => ({
  markApplicationHired: (...args: Parameters<typeof mockMarkApplicationHired>) =>
    mockMarkApplicationHired(...args),
}));

const mockPush = vi.fn();
const mockMarkApplicationHired = vi.fn();

const BASE_PROPS = {
  flowId: "test-flow",
  applicationId: null as string | null,
  coverLetterId: null as string | null,
  onDownloadPdf: vi.fn(),
  onGenerateCoverLetter: vi.fn(),
  onNext: vi.fn(),
};

describe("CVPageActionBar", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders the Download button", () => {
    render(withIntl(<CVPageActionBar {...BASE_PROPS} />));
    expect(screen.getByTestId("page-action-download")).toBeTruthy();
  });

  it("Download click invokes onDownloadPdf", () => {
    const onDownloadPdf = vi.fn();
    render(withIntl(<CVPageActionBar {...BASE_PROPS} onDownloadPdf={onDownloadPdf} />));
    fireEvent.click(screen.getByTestId("page-action-download"));
    expect(onDownloadPdf).toHaveBeenCalled();
  });

  it("renders Anschreiben generate button when coverLetterId is null", () => {
    render(withIntl(<CVPageActionBar {...BASE_PROPS} coverLetterId={null} />));
    expect(screen.getByTestId("page-action-cover-letter-generate")).toBeTruthy();
    expect(screen.queryByTestId("page-action-cover-letter-view")).toBeNull();
  });

  it("renders Anschreiben view link when coverLetterId is set", () => {
    render(withIntl(<CVPageActionBar {...BASE_PROPS} coverLetterId="cl-1" />));
    expect(screen.getByTestId("page-action-cover-letter-view")).toBeTruthy();
  });

  it("does not render Mark Hired button when applicationId is null", () => {
    render(withIntl(<CVPageActionBar {...BASE_PROPS} applicationId={null} />));
    expect(screen.queryByTestId("page-action-hired")).toBeNull();
  });

  it("renders Mark Hired button when applicationId is set", () => {
    render(withIntl(<CVPageActionBar {...BASE_PROPS} applicationId="app-1" />));
    expect(screen.getByTestId("page-action-hired")).toBeTruthy();
  });

  it("Mark Hired click calls markApplicationHired and routes to redirect_url", async () => {
    mockMarkApplicationHired.mockResolvedValue({
      application_id: "app-1",
      user_status: "hired",
      redirect_url: "/profile/upload?action=add-role&source=application&application_id=app-1",
    });

    render(withIntl(<CVPageActionBar {...BASE_PROPS} applicationId="app-1" />));
    fireEvent.click(screen.getByTestId("page-action-hired"));

    await waitFor(() => {
      expect(mockMarkApplicationHired).toHaveBeenCalledWith("app-1");
      expect(mockPush).toHaveBeenCalledWith(
        "/profile/upload?action=add-role&source=application&application_id=app-1",
      );
    });
  });

  it("Weiter click invokes onNext", () => {
    const onNext = vi.fn();
    render(withIntl(<CVPageActionBar {...BASE_PROPS} onNext={onNext} />));
    fireEvent.click(screen.getByTestId("page-action-next"));
    expect(onNext).toHaveBeenCalled();
  });

  it("Anschreiben generate click invokes onGenerateCoverLetter", () => {
    const onGenerateCoverLetter = vi.fn();
    render(withIntl(<CVPageActionBar {...BASE_PROPS} onGenerateCoverLetter={onGenerateCoverLetter} />));
    fireEvent.click(screen.getByTestId("page-action-cover-letter-generate"));
    expect(onGenerateCoverLetter).toHaveBeenCalled();
  });
});
