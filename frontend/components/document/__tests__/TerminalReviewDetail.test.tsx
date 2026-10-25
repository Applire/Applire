// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// R2-5 (founder ruling T-1 = A, 2026-10-24, Hazelnut build 4): the final-check
// card says its state and items in the UI language. ux-audit-2 B-2: the DE card
// printed "after 4 round(s)", "cv.role_budget", a role UUID — none may appear.

import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { ATSCheck } from "@/lib/ats-report";
import { isSilentPass } from "@/lib/ats-report";
import { TerminalReviewDetail, hasTerminalReviewCard } from "../TerminalReviewDetail";

const EN_DETAILS =
  "The terminal review settled after 4 round(s) with no blocking finding … Code checks still open on the " +
  "delivered CV (shipped and reported): cv.role_budget: Over the per-role bullet budget (role + nested " +
  "project bullets): c256b3f1-90c6-4582-964a-f468e8fd9ca5 (Weberit Kunststofftechnik GmbH): 6 bu";

const MARCUS: ATSCheck = {
  id: "terminal-review",
  status: "not_applicable",
  details: EN_DETAILS,
  details_key: "terminal-review-unverified",
  details_params: { count: 2 },
  notes: [
    { kind: "finding", text: "w2 Weberit: the MES project is repeated.", plain: "Weberit Kunststofftechnik GmbH: Das MES-Projekt steht doppelt." },
    { kind: "finding", text: "The summary repeats the 38 staff figure.", plain: null },
    { kind: "code", key: "cv.claim_missing", subjects: ["Supply Chain"], text: "claimable concept …" },
    { kind: "code", key: "cv.skill_untied", subjects: ["Verpackungsindustrie"], text: "Skills entries …" },
    { kind: "code", key: "cv.role_budget", subjects: ["Weberit Kunststofftechnik GmbH"], text: "Over the per-role bullet budget: c256b3f1-90c6-4582-964a-f468e8fd9ca5" },
    { kind: "code", key: "cv.something_new", subjects: [], text: "an EN sentence nobody translated" },
  ],
};

function renderDetail(check: ATSCheck, locale: "de" | "en" = "de", kind: "cv" | "cover-letter" = "cv") {
  return render(withIntl(<TerminalReviewDetail check={check} documentKind={kind} />, locale));
}

describe("R2-5 — the final-check card in the user's words", () => {
  it("DE: lead sentence, the reviewer's plain notes, code checks in German — no id, round count or UUID", () => {
    renderDetail(MARCUS);
    const text = screen.getByTestId("terminal-review-detail").textContent ?? "";
    expect(text).toContain("Unsere Schlussprüfung hatte 2 Hinweise, die wir nach der letzten Änderung nicht erneut prüfen konnten");
    expect(text).toContain("Weberit Kunststofftechnik GmbH: Das MES-Projekt steht doppelt.");
    expect(text).toContain("Im fertigen Lebenslauf außerdem offen:");
    expect(text).toContain("„Supply Chain“ belegt Dein Profil, im Lebenslauf fehlt es.");
    // 790:73: the instrument knows "not a skill in the profile", never "not backed" —
    // the Oracle grounds Marcus's "Verpackungsindustrie" on a responsibility line.
    expect(text).toContain(
      "„Verpackungsindustrie“ steht in der Skill-Liste, ist in Deinem Profil aber keine Fähigkeit. Belegt Dein Profil es, gehört es in den Stichpunkt mit dem Beleg.",
    );
    expect(text).not.toContain("belegt es aber nicht");
    expect(text).toContain("Bei Weberit Kunststofftechnik GmbH stehen mehr Stichpunkte als vorgesehen.");
    expect(text).toContain("Eine automatische Prüfung meldet hier noch etwas");
    expect(text).not.toMatch(/round\(s\)|cv\.|cv-|[0-9a-f]{8}-[0-9a-f]{4}-|an EN sentence|Over the per-role/);
  });

  it("a finding without a plain note falls back to the reviewer's words (never empty)", () => {
    renderDetail(MARCUS);
    const items = screen.getByTestId("terminal-review-findings").querySelectorAll("li");
    expect(items[1].textContent).toBe("The summary repeats the 38 staff figure.");
  });

  it("EN, a letter: the letter heading and the pipeline notes with the removed sentence", () => {
    renderDetail(
      {
        id: "terminal-review",
        status: "pass",
        details: "x",
        details_key: "terminal-review-clean-code-open",
        details_params: { count: 1 },
        notes: [
          { kind: "code", key: "letter.page_length", subjects: [], text: "The letter renders to 2 pages" },
          { kind: "note", key: "letter.limit_cut", subjects: ["I have no SAP experience."], text: "One sentence was REMOVED" },
        ],
      },
      "en",
      "cover-letter",
    );
    const text = screen.getByTestId("terminal-review-detail").textContent ?? "";
    expect(text).toContain("Our final check found nothing. An automatic check still flags something, though:");
    expect(text).toContain("Also open in the finished letter:");
    expect(text).toContain("The letter is longer than one page.");
    expect(text).toContain("We removed a sentence from the letter: it said you lack something you never told us. Removed: „I have no SAP experience.“");
  });

  it("a long list ends in its rest count", () => {
    renderDetail({
      ...MARCUS,
      notes: [{ kind: "finding", text: "a", plain: "Eins." }, { kind: "finding", key: "more", count: 3 }],
    });
    expect(screen.getByTestId("terminal-review-findings").textContent).toContain("… und 3 weitere.");
  });

  it("a legacy report (no key, no notes) has no card — the EN details stays", () => {
    expect(hasTerminalReviewCard({ id: "terminal-review", status: "fail", details: EN_DETAILS })).toBe(false);
    expect(hasTerminalReviewCard(MARCUS)).toBe(true);
  });

  it("a clean pass is a passed check, not an advisory row; a legacy pass with details stays advisory", () => {
    expect(
      isSilentPass({ id: "terminal-review", status: "pass", details: "approved", details_key: "terminal-review-clean", details_params: { count: 0 }, notes: [] }),
    ).toBe(true);
    expect(isSilentPass({ id: "terminal-review", status: "pass", details: "approved" })).toBe(false);
    expect(isSilentPass({ id: "x", status: "pass", details: null })).toBe(true);
  });
});
