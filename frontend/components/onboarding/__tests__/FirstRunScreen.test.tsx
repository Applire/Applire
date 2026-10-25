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
import { fireEvent, render, screen, within } from "@testing-library/react";
import { FirstRunScreen } from "../FirstRunScreen";
import { withIntl } from "@/lib/test-utils/with-intl";
import deCatalog from "@/messages/de.json";
import enCatalog from "@/messages/en.json";

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

  it("789:178: on phone steps 1 and 3 still show their duration (bodies are hidden below md)", () => {
    render(withIntl(<FirstRunScreen />));
    const steps = screen.getAllByTestId("first-run-step");
    const tag1 = within(steps[0]).getByTestId("first-run-step-phone-tag");
    const tag3 = within(steps[2]).getByTestId("first-run-step-phone-tag");
    expect(tag1).toHaveTextContent("1–2 min per file");
    expect(tag3).toHaveTextContent("a few min");
    expect(tag1.className).toContain("md:hidden");
    expect(within(steps[1]).queryByTestId("first-run-step-phone-tag")).toBeNull();
  });

  it.each([["de", deCatalog], ["en", enCatalog]] as const)(
    "%s: the phone chip names the same duration as the desktop body",
    (_l, cat) => {
      const home = (cat as { home: Record<string, string> }).home;
      expect(home.step1Body).toContain("1–2");
      expect(home.step1PhoneTag).toContain("1–2");
    },
  );

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
