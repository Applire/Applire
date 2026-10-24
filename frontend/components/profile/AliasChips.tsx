// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * #709 / #716 — "Auch bekannt als" chips (ADR-046 amended 2026-10-07; mock
 * gate V-3). Shows the alternate names the vault recorded for ONE entry and
 * lets the candidate remove a wrong one. Removal is an ordinary section save
 * (`saveProfileSection` → `ReplaceSection` through the committer), so the
 * profile history records it like every other edit. There is deliberately no
 * free-text entry: an alias is only ever a name an import read (ADR-046 cl. 2).
 *
 * Renders nothing when the entry carries no alias.
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { saveProfileSection } from "@/lib/sectionSave";
import type { ProfileSectionsResponse } from "@/lib/profile-entries";

export const ALIAS_FIELDS_BY_SECTION: Record<string, readonly string[]> = {
  skills: ["aliases"],
  languages: ["aliases"],
  work_experience: ["company_aliases", "role_aliases"],
  volunteer_activities: ["organization_aliases"],
  education: ["institution_aliases", "degree_aliases"],
};

type Entry = { id?: string; [key: string]: unknown };

/** R2-4 (ADR-013 note 2026-10-24) — an ADDITIONAL role held since a stated
 * date. Its role text is also in `role_aliases`; the dated line shows it at
 * the top of the entry instead of among the "Auch bekannt als" chips. */
export interface DatedRoleAlias {
  role: string;
  since: string;
  bullets?: string[];
}

function normName(value: string): string {
  return value.normalize("NFC").trim().toLowerCase();
}

export function datedRoleAliasesOf(entry: Entry): DatedRoleAlias[] {
  const list = entry.dated_role_aliases;
  if (!Array.isArray(list)) return [];
  const aliases = Array.isArray(entry.role_aliases) ? (entry.role_aliases as unknown[]) : [];
  const names = new Set(aliases.filter((a): a is string => typeof a === "string").map(normName));
  return list.filter(
    (d): d is DatedRoleAlias =>
      !!d &&
      typeof (d as DatedRoleAlias).role === "string" &&
      typeof (d as DatedRoleAlias).since === "string" &&
      names.has(normName((d as DatedRoleAlias).role)),
  );
}

export function aliasesOf(section: string, entry: Entry): Array<{ field: string; value: string }> {
  const fields = ALIAS_FIELDS_BY_SECTION[section] ?? [];
  const dated = new Set(
    section === "work_experience" ? datedRoleAliasesOf(entry).map((d) => normName(d.role)) : [],
  );
  const out: Array<{ field: string; value: string }> = [];
  for (const field of fields) {
    const list = entry[field];
    if (!Array.isArray(list)) continue;
    for (const value of list) {
      if (typeof value !== "string" || !value.trim()) continue;
      if (field === "role_aliases" && dated.has(normName(value))) continue;
      out.push({ field, value });
    }
  }
  return out;
}

/** The entry with one alias removed — and, for a role alias, its dated record
 * with it (the backend drops a dated record whose alias is gone; sending it
 * would read as a lost write to the H0.4 detector). */
export function withoutAlias<E extends Entry>(current: E, field: string, value: string): E {
  const list = Array.isArray(current[field]) ? (current[field] as string[]) : [];
  const remaining = list.filter((a) => a !== value);
  // The backend omits an EMPTY alias list from every dump, so the last alias
  // removed drops the key (keeps the H0.4 sent-vs-returned check equal).
  const updated: E = { ...current, [field]: remaining };
  if (remaining.length === 0 && field !== "role_aliases") delete updated[field];
  if (field === "role_aliases" && Array.isArray(current.dated_role_aliases)) {
    const keep = (current.dated_role_aliases as DatedRoleAlias[]).filter(
      (d) => normName(d.role) !== normName(value),
    );
    if (keep.length === 0) delete (updated as Entry).dated_role_aliases;
    else (updated as Entry).dated_role_aliases = keep;
  }
  return updated;
}

