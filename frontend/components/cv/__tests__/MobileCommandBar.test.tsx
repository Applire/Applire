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

import { render, screen, fireEvent, type RenderResult } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { MobileCommandBar } from "../MobileCommandBar";
import type { ATSReport } from "../ATSChecksPanel";

const REPORT: ATSReport = {
  checks: [
    { id: "contact-name", status: "pass" },
    { id: "contact-email", status: "pass" },
    { id: "work-1", status: "fail" },
  ],
  keywords: { present: ["react"], missing: ["docker"] },
};

function renderBar(overrides: Partial<React.ComponentProps<typeof MobileCommandBar>> = {}) {
  const props = {
    atsReport: REPORT,
    atsPanel: <div data-testid="slot-ats">ats-panel</div>,
    fineTuneSurface: <div data-testid="slot-finetune">finetune-surface</div>,
    onDownloadPdf: vi.fn(),
    ...overrides,
  };
  render(withIntl(<MobileCommandBar {...props} />));
  return props;
}

/**
 * Like `renderBar`, but keeps the base props and the RTL `rerender` handle so
 * a test can flip `suspended` (or any other prop) on the SAME mounted tree —
 * needed to prove the sheet stays mounted rather than being unmounted and
 * remounted with a new value.
 */
function renderBarWithRerender(overrides: Partial<React.ComponentProps<typeof MobileCommandBar>> = {}) {
  const props = {
    atsReport: REPORT,
    atsPanel: <div data-testid="slot-ats">ats-panel</div>,
    fineTuneSurface: <div data-testid="slot-finetune">finetune-surface</div>,
    onDownloadPdf: vi.fn(),
    ...overrides,
  };
  const result: RenderResult = render(withIntl(<MobileCommandBar {...props} />));
  return {
    ...result,
    rerender: (next: Partial<React.ComponentProps<typeof MobileCommandBar>>) =>
      result.rerender(withIntl(<MobileCommandBar {...props} {...next} />)),
  };
}

