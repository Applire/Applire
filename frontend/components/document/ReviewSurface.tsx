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

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Pencil,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { localizedMessage } from "@/components/cv/CriticAdvisoryPanel";
import type { GapHintItem } from "@/components/cv/ContentTab";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { baseId, usesLocalizedDetail, type ATSCheck, type ATSReport } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import {
  buildGroup1Rows,
  buildReviewGroups,
  verdictState,
  type Group1Row,
  type ReviewGroup,
  type ReviewItem,
  type ReviewProducer,
} from "@/lib/review-groups";
import {
  addEvidence,
  markWalked,
  refreshedReport,
  ReviewActionError,
  takeOut,
  undoDecision,
  type ReviewDocumentKind,
  type ReviewRefresh,
  type ReviewState,
  type SectionChange,
  type TestimonyOutcome,
} from "@/lib/api/document-review";
import { countInText, type LocateTarget, type PreviewLocator } from "@/lib/locate-in-preview";
import { changedPassages } from "@/lib/passage-diff";

/**
 * The document review surface (arc42 §5.3.29, ADR-081 as amended by ADR-090).
 *
 * ONE layout (ADR-090 cl. 1 — the overview/guided pair is gone): the verdict,
 * group 1 as a list of DECISIONS with the current one open as a card carrying
 * four handles (*Show me where*, *It's true, add it to my profile*, *Take it
 * out for me*, *Let me edit it*), and groups 2–4 beneath as one row each.
 *
 * Invariants, each gated by its own test:
 *
 * - **ADR-081 cl. 4 as amended by ADR-090 cl. 6** — the verdict sentence and the
 *   tab badge count the OPEN group-1 rows actually rendered; *n* in
 *   "k of n decided" is open + decided. A decision never hides a finding the
 *   live report still lists (`buildGroup1Rows`, SF-REVIEW.9).
 * - **ADR-081 cl. 6** — every non-zero group's count is on screen without
 *   interaction; a group collapses to a single line only at count zero.
 * - **ADR-081 cl. 9** — a producer that did not run renders as *unknown*, never
 *   0; for group 1 the note stands above the list and the verdict, and no
 *   progress figure reads as complete.
 *
 * Group 2 still offers no action (ADR-081 cl. 3 — ADR-090 amends it for group 1
 * only, in the removal direction).
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

/**
 * Ruling B-1 (founder, 2026-09-23): a finding whose EVERY matched form hit only
 * through the token-stem fallback cannot be taken out by the removal rewrite
 * (the document holds no literal form to remove), so the card does not offer
 * it. The backend refuses it too (409 `take_out_unavailable_stem_only`).
 */
export function isStemOnly(targets: LocateTarget[] | null | undefined): boolean {
  return Boolean(targets && targets.length > 0 && targets.every((t) => t.stem === true));
}

const STATUS_KEY: Record<"added" | "taken_out" | "edited", string> = {
  added: "statusAdded",
  taken_out: "statusTakenOut",
  edited: "statusEdited",
};

/** Opening a finding in the section editor (ADR-090 cl. 5). */
export interface EditFindingRequest {
  findingKey: string;
  label: string;
  targets: LocateTarget[] | null;
  /** 0-based place the user was looking at (0 when not located). */
  placeIndex: number;
}

export interface ReviewSurfaceProps {
  documentKind: ReviewDocumentKind;
  /** The GENERATED-DOCUMENT id — decisions are kept per document (ADR-090 cl. 6). */
  documentId: string | null;
  atsReport: ATSReport;
  truthReport: TruthfulnessReport;
  criticReport: OutcomeCriticReport;
  /** `null` = never loaded → group 3's cluster half is *unknown*, not zero. */
  gapClusters: GapHintItem[] | null;
  /**
   * `false` on the cover letter: §5.3.26's clusters are computed against the
   * CV, so the producer does not apply and is removed from groups 2 and 3.
   */
  hasClusterProducer?: boolean;
  /** ADR-090 cl. 6 — the persisted per-document decisions. `null` = none. */
  reviewState?: ReviewState | null;
  /**
   * A review action answered with refreshed reports and state (the server
   * awaited the re-audit). `documentChanged` = the document text changed (take
   * out / undo), so the page reloads the preview.
   */
  onRefresh?: (refresh: ReviewRefresh, opts: { documentChanged: boolean }) => void;
  /** The preview's locate handle (ADR-090 cl. 2). `null` = no preview to search. */
  locator?: PreviewLocator | null;
  /** Bumps whenever the preview document (re)loads — marks do not survive a reload. */
  previewVersion?: number;
  /** `sheet` = the phone's review sheet (ADR-050): *Show me where* switches to the locate view. */
  layout?: "panel" | "sheet";
  /** Phone only — the locate view opened (true) or closed (false). */
  onLocateModeChange?: (active: boolean) => void;
  /** ADR-090 cl. 5 — open the Edit tab on the section holding the place. */
  onEditFinding?: (request: EditFindingRequest) => void;
  /** Display label for a section id in the take-out result. */
  sectionLabel?: (sectionId: string) => string;
  /** Group 3's row: the gap view of this application. */
  gapAnalysisHref?: string | null;
  /** Existing handler for a gap cluster (routes to the editor or to /profile). */
  onResolveCluster?: (gapId: string) => void;
  /** #667 — a claimable gap cluster in group 2 lands in the section editor. */
  onEditGapSection?: (gapId: string) => void;
  /** Not one of the four producers — rendered after the groups (the letter's unasked-requirements panel). */
  children?: ReactNode;
}

