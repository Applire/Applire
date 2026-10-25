// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { withIntl } from "@/lib/test-utils/with-intl";
import DocumentsPage from "../page";

vi.mock("@/components/shell/AppTopbar", () => ({
  AppTopbar: () => <div data-testid="topbar" />,
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

afterEach(() => vi.unstubAllGlobals());

describe("My Documents page — 789:187 \"Total documents\" counts documents, not CV rows", () => {
  it("shows documents_total (a CV with its letter is two), not the row total", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], total: 3, documents_total: 4 }) }),
    );
    render(withIntl(<DocumentsPage />));
    const label = await screen.findByText("Total documents");
    await waitFor(() => expect(label.previousElementSibling?.textContent).toBe("4"));
  });

  it("falls back to the row total from a backend that predates documents_total", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], total: 2 }) }));
    render(withIntl(<DocumentsPage />));
    const label = await screen.findByText("Total documents");
    await waitFor(() => expect(label.previousElementSibling?.textContent).toBe("2"));
  });
});
