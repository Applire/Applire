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

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";

import UnaskedRequirementsPanel from "@/components/gaps/UnaskedRequirementsPanel";
import de from "@/messages/de.json";
import en from "@/messages/en.json";

function renderPanel(
  requirements: Parameters<typeof UnaskedRequirementsPanel>[0]["requirements"],
  locale: "de" | "en" = "de",
) {
  return render(
    <NextIntlClientProvider locale={locale} messages={locale === "de" ? de : en}>
      <UnaskedRequirementsPanel requirements={requirements} />
    </NextIntlClientProvider>,
  );
}

describe("UnaskedRequirementsPanel (ADR-074)", () => {
  it("renders nothing when no requirement went unasked", () => {
    const { container } = renderPanel([]);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the field is absent on a legacy response", () => {
    const { container } = renderPanel(undefined);
    expect(container).toBeEmptyDOMElement();
  });

  it("names every unasked requirement", () => {
    renderPanel([{ concept: "Digitalisierung" }, { concept: "Investitionsverantwortung" }]);
    expect(screen.getByTestId("unasked-requirement-0")).toHaveTextContent("Digitalisierung");
    expect(screen.getByTestId("unasked-requirement-1")).toHaveTextContent(
      "Investitionsverantwortung",
    );
  });

  it("says the posting asks for it and that we never asked — never that the candidate lacks it", () => {
    // The wording IS the decision (ADR-074 clause 3). "You lack X" is a claim
    // about a person that nothing in this system evidences; "we never asked" is
    // the only thing that is actually true.
    renderPanel([{ concept: "Digitalisierung" }]);
    const subtitle = screen.getByTestId("unasked-requirements-subtitle").textContent ?? "";
    expect(subtitle).toMatch(/verlangt/i);
    expect(subtitle).toMatch(/gefragt/i);
    expect(subtitle).not.toMatch(/dir fehlt|fehlt dir|kannst du nicht/i);
  });

  it("keeps the reassurance in English too", () => {
    renderPanel([{ concept: "Digitalisierung" }], "en");
    expect(screen.getByTestId("unasked-requirements-subtitle").textContent).toMatch(
      /never asked/i,
    );
    expect(screen.getByTestId("unasked-requirements-reassurance").textContent).toMatch(
      /do not know/i,
    );
  });

  it("carries a German title, not the English string copied across", () => {
    // Key parity cannot see this: a `de` value left in English passes it.
    renderPanel([{ concept: "Digitalisierung" }], "de");
    expect(screen.getByTestId("unasked-requirements-title").textContent).toBe("Nicht erfragt");
  });
});
