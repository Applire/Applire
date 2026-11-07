// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// 789:264 (Hazelnut build 7, WP-F): the CV's language says where it comes from
// on the template step — the Match page's control, reused, plus one sentence.
import { render, screen } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { CvLanguageChoice } from "../CvLanguageChoice";
import { withIntl } from "@/lib/test-utils/with-intl";

function mockFetch(job: unknown, app: unknown) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/api/job/")) return { ok: job !== null, json: async () => job } as Response;
    if (url.includes("/api/applications/")) return { ok: app !== null, json: async () => app } as Response;
    return { ok: false, json: async () => ({}) } as Response;
  });
}

describe("CvLanguageChoice", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the detected posting language as auto-detected, with the sentence naming the default", async () => {
    mockFetch({ jd_language: "en" }, { language_override: null });
    render(withIntl(<CvLanguageChoice applicationId="a1" jobId="j1" apiBase="" />, "de"));
    expect(await screen.findByTestId("doc-language-auto-badge")).toBeInTheDocument();
    expect(screen.getByTestId("doc-language-en")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("cv-language-follows-posting").textContent).toContain("Wie die Stellenanzeige");
  });

  it("a chosen language is shown as chosen (no auto badge)", async () => {
    mockFetch({ jd_language: "en" }, { language_override: "de" });
    render(withIntl(<CvLanguageChoice applicationId="a1" jobId="j1" apiBase="" />, "de"));
    expect(await screen.findByTestId("doc-language-de")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("doc-language-auto-badge")).toBeNull();
  });

  it("stays hidden when a read fails", async () => {
    mockFetch({ jd_language: "en" }, null);
    render(withIntl(<CvLanguageChoice applicationId="a1" jobId="j1" apiBase="" />, "de"));
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByTestId("cv-language-choice")).toBeNull();
  });
});
