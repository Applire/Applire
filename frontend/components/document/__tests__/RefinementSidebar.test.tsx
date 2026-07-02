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
import { RefinementSidebar } from "../RefinementSidebar";

const TABS = [
  { id: "content", label: "Inhalt", body: <div data-testid="body-content">C</div>, footer: <div data-testid="footer-content">FC</div> },
  { id: "design", label: "Design", body: <div data-testid="body-design">D</div>, footer: <div data-testid="footer-design">FD</div> },
  { id: "actions", label: "Aktionen", body: <div data-testid="body-actions">A</div> },
];

const BASE = {
  matchScore: 39,
  validity: { label: "gültig bis 29.9.2026", level: "warning" as const },
  tabs: TABS,
  collapsed: false,
  onToggleCollapse: vi.fn(),
};

describe("RefinementSidebar", () => {
  afterEach(() => vi.clearAllMocks());

  it("renders a navy status header with the match score and validity", () => {
    render(withIntl(<RefinementSidebar {...BASE} />));
    expect(screen.getByTestId("sidebar-status-header")).toBeTruthy();
    expect(screen.getByText("39%")).toBeTruthy();
    expect(screen.getByText("gültig bis 29.9.2026")).toBeTruthy();
  });

  it("rounds a fractional match score to a whole percent", () => {
    render(withIntl(<RefinementSidebar {...BASE} matchScore={57.14285714285713} />));
    expect(screen.getByText("57%")).toBeTruthy();
    expect(screen.queryByText(/57\.14/)).toBeNull();
  });

  it("does NOT show any premium / pro-tier framing (E038 decision)", () => {
    render(withIntl(<RefinementSidebar {...BASE} />));
    expect(screen.queryByText(/premium|pro zugang|pro tip/i)).toBeNull();
  });

  it("renders all tab labels and shows the first tab body by default", () => {
    render(withIntl(<RefinementSidebar {...BASE} />));
    expect(screen.getByTestId("sidebar-tab-content")).toBeTruthy();
    expect(screen.getByTestId("sidebar-tab-design")).toBeTruthy();
    expect(screen.getByTestId("sidebar-tab-actions")).toBeTruthy();
    expect(screen.getByTestId("body-content")).toBeTruthy();
    expect(screen.queryByTestId("body-design")).toBeNull();
  });

  it("switches the active body and footer when a tab is clicked", () => {
    render(withIntl(<RefinementSidebar {...BASE} />));
    expect(screen.getByTestId("footer-content")).toBeTruthy();
    fireEvent.click(screen.getByTestId("sidebar-tab-design"));
    expect(screen.getByTestId("body-design")).toBeTruthy();
    expect(screen.queryByTestId("body-content")).toBeNull();
    expect(screen.getByTestId("footer-design")).toBeTruthy();
    expect(screen.queryByTestId("footer-content")).toBeNull();
  });

  it("renders no footer region for a tab without a footer", () => {
    render(withIntl(<RefinementSidebar {...BASE} />));
    fireEvent.click(screen.getByTestId("sidebar-tab-actions"));
    expect(screen.queryByTestId("sidebar-footer")).toBeNull();
  });

  it("collapses to a rail with an expand control and hides tab bodies", () => {
    render(withIntl(<RefinementSidebar {...BASE} collapsed />));
    expect(screen.getByTestId("sidebar-expand-btn")).toBeTruthy();
    expect(screen.queryByTestId("body-content")).toBeNull();
  });
});
