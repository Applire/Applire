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
import { vi, describe, it, expect, afterEach } from "vitest";
import { CoverLetterDocument } from "../CoverLetterDocument";

// ---------------------------------------------------------------------------
// Mock next-intl — CoverLetterDocument uses useTranslations()
// ---------------------------------------------------------------------------
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

const CL_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb";
const TEST_HTML = "<html><body><p>Sehr geehrte Damen</p></body></html>";

describe("CoverLetterDocument", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("shows loading state while fetch is in flight", () => {
    vi.spyOn(globalThis, "fetch").mockReturnValue(new Promise(() => {}));
    render(<CoverLetterDocument coverLetterId={CL_ID} />);
    expect(screen.getByText("previewLoading")).toBeTruthy();
    expect(screen.queryByTestId("cover-letter-iframe")).toBeNull();
  });

  it("renders iframe with srcDoc after successful fetch", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      text: async () => TEST_HTML,
    } as Response);
    render(<CoverLetterDocument coverLetterId={CL_ID} />);
    const iframe = await screen.findByTestId("cover-letter-iframe") as HTMLIFrameElement;
    expect(iframe.getAttribute("srcdoc")).toBe(TEST_HTML);
  });

  it("shows error message when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("Network error"));
    render(<CoverLetterDocument coverLetterId={CL_ID} />);
    await screen.findByText("Network error");
  });

  it("fetches from correct API URL", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      text: async () => TEST_HTML,
    } as Response);
    render(<CoverLetterDocument coverLetterId={CL_ID} />);
    await screen.findByTestId("cover-letter-iframe");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/api/cover-letter/${CL_ID}/html`)
    );
  });

  it("applies CSS scale transform proportional to container width", async () => {
    let roCallback: ResizeObserverCallback | undefined;
    vi.stubGlobal(
      "ResizeObserver",
      vi.fn((cb: ResizeObserverCallback) => {
        roCallback = cb;
        return { observe: vi.fn(), unobserve: vi.fn(), disconnect: vi.fn() };
      })
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      text: async () => TEST_HTML,
    } as Response);

    render(<CoverLetterDocument coverLetterId={CL_ID} />);
    const iframe = await screen.findByTestId("cover-letter-iframe") as HTMLIFrameElement;

    // 600px container → scale = Math.min(1, 600/794) ≈ 0.755...
    act(() => {
      roCallback?.(
        [{ contentRect: { width: 600 } } as ResizeObserverEntry],
        {} as ResizeObserver
      );
    });

    await waitFor(() => {
      expect(iframe.style.transform).toMatch(/scale\(0\.7/);
    });
  });

  it("caps scale at 1 for containers wider than 794px", async () => {
    let roCallback: ResizeObserverCallback | undefined;
    vi.stubGlobal(
      "ResizeObserver",
      vi.fn((cb: ResizeObserverCallback) => {
        roCallback = cb;
        return { observe: vi.fn(), unobserve: vi.fn(), disconnect: vi.fn() };
      })
    );
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      text: async () => TEST_HTML,
    } as Response);

    render(<CoverLetterDocument coverLetterId={CL_ID} />);
    const iframe = await screen.findByTestId("cover-letter-iframe") as HTMLIFrameElement;

    act(() => {
      roCallback?.(
        [{ contentRect: { width: 1000 } } as ResizeObserverEntry],
        {} as ResizeObserver
      );
    });

    await waitFor(() => {
      expect(iframe.style.transform).toBe("scale(1)");
    });
  });
});
