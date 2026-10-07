// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * WP-V (Strawberry build 2) — #717 "Schon in deinem Profil" on the import
 * summary and #709/#716 "Auch bekannt als" chips (ADR-046/063 am. 2026-10-07,
 * rulings V-2 = A).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RecognisedMatches } from "../RecognisedMatches";
import { AliasChips, aliasesOf } from "../AliasChips";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { MatchReceiptItem } from "@/lib/import-cv";

const pair = (over: Partial<MatchReceiptItem> = {}): MatchReceiptItem => ({
  section: "skills",
  entity_id: "s1",
  incoming: "Machine Learning",
  existing: "Maschinelles Lernen",
  basis: "model",
  ...over,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RecognisedMatches (#717)", () => {
  it("renders nothing without pairs", () => {
    const { container } = render(withIntl(<RecognisedMatches matched={[]} />));
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the pairs with section, link and undo — German copy", () => {
    render(
      withIntl(
        <RecognisedMatches
          matched={[
            pair(),
            pair({ section: "languages", entity_id: "l1", incoming: "German", existing: "Deutsch", basis: "name_table" }),
          ]}
        />,
        "de",
      ),
    );
    expect(screen.getByText("Schon in deinem Profil (2)")).toBeInTheDocument();
    expect(screen.getAllByTestId("recognised-match-row")).toHaveLength(2);
    expect(screen.getByText("Fähigkeiten")).toBeInTheDocument();
    // a name-table pair is a fact: a tag, no undo
    expect(screen.getAllByTestId("recognised-match-separate")).toHaveLength(1);
    expect(screen.getByTestId("recognised-match-table")).toHaveTextContent("gleiche Sprache");
    expect(screen.getAllByText("Im Profil zeigen")[0].closest("a")).toHaveAttribute(
      "href",
      "/profile#section-skills",
    );
  });

  it("undo posts the pair and shows the added state", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const onSeparated = vi.fn();
    render(withIntl(<RecognisedMatches matched={[pair()]} onSeparated={onSeparated} />));
    fireEvent.click(screen.getByTestId("recognised-match-separate"));
    await waitFor(() => expect(screen.getByTestId("recognised-match-done")).toBeInTheDocument());
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/profile/matches/separate");
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      entity_id: "s1",
      incoming: "Machine Learning",
    });
    expect(onSeparated).toHaveBeenCalledOnce();
    expect(screen.getByText("Added as its own entry")).toBeInTheDocument();
  });

  it("a failed undo says so and keeps the button", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 409 })));
    render(withIntl(<RecognisedMatches matched={[pair()]} />));
    fireEvent.click(screen.getByTestId("recognised-match-separate"));
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getByTestId("recognised-match-separate")).toBeInTheDocument();
  });

  it("folds after five rows and hides undone receipts", () => {
    const many = Array.from({ length: 7 }, (_, i) => pair({ entity_id: `s${i}`, incoming: `x${i}` }));
    many.push(pair({ entity_id: "gone", incoming: "old", undone_at: "2026-10-07T10:00:00Z" }));
    render(withIntl(<RecognisedMatches matched={many} />));
    expect(screen.getAllByTestId("recognised-match-row")).toHaveLength(5);
    fireEvent.click(screen.getByTestId("recognised-matches-show-all"));
    expect(screen.getAllByTestId("recognised-match-row")).toHaveLength(7);
  });
});

describe("AliasChips (#709/#716)", () => {
  const entries = [
    { id: "w1", company: "Roche Diagnostics GmbH", role: "System Analyst", company_aliases: ["Roche"], role_aliases: ["Systemanalytiker"] },
    { id: "w2", company: "Acme", role: "Dev", role_aliases: [] },
  ];

  it("reads every alias field of the section", () => {
    expect(aliasesOf("work_experience", entries[0])).toEqual([
      { field: "company_aliases", value: "Roche" },
      { field: "role_aliases", value: "Systemanalytiker" },
    ]);
    expect(aliasesOf("work_experience", entries[1])).toEqual([]);
  });

  it("renders nothing for an entry without aliases", () => {
    const { container } = render(
      withIntl(
        <AliasChips section="work_experience" entries={entries} index={1} apiBase="" profileUpdatedAt="t" onProfileUpdated={() => {}} />,
      ),
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("removing the last alias of a field saves the section without that key", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ profile: { work_experience: [] }, updated_at: "u" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const onUpdated = vi.fn();
    render(
      withIntl(
        <AliasChips section="work_experience" entries={entries} index={0} apiBase="" profileUpdatedAt="t" onProfileUpdated={onUpdated} />,
        "de",
      ),
    );
    expect(screen.getByText("Auch bekannt als")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "„Roche“ als weiteren Namen entfernen" }));
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
    const body = String((fetchMock.mock.calls[0][1] as RequestInit).body);
    expect(body).not.toContain("company_aliases");
    expect(body).toContain("Systemanalytiker"); // the other field's alias survives
  });

  it("a pill section names the entry", () => {
    render(
      withIntl(
        <AliasChips
          section="skills"
          entries={[{ id: "s1", name: "Maschinelles Lernen", aliases: ["Machine Learning"] }]}
          index={0}
          prefix="Maschinelles Lernen"
          apiBase=""
          profileUpdatedAt="t"
          onProfileUpdated={() => {}}
        />,
      ),
    );
    expect(screen.getByText("Maschinelles Lernen — also known as")).toBeInTheDocument();
    expect(screen.getByTestId("alias-chip")).toHaveTextContent("Machine Learning");
  });
});
