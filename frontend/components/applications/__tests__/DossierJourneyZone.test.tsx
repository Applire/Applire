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

describe("DossierJourneyZone (US233; #789 — one re-entry action, a truthful spinner)", () => {
  it("renders the zone root testid", () => {
    render(withIntl(<DossierJourneyZone flowSessionId="flow-1" currentStep="gap_analysis" running={false} />));
    expect(screen.getByTestId("dossier-journey-zone")).toBeInTheDocument();
  });

  describe("completed", () => {
    it("offers only the letter-draft link — 'Open CV' lives once, in the dossier header", () => {
      render(withIntl(<DossierJourneyZone flowSessionId="flow-1" currentStep="complete" running={false} />));
      const links = screen.getAllByRole("link");
      expect(links).toHaveLength(1);
      expect(screen.queryByTestId("dossier-journey-cv-link")).toBeNull();
      expect(screen.getByTestId("dossier-journey-cl-link")).toHaveAttribute("href", "/flow/flow-1/cover-letter");
    });

    it("renders all step milestones as done, none of them links", () => {
      render(withIntl(<DossierJourneyZone flowSessionId="flow-1" currentStep="complete" running={false} />));
      const milestones = screen.getAllByTestId("dossier-journey-milestone");
      expect(milestones).toHaveLength(4);
      milestones.forEach((m) => {
        expect(m.dataset.state).toBe("done");
        expect(within(m).queryByRole("link")).not.toBeInTheDocument();
      });
    });
  });

  describe("mid-flow", () => {
    it("highlights the current step and carries no second Resume (the header has the one)", () => {
      render(withIntl(<DossierJourneyZone flowSessionId="flow-1" currentStep="gap_analysis" running={false} />));
      const milestones = screen.getAllByTestId("dossier-journey-milestone");
      expect(milestones).toHaveLength(4);
      expect(milestones[0].dataset.state).toBe("done");
      expect(milestones[1].dataset.state).toBe("active");
      expect(milestones[2].dataset.state).toBe("pending");
      expect(milestones[3].dataset.state).toBe("pending");
      expect(screen.queryAllByRole("link")).toHaveLength(0);
      expect(screen.queryByTestId("dossier-journey-resume-link")).toBeNull();
    });

    it("a finished step waiting for the user shows no spinner; a running one does (felix-020)", () => {
      const { container, unmount } = render(
        withIntl(<DossierJourneyZone flowSessionId="flow-1" currentStep="gap_analysis" running={false} />),
      );
      expect(container.querySelector(".animate-spin")).toBeNull();
      unmount();
      const running = render(
        withIntl(<DossierJourneyZone flowSessionId="flow-1" currentStep="cv_generation" running />),
      );
      expect(running.container.querySelector(".animate-spin")).not.toBeNull();
    });
  });

  it("translates step labels — never renders a raw enum like cv_generation", () => {
    render(withIntl(<DossierJourneyZone flowSessionId="flow-1" currentStep="cv_generation" running />));
    expect(screen.queryByText(/cv_generation/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/gap_analysis/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/jd_analysis/i)).not.toBeInTheDocument();
    expect(screen.getByText(/profile/i)).toBeInTheDocument();
    expect(screen.getByText(/gaps/i)).toBeInTheDocument();
    expect(screen.getByText(/interview/i)).toBeInTheDocument();
    expect(screen.getByText(/cv/i)).toBeInTheDocument();
  });
});
