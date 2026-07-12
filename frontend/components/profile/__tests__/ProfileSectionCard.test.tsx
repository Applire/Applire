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
import {
  ProfileSectionBody,
  resolveSummary,
} from "../ProfileSectionCard";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("ProfileSectionCard — structured rendering (F8)", () => {
  it("renders work experience as readable fields, not raw JSON, and hides internal ids", () => {
    const work = [
      {
        id: "e43fc4b2-1111-2222-3333-444455556666",
        role: "Senior Software Engineer",
        company: "Logivia",
        start_date: "2020-03",
        end_date: null,
        responsibilities: ["Owned the platform team's CI"],
        achievements: ["Cut build times by 40%"],
        role_aliases: ["Engineering Lead, Platform"],
        source: "cv_upload",
      },
    ];

    render(
      withIntl(
        <ProfileSectionBody section="work_experience" value={work} uiLanguage="en" />,
        "en",
      ),
    );

    // Human-readable fields surface…
    expect(screen.getByText("Senior Software Engineer")).toBeInTheDocument();
    expect(screen.getByText(/Logivia/)).toBeInTheDocument();
    expect(screen.getByText(/Cut build times by 40%/)).toBeInTheDocument();
    // …and internal plumbing does NOT.
    expect(screen.queryByText(/e43fc4b2/)).not.toBeInTheDocument();
    expect(screen.queryByText(/cv_upload/)).not.toBeInTheDocument();
    expect(screen.queryByText(/role_aliases/)).not.toBeInTheDocument();
    // No raw JSON braces leaking into the DOM text.
    expect(document.body.textContent).not.toMatch(/"source":/);
  });

  // #155 — is_current=true renders the present label; a real end date still wins
  // when the entry is not current; the null-fallback behaviour is unchanged.
  it("renders the present label for a current position (is_current === true)", () => {
    const work = [
      {
        role: "Senior Software Engineer",
        company: "Logivia",
        start_date: "2020-03",
        end_date: null,
        is_current: true,
      },
    ];
    render(
      withIntl(
        <ProfileSectionBody section="work_experience" value={work} uiLanguage="en" />,
        "en",
      ),
    );
    expect(screen.getByText(/2020-03 → present/)).toBeInTheDocument();
  });

  it("renders the end date for an ended position (is_current === false)", () => {
    const work = [
      {
        role: "Software Engineer",
        company: "StartupX",
        start_date: "2018-06",
        end_date: "2021-02",
        is_current: false,
      },
    ];
    render(
      withIntl(
        <ProfileSectionBody section="work_experience" value={work} uiLanguage="en" />,
        "en",
      ),
    );
    expect(screen.getByText(/2018-06 → 2021-02/)).toBeInTheDocument();
  });

  it("keeps the null-fallback: no end date and no marker still shows the present label", () => {
    const work = [
      {
        role: "Engineer",
        company: "Acme",
        start_date: "2022-01",
        end_date: null,
      },
    ];
    render(
      withIntl(
        <ProfileSectionBody section="work_experience" value={work} uiLanguage="en" />,
        "en",
      ),
    );
    expect(screen.getByText(/2022-01 → present/)).toBeInTheDocument();
  });

  it("renders skills as named chips with a proficiency label, hiding source/refs", () => {
    const skills = [
      { name: "Kubernetes", category: "technical", proficiency: "advanced", source: "work:Logivia" },
      { name: "Python", category: "technical", proficiency: "expert", experience_refs: ["x"] },
    ];

    render(
      withIntl(<ProfileSectionBody section="skills" value={skills} uiLanguage="en" />, "en"),
    );

    expect(screen.getByText("Kubernetes")).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.queryByText(/work:Logivia/)).not.toBeInTheDocument();
    expect(screen.queryByText(/experience_refs/)).not.toBeInTheDocument();
  });

  it("renders the professional summary in the UI language and never as a {de,en} object", () => {
    const summary = { de: null, en: "Backend/platform engineer, 11 years." };

    render(
      withIntl(
        <ProfileSectionBody
          section="professional_summary"
          value={summary}
          uiLanguage="de"
        />,
        "de",
      ),
    );

    // Falls back to the available English summary rather than rendering "null".
    expect(screen.getByText("Backend/platform engineer, 11 years.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\{.*"de".*\}/);
    expect(document.body.textContent).not.toMatch(/null/);
  });

  it("renders an empty-state, not raw JSON, when a section has no data", () => {
    render(
      withIntl(<ProfileSectionBody section="education" value={[]} uiLanguage="en" />, "en"),
    );
    expect(screen.getByText("Not provided")).toBeInTheDocument();
  });
});

describe("resolveSummary — language-aware summary check (F9.2)", () => {
  it("prefers the UI language when present", () => {
    const r = resolveSummary({ de: "Deutsch", en: "English" }, "de");
    expect(r.text).toBe("Deutsch");
    expect(r.missing).toBe(false);
  });

  it("falls back to the other language and flags which language is missing", () => {
    const r = resolveSummary({ de: null, en: "English only" }, "de");
    expect(r.text).toBe("English only");
    expect(r.missing).toBe(false); // a summary EXISTS — not 'incomplete'
    expect(r.missingLanguage).toBe("de");
  });

  it("reports truly missing only when no language has a summary", () => {
    const r = resolveSummary({ de: null, en: null }, "en");
    expect(r.text).toBeNull();
    expect(r.missing).toBe(true);
  });

  it("accepts a plain string summary (legacy shape)", () => {
    const r = resolveSummary("Just a string", "en");
    expect(r.text).toBe("Just a string");
    expect(r.missing).toBe(false);
  });
});
