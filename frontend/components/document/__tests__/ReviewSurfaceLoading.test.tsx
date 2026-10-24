// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// R2-8 (8), Hazelnut build 4: on every CV page load the review flashed "Nicht
// geprüft: Lückenanalyse" and "Teilweise ungeprüft … lief nicht" with wrong
// counts for 1–3 s (marcus-057 vs marcus-058) — the reports were simply not
// loaded yet, and `null` reads as "did not run" (ADR-081 cl. 9). While the page
// says `loading`, the surface claims nothing.

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { ReviewSurface, type ReviewSurfaceProps } from "../ReviewSurface";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

function props(loading: boolean): ReviewSurfaceProps {
  // The half-loaded state the flash came from: the ATS report is in, the
  // truthfulness report, critic and gap clusters are still `null`.
  return {
    documentKind: "cv",
    documentId: "cv-1",
    atsReport: { checks: [], keywords: { present: [], missing: [] } },
    truthReport: null,
    criticReport: null,
    gapClusters: null,
    degradation: [{ check: "cv_language", stage: "review", reason: "timeout" }],
    loading,
  };
}

describe("R2-8 (8) — no degradation notice before the report has loaded", () => {
  it("says it is loading and nothing else (de)", () => {
    render(withIntl(<ReviewSurface {...props(true)} />, "de"));
    expect(screen.getByTestId("review-loading").textContent).toBe("Prüfung wird geladen …");
    const text = screen.getByTestId("review-surface").textContent ?? "";
    expect(text).not.toMatch(/Nicht geprüft|ungeprüft|lief nicht|Erfunden/);
    expect(screen.queryByTestId("review-verdict")).toBeNull();
  });

  it("says it is loading (en)", () => {
    render(withIntl(<ReviewSurface {...props(true)} />, "en"));
    expect(screen.getByTestId("review-loading").textContent).toBe("Loading the review …");
  });

  it("once loaded, the same null producers DO read as not checked (ADR-081 cl. 9 kept)", () => {
    render(withIntl(<ReviewSurface {...props(false)} />, "de"));
    expect(screen.queryByTestId("review-loading")).toBeNull();
    expect(screen.getByTestId("review-surface").textContent ?? "").toMatch(/Nicht geprüft/);
  });
});
