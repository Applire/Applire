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

import { render, screen, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, afterEach } from "vitest";
import { withIntl } from "@/lib/test-utils/with-intl";
import { CoverLetterActionsTab } from "../CoverLetterActionsTab";

describe("CoverLetterActionsTab", () => {
  afterEach(() => vi.clearAllMocks());

  it("regenerate button invokes onRegenerateCoverLetter", () => {
    const onRegenerateCoverLetter = vi.fn();
    render(
      withIntl(<CoverLetterActionsTab onRegenerateCoverLetter={onRegenerateCoverLetter} />)
    );
    fireEvent.click(screen.getByTestId("cl-regenerate-btn"));
    expect(onRegenerateCoverLetter).toHaveBeenCalledOnce();
  });

  it("does NOT render a redundant PDF download button — the shared top bar owns PDF (E038 parity with CVActionsTab)", () => {
    render(withIntl(<CoverLetterActionsTab onRegenerateCoverLetter={vi.fn()} />));
    expect(screen.queryByTestId("cl-download-pdf-btn")).toBeNull();
  });
});
