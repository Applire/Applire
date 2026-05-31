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
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { RefinementPanel } from "../RefinementPanel";

beforeEach(() => {
  globalThis.fetch = vi.fn(() =>
    Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ sections: [], general_gaps: [] }),
    }),
  ) as unknown as typeof fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
});

const BASE_PROPS = {
  cvId: "cv-1",
  flowId: "flow-1",
  roleTitle: "QA Manager — Frankfurt",
  gapSummary: { gaps: [], sections: [] },
  cvSummary: { sections: [] },
  templateLabel: "Classic German",
  matchScore: 0.82,
  expiryWarning: null as { level: "none" | "warning" | "critical"; expiresIn: string } | null,
  detectedCompany: null as { name: string; hex: string } | null,
  currentAccentHex: "#003399",
  onHtmlRefresh: vi.fn(),
  onRegenerateSame: vi.fn(),
  onRegenerateDifferent: vi.fn(),
  collapsed: false,
  onToggleCollapse: vi.fn(),
};

describe("RefinementPanel", () => {
  it("renders the status header with role title and match score", () => {
    render(withIntl(<RefinementPanel {...BASE_PROPS} />));
    expect(screen.getByTestId("refinement-header")).toBeTruthy();
    expect(screen.getByText("QA Manager — Frankfurt")).toBeTruthy();
    expect(screen.getByText("82%")).toBeTruthy();
  });

  it("renders exactly two tabs: Inhalt and Design", () => {
    render(withIntl(<RefinementPanel {...BASE_PROPS} />));
    expect(screen.getByTestId("tab-content")).toBeTruthy();
    expect(screen.getByTestId("tab-design")).toBeTruthy();
    expect(screen.queryByTestId("tab-actions")).toBeNull();
    expect(screen.queryByTestId("tab-appearance")).toBeNull();
  });

  it("defaults to the Inhalt tab", () => {
    render(withIntl(<RefinementPanel {...BASE_PROPS} />));
    expect(screen.getByTestId("tab-content").getAttribute("aria-selected")).toBe("true");
  });

  it("clicking Design tab switches to design content", () => {
    render(withIntl(<RefinementPanel {...BASE_PROPS} />));
    fireEvent.click(screen.getByTestId("tab-design"));
    expect(screen.getByTestId("design-tab")).toBeTruthy();
  });

  it("does not render a sticky footer with a Download button", () => {
    render(withIntl(<RefinementPanel {...BASE_PROPS} />));
    expect(screen.queryByTestId("download-pdf-btn")).toBeNull();
  });

  it("collapsed view shows only icon strip with two tab icons", () => {
    render(withIntl(<RefinementPanel {...BASE_PROPS} collapsed={true} />));
    expect(screen.getByTestId("cv-tab-icon-content")).toBeTruthy();
    expect(screen.getByTestId("cv-tab-icon-design")).toBeTruthy();
    expect(screen.queryByTestId("cv-tab-icon-actions")).toBeNull();
  });
});
