// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

// frontend/components/cv/ATSChecksPanel.tsx
"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";

type ATSCheck = { id: string; status: "pass" | "fail"; details?: string | null };
export type ATSReport = {
  checks: ATSCheck[];
  keywords: {
    present: string[];
    missing: string[];
    // US203 (ADR-048): a missing keyword the candidate HAS per the Keyword Ledger
    // (a surfacing miss — fixable) vs one they genuinely lack (an honest gap, never
    // something to fabricate). Optional for back-compat with legacy reports.
    missing_claimable?: string[];
    missing_honest_gap?: string[];
  };
} | null;

// Strip trailing numeric index (e.g. "work-1" → "work", "education-2" → "education", "body-3" → "body")
const baseId = (id: string) => id.replace(/-\d+$/, "");

type CheckGroup = { base: string; checks: ATSCheck[] };

function groupChecks(checks: ATSCheck[]): CheckGroup[] {
  const byBase = new Map<string, CheckGroup>();
  const groups: CheckGroup[] = [];
  for (const c of checks) {
    const b = baseId(c.id);
    let g = byBase.get(b);
    if (!g) {
      g = { base: b, checks: [] };
      byBase.set(b, g);
      groups.push(g);
    }
    g.checks.push(c);
  }
  return groups;
}

// Keyword-coverage ring — visualises the present/total COUNT (a fact from the
// report), never an aggregate quality score (ADR-035/ADR-039: no synthetic scores).
function KeywordRing({ present, total, label }: { present: number; total: number; label: string }) {
  const radius = 20;
  const circumference = 2 * Math.PI * radius;
  const fraction = total > 0 ? present / total : 1;
  return (
    <svg width="52" height="52" viewBox="0 0 52 52" role="img" aria-label={label}>
      <circle cx="26" cy="26" r={radius} fill="none" stroke="#e5e7eb" strokeWidth="5" />
      <circle
        cx="26"
        cy="26"
        r={radius}
        fill="none"
        stroke="#C9A84C"
        strokeWidth="5"
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={circumference * (1 - fraction)}
        transform="rotate(-90 26 26)"
      />
      <text
        x="26"
        y="30"
        textAnchor="middle"
        fontSize="12"
        fontWeight="700"
        fill="#1B4F72"
        fontFamily="Poppins, sans-serif"
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- numeric fraction separator */}
        {present}/{total}
      </text>
    </svg>
  );
}

