// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { FileChip } from "../file-chip";
import { withIntl } from "@/lib/test-utils/with-intl";

describe("FileChip file size follows the UI locale (polish f)", () => {
  it("prints a decimal comma in German", () => {
    render(withIntl(<FileChip filename="cv.pdf" size={35 * 1024} onRemove={vi.fn()} />, "de"));
    expect(screen.getByText("35,0 KB")).toBeInTheDocument();
  });

  it("prints a decimal point in English", () => {
    render(withIntl(<FileChip filename="cv.pdf" size={35 * 1024} onRemove={vi.fn()} />, "en"));
    expect(screen.getByText("35.0 KB")).toBeInTheDocument();
  });

  it("formats megabytes with the locale and bytes without decimals", () => {
    const { unmount } = render(
      withIntl(<FileChip filename="a.pdf" size={Math.round(1.5 * 1024 * 1024)} onRemove={vi.fn()} />, "de")
    );
    expect(screen.getByText("1,5 MB")).toBeInTheDocument();
    unmount();
    render(withIntl(<FileChip filename="b.pdf" size={900} onRemove={vi.fn()} />, "de"));
    expect(screen.getByText("900 B")).toBeInTheDocument();
  });
});
