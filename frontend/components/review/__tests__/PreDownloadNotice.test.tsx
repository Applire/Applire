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
import { describe, it, expect, vi } from "vitest";

import { withIntl } from "@/lib/test-utils/with-intl";
import { PreDownloadNotice } from "../PreDownloadNotice";

describe("PreDownloadNotice", () => {
  it("always shows the AI-content warning and Download", () => {
    render(withIntl(<PreDownloadNotice canSuppress onConfirm={vi.fn()} onCancel={vi.fn()} />));
    expect(screen.getByTestId("predownload-notice")).toBeTruthy();
    expect(screen.getByTestId("predownload-download")).toBeTruthy();
  });

  it("never renders the retired red-flag diff section", () => {
    render(withIntl(<PreDownloadNotice canSuppress onConfirm={vi.fn()} onCancel={vi.fn()} />));
    expect(screen.queryByTestId("predownload-redflags")).toBeNull();
  });

  it("suppressible: shows the 'don't show again' checkbox", () => {
    render(withIntl(<PreDownloadNotice canSuppress onConfirm={vi.fn()} onCancel={vi.fn()} />));
    expect(screen.getByTestId("predownload-dontshowagain")).toBeTruthy();
  });

  it("confirm reports the checkbox state (true after ticking)", () => {
    const onConfirm = vi.fn();
    render(withIntl(<PreDownloadNotice canSuppress onConfirm={onConfirm} onCancel={vi.fn()} />));
    fireEvent.click(screen.getByTestId("predownload-dontshowagain-input"));
    fireEvent.click(screen.getByTestId("predownload-download"));
    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it("confirm reports false when the checkbox is untouched", () => {
    const onConfirm = vi.fn();
    render(withIntl(<PreDownloadNotice canSuppress onConfirm={onConfirm} onCancel={vi.fn()} />));
    fireEvent.click(screen.getByTestId("predownload-download"));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("Cancel calls onCancel", () => {
    const onCancel = vi.fn();
    render(withIntl(<PreDownloadNotice canSuppress onConfirm={vi.fn()} onCancel={onCancel} />));
    fireEvent.click(screen.getByTestId("predownload-cancel"));
    expect(onCancel).toHaveBeenCalled();
  });

  it("not suppressible: no checkbox, confirm reports false", () => {
    const onConfirm = vi.fn();
    render(withIntl(<PreDownloadNotice canSuppress={false} onConfirm={onConfirm} onCancel={vi.fn()} />));
    expect(screen.queryByTestId("predownload-dontshowagain")).toBeNull();
    fireEvent.click(screen.getByTestId("predownload-download"));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });
});