export default function ATSChecksPanel({ report }: { report: ATSReport }) {
  const t = useTranslations("ats");
  const tCommon = useTranslations("common");
  const [open, setOpen] = useState(false);

  // Close the drawer on Escape
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!report) {
    return (
      <div
        data-testid="ats-unavailable"
        className="rounded-xl border border-outline-variant surface-glass p-4 text-sm text-on-surface-variant"
      >
        {t("unavailable")}
      </div>
    );
  }

  const labelKey = (id: string) => `checks.${baseId(id)}`;
  const failed = report.checks.filter((c) => c.status === "fail");
  const present = report.keywords.present.length;
  const total = present + report.keywords.missing.length;
  const coverageLabel = t("keywordCoverage", { present, total });

  // US203: split missing keywords into claimable (held but absent — fixable by surfacing)
  // vs honest gap (not in the profile). Legacy reports without the buckets fall back to
  // showing the flat missing list as honest gaps so nothing regresses.
  // A report is "bucketed" once the backend annotated it (US203). Legacy reports lack the
  // fields entirely and fall back to a single flat missing line.
  const isBucketed = report.keywords.missing_claimable !== undefined;
  const missingClaimable = report.keywords.missing_claimable ?? [];
  const missingHonestGap = report.keywords.missing_honest_gap ?? [];

  return (
    <>
      <section
        data-testid="ats-panel"
        aria-label={t("title")}
        className="rounded-xl border border-outline-variant surface-glass px-4 py-2.5"
      >
        <div className="flex items-center gap-4">
          <div data-testid="ats-keywords-coverage" className="shrink-0" title={coverageLabel}>
            <KeywordRing present={present} total={total} label={coverageLabel} />
          </div>

          <div className="min-w-0 flex-1 space-y-0.5">
            <p
              data-testid="ats-structure-status"
              className={`flex items-center gap-1.5 text-sm font-medium ${
                failed.length === 0 ? "text-on-surface" : "text-critical"
              }`}
            >
              <span
                aria-hidden="true"
                className={`text-xs font-bold ${failed.length === 0 ? "text-success" : "text-critical"}`}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail glyphs */}
                {failed.length === 0 ? "✓" : "✗"}
              </span>
              {failed.length === 0 ? t("structureOk") : t("structureIssues", { count: failed.length })}
            </p>
            {missingClaimable.length > 0 && (
              <p
                data-testid="ats-keywords-missing-claimable"
                className="text-xs text-on-surface-variant"
              >
                {t("missingClaimable", { count: missingClaimable.length })}
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                {" "}
                <span className="text-on-surface">{missingClaimable.join(", ")}</span>
              </p>
            )}
            {missingHonestGap.length > 0 && (
              <p
                data-testid="ats-keywords-missing-honest-gap"
                className="text-xs text-on-surface-variant"
              >
                {t("missingHonestGap", { count: missingHonestGap.length })}
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                {" "}
                <span className="text-on-surface">{missingHonestGap.join(", ")}</span>
              </p>
            )}
            {/* Back-compat sentinel: a legacy report (no buckets) still renders a flat line */}
            {!isBucketed && report.keywords.missing.length > 0 && (
                <p
                  data-testid="ats-keywords-missing"
                  className="truncate text-xs text-on-surface-variant"
                >
                  {t("missingKeywords", { count: report.keywords.missing.length })}
                  {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                  {" "}
                  <span className="text-on-surface">{report.keywords.missing.join(", ")}</span>
                </p>
              )}
          </div>

          <Button
            data-testid="ats-details-button"
            size="sm"
            variant="outline"
            className="shrink-0"
            onClick={() => setOpen(true)}
          >
            {t("detailsButton")}
          </Button>
        </div>

        {/* Failures stay loud: rendered inline on the compact card, no interaction needed */}
        {failed.length > 0 && (
          <ul className="mt-2 space-y-1 border-t border-outline-variant pt-2">
            {failed.map((c) => (
              <li
                key={c.id}
                data-testid={`ats-check-${c.id}`}
                className="flex items-start gap-2 text-sm text-on-surface"
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative fail glyph */}
                <span aria-hidden="true" className="mt-0.5 shrink-0 text-xs font-bold text-critical">✗</span>
                <span>
                  {t(labelKey(c.id))}
                  {c.details ? (
                    <span className="text-on-surface-variant">
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative em-dash separator */}
                      {" — "}
                      {c.details}
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {open && (
        <>
          <button
            type="button"
            data-testid="ats-drawer-backdrop"
            aria-label={tCommon("close")}
            className="fixed inset-0 z-40 bg-black/30"
            onClick={() => setOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label={t("title")}
            data-testid="ats-drawer"
            className="fixed inset-y-0 right-0 z-50 w-full max-w-[380px] overflow-y-auto border-l border-outline-variant bg-white p-5 shadow-xl"
          >
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-sm font-heading font-semibold text-on-surface">{t("title")}</h3>
              <button
                type="button"
                data-testid="ats-drawer-close"
                aria-label={tCommon("close")}
                className="text-lg leading-none text-on-surface-variant hover:text-on-surface"
                onClick={() => setOpen(false)}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative close glyph */}
                <span aria-hidden="true">×</span>
              </button>
            </div>

            <ul className="space-y-1.5">
              {groupChecks(report.checks).map((g) => {
                const passed = g.checks.filter((c) => c.status === "pass").length;
                const groupOk = passed === g.checks.length;
                const failing = g.checks.filter((c) => c.status === "fail");
                return (
                  <li
                    key={g.base}
                    data-testid={`ats-drawer-check-${g.base}`}
                    className="flex items-start gap-2 text-sm text-on-surface"
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-0.5 shrink-0 text-xs font-bold ${groupOk ? "text-success" : "text-critical"}`}
                    >
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative pass/fail glyphs */}
                      {groupOk ? "✓" : "✗"}
                    </span>
                    <span>
                      {t(`checks.${g.base}`)}
                      {g.checks.length > 1 && (
                        <span className="text-on-surface-variant">
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space before count */}
                          {" "}
                          {t("groupCount", { passed, total: g.checks.length })}
                        </span>
                      )}
                      {failing.map((c) =>
                        c.details ? (
                          <span key={c.id} className="block text-xs text-on-surface-variant">
                            {c.details}
                          </span>
                        ) : null,
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>

            <div className="mt-4 space-y-2 border-t border-outline-variant pt-3">
              <p data-testid="ats-drawer-coverage" className="text-sm text-on-surface-variant">
                {coverageLabel}
              </p>
              {isBucketed ? (
                <>
                  {missingClaimable.length > 0 && (
                    <p
                      data-testid="ats-drawer-missing-claimable"
                      className="text-sm text-on-surface-variant"
                    >
                      {t("missingClaimable", { count: missingClaimable.length })}
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                      {" "}
                      <span className="text-on-surface">{missingClaimable.join(", ")}</span>
                    </p>
                  )}
                  {missingHonestGap.length > 0 && (
                    <p
                      data-testid="ats-drawer-missing-honest-gap"
                      className="text-sm text-on-surface-variant"
                    >
                      {t("missingHonestGap", { count: missingHonestGap.length })}
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                      {" "}
                      <span className="text-on-surface">{missingHonestGap.join(", ")}</span>
                    </p>
                  )}
                </>
              ) : (
                report.keywords.missing.length > 0 && (
                  <p data-testid="ats-drawer-missing" className="text-sm text-on-surface-variant">
                    {t("missingKeywords", { count: report.keywords.missing.length })}
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- space between label and keyword list */}
                    {" "}
                    <span className="text-on-surface">{report.keywords.missing.join(", ")}</span>
                  </p>
                )
              )}
            </div>
          </aside>
        </>
      )}
    </>
  );
}
