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

export function aliasesOf(section: string, entry: Entry): Array<{ field: string; value: string }> {
  const fields = ALIAS_FIELDS_BY_SECTION[section] ?? [];
  const out: Array<{ field: string; value: string }> = [];
  for (const field of fields) {
    const list = entry[field];
    if (!Array.isArray(list)) continue;
    for (const value of list) {
      if (typeof value === "string" && value.trim()) out.push({ field, value });
    }
  }
  return out;
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
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const entry = entries[index];
  if (!entry) return null;
  const aliases = aliasesOf(section, entry);
  if (aliases.length === 0) return null;

  async function remove(field: string, value: string) {
    if (busy) return;
    const current = entries[index];
    const list = Array.isArray(current[field]) ? (current[field] as string[]) : [];
    const remaining = list.filter((a) => a !== value);
    // The backend omits an EMPTY alias list from every dump, so the last alias
    // removed drops the key (keeps the H0.4 sent-vs-returned check equal).
    const updated: E = { ...current, [field]: remaining };
    if (remaining.length === 0 && field !== "role_aliases") delete updated[field];
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
