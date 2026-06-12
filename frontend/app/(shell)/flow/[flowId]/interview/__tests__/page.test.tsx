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
import { render, screen, waitFor } from "@testing-library/react";
import InterviewPage from "../page";

const mockPush = vi.fn();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));

// React 19 `use()` reads instrumented promises synchronously — avoids
// Suspense plumbing in jsdom.
function fulfilledParams(flowId: string) {
  const p = Promise.resolve({ flowId });
  return Object.assign(p, { status: "fulfilled", value: { flowId } });
}

function mockApi({ currentStep, advanceStatus }: { currentStep: string; advanceStatus: number }) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/flow/f1/state")) {
      return {
        ok: true,
        json: async () => ({
          job_id: "j1",
          current_step: currentStep,
          job_summary: { role_title: "Engineer" },
        }),
      };
    }
    if (url.includes("/api/job/j1/gaps")) {
      return { ok: false, status: 404, json: async () => ({}) };
    }
    if (url.includes("/api/session")) {
      return {
        ok: true,
        json: async () => ({
          session_id: "s1",
          mode: "targeted",
          first_question: "What is your Docker experience?",
          question: "What is your Docker experience?",
          estimated_questions: 5,
          gaps_total: 2,
          gaps_remaining: 2,
          choices: null,
          resumed: false,
        }),
      };
    }
    if (url.includes("/api/flow/f1/advance")) {
      return {
        ok: advanceStatus < 400,
        status: advanceStatus,
        json: async () => ({}),
        text: async () => "conflict",
      };
    }
    throw new Error(`unexpected fetch: ${url} ${init?.method ?? "GET"}`);
  });
}

describe("InterviewPage flow-advance guard", () => {
  beforeEach(() => {
    mockPush.mockReset();
    mockReplace.mockReset();
    // jsdom does not implement scrollIntoView (used by the chat auto-scroll)
    Element.prototype.scrollIntoView = vi.fn();
  });

  it("bounces to the flow index when advance 409s and the flow is not at interview", async () => {
    global.fetch = mockApi({ currentStep: "jd_analysis", advanceStatus: 409 }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/flow/f1"));
  });

  it("treats 409 as benign when the flow is already at interview", async () => {
    global.fetch = mockApi({ currentStep: "interview", advanceStatus: 409 }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "What is your Docker experience?",
      ),
    );
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("renders normally when advance succeeds", async () => {
    global.fetch = mockApi({ currentStep: "gap_analysis", advanceStatus: 200 }) as unknown as typeof fetch;

    render(<InterviewPage params={fulfilledParams("f1")} />);

    await waitFor(() =>
      expect(screen.getByTestId("interview-question")).toHaveTextContent(
        "What is your Docker experience?",
      ),
    );
    expect(mockReplace).not.toHaveBeenCalled();
  });
});
