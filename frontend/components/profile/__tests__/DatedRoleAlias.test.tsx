// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * R2-4 (Hazelnut build 4, mock V-1) — an additional role at the same employer
 * since a stated date renders as "auch <Rolle> seit <Datum>" at the top of the
 * entry, its bullet first; ✕ removes the alias AND its dated record.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { WorkExperienceEditor } from "../WorkExperienceEditor";
import { aliasesOf, withoutAlias } from "../AliasChips";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { WorkEntry } from "@/lib/profile-entries";

const ROLE = "Workstream Lead für den DORA-Readiness-Workstream";
const NEW_BULLET = "Koordiniere das ICT-Risikoregister.";

const ENTRY: WorkEntry = {
  id: "w-cargo",
  company: "Cargonaut Logistics GmbH",
  role: "Senior Backend Engineer",
  start_date: "2021-03",
  is_current: true,
  achievements: ["Cut p95 latency from 1.8s to 240ms."],
  responsibilities: [NEW_BULLET, "Own the AWS deployment."],
  technologies: [],
  role_aliases: [ROLE, "Backend Engineer"],
  dated_role_aliases: [{ role: ROLE, since: "2026-08", bullets: [NEW_BULLET] }],
};

function jsonResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

function renderEditor(entries: WorkEntry[], locale: "de" | "en" = "de") {
  const onProfileUpdated = vi.fn();
  render(
    withIntl(
      <WorkExperienceEditor
        entries={entries}
        apiBase="http://api"
        profileUpdatedAt="2026-10-24T09:00:00Z"
        onProfileUpdated={onProfileUpdated}
      />,
      locale,
    ),
  );
  return { onProfileUpdated };
}

describe("dated role alias (R2-4)", () => {
  beforeEach(() => {
    global.fetch = vi.fn() as unknown as typeof fetch;
  });

  it("shows 'auch <Rolle> seit <Datum>' and keeps undated aliases as chips", () => {
    renderEditor([ENTRY]);
    const line = screen.getByTestId("dated-role-alias");
    expect(line.textContent).toContain("auch");
    expect(line.textContent).toContain(`${ROLE} seit`);
    expect(line.textContent).toMatch(/Aug\.? 2026/);
    const chips = screen.getAllByTestId("alias-chip").map((c) => c.textContent);
    expect(chips).toEqual(["Backend Engineer"]); // the dated role is not repeated as a chip
  });

  it("renders the English form", () => {
    renderEditor([ENTRY], "en");
    expect(screen.getByTestId("dated-role-alias").textContent).toContain(`also${ROLE} since`);
  });

  it("puts the dated role's bullet first, with its since tag", () => {
    renderEditor([ENTRY]);
    const items = within(screen.getByTestId("dated-role-aliases").parentElement as HTMLElement)
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(items[0]).toContain(NEW_BULLET);
    expect(items[0]).toMatch(/^seit Aug\.? 2026/);
    expect(items[1]).toContain("Cut p95 latency");
  });

  it("removing the dated role drops its alias and its dated record in one save", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(200, { updated_at: "t2", profile: { work_experience: [ENTRY] } }),
    );
    global.fetch = fetchMock as unknown as typeof fetch;
    renderEditor([ENTRY]);
    fireEvent.click(screen.getByTestId("dated-role-alias-remove"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
    const sent = body[0] as WorkEntry;
    expect(sent.role_aliases).toEqual(["Backend Engineer"]);
    expect("dated_role_aliases" in sent).toBe(false);
  });

  it("a dated record whose alias is gone is not shown", () => {
    const orphan = { ...ENTRY, role_aliases: ["Backend Engineer"] };
    renderEditor([orphan]);
    expect(screen.queryByTestId("dated-role-alias")).toBeNull();
  });

  it("helpers: aliasesOf skips the dated role; withoutAlias keeps other dated records", () => {
    expect(aliasesOf("work_experience", ENTRY).map((a) => a.value)).toEqual(["Backend Engineer"]);
    const two = {
      ...ENTRY,
      role_aliases: [ROLE, "Tech Lead"],
      dated_role_aliases: [...ENTRY.dated_role_aliases!, { role: "Tech Lead", since: "2026-09" }],
    };
    expect(withoutAlias(two, "role_aliases", ROLE).dated_role_aliases).toEqual([
      { role: "Tech Lead", since: "2026-09" },
    ]);
  });
});