type Busy = "add" | "takeout" | "undo" | null;

interface TakeOutResult {
  findingKey: string;
  changes: SectionChange[];
  stillListed: boolean;
  places: number;
}

interface LocateState {
  findingKey: string;
  index: number;
  total: number;
  texts: string[];
}

export function ReviewSurface({
  documentKind,
  documentId,
  atsReport,
  truthReport,
  criticReport,
  gapClusters,
  hasClusterProducer = true,
  reviewState = null,
  onRefresh,
  locator = null,
  previewVersion = 0,
  layout = "panel",
  onLocateModeChange,
  onEditFinding,
  sectionLabel,
  gapAnalysisHref = null,
  onResolveCluster,
  onEditGapSection,
  children,
}: ReviewSurfaceProps) {
  const t = useTranslations("documentReview");
  const tAts = useTranslations("ats");
  const tProfile = useTranslations("profile");
  const tErrors = useTranslations("errors");
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
  const rows = useMemo(() => buildGroup1Rows(group1.items, reviewState), [group1.items, reviewState]);
  const openRows = rows.filter((r) => r.status === "open");
  const decidedCount = rows.length - openRows.length;

  // The current card, by finding key — stable across report refreshes.
  const [currentKey, setCurrentKey] = useState<string | null>(null);
  const current: Group1Row | null =
    rows.find((r) => r.findingKey === currentKey) ?? openRows[0] ?? rows[0] ?? null;
  const currentIndex = current ? rows.indexOf(current) : -1;

  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addText, setAddText] = useState("");
  const [takeOutResult, setTakeOutResult] = useState<TakeOutResult | null>(null);
  const [testimony, setTestimony] = useState<{ findingKey: string; outcome: TestimonyOutcome } | null>(null);
  const [locate, setLocate] = useState<LocateState | null>(null);
  const [phoneLocating, setPhoneLocating] = useState(false);
  const [scan, setScan] = useState<{ findingKey: string; total: number; texts: string[] } | null>(null);
  const highlighted = useRef(false);
  const cardRef = useRef<HTMLDivElement>(null);

  const targets = current?.item?.targets ?? null;
  const targetsSig = targets ? JSON.stringify(targets) : "";

  const clearLocate = useCallback(() => {
    if (highlighted.current) {
      locator?.clear();
      highlighted.current = false;
    }
    setLocate(null);
  }, [locator]);

  // A different card: drop the marks, the add box, the transient error.
  const currentFindingKey = current?.findingKey ?? null;
  useEffect(() => {
    clearLocate();
    setAddOpen(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFindingKey]);

  // A reloaded preview carries no marks.
  useEffect(() => {
    highlighted.current = false;
    setLocate(null);
  }, [previewVersion]);

  // How many places the preview holds for the current card (no marking).
  useEffect(() => {
    if (!current || !targets || !locator) {
      setScan(null);
      return;
    }
    const r = locator.scan(targets);
    setScan(r ? { findingKey: current.findingKey, total: r.total, texts: r.texts } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFindingKey, targetsSig, locator, previewVersion, atsReport]);

  const endPhoneLocate = useCallback(() => {
    if (!phoneLocating) return;
    setPhoneLocating(false);
    onLocateModeChange?.(false);
  }, [phoneLocating, onLocateModeChange]);

  // Leaving the surface (sheet closed, tab switched) removes the marks it placed.
  useEffect(
    () => () => {
      if (highlighted.current) locator?.clear();
    },
    [locator],
  );

  const showPlace = (index: number) => {
    if (!current || !targets || !locator) return;
    const r = locator.show(targets, index);
    if (!r) return;
    highlighted.current = r.total > 0;
    setLocate({ findingKey: current.findingKey, index, total: r.total, texts: r.texts });
    if (layout === "sheet" && r.total > 0 && !phoneLocating) {
      setPhoneLocating(true);
      onLocateModeChange?.(true);
    }
  };

  const applyRefresh = (refresh: ReviewRefresh, documentChanged: boolean) => {
    onRefresh?.(refresh, { documentChanged });
  };

  const fail = (e: unknown) => {
    const status = e instanceof ReviewActionError ? e.status : 0;
    setError(tErrors("generic", { status: status || "—" }));
  };

  const maybeWalked = (state: ReviewState | null | undefined, stillOpen: number) => {
    if (!documentId || stillOpen > 0 || state?.walked_at) return;
    markWalked(documentKind, documentId)
      .then((r) => applyRefresh({ review_state: r.review_state }, false))
      .catch(() => {});
  };

  const runTakeOut = async (row: Group1Row) => {
    if (!documentId || busy) return;
    endPhoneLocate();
    clearLocate();
    setBusy("takeout");
    setError(null);
    try {
      const res = await takeOut(documentKind, documentId, row.findingKey);
      const rowTargets = row.item?.targets ?? null;
      let places = 0;
      if (rowTargets) {
        for (const c of res.changes) {
          places += Math.max(0, countInText(c.before, rowTargets) - countInText(c.after, rowTargets));
        }
      }
      setTakeOutResult({
        findingKey: row.findingKey,
        changes: res.changes,
        stillListed: res.still_listed,
        places: places > 0 ? places : res.changes.length,
      });
      setCurrentKey(row.findingKey);
      applyRefresh(res, res.changes.length > 0);
      if (!res.still_listed) maybeWalked(res.review_state, openRows.length - 1);
    } catch (e) {
      if (e instanceof ReviewActionError && e.status === 409) {
        // Ruling B-1: the server refused the removal (stem-only finding) —
        // the existing still-listed path, which points to *Let me edit it*.
        setTakeOutResult({ findingKey: row.findingKey, changes: [], stillListed: true, places: 0 });
        setCurrentKey(row.findingKey);
      } else {
        fail(e);
      }
    } finally {
      setBusy(null);
    }
  };

  const runAdd = async (row: Group1Row) => {
    if (!documentId || busy || !addText.trim()) return;
    setBusy("add");
    setError(null);
    try {
      const res = await addEvidence(documentKind, documentId, row.findingKey, addText.trim());
      setTestimony({ findingKey: row.findingKey, outcome: res.testimony });
      setAddOpen(false);
      setAddText("");
      setCurrentKey(row.findingKey);
      applyRefresh(res, false);
      const stillListed = buildReviewGroups({
        atsReport: refreshedReport(res) ?? atsReport,
        truthReport: res.truthfulness === undefined || res.truthfulness === null ? truthReport : res.truthfulness,
        criticReport,
        gapClusters,
        hasClusterProducer,
      })
        .find((g) => g.id === 1)!
        .items.some((it) => it.findingKey === row.findingKey);
      if (!stillListed) maybeWalked(res.review_state, openRows.length - 1);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const runUndo = async (row: Group1Row) => {
    if (!documentId || busy) return;
    setBusy("undo");
    setError(null);
    try {
      const res = await undoDecision(documentKind, documentId, row.findingKey);
      setTakeOutResult(null);
      setTestimony(null);
      setCurrentKey(row.findingKey);
      applyRefresh(res, true);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };

  const editRow = (row: Group1Row) => {
    endPhoneLocate();
    onEditFinding?.({
      findingKey: row.findingKey,
      label: row.label,
      targets: row.item?.targets ?? null,
      placeIndex: locate?.findingKey === row.findingKey ? locate.index : 0,
    });
  };

  const nextFinding = () => {
    setTakeOutResult(null);
    setTestimony(null);
    const after = rows.slice(currentIndex + 1).find((r) => r.status === "open");
    const next = after ?? openRows.find((r) => r.findingKey !== current?.findingKey) ?? null;
    if (next) setCurrentKey(next.findingKey);
    else maybeWalked(reviewState, openRows.filter((r) => r.findingKey !== current?.findingKey).length);
  };

  const testimonyMessage = (o: TestimonyOutcome): string => {
    switch (o.status) {
      case "applied":
        return tProfile("testimony.statusApplied", { count: o.changes?.length ?? 0 });
      case "partial":
        return tProfile("testimony.statusPartial", { count: o.not_applied?.length ?? 0 });
      case "no_change":
        return tProfile("testimony.statusNoChange");
      case "denial_recorded":
        return tProfile("testimony.statusDenialRecorded");
      case "needs_confirmation":
        return tProfile("testimony.statusNeedsConfirmation");
      case "conflict":
        return tProfile("testimony.statusConflict");
      case "error":
        return tProfile("testimony.statusError", { detail: o.detail ?? "" });
      default:
        return "";
    }
  };

  // ADR-081 cl. 4 (amended by ADR-090 cl. 6): the number is the OPEN group-1
  // rows actually rendered — the length of the very array the list renders.
  const verdict = verdictState(groups, openRows.length);
  const group1Blind = group1.unknownProducers.length > 0;
  const present = atsReport?.keywords.present.length ?? 0;
  const total = present + (atsReport?.keywords.missing.length ?? 0);

  const wordingOf = (texts: string[]): string => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const x of texts) {
      const k = x.toLowerCase();
      if (!seen.has(k)) {
        seen.add(k);
        out.push(x);
      }
    }
    // The ICU message wraps `{wording}` in quotes; several distinct wordings
    // are listed inside one pair, separated as quoted items.
    return out.join("”, “");
  };

  const currentScan = scan && current && scan.findingKey === current.findingKey ? scan : null;
  const currentLocate = locate && current && locate.findingKey === current.findingKey ? locate : null;
  const currentTakeOut =
    takeOutResult && current && takeOutResult.findingKey === current.findingKey ? takeOutResult : null;
  const currentTestimony =
    testimony && current && testimony.findingKey === current.findingKey ? testimony.outcome : null;

  const handleButtons = (row: Group1Row, variant: "stack" | "grid") => {
    const base =
      variant === "stack"
        ? "flex w-full items-center gap-2.5 min-h-11 rounded-xl border border-outline-variant bg-surface-bright px-3 py-2 text-left text-[13px] font-semibold text-on-surface hover:bg-surface-container disabled:opacity-50"
        : "flex flex-col items-center justify-center gap-1 min-h-[60px] rounded-xl border border-outline-variant bg-surface-bright px-1 py-1.5 text-center text-xs font-semibold leading-tight text-on-surface hover:bg-surface-container disabled:opacity-50";
    const canTakeOut = !isStemOnly(row.item?.targets);
    const wrap =
      variant === "stack" ? "flex flex-col gap-2" : `grid ${canTakeOut ? "grid-cols-3" : "grid-cols-2"} gap-1.5`;
    return (
      <div className={wrap} data-testid={`review-handles-${variant}`}>
        <button
          type="button"
          data-testid="review-action-add"
          disabled={busy !== null}
          onClick={() => {
            endPhoneLocate();
            setAddOpen(true);
          }}
          className={base}
        >
          <Plus className="h-4 w-4 shrink-0" aria-hidden="true" />
          {t("actionAddToProfile")}
        </button>
        {canTakeOut && (
        <button
          type="button"
          data-testid="review-action-takeout"
          disabled={busy !== null}
          aria-busy={busy === "takeout"}
          onClick={() => void runTakeOut(row)}
          className={base}
        >
          {busy === "takeout" ? (
            <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
          ) : (
            <Trash2 className="h-4 w-4 shrink-0" aria-hidden="true" />
          )}
          {t("actionTakeOut")}
        </button>
        )}
        <button
          type="button"
          data-testid="review-action-edit"
          disabled={busy !== null}
          onClick={() => editRow(row)}
          className={base}
        >
          <Pencil className="h-4 w-4 shrink-0" aria-hidden="true" />
          {t("actionEdit")}
        </button>
      </div>
    );
  };

  const stepper = (loc: LocateState, size: "card" | "sheet") => {
    const btn =
      size === "card"
        ? "flex h-9 w-9 items-center justify-center rounded-full border border-outline-variant bg-surface-bright text-primary disabled:text-outline-variant"
        : "flex h-11 w-11 items-center justify-center rounded-full border border-outline-variant bg-surface-bright text-primary disabled:text-outline-variant";
    return (
      <>
        <button
          type="button"
          data-testid="review-locate-prev"
          aria-label={t("locatePrev")}
          disabled={loc.index <= 0}
          onClick={() => showPlace(loc.index - 1)}
          className={btn}
        >
          <ChevronLeft className="h-4 w-4" aria-hidden="true" />
        </button>
        <button
          type="button"
          data-testid="review-locate-next"
          aria-label={t("locateNext")}
          disabled={loc.index >= loc.total - 1}
          onClick={() => showPlace(loc.index + 1)}
          className={btn}
        >
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </button>
      </>
    );
  };

  /* ------------------------------------------------------------ the card */

  const renderCard = (row: Group1Row) => {
    const decided = row.status !== "open";
    const notMarkable = !decided && (!row.item?.targets || (currentScan !== null && currentScan.total === 0));
    const border = decided ? "border-success" : "border-primary";
    return (
      <div
        ref={cardRef}
        data-testid="review-card"
        data-finding-key={row.findingKey}
        data-status={row.status}
        aria-busy={busy !== null}
        className={`flex flex-col gap-3 rounded-xl border-2 ${border} bg-surface-bright p-3.5 shadow-sm`}
      >
        <div className="flex flex-col gap-1">
          <span
            data-testid="review-card-position"
            className="text-[11px] font-bold tracking-wide text-on-surface-variant"
          >
            {t("cardPosition", { index: currentIndex + 1, total: rows.length })}
          </span>
          <span className="font-heading text-[17px] font-bold text-on-surface">{row.label}</span>
          {/* ADR-081 cl. 2: a merged row CITES both producers. */}
          {row.item && (
            <span className="flex flex-wrap items-center gap-1" data-testid="review-card-producers">
              {row.item.producers.map((p) => (
                <span
                  key={p}
                  data-testid={`review-item-producer-${p}`}
                  className="rounded-full bg-surface-container px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-on-surface-variant"
                >
                  {t(PRODUCER_LABEL_KEY[p])}
                </span>
              ))}
            </span>
          )}

          {decided && !currentTakeOut && (
            <span data-testid="review-card-status" className="text-[13px] font-semibold text-success">
              {t(STATUS_KEY[row.status as keyof typeof STATUS_KEY])}
            </span>
          )}

          {!decided && currentScan && currentScan.total > 0 && (
            <span data-testid="review-card-matched" className="text-[13px] leading-snug text-on-surface-variant">
              {t("cardMatched", { wording: wordingOf(currentScan.texts), count: currentScan.total })}
            </span>
          )}
          {notMarkable && (
            <span data-testid="review-locate-not-found" className="text-[13px] leading-snug text-on-surface-variant">
              {t("locateNotFound")}
            </span>
          )}
          {!decided && row.item?.detail && (
            <span className="text-xs text-on-surface-variant">{row.item.detail}</span>
          )}
        </div>

        {/* Take it out — the result, with what changed (canvas: TakeOut). */}
        {currentTakeOut && (
          <div className="flex flex-col gap-2" data-testid="review-takeout-result">
            <span
              data-testid={currentTakeOut.stillListed ? "review-takeout-still" : "review-takeout-done"}
              className={`text-[13px] font-semibold ${currentTakeOut.stillListed ? "text-critical" : "text-success"}`}
            >
              {currentTakeOut.stillListed
                ? t("takenOutStillFlagged")
                : t("takenOutDone", { count: currentTakeOut.places })}
            </span>
            {currentTakeOut.changes.map((c, i) => (
              <div
                key={`${c.section_id}-${i}`}
                data-testid="review-takeout-change"
                className="flex flex-col gap-1.5 rounded-lg bg-surface-container px-3 py-2.5"
              >
                <span className="text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
                  {sectionLabel ? sectionLabel(c.section_id) : c.section_id}
                </span>
                {/* D-3 / ADR-090 cl. 3: the CHANGED passages only — each
                    changed sentence or line struck through, then what
                    replaced it; unchanged text elided. */}
                {changedPassages(c.before, c.after).map((h, k) => (
                  <div key={k} data-testid="review-takeout-hunk" className="flex flex-col gap-1">
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                    {h.elidedBefore && <span aria-hidden="true" className="text-xs text-on-surface-variant">…</span>}
                    {h.before.map((u, x) => (
                      <span
                        key={`b${x}`}
                        data-testid="review-takeout-before"
                        className="text-xs leading-snug text-on-surface-variant line-through"
                      >
                        {u}
                      </span>
                    ))}
                    {h.after.map((u, x) => (
                      <span key={`a${x}`} data-testid="review-takeout-after" className="text-xs leading-snug text-on-surface">
                        {u}
                      </span>
                    ))}
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                    {h.elidedAfter && <span aria-hidden="true" className="text-xs text-on-surface-variant">…</span>}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {/* Add it to my profile — the box, then the testimony outcome. */}
        {currentTestimony && (
          <p
            data-testid="review-testimony-result"
            data-status={currentTestimony.status}
            className={`text-[13px] ${currentTestimony.status === "error" ? "text-critical" : "text-on-surface"}`}
          >
            {testimonyMessage(currentTestimony)}
          </p>
        )}

        {!decided && !currentLocate && !notMarkable && !addOpen && (
          <button
            type="button"
            data-testid="review-action-locate"
            disabled={busy !== null}
            onClick={() => showPlace(0)}
            className="flex min-h-11 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-white disabled:opacity-50"
          >
            <Search className="h-4 w-4" aria-hidden="true" />
            {t("actionShowWhere")}
          </button>
        )}

        {!decided && currentLocate && currentLocate.total > 0 && layout === "panel" && (
          <div
            data-testid="review-locate-stepper"
            className="flex items-center gap-2 rounded-lg bg-gold-container py-1.5 pl-3 pr-1.5"
          >
            <Search className="h-4 w-4 shrink-0 text-gold-dim" aria-hidden="true" />
            <span className="flex-1 text-[13px] font-semibold text-on-surface" aria-live="polite">
              {t("locateShowing", { index: currentLocate.index + 1, total: currentLocate.total })}
            </span>
            {stepper(currentLocate, "card")}
          </div>
        )}

        {!decided && addOpen && (
          <div data-testid="review-add-box" className="flex flex-col gap-2">
            <label htmlFor={`review-add-${row.findingKey}`} className="text-[13px] font-semibold text-on-surface">
              {t("addPrompt")}
            </label>
            <textarea
              id={`review-add-${row.findingKey}`}
              data-testid="review-add-text"
              value={addText}
              onChange={(e) => setAddText(e.target.value)}
              disabled={busy !== null}
              rows={3}
              className="w-full rounded-lg border border-outline-variant bg-surface-bright px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
            />
            <div className="flex gap-2">
              <button
                type="button"
                data-testid="review-add-submit"
                disabled={busy !== null || !addText.trim()}
                onClick={() => void runAdd(row)}
                className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy === "add" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {t("addSubmit")}
              </button>
              <button
                type="button"
                data-testid="review-add-cancel"
                disabled={busy !== null}
                onClick={() => {
                  setAddOpen(false);
                  setAddText("");
                }}
                className="min-h-11 rounded-full border border-outline-variant bg-surface-bright px-4 text-sm font-semibold text-on-surface"
              >
                {t("addCancel")}
              </button>
            </div>
          </div>
        )}

        {!decided && !addOpen && handleButtons(row, "stack")}

        {/* The decided card, and the still-listed take-out: Next / Undo / Edit. */}
        {(decided || currentTakeOut) && (
          <div className="flex flex-wrap gap-2">
            {openRows.some((r) => r.findingKey !== row.findingKey) && (
              <button
                type="button"
                data-testid="review-action-next"
                onClick={nextFinding}
                className="min-h-11 flex-1 whitespace-nowrap rounded-full bg-primary px-4 text-sm font-semibold text-white"
              >
                {t("actionNextFinding")}
              </button>
            )}
            {row.decision?.undo && (row.status !== "open" || currentTakeOut) && (
              <button
                type="button"
                data-testid="review-action-undo"
                disabled={busy !== null}
                onClick={() => void runUndo(row)}
                className="flex min-h-11 items-center gap-2 whitespace-nowrap rounded-full border border-outline-variant bg-surface-bright px-4 text-sm font-semibold text-on-surface disabled:opacity-50"
              >
                {busy === "undo" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {t("actionUndo")}
              </button>
            )}
            {decided && currentTakeOut && (
              <button
                type="button"
                data-testid="review-action-edit-after"
                onClick={() => editRow(row)}
                className="min-h-11 whitespace-nowrap rounded-full border border-outline-variant bg-surface-bright px-4 text-sm font-semibold text-on-surface"
              >
                {t("actionEdit")}
              </button>
            )}
          </div>
        )}

        {error && (
          <p data-testid="review-action-error" role="alert" className="text-[13px] text-critical">
            {error}
          </p>
        )}
      </div>
    );
  };

  /* ------------------------------------------------ phone locate view */

  const phoneLocate =
    layout === "sheet" && phoneLocating && current && currentLocate && typeof document !== "undefined"
      ? createPortal(
          <div className="md:hidden" data-testid="review-phone-locate">
            <div className="fixed inset-x-0 top-0 z-[70] flex items-center gap-2 bg-primary px-3 py-2.5">
              <button
                type="button"
                data-testid="review-locate-back"
                onClick={() => {
                  clearLocate();
                  endPhoneLocate();
                }}
                className="flex min-h-11 items-center gap-1.5 px-2 text-sm font-semibold text-white"
              >
                <ArrowLeft className="h-4 w-4" aria-hidden="true" />
                {t("locateBack")}
              </button>
            </div>
            <div className="fixed inset-x-0 bottom-0 z-[70] flex flex-col gap-3 rounded-t-2xl bg-surface-bright px-4 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-3.5 shadow-[0_-4px_16px_rgba(0,0,0,0.12)]">
              <div className="flex items-center gap-2">
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate font-heading text-[15px] font-bold text-on-surface">{current.label}</span>
                  <span
                    data-testid="review-locate-place"
                    aria-live="polite"
                    className="text-xs text-on-surface-variant"
                  >
                    {t("locatePlace", {
                      wording: currentLocate.texts[currentLocate.index] ?? current.label,
                      index: currentLocate.index + 1,
                      total: currentLocate.total,
                    })}
                  </span>
                </div>
                {stepper(currentLocate, "sheet")}
              </div>
              {handleButtons(current, "grid")}
            </div>
          </div>,
          document.body,
        )
      : null;

  /* ------------------------------------------------------------ render */

  return (
    <section data-testid="review-surface" aria-label={t("title")} className="flex flex-col gap-4 p-3">
      {/* ADR-081 cl. 9 / ADR-090 cl. 1: a blind group-1 producer is stated
          ABOVE the verdict and the list, never inside a collapse. */}
      <UnknownProducerNote group={group1} />

      <div className="flex flex-col gap-1.5">
        <p
          data-testid="review-verdict"
          className={`font-heading text-lg font-bold leading-snug ${
            verdict.kind === "unknown" ? "text-on-surface-variant" : "text-on-surface"
          }`}
        >
          {verdict.kind === "findings" && t("verdictFindings", { count: verdict.count })}
          {verdict.kind === "clear" && t("verdictClear")}
          {verdict.kind === "clear_with_others" && t("verdictClearWithOthers", { count: verdict.others })}
          {verdict.kind === "unknown" && t("verdictUnknown")}
        </p>
        {verdict.kind === "findings" && (
          <p data-testid="review-verdict-sub" className="text-sm leading-snug text-on-surface-variant">
            {t("verdictSub")}
          </p>
        )}
      </div>

      {/* "k of n decided" — n = open + decided. Withheld while a group-1
          producer is blind: a complete-looking figure would be a lie (cl. 9). */}
      {rows.length > 0 && !group1Blind && (
        <div className="flex flex-col gap-1.5" data-testid="review-progress">
          <div
            aria-hidden="true"
            className="grid gap-1"
            style={{ gridTemplateColumns: `repeat(${rows.length}, minmax(0, 1fr))` }}
          >
            {rows.map((r) => (
              <span
                key={r.findingKey}
                className={`h-1.5 rounded-sm ${
                  r.status !== "open"
                    ? "bg-success"
                    : r.findingKey === current?.findingKey
                      ? "bg-primary"
                      : "bg-surface-container-highest"
                }`}
              />
            ))}
          </div>
          <span data-testid="review-progress-label" className="text-xs font-semibold text-on-surface-variant">
            {t("progressDecided", { decided: decidedCount, total: rows.length })}
          </span>
        </div>
      )}

      {current && renderCard(current)}

      {rows.length > 0 && (
        <ul data-testid="review-group1-list" className="flex flex-col">
          {rows.map((r) => {
            const isCurrent = r.findingKey === current?.findingKey;
            return (
              <li key={r.findingKey}>
                <button
                  type="button"
                  data-testid={`review-item-g1-${r.findingKey}`}
                  data-status={r.status}
                  aria-current={isCurrent ? "true" : undefined}
                  onClick={() => {
                    setCurrentKey(r.findingKey);
                    if (!isCurrent) {
                      setTakeOutResult(null);
                      setTestimony(null);
                    }
                    // The card sits above the list: bring it back into view.
                    cardRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
                  }}
                  className={`flex min-h-11 w-full items-center gap-2.5 border-b border-surface-container px-1 text-left ${
                    isCurrent ? "rounded-lg bg-surface-container" : ""
                  }`}
                >
                  <span className="flex w-[18px] shrink-0 justify-center" aria-hidden="true">
                    {r.status !== "open" ? (
                      <Check className="h-[18px] w-[18px] text-success" strokeWidth={2.4} />
                    ) : isCurrent ? (
                      <span className="h-2 w-2 rounded-full bg-primary" />
                    ) : (
                      <span className="h-2 w-2 rounded-full border-[1.5px] border-critical" />
                    )}
                  </span>
                  <span
                    className={`min-w-0 flex-1 text-sm ${
                      isCurrent
                        ? "font-semibold text-primary"
                        : r.status !== "open"
                          ? "text-on-surface-variant"
                          : "text-on-surface"
                    }`}
                  >
                    {r.label}
                  </span>
                  {r.status !== "open" && (
                    <span
                      data-testid={`review-row-status-${r.status}`}
                      className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                        r.status === "added"
                          ? "bg-success-container text-success"
                          : "bg-surface-container text-on-surface-variant"
                      }`}
                    >
                      {t(STATUS_KEY[r.status])}
                    </span>
                  )}
                  {r.status === "open" && !isCurrent && (
                    <ChevronRight className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <OtherFindings
        groups={groups}
        gapAnalysisHref={gapAnalysisHref}
        renderItem={(item, group) => (
          <ItemRow
            key={item.key}
            item={item}
            group={group}
            locale={locale}
            t={t}
            tAts={tAts}
            onResolveCluster={onResolveCluster}
            onEditGapSection={onEditGapSection}
          />
        )}
      />

      {atsReport && (
        <p data-testid="review-coverage" className="text-xs text-on-surface-variant">
          {t("coverageLine", { present, total })}
        </p>
      )}

      {children}
      {phoneLocate}
    </section>
  );
}

/* ------------------------------------------------------------- group counts */

/**
 * ADR-081 cl. 6's visibility invariant: a non-zero group's count is on screen
 * without interaction, and an *unknown* group says so instead of showing a zero.
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
        empty ? "bg-success-container text-success" : "bg-surface-container text-on-surface"
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
    <p data-testid={`review-group-unknown-${group.id}`} className="text-xs text-on-surface-variant">
      {group.unknown ? t("unknownWhole", { producers: names }) : t("unknownPartial", { producers: names })}
    </p>
  );
}

/* ---------------------------------------------------------- groups 2 – 4 */

/**
 * Groups 2–4 beneath group 1, one row each with its count (ADR-090 cl. 1;
 * ADR-081 cl. 6 unchanged). Group 2 and 4 open in place; group 3 links to the
 * gap view (ruling C-1: `/flow/{id}/gaps` is reachable once the analysis exists).
 */
function OtherFindings({
  groups,
  gapAnalysisHref,
  renderItem,
}: {
  groups: ReviewGroup[];
  gapAnalysisHref: string | null;
  renderItem: (item: ReviewItem, group: ReviewGroup) => ReactNode;
}) {
  const t = useTranslations("documentReview");
  const [openId, setOpenId] = useState<number | null>(null);
  const order = [2, 4, 3] as const; // the canvas' order: the two in-place groups, then the link out
  return (
    <div className="flex flex-col gap-2 border-t border-outline-variant pt-2" data-testid="review-other">
      <span className="font-heading text-[13px] font-bold text-on-surface">{t("otherTitle")}</span>
      <span className="text-xs text-on-surface-variant">{t("otherSub")}</span>
      <ul className="flex flex-col gap-2">
        {order.map((id) => {
          const group = groups.find((g) => g.id === id)!;
          const count = group.items.length;
          // cl. 6: a group may collapse to a single line ONLY at count zero.
          const expandable = count > 0;
          const open = expandable && openId === id;
          const rowClass =
            "flex min-h-11 w-full items-center gap-2.5 rounded-xl border border-outline-variant bg-surface-bright px-3 text-left disabled:cursor-default";

          if (id === 3 && gapAnalysisHref && !group.unknown) {
            return (
              <li key={id} data-testid="review-group-3">
                <Link href={gapAnalysisHref} data-testid="review-group3-link" className={rowClass}>
                  <span className="flex min-w-0 flex-1 flex-col py-1.5">
                    <span className="text-[13px] text-on-surface">{t(GROUP_TITLE_KEY[3])}</span>
                    <span className="text-xs font-semibold text-primary">{t("group3Link")}</span>
                  </span>
                  <GroupCount group={group} />
                  <ChevronRight className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
                </Link>
                <div className="px-1 pt-1">
                  <UnknownProducerNote group={group} />
                </div>
              </li>
            );
          }

          return (
            <li key={id} data-testid={`review-group-${id}`}>
              <button
                type="button"
                data-testid={`review-group-toggle-${id}`}
                aria-expanded={open}
                disabled={!expandable || id === 3}
                onClick={() => setOpenId(open ? null : id)}
                className={rowClass}
              >
                <span className="min-w-0 flex-1 text-[13px] text-on-surface">{t(GROUP_TITLE_KEY[id])}</span>
                <GroupCount group={group} />
                {expandable && id !== 3 ? (
                  <ChevronDown
                    className={`h-4 w-4 shrink-0 text-on-surface-variant transition-transform ${open ? "rotate-180" : ""}`}
                    aria-hidden="true"
                  />
                ) : (
                  <span aria-hidden="true" className="h-4 w-4 shrink-0" />
                )}
              </button>
              {/* cl. 9: a blind producer is stated WITHOUT interaction. */}
              <div className="px-1 pt-1">
                <UnknownProducerNote group={group} />
                {/* cl. 6 — the all-clear collapse is for PASSING CHECKS only. */}
                {id === 4 && group.passedChecks > 0 && !group.unknownProducers.includes("ats") && (
                  <p data-testid="review-passed-checks" className="text-xs text-success">
                    {t("passedChecks", { count: group.passedChecks })}
                  </p>
                )}
              </div>
              {open && (
                <div className="mt-1 rounded-xl border border-outline-variant px-1 pb-2 pt-1">
                  {id === 2 && <Group2Trade />}
                  <ul>{group.items.map((item) => renderItem(item, group))}</ul>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
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
  onEditGapSection,
}: {
  item: ReviewItem;
  group: ReviewGroup;
  locale: string;
  t: ReturnType<typeof useTranslations<"documentReview">>;
  tAts: ReturnType<typeof useTranslations<"ats">>;
  onResolveCluster?: (gapId: string) => void;
  onEditGapSection?: (gapId: string) => void;
}) {
  const detail = itemDetail(item, locale, tAts);
  const label = item.kind === "check" && item.checkId ? tAts(`checks.${baseId(item.checkId)}`) : item.label;

  const body = (
    <>
      <span aria-hidden="true" className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${SEVERITY_DOT[item.severity]}`} />
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-on-surface">{label}</span>
        {detail && <span className="block text-xs text-on-surface-variant">{detail}</span>}
        <span className="mt-0.5 flex flex-wrap items-center gap-1">
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

  // #667 / ADR-081 cl. 3 amended 2026-09-11: a group-2 CLAIMABLE cluster lands
  // the user in the section editor with this gap preselected — the surface
  // navigates, the insertion is the user's own hand.
  if (item.kind === "cluster" && item.clusterId && onEditGapSection && group.id === 2) {
    return (
      <li>
        <button
          type="button"
          data-testid={`review-item-g${group.id}-${item.key}`}
          data-handle="section-editor"
          onClick={() => onEditGapSection(item.clusterId!)}
          className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-surface-container"
        >
          {body}
        </button>
      </li>
    );
  }

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
    <li data-testid={`review-item-g${group.id}-${item.key}`} className="flex items-start gap-2 px-2 py-1.5">
      {body}
    </li>
  );
}

function itemDetail(item: ReviewItem, locale: string, tAts: ReturnType<typeof useTranslations<"ats">>): string | null {
  if (item.kind === "advisory" && item.advisory) return localizedMessage(item.advisory.message, locale);
  if (item.kind === "check" && item.check) return checkDetail(item.check, tAts);
  return item.detail ?? null;
}

/**
 * The ATS auditor's own detail line — the key/params rule from
 * `lib/ats-report.ts`, the same rule `ATSChecksPanel` uses.
 */
function checkDetail(c: ATSCheck, tAts: ReturnType<typeof useTranslations<"ats">>): string | null {
  if (usesLocalizedDetail(c)) return tAts(`checkDetails.${c.details_key}`, c.details_params ?? undefined);
  return c.details ?? null;
}

/**
 * ADR-081 clause 3 / US302 — group 2 names the trade and the handles that
 * already exist. It offers NO action: what it shows is the residue AFTER the
 * coverage loop tried, and an insertion pass is barred by ADR-076. ADR-090
 * amends clause 3 for group 1 only, in the removal direction.
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
        <li data-testid="review-group2-handle-editor">{t("group2HandleEditor")}</li>
      </ul>
    </div>
  );
}
