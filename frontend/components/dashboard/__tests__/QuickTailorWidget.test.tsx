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

// next-intl mock (useLocale: the embedded DuplicateJdDialog formats dates)
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
  useLocale: () => "de",
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

  // US224 — mobile Quick Tailor capture: the input+button row must stack
  // (not squash) below sm so both tabs stay usable at 390px.
  describe("mobile capture row (US224)", () => {
    it("stacks the URL input and button into a column below sm, row at sm+", () => {
      render(<QuickTailorWidget />);
      const input = screen.getByPlaceholderText("urlPlaceholder");
      const row = input.parentElement as HTMLElement;
      expect(row.className).toContain("flex-col");
      expect(row.className).toContain("sm:flex-row");
      expect(input.className).toContain("w-full");
    });

    it("makes the Analyse button full-width below sm on both tabs", () => {
      render(<QuickTailorWidget />);
      const urlButton = screen.getByText("analyseButton");
      expect(urlButton.className).toContain("w-full");
      expect(urlButton.className).toContain("sm:w-auto");

      fireEvent.click(screen.getByText("tabText"));
      const textButton = screen.getByText("analyseButton");
      expect(textButton.className).toContain("w-full");
      expect(textButton.className).toContain("sm:w-auto");
    });

    it("keeps the text-tab textarea full-width in the stacked row", () => {
      render(<QuickTailorWidget />);
      fireEvent.click(screen.getByText("tabText"));
      const textarea = screen.getByPlaceholderText("textPlaceholder");
      const wrapper = textarea.parentElement as HTMLElement;
      expect(wrapper.className).toContain("w-full");
    });
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

  // E039/US220 — journey Branch F: duplicate-JD recognition
  describe("duplicate-JD hint", () => {
    const DUP_HINT = {
      application_id: "app-9",
      job_analysis_id: "job-1",
      company_name: "DataCraft GmbH",
      role_title: "Senior Data Analyst",
      analyzed_at: "2026-07-05T10:00:00Z",
      matched_on: "job",
    };

    function analyzeWithHint() {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ id: "job-1", duplicate_of: DUP_HINT }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ id: "app-new", flow_session_id: "flow-2" }),
        });
      render(<QuickTailorWidget />);
      fireEvent.change(screen.getByPlaceholderText("urlPlaceholder"), {
        target: { value: "https://example.de/job/123" },
      });
      fireEvent.click(screen.getByText("analyseButton"));
    }

    it("shows the Branch F dialog instead of silently creating the application", async () => {
      analyzeWithHint();
      await waitFor(() =>
        expect(screen.getByTestId("duplicate-jd-dialog")).toBeInTheDocument(),
      );
      // no application was created, no navigation happened
      expect(global.fetch).toHaveBeenCalledTimes(1);
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("'open existing' navigates to the existing application", async () => {
      analyzeWithHint();
      await waitFor(() =>
        expect(screen.getByTestId("duplicate-jd-dialog")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByTestId("duplicate-jd-open-existing"));
      expect(mockPush).toHaveBeenCalledWith("/applications/app-9");
      expect(global.fetch).toHaveBeenCalledTimes(1); // still no create call
    });

    it("'continue as new' proceeds with the normal create + flow routing", async () => {
      analyzeWithHint();
      await waitFor(() =>
        expect(screen.getByTestId("duplicate-jd-dialog")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByTestId("duplicate-jd-continue-new"));
      await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/flow/flow-2"));
      const createCall = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[1];
      expect(JSON.parse(createCall[1].body)).toMatchObject({
        job_analysis_id: "job-1",
        start_workflow: true,
      });
    });

    it("dismissing the dialog leaves the dashboard untouched (never blocks)", async () => {
      analyzeWithHint();
      await waitFor(() =>
        expect(screen.getByTestId("duplicate-jd-dialog")).toBeInTheDocument(),
      );
      fireEvent.click(screen.getByTestId("duplicate-jd-dismiss"));
      expect(screen.queryByTestId("duplicate-jd-dialog")).not.toBeInTheDocument();
      expect(global.fetch).toHaveBeenCalledTimes(1);
      expect(mockPush).not.toHaveBeenCalled();
    });

    it("no dialog when the analysis carries no hint", async () => {
      global.fetch = vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ id: "job-1", duplicate_of: null }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ id: "app-1", flow_session_id: "flow-1" }),
        });
      render(<QuickTailorWidget />);
      fireEvent.change(screen.getByPlaceholderText("urlPlaceholder"), {
        target: { value: "https://example.de/job/123" },
      });
      fireEvent.click(screen.getByText("analyseButton"));
      await waitFor(() => expect(mockPush).toHaveBeenCalledWith("/flow/flow-1"));
      expect(screen.queryByTestId("duplicate-jd-dialog")).not.toBeInTheDocument();
    });
  });
});

// US229 (E040, ADR-050 amendment 2026-09-05 clause 4c) — the share-target /
// deep-link prefill. Emma Journey-FMEA JF-E-Q.6's control is the NEGATIVE half
// of these tests: a prefill that also submitted would satisfy every positive
// assertion, so each case asserts that nothing was fetched.
describe("QuickTailorWidget — ?jd_url= / ?jd_text= prefill (US229)", () => {
  function withSearch(search: string) {
    window.history.replaceState({}, "", `/dashboard${search}`);
  }

  beforeEach(() => {
    vi.restoreAllMocks();
    mockPush.mockReset();
    global.fetch = vi.fn();
    withSearch("");
  });

  it("prefills the URL tab from ?jd_url=", () => {
    withSearch("?jd_url=https%3A%2F%2Fjobs.example.com%2F42");
    render(<QuickTailorWidget />);
    expect(screen.getByPlaceholderText("urlPlaceholder")).toHaveValue(
      "https://jobs.example.com/42",
    );
    expect(screen.getByText("analyseButton")).not.toBeDisabled();
  });

  it("prefills the TEXT tab from ?jd_text= and switches to it", () => {
    withSearch("?jd_text=Senior%20Engineer%20(m%2Fw%2Fd)");
    render(<QuickTailorWidget />);
    expect(screen.getByPlaceholderText("textPlaceholder")).toHaveValue("Senior Engineer (m/w/d)");
    expect(screen.queryByPlaceholderText("urlPlaceholder")).not.toBeInTheDocument();
  });

  it("prefills only — it never starts the analysis on its own", () => {
    withSearch("?jd_url=https%3A%2F%2Fjobs.example.com%2F42");
    render(<QuickTailorWidget />);
    expect(global.fetch).not.toHaveBeenCalled();
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("ignores a non-http jd_url rather than putting it in the URL tab", () => {
    withSearch("?jd_url=javascript%3Aalert(1)");
    render(<QuickTailorWidget />);
    expect(screen.getByPlaceholderText("urlPlaceholder")).toHaveValue("");
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("prefers jd_url when a share somehow carried both", () => {
    withSearch("?jd_url=https%3A%2F%2Fjobs.example.com%2F42&jd_text=some%20prose");
    render(<QuickTailorWidget />);
    expect(screen.getByPlaceholderText("urlPlaceholder")).toHaveValue(
      "https://jobs.example.com/42",
    );
  });

  it("leaves the widget untouched without the params", () => {
    render(<QuickTailorWidget />);
    expect(screen.getByPlaceholderText("urlPlaceholder")).toHaveValue("");
    expect(global.fetch).not.toHaveBeenCalled();
  });
});
