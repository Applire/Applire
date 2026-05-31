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


import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { scoreColor, scoreBarClass, formatScore } from "@/lib/match-utils";

export interface JobMatchResult {
  job_id: string;
  role_title: string;
  company_name: string | null;
  berufsbild_code: string | null;
  berufsbild_label: string | null;
  llm_match_score: number | null;
  embedding_similarity: number | null;
  combined_score: number;
  gap_analysis_id: string | null;
}

interface JobCardProps {
  job: JobMatchResult;
  /** Ordered list of strength labels (top 3 shown). */
  strengths?: string[];
  /** Ordered list of critical gap labels (top 3 shown). */
  gaps?: string[];
}

/** Seniority level badge — extracted from role title heuristically if not separately provided. */
function SeniorityBadge({ roleTitle }: { roleTitle: string }) {
  const lower = roleTitle.toLowerCase();
  let level = "Mid";
  if (/(senior|lead|principal|staff|architect)/i.test(lower)) level = "Senior";
  else if (/(junior|jr\.?|entry|werkstudent|praktikant|trainee)/i.test(lower)) level = "Junior";
  else if (/(executive|director|vp |head of|cto|ceo|cfo|cpo)/i.test(lower)) level = "Executive";

  const variantMap: Record<string, "success" | "warning" | "outline" | "secondary"> = {
    Senior: "success",
    Executive: "in-progress" as "success",
    Junior: "outline",
    Mid: "secondary",
  };

  return (
    <Badge variant={variantMap[level] ?? "secondary"} data-testid="seniority-badge">
      {level}
    </Badge>
  );
}

export function JobCard({ job, strengths = [], gaps = [] }: JobCardProps) {
  const router = useRouter();
  const t = useTranslations("match");
  const color = scoreColor(job.combined_score);
  const barClass = scoreBarClass(job.combined_score);
  const scorePercent = Math.round(job.combined_score * 100);

  function handleRunGapAnalysis() {
    // Navigate to the main flow page pre-seeded with this job
    router.push(`/?job_id=${job.job_id}`);
  }

  return (
    <Card
      className="p-5 flex flex-col gap-4"
      data-testid="job-card"
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h3
            className="font-heading font-semibold text-neutral-dark truncate"
            data-testid="job-card-title"
          >
            {job.role_title}
          </h3>
          {job.company_name && (
            <p className="text-sm text-gray-500 mt-0.5" data-testid="job-card-company">
              {job.company_name}
            </p>
          )}
        </div>

        {/* Badges */}
        <div className="flex gap-2 flex-shrink-0 flex-wrap justify-end">
          <SeniorityBadge roleTitle={job.role_title} />
          {job.berufsbild_label && (
            <Badge variant="outline" data-testid="berufsbild-badge">
              {job.berufsbild_label}
            </Badge>
          )}
        </div>
      </div>

      {/* Combined score bar */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-gray-500">{t("matchScore")}</span>
          <span
            className={cn(
              "text-xs font-semibold",
              color === "success" && "text-success",
              color === "warning" && "text-warning",
              color === "critical" && "text-critical",
            )}
            data-testid="job-card-score"
          >
            {formatScore(job.combined_score)}
          </span>
        </div>
        <div
          className="h-2 w-full rounded-full bg-gray-200 overflow-hidden"
          data-testid="score-bar-track"
        >
          <div
            className={cn("h-full rounded-full transition-all duration-500 ease-out", barClass)}
            style={{ width: `${scorePercent}%` }}
            data-testid="score-bar-fill"
          />
        </div>
      </div>

      {/* Strengths + Gaps pills */}
      {(strengths.length > 0 || gaps.length > 0) && (
        <div className="flex flex-wrap gap-1.5">
          {strengths.slice(0, 3).map((s) => (
            <span
              key={s}
              className="inline-flex items-center px-2 py-0.5 rounded-full bg-success/10 text-success text-xs font-medium"
              data-testid="strength-pill"
            >
              {t("strengthPrefix", { label: s })}
            </span>
          ))}
          {gaps.slice(0, 3).map((g) => (
            <span
              key={g}
              className="inline-flex items-center px-2 py-0.5 rounded-full bg-critical/10 text-critical text-xs font-medium"
              data-testid="gap-pill"
            >
              {t("gapPrefix", { label: g })}
            </span>
          ))}
        </div>
      )}

      {/* CTA */}
      <div className="flex justify-end">
        <Button
          size="sm"
          variant="outline"
          onClick={handleRunGapAnalysis}
          data-testid="run-gap-analysis-btn"
        >
          {t("runGapAnalysis")}
        </Button>
      </div>
    </Card>
  );
}
