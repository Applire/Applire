"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";
import { ChevronDown, ChevronRight, LayoutList, ListChecks } from "lucide-react";
import { localizedMessage } from "@/components/cv/CriticAdvisoryPanel";
import type { GapHintItem } from "@/components/cv/ContentTab";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { baseId, usesLocalizedDetail, type ATSCheck, type ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import {
  buildReviewGroups,
  verdictState,
  type ReviewGroup,
  type ReviewItem,
  type ReviewProducer,
} from "@/lib/review-groups";
import {
  markDocumentWalked,
  resolveReviewMode,
  type ResolvedReviewMode,
  type ReviewDocumentKind,
  type ReviewModePreference,
} from "@/lib/review-walked";

/**
 * The document review surface (arc42 §5.3.29, ADR-081) — E058 / US300–US302.
 *
 * One panel, four groups, one verdict sentence, two modes. It READS the four
 * producers' persisted payloads through `lib/review-groups.ts` and renders
 * them; it never merges, recomputes, deduplicates or reconciles across
 * producers beyond the single stated group-1 carve-out that lives in that
 * module (ADR-081 cl. 2 / `SF-REVIEW.3`).
 *
 * Three invariants are load-bearing and are each gated by their own test:
 *
 * - **cl. 4** — the verdict sentence's number is the length of the very array
 *   group 1 is rendered from. There is deliberately no second expression that
 *   could compute it differently.
 * - **cl. 6** — every non-zero group renders its count in BOTH modes; a group
 *   collapses to a single line only at count zero; guided always exposes the
 *   group-1 badge and the one-click switch to overview. Gated by one test PER
 *   MODE — a shared assertion passes on the mode it was written against
 *   (`SF-REVIEW.2`, `JF-F-K.2`).
 * - **cl. 9** — a producer that did not run renders as *unknown*, never as 0,
 *   and never inside the passed-checks collapse (`SF-REVIEW.4`, `JF-F-K.4`).
 *
 * Group 2 offers no action, by decision, not by omission: ADR-076 bars a new
 * deterministic post-review editing pass, and what this group shows is the
 * residue AFTER the coverage loop already tried (ADR-081 cl. 3 / US302). A fact
 * pin is not offered as a remedy either — ADR-077 cl. 2 forbids any audit path
 * branching on pinned-ness, so pins live on the editing tab and nothing here
 * reads them.
 */

const GROUP_TITLE_KEY: Record<1 | 2 | 3 | 4, string> = {
  1: "group1Title",
  2: "group2Title",
  3: "group3Title",
  4: "group4Title",
};

const PRODUCER_LABEL_KEY: Record<ReviewProducer, string> = {
  ats: "producerAts",
  oracle: "producerOracle",
  critic: "producerCritic",
  clusters: "producerClusters",
};

const KIND_LABEL_KEY: Record<ReviewItem["kind"], string> = {
  term: "kindTerm",
  cluster: "kindCluster",
  claim: "kindClaim",
  check: "kindCheck",
  advisory: "kindAdvisory",
};

const SEVERITY_DOT: Record<ReviewItem["severity"], string> = {
  critical: "bg-critical",
  warning: "bg-warning",
  info: "bg-primary",
  neutral: "bg-outline-variant",
};

export interface ReviewSurfaceProps {
  documentKind: ReviewDocumentKind;
  /** The GENERATED-DOCUMENT id — ADR-081 cl. 5a keys the walked bit on it. */
  documentId: string | null;
  atsReport: ATSReport;
  truthReport: TruthfulnessReport;
  criticReport: OutcomeCriticReport;
  /** `null` = never loaded → group 3's cluster half is *unknown*, not zero. */
  gapClusters: GapHintItem[] | null;
  /**
   * `false` on the cover letter: §5.3.26's clusters are computed against the
   * CV, so the producer does not apply and is removed from groups 2 and 3
   * rather than being reported as empty (a lie) or unknown (a different lie).
   */
  hasClusterProducer?: boolean;
  /** The stored `user_settings.review_mode`. */
  modePreference: ReviewModePreference;
  /** Existing handler for a gap cluster (routes to the editor or to /profile). */
  onResolveCluster?: (gapId: string) => void;
  /**
   * Anything the page wants under the four groups that is NOT one of ADR-081's
   * four producers — today only the cover letter's unasked-requirements panel.
   * Rendered after the groups so it can never be mistaken for one of them.
   */
  children?: ReactNode;
}

export function ReviewSurface({
  documentKind,
  documentId,
  atsReport,
  truthReport,
  criticReport,
  gapClusters,
  hasClusterProducer = true,
  modePreference,
  onResolveCluster,
  children,
}: ReviewSurfaceProps) {
  const t = useTranslations("documentReview");
  const tAts = useTranslations("ats");
  const locale = useLocale();

  const groups = useMemo(
    () =>
      buildReviewGroups({
        atsReport,
        truthReport,
        criticReport,
        gapClusters,
        hasClusterProducer,
      }),
    [atsReport, truthReport, criticReport, gapClusters, hasClusterProducer],
  );
  const group1 = groups.find((g) => g.id === 1)!;

  // ADR-081 cl. 5: the mode the panel OPENS in. `auto` follows the document;
  // the header switch changes the current view only, never the preference.
  const initialMode: ResolvedReviewMode = resolveReviewMode({
    preference: modePreference,
    kind: documentKind,
    documentId,
    hasGroup1Findings: group1.items.length > 0,
  });
  const [mode, setMode] = useState<ResolvedReviewMode>(initialMode);
  const [modeTouched, setModeTouched] = useState(false);

  // Re-resolve while the user has not touched the switch: the reports arrive
  // asynchronously, so `hasGroup1Findings` is false on the first render of a
  // document that does have findings. Once the user has switched, their choice
  // wins for the rest of the visit.
  useEffect(() => {
    if (modeTouched) return;
    setMode(
      resolveReviewMode({
        preference: modePreference,
        kind: documentKind,
        documentId,
        hasGroup1Findings: group1.items.length > 0,
      }),
    );
  }, [modeTouched, modePreference, documentKind, documentId, group1.items.length]);

  return (
    <section data-testid="review-surface" aria-label={t("title")} className="flex flex-col gap-3 p-3">
      <ReviewHeader
        groups={groups}
        renderedGroup1Count={group1.items.length}
        mode={mode}
        onToggleMode={() => {
          setModeTouched(true);
          setMode((m) => (m === "guided" ? "overview" : "guided"));
        }}
        atsReport={atsReport}
      />

      {mode === "overview" ? (
        <OverviewBody
          groups={groups}
          renderItem={(item, group) => (
            <ItemRow
              key={item.key}
              item={item}
              group={group}
              locale={locale}
              t={t}
              tAts={tAts}
              onResolveCluster={onResolveCluster}
            />
          )}
        />
      ) : (
        <GuidedBody
          groups={groups}
          documentKind={documentKind}
          documentId={documentId}
          renderItem={(item, group) => (
            <ItemRow
              key={item.key}
              item={item}
              group={group}
              locale={locale}
              t={t}
              tAts={tAts}
              onResolveCluster={onResolveCluster}
            />
          )}
        />
      )}

      {children}
    </section>
  );
}

/* ------------------------------------------------------------------ header */

function ReviewHeader({
  groups,
  renderedGroup1Count,
  mode,
  onToggleMode,
  atsReport,
}: {
  groups: ReviewGroup[];
  renderedGroup1Count: number;
  mode: ResolvedReviewMode;
  onToggleMode: () => void;
  atsReport: ATSReport;
}) {
  const t = useTranslations("documentReview");
  const tAts = useTranslations("ats");
  // ADR-081 cl. 4: the number is the length of the array group 1 renders from.
  // Nothing else computes it.
  const verdict = verdictState(groups, renderedGroup1Count);
  const present = atsReport?.keywords.present.length ?? 0;
  const total = present + (atsReport?.keywords.missing.length ?? 0);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <p
          data-testid="review-verdict"
          className={`flex-1 text-sm font-medium ${
            verdict.kind === "findings"
              ? "text-critical"
              : verdict.kind === "unknown"
                ? "text-on-surface-variant"
                : "text-on-surface"
          }`}
        >
          {verdict.kind === "findings" && t("verdictFindings", { count: verdict.count })}
          {verdict.kind === "clear" && t("verdictClear")}
          {verdict.kind === "clear_with_others" &&
            t("verdictClearWithOthers", { count: verdict.others })}
          {verdict.kind === "unknown" && t("verdictUnknown")}
        </p>

        {/* ADR-081 cl. 5: two-way, one click in either direction, in EVERY
            mode. It is the escape from guided and the entrance back, and it
            never writes the stored preference. */}
        <button
          type="button"
          data-testid="review-mode-switch"
          onClick={onToggleMode}
          aria-label={mode === "guided" ? t("switchToOverview") : t("switchToGuided")}
          title={mode === "guided" ? t("switchToOverview") : t("switchToGuided")}
          className="shrink-0 inline-flex items-center gap-1.5 rounded-full border border-outline-variant px-2.5 py-1 text-xs font-medium text-on-surface-variant hover:text-on-surface hover:bg-surface-container"
        >
          {mode === "guided" ? (
            <LayoutList className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ListChecks className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {mode === "guided" ? t("modeOverview") : t("modeGuided")}
        </button>
      </div>

      {atsReport && (
        <p data-testid="review-coverage" className="text-xs text-on-surface-variant">
          {tAts("keywordCoverage", { present, total })}
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------- group counts */

/**
 * ADR-081 cl. 6's visibility invariant, as one component used by BOTH modes:
 * a non-zero group's count is on screen without interaction, and an *unknown*
 * group says so instead of showing a zero.
 */
function GroupCount({ group }: { group: ReviewGroup }) {
  const t = useTranslations("documentReview");
  if (group.unknown) {
    return (
      <span
        data-testid={`review-group-count-${group.id}`}
        data-review-unknown="true"
        className="shrink-0 rounded-full border border-dashed border-outline-variant px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant"
      >
        {t("unknownLabel")}
      </span>
    );
  }
  const empty = group.items.length === 0;
  return (
    <span
      data-testid={`review-group-count-${group.id}`}
      className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-bold ${
        empty
          ? "bg-success-container text-success"
          : group.id === 1
            ? "bg-critical-container text-critical"
            : "bg-surface-container text-on-surface"
      }`}
    >
      {group.items.length}
    </span>
  );
}

function UnknownProducerNote({ group }: { group: ReviewGroup }) {
  const t = useTranslations("documentReview");
  if (group.unknownProducers.length === 0) return null;
  const names = group.unknownProducers.map((p) => t(PRODUCER_LABEL_KEY[p])).join(", ");
  return (
    <p
      data-testid={`review-group-unknown-${group.id}`}
      className="text-xs text-on-surface-variant"
    >
      {group.unknown ? t("unknownWhole", { producers: names }) : t("unknownPartial", { producers: names })}
    </p>
  );
}

/* ---------------------------------------------------------------- item rows */

function ItemRow({
  item,
  group,
  locale,
  t,
  tAts,
  onResolveCluster,
}: {
  item: ReviewItem;
  group: ReviewGroup;
  locale: string;
  t: ReturnType<typeof useTranslations<"documentReview">>;
  tAts: ReturnType<typeof useTranslations<"ats">>;
  onResolveCluster?: (gapId: string) => void;
}) {
  const detail = itemDetail(item, locale, tAts);
  const label =
    item.kind === "check" && item.checkId ? tAts(`checks.${baseId(item.checkId)}`) : item.label;

  const body = (
    <>
      <span
        aria-hidden="true"
        className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${SEVERITY_DOT[item.severity]}`}
      />
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-on-surface">{label}</span>
        {detail && <span className="block text-xs text-on-surface-variant">{detail}</span>}
        <span className="mt-0.5 flex flex-wrap items-center gap-1">
          {/* Group 3 renders two granularities under one heading and labels each
              item with its origin — ADR-081 cl. 2 forbids fusing them. The same
              label is shown in every group so the vocabulary stays one. */}
          <span className="rounded-full border border-outline-variant px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-on-surface-variant">
            {t(KIND_LABEL_KEY[item.kind])}
          </span>
          {item.producers.map((p) => (
            <span
              key={p}
              data-testid={`review-item-producer-${p}`}
              className="rounded-full bg-surface-container px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-on-surface-variant"
            >
              {t(PRODUCER_LABEL_KEY[p])}
            </span>
          ))}
        </span>
      </span>
    </>
  );

  // A cluster keeps the existing route into the editor / profile enrichment —
  // that is an EXISTING path (ADR-081 cl. 2's group 3 action), not a new
  // editing pass. Group 2 rows are never clickable: ADR-076 / cl. 3.
  if (item.kind === "cluster" && item.clusterId && onResolveCluster && group.id !== 2) {
    return (
      <li>
        <button
          type="button"
          data-testid={`review-item-g${group.id}-${item.key}`}
          onClick={() => onResolveCluster(item.clusterId!)}
          className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-container"
        >
          {body}
        </button>
      </li>
    );
  }

  return (
    <li
      data-testid={`review-item-g${group.id}-${item.key}`}
      className="flex items-start gap-2 px-2 py-1.5"
    >
      {body}
    </li>
  );
}

function itemDetail(
  item: ReviewItem,
  locale: string,
  tAts: ReturnType<typeof useTranslations<"ats">>,
): string | null {
  if (item.kind === "advisory" && item.advisory) {
    return localizedMessage(item.advisory.message, locale);
  }
  if (item.kind === "check" && item.check) return checkDetail(item.check, tAts);
  return item.detail ?? null;
}

/**
 * The ATS auditor's own detail line. The key/params rule comes from
 * `lib/ats-report.ts` — the same rule `ATSChecksPanel` uses, not a second copy:
 * next-intl does not throw on a missing ICU variable, it renders the raw key
 * path, so a keyed check without params must take the EN fallback.
 */
function checkDetail(
  c: ATSCheck,
  tAts: ReturnType<typeof useTranslations<"ats">>,
): string | null {
  if (usesLocalizedDetail(c)) {
    return tAts(`checkDetails.${c.details_key}`, c.details_params ?? undefined);
  }
  return c.details ?? null;
}

/* ------------------------------------------------------------------ overview */

function OverviewBody({
  groups,
  renderItem,
}: {
  groups: ReviewGroup[];
  renderItem: (item: ReviewItem, group: ReviewGroup) => ReactNode;
}) {
  const t = useTranslations("documentReview");
  // ADR-081 cl. 5: "all four groups as collapsed rows with counts, the FIRST
  // PRESENT group open".
  const firstPresent = groups.find((g) => g.items.length > 0)?.id ?? null;
  const [openId, setOpenId] = useState<number | null>(firstPresent);

  useEffect(() => setOpenId(firstPresent), [firstPresent]);

  return (
    <ul className="flex flex-col gap-2" data-testid="review-overview">
      {groups.map((group) => {
        // cl. 6: a group may collapse to a single line ONLY at count zero.
        const collapsible = group.items.length > 0;
        const open = collapsible && openId === group.id;
        return (
          <li
            key={group.id}
            data-testid={`review-group-${group.id}`}
            className="rounded-xl border border-outline-variant surface-glass"
          >
            <button
              type="button"
              data-testid={`review-group-toggle-${group.id}`}
              aria-expanded={open}
              disabled={!collapsible}
              onClick={() => setOpenId(open ? null : group.id)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left disabled:cursor-default"
            >
              {collapsible ? (
                open ? (
                  <ChevronDown className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                ) : (
                  <ChevronRight className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                )
              ) : (
                <span aria-hidden="true" className="h-4 w-4 shrink-0" />
              )}
              <span className="min-w-0 flex-1 text-sm font-heading font-semibold text-on-surface">
                {t(GROUP_TITLE_KEY[group.id])}
              </span>
              <GroupCount group={group} />
            </button>

            {/* ADR-081 cl. 9: a blind producer is stated WITHOUT interaction.
                Putting this inside the collapse would make the one thing the
                clause exists to say reachable only by opening the group. */}
            <div className="px-3 pb-1">
              <UnknownProducerNote group={group} />
              {/* cl. 6 — the all-clear collapse is permitted for PASSING CHECKS
                  ONLY, and never for a producer that did not run. */}
              {group.id === 4 &&
                group.passedChecks > 0 &&
                !group.unknownProducers.includes("ats") && (
                  <p data-testid="review-passed-checks" className="text-xs text-success">
                    {t("passedChecks", { count: group.passedChecks })}
                  </p>
                )}
            </div>

            {open && (
              <div className="border-t border-outline-variant px-1 pb-2 pt-1">
                {/* US302 / ADR-081 cl. 3 — the trade, named, with no action. */}
                {group.id === 2 && <Group2Trade />}
                <ul>{group.items.map((item) => renderItem(item, group))}</ul>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/**
 * ADR-081 clause 3 / US302 — group 2 names the trade and points at the three
 * handles that already exist. It offers NO action, and that is a decision:
 *
 * 1. It would misrepresent the data. `verified_missing_claimable` already
 *    drives the ADR-021 coverage reviewer, the rank gate, the #234 guard and
 *    both writer prompts — what this group shows is the residue AFTER the loop
 *    tried, not an untried opportunity. A button would tell the user the system
 *    had not bothered.
 * 2. It is architecturally forbidden. It would be a new deterministic
 *    post-review editing pass, and ADR-076 bars those for new work.
 *
 * A fact pin is not offered either (ADR-077 cl. 2 forbids any audit path
 * branching on pinned-ness); the pin is NAMED as an existing handle, and the
 * user reaches it on the editing tab where it lives, application-scoped.
 */
function Group2Trade() {
  const t = useTranslations("documentReview");
  return (
    <div data-testid="review-group2-trade" className="mx-2 mb-2 rounded-lg bg-surface-container px-3 py-2">
      <p className="text-xs text-on-surface">{t("group2TradeBody")}</p>
      <p className="mt-1 text-xs font-medium text-on-surface-variant">{t("group2HandlesTitle")}</p>
      <ul className="mt-0.5 list-disc pl-4 text-xs text-on-surface-variant">
        <li data-testid="review-group2-handle-pages">{t("group2HandlePages")}</li>
        <li data-testid="review-group2-handle-pin">{t("group2HandlePin")}</li>
        <li data-testid="review-group2-handle-regenerate">{t("group2HandleRegenerate")}</li>
      </ul>
    </div>
  );
}

/* -------------------------------------------------------------------- guided */

function GuidedBody({
  groups,
  documentKind,
  documentId,
  renderItem,
}: {
  groups: ReviewGroup[];
  documentKind: ReviewDocumentKind;
  documentId: string | null;
  renderItem: (item: ReviewItem, group: ReviewGroup) => ReactNode;
}) {
  const t = useTranslations("documentReview");
  const queue = useMemo(
    () => groups.flatMap((g) => g.items.map((item) => ({ item, group: g }))),
    [groups],
  );
  const [index, setIndex] = useState(0);

  const group1Count = groups.find((g) => g.id === 1)?.items.length ?? 0;

  // ADR-081 cl. 5a: the walked bit is per GENERATED DOCUMENT. It is set once
  // the reader has been past the last group-1 finding — group 1 is the class
  // `auto` exists to walk, so finishing it is what "walked" means.
  useEffect(() => {
    if (group1Count === 0) return;
    if (index >= group1Count) markDocumentWalked(documentKind, documentId);
  }, [index, group1Count, documentKind, documentId]);

  const current = queue[index];
  const remaining = Math.max(0, queue.length - index - 1);

  return (
    <div data-testid="review-guided" className="flex flex-col gap-2">
      {/* cl. 6 — the count strip. Every non-zero group's count is on screen in
          guided mode too, and the group-1 badge is always among them, so no
          finding is reachable only by completing the queue (JF-F-K.2). */}
      <ul className="flex flex-wrap items-center gap-1.5" data-testid="review-guided-counts">
        {groups.map((group) => (
          <li
            key={group.id}
            className="flex items-center gap-1 rounded-full border border-outline-variant px-2 py-0.5"
          >
            <span className="text-[11px] text-on-surface-variant">{t(GROUP_TITLE_KEY[group.id])}</span>
            <GroupCount group={group} />
          </li>
        ))}
      </ul>

      {queue.length === 0 ? (
        <p data-testid="review-guided-empty" className="rounded-xl border border-outline-variant surface-glass px-3 py-3 text-sm text-on-surface">
          {t("guidedNothing")}
        </p>
      ) : current ? (
        <div className="rounded-xl border border-outline-variant surface-glass px-3 py-2">
          <p className="text-xs font-heading font-semibold uppercase tracking-wide text-on-surface-variant">
            {t(GROUP_TITLE_KEY[current.group.id])}
          </p>
          <UnknownProducerNote group={current.group} />
          {current.group.id === 2 && <Group2Trade />}
          <ul>{renderItem(current.item, current.group)}</ul>
        </div>
      ) : (
        <p
          data-testid="review-guided-done"
          className="rounded-xl border border-outline-variant surface-glass px-3 py-3 text-sm text-on-surface"
        >
          {t("guidedDone")}
        </p>
      )}

      {queue.length > 0 && (
        <div className="flex items-center justify-between gap-2">
          {/* JF-F-K.2 asked for "how many remain unread", explicitly NOT "n of
              N" — an interrupted walk must not look like a completed one. */}
          <p data-testid="review-guided-remaining" className="text-xs text-on-surface-variant">
            {t("guidedRemaining", { count: remaining })}
          </p>
          <div className="flex items-center gap-2">
            <button
              type="button"
              data-testid="review-guided-prev"
              disabled={index === 0}
              onClick={() => setIndex((i) => Math.max(0, i - 1))}
              className="rounded-full border border-outline-variant px-3 py-1 text-xs font-medium text-on-surface-variant disabled:opacity-40 hover:bg-surface-container"
            >
              {t("guidedPrev")}
            </button>
            <button
              type="button"
              data-testid="review-guided-next"
              disabled={index >= queue.length}
              onClick={() => setIndex((i) => Math.min(queue.length, i + 1))}
              className="rounded-full bg-primary px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
            >
              {t("guidedNext")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
