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
import { GapHint } from "../GapHint";

const GAP = { id: "Python", label: "Python" };

const BASE_PROPS = {
  gap: GAP,
  onDismiss: vi.fn(),
  onAddressGap: vi.fn(),
};

describe("GapHint", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders gap label", () => {
    render(withIntl(<GapHint {...BASE_PROPS} />));
    expect(screen.getByText("Python")).toBeTruthy();
  });

  it("'Write it myself' button calls onDismiss with gap id", () => {
    const onDismiss = vi.fn();
    render(withIntl(<GapHint {...BASE_PROPS} onDismiss={onDismiss} />));
    fireEvent.click(screen.getByTestId("write-myself-btn"));
    expect(onDismiss).toHaveBeenCalledWith("Python");
  });

  it("'Let Kaile help' button calls onAddressGap with gap id", () => {
    const onAddressGap = vi.fn();
    render(withIntl(<GapHint {...BASE_PROPS} onAddressGap={onAddressGap} />));
    fireEvent.click(screen.getByTestId("kaile-help-btn"));
    expect(onAddressGap).toHaveBeenCalledWith("Python");
  });

  it("'Let Kaile help' button does not open inline session", () => {
    render(withIntl(<GapHint {...BASE_PROPS} />));
    fireEvent.click(screen.getByTestId("kaile-help-btn"));
    // No AssistMicroSession — no question text should appear
    expect(screen.queryByTestId("assist-question")).toBeNull();
  });

  // #117 (ADR-019/048): an honest gap must never invite a written claim —
  // no write/Kaile CTAs; it routes to profile enrichment instead.
  describe("honest gap kind", () => {
    const HONEST = { id: "DevSecOps", label: "DevSecOps", kind: "honest" as const };

    it("hides the write-myself and Kaile CTAs", () => {
      render(withIntl(<GapHint {...BASE_PROPS} gap={HONEST} />));
      expect(screen.queryByTestId("write-myself-btn")).toBeNull();
      expect(screen.queryByTestId("kaile-help-btn")).toBeNull();
    });

    it("shows the not-in-profile tag and an enrich-profile CTA", () => {
      const onEnrichProfile = vi.fn();
      render(withIntl(
        <GapHint {...BASE_PROPS} gap={HONEST} onEnrichProfile={onEnrichProfile} />
      ));
      expect(screen.getByTestId("honest-gap-tag")).toBeTruthy();
      fireEvent.click(screen.getByTestId("enrich-profile-btn"));
      expect(onEnrichProfile).toHaveBeenCalledWith("DevSecOps");
    });

    it("still allows dismissing the hint", () => {
      const onDismiss = vi.fn();
      render(withIntl(<GapHint {...BASE_PROPS} gap={HONEST} onDismiss={onDismiss} />));
      fireEvent.click(screen.getByTestId("dismiss-hint-btn"));
      expect(onDismiss).toHaveBeenCalledWith("DevSecOps");
    });
  });

  it("claimable kind (default) keeps both write CTAs", () => {
    render(withIntl(
      <GapHint {...BASE_PROPS} gap={{ id: "Python", label: "Python", kind: "claimable" }} />
    ));
    expect(screen.getByTestId("write-myself-btn")).toBeTruthy();
    expect(screen.getByTestId("kaile-help-btn")).toBeTruthy();
  });
});
