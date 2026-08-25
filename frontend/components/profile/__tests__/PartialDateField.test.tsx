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

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PartialDateField } from "../PartialDateField";
import { withIntl } from "@/lib/test-utils/with-intl";

/** A stateful wrapper so successive picker interactions see each other's
 * committed value, exactly as they do inside the entry-editor dialogs. */
function ControlledField({ onChange, initial = null }: { onChange: (v: string | null) => void; initial?: string | null }) {
  const [value, setValue] = useState<string | null>(initial);
  return (
    <PartialDateField
      id="start"
      label="Start"
      value={value}
      onChange={(v) => {
        setValue(v);
        onChange(v);
      }}
    />
  );
}

describe("PartialDateField", () => {
  // H1.2 — Monat + Jahr emit the canonical YYYY-MM shape.
  it("emits YYYY-MM once both month and year are set", () => {
    const onChange = vi.fn();
    render(withIntl(<ControlledField onChange={onChange} />));

    fireEvent.change(screen.getByTestId("start-year"), { target: { value: "2021" } });
    fireEvent.change(screen.getByTestId("start-month"), { target: { value: "07" } });

    expect(onChange).toHaveBeenLastCalledWith("2021-07");
  });

  // H1.2 — a month chosen BEFORE the year must survive until the year
  // arrives (real-browser finding 2026-08-25: it used to vanish, leaving
  // "2018" where the user meant "2018-06").
  it("keeps a month chosen before the year and emits YYYY-MM once the year arrives", () => {
    const onChange = vi.fn();
    render(withIntl(<ControlledField onChange={onChange} />));

    fireEvent.change(screen.getByTestId("start-month"), { target: { value: "06" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect((screen.getByTestId("start-month") as HTMLSelectElement).value).toBe("06");

    fireEvent.change(screen.getByTestId("start-year"), { target: { value: "2018" } });

    expect(onChange).toHaveBeenLastCalledWith("2018-06");
  });

  // A partially typed year must not be wiped by the null emission.
  it("keeps a partially typed year visible while it is not yet four digits", () => {
    const onChange = vi.fn();
    render(withIntl(<ControlledField onChange={onChange} />));

    fireEvent.change(screen.getByTestId("start-year"), { target: { value: "201" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
    expect((screen.getByTestId("start-year") as HTMLInputElement).value).toBe("201");

    fireEvent.change(screen.getByTestId("start-year"), { target: { value: "2019" } });
    expect(onChange).toHaveBeenLastCalledWith("2019");
  });

  // H1.2 — year-only (month left at "—") emits YYYY.
  it("emits YYYY when only the year is set", () => {
    const onChange = vi.fn();
    render(withIntl(<PartialDateField id="start" label="Start" value={null} onChange={onChange} />));

    fireEvent.change(screen.getByTestId("start-year"), { target: { value: "2019" } });

    expect(onChange).toHaveBeenLastCalledWith("2019");
  });

  // H1.9 — clearing the year goes back to null, never "".
  it("emits null (never an empty string) when the year is cleared", () => {
    const onChange = vi.fn();
    render(
      withIntl(<PartialDateField id="start" label="Start" value="2019-05" onChange={onChange} />),
    );

    fireEvent.change(screen.getByTestId("start-year"), { target: { value: "" } });

    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("never calls onChange while untouched (no default value)", () => {
    const onChange = vi.fn();
    render(withIntl(<PartialDateField id="start" label="Start" value={null} onChange={onChange} />));
    expect(onChange).not.toHaveBeenCalled();
  });

  // H1.12 — a legacy value is shown verbatim and left alone until acted on.
  it("shows an unparseable legacy value verbatim and does not touch it", () => {
    const onChange = vi.fn();
    render(
      withIntl(<PartialDateField id="start" label="Start" value="Q3 2019" onChange={onChange} />),
    );

    expect(screen.getByTestId("start-legacy-original").textContent).toContain("Q3 2019");
    expect((screen.getByTestId("start-year") as HTMLInputElement).value).toBe("");
    expect((screen.getByTestId("start-month") as HTMLSelectElement).value).toBe("");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("replaces a legacy value once the user actually picks a new one", () => {
    const onChange = vi.fn();
    render(
      withIntl(<PartialDateField id="start" label="Start" value="Q3 2019" onChange={onChange} />),
    );

    fireEvent.change(screen.getByTestId("start-year"), { target: { value: "2020" } });

    expect(onChange).toHaveBeenLastCalledWith("2020");
  });
});
