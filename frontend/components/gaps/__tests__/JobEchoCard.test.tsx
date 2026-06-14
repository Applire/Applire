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
 * JobEchoCard — "what we read from the job ad" echo (US158, FMEA JF-M-4.3/4.4).
 */
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { JobEchoCard } from "../JobEchoCard";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("JobEchoCard", () => {
  it("echoes role, company and the extracted requirements", () => {
    render(
      withIntl(
        <JobEchoCard
          companyName="Südlicht GmbH"
          roleTitle="Creative Director"
          requiredSkills={["Markenführung", "Kampagnenentwicklung"]}
          niceToHaveSkills={["Motion Design"]}
        />
      )
    );
    expect(screen.getByTestId("job-echo-role")).toHaveTextContent("Creative Director");
    expect(screen.getByTestId("job-echo-company")).toHaveTextContent("Südlicht GmbH");
    const reqs = screen.getByTestId("job-echo-requirements");
    expect(reqs).toHaveTextContent("Markenführung");
    expect(reqs).toHaveTextContent("Kampagnenentwicklung");
    expect(reqs).toHaveTextContent("Motion Design");
  });

  it("shows a 'no title detected' hint when the role title is empty (FMEA 4.5)", () => {
    render(
      withIntl(
        <JobEchoCard
          companyName="Acme"
          roleTitle=""
          requiredSkills={["Python"]}
          niceToHaveSkills={[]}
        />
      )
    );
    // Falls back to the no-title hint rather than rendering an empty role.
    expect(screen.getByTestId("job-echo-role")).not.toHaveTextContent("Acme");
    expect(screen.getByTestId("job-echo-role").textContent?.trim().length).toBeGreaterThan(0);
  });
});
