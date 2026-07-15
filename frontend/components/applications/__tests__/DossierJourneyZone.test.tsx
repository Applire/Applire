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

import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { DossierJourneyZone } from "../DossierJourneyZone";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("DossierJourneyZone (US233 — back into the flow)", () => {
  it("renders the zone root testid", () => {
    render(
      withIntl(
        <DossierJourneyZone flowSessionId="flow-1" currentStep="gap_analysis" workflowStatus="analyzing" />
      )
    );
    expect(screen.getByTestId("dossier-journey-zone")).toBeInTheDocument();
  });

  describe("completed", () => {
    it("renders exactly two links with the exact result-page hrefs", () => {
      render(
        withIntl(
          <DossierJourneyZone flowSessionId="flow-1" currentStep="complete" workflowStatus="completed" />
        )
      );
      const links = screen.getAllByRole("link");
      expect(links).toHaveLength(2);
      expect(screen.getByTestId("dossier-journey-cv-link")).toHaveAttribute("href", "/flow/flow-1/cv");
      expect(screen.getByTestId("dossier-journey-cl-link")).toHaveAttribute(
        "href",
        "/flow/flow-1/cover-letter"
      );
    });

    it("renders all step milestones as done, none of them links", () => {
      render(
        withIntl(
          <DossierJourneyZone flowSessionId="flow-1" currentStep="complete" workflowStatus="completed" />
        )
      );
      const milestones = screen.getAllByTestId("dossier-journey-milestone");
      expect(milestones).toHaveLength(4);
      milestones.forEach((m) => {
        expect(m.dataset.state).toBe("done");
        expect(within(m).queryByRole("link")).not.toBeInTheDocument();
      });
    });
  });

  describe("mid-flow", () => {
    it("highlights the current step and offers a single index-only navigation action", () => {
      render(
        withIntl(
          <DossierJourneyZone flowSessionId="flow-1" currentStep="gap_analysis" workflowStatus="analyzing" />
        )
      );

      const milestones = screen.getAllByTestId("dossier-journey-milestone");
      expect(milestones).toHaveLength(4);
      // Profile (cv_import) is earlier → done, not a link.
      expect(milestones[0].dataset.state).toBe("done");
      // Gaps (gap_analysis) is the current step → active.
      expect(milestones[1].dataset.state).toBe("active");
      // Interview / CV are later → pending.
      expect(milestones[2].dataset.state).toBe("pending");
      expect(milestones[3].dataset.state).toBe("pending");
      milestones.forEach((m) => expect(within(m).queryByRole("link")).not.toBeInTheDocument());

      // Exactly one action, and it must navigate to the flow INDEX — never a
      // hard-coded step route (the flow-routing boundary, US233).
      const links = screen.getAllByRole("link");
      expect(links).toHaveLength(1);
      expect(links[0]).toHaveAttribute("href", "/flow/flow-1");
    });

    it("navigates to the index for every mid-flow step, never a step sub-route", () => {
      for (const step of ["jd_analysis", "cv_import", "gap_analysis", "interview", "cv_generation"]) {
        const { unmount } = render(
          withIntl(
            <DossierJourneyZone flowSessionId="flow-9" currentStep={step} workflowStatus="analyzing" />
          )
        );
        const links = screen.getAllByRole("link");
        expect(links).toHaveLength(1);
        expect(links[0]).toHaveAttribute("href", "/flow/flow-9");
        unmount();
      }
    });
  });

  it("translates step labels — never renders a raw enum like cv_generation", () => {
    render(
      withIntl(
        <DossierJourneyZone flowSessionId="flow-1" currentStep="cv_generation" workflowStatus="cv_generating" />
      )
    );
    expect(screen.queryByText(/cv_generation/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/gap_analysis/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/jd_analysis/i)).not.toBeInTheDocument();
    // English catalog labels (flow.step*) are rendered instead.
    expect(screen.getByText(/profile/i)).toBeInTheDocument();
    expect(screen.getByText(/gaps/i)).toBeInTheDocument();
    expect(screen.getByText(/interview/i)).toBeInTheDocument();
    expect(screen.getByText(/cv/i)).toBeInTheDocument();
  });
});
