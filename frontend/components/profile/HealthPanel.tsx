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

import { useState, type ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { conflictSourceLabel, describeConflict } from "@/lib/conflict-display";
import type { DeliveryClass } from "@/lib/ats-report";

// US160 (E033 / ADR-041 amended) — the deterministic /api/profile/health contract.
// "confirmation" (#333): an N-option ambiguity the reconciler parked for the
// human — resolved by the profile-review interview, not by a 2-value pick.
// "unit" (#382, PO decision 2026-08-08): a budget figure the vault holds but no
// document may state, because it carries no unit. Its own thread because it is
// not a mismatch — the value is exactly what the candidate said — and because
// Option A's condition is that the omission reaches the user.
// `not_applied` (#684 / founder ruling V-6) — something the candidate submitted
// did not reach the vault, with the reason. Not a decision they owe: there is
// nothing to pick between, which is why the gaps page's popup stack filters on
// `conflict`/`confirmation` and deliberately excludes this thread.
export type HealthThread =
  | "conflict"
  | "accuracy"
  | "confirmation"
  | "unit"
  | "not_applied"
  // #764 — a transcribed skill duration no source states (the Oracle's
  // "laundered span"); fixed by one deterministic recompute, not a decision.
  | "span_provenance"
  // FS-5 (Hazelnut build 4) — a recorded skill binding that crosses
  // technical↔soft ("REST APIs" on "Mentoring"). Read-only; answered by the
  // pair's own acts (confirm / #717 separate), never a decision popup.
  | "suspect_binding";
export type HealthSeverity = "info" | "review" | "critical";

export interface HealthIssue {
  id: string;
  thread: HealthThread;
  profile_mismatch_severity: HealthSeverity;
  summary: string;
  field_ref?: string | null;
  source_record_ref?: string | null;
  // ADR-099 cl. 2/11 — delivery class + the one action's resolver key from the
  // backend's finding registry. Null/absent = unclassified (never read as "S").
  delivery_class?: DeliveryClass | null;
  resolver?: string | null;
  // #626 (conflict legibility) — structured fields, populated for `conflict`
  // issues only (every other thread leaves them undefined/null and renders
  // exactly as before). See `services/profile/health.py`'s `HealthIssue` for
  // the full contract, incl. why `existing_source` is always null today.
  entity_label?: string | null;
  section?: string | null;
  field?: string | null;
  existing_value_display?: string | null;
  incoming_value_display?: string | null;
  existing_source?: string | null;
  incoming_source?: string | null;
  // founder ruling V-7 — the pieces the `not_applied` sentence is composed
  // from, so it is written in the reader's language rather than shipped as the
  // server's English `summary`. Null on every other thread.
  not_applied_count?: number | null;
  not_applied_source?: string | null;
  not_applied_reasons?: string[] | null;
  not_applied_labels?: string[] | null;
  // #705 (founder UAT, 2026-09-15) — the FULL per-item receipt (never
  // capped), so the overlay can list every item grouped by section instead
  // of guessing from a 3-item-capped sentence. `label`/`reason` are raw keys
  // — see `groupNotAppliedItems` for how a reader composes them. `undefined`
  // for a record from a backend predating #705 (falls back to the old
  // single-summary rendering); `null` never sent by the backend but accepted
  // defensively.
  not_applied_items?: { section: string | null; label: string; reason: string }[] | null;
  // #764 — the pieces the `span_provenance` sentence is composed from (its
  // server `summary` is an English fallback and is never rendered). Null/absent
  // on every other thread.
  skill_id?: string | null;
  skill_name?: string | null;
  span_years?: number | null;
  recomputed_years?: number | null;
  // FS-5 — the name the document used for a `suspect_binding` pair; the
  // target's name is `skill_name`, its id `skill_id`.
  incoming_name?: string | null;
  // G-5 / #604 — a `confirmation` issue's question per language; the panel
  // renders the reader's own instead of the English `summary`.
  question_i18n?: Record<string, string> | null;
  // G-5 / ruling V-5 (c) — a `confirmation` the panel answers IN PLACE (the
  // `denial_scope` family): the ask's id and its options, plain and per
  // language. Null options = answered in the interview ("Beheben").
  confirmation_id?: string | null;
  confirmation_kind?: string | null;
  options?: string[] | null;
  options_i18n?: Record<string, string>[] | null;
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

/**
 * Founder ruling V-7 — the `not_applied` sentence, composed in the reader's
 * language from the pieces the backend sends.
 *
 * Every part is either data or a key: the item labels are natural keys the
 * candidate wrote themselves (an employer, a degree), the `source` is an
 * `EnrichmentRecord.source` key localised through the SAME `profile.sources.*`
 * dictionary the enrichment trail and the conflict rows use, and each reason is
 * a `health.notAppliedReason.*` key. A `professional_summary` item's label is a
 * language slot, mapped through `health.fieldLabel.summaryDe/summaryEn` — the
 * same words the dispute surface uses, so the hub and the dispute never call
 * one thing by two names.
 *
 * Falls back to the server's `summary` when the structured fields are absent
 * (a record from a backend that predates this), which is exactly what every
 * other thread does today.
 */
export function describeNotApplied(
  issue: HealthIssue,
  t: ReturnType<typeof useTranslations>,
  tProfile: ReturnType<typeof useTranslations>,
): string {
  const count = issue.not_applied_count;
  if (!count) return issue.summary;
  const isSummarySection = issue.field_ref === "professional_summary";
  const labels = (issue.not_applied_labels ?? []).map((label) =>
    isSummarySection
      ? t(label === "de" ? "fieldLabel.summaryDe" : "fieldLabel.summaryEn")
      : label,
  );
  const reasons = (issue.not_applied_reasons ?? [])
    .map((reason) =>
      t.has(`notAppliedReason.${reason}`) ? t(`notAppliedReason.${reason}`) : reason,
    )
    .join("; ");
  return t("notAppliedSummary", {
    count,
    source: conflictSourceLabel(tProfile, issue.not_applied_source),
    items: labels.join(", "),
    reasons,
  });
}


// Machine section key → `profile` namespace label key, covering every value
// `ImportNotApplied.section` can carry (the nine list-valued content sections
// plus `professional_summary`; `null`/`op_rejected`/`no_write` items have no
// section and group under a synthetic key handled by the caller).
const NOT_APPLIED_SECTION_LABEL_KEY: Record<string, string> = {
  professional_summary: "sectionSummary",
  work_experience: "sectionWorkExperience",
  education: "sectionEducation",
  skills: "sectionSkills",
  languages: "sectionLanguages",
  certifications: "sectionCertifications",
  projects: "sectionProjects",
  publications: "sectionPublications",
  volunteer_activities: "sectionVolunteer",
  signature_stories: "sectionSignatureStories",
};

// A section the map above does not know renders under its raw key rather
// than under a wrong human label — a mislabel would send the user to the
// wrong section, which is the #705 defect in a new coat.
function notAppliedSectionLabel(key: string, t: Translator, tProfile: Translator): string {
  if (!key) return t("notAppliedNoSection");
  const labelKey = NOT_APPLIED_SECTION_LABEL_KEY[key];
  return labelKey ? tProfile(labelKey) : key;
}

export interface NotAppliedGroup {
  // The machine section key, or `null` for a sectionless item (`op_rejected`/
  // `no_write` — a raw op or a statement turn, neither tied to a profile
  // section). The overlay only offers a "go to section" action when this is
  // non-null — there is nowhere to navigate for a `null` group.
  section: string | null;
  sectionLabel: string;
  labels: string[];
  reasonText: string;
}

/**
 * #705 (founder UAT, 2026-09-15) — group the FULL `not_applied_items` receipt
 * by section, so the overlay can say WHICH items were not carried over and
 * where to add them back, instead of the founder's reported experience: a
 * 3-item-capped sentence and one action that "just randomly moves to one of
 * the affected sections". Reuses the reason clauses `describeNotApplied`
 * already composes (`health.notAppliedReason.*`) and the same
 * `professional_summary` slot mapping.
 *
 * Returns `[]` when `issue.not_applied_items` is absent (a record from a
 * backend predating #705) — the caller keeps rendering the old single
 * summary + one action for that case.
 */
export function groupNotAppliedItems(
  issue: HealthIssue,
  t: Translator,
  tProfile: Translator,
): NotAppliedGroup[] {
  const items = issue.not_applied_items ?? [];
  const order: string[] = [];
  const groups = new Map<string, { labels: string[]; reasons: Set<string> }>();
  for (const item of items) {
    const key = item.section ?? "";
    if (!groups.has(key)) {
      groups.set(key, { labels: [], reasons: new Set() });
      order.push(key);
    }
    const group = groups.get(key)!;
    group.labels.push(
      item.section === "professional_summary"
        ? t(item.label === "de" ? "fieldLabel.summaryDe" : "fieldLabel.summaryEn")
        : item.label,
    );
    group.reasons.add(item.reason);
  }
  return order.map((key) => {
    const group = groups.get(key)!;
    return {
      section: key || null,
      sectionLabel: notAppliedSectionLabel(key, t, tProfile),
      labels: group.labels,
      reasonText: Array.from(group.reasons)
        .map((reason) =>
          t.has(`notAppliedReason.${reason}`) ? t(`notAppliedReason.${reason}`) : reason,
        )
        .join("; "),
    };
  });
}

// `span_provenance` is absent on purpose: its label lives in the
// `profileHealth.launderedSpan` namespace (see IssueCard), not in `health`.
const THREAD_LABEL: Record<
  Exclude<HealthThread, "span_provenance" | "suspect_binding">,
  | "threadConflict"
  | "threadAccuracy"
  | "threadConfirmation"
  | "threadUnit"
  | "threadNotApplied"
> = {
  conflict: "threadConflict",
  accuracy: "threadAccuracy",
  confirmation: "threadConfirmation",
  unit: "threadUnit",
  not_applied: "threadNotApplied",
};

export type Translator = ReturnType<typeof useTranslations>;

/**
 * #626 — compose a `conflict` issue into a localized heading + two
 * provenance-labeled value rows, so no surface shows the backend's raw
 * `work_experience.end_date: '…' vs '…'` string (the reported defect: it named
 * the field but never WHICH work entry). `null` for a non-conflict issue — the
 * caller falls back to its own thread-specific rendering.
 *
 * #604 — the composition itself now lives in `lib/conflict-display.ts`, shared
 * with the live interview's `ConflictCard`, which showed the same raw shape via
 * a second mechanism. This is the `HealthIssue`-shaped adapter onto it.
 */
export function describeConflictIssue(
  issue: HealthIssue,
  t: Translator,
  tProfile: Translator,
): { heading: string; existingRow: string; incomingRow: string } | null {
  if (issue.thread !== "conflict") return null;
  return describeConflict(issue, t, tProfile);
}

export type AnswerBinding = (issue: HealthIssue, same: boolean) => Promise<boolean>;
/** V-5 (c) — answer a parked confirmation with one of its plain options;
 *  resolves to false when the act failed. */
export type AnswerConfirmation = (issue: HealthIssue, option: string) => Promise<boolean>;

function IssueCard({
  issue,
  onResolve,
  onRecomputeSpan,
  onAnswerBinding,
  onAnswerConfirmation,
  prominent,
}: {
  issue: HealthIssue;
  onResolve: (issue: HealthIssue) => void;
  onRecomputeSpan?: (issue: HealthIssue) => Promise<void> | void;
  onAnswerBinding?: AnswerBinding;
  onAnswerConfirmation?: AnswerConfirmation;
  prominent?: boolean;
}) {
  const t = useTranslations("health");
  const tProfile = useTranslations("profile");
  const tSpan = useTranslations("profileHealth.launderedSpan");
  const [recomputing, setRecomputing] = useState(false);
  const locale = useLocale();
  const [answering, setAnswering] = useState(false);
  const [answerFailed, setAnswerFailed] = useState(false);
  const conflict = describeConflictIssue(issue, t, tProfile);
  const isSpan = issue.thread === "span_provenance";
  // The one-click recompute needs a handler; without one the card degrades to
  // the ordinary Resolve button (and the server summary is not the sentence).
  const canRecompute = isSpan && !!onRecomputeSpan;
  // FS-5 — a suspect binding is ONE question with the pair's two acts; without
  // a handler it degrades to the ordinary Resolve button.
  const isBinding = issue.thread === "suspect_binding";
  const canAnswerBinding = isBinding && !!onAnswerBinding;
  // V-5 (c) — a `denial_scope` ask is answered with its own options; the
  // answer IS the act (no second dialog). Without a handler: "Beheben".
  const confirmationOptions =
    issue.thread === "confirmation" && issue.options && issue.options.length > 0 ? issue.options : null;
  const canAnswerConfirmation = !!confirmationOptions && !!onAnswerConfirmation;
  const optionLabel = (i: number) =>
    issue.options_i18n?.[i]?.[locale] ?? confirmationOptions?.[i] ?? "";
  const handleConfirmation = async (option: string) => {
    if (!onAnswerConfirmation || answering) return;
    setAnswering(true);
    setAnswerFailed(false);
    try {
      if (!(await onAnswerConfirmation(issue, option))) setAnswerFailed(true);
    } finally {
      setAnswering(false);
    }
  };
  const handleBinding = async (same: boolean) => {
    if (!onAnswerBinding || answering) return;
    setAnswering(true);
    setAnswerFailed(false);
    try {
      if (!(await onAnswerBinding(issue, same))) setAnswerFailed(true);
    } finally {
      setAnswering(false);
    }
  };
  const bold = (chunks: ReactNode) => <b className="font-semibold">{chunks}</b>;
  const handleRecompute = async () => {
    if (!onRecomputeSpan || recomputing) return;
    setRecomputing(true);
    try {
      await onRecomputeSpan(issue);
    } finally {
      setRecomputing(false);
    }
  };
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
        <span className="text-xs text-gray-500">
          {issue.thread === "span_provenance"
            ? tSpan("thread")
            : issue.thread === "suspect_binding"
              ? t("threadSuspectBinding")
              : t(THREAD_LABEL[issue.thread])}
        </span>
      </div>
      {/* #626: a `conflict` issue composes a localized heading (naming the
          entry the dispute belongs to) + two provenance-labeled value rows,
          instead of the backend's raw "section.field: 'x' vs 'y'" summary.
          #382: a `unit` issue is a QUESTION put to the user, asked in their own
          language. V-7: a `not_applied` issue composes the same way — its
          screenshot showed a raw-English sentence sitting directly beneath a
          fully-German conflict card, which `applire-i18n` makes a defect rather
          than a rough edge.

          Adversarial finding (2026-09-09, WP-adv-vault): this comment used to
          claim `accuracy` was the ONE thread still falling through. It is not
          — `confirmation` does too (`services/profile/health.py::
          _confirmation_issue` sets `summary=confirmation.question`, always the
          English plain field per #669's `_build`), and V-7's own screenshot
          pass never rendered a `confirmation` issue to catch it. A real DE
          screenshot of the profile page with a parked confirmation shows the
          identical raw-English pattern the V-7 fix was written for. Both
          threads are on the Frontend collector (#604) now, not just one —
          fixing `confirmation` needs a backend change too
          (`HealthIssue` carries no `question_i18n`/`options_i18n` for it, only
          `not_applied` got that treatment), which is why it is deferred
          alongside `accuracy` rather than built here.
          G-5 (Hazelnut build 4): `confirmation` now carries `question_i18n`
          and renders the reader's language; `accuracy` still falls through. */}
      {conflict ? (
        <div data-testid="health-issue-conflict">
          <p className="text-sm font-medium text-neutral-dark">{conflict.heading}</p>
          <p className="text-xs text-on-surface-variant mt-1">{conflict.existingRow}</p>
          <p className="text-xs text-on-surface-variant">{conflict.incomingRow}</p>
        </div>
      ) : isSpan ? (
        <p className="text-sm text-neutral-dark" data-testid="health-issue-span-provenance">
          {tSpan("summary", {
            skill: issue.skill_name ?? issue.source_record_ref ?? "",
            years: issue.span_years ?? 0,
            recomputed: issue.recomputed_years ?? 0,
          })}
        </p>
      ) : isBinding ? (
        <div data-testid="health-issue-suspect-binding">
          <p className="text-sm text-neutral-dark">
            {t.rich("suspectBindingQuestion", {
              incoming: issue.incoming_name ?? "",
              existing: issue.skill_name ?? "",
              b: bold,
            })}
          </p>
          <p className="text-xs text-on-surface-variant mt-1">{t("suspectBindingNote")}</p>
          {answerFailed && (
            <p className="text-xs text-critical mt-1" role="alert">
              {t("suspectBindingFailed")}
            </p>
          )}
        </div>
      ) : (
        <p className="text-sm text-neutral-dark" data-testid="health-issue-summary">
          {issue.thread === "unit"
            ? t("unitBudgetIssue", { entry: issue.source_record_ref ?? "" })
            : issue.thread === "not_applied"
              ? describeNotApplied(issue, t, tProfile)
              : issue.thread === "confirmation" && issue.question_i18n?.[locale]
                ? issue.question_i18n[locale]
                : issue.summary}
        </p>
      )}
      {canAnswerConfirmation && answerFailed && (
        <p className="text-xs text-critical mt-1" role="alert">
          {t("suspectBindingFailed")}
        </p>
      )}
      <div className="mt-2 flex flex-wrap justify-end gap-2">
        {canAnswerConfirmation && confirmationOptions ? (
          confirmationOptions.map((option, i) => (
            <Button
              key={option}
              size="sm"
              variant="outline"
              className="h-auto min-h-8 whitespace-normal text-left"
              data-testid="health-confirmation-option"
              disabled={answering}
              onClick={() => handleConfirmation(option)}
            >
              {optionLabel(i)}
            </Button>
          ))
        ) : canAnswerBinding ? (
          <>
            <Button
              size="sm"
              variant="outline"
              data-testid="health-binding-same"
              disabled={answering}
              onClick={() => handleBinding(true)}
            >
              {t("suspectBindingSame")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              data-testid="health-binding-separate"
              disabled={answering}
              onClick={() => handleBinding(false)}
            >
              {t("suspectBindingSeparate")}
            </Button>
          </>
        ) : canRecompute ? (
          <Button
            size="sm"
            variant="outline"
            data-testid="health-recompute-span"
            disabled={recomputing}
            onClick={handleRecompute}
          >
            {recomputing ? tSpan("recomputing") : tSpan("recompute")}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            data-testid="health-resolve"
            onClick={() => onResolve(issue)}
          >
            {t("resolve")}
          </Button>
        )}
      </div>
    </div>
  );
}

export function HealthPanel({
  health,
  onResolve,
  onImprove,
  onRecomputeSpan,
  onAnswerBinding,
  onAnswerConfirmation,
}: {
  health: ProfileHealth;
  onResolve: (issue: HealthIssue) => void;
  // #764 — one-click recompute of a `span_provenance` issue. Optional: without
  // it that thread falls back to the ordinary Resolve button.
  onRecomputeSpan?: (issue: HealthIssue) => Promise<void> | void;
  // US166 — launch the standalone Mode C (ADR-028) enrichment conversation in
  // context from the completeness block. Optional: growth, never blocking.
  onImprove?: () => void;
  // FS-5 — answer a `suspect_binding` pair (true = same, false = separate);
  // resolves to false when the act failed. Optional: without it that thread
  // falls back to the ordinary Resolve button.
  onAnswerBinding?: AnswerBinding;
  // V-5 (c) — answer a `denial_scope` ask in place. Optional: without it the
  // ask falls back to the ordinary Resolve button.
  onAnswerConfirmation?: AnswerConfirmation;
}) {
  const t = useTranslations("health");
  const tSection = useTranslations("review.section");
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
            <IssueCard
              key={issue.id}
              issue={issue}
              onResolve={onResolve}
              onRecomputeSpan={onRecomputeSpan}
              onAnswerBinding={onAnswerBinding}
              onAnswerConfirmation={onAnswerConfirmation}
              prominent
            />
          ))}
        </div>
      )}

      {nudgeable.length > 0 && (
        <div className="space-y-2">
          {nudgeable.map((issue) => (
            <IssueCard
              key={issue.id}
              issue={issue}
              onResolve={onResolve}
              onRecomputeSpan={onRecomputeSpan}
              onAnswerBinding={onAnswerBinding}
              onAnswerConfirmation={onAnswerConfirmation}
            />
          ))}
        </div>
      )}

      {(health.completeness.gaps.length > 0 || health.completeness.field_gaps.length > 0) && (
        <div className="mt-3 flex items-center justify-between gap-2">
          {health.completeness.gaps.length > 0 && (
            <p className="text-xs text-gray-500">
              {t("gapsLabel", {
                // Section keys in the reader's words, never the raw key.
                sections: health.completeness.gaps
                  .map((g) => (tSection.has(g) ? tSection(g) : g))
                  .join(", "),
              })}
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
