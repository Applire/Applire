"use client";

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

import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";

interface JobEchoCardProps {
  companyName?: string | null;
  roleTitle?: string | null;
  requiredSkills: string[];
  niceToHaveSkills: string[];
}

/**
 * "What we read from the job ad" echo (US158, FMEA JF-M-4.3/4.4).
 *
 * Surfaces the company, role title and extracted requirements *before* the
 * interview, so the user can catch a wrong/partial JD paste or a missed/invented
 * requirement. When the title is empty (a title-less but valid JD, FMEA 4.5),
 * a hint is shown instead of a blank role.
 */
export function JobEchoCard({
  companyName,
  roleTitle,
  requiredSkills,
  niceToHaveSkills,
}: JobEchoCardProps) {
  const t = useTranslations("gaps");
  const total = requiredSkills.length + niceToHaveSkills.length;

  return (
    <Card data-testid="job-echo-card" className="p-5 mb-8">
      <h3 className="font-heading text-base font-bold text-neutral-dark mb-1">
        {t("jdEchoTitle")}
      </h3>
      <p className="text-sm text-on-surface-variant mb-4">{t("jdEchoSubtitle")}</p>

      <div className="flex flex-wrap gap-x-10 gap-y-3 mb-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-on-surface-variant">{t("jdEchoRole")}</p>
          <p data-testid="job-echo-role" className="text-sm font-medium text-neutral-dark">
            {roleTitle && roleTitle.trim() ? roleTitle : t("jdEchoNoTitle")}
          </p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-on-surface-variant">{t("jdEchoCompany")}</p>
          <p data-testid="job-echo-company" className="text-sm font-medium text-neutral-dark">
            {companyName && companyName.trim() ? companyName : t("jdEchoUnknown")}
          </p>
        </div>
      </div>

      <p className="text-xs uppercase tracking-wide text-on-surface-variant mb-2">
        {t("jdEchoRequirements", { count: total })}
      </p>
      <div data-testid="job-echo-requirements" className="flex flex-wrap gap-1.5">
        {requiredSkills.map((s) => (
          <span
            key={`req-${s}`}
            className="rounded-md bg-surface-container px-2 py-0.5 text-xs text-on-surface"
          >
            {s}
          </span>
        ))}
        {niceToHaveSkills.map((s) => (
          <span
            key={`nice-${s}`}
            className="rounded-md bg-surface-container px-2 py-0.5 text-xs text-on-surface-variant"
          >
            {s}
          </span>
        ))}
      </div>
    </Card>
  );
}
