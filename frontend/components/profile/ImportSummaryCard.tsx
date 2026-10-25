// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * R2-8 (9) (Hazelnut build 4, ruling V-2 = A, mock V-2) — "See what was
 * combined" lands HERE (`/profile#import-summary`), not on the engineer log:
 * the LAST import in the user's terms, read from the stored record by one
 * backend producer (`GET /api/profile/import-summary`). What it added, grouped
 * per section; each pair it combined with an existing entry as one question
 * (FS-6, ruling V-4 = A); what it kept apart; what it held back as a
 * restatement. The technical log (the full trail) stays, folded, as
 * `children`. With no import in the trail the log renders as before.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { RecognisedMatches } from "@/components/profile/RecognisedMatches";
import { RestatedBullets } from "@/components/profile/RestatedBullets";
import { fetchImportSummary, type ImportSummary } from "@/lib/api/importSummary";

export const IMPORT_SUMMARY_ANCHOR = "import-summary";

export function ImportSummaryCard({
  reloadKey,
  onChanged,
  children,
}: {
  /** Changes whenever the profile was reloaded (re-fetch the summary). */
  reloadKey?: unknown;
  /** A pair answer or "Trotzdem übernehmen" changed the profile. */
  onChanged?: () => void;
  /** The technical log, shown folded. */
  children?: ReactNode;
}) {
  const t = useTranslations("importSummary");
  const tSection = useTranslations("review.section");
  const locale = useLocale();
  const [summary, setSummary] = useState<ImportSummary | null>(null);
  const [failed, setFailed] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  const load = useCallback(() => {
    fetchImportSummary()
      .then((s) => {
        setSummary(s);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    load();
  }, [load, reloadKey]);

  // The card renders after an async load, so the browser's own jump to the
  // hash has already happened — bring it into view once it exists.
  useEffect(() => {
    if (!summary?.record_id || typeof window === "undefined") return;
    const jump = () => {
      if (window.location.hash === `#${IMPORT_SUMMARY_ANCHOR}`) {
        ref.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
      }
    };
    jump();
    window.addEventListener("hashchange", jump);
    return () => window.removeEventListener("hashchange", jump);
  }, [summary?.record_id]);

  if (!summary?.record_id) {
    return (
      <>
        {failed && (
          <p role="alert" className="mt-6 text-sm text-red-700">
            {t("loadFailed")}
          </p>
        )}
        {children}
      </>
    );
  }

  const date = summary.timestamp
    ? new Date(summary.timestamp).toLocaleDateString(locale === "de" ? "de-DE" : "en-GB")
    : "";
  const matchedCount = summary.matched.filter((m) => !m.undone_at).length;
  const sectionLabel = (section: string) => (tSection.has(section) ? tSection(section) : tSection("*"));
  const changed = () => {
    load();
    onChanged?.();
  };

  return (
    <Card
      id={IMPORT_SUMMARY_ANCHOR}
      ref={ref}
      data-testid="import-summary"
      className="mt-6 scroll-mt-24 p-4"
    >
      <h3 className="font-heading text-base font-semibold text-neutral-dark">
        {summary.file_name
          ? t("lastImportTitle", { date, file: summary.file_name })
          : t("lastImportTitleNoFile", { date })}
      </h3>
      <p data-testid="import-summary-lead" className="mt-1 text-sm text-on-surface-variant">
        {t("lastImportLead", { added: summary.added_total, matched: matchedCount })}
      </p>

      {summary.groups.length > 0 && (
        <ul data-testid="import-summary-groups" className="mt-3 divide-y divide-outline-variant rounded-lg border border-outline-variant">
          {summary.groups.map((g) => (
            <li key={g.section} className="flex flex-wrap gap-x-3 gap-y-1 px-3 py-2 text-sm">
              <span className="min-w-[120px] font-semibold text-on-surface">{sectionLabel(g.section)}</span>
              <span className="text-on-surface-variant">{t("groupCount", { count: g.count })}</span>
              <span className="basis-full text-[13px] text-on-surface-variant sm:basis-auto sm:flex-1">
                {g.entities.length > 0
                  ? g.entities.map((e) => `${e.label}: ${e.count}`).join(" · ")
                  : [...g.preview, ...(g.more > 0 ? [`+${g.more}`] : [])].join(", ")}
              </span>
            </li>
          ))}
        </ul>
      )}

      {summary.not_applied_total > 0 && (
        <p data-testid="import-summary-not-applied" className="mt-2 text-[13px] text-amber-800">
          {t("notAppliedNote", { count: summary.not_applied_total })}
        </p>
      )}

      <RecognisedMatches matched={summary.matched} keptSeparate={summary.kept_separate} onSeparated={changed} />
      <RestatedBullets restated={summary.restated} onApplied={changed} />

      {children && (
        <details data-testid="import-tech-log" className="mt-4">
          <summary className="cursor-pointer text-sm text-primary">{t("techLog")}</summary>
          {children}
        </details>
      )}
    </Card>
  );
}
