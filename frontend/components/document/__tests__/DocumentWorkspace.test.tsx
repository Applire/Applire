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

  // E040/US226 — responsive layout, jsdom can't evaluate media queries so these
  // assert the class strings that encode the below-md/md+ behavior.
  describe("responsive layout classes (E040/US226)", () => {
    it("wraps the sidebar slot in `hidden md:contents` (hidden below md, direct flex child at md+)", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      const wrapper = screen.getByTestId("slot-sidebar").parentElement;
      expect(wrapper?.className).toContain("hidden");
      expect(wrapper?.className).toContain("md:contents");
    });

    it("wraps the inline atsPanel slot in `hidden md:block` (moves into the command bar's sheet below md)", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      const wrapper = screen.getByTestId("slot-ats").parentElement;
      expect(wrapper?.className).toContain("hidden");
      expect(wrapper?.className).toContain("md:block");
    });

    it("renders the commandBar slot when supplied", () => {
      render(
        withIntl(
          <DocumentWorkspace
            {...BASE}
            commandBar={<div data-testid="slot-commandbar">command bar</div>}
          />
        )
      );
      expect(screen.getByTestId("slot-commandbar")).toBeTruthy();
    });

    it("omits the commandBar slot when not supplied", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      expect(screen.queryByTestId("slot-commandbar")).toBeNull();
    });

    it("hides the top-bar Download button below md when a commandBar is supplied (avoids a redundant CTA)", () => {
      render(
        withIntl(
          <DocumentWorkspace
            {...BASE}
            commandBar={<div data-testid="slot-commandbar">command bar</div>}
          />
        )
      );
      const className = screen.getByTestId("document-download-btn").className;
      expect(className).toContain("hidden");
      expect(className).toContain("md:inline-flex");
    });

    it("keeps the top-bar Download button visible below md when no commandBar is supplied", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      const className = screen.getByTestId("document-download-btn").className;
      expect(className).not.toContain("hidden");
      expect(className).toContain("inline-flex");
    });
  });
});
