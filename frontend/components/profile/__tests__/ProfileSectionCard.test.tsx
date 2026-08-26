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

describe("ProfileSectionCard — signature stories (E046, ADR-055)", () => {
  const stories = [
    {
      id: "story-uuid-1111-2222-3333",
      title: "SAP cutover rescue",
      challenge: "Migration was six weeks from a failed go-live.",
      mechanism: "Rebuilt the interface layer around an event queue.",
      outcome: "Cutover succeeded with 30% less downtime.",
      benchmark: "Prior attempt was rolled back after 14 hours.",
      experience_refs: ["w-1", "w-2"],
      source: "interview",
    },
  ];

  it("renders title, the four labelled fields, and the linked-experience count — internals hidden", () => {
    render(
      withIntl(
        <ProfileSectionBody
          section="signature_stories"
          value={stories}
          uiLanguage="en"
        />,
        "en",
      ),
    );
    expect(screen.getByText("SAP cutover rescue")).toBeInTheDocument();
    expect(screen.getByText("Challenge")).toBeInTheDocument();
    expect(screen.getByText(/failed go-live/)).toBeInTheDocument();
    expect(screen.getByText("Outcome")).toBeInTheDocument();
    expect(screen.getByText(/30% less downtime/)).toBeInTheDocument();
    expect(screen.getByText("Benchmark")).toBeInTheDocument();
    expect(screen.getByText("2 linked experiences")).toBeInTheDocument();
    // F8 rule: internal plumbing never renders.
    expect(screen.queryByText(/story-uuid/)).not.toBeInTheDocument();
    expect(screen.queryByText(/w-1/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/"source":/);
  });

  it("omits the benchmark row when absent and the refs line when empty", () => {
    render(
      withIntl(
        <ProfileSectionBody
          section="signature_stories"
          value={[{ ...stories[0], benchmark: null, experience_refs: [] }]}
          uiLanguage="en"
        />,
        "en",
      ),
    );
    expect(screen.queryByText("Benchmark")).not.toBeInTheDocument();
    expect(screen.queryByText(/linked experience/)).not.toBeInTheDocument();
  });
});

// #113(c) — the vault stores a language as {language, level} (backend
// `Language` in schemas/profile.py, `TailoredLanguage` in schemas/cv.py, and
// `lang.language` in every CV template). The card read `l.name`, which is never
// present, so a profile carrying German (Native) / English (C1) rendered
// "Not provided" — the user's own data silently invisible.
describe("ProfileSectionCard — languages read the vault field (#113c)", () => {
  it("renders languages stored under the vault's `language` field", () => {
    const languages = [
      { language: "German", level: "Native", status: "confirmed" },
      { language: "English", level: "C1", status: "confirmed" },
    ];
    render(
      withIntl(
        <ProfileSectionBody section="languages" value={languages} uiLanguage="en" />,
        "en",
      ),
    );
    expect(screen.getByText("German")).toBeInTheDocument();
    expect(screen.getByText("Native")).toBeInTheDocument();
    expect(screen.getByText("English")).toBeInTheDocument();
    expect(screen.getByText("C1")).toBeInTheDocument();
    expect(screen.queryByText("Not provided")).not.toBeInTheDocument();
  });

  it("still renders the empty state when there genuinely are no languages", () => {
    render(
      withIntl(<ProfileSectionBody section="languages" value={[]} uiLanguage="en" />, "en"),
    );
    expect(screen.getByText("Not provided")).toBeInTheDocument();
  });
});

// #113(d) — an education entry whose start date is unknown rendered
// "— → 2006". The DACH convention already shipped in the CV templates
// (lebenslauf.html.j2: no start ⇒ end date alone, no dash) is the reference.
describe("ProfileSectionCard — open-ended periods (#113d)", () => {
  it("renders an unknown education start as the end date alone, never an em-dash placeholder", () => {
    const education = [
      {
        degree: "Diplom",
        field: "Chemie",
        institution: "TU Musterstadt",
        end_date: "2006",
      },
    ];
    render(
      withIntl(<ProfileSectionBody section="education" value={education} uiLanguage="en" />, "en"),
    );
    expect(screen.getByText("2006")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("—");
    expect(document.body.textContent).not.toContain("→");
  });

  it("renders an ongoing education entry with the present label, not an em-dash", () => {
    const education = [
      { degree: "PhD", institution: "TU Musterstadt", start_date: "2023-09" },
    ];
    render(
      withIntl(<ProfileSectionBody section="education" value={education} uiLanguage="en" />, "en"),
    );
    expect(screen.getByText(/2023-09 → present/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("—");
  });

  it("renders an unknown work start as the end date alone", () => {
    const work = [
      { role: "Laborleiter", company: "Labsynth", end_date: "2011-08", is_current: false },
    ];
    render(
      withIntl(
        <ProfileSectionBody section="work_experience" value={work} uiLanguage="en" />,
        "en",
      ),
    );
    expect(screen.getByText("2011-08")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("—");
    expect(document.body.textContent).not.toContain("→");
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

// US292 — personal_info gained six new read-only rows (address, nationality,
// date_of_birth, linkedin_url, xing_url, website_url); photo_url stays
// hidden regardless (it is owned by PhotoManager, never a text row here).
describe("ProfileSectionCard — personal_info extended contact fields (US292)", () => {
  it("renders the new fields when present, as stored", () => {
    const info = {
      name: "Anna Bauer",
      address: "Musterstraße 1, 10115 Berlin",
      nationality: "Deutsch",
      date_of_birth: "1990-02-01",
      linkedin_url: "https://linkedin.com/in/annabauer",
      xing_url: "https://xing.com/profile/AnnaBauer",
      website_url: "https://annabauer.dev",
    };
    render(withIntl(<ProfileSectionBody section="personal_info" value={info} uiLanguage="en" />, "en"));
    expect(screen.getByText("Address")).toBeInTheDocument();
    expect(screen.getByText("Musterstraße 1, 10115 Berlin")).toBeInTheDocument();
    expect(screen.getByText("Nationality")).toBeInTheDocument();
    expect(screen.getByText("Deutsch")).toBeInTheDocument();
    expect(screen.getByText("Date of birth")).toBeInTheDocument();
    expect(screen.getByText("1990-02-01")).toBeInTheDocument();
    expect(screen.getByText("LinkedIn")).toBeInTheDocument();
    expect(screen.getByText("https://linkedin.com/in/annabauer")).toBeInTheDocument();
    expect(screen.getByText("XING")).toBeInTheDocument();
    expect(screen.getByText("https://xing.com/profile/AnnaBauer")).toBeInTheDocument();
    expect(screen.getByText("Website")).toBeInTheDocument();
    expect(screen.getByText("https://annabauer.dev")).toBeInTheDocument();
  });

  it("never renders photo_url, even when present on the value", () => {
    const info = { name: "Anna Bauer", photo_url: "https://cdn.example.com/photo.jpg" };
    render(withIntl(<ProfileSectionBody section="personal_info" value={info} uiLanguage="en" />, "en"));
    expect(screen.queryByText(/photo\.jpg/)).not.toBeInTheDocument();
  });

  it("omits the new rows entirely when their fields are absent", () => {
    const info = { name: "Anna Bauer" };
    render(withIntl(<ProfileSectionBody section="personal_info" value={info} uiLanguage="en" />, "en"));
    expect(screen.queryByText("Address")).not.toBeInTheDocument();
    expect(screen.queryByText("Nationality")).not.toBeInTheDocument();
    expect(screen.queryByText("Date of birth")).not.toBeInTheDocument();
    expect(screen.queryByText("LinkedIn")).not.toBeInTheDocument();
    expect(screen.queryByText("XING")).not.toBeInTheDocument();
    expect(screen.queryByText("Website")).not.toBeInTheDocument();
  });
});
