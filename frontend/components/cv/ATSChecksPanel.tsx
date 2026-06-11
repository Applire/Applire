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

import { useTranslations } from "next-intl";

type ATSCheck = { id: string; status: "pass" | "fail"; details?: string | null };
export type ATSReport = {
  checks: ATSCheck[];
  keywords: { present: string[]; missing: string[] };
} | null;

export default function ATSChecksPanel({ report }: { report: ATSReport }) {
  const t = useTranslations("ats");

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

  // Strip trailing numeric index (e.g. "work-1" → "work", "education-2" → "education", "body-3" → "body")
  const labelKey = (id: string) => `checks.${id.replace(/-\d+$/, "")}`;

  return (
    <section
      data-testid="ats-panel"
      aria-label={t("title")}
      className="rounded-xl border border-outline-variant surface-glass p-4 space-y-3"
    >
      <h3 className="text-sm font-heading font-semibold text-on-surface">{t("title")}</h3>

      <ul className="space-y-1.5">
        {report.checks.map((c) => (
          <li
            key={c.id}
            data-testid={`ats-check-${c.id}`}
            className={`flex items-start gap-2 text-sm ${
              c.status === "pass" ? "text-on-surface" : "text-on-surface"
            }`}
          >
            <span
              aria-hidden="true"
              className={`mt-0.5 text-xs font-bold shrink-0 ${
                c.status === "pass" ? "text-success" : "text-critical"
              }`}
            >
              {c.status === "pass" ? "✓" : "✗"}
            </span>
            <span>
              {t(labelKey(c.id))}
              {c.status === "fail" && c.details ? (
                <span className="text-on-surface-variant"> — {c.details}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>

      {report.keywords.missing.length > 0 && (
        <p data-testid="ats-keywords-missing" className="text-sm text-on-surface-variant">
          {t("missingKeywords", { count: report.keywords.missing.length })}{" "}
          <span className="text-on-surface">{report.keywords.missing.join(", ")}</span>
        </p>
      )}

      <p data-testid="ats-keywords-coverage" className="text-sm text-on-surface-variant">
        {t("keywordCoverage", {
          present: report.keywords.present.length,
          total: report.keywords.present.length + report.keywords.missing.length,
        })}
      </p>
    </section>
  );
}
