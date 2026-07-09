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

// E039/US221 — stale-CV helpers: the explained delta ("your profile gained X")
// formatted for humans, plus the compact query-param encoding that carries the
// delta from the re-tailor click to the flow CV page where the new version lands.

import { describe, it, expect } from "vitest";
import {
  decodeGained,
  encodeGained,
  formatGained,
  SECTION_LABEL_KEYS,
  type StaleCVGained,
} from "../stale-cv";

const GAINED: StaleCVGained[] = [
  { section: "skills", count: 3 },
  { section: "work_experience", count: 1 },
];

// Resolver stub: maps the profile-namespace key to a fake localized label.
const resolve = (key: string) => ({ sectionSkills: "Fähigkeiten", sectionWorkExperience: "Berufserfahrung" }[key] ?? key);

describe("formatGained", () => {
  it("renders localized section labels with +count, comma-joined", () => {
    expect(formatGained(GAINED, resolve)).toBe("Fähigkeiten +3, Berufserfahrung +1");
  });

  it("falls back to a humanized raw section for unknown sections", () => {
    expect(formatGained([{ section: "volunteer_work", count: 2 }], resolve)).toBe(
      "volunteer work +2",
    );
  });

  it("returns empty string for an empty delta", () => {
    expect(formatGained([], resolve)).toBe("");
  });
});

describe("encodeGained / decodeGained", () => {
  it("round-trips the delta through the query param", () => {
    expect(decodeGained(encodeGained(GAINED))).toEqual(GAINED);
  });

  it("encodes to a compact URL-safe string", () => {
    expect(encodeGained(GAINED)).toBe("skills:3,work_experience:1");
  });

  it("rejects garbage instead of rendering it", () => {
    expect(decodeGained(null)).toEqual([]);
    expect(decodeGained("")).toEqual([]);
    expect(decodeGained("<script>:1")).toEqual([]);
    expect(decodeGained("skills:notanumber")).toEqual([]);
  });
});

describe("SECTION_LABEL_KEYS", () => {
  it("maps the enrichment-trail sections onto profile-namespace keys", () => {
    expect(SECTION_LABEL_KEYS.skills).toBe("sectionSkills");
    expect(SECTION_LABEL_KEYS.work_experience).toBe("sectionWorkExperience");
  });
});
