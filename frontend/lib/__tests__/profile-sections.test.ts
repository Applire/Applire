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
import { sectionForFieldRef } from "@/lib/profile-sections";

describe("sectionForFieldRef — Health-hub Resolve routing", () => {
  it("routes an exact section name (accuracy issues) to that section, incl. the US292 ones", () => {
    expect(sectionForFieldRef("projects")).toBe("projects");
    expect(sectionForFieldRef("publications")).toBe("publications");
    expect(sectionForFieldRef("volunteer_activities")).toBe("volunteer_activities");
    expect(sectionForFieldRef("projects, skills")).toBe("projects");
    expect(sectionForFieldRef("certifications")).toBe("certifications");
  });

  it("routes a dotted path by its first segment", () => {
    expect(sectionForFieldRef("work_experience.budget_managed")).toBe("work_experience");
    expect(sectionForFieldRef("publications.published_date")).toBe("publications");
  });

  it("falls back to substring guesses for bare field names", () => {
    expect(sectionForFieldRef("start_date")).toBe("work_experience");
    expect(sectionForFieldRef("degree")).toBe("education");
    expect(sectionForFieldRef("patent_number")).toBe("publications");
    expect(sectionForFieldRef("project_role")).toBe("projects");
    expect(sectionForFieldRef(null)).toBe("skills");
  });
});
