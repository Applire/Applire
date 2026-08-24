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
 * DocumentLanguageSwitch — E054/US289, ADR-038 amendment clause 6.
 *
 * Post-generation language switch on the document view: choosing the other
 * language shows an explicit regeneration notice that NAMES the section-
 * override loss (FMEA JF-F-G2.1 — the notice must name the loss, not just
 * "regenerates"); confirming persists `language_override` and only THEN hands
 * off to the page's existing regeneration path.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DocumentLanguageSwitch } from "../DocumentLanguageSwitch";
import { withIntl } from "@/lib/test-utils/with-intl";

const APP_ID = "22222222-2222-2222-2222-222222222222";

function mockFetchOk() {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ language_override: "en" }),
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("DocumentLanguageSwitch", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("highlights the document's pinned language", () => {
    mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          onSwitched={() => {}}
          apiBase=""
        />
      )
    );
    expect(screen.getByTestId("doc-language-switch-de")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("doc-language-switch-en")).toHaveAttribute("aria-pressed", "false");
  });

  it("clicking the other language opens the notice — nothing is written yet", () => {
    const fetchMock = mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          onSwitched={() => {}}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-switch-en"));
    expect(screen.getByTestId("doc-language-switch-dialog")).toBeInTheDocument();
    // The notice comes BEFORE the action: no PATCH until confirmed.
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("JF-F-G2.1: the notice NAMES the overridden sections that will be lost", () => {
    mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          overriddenSectionLabels={["Berufserfahrung", "Kenntnisse"]}
          onSwitched={() => {}}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-switch-en"));
    const loss = screen.getByTestId("doc-language-switch-override-loss");
    expect(loss.textContent).toContain("Berufserfahrung");
    expect(loss.textContent).toContain("Kenntnisse");
  });

  it("falls back to a generic loss sentence when no overridden sections are known", () => {
    mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          onSwitched={() => {}}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-switch-en"));
    // Still an explicit loss statement — never a bare "regenerates".
    expect(screen.getByTestId("doc-language-switch-override-loss").textContent?.length ?? 0).toBeGreaterThan(0);
  });

  it("confirm persists the override FIRST, then hands off to regeneration", async () => {
    const fetchMock = mockFetchOk();
    const onSwitched = vi.fn();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          onSwitched={onSwitched}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-switch-en"));
    fireEvent.click(screen.getByTestId("doc-language-switch-confirm"));
    await waitFor(() => expect(onSwitched).toHaveBeenCalledWith("en"));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/applications/${APP_ID}`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ language_override: "en" });
    // Dialog closes after the successful handoff.
    expect(screen.queryByTestId("doc-language-switch-dialog")).toBeNull();
  });

  it("cancel closes the dialog without writing or regenerating", () => {
    const fetchMock = mockFetchOk();
    const onSwitched = vi.fn();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          onSwitched={onSwitched}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-switch-en"));
    fireEvent.click(screen.getByTestId("doc-language-switch-cancel"));
    expect(screen.queryByTestId("doc-language-switch-dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onSwitched).not.toHaveBeenCalled();
  });

  it("a failed PATCH keeps the dialog open, shows the error, and never regenerates", async () => {
    const fn = vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) });
    vi.stubGlobal("fetch", fn);
    const onSwitched = vi.fn();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          onSwitched={onSwitched}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-switch-en"));
    fireEvent.click(screen.getByTestId("doc-language-switch-confirm"));
    await waitFor(() => expect(fn).toHaveBeenCalled());
    // Regenerating without the persisted override would generate in the OLD
    // language — the handoff must not happen on failure.
    expect(onSwitched).not.toHaveBeenCalled();
    expect(screen.getByTestId("doc-language-switch-dialog")).toBeInTheDocument();
  });

  it("clicking the already-active language is a no-op (nothing to switch)", () => {
    const fetchMock = mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage="de"
          onSwitched={() => {}}
          apiBase=""
        />
      )
    );
    fireEvent.click(screen.getByTestId("doc-language-switch-de"));
    expect(screen.queryByTestId("doc-language-switch-dialog")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("legacy row (no pinned language): neither segment is highlighted, both open the dialog", () => {
    mockFetchOk();
    render(
      withIntl(
        <DocumentLanguageSwitch
          applicationId={APP_ID}
          documentLanguage={null}
          onSwitched={() => {}}
          apiBase=""
        />
      )
    );
    expect(screen.getByTestId("doc-language-switch-de")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("doc-language-switch-en")).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByTestId("doc-language-switch-de"));
    expect(screen.getByTestId("doc-language-switch-dialog")).toBeInTheDocument();
  });
});
