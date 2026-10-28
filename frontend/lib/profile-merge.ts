// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

// Hazelnut build 5 (U-11, founder idea 10-28; INTERFACE A1 item 4) — the user
// merges two entries of one section: "source" is folded into "target" (target
// keeps its id; source's title/company become aliases). Preview never writes;
// commit goes through commit_ops and is undone by /api/profile/undo-last-merge.

import { ReviewApiError } from "@/lib/profile-review";
import { formatEntryPeriod, type ProfileSectionsResponse } from "@/lib/profile-entries";
import type { DisplayLocale } from "@/lib/profileDisplay";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export const MERGE_SECTIONS = [
  "work_experience",
  "volunteer_activities",
  "projects",
  "education",
] as const;
export type MergeSection = (typeof MERGE_SECTIONS)[number];

export function isMergeSection(section: string): section is MergeSection {
  return (MERGE_SECTIONS as readonly string[]).includes(section);
}

export interface EntryRef {
  section: MergeSection;
  id: string;
  label: string;
  /** Display period, e.g. "03/2015 – 06/2019"; empty when the entry has none. */
  period: string;
}

export interface DatePair {
  start_date?: string | null;
  end_date?: string | null;
}

export interface DuplicatePair {
  pair_id: string;
  target_text: string;
  source_text: string;
  /** The model's one-sentence merge, or null when its fact check failed. */
  proposal: string | null;
}

export interface MergePreview {
  merged_entry: Record<string, unknown>;
  aliases: Array<Record<string, string | null | undefined>>;
  date_conflict: { target: DatePair; source: DatePair } | null;
  duplicate_pairs: DuplicatePair[];
  unique_bullets: string[];
}

export type DuplicateOutcome = "keep_current" | "take_new" | "keep_both" | "merge";

export interface DuplicateChoice {
  pair_id: string;
  target_text: string;
  source_text: string;
  outcome: DuplicateOutcome;
  merged_text?: string;
}

export interface MergeChoices {
  dates?: "target" | "source";
  duplicates?: DuplicateChoice[];
}

async function call<T>(body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}/api/profile/entries/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let code: string | null = null;
    try {
      const detail = (await res.json())?.detail;
      code = typeof detail === "string" ? detail : (detail?.code ?? null);
    } catch {
      /* keep null */
    }
    throw new ReviewApiError(res.status, code, code ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function previewMerge(
  section: MergeSection,
  targetId: string,
  sourceId: string,
): Promise<MergePreview> {
  return call<MergePreview>({ section, target_id: targetId, source_id: sourceId, mode: "preview" });
}

export function commitMerge(
  section: MergeSection,
  targetId: string,
  sourceId: string,
  choices: MergeChoices,
): Promise<ProfileSectionsResponse> {
  return call<ProfileSectionsResponse>({
    section,
    target_id: targetId,
    source_id: sourceId,
    mode: "commit",
    choices,
  });
}

export async function undoLastMerge(): Promise<{ restored: boolean; discarded_later_edits?: boolean }> {
  const res = await fetch(`${API_BASE}/api/profile/undo-last-merge`, { method: "POST" });
  if (!res.ok) throw new ReviewApiError(res.status, null, `HTTP ${res.status}`);
  return res.json();
}

export async function keepEntriesSeparate(section: string, entryIds: string[]): Promise<boolean> {
  const res = await fetch(`${API_BASE}/api/profile/entries/keep-separate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, entry_ids: entryIds }),
  });
  return res.ok;
}

/**
 * Every open question of a preview answered? Dates need a pick when they
 * differ; every duplicate pair needs an outcome, and a "merge" outcome needs a
 * non-empty text. The commit button stays disabled until this holds — the
 * backend would refuse (422 dates_choice_required) anyway.
 */
export function previewComplete(
  preview: MergePreview,
  dates: "target" | "source" | null,
  answers: Record<string, { outcome: DuplicateOutcome; merged_text?: string }>,
): boolean {
  if (preview.date_conflict && !dates) return false;
  return preview.duplicate_pairs.every((p) => {
    const a = answers[p.pair_id];
    if (!a) return false;
    return a.outcome !== "merge" || (a.merged_text ?? "").trim().length > 0;
  });
}

/** The commit payload; echoes each pair's two texts (A1: else 409 stale_preview). */
export function buildChoices(
  preview: MergePreview,
  dates: "target" | "source" | null,
  answers: Record<string, { outcome: DuplicateOutcome; merged_text?: string }>,
): MergeChoices {
  const choices: MergeChoices = {};
  if (preview.date_conflict && dates) choices.dates = dates;
  if (preview.duplicate_pairs.length > 0) {
    choices.duplicates = preview.duplicate_pairs.map((p) => {
      const a = answers[p.pair_id];
      const out: DuplicateChoice = {
        pair_id: p.pair_id,
        target_text: p.target_text,
        source_text: p.source_text,
        outcome: a.outcome,
      };
      if (a.outcome === "merge") out.merged_text = (a.merged_text ?? "").trim();
      return out;
    });
  }
  return choices;
}

/** "03/2015 – 06/2019" from a DatePair, via the caller's formatter. */
export function formatPair(pair: DatePair, fmt: (d: string) => string, present: string): string {
  const start = pair.start_date ? fmt(pair.start_date) : "";
  const end = pair.end_date ? fmt(pair.end_date) : present;
  return [start, end].filter(Boolean).join(" – ");
}

interface RefSource {
  id?: string | null;
  role?: string | null;
  company?: string | null;
  organization?: string | null;
  name?: string | null;
  degree?: string | null;
  institution?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  is_current?: boolean | null;
  year?: string | null;
}

const filled = (s: string | null | undefined): s is string => typeof s === "string" && s.trim() !== "";

/**
 * The ONE place an entry's merge identity is built — the editors (controls,
 * drop zones) and the profile page (picker list, hub's duplicate pair) must
 * show the same label and period for the same entry. Labels follow what each
 * editor already shows; `null` for an entry without an id (nothing to merge).
 */
export function toEntryRef(
  section: MergeSection,
  entry: RefSource,
  labels: { present: string; notProvided: string },
  locale: DisplayLocale,
): EntryRef | null {
  if (!filled(entry.id)) return null;
  const parts =
    section === "work_experience"
      ? [entry.role, entry.company]
      : section === "volunteer_activities"
        ? [entry.role, entry.organization]
        : section === "projects"
          ? [entry.name]
          : [entry.degree, entry.institution];
  const raw = parts.filter(filled).join(section === "education" ? " · " : " @ ");
  const period =
    formatEntryPeriod(
      entry.start_date,
      entry.end_date,
      labels.present,
      section === "education" ? undefined : entry.is_current,
      locale,
    ) ?? (section === "education" && filled(entry.year) ? entry.year : null);
  return { section, id: entry.id, label: raw || labels.notProvided, period: period ?? "" };
}
