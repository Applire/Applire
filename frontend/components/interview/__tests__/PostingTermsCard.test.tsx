// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Sorbet WP-Q10 (ruling Q7-2; Q10-1…Q10-3) — the posting-terms card, its gate
 * dialog, the receipt, and the profile's confirmation line.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { PostingTermsCard, PostingTermsGate, PostingTermsReceipt } from "../PostingTermsCard";
import { SkillConfirmationLines } from "@/components/profile/SkillConfirmationLines";
import { SkillsEditor } from "@/components/profile/SkillsEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import { confirmationKey, hasOpenTerms, type PostingTermsState } from "@/lib/posting-terms";
import type { Skill } from "@/lib/profile-entries";

const TERMS = [
  { term: "Observability", their_words: ["Prometheus", "Built Prometheus and Grafana dashboards."] },
  { term: "Event-driven architecture", their_words: [] },
];
const STATE: PostingTermsState = {
  terms: TERMS, declined: [], left_open: [], confirmed: [], answered_at: null, language: "en",
};

function json(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

describe("PostingTermsCard", () => {
  it("shows every term with the person's own words and nothing ticked", () => {
    render(withIntl(<PostingTermsCard terms={TERMS} selected={[]} onChange={() => {}} language="en" />, "en"));
    expect(screen.getByText("One more question before your CV is written")).toBeTruthy();
    expect(screen.getAllByTestId("posting-term")).toHaveLength(2);
    const ticks = screen.getAllByTestId("posting-term-tick") as HTMLInputElement[];
    expect(ticks.every((t) => !t.checked)).toBe(true);
    expect(screen.getByText("In your words so far")).toBeTruthy();
    expect(screen.getAllByTestId("posting-term-words").map((n) => n.textContent)).toEqual([
      "“Prometheus”", "“Built Prometheus and Grafana dashboards.”",
    ]);
  });

  it("quotes the person's words German-style on a German card", () => {
    render(withIntl(<PostingTermsCard terms={TERMS} selected={[]} onChange={() => {}} language="de" />, "de"));
    expect(screen.getByText("Noch eine Frage, bevor dein Lebenslauf entsteht")).toBeTruthy();
    expect(screen.getAllByTestId("posting-term-words")[0].textContent).toBe("„Prometheus“");
  });

  it("ticks and unticks a term", () => {
    const onChange = vi.fn();
    const { rerender } = render(withIntl(<PostingTermsCard terms={TERMS} selected={[]} onChange={onChange} />, "en"));
    fireEvent.click(screen.getAllByTestId("posting-term-tick")[0]);
    expect(onChange).toHaveBeenLastCalledWith(["Observability"]);
    rerender(withIntl(<PostingTermsCard terms={TERMS} selected={["Observability"]} onChange={onChange} />, "en"));
    fireEvent.click(screen.getAllByTestId("posting-term-tick")[0]);
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("renders nothing for an empty list", () => {
    const { container } = render(withIntl(<PostingTermsCard terms={[]} selected={[]} onChange={() => {}} />, "en"));
    expect(container.textContent).toBe("");
    expect(hasOpenTerms({ ...STATE, terms: [] })).toBe(false);
    expect(hasOpenTerms(null)).toBe(false);
    expect(hasOpenTerms(STATE)).toBe(true);
  });
});

describe("PostingTermsGate", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  it("posts the ticked terms and then runs the generation; nothing ticked is an answer", async () => {
    const fetchMock = vi.fn(async () => json(200, { ...STATE, terms: [], confirmed: [] }));
    global.fetch = fetchMock as unknown as typeof fetch;
    const onAnswered = vi.fn();
    render(withIntl(<PostingTermsGate jobId="j1" state={STATE} onAnswered={onAnswered} onCancel={() => {}} />, "en"));
    fireEvent.click(screen.getByTestId("posting-terms-gate-generate"));
    await waitFor(() => expect(onAnswered).toHaveBeenCalledTimes(1));
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/job\/j1\/posting-terms$/);
    expect(JSON.parse(init.body as string)).toEqual({ confirmed: [] });
  });

  it("does not run the generation when the answer was not stored", async () => {
    global.fetch = vi.fn(async () => json(500, {})) as unknown as typeof fetch;
    const onAnswered = vi.fn();
    render(withIntl(<PostingTermsGate jobId="j1" state={STATE} onAnswered={onAnswered} onCancel={() => {}} />, "en"));
    fireEvent.click(screen.getAllByTestId("posting-term-tick")[1]);
    fireEvent.click(screen.getByTestId("posting-terms-gate-generate"));
    await waitFor(() => expect(screen.getByTestId("posting-terms-error")).toBeTruthy());
    expect(onAnswered).not.toHaveBeenCalled();
  });
});

describe("the receipt and the profile line", () => {
  it("names the terms added", () => {
    render(withIntl(<PostingTermsReceipt confirmed={["Event-driven architecture", "Observability"]} />, "de"));
    expect(screen.getByTestId("posting-terms-receipt").textContent).toBe(
      "2 Begriffe aus der Stellenanzeige als deine Kenntnisse übernommen: Event-driven architecture, Observability.",
    );
  });

  it("says who confirmed the skill, when, and from which posting", () => {
    const skill: Skill = {
      name: "Observability", status: "confirmed",
      confirmations: [{ name: "Observability", via: "person", on: "2026-11-17", posting: "NovaPay" }],
    };
    render(withIntl(<SkillConfirmationLines skill={skill} />, "de"));
    expect(screen.getByTestId("skill-confirmations").textContent).toBe(
      "Observability · Von dir bestätigt am 17.11.2026 · Wortlaut aus der Stellenanzeige NovaPay",
    );
    expect(confirmationKey("Observability", { name: "observability", via: "agent" })).toBe("byAgent");
    expect(confirmationKey("Budgetierung", { name: "Budgetprozess", via: "person" })).toBe("aliasByYou");
  });

  it("keeps a skill's confirmations through a profile edit (round trip)", async () => {
    const skill: Skill = {
      id: "s1", name: "Observability", status: "confirmed",
      confirmations: [{ name: "Observability", via: "agent", on: "2026-11-17", posting: "NovaPay", job_id: "j1" }],
    };
    const fetchMock = vi.fn(async () => json(200, { updated_at: "t2", profile: { skills: [skill] } }));
    global.fetch = fetchMock as unknown as typeof fetch;
    render(withIntl(
      <SkillsEditor entries={[skill]} apiBase="http://api" profileUpdatedAt="2026-11-17T09:00:00Z" onProfileUpdated={vi.fn()} />,
    ));
    fireEvent.click(screen.getByTestId("skill-edit-0"));
    fireEvent.click(screen.getByTestId("skill-entry-save"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
    expect(body[0].confirmations).toEqual(skill.confirmations);
  });
});
