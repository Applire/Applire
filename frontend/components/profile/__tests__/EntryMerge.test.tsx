// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** U-11 — merging two entries of one profile section (EntryMerge + editor wiring). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  EntryDropZone,
  EntryMergeControls,
  EntryMergeProvider,
} from "../EntryMerge";
import { WorkExperienceEditor } from "../WorkExperienceEditor";
import { withIntl } from "@/lib/test-utils/with-intl";
import type { EntryRef } from "@/lib/profile-merge";
import type { WorkEntry } from "@/lib/profile-entries";

const W1: EntryRef = { section: "work_experience", id: "w1", label: "Disponent @ Nordwerk Logistik GmbH", period: "03/2015 → 06/2019" };
const W2: EntryRef = { section: "work_experience", id: "w2", label: "Disponent @ Nordwerk Logistik", period: "03/2015 → 06/2019" };
const P1: EntryRef = { section: "projects", id: "p1", label: "Lagerportal", period: "" };
const P2: EntryRef = { section: "projects", id: "p2", label: "Portal Lager", period: "" };

const PREVIEW = {
  merged_entry: {},
  aliases: [{ role: "Disponent", company: "Nordwerk Logistik", title: "" }],
  date_conflict: {
    target: { start_date: "2015-03", end_date: "2019-06" },
    source: { start_date: "2015-04", end_date: null },
  },
  duplicate_pairs: [
    { pair_id: "d1", target_text: "Tourenplanung optimiert", source_text: "Touren geplant", proposal: "Touren geplant und optimiert" },
  ],
  unique_bullets: ["Zollabwicklung betreut"],
};

