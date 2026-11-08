// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-099 cl. 9 (UAT-3e) amends ADR-074's display-only panel: with a job, the
 * panel carries ONE action, "Jetzt beantworten", which opens the interview on
 * exactly the unasked requirements (INTERFACE A1: `requirement_concepts`).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { withIntl } from "@/lib/test-utils/with-intl";
import UnaskedRequirementsPanel from "@/components/gaps/UnaskedRequirementsPanel";

const REQS = [{ concept: "Reinraumerfahrung" }, { concept: "GMP-Dokumentation" }];

function mockFetch(onMessage: { complete: boolean; question?: string }) {
  const calls: { url: string; body: unknown }[] = [];
  global.fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null });
    if (String(url).endsWith("/api/session")) {
      return { ok: true, status: 200, json: async () => ({ session_id: "s-1", first_question: "Wo hast Du im Reinraum gearbeitet?" }) } as Response;
    }
    return { ok: true, status: 200, json: async () => onMessage } as Response;
  }) as typeof fetch;
  return calls;
}

describe("UnaskedRequirementsPanel — Jetzt beantworten (ADR-099 cl. 9)", () => {
  afterEach(() => vi.restoreAllMocks());

  it("without a job it stays display-only with its reassurance", () => {
    render(withIntl(<UnaskedRequirementsPanel requirements={REQS} />, "de"));
    expect(screen.queryByTestId("unasked-requirements-answer")).toBeNull();
    expect(screen.getByTestId("unasked-requirements-reassurance")).toBeTruthy();
  });

  it("with a job: one action that starts the interview on exactly these requirements", async () => {
    const calls = mockFetch({ complete: true });
    const onAnswered = vi.fn();
    render(withIntl(<UnaskedRequirementsPanel requirements={REQS} jobId="job-1" onAnswered={onAnswered} />, "de"));
    expect(screen.queryByTestId("unasked-requirements-reassurance")).toBeNull();
    expect(screen.getByTestId("unasked-requirements-answer").textContent).toBe("Jetzt beantworten");
    fireEvent.click(screen.getByTestId("unasked-requirements-answer"));
    expect((await screen.findByTestId("unasked-requirements-question")).textContent).toBe("Wo hast Du im Reinraum gearbeitet?");
    expect(calls[0].body).toEqual({ job_id: "job-1", requirement_concepts: ["Reinraumerfahrung", "GMP-Dokumentation"] });

    fireEvent.change(screen.getByTestId("unasked-requirements-text"), { target: { value: "Bei Corvane, 2 Jahre ISO 7." } });
    fireEvent.click(screen.getByTestId("unasked-requirements-send"));
    await waitFor(() => expect(onAnswered).toHaveBeenCalledTimes(1));
    expect(calls[1]).toEqual({ url: expect.stringMatching(/\/api\/session\/s-1\/message$/), body: { message: "Bei Corvane, 2 Jahre ISO 7." } });
    expect(screen.queryByTestId("unasked-requirements-turn")).toBeNull();
  });

  it("a session that fails to start says so and keeps the action", async () => {
    global.fetch = vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) }) as Response) as typeof fetch;
    render(withIntl(<UnaskedRequirementsPanel requirements={REQS} jobId="job-1" />, "de"));
    fireEvent.click(screen.getByTestId("unasked-requirements-answer"));
    expect(await screen.findByTestId("unasked-requirements-error")).toBeTruthy();
    expect(screen.getByTestId("unasked-requirements-answer")).toBeTruthy();
  });

  it("UAT-2b/2c: the buttons read the ask, a click inserts the statement (no send), one lead-in above", async () => {
    const calls: { url: string }[] = [];
    global.fetch = vi.fn(async (url: RequestInfo | URL) => {
      calls.push({ url: String(url) });
      return {
        ok: true,
        status: 200,
        json: async () => ({
          session_id: "s-1",
          first_question: "Wo hast Du im Reinraum gearbeitet?",
          choices: ["Als Schichtleiter habe ich auch nach GMP dokumentiert.", "Ich habe die Linie geführt."],
          choice_asks: ["Hast du als Schichtleiter auch nach GMP dokumentiert?", null],
          choice_unmentioned: ["GMP-Dokumentation"],
        }),
      } as Response;
    }) as typeof fetch;
    render(withIntl(<UnaskedRequirementsPanel requirements={REQS} jobId="job-1" />, "de"));
    fireEvent.click(screen.getByTestId("unasked-requirements-answer"));
    await screen.findByTestId("unasked-requirements-question");
    const buttons = screen.getAllByTestId("unasked-requirements-choice");
    expect(buttons.map((b) => b.textContent)).toEqual([
      "Hast du als Schichtleiter auch nach GMP dokumentiert?",
      "Ich habe die Linie geführt.",
    ]);
    expect(screen.getAllByTestId("choice-lead-in")).toHaveLength(1);
    expect(screen.getByTestId("choice-lead-in").textContent).toBe(
      "In deinem Profil habe ich keine direkte Nennung von \u201EGMP-Dokumentation\u201C gefunden. Vielleicht passt eine dieser Fragen \u2013 ein Klick übernimmt den Satz, du kannst ihn anpassen:",
    );
    fireEvent.click(buttons[0]);
    expect(screen.getByTestId("unasked-requirements-text")).toHaveValue("Als Schichtleiter habe ich auch nach GMP dokumentiert.");
    expect(calls).toHaveLength(1); // the click sent nothing
  });
});
