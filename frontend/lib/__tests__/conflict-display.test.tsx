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
import { useTranslations } from "next-intl";
import { describeConflict, type ConflictFacts } from "@/lib/conflict-display";
import { withIntl } from "@/lib/test-utils/with-intl";

/**
 * #604 — the composition both conflict surfaces render through.
 *
 * Driven through the REAL next-intl translator and the REAL message catalogs,
 * not a hand-rolled fake: the point of the fix is that the Health hub and the
 * live interview say the same words, so a test that mocks the words would
 * assert nothing about it.
 */
function Probe({ facts }: { facts: ConflictFacts }) {
  const t = useTranslations("health");
  const tProfile = useTranslations("profile");
  const d = describeConflict(facts, t, tProfile);
  return (
    <div>
      <p data-testid="heading">{d.heading}</p>
      <p data-testid="existing">{d.existingRow}</p>
      <p data-testid="incoming">{d.incomingRow}</p>
    </div>
  );
}

const WORK_CONFLICT: ConflictFacts = {
  entity_label: "Senior Software Engineer @ Acme GmbH",
  section: "work_experience",
  field: "end_date",
  existing_value_display: "2023-12",
  incoming_value_display: "2024-03",
  incoming_source: "cv_upload",
};

describe("describeConflict (#604)", () => {
  it("names the entry AND the field — the shape #626 introduced", () => {
    render(withIntl(<Probe facts={WORK_CONFLICT} />));
    const heading = screen.getByTestId("heading").textContent ?? "";
    expect(heading).toContain("Senior Software Engineer @ Acme GmbH");
    expect(heading).toContain("End date");
    // The defect this replaces: a raw snake_case field and no entry at all.
    expect(heading).not.toContain("end_date");
  });

  it("shows both values with their provenance", () => {
    render(withIntl(<Probe facts={WORK_CONFLICT} />));
    expect(screen.getByTestId("existing").textContent).toContain("2023-12");
    expect(screen.getByTestId("incoming").textContent).toContain("2024-03");
    // `cv_upload` resolves through the SAME dictionary the enrichment trail
    // uses — never the raw key.
    expect(screen.getByTestId("incoming").textContent).not.toContain("cv_upload");
  });

  it("falls back to a field-only heading when no entity resolves", () => {
    render(
      withIntl(<Probe facts={{ ...WORK_CONFLICT, entity_label: null }} />)
    );
    const heading = screen.getByTestId("heading").textContent ?? "";
    expect(heading).toContain("End date");
    expect(heading).not.toMatch(/null|undefined/);
  });

  it("reads professional_summary's language slot as a summary label", () => {
    render(
      withIntl(
        <Probe
          facts={{
            entity_label: null,
            section: "professional_summary",
            field: "de",
            existing_value_display: "Alt",
            incoming_value_display: "Neu",
            incoming_source: "cv_upload",
          }}
        />
      )
    );
    expect(screen.getByTestId("heading").textContent).toContain("Summary (German)");
  });

  it("humanises an unknown field instead of inventing a translation", () => {
    render(
      withIntl(<Probe facts={{ ...WORK_CONFLICT, field: "some_new_field" }} />)
    );
    const heading = screen.getByTestId("heading").textContent ?? "";
    expect(heading).toContain("some new field");
    expect(heading).not.toContain("some_new_field");
  });

  it("renders German from the German catalog, not English fallbacks", () => {
    render(withIntl(<Probe facts={WORK_CONFLICT} />, "de"));
    expect(screen.getByTestId("heading").textContent).toContain("Enddatum");
    expect(screen.getByTestId("existing").textContent).toContain("Aktueller Wert");
  });

  it("survives a conflict carrying none of the structured facts", () => {
    // A response produced before the backend carried them (the PQ mocks, an
    // older `:latest` backend behind a newer frontend).
    render(withIntl(<Probe facts={{}} />));
    expect(screen.getByTestId("heading").textContent).not.toMatch(/null|undefined/);
    expect(screen.getByTestId("existing").textContent).not.toMatch(/null|undefined/);
  });
});
