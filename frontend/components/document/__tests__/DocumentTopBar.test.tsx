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
import { DocumentTopBar } from "../DocumentTopBar";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const BASE = {
  flowId: "flow-1",
  activeDoc: "cv" as "cv" | "cover-letter",
  onDownloadPdf: vi.fn(),
  downloadDisabled: false,
};

describe("DocumentTopBar", () => {
  afterEach(() => vi.clearAllMocks());

  it("renders the CV / cover-letter document toggle with correct hrefs", () => {
    render(withIntl(<DocumentTopBar {...BASE} />));
    const cvLink = screen.getByTestId("document-nav-cv");
    const clLink = screen.getByTestId("document-nav-cover-letter");
    expect(cvLink.getAttribute("href")).toBe("/flow/flow-1/cv");
    expect(clLink.getAttribute("href")).toBe("/flow/flow-1/cover-letter");
  });

  it("marks the active document with aria-current", () => {
    render(withIntl(<DocumentTopBar {...BASE} activeDoc="cover-letter" />));
    expect(screen.getByTestId("document-nav-cover-letter").getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("document-nav-cv").getAttribute("aria-current")).toBeNull();
  });

  it("renders the primary PDF download button and invokes onDownloadPdf on click", () => {
    const onDownloadPdf = vi.fn();
    render(withIntl(<DocumentTopBar {...BASE} onDownloadPdf={onDownloadPdf} />));
    fireEvent.click(screen.getByTestId("document-download-btn"));
    expect(onDownloadPdf).toHaveBeenCalledOnce();
  });

  it("disables the download button when downloadDisabled is true", () => {
    render(withIntl(<DocumentTopBar {...BASE} downloadDisabled />));
    expect((screen.getByTestId("document-download-btn") as HTMLButtonElement).disabled).toBe(true);
  });

  it("keeps the download button visible below md by default (no command bar present)", () => {
    render(withIntl(<DocumentTopBar {...BASE} />));
    const className = screen.getByTestId("document-download-btn").className;
    expect(className).not.toContain("hidden");
    expect(className).toContain("inline-flex");
  });

  it("hides the download button below md when hideDownloadBelowMd is set (E040/US226 — avoids a redundant CTA next to the mobile command bar)", () => {
    render(withIntl(<DocumentTopBar {...BASE} hideDownloadBelowMd />));
    const className = screen.getByTestId("document-download-btn").className;
    expect(className).toContain("hidden");
    expect(className).toContain("md:inline-flex");
  });
});
