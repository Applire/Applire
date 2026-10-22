// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen } from "@testing-library/react";
import { useTranslations } from "next-intl";
import { describe, expect, it } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import de from "@/messages/de.json";
import en from "@/messages/en.json";
import { oracleDetailText, type OracleDetailSource } from "../oracle-detail";

function Probe({ src }: { src: OracleDetailSource }) {
  const t = useTranslations("oracleDetail");
  return <span data-testid="probe">{oracleDetailText(src, t)}</span>;
}

describe("oracleDetailText (ADR-052 amended 2026-10-22)", () => {
  it("says a keyed detail in the UI language", () => {
    render(
      withIntl(
        <Probe
          src={{
            detail: 'Claimed duration "12 Jahre" exceeds … (e.g. "über 9 Jahre").',
            detail_key: "subjectTenureExceeds",
            detail_params: { claimed: "12 Jahre", years: "9.4", whole: "9", skill: "SAP", where: "" },
          }}
        />,
        "en",
      ),
    );
    // The EN surface no longer shows the German example inside English text.
    expect(screen.getByTestId("probe").textContent).toBe(
      "The stated duration “12 Jahre” is longer than the 9.4 years of the positions that mention “SAP”. Write at most “over 9 years”.",
    );
  });

  it("falls back to the backend text for an unknown key or none", () => {
    render(withIntl(<Probe src={{ detail: "Plain.", detail_key: "notACatalogKey" }} />));
    expect(screen.getByTestId("probe").textContent).toBe("Plain.");
  });

  it("de/en carry the same oracleDetail keys", () => {
    expect(Object.keys(de.oracleDetail).sort()).toEqual(Object.keys(en.oracleDetail).sort());
  });
});
