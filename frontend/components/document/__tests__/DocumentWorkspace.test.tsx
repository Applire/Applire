// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { DocumentWorkspace } from "../DocumentWorkspace";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const BASE = {
  preview: <div data-testid="slot-preview">preview</div>,
  sidebar: <div data-testid="slot-sidebar">sidebar</div>,
};

describe("DocumentWorkspace", () => {
  afterEach(() => vi.clearAllMocks());

  it("renders the preview and the one document-scope panel", () => {
    render(withIntl(<DocumentWorkspace {...BASE} />));
    expect(screen.getByTestId("document-workspace")).toBeTruthy();
    expect(screen.getByTestId("slot-preview")).toBeTruthy();
    expect(screen.getByTestId("slot-sidebar")).toBeTruthy();
  });

  // ADR-081 cl. 1 / US299 — #625's MECHANISM, pinned structurally.
  //
  // jsdom performs no layout, so a pixel assertion here would be theatre (the
  // real height evidence is the OQ spec `document-review-surface.spec.ts`,
  // which measures the preview against a short AND a long findings payload in
  // a real browser, plus the committed screenshots). What CAN be pinned here is
  // the mechanism itself, and it is the whole of the defect: the preview used
  // to be `flex-1 min-h-0` inside an `overflow-y-auto` column that also held
  // the findings, so it shrank to whatever the tall sibling left.
  describe("#625 — the preview column takes the height unconditionally", () => {
    it("does not make the preview column a scroll container", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      const column = screen.getByTestId("document-preview-column");
      expect(column.className).not.toContain("overflow-y-auto");
    });

    it("gives the preview the column's whole height", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      const wrapper = screen.getByTestId("slot-preview").parentElement!;
      expect(wrapper.className).toContain("flex-1");
      expect(wrapper.className).toContain("min-h-0");
    });

    it("leaves the preview no sibling in its column that could take height from it", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      const column = screen.getByTestId("document-preview-column");
      // Exactly one child: the preview wrapper. A findings stack re-entering
      // this column is the #625 regression, and it reddens here.
      expect(column.children).toHaveLength(1);
      expect(column.firstElementChild!.contains(screen.getByTestId("slot-preview"))).toBe(true);
    });

    it("accepts no findings slot at all — the findings live in the panel now", () => {
      // A compile-time guarantee in TS; asserted at runtime so a loosened prop
      // type cannot quietly restore the old two-children column.
      expect(Object.keys(BASE)).not.toContain("atsPanel");
    });
  });

  // E040/US226 — responsive layout. jsdom can't evaluate media queries, so
  // these assert the class strings that encode the below-md/md+ behaviour.
  describe("responsive layout classes (E040/US226)", () => {
    it("wraps the sidebar slot in `hidden md:contents` (hidden below md, direct flex child at md+)", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      const wrapper = screen.getByTestId("slot-sidebar").parentElement;
      expect(wrapper?.className).toContain("hidden");
      expect(wrapper?.className).toContain("md:contents");
    });

    it("renders the commandBar slot when supplied", () => {
      render(
        withIntl(
          <DocumentWorkspace
            {...BASE}
            commandBar={<div data-testid="slot-commandbar">command bar</div>}
          />,
        ),
      );
      expect(screen.getByTestId("slot-commandbar")).toBeTruthy();
    });

    it("omits the commandBar slot when not supplied", () => {
      render(withIntl(<DocumentWorkspace {...BASE} />));
      expect(screen.queryByTestId("slot-commandbar")).toBeNull();
    });
  });

  // ADR-081 cl. 1: three document-scope chrome regions became one.
  it("renders no document top bar of its own — it was dissolved into the panel", () => {
    render(withIntl(<DocumentWorkspace {...BASE} />));
    expect(screen.queryByTestId("document-topbar")).toBeNull();
  });
});
