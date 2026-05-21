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

import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { RefinementHeader } from "../RefinementHeader";

describe("RefinementHeader", () => {
  it("renders the role title when provided", () => {
    render(withIntl(<RefinementHeader roleTitle="QA Manager — Frankfurt" matchScore={0.82} expiryWarning={null} />));
    expect(screen.getByText("QA Manager — Frankfurt")).toBeTruthy();
  });

  it("omits role title block when null", () => {
    render(withIntl(<RefinementHeader roleTitle={null} matchScore={0.82} expiryWarning={null} />));
    expect(screen.queryByTestId("refinement-header-role")).toBeNull();
  });

  it("renders the match score percentage", () => {
    render(withIntl(<RefinementHeader roleTitle="X" matchScore={0.82} expiryWarning={null} />));
    expect(screen.getByText("82%")).toBeTruthy();
  });

  it("omits the score ring when matchScore is null", () => {
    render(withIntl(<RefinementHeader roleTitle="X" matchScore={null} expiryWarning={null} />));
    expect(screen.queryByTestId("refinement-header-score")).toBeNull();
  });

  it("does not render expiry chip when warning level is none or null", () => {
    render(withIntl(<RefinementHeader roleTitle="X" matchScore={null} expiryWarning={null} />));
    expect(screen.queryByTestId("refinement-header-expiry")).toBeNull();
  });

  it("renders a warning expiry chip", () => {
    render(
      withIntl(
        <RefinementHeader
          roleTitle="X"
          matchScore={null}
          expiryWarning={{ level: "warning", expiresIn: "21.05.2026" }}
        />,
      ),
    );
    const chip = screen.getByTestId("refinement-header-expiry");
    expect(chip).toBeTruthy();
    expect(chip.textContent).toContain("21.05.2026");
  });

  it("renders a critical expiry chip with different style class", () => {
    render(
      withIntl(
        <RefinementHeader
          roleTitle="X"
          matchScore={null}
          expiryWarning={{ level: "critical", expiresIn: "in 3 Stunden" }}
        />,
      ),
    );
    const chip = screen.getByTestId("refinement-header-expiry");
    expect(chip.className).toMatch(/critical|red/);
  });
});
