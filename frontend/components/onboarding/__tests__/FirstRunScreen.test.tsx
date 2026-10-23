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

import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { FirstRunScreen } from "../FirstRunScreen";
import { withIntl } from "@/lib/test-utils/with-intl";

vi.mock("@/lib/import-capabilities", async (orig) => ({
  ...(await orig<typeof import("@/lib/import-capabilities")>()),
  useImportCapabilities: () => ({ imageImport: false }),
}));
vi.mock("@/components/processing-overlay", () => ({ ProcessingOverlay: () => <div data-testid="processing-overlay" /> }));

describe("FirstRunScreen (#789 first run has no orientation)", () => {
  it("names the five steps with their durations before asking for a file", () => {
    render(withIntl(<FirstRunScreen />));
    const steps = screen.getAllByTestId("first-run-step");
    expect(steps).toHaveLength(5);
    expect(steps[3]).toHaveTextContent("optional");
    const list = screen.getByTestId("first-run-steps");
    expect(list.compareDocumentPosition(screen.getByTestId("upload-area")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says 'upload at least one' only after an attempt, and the button still reads as disabled", () => {
    render(withIntl(<FirstRunScreen />));
    const submit = screen.getByTestId("submit-button");
    expect(submit).toHaveAttribute("aria-disabled", "true");
    expect(screen.queryByTestId("upload-at-least-one")).toBeNull();
    fireEvent.click(submit);
    expect(screen.getByTestId("upload-at-least-one")).toBeInTheDocument();
    expect(screen.getByTestId("upload-at-least-one").className).not.toContain("amber");
    expect(screen.queryByTestId("processing-overlay")).toBeNull();
  });
});
