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
import { CVActionsTab } from "../CVActionsTab";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));
vi.mock("@/lib/profile-roles", () => ({
  markApplicationHired: (...a: Parameters<typeof mockHired>) => mockHired(...a),
}));
const mockPush = vi.fn();
const mockHired = vi.fn();

const BASE = {
  flowId: "flow-1",
  applicationId: null as string | null,
  coverLetterId: null as string | null,
  onGenerateCoverLetter: vi.fn(),
  onRegenerateSame: vi.fn(),
  onNext: vi.fn(),
};

describe("CVActionsTab", () => {
  afterEach(() => vi.clearAllMocks());

  it("regenerate button invokes onRegenerateSame", () => {
    const onRegenerateSame = vi.fn();
    render(withIntl(<CVActionsTab {...BASE} onRegenerateSame={onRegenerateSame} />));
    fireEvent.click(screen.getByTestId("cv-actions-regenerate"));
    expect(onRegenerateSame).toHaveBeenCalledOnce();
  });

  it("shows a generate-cover-letter action only when there is no cover letter yet", () => {
    const { rerender } = render(withIntl(<CVActionsTab {...BASE} coverLetterId={null} />));
    expect(screen.getByTestId("cv-actions-generate-cl")).toBeTruthy();
    rerender(withIntl(<CVActionsTab {...BASE} coverLetterId="cl-1" />));
    expect(screen.queryByTestId("cv-actions-generate-cl")).toBeNull();
  });

  it("Eingestellt renders only with an applicationId and routes on click", async () => {
    render(withIntl(<CVActionsTab {...BASE} applicationId={null} />));
    expect(screen.queryByTestId("cv-actions-hired")).toBeNull();

    mockHired.mockResolvedValue({ redirect_url: "/profile/upload?x=1" });
    render(withIntl(<CVActionsTab {...BASE} applicationId="app-1" />));
    fireEvent.click(screen.getByTestId("cv-actions-hired"));
    await waitFor(() => {
      expect(mockHired).toHaveBeenCalledWith("app-1");
      expect(mockPush).toHaveBeenCalledWith("/profile/upload?x=1");
    });
  });

  it("Weiter invokes onNext", () => {
    const onNext = vi.fn();
    render(withIntl(<CVActionsTab {...BASE} onNext={onNext} />));
    fireEvent.click(screen.getByTestId("cv-actions-next"));
    expect(onNext).toHaveBeenCalledOnce();
  });
});
