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

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QuickTailorWidget } from "../QuickTailorWidget";

// next-intl mock
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

describe("QuickTailorWidget", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
  });

  it("renders URL tab by default", () => {
    render(<QuickTailorWidget />);
    expect(screen.getByPlaceholderText("urlPlaceholder")).toBeInTheDocument();
  });

  it("switches to text textarea when Paste Text tab is clicked", () => {
    render(<QuickTailorWidget />);
    fireEvent.click(screen.getByText("tabText"));
    expect(screen.getByPlaceholderText("textPlaceholder")).toBeInTheDocument();
  });

  it("Analyse button is disabled when input is empty", () => {
    render(<QuickTailorWidget />);
    expect(screen.getByText("analyseButton")).toBeDisabled();
  });

  it("Analyse button enables when URL is typed", () => {
    render(<QuickTailorWidget />);
    fireEvent.change(screen.getByPlaceholderText("urlPlaceholder"), {
      target: { value: "https://example.de/job/123" },
    });
    expect(screen.getByText("analyseButton")).not.toBeDisabled();
  });

  it("shows error message on API failure", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      json: async () => ({ detail: "Bad URL" }),
    });
    render(<QuickTailorWidget />);
    fireEvent.change(screen.getByPlaceholderText("urlPlaceholder"), {
      target: { value: "https://example.de/job" },
    });
    fireEvent.click(screen.getByText("analyseButton"));
    await waitFor(() => expect(screen.getByText("Bad URL")).toBeInTheDocument());
  });

  // E039/US216 — application dossier: optional source link on the text tab
  it("shows an optional source-link field on the text tab only", () => {
    render(<QuickTailorWidget />);
    expect(screen.queryByPlaceholderText("sourcePlaceholder")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("tabText"));
    expect(screen.getByPlaceholderText("sourcePlaceholder")).toBeInTheDocument();
  });

  it("passes the manual source link as source_url when creating from pasted text", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "job-1" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: "app-1", flow_session_id: "flow-1" }),
      });
    render(<QuickTailorWidget />);
    fireEvent.click(screen.getByText("tabText"));
    fireEvent.change(screen.getByPlaceholderText("textPlaceholder"), {
      target: { value: "Head of Department at Example AG…" },
    });
    fireEvent.change(screen.getByPlaceholderText("sourcePlaceholder"), {
      target: { value: "https://www.linkedin.com/jobs/view/456" },
    });
    fireEvent.click(screen.getByText("analyseButton"));
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/flow/flow-1"));
    const createCall = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(JSON.parse(createCall[1].body)).toMatchObject({
      job_analysis_id: "job-1",
      source_url: "https://www.linkedin.com/jobs/view/456",
    });
  });

  it("omits source_url when the text-tab source field is left empty", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "job-1" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: "app-1", flow_session_id: "flow-1" }),
      });
    render(<QuickTailorWidget />);
    fireEvent.click(screen.getByText("tabText"));
    fireEvent.change(screen.getByPlaceholderText("textPlaceholder"), {
      target: { value: "Head of Department at Example AG…" },
    });
    fireEvent.click(screen.getByText("analyseButton"));
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/flow/flow-1"));
    const createCall = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[1];
    expect(JSON.parse(createCall[1].body)).not.toHaveProperty("source_url");
  });

  it("routes to the flow index (not a hard-coded step) after creating the application", async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: "job-1" }) })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: "app-1", flow_session_id: "flow-1" }),
      });
    render(<QuickTailorWidget />);
    fireEvent.change(screen.getByPlaceholderText("urlPlaceholder"), {
      target: { value: "https://example.de/job" },
    });
    fireEvent.click(screen.getByText("analyseButton"));
    // The flow index page owns step routing; pushing a step directly
    // desyncs the flow state machine for returning users.
    await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/flow/flow-1"));
  });
});
