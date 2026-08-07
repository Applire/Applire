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
import { ScoreCircle } from "../score-circle";

describe("ScoreCircle band label", () => {
  it("renders the strong band in German when the locale is de", () => {
    render(withIntl(<ScoreCircle score={82} />, "de"));
    expect(screen.getByText("Starke Übereinstimmung")).toBeTruthy();
  });

  it("renders the moderate band in German when the locale is de", () => {
    render(withIntl(<ScoreCircle score={61} />, "de"));
    expect(screen.getByText("Mittlere Übereinstimmung")).toBeTruthy();
  });

  it("renders the weak band in German when the locale is de", () => {
    render(withIntl(<ScoreCircle score={22} />, "de"));
    expect(screen.getByText("Geringe Übereinstimmung")).toBeTruthy();
  });

  it("renders the moderate band in English when the locale is en", () => {
    render(withIntl(<ScoreCircle score={61} />, "en"));
    expect(screen.getByText("Moderate Fit")).toBeTruthy();
  });

  it("still honours an explicit label prop", () => {
    render(withIntl(<ScoreCircle score={61} label="Eigenes Label" />, "de"));
    expect(screen.getByText("Eigenes Label")).toBeTruthy();
  });
});
