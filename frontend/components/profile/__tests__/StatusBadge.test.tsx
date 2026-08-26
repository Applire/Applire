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

// US291 (H2.1) — StatusBadge is a read-only display, never a form control.
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StatusBadge } from "../StatusBadge";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("StatusBadge", () => {
  it.each([
    ["confirmed", "confirmed"],
    ["unconfirmed", "unconfirmed"],
    ["denied", "denied"],
  ])("renders the %s badge with its translated label", (status, expected) => {
    render(withIntl(<StatusBadge status={status} />));
    expect(screen.getByTestId("status-badge")).toHaveTextContent(expected);
  });

  it("renders nothing for a missing/unknown status", () => {
    const { container: withNull } = render(withIntl(<StatusBadge status={null} />));
    expect(withNull.textContent).toBe("");
    const { container: withUnknown } = render(withIntl(<StatusBadge status="something-else" />));
    expect(withUnknown.textContent).toBe("");
  });

  it("renders the German labels under the de locale", () => {
    render(withIntl(<StatusBadge status="unconfirmed" />, "de"));
    expect(screen.getByTestId("status-badge")).toHaveTextContent("unbestätigt");
  });
});
