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

  it("renders nothing when the CV fits", async () => {
    mockFetch(null);
    const { container } = render(withIntl(<PageOverflowCard cvId="cv-1" />));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  // INTERFACE B-2 / ADR-099 cl. 7: the card shows WHENEVER the CV is over its
  // target — even when nothing can be cut (only "keep" is offered then).
  it("shows the card with only keep when nothing can be cut (cut_pages null)", async () => {
    mockFetch({ ...PROPOSAL, proposed: [], cut_available: false, reaches_target: false, cut_pages: null });
    render(withIntl(<PageOverflowCard cvId="cv-2" />, "de"));
    const card = await screen.findByTestId("cv-page-overflow-card");
    expect(screen.getByTestId("cv-page-overflow-keep")).toBeTruthy();
    expect(screen.queryByTestId("cv-page-overflow-cut")).toBeNull();
    // U-6 (founder ruling 2026-10-28): it says only that it cannot get shorter —
    // the ladder never checked what the job asks for.
    expect(screen.getByTestId("cv-page-overflow-body").textContent).toBe("Kürzer geht es nicht.");
    expect(card.textContent).toContain("3 Seiten");
  });

  it("names the closest reachable length when the target cannot be reached", async () => {
    mockFetch({ ...PROPOSAL, reaches_target: false, cut_pages: 3, actual: 4 });
    render(withIntl(<PageOverflowCard cvId="cv-3" />));
    await screen.findByTestId("cv-page-overflow-card");
    const body = screen.getByTestId("cv-page-overflow-body").textContent ?? "";
    expect(body).toContain("cannot reach 2 pages");
    expect(body).toContain("The closest is 3 pages");
    expect(body).not.toContain("the job asks for");
    expect(screen.getByTestId("cv-page-overflow-cut")).toBeTruthy();
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
    // The CV changed since the cut — no switch offered, and the card says why.
    expect(screen.queryByTestId("cv-page-overflow-switch")).toBeNull();
    expect(screen.getByTestId("cv-page-overflow-undo-gone").textContent).toBe(
      "This can no longer be undone: the CV has changed since the cut.",
    );
  });

  it("an undo refused with 409 re-reads the state and says why, not a generic error (MD3-3)", async () => {
    const cutState = { ...PROPOSAL, choice: "cut" as const, cut_available: false, uncut_available: true };
    let statusReads = 0;
    global.fetch = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") return { ok: false, status: 409, json: async () => ({}) } as Response;
      statusReads += 1;
      const po = statusReads === 1 ? cutState : { ...cutState, uncut_available: false };
      return { ok: true, status: 200, json: async () => ({ page_overflow: po }) } as Response;
    }) as typeof fetch;
    render(withIntl(<PageOverflowCard cvId="cv-1" />, "de"));
    fireEvent.click(await screen.findByTestId("cv-page-overflow-switch"));
    expect((await screen.findByTestId("cv-page-overflow-undo-gone")).textContent).toBe(
      "Rückgängig machen geht hier nicht mehr: Der Lebenslauf hat sich seit dem Kürzen geändert.",
    );
    expect(screen.queryByTestId("cv-page-overflow-error")).toBeNull();
    expect(screen.queryByTestId("cv-page-overflow-switch")).toBeNull();
  });

  it("cut is disabled when the backend says the CV changed since the proposal", async () => {
    mockFetch({ ...PROPOSAL, cut_available: false });
    render(withIntl(<PageOverflowCard cvId="cv-1" />));
    expect(((await screen.findByTestId("cv-page-overflow-cut")) as HTMLButtonElement).disabled).toBe(true);
    // #789:190 — and the card says why.
    expect((await screen.findByTestId("cv-page-overflow-cut-gone")).textContent).toContain("no longer possible");
  });

  it("no explanation line while cutting is available", async () => {
    mockFetch(PROPOSAL);
    render(withIntl(<PageOverflowCard cvId="cv-1" />));
    await screen.findByTestId("cv-page-overflow-cut");
    expect(screen.queryByTestId("cv-page-overflow-cut-gone")).toBeNull();
  });

  it("a refused choice shows the error and keeps the card", async () => {
    mockFetch(PROPOSAL, undefined, false);
    render(withIntl(<PageOverflowCard cvId="cv-1" />));
    fireEvent.click(await screen.findByTestId("cv-page-overflow-cut"));
    expect((await screen.findByTestId("cv-page-overflow-error")).textContent).toContain("That did not work");
    expect(screen.getByTestId("cv-page-overflow-card")).toBeTruthy();
  });

  it("after a cut the undo names the uncut count, not the cut CV's (A-3b)", async () => {
    // The backend re-measures on every switch: after the cut `actual` is the cut
    // CV's count (2); `full_pages` is what "keep" gives back (3).
    mockFetch(PROPOSAL, { ...PROPOSAL, actual: 2, full_pages: 3, choice: "cut", cut_available: false, uncut_available: true });
    render(withIntl(<PageOverflowCard cvId="cv-1" />, "de"));
    fireEvent.click(await screen.findByTestId("cv-page-overflow-cut"));
    await screen.findByTestId("cv-page-overflow-done");
    expect(screen.getByTestId("cv-page-overflow-switch").textContent).toBe("Doch 3 Seiten");
  });

  it("the kept line and the keep button name full_pages when the row carries it", async () => {
    mockFetch({ ...PROPOSAL, actual: 3, full_pages: 3, choice: "keep" });
    render(withIntl(<PageOverflowCard cvId="cv-1" />, "de"));
    expect((await screen.findByTestId("cv-page-overflow-done")).textContent).toContain("3 Seiten — so von dir gewählt.");
  });

  it("target 1 reads singular", async () => {
    mockFetch({ ...PROPOSAL, target: 1, actual: 2 });
    render(withIntl(<PageOverflowCard cvId="cv-1" />, "de"));
    expect((await screen.findByTestId("cv-page-overflow-cut")).textContent).toBe("Auf 1 Seite kürzen");
  });

  // U-6 (founder ruling 2026-10-28): the ladder's last rung keeps an old role's
  // title, company and dates — the card never says such a role "goes".
  describe("U-6 — an old role is shortened, never named as removed", () => {
    const OLD: PageOverflow = {
      ...PROPOSAL,
      proposed: [
        { kind: "off_job_skills", items: ["Kosmetik-Verpackungen"] },
        { kind: "old_role_bullets", items: ["Schichtleiter — Henkel AG"] },
      ],
    };

    it("the proposal says leave out OR shorten, and names the old role with what stays (DE)", async () => {
      mockFetch(OLD);
      render(withIntl(<PageOverflowCard cvId="cv-u6" />, "de"));
      await screen.findByTestId("cv-page-overflow-card");
      const body = screen.getByTestId("cv-page-overflow-body").textContent ?? "";
      expect(body).toContain("würde Applire zusätzlich Folgendes weglassen oder kürzen:");
      expect(body).not.toContain("müssten zusätzlich diese Angaben weg");
      expect(screen.getByTestId("cv-page-overflow-cuts").textContent).toContain(
        "Ältere Stationen nur mit Titel, Firma und Zeitraum: Schichtleiter — Henkel AG",
      );
    });

    it("a removal-only proposal keeps the plain 'would have to go' (EN)", async () => {
      mockFetch(PROPOSAL);
      render(withIntl(<PageOverflowCard cvId="cv-u6b" />));
      await screen.findByTestId("cv-page-overflow-card");
      expect(screen.getByTestId("cv-page-overflow-body").textContent).toContain("these would also have to go:");
    });

    it("the unreachable body follows the same rule", async () => {
      mockFetch({ ...OLD, reaches_target: false, cut_pages: 3, actual: 4 });
      render(withIntl(<PageOverflowCard cvId="cv-u6c" />));
      await screen.findByTestId("cv-page-overflow-card");
      const body = screen.getByTestId("cv-page-overflow-body").textContent ?? "";
      expect(body).toBe("Applire cannot reach 2 pages. The closest is 3 pages – for that, Applire would leave out or shorten the following:");
    });

    it.each([
      ["both", OLD.proposed, "Cut to 2 pages — removed: Kosmetik-Verpackungen; title, company and dates only: Schichtleiter — Henkel AG."],
      ["old roles only", [OLD.proposed[1]], "Cut to 2 pages — title, company and dates only: Schichtleiter — Henkel AG."],
    ])("the done line after a cut (%s) never lists the old role as removed", async (_l, proposed, line) => {
      mockFetch({ ...OLD, proposed, choice: "cut", cut_available: false, uncut_available: true });
      render(withIntl(<PageOverflowCard cvId="cv-u6d" />));
      const done = await screen.findByTestId("cv-page-overflow-done");
      expect(done.textContent).toContain(line);
      expect(done.textContent).not.toMatch(/removed:[^;]*Schichtleiter/);
    });
  });

  // D-23 (B's `auto_cuts`): what the generation already left out is named
  // ABOVE the body — and nothing renders for an empty list (older rows).
  describe("D-23 — what Applire already left out", () => {
    const AUTO: PageOverflow = {
      ...PROPOSAL,
      proposed: [],
      cut_available: false,
      reaches_target: false,
      cut_pages: null,
      auto_cuts: [
        { kind: "oldest_certification", items: ["REFA-Grundschein"] },
        { kind: "off_job_skills", items: ["Kosmetik-Verpackungen", "Lackierung"] },
      ],
    };

    it.each([
      ["de", "Damit er höchstens 3 Seiten hat, hat Applire schon weggelassen:", "Ältere Weiterbildungen und Zertifikate: REFA-Grundschein", "Kürzer geht es nicht."],
      ["en", "To keep it to at most 3 pages, Applire already left out:", "REFA-Grundschein", null],
    ] as const)("%s: the line and its list sit above the body", async (locale, lead, item, body) => {
      mockFetch(AUTO);
      render(withIntl(<PageOverflowCard cvId="cv-auto" />, locale));
      const box = await screen.findByTestId("cv-page-overflow-auto-cuts");
      expect(box.textContent).toContain(lead);
      expect(box.textContent).toContain(item);
      expect(box.querySelectorAll("li")).toHaveLength(2);
      const bodyEl = screen.getByTestId("cv-page-overflow-body");
      if (body) expect(bodyEl.textContent).toBe(body);
      // above: the auto-cut block precedes the body in document order
      expect(box.compareDocumentPosition(bodyEl) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(screen.queryByTestId("cv-page-overflow-cut")).toBeNull(); // no offer-back
    });

    it.each(["de", "en"] as const)("%s: an empty auto_cuts renders nothing (and an absent field neither)", async (locale) => {
      mockFetch({ ...AUTO, auto_cuts: [] });
      const { unmount } = render(withIntl(<PageOverflowCard cvId="cv-e" />, locale));
      await screen.findByTestId("cv-page-overflow-card");
      expect(screen.queryByTestId("cv-page-overflow-auto-cuts")).toBeNull();
      expect(screen.getByTestId("cv-page-overflow-card").textContent).not.toMatch(/weggelassen|left out/);
      unmount();
      const { auto_cuts: _drop, ...older } = AUTO;
      void _drop;
      mockFetch(older);
      render(withIntl(<PageOverflowCard cvId="cv-o" />, locale));
      await screen.findByTestId("cv-page-overflow-card");
      expect(screen.queryByTestId("cv-page-overflow-auto-cuts")).toBeNull();
    });
  });
});