describe("MobileCommandBar", () => {
  afterEach(() => vi.clearAllMocks());

  it("renders the three command actions", () => {
    renderBar();
    expect(screen.getByTestId("command-ats")).toBeTruthy();
    expect(screen.getByTestId("command-finetune")).toBeTruthy();
    expect(screen.getByTestId("command-download")).toBeTruthy();
  });

  it("shows a pass-count badge reflecting passing checks", () => {
    renderBar();
    // 2 of the 3 checks pass
    expect(screen.getByTestId("command-ats-badge").textContent).toContain("2");
  });

  it("omits the badge when no ATS report is available", () => {
    renderBar({ atsReport: null });
    expect(screen.queryByTestId("command-ats-badge")).toBeNull();
  });

  it("wires the primary download action", () => {
    const { onDownloadPdf } = renderBar();
    fireEvent.click(screen.getByTestId("command-download"));
    expect(onDownloadPdf).toHaveBeenCalledOnce();
  });

  it("opens and closes the ATS bottom sheet", () => {
    renderBar();
    expect(screen.queryByTestId("slot-ats")).toBeNull();
    fireEvent.click(screen.getByTestId("command-ats"));
    expect(screen.getByTestId("command-sheet")).toBeTruthy();
    expect(screen.getByTestId("slot-ats")).toBeTruthy();
    fireEvent.click(screen.getByTestId("command-sheet-close"));
    expect(screen.queryByTestId("slot-ats")).toBeNull();
  });

  it("opens the Fine-tune sheet lazily, and no longer disclaims it", () => {
    renderBar();
    // Not mounted before the sheet is opened
    expect(screen.queryByTestId("slot-finetune")).toBeNull();
    fireEvent.click(screen.getByTestId("command-finetune"));
    expect(screen.getByTestId("slot-finetune")).toBeTruthy();
    // US228: the "open this on your computer" notice is retired — the editing
    // surface is real here now, so the disclaimer would be a false statement,
    // not a caveat. Asserted as an ABSENCE on purpose: the notice's catalog key
    // is gone from both locales, and next-intl renders a missing key as its own
    // name rather than throwing, so nothing else would have caught a leftover.
    expect(screen.queryByTestId("command-finetune-degraded")).toBeNull();
  });

  it("only one sheet is open at a time", () => {
    renderBar();
    fireEvent.click(screen.getByTestId("command-ats"));
    expect(screen.getByTestId("slot-ats")).toBeTruthy();
    fireEvent.click(screen.getByTestId("command-finetune"));
    expect(screen.queryByTestId("slot-ats")).toBeNull();
    expect(screen.getByTestId("slot-finetune")).toBeTruthy();
  });

  // ADR-090 cl. 1 (phone) — `suspended`: the open sheet stays MOUNTED but
  // hidden while *Show me where* takes over the screen, so the review surface
  // underneath keeps its state and *Back to review* returns to the same card.
  describe("suspended (ADR-090 cl. 1)", () => {
    it("keeps the open sheet mounted but applies the hidden class", () => {
      const { rerender } = renderBarWithRerender();
      fireEvent.click(screen.getByTestId("command-ats"));
      const sheet = screen.getByTestId("command-sheet");
      // Kills: dropping the `suspended` prop/className branch entirely, or a
      // mutation that always renders "flex" regardless of `suspended` — both
      // leave the sheet visible instead of hidden.
      expect(sheet.classList.contains("hidden")).toBe(false);
      rerender({ suspended: true });
      const sheetAfter = screen.getByTestId("command-sheet");
      // Still mounted (not unmounted/removed) — the surface underneath keeps
      // its state. Kills: a mutation that unmounts the sheet instead of
      // hiding it (e.g. `{sheet && !suspended && (...)}`).
      expect(screen.getByTestId("slot-ats")).toBeTruthy();
      expect(sheetAfter.classList.contains("hidden")).toBe(true);
    });

    it("does not apply the hidden class when not suspended", () => {
      renderBar({ suspended: false });
      fireEvent.click(screen.getByTestId("command-ats"));
      // Control for the mutation above: without `suspended`, "hidden" must
      // never appear, so the two tests together pin the branch both ways.
      expect(screen.getByTestId("command-sheet").classList.contains("hidden")).toBe(false);
    });

    it("Escape closes the sheet normally (control for the suspended guard)", () => {
      renderBar();
      fireEvent.click(screen.getByTestId("command-ats"));
      expect(screen.getByTestId("command-sheet")).toBeTruthy();
      fireEvent.keyDown(window, { key: "Escape" });
      expect(screen.queryByTestId("command-sheet")).toBeNull();
    });

    it("Escape does NOT close the sheet while suspended", () => {
      const { rerender } = renderBarWithRerender();
      fireEvent.click(screen.getByTestId("command-ats"));
      rerender({ suspended: true });
      fireEvent.keyDown(window, { key: "Escape" });
      // Kills: changing `if (!sheet || suspended) return;` back to
      // `if (!sheet) return;` in the Escape effect — verified by actually
      // making that edit, running this file, seeing it fail (the sheet
      // unmounted), and reverting.
      expect(screen.getByTestId("command-sheet")).toBeTruthy();
      expect(screen.getByTestId("slot-ats")).toBeTruthy();
    });
  });

  // ADR-081 cl. 6 / ADR-090 cl. 6 — `openCount`: the one send-blocking number
  // pre-empts the pass-count badge on the review button.
  describe("openCount badge (ADR-081 cl. 6 / ADR-090 cl. 6)", () => {
    it("shows the critical open-count badge and hides the pass badge when > 0", () => {
      renderBar({ openCount: 3 });
      const badge = screen.getByTestId("command-ats-open-badge");
      // Kills: a mutation that drops the `openCount > 0` guard (or the whole
      // ternary), which would leave the pass badge showing instead. Verified
      // by actually changing `openCount !== null && openCount > 0` to `false`
      // in MobileCommandBar.tsx, running this file (this assertion turned
      // red because command-ats-open-badge disappeared), and reverting —
      // confirmed clean with `git diff --stat`.
      expect(badge.textContent).toContain("3");
      expect(badge.getAttribute("aria-label")).toBe(
        "3 places in the document are not covered by your profile."
      );
      expect(screen.queryByTestId("command-ats-badge")).toBeNull();
    });

    it("falls back to the pass badge when openCount is 0", () => {
      renderBar({ openCount: 0 });
      // Kills: a mutation that checks only `openCount !== null` (dropping
      // `> 0`), which would show the critical badge at 0 instead of the pass
      // badge.
      expect(screen.queryByTestId("command-ats-open-badge")).toBeNull();
      expect(screen.getByTestId("command-ats-badge").textContent).toContain("2");
    });

    it("falls back to the pass badge when openCount is null (default)", () => {
      renderBar({ openCount: null });
      expect(screen.queryByTestId("command-ats-open-badge")).toBeNull();
      expect(screen.getByTestId("command-ats-badge").textContent).toContain("2");
    });
  });
});
