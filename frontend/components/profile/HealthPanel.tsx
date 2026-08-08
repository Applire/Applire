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

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

// US160 (E033 / ADR-041 amended) — the deterministic /api/profile/health contract.
// "confirmation" (#333): an N-option ambiguity the reconciler parked for the
// human — resolved by the profile-review interview, not by a 2-value pick.
// "unit" (#382, PO decision 2026-08-08): a budget figure the vault holds but no
// document may state, because it carries no unit. Its own thread because it is
// not a mismatch — the value is exactly what the candidate said — and because
// Option A's condition is that the omission reaches the user.
export type HealthThread = "conflict" | "accuracy" | "confirmation" | "unit";
export type HealthSeverity = "info" | "review" | "critical";

export interface HealthIssue {
  id: string;
  thread: HealthThread;
  profile_mismatch_severity: HealthSeverity;
  summary: string;
  field_ref?: string | null;
  source_record_ref?: string | null;
}

export interface ProfileHealth {
  issues: HealthIssue[];
  // gaps: section-level breakdown (US104) — shown in the "Missing sections" label.
  // field_gaps: role-aware work-entry gaps the no-JD enrichment interview will ask
  // (US179) — gates and counts the enrich entry-point so the count equals the
  // questions the interview will actually ask (unified with backend CompletenessBlock).
  completeness: { score: number; gaps: string[]; field_gaps: string[] };
}

// review/info are non-blocking nudges; critical is shown prominently (ADR-040 §4).
const SEVERITY_BADGE: Record<HealthSeverity, "critical" | "warning" | "secondary"> = {
  critical: "critical",
  review: "warning",
  info: "secondary",
};

const SEVERITY_LABEL: Record<HealthSeverity, "severityCritical" | "severityReview" | "severityInfo"> = {
  critical: "severityCritical",
  review: "severityReview",
  info: "severityInfo",
};

const THREAD_LABEL: Record<
  HealthThread,
  "threadConflict" | "threadAccuracy" | "threadConfirmation" | "threadUnit"
> = {
  conflict: "threadConflict",
  accuracy: "threadAccuracy",
  confirmation: "threadConfirmation",
  unit: "threadUnit",
};

function IssueCard({
  issue,
  onResolve,
  prominent,
}: {
  issue: HealthIssue;
  onResolve: (issue: HealthIssue) => void;
  prominent?: boolean;
}) {
  const t = useTranslations("health");
  return (
    <div
      data-testid="health-issue"
      className={`rounded-lg border p-3 ${
        prominent ? "border-critical/30 bg-critical/5" : "border-gray-200 bg-white"
      }`}
    >
      <div className="flex items-center gap-2 mb-1">
        <Badge variant={SEVERITY_BADGE[issue.profile_mismatch_severity]}>
          {t(SEVERITY_LABEL[issue.profile_mismatch_severity])}
        </Badge>
        <span className="text-xs text-gray-500">{t(THREAD_LABEL[issue.thread])}</span>
      </div>
      {/* #382: a `unit` issue is a QUESTION put to the user, so it is asked in
          their language rather than shown as the backend's log-shaped summary.
          The other threads keep the summary — it quotes the two conflicting
          values verbatim, which no translation could reproduce. */}
      <p className="text-sm text-neutral-dark">
        {issue.thread === "unit"
          ? t("unitBudgetIssue", { entry: issue.source_record_ref ?? "" })
          : issue.summary}
      </p>
      <div className="mt-2 flex justify-end">
        <Button
          size="sm"
          variant="outline"
          data-testid="health-resolve"
          onClick={() => onResolve(issue)}
        >
          {t("resolve")}
        </Button>
      </div>
    </div>
  );
}

export function HealthPanel({
  health,
  onResolve,
  onImprove,
}: {
  health: ProfileHealth;
  onResolve: (issue: HealthIssue) => void;
  // US166 — launch the standalone Mode C (ADR-028) enrichment conversation in
  // context from the completeness block. Optional: growth, never blocking.
  onImprove?: () => void;
}) {
  const t = useTranslations("health");
  const [nudgeDismissed, setNudgeDismissed] = useState(false);

  const critical = health.issues.filter((i) => i.profile_mismatch_severity === "critical");
  const nudgeable = health.issues.filter((i) => i.profile_mismatch_severity !== "critical");
  const pct = Math.round((health.completeness.score ?? 0) * 100);

  return (
    <Card data-testid="health-panel" className="p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-heading text-base font-semibold text-neutral-dark">
          {t("title")}
        </h3>
        <span className="text-xs text-gray-500">{t("completeness", { pct })}</span>
      </div>

      {health.issues.length === 0 ? (
        <p className="text-sm text-gray-600">{t("allClear")}</p>
      ) : (
        <p className="text-sm text-gray-600 mb-3">
          {t("issuesSummary", { count: health.issues.length })}
        </p>
      )}

      {/* review/info nudge — dismissible, never blocks (nudge-not-gate, ADR-040 §4) */}
      {nudgeable.length > 0 && !nudgeDismissed && (
        <div
          data-testid="health-nudge"
          className="mb-3 flex items-center justify-between rounded-lg border border-warning/30 bg-warning/5 px-3 py-2"
        >
          <span className="text-sm text-neutral-dark">
            {t("nudge", { count: nudgeable.length })}
          </span>
          <Button
            size="sm"
            variant="ghost"
            data-testid="health-nudge-dismiss"
            onClick={() => setNudgeDismissed(true)}
          >
            {t("dismissNudge")}
          </Button>
        </div>
      )}

      {/* critical issues — always prominent, never dismissible */}
      {critical.length > 0 && (
        <div data-testid="health-critical" className="space-y-2 mb-3">
          <p className="text-sm font-medium text-critical">{t("criticalHeading")}</p>
          {critical.map((issue) => (
            <IssueCard key={issue.id} issue={issue} onResolve={onResolve} prominent />
          ))}
        </div>
      )}

      {nudgeable.length > 0 && (
        <div className="space-y-2">
          {nudgeable.map((issue) => (
            <IssueCard key={issue.id} issue={issue} onResolve={onResolve} />
          ))}
        </div>
      )}

      {(health.completeness.gaps.length > 0 || health.completeness.field_gaps.length > 0) && (
        <div className="mt-3 flex items-center justify-between gap-2">
          {health.completeness.gaps.length > 0 && (
            <p className="text-xs text-gray-500">
              {t("gapsLabel", { sections: health.completeness.gaps.join(", ") })}
            </p>
          )}
          {/* US179: gate the enrich entry-point on field_gaps (role-aware work-entry
              gaps the interview will actually ask) so the count equals the questions
              asked — not on section-level gaps which may differ. */}
          {onImprove && health.completeness.field_gaps.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              data-testid="health-improve"
              className="shrink-0"
              onClick={onImprove}
            >
              {t("improve")}
            </Button>
          )}
        </div>
      )}
    </Card>
  );
}