function useAliasRemoval<E extends Entry>({
  section,
  entries,
  index,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
}: {
  section: string;
  entries: E[];
  index: number;
  apiBase: string;
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  async function remove(field: string, value: string) {
    if (busy) return;
    const current = entries[index];
    const updated = withoutAlias(current, field, value);
    const nextEntries = entries.map((e, i) => (i === index ? updated : e));
    setBusy(true);
    setFailed(false);
    const result = await saveProfileSection<ProfileSectionsResponse>({
      apiBase,
      section,
      entries: nextEntries,
      basisUpdatedAt: profileUpdatedAt,
      savedEntryId: typeof current.id === "string" ? current.id : undefined,
    });
    setBusy(false);
    if (result.status === "ok") {
      onProfileUpdated(result.profile);
      return;
    }
    if (result.status === "stale") {
      onProfileUpdated(result.current);
    }
    setFailed(true);
  }

  return { busy, failed, remove };
}

export function AliasChips<E extends Entry>({
  section,
  entries,
  index,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
  prefix,
}: {
  /** Pill-shaped sections (skills, languages) name the entry the line is about. */
  prefix?: string;
  section: string;
  entries: E[];
  index: number;
  apiBase: string;
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
}) {
  const t = useTranslations("profileAliases");
  const { busy, failed, remove } = useAliasRemoval({
    section, entries, index, apiBase, profileUpdatedAt, onProfileUpdated,
  });
  const entry = entries[index];
  if (!entry) return null;
  const aliases = aliasesOf(section, entry);
  if (aliases.length === 0) return null;

  return (
    <div
      data-testid={`alias-chips-${section}`}
      className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[12px] text-on-surface-variant"
    >
      <span>{prefix ? t("labelFor", { name: prefix }) : t("label")}</span>
      {aliases.map(({ field, value }) => (
        <span
          key={`${field}-${value}`}
          data-testid="alias-chip"
          className="inline-flex items-center gap-1 rounded-full border border-outline-variant bg-surface-container py-0.5 pl-2.5 pr-1 text-on-surface"
        >
          {value}
          <button
            type="button"
            data-testid="alias-chip-remove"
            aria-label={t("remove", { alias: value })}
            disabled={busy}
            onClick={() => void remove(field, value)}
            className="inline-flex h-5 w-5 items-center justify-center rounded-full text-on-surface-variant hover:bg-white disabled:opacity-50"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </span>
      ))}
      {failed && (
        <span role="alert" className="text-red-700">
          {t("removeFailed")}
        </span>
      )}
    </div>
  );
}

/**
 * R2-4 (mock V-1, ADR-013 note 2026-10-24) — "auch <Rolle> seit <Datum>": an
 * additional role at this employer, shown at the top of the entry. ✕ removes
 * it like any alias (an ordinary section save through the committer).
 */
export function DatedRoleAliasLines<E extends Entry>({
  entries,
  index,
  apiBase,
  profileUpdatedAt,
  onProfileUpdated,
  formatSince,
}: {
  entries: E[];
  index: number;
  apiBase: string;
  profileUpdatedAt: string;
  onProfileUpdated: (profile: ProfileSectionsResponse) => void;
  /** Month + year in the UI locale. */
  formatSince: (since: string) => string;
}) {
  const t = useTranslations("profileAliases");
  const { busy, failed, remove } = useAliasRemoval({
    section: "work_experience", entries, index, apiBase, profileUpdatedAt, onProfileUpdated,
  });
  const entry = entries[index];
  if (!entry) return null;
  const dated = datedRoleAliasesOf(entry);
  if (dated.length === 0) return null;
  return (
    <div data-testid="dated-role-aliases" className="mt-1.5 space-y-1">
      {dated.map((d) => (
        <div
          key={`${d.role}-${d.since}`}
          data-testid="dated-role-alias"
          className="flex flex-wrap items-center gap-1.5 text-sm text-on-surface"
        >
          <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-primary">
            {t("alsoTag")}
          </span>
          <span>
            {t.rich("alsoSince", {
              role: d.role,
              date: formatSince(d.since),
              b: (chunks) => <b className="font-semibold">{chunks}</b>,
            })}
          </span>
          <button
            type="button"
            data-testid="dated-role-alias-remove"
            aria-label={t("remove", { alias: d.role })}
            disabled={busy}
            onClick={() => void remove("role_aliases", d.role)}
            className="inline-flex h-5 w-5 items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container disabled:opacity-50"
          >
            <X aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      {failed && (
        <span role="alert" className="text-[12px] text-red-700">
          {t("removeFailed")}
        </span>
      )}
    </div>
  );
}
