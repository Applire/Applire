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

import { render, screen, act, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, afterEach, beforeEach } from "vitest";
import { createRef } from "react";
import { CVDocument, type CVDocumentHandle } from "../CVDocument";
import { withIntl } from "@/lib/test-utils/with-intl";

const CV_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa";
const TEST_HTML = "<html><body><p>Max Mustermann</p></body></html>";

describe("CVDocument", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows loading skeleton while fetch is in flight", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(withIntl(<CVDocument cvId={CV_ID} />));
    expect(screen.getByTestId("cv-loading")).toBeTruthy();
    expect(screen.queryByTestId("cv-iframe")).toBeNull();
  });

  it("renders iframe with srcDoc after successful fetch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      text: async () => TEST_HTML,
    } as Response);

    render(withIntl(<CVDocument cvId={CV_ID} />));
    const iframe = await screen.findByTestId("cv-iframe") as HTMLIFrameElement;
    expect(iframe.getAttribute("srcdoc")).toBe(TEST_HTML);
  });

  it("shows error state when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));
    render(withIntl(<CVDocument cvId={CV_ID} />));
    await screen.findByText("Preview could not be loaded.");
  });

  it("retries fetch when error retry button clicked", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockRejectedValueOnce(new Error("fail"))
      .mockResolvedValueOnce({ ok: true, text: async () => TEST_HTML } as Response);

    render(withIntl(<CVDocument cvId={CV_ID} />));
    const retryBtn = await screen.findByText("Try again");
    act(() => retryBtn.click());
    await screen.findByTestId("cv-iframe");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("refresh() via ref triggers a new fetch", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      text: async () => TEST_HTML,
    } as Response);

    const ref = createRef<CVDocumentHandle>();
    render(withIntl(<CVDocument cvId={CV_ID} ref={ref} />));
    await screen.findByTestId("cv-iframe");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    act(() => ref.current?.refresh());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
  });

  it("calls fetch with correct URL", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      text: async () => TEST_HTML,
    } as Response);

    render(withIntl(<CVDocument cvId={CV_ID} />));
    await screen.findByTestId("cv-iframe");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/api/cv/${CV_ID}/html`)
    );
  });
});