function res(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;
let commitResponses: Response[];

function bodies(mode: string): Array<Record<string, unknown>> {
  return fetchMock.mock.calls
    .filter(([u]) => String(u).endsWith("/api/profile/entries/merge"))
    .map(([, init]) => JSON.parse((init as RequestInit).body as string))
    .filter((b) => b.mode === mode);
}

beforeEach(() => {
  commitResponses = [res(200, { ok: true })];
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).endsWith("/api/profile/undo-last-merge")) return res(200, { restored: true });
    const body = JSON.parse((init?.body as string) ?? "{}");
    if (body.mode === "preview") return res(200, PREVIEW);
    return commitResponses.shift() ?? res(200, {});
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function Harness({ onProfileChanged = vi.fn() }: { onProfileChanged?: () => void }) {
  const all = [W1, W2, P1, P2];
  return (
    <EntryMergeProvider
      entriesBySection={{ work_experience: [W1, W2], projects: [P1, P2] }}
      onProfileChanged={onProfileChanged}
    >
      {all.map((r) => (
        <EntryDropZone key={r.id} entry={r} className="row">
          <span data-testid={`row-${r.id}`}>{r.label}</span>
          <EntryMergeControls entry={r} />
        </EntryDropZone>
      ))}
    </EntryMergeProvider>
  );
}

function renderHarness(onProfileChanged = vi.fn()) {
  render(withIntl(<Harness onProfileChanged={onProfileChanged} />));
  return onProfileChanged;
}

async function openPreviewViaPicker() {
  fireEvent.click(screen.getAllByTestId("entry-merge-open")[0]); // W1 = source
  const options = screen.getAllByTestId("entry-merge-pick-option");
  expect(options).toHaveLength(1);
  expect(options[0].textContent).toContain("same period");
  fireEvent.click(options[0]);
  fireEvent.click(screen.getByTestId("entry-merge-pick-next"));
  await screen.findByTestId("entry-merge-confirm");
  await screen.findByTestId("entry-merge-dates-target");
}

describe("EntryMerge", () => {
  it("(a) picker -> next fetches the preview with section/target/source", async () => {
    renderHarness();
    await openPreviewViaPicker();
    expect(bodies("preview")).toEqual([
      { section: "work_experience", target_id: "w2", source_id: "w1", mode: "preview" },
    ]);
    // alias chip joins non-empty values only
    expect(screen.getByText("Disponent · Nordwerk Logistik")).toBeTruthy();
  });

  it("(b) confirm stays disabled until dates and every pair are answered; empty combine keeps it disabled", async () => {
    renderHarness();
    await openPreviewViaPicker();
    const confirm = screen.getByTestId("entry-merge-confirm") as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    expect(screen.getByText("Answer the open questions first.")).toBeTruthy();
    fireEvent.click(screen.getByTestId("entry-merge-dates-source"));
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByTestId("entry-merge-dup-merge"));
    const ta = screen.getByTestId("entry-merge-dup-text") as HTMLTextAreaElement;
    expect(ta.value).toBe("Touren geplant und optimiert");
    expect(confirm.disabled).toBe(false);
    fireEvent.change(ta, { target: { value: "   " } });
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByTestId("entry-merge-dup-keep_both"));
    expect(confirm.disabled).toBe(false);
  });

  it("(c) commit body echoes the pair texts and carries dates and merged_text", async () => {
    renderHarness();
    await openPreviewViaPicker();
    fireEvent.click(screen.getByTestId("entry-merge-dates-target"));
    fireEvent.click(screen.getByTestId("entry-merge-dup-merge"));
    fireEvent.change(screen.getByTestId("entry-merge-dup-text"), { target: { value: "Touren geplant, optimiert" } });
    fireEvent.click(screen.getByTestId("entry-merge-confirm"));
    await screen.findByTestId("entry-merge-banner");
    expect(bodies("commit")).toEqual([
      {
        section: "work_experience",
        target_id: "w2",
        source_id: "w1",
        mode: "commit",
        choices: {
          dates: "target",
          duplicates: [
            {
              pair_id: "d1",
              target_text: "Tourenplanung optimiert",
              source_text: "Touren geplant",
              outcome: "merge",
              merged_text: "Touren geplant, optimiert",
            },
          ],
        },
      },
    ]);
  });

  it("(d) 409 stale_preview re-fetches the preview and shows the stale note", async () => {
    commitResponses = [res(409, { detail: "stale_preview" })];
    renderHarness();
    await openPreviewViaPicker();
    fireEvent.click(screen.getByTestId("entry-merge-dates-target"));
    fireEvent.click(screen.getByTestId("entry-merge-dup-keep_both"));
    fireEvent.click(screen.getByTestId("entry-merge-confirm"));
    await screen.findByTestId("entry-merge-stale");
    await waitFor(() => expect(bodies("preview")).toHaveLength(2));
    // answers were reset
    await waitFor(() =>
      expect((screen.getByTestId("entry-merge-confirm") as HTMLButtonElement).disabled).toBe(true),
    );
    expect(screen.queryByTestId("entry-merge-banner")).toBeNull();
  });

  it("(e) success shows the banner; undo POSTs and refreshes the profile", async () => {
    const onChanged = renderHarness();
    await openPreviewViaPicker();
    fireEvent.click(screen.getByTestId("entry-merge-dates-target"));
    fireEvent.click(screen.getByTestId("entry-merge-dup-keep_current"));
    fireEvent.click(screen.getByTestId("entry-merge-confirm"));
    const banner = await screen.findByTestId("entry-merge-banner");
    expect(banner.textContent).toContain(W1.label);
    expect(screen.queryByTestId("entry-merge-dialog")).toBeNull();
    expect(onChanged).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("entry-merge-undo"));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(2));
    const undoCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/profile/undo-last-merge"));
    expect(undoCall).toBeTruthy();
    expect((undoCall![1] as RequestInit).method).toBe("POST");
    expect(screen.getByTestId("entry-merge-banner").textContent).toContain("Undone");
  });

  it("(f) drag one entry onto another fetches the preview; another section shows no hint", async () => {
    renderHarness();
    const dt = { setData: vi.fn(), effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(screen.getAllByTestId("entry-merge-handle")[0], { dataTransfer: dt });
    expect(dt.setData).toHaveBeenCalledWith("text/plain", "w1");

    // other section: no hint
    fireEvent.dragOver(screen.getByTestId("row-p1"), { dataTransfer: dt });
    expect(screen.queryByTestId("entry-merge-drop-hint")).toBeNull();
    // same entry: no hint
    fireEvent.dragOver(screen.getByTestId("row-w1"), { dataTransfer: dt });
    expect(screen.queryByTestId("entry-merge-drop-hint")).toBeNull();

    fireEvent.dragOver(screen.getByTestId("row-w2"), { dataTransfer: dt });
    expect(screen.getByTestId("entry-merge-drop-hint").textContent).toContain(W2.label);
    fireEvent.drop(screen.getByTestId("row-w2"), { dataTransfer: dt });
    await screen.findByTestId("entry-merge-confirm");
    expect(bodies("preview")).toEqual([
      { section: "work_experience", target_id: "w2", source_id: "w1", mode: "preview" },
    ]);
  });
});

const ENTRIES: WorkEntry[] = [
  { id: "w1", company: "Nordwerk Logistik GmbH", role: "Disponent", start_date: "2015-03", end_date: "2019-06", is_current: false },
  { id: "w2", company: "Nordwerk Logistik", role: "Disponent", start_date: "2015-03", end_date: "2019-06", is_current: false },
];

function editor() {
  return (
    <WorkExperienceEditor
      entries={ENTRIES}
      apiBase="http://api"
      profileUpdatedAt="2026-10-28T09:00:00Z"
      onProfileUpdated={vi.fn()}
    />
  );
}

describe("editor wiring", () => {
  it("(g) without a provider the editor renders no merge controls", () => {
    render(withIntl(editor()));
    expect(screen.getByTestId("work-entry-edit-0")).toBeTruthy();
    expect(screen.queryByTestId("entry-merge-open")).toBeNull();
  });

  it("(h) with a provider and two entries, each entry gets a merge button", () => {
    render(
      withIntl(
        <EntryMergeProvider entriesBySection={{ work_experience: [W1, W2] }} onProfileChanged={vi.fn()}>
          {editor()}
        </EntryMergeProvider>,
      ),
    );
    expect(screen.getAllByTestId("entry-merge-open")).toHaveLength(2);
    expect(screen.getByTestId("work-entry-edit-0")).toBeTruthy();
  });
});
