// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

import {
  getReceiptSummary,
  type ReceiptFact,
  type ReceiptGroup,
  type ReceiptSummary,
} from "@/lib/api/review";

/**
 * ADR-040 amended 2026-10-22 (Hazelnut b3 WP-V, ruling V-1 = A) — the
 * interview receipt as a SUMMARY of this session: a lead line, one card per
 * section with counts, "Das Wichtigste", and the full list behind one
 * expander. The backend producer (`services/profile/receipts.py`) has already
 * made it one fact per line; this component never joins, truncates or
 * re-deduplicates — it lays out what it is given.
 *
 * Replaced: the 38-row audit log the UX audit found (Marcus, 2026-10-20) —
 * every trail row of every interview ever, list values joined with ", ",
 * long lines cut with "…" and no way to expand them.
 */

const ENGAGEMENT = new Set(["work_experience", "projects", "volunteer_activities"]);
const POINT_KINDS = new Set(["responsibility", "result", "detail"]);

/** "Role @ Company" → "Role – Company" (display only). */
function displayLabel(label: string): string {
  return label.replace(" @ ", " – ");
}

/** The role part of an entity label, for the short "— Role" attribution. */
function rolePart(label: string): string {
  return label.split(" @ ")[0] ?? label;
}

export interface ReceiptSummaryReviewProps {
  /** The interview session to summarise; the latest one when absent. */
  sessionId?: string | null;
  /** Per-row correction entry point (Branch G). */
  onFix?: (fact: ReceiptFact) => void;
}

export function ReceiptSummaryReview({ sessionId, onFix }: ReceiptSummaryReviewProps) {
  const t = useTranslations("review");
  const [summary, setSummary] = useState<ReceiptSummary | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getReceiptSummary(sessionId)
      .then((s) => {
        if (!cancelled) setSummary(s);
      })
      .catch(() => {
        if (!cancelled) setSummary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const sectionName = (section: string) =>
    section === "limits"
      ? t("summary.limits")
      : t.has(`section.${section}`)
        ? t(`section.${section}`)
        : t("section.*");

  if (summary === null) return null;

  // The full list, grouped per section and — for roles/projects — per entity.
  const fullGroups: { heading: string; facts: ReceiptFact[] }[] = [];
  for (const fact of summary.facts) {
    const heading =
      ENGAGEMENT.has(fact.section) && fact.entity_label
        ? `${sectionName(fact.section)} · ${displayLabel(fact.entity_label)}`
        : sectionName(fact.section);
    const existing = fullGroups.find((g) => g.heading === heading);
    if (existing) existing.facts.push(fact);
    else fullGroups.push({ heading, facts: [fact] });
  }
  if (summary.facts_total === 0) {
    return (
      <section data-testid="receipt-summary" className="rounded-xl border border-outline-variant bg-white p-4 text-left">
        <h3 className="text-base font-semibold text-on-surface">{t("titleInterview")}</h3>
        <p data-testid="receipt-summary-empty" className="mt-2 text-sm text-on-surface-variant">
          {t("empty")}
        </p>
      </section>
    );
  }

  const groupCount = (g: ReceiptGroup) => {
    if (g.section === "limits") return t("summary.limitsCount", { count: g.count });
    if (ENGAGEMENT.has(g.section)) {
      const facts = summary.facts.filter((f) => f.section === g.section);
      const points = facts.filter((f) => POINT_KINDS.has(f.kind)).length;
      const tools = facts.filter((f) => f.kind === "tool").length;
      const parts = [];
      if (points) parts.push(t("summary.points", { count: points }));
      if (tools) parts.push(t("summary.tools", { count: tools }));
      if (g.entities.length > 1) parts.push(t("summary.entities", { count: g.entities.length }));
      return parts.length ? parts.join(" · ") : t("summary.added", { count: g.count });
    }
    return t("summary.added", { count: g.count });
  };

  return (
    <section
      data-testid="receipt-summary"
      className="rounded-xl border border-outline-variant bg-white p-4 text-left sm:p-6"
    >
      <h3 className="text-base font-semibold text-on-surface">{t("titleInterview")}</h3>
      <p data-testid="receipt-summary-lead" className="mt-1 text-sm text-on-surface-variant">
        {t("summary.lead", { count: summary.facts_total })}
      </p>

      <ul className="mt-4 grid gap-2.5">
        {summary.groups.map((g) => (
          <li
            key={g.section}
            data-testid="receipt-summary-group"
            data-section={g.section}
            className="rounded-lg border border-outline-variant px-3.5 py-3"
          >
            <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:justify-between sm:gap-3">
              <span className="text-sm font-semibold text-on-surface">{sectionName(g.section)}</span>
              <span className="text-xs text-on-surface-variant sm:whitespace-nowrap">{groupCount(g)}</span>
            </div>
            {ENGAGEMENT.has(g.section) && g.entities.length > 0 ? (
              <p className="mt-1 text-sm text-on-surface">
                {g.entities
                  .map((e) => t("summary.entityCount", { label: displayLabel(e.label), count: e.count }))
                  .join(" · ")}
              </p>
            ) : (
              <p className="mt-1 text-sm text-on-surface">
                {g.preview.join(", ")}
                {g.more > 0 && (
                  <span className="text-on-surface-variant"> {t("summary.more", { count: g.more })}</span>
                )}
              </p>
            )}
          </li>
        ))}
      </ul>

      {summary.top.length > 0 && (
        <div className="mt-5">
          <h4 className="text-sm font-semibold text-on-surface">{t("summary.top")}</h4>
          <ul data-testid="receipt-summary-top" className="mt-2 list-disc space-y-1 pl-5 text-sm text-on-surface">
            {summary.top.map((f) => (
              <li key={f.key}>
                {f.text}
                {f.detail && <span className="block text-on-surface-variant">{f.detail}</span>}
                <span className="text-xs text-on-surface-variant">
                  {" "}
                  {t("summary.attribution", {
                    where: f.entity_label ? rolePart(f.entity_label) : sectionName(f.section),
                  })}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-5 border-t border-outline-variant pt-3">
        <button
          type="button"
          data-testid="receipt-summary-expand"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="text-sm font-semibold text-teal hover:underline"
        >
          {open ? t("summary.collapse") : t("summary.expand", { count: summary.facts_total })}
        </button>
        {open && (
          <div data-testid="receipt-summary-full" className="mt-3 space-y-4">
            {fullGroups.map((group) => (
              <div key={group.heading}>
                <h5 className="text-xs font-semibold text-on-surface">{group.heading}</h5>
                <ul className="mt-1 divide-y divide-dashed divide-outline-variant">
                  {group.facts.map((f) => (
                    <li
                      key={f.key}
                      data-testid="receipt-summary-row"
                      className="flex items-start justify-between gap-3 py-1.5 text-sm"
                    >
                      <span className="min-w-0 break-words text-on-surface">
                        {f.text}
                        {f.also_in.length > 0 && (
                          <span className="block text-xs text-on-surface-variant">
                            {f.also_in.map((s) =>
                              ENGAGEMENT.has(s) && f.entity_label
                                ? t("summary.alsoAt", { label: displayLabel(f.entity_label) })
                                : t("summary.alsoIn", { section: sectionName(s) }),
                            ).join(" · ")}
                          </span>
                        )}
                      </span>
                      {onFix && (
                        <button
                          type="button"
                          data-testid="receipt-summary-fix"
                          onClick={() => onFix(f)}
                          className="shrink-0 text-xs font-medium text-teal hover:underline"
                        >
                          {t("summary.fix")}
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
