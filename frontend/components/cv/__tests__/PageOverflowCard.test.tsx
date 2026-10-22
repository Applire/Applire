// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// #786 L-3 (founder ruling 2026-10-22): the x+1 card above the CV preview.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { PageOverflowCard, type PageOverflow } from "../PageOverflowCard";

const PROPOSAL: PageOverflow = {
  target: 2,
  actual: 3,
  proposed: [
    { kind: "oldest_certification", items: ["REFA-Grundschein", "Sicherheitsbeauftragter"] },
    { kind: "off_job_skills", items: ["Kosmetik-Verpackungen"] },
  ],
  choice: null,
  cut_available: true,
  uncut_available: false,
};

function mockFetch(status: PageOverflow | null, post?: PageOverflow, postOk = true) {
  const calls: { url: string; init?: RequestInit }[] = [];
  global.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (init?.method === "POST") {
      return { ok: postOk, status: postOk ? 200 : 409, json: async () => post } as Response;
    }
    return { ok: true, status: 200, json: async () => ({ page_overflow: status }) } as Response;
  }) as typeof fetch;
  return calls;
}

describe("PageOverflowCard", () => {
  afterEach(() => vi.restoreAllMocks());

  it("names each cut with its items and offers keep and cut (DE copy)", async () => {
    mockFetch(PROPOSAL);
    render(withIntl(<PageOverflowCard cvId="cv-1" />, "de"));
    const card = await screen.findByTestId("cv-page-overflow-card");
    expect(card.textContent).toContain("Dein Lebenslauf braucht 3 Seiten statt 2 Seiten");
    expect(card.textContent).toContain("Ältere Weiterbildungen und Zertifikate: REFA-Grundschein, Sicherheitsbeauftragter");
    expect(card.textContent).toContain("Kenntnisse, die die Stelle nicht nennt: Kosmetik-Verpackungen");
    expect(screen.getByTestId("cv-page-overflow-keep").textContent).toBe("3 Seiten behalten");
    expect(screen.getByTestId("cv-page-overflow-cut").textContent).toBe("Auf 2 Seiten kürzen");
  });

  it("renders nothing when the CV fits, or when nothing could be cut", async () => {
    mockFetch(null);
    const { container, rerender } = render(withIntl(<PageOverflowCard cvId="cv-1" />));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
    mockFetch({ ...PROPOSAL, proposed: [] });
    rerender(withIntl(<PageOverflowCard cvId="cv-2" />));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(screen.queryByTestId("cv-page-overflow-card")).toBeNull();
  });

  it("cut posts the choice, names what went, and refreshes the preview", async () => {
    const calls = mockFetch(PROPOSAL, { ...PROPOSAL, choice: "cut", cut_available: false, uncut_available: true });
    const onContentChanged = vi.fn();
    render(withIntl(<PageOverflowCard cvId="cv-1" onContentChanged={onContentChanged} />));
    fireEvent.click(await screen.findByTestId("cv-page-overflow-cut"));
    const done = await screen.findByTestId("cv-page-overflow-done");
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect(post.url).toContain("/api/cv/cv-1/page-overflow");
    expect(JSON.parse(String(post.init!.body))).toEqual({ choice: "cut" });
    expect(done.textContent).toContain("Cut to 2 pages — removed: REFA-Grundschein, Sicherheitsbeauftragter, Kosmetik-Verpackungen.");
    expect(screen.getByTestId("cv-page-overflow-switch").textContent).toBe("Keep 3 pages after all");
    expect(onContentChanged).toHaveBeenCalledTimes(1);
  });

  it("keep only remembers the choice — no preview refresh — and offers the cut again", async () => {
    mockFetch(PROPOSAL, { ...PROPOSAL, choice: "keep" });
    const onContentChanged = vi.fn();
    render(withIntl(<PageOverflowCard cvId="cv-1" onContentChanged={onContentChanged} />));
    fireEvent.click(await screen.findByTestId("cv-page-overflow-keep"));
    const done = await screen.findByTestId("cv-page-overflow-done");
    expect(done.textContent).toContain("3 pages — your choice.");
    expect(screen.getByTestId("cv-page-overflow-switch").textContent).toBe("Cut to 2 pages after all");
    expect(onContentChanged).not.toHaveBeenCalled();
  });

  it("a remembered choice shows as the done line on reload", async () => {
    mockFetch({ ...PROPOSAL, choice: "cut", cut_available: false, uncut_available: false });
    render(withIntl(<PageOverflowCard cvId="cv-1" />));
    const done = await screen.findByTestId("cv-page-overflow-done");
    expect(done.getAttribute("data-choice")).toBe("cut");
    // The CV changed since the cut — no switch offered.
    expect(screen.queryByTestId("cv-page-overflow-switch")).toBeNull();
  });

  it("cut is disabled when the backend says the CV changed since the proposal", async () => {
    mockFetch({ ...PROPOSAL, cut_available: false });
    render(withIntl(<PageOverflowCard cvId="cv-1" />));
    expect(((await screen.findByTestId("cv-page-overflow-cut")) as HTMLButtonElement).disabled).toBe(true);
  });

  it("a refused choice shows the error and keeps the card", async () => {
    mockFetch(PROPOSAL, undefined, false);
    render(withIntl(<PageOverflowCard cvId="cv-1" />));
    fireEvent.click(await screen.findByTestId("cv-page-overflow-cut"));
    expect((await screen.findByTestId("cv-page-overflow-error")).textContent).toContain("That did not work");
    expect(screen.getByTestId("cv-page-overflow-card")).toBeTruthy();
  });

  it("target 1 reads singular", async () => {
    mockFetch({ ...PROPOSAL, target: 1, actual: 2 });
    render(withIntl(<PageOverflowCard cvId="cv-1" />, "de"));
    expect((await screen.findByTestId("cv-page-overflow-cut")).textContent).toBe("Auf 1 Seite kürzen");
  });
});
