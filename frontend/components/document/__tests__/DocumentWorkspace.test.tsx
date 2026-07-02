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

import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { DocumentWorkspace } from "../DocumentWorkspace";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const BASE = {
  flowId: "flow-1",
  activeDoc: "cv" as "cv" | "cover-letter",
  onDownloadPdf: vi.fn(),
  preview: <div data-testid="slot-preview">preview</div>,
  atsPanel: <div data-testid="slot-ats">ats</div>,
  sidebar: <div data-testid="slot-sidebar">sidebar</div>,
};

describe("DocumentWorkspace", () => {
  afterEach(() => vi.clearAllMocks());

  it("renders the shared top bar, preview, ATS and sidebar slots", () => {
    render(withIntl(<DocumentWorkspace {...BASE} />));
    expect(screen.getByTestId("document-workspace")).toBeTruthy();
    expect(screen.getByTestId("document-topbar")).toBeTruthy();
    expect(screen.getByTestId("slot-preview")).toBeTruthy();
    expect(screen.getByTestId("slot-ats")).toBeTruthy();
    expect(screen.getByTestId("slot-sidebar")).toBeTruthy();
  });

  it("wires the top bar download button to onDownloadPdf", () => {
    const onDownloadPdf = vi.fn();
    render(withIntl(<DocumentWorkspace {...BASE} onDownloadPdf={onDownloadPdf} />));
    fireEvent.click(screen.getByTestId("document-download-btn"));
    expect(onDownloadPdf).toHaveBeenCalledOnce();
  });

  it("omits the ATS region when no atsPanel is supplied", () => {
    render(withIntl(<DocumentWorkspace {...BASE} atsPanel={undefined} />));
    expect(screen.queryByTestId("slot-ats")).toBeNull();
    // preview + sidebar still render
    expect(screen.getByTestId("slot-preview")).toBeTruthy();
    expect(screen.getByTestId("slot-sidebar")).toBeTruthy();
  });
});
