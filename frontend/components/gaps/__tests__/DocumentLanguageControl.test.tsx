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

/**
 * DocumentLanguageControl — E054/US288, ADR-038 amendment 2026-08-23.
 *
 * The user's choice of the leading document language, shown after JD analysis.
 * Clause 6: ANY interaction — including confirming the prefilled/detected
 * value — writes `language_override`; a confirmation must be distinguishable
 * from never-having-looked (the 2026-08-01 `ui_language` default-vs-choice
 * lesson).
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DocumentLanguageControl } from "../DocumentLanguageControl";
import { withIntl } from "@/lib/test-utils/with-intl";

const APP_ID = "11111111-1111-1111-1111-111111111111";

function mockFetchOk() {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ language_override: "en" }),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("DocumentLanguageControl", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("prefills with the detected language and shows the auto-detected badge", () => {
    mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageControl
          applicationId={APP_ID}
          detectedLanguage="de"
          initialOverride={null}
          apiBase=""
        />
      )
    );
    const de = screen.getByTestId("doc-language-de");
    expect(de).toHaveAttribute("aria-pressed", "true");
    // No override on record → the choice is the detector's, badge says so.
    expect(screen.getByTestId("doc-language-auto-badge")).toBeInTheDocument();
  });

  it("writes the override when the user switches the language", async () => {
    const fetchMock = mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageControl
          applicationId={APP_ID}
          detectedLanguage="de"
          initialOverride={null}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-en"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/applications/${APP_ID}`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ language_override: "en" });
  });

  it("clause 6: confirming the PREFILLED value also writes the override", async () => {
    // Clicking the already-active detected language is a real choice — it
    // must persist, otherwise a later re-analysis silently flips the
    // language against an expressed confirmation.
    const fetchMock = mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageControl
          applicationId={APP_ID}
          detectedLanguage="de"
          initialOverride={null}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-de"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ language_override: "de" });
  });

  it("hides the auto badge once an override is on record", () => {
    mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageControl
          applicationId={APP_ID}
          detectedLanguage="de"
          initialOverride="en"
          apiBase=""
        />
      )
    );
    expect(screen.getByTestId("doc-language-en")).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(screen.queryByTestId("doc-language-auto-badge")).toBeNull();
  });
});
