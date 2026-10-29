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
import { useTranslations } from "next-intl";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CornerDownRight,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import type { GapHintItem } from "@/components/cv/ContentTab";
import type { OutcomeCriticReport } from "@/components/cv/CriticAdvisoryPanel";
import { baseId, usesLocalizedDetail, type ATSCheck, type ATSReport, type DeliveryRemoval } from "@/lib/ats-report";
import type { TruthfulnessReport } from "@/lib/truthfulness-display";
import {
  buildDeliveryView,
  buildReviewGroups,
  removeOnly,
  noChangeExit,
  noChangeFound,
  unknownState,
  type Group1Row,
  type UnknownState,
  type ReviewItem,
  type ReviewProducer,
  type UnknownRow,
} from "@/lib/review-groups";
import {
  addEvidence,
  confirmSupported,
  keepLetterLength,
  listStations,
  markWalked,
  moveToStation,
  PAGE_LENGTH_KEY,
  recheckDocument,
  removalEvidence,
  ReviewActionError,
  rewriteAsGoal,
  SEND_LANGUAGE_KEY,
  shortenLetter,
  takeOut,
  translateSpans,
  undoDecision,
  type ReviewDocumentKind,
  type ReviewRefresh,
  type ReviewState,
  type SectionChange,
  type StationOption,
  type TestimonyOutcome,
} from "@/lib/api/document-review";
import { countInText, type LocateTarget, type PreviewLocator } from "@/lib/locate-in-preview";
import { normQuote } from "@/lib/norm-quote";
import { changedPassages } from "@/lib/passage-diff";
import { ReviewDegradationNote } from "./ReviewDegradationNote";
import { oracleDetailText, type OracleDetailTranslator } from "@/lib/oracle-detail";
import type { DegradedReviewStep } from "@/lib/review-degradation";

/**
 * The document review surface (arc42 §5.3.29, ADR-081 / ADR-090 as amended by
 * ADR-099 — "fix it, ask once, or stay silent"; mock approved RULING C-1).
 *
 * Every row renders by the delivery class and resolver READ from its producer's
 * row (`buildDeliveryView`): class A = ONE question with the answers that settle
 * it, class U = "Nicht geprüft" with "Jetzt prüfen" / "Neue Fassung erstellen",
 * F and S silent, an unclassified row as today's fallback (never hidden).
 *
 * Invariants, each gated by its own test:
 *
 * - **ADR-099 cl. 3** — the headline and the phone badge count the class A and
 *   U rows of the rendered view (`view.count` — the same number the page uses).
 * - **cl. 5** — removals: ONE collapsed, uncounted line; a pinned removal opens it.
 * - **cl. 6** — group 1: one question per kind; "Stimmt" without a vault change
 *   never closes a row (the card then offers only "Entfernen").
 * - **cl. 7/8** — no group 2, no cross-document section, no craft rows; group 4
 *   is one passed line. **cl. 12** — no server `details` prose.
 */

/** ADV-10: the U row's words follow what the producer wrote (`unknownState`). */
const UNKNOWN_TITLE_KEY = {
  notRun: "delivery.unknownTitle",
  unfinished: "delivery.unknownTitleUnfinished",
  earlierDraft: "delivery.unknownTitleEarlierDraft",
  foundNotFixed: "delivery.unknownTitleFoundNotFixed",
} as const;
const UNKNOWN_BODY_KEY: Record<Exclude<UnknownState, "notRun">, string> = {
  unfinished: "delivery.unknownBodyUnfinished",
  earlierDraft: "delivery.unknownBodyEarlierDraft",
  foundNotFixed: "delivery.unknownBodyFoundNotFixed",
} as const;

const PRODUCER_LABEL_KEY: Record<ReviewProducer, string> = {
  ats: "producerAts",
  oracle: "producerOracle",
  critic: "producerCritic",
  clusters: "producerClusters",
};

/** The decided-row labels (the finding left the report). */
const STATUS_KEY: Record<string, string> = {
  added: "statusAdded",
  supported: "statusSupported",
  taken_out: "statusTakenOut",
  edited: "statusEdited",
  moved: "delivery.statusMoved",
  as_goal: "delivery.statusAsGoal",
};

/**
 * Ruling B-1 (founder, 2026-09-23): a finding whose EVERY matched form hit only
 * through the token-stem fallback cannot be taken out by the removal rewrite.
 */
export function isStemOnly(targets: LocateTarget[] | null | undefined): boolean {
  return Boolean(targets && targets.length > 0 && targets.every((t) => t.stem === true));
}

/** Opening a finding in the section editor (ADR-090 cl. 5). */
export interface EditFindingRequest {
  findingKey: string;
  label: string;
  targets: LocateTarget[] | null;
  /** 0-based place the user was looking at (0 when not located). */
  placeIndex: number;
}

/**
 * A counted (or uncounted) card the PAGE renders into the review list — the
 * CV's page-length card, the letter's unasked requirements and signature card
 * (ADR-099 cl. 7/9). `counted` enters the headline (cl. 3).
 */
export interface ReviewExtraCard {
  key: string;
  counted: boolean;
  node: ReactNode;
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
  /** `false` on the cover letter: §5.3.26's clusters are computed against the CV. */
  hasClusterProducer?: boolean;
  /** R2-10: the job's open requirements by name — group 3's population and count. */
  openRequirements?: string[] | null;
  /** ADR-090 cl. 6 — the persisted per-document decisions. `null` = none. */
  reviewState?: ReviewState | null;
  /** A review action answered with refreshed reports and state (the server awaited the re-audit). */
  onRefresh?: (refresh: ReviewRefresh, opts: { documentChanged: boolean }) => void;
  /** The preview's locate handle (ADR-090 cl. 2). `null` = no preview to search. */
  locator?: PreviewLocator | null;
  /** Bumps whenever the preview document (re)loads — marks do not survive a reload. */
  previewVersion?: number;
  /** `sheet` = the phone's review sheet (ADR-050). */
  layout?: "panel" | "sheet";
  /** Phone only — the locate view opened (true) or closed (false). */
  onLocateModeChange?: (active: boolean) => void;
  /** ADR-090 cl. 5 — open the Edit tab on the section holding the place (after a take-out that left it listed). */
  onEditFinding?: (request: EditFindingRequest) => void;
  /** Display label for a section id in the action result. */
  sectionLabel?: (sectionId: string) => string;
  /** Group 3's row: the gap view of this application. */
  gapAnalysisHref?: string | null;
  /** The page's own cards in the review list (page length, unasked requirements, signature). */
  extraCards?: ReviewExtraCard[];
  /** Rendered after the review list. */
  children?: ReactNode;
  /** ADR-081 cl. 9 (T-2): the writing loop's review steps that did not run. */
  degradation?: DegradedReviewStep[] | null;
  /** The existing regenerate path (behind the page's new-version confirmation). */
  onRegenerate?: () => void;
  /** R2-8 (8): the page's report fetches have not all settled yet. */
  loading?: boolean;
  // Accepted for call-site compatibility; ADR-099 removed what used them.
  cvEditHref?: string | null;
  onResolveCluster?: (gapId: string) => void;
  onEditGapSection?: (gapId: string) => void;
  onOpenPins?: () => void;
}

type Busy = "answer" | "remove" | "move" | "goal" | "undo" | "translate" | "recheck" | "page" | null;

interface EditResult {
  key: string;
  action: "taken_out" | "moved" | "as_goal" | "translated" | "shortened";
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
  openRequirements,
  reviewState = null,
  onRefresh,
  locator = null,
  previewVersion = 0,
  layout = "panel",
  onLocateModeChange,
  onEditFinding,
  sectionLabel,
  gapAnalysisHref = null,
  extraCards = [],
  children,
  degradation = null,
  onRegenerate,
  loading = false,
}: ReviewSurfaceProps) {
  const t = useTranslations("documentReview");
  const tAts = useTranslations("ats");
  const tCv = useTranslations("cv");
  const tProfile = useTranslations("profile");
  const tErrors = useTranslations("errors");
  const tOracle = useTranslations("oracleDetail");
  const isLetter = documentKind === "cover-letter";

  const extraCounted = extraCards.filter((c) => c.counted).length;
  const view = useMemo(
    () =>
      buildDeliveryView({
        atsReport,
        truthReport,
        criticReport,
        gapClusters,
        hasClusterProducer,
        openRequirements,
        reviewState,
        degradation,
        extraCounted,
        pageCardFromPage: !isLetter,
      }),
    [atsReport, truthReport, criticReport, gapClusters, hasClusterProducer, openRequirements, reviewState, degradation, extraCounted, isLetter],
  );
  const group3 = useMemo(
    () =>
      buildReviewGroups({ atsReport, truthReport, criticReport, gapClusters, hasClusterProducer, openRequirements }).find(
        (g) => g.id === 3,
      )!,
    [atsReport, truthReport, criticReport, gapClusters, hasClusterProducer, openRequirements],
  );

  const rows = view.questions;
  const openRows = rows.filter((r) => r.status === "open");

  // The current card, by finding key — stable across report refreshes.
  const [currentKey, setCurrentKey] = useState<string | null>(null);
  const current: Group1Row | null =
    rows.find((r) => r.findingKey === currentKey) ?? openRows[0] ?? rows[0] ?? null;
  const currentIndex = current ? rows.indexOf(current) : -1;

  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<{ scope: string; text: string } | null>(null);
  const [answerOpen, setAnswerOpen] = useState(false);
  const [answerText, setAnswerText] = useState("");
  const [edit, setEdit] = useState<EditResult | null>(null);
  const [testimony, setTestimony] = useState<{ key: string; outcome: TestimonyOutcome } | null>(null);
  const [removeOnlyKeys, setRemoveOnlyKeys] = useState<Set<string>>(new Set());
  const [stations, setStations] = useState<{ key: string; list: StationOption[] | null; failed: boolean; nonce: number } | null>(null);
  const [locate, setLocate] = useState<LocateState | null>(null);
  const [phoneLocating, setPhoneLocating] = useState(false);
  const [scan, setScan] = useState<{ findingKey: string; total: number; texts: string[] } | null>(null);
  const [rechecked, setRechecked] = useState(false);
  // ADV-4: a recheck re-audits against the profile as it is now — the station
  // answers are fetched again afterwards (a 409 meant the audit's evidence no
  // longer names an entry of the current profile).
  const [stationsNonce, setStationsNonce] = useState(0);
  const highlighted = useRef(false);
  const cardRef = useRef<HTMLDivElement>(null);

  const targets = current?.item?.targets ?? null;
  const targetsSig = targets ? JSON.stringify(targets) : "";
  const currentFindingKey = current?.findingKey ?? null;
  const currentResolver = current?.status === "open" ? (current.item?.resolver ?? null) : null;

  const clearLocate = useCallback(() => {
    if (highlighted.current) {
      locator?.clear();
      highlighted.current = false;
    }
    setLocate(null);
  }, [locator]);

  // A different card: drop the marks, the answer box, the transient error.
  useEffect(() => {
    clearLocate();
    setAnswerOpen(false);
    setAnswerText("");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFindingKey]);

  // "Wohin gehört das?" — its answers are the stations that own the evidence.
  useEffect(() => {
    if (!documentId || !currentFindingKey || currentResolver !== "ask_station") return;
    if (stations?.key === currentFindingKey && stations.nonce === stationsNonce) return;
    let cancelled = false;
    const nonce = stationsNonce;
    setStations({ key: currentFindingKey, list: null, failed: false, nonce });
    listStations(documentKind, documentId, currentFindingKey)
      .then((list) => {
        if (!cancelled) setStations({ key: currentFindingKey, list, failed: false, nonce });
      })
      .catch(() => {
        if (!cancelled) setStations({ key: currentFindingKey, list: [], failed: true, nonce });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId, documentKind, currentFindingKey, currentResolver, stationsNonce]);

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

  // Leaving the surface removes the marks it placed.
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

  const fail = (scope: string, e: unknown) => {
    const code = e instanceof ReviewActionError ? e.code : undefined;
    const status = e instanceof ReviewActionError ? e.status : 0;
    const text =
      code === "resolver_unavailable"
        ? t("delivery.errorUnavailable")
        : code === "rewrite_failed"
          ? t("delivery.errorRewriteFailed")
          : code === "action_not_applicable"
            ? t("delivery.errorNotApplicable")
            : tErrors("generic", { status: status || "—" });
    setError({ scope, text });
  };

  const maybeWalked = (state: ReviewState | null | undefined, stillOpen: number) => {
    if (!documentId || stillOpen > 0 || state?.walked_at) return;
    markWalked(documentKind, documentId)
      .then((r) => applyRefresh({ review_state: r.review_state }, false))
      .catch(() => {});
  };

  const placesChanged = (row: Group1Row | null, changes: SectionChange[]) => {
    const rowTargets = row?.item?.targets ?? null;
    let places = 0;
    if (rowTargets) {
      for (const c of changes) places += Math.max(0, countInText(c.before, rowTargets) - countInText(c.after, rowTargets));
    }
    return places > 0 ? places : changes.length;
  };

  /** One corrector-backed answer: the server edits, re-audits and records an undoable decision. */
  const runEdit = async (
    row: Group1Row,
    action: EditResult["action"],
    busyAs: Busy,
    call: () => Promise<{ changes: SectionChange[]; still_listed: boolean } & ReviewRefresh>,
  ) => {
    if (!documentId || busy) return;
    endPhoneLocate();
    clearLocate();
    setBusy(busyAs);
    setError(null);
    try {
      const res = await call();
      setEdit({
        key: row.findingKey,
        action,
        changes: res.changes,
        stillListed: res.still_listed,
        places: placesChanged(row, res.changes),
      });
      setCurrentKey(row.findingKey);
      applyRefresh(res, res.changes.length > 0);
      if (!res.still_listed) maybeWalked(res.review_state, openRows.length - 1);
    } catch (e) {
      if (action === "taken_out" && e instanceof ReviewActionError && e.status === 409) {
        // Ruling B-1: the removal was refused (stem-only) — the still-listed path.
        setEdit({ key: row.findingKey, action, changes: [], stillListed: true, places: 0 });
      } else {
        fail(row.findingKey, e);
      }
    } finally {
      setBusy(null);
    }
  };

  const runRemove = (row: Group1Row) =>
    runEdit(row, "taken_out", "remove", () => takeOut(documentKind, documentId!, row.findingKey));
  const runMove = (row: Group1Row, stationId: string) =>
    runEdit(row, "moved", "move", () => moveToStation(documentKind, documentId!, row.findingKey, stationId));
  const runAsGoal = (row: Group1Row) =>
    runEdit(row, "as_goal", "goal", () => rewriteAsGoal(documentKind, documentId!, row.findingKey));

  /** "Ja, und zwar bei …" — testimony through the reconcile door (ADR-061). */
  const runAnswer = async (row: Group1Row) => {
    if (!documentId || busy || !answerText.trim()) return;
    setBusy("answer");
    setError(null);
    try {
      const res = await addEvidence(documentKind, documentId, row.findingKey, answerText.trim());
      setTestimony({ key: row.findingKey, outcome: res.testimony });
      setAnswerOpen(false);
      setAnswerText("");
      setCurrentKey(row.findingKey);
      if (res.remove_only) setRemoveOnlyKeys((s) => new Set(s).add(row.findingKey));
      applyRefresh(res, false);
      if (res.still_listed === false) maybeWalked(res.review_state, openRows.length - 1);
    } catch (e) {
      fail(row.findingKey, e);
    } finally {
      setBusy(null);
    }
  };

  /** Unclassified fallback (a legacy report): today's "Stimmt" — no ask when the profile holds it. */
  const runLegacyConfirm = async (row: Group1Row) => {
    if (!documentId || busy) return;
    setBusy("answer");
    setError(null);
    try {
      const res = await confirmSupported(documentKind, documentId, row.findingKey);
      if (res === null) {
        setAnswerOpen(true);
        return;
      }
      setCurrentKey(row.findingKey);
      applyRefresh(res, false);
    } catch (e) {
      fail(row.findingKey, e);
    } finally {
      setBusy(null);
    }
  };

  const runUndo = async (key: string, scope: string) => {
    if (!documentId || busy) return;
    setBusy("undo");
    setError(null);
    try {
      const res = await undoDecision(documentKind, documentId, key);
      setEdit(null);
      setTestimony(null);
      applyRefresh(res, true);
    } catch (e) {
      fail(scope, e);
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
    setEdit(null);
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
        if (o.error_code === "not_saved" && tProfile.has("testimony.statusNotSaved")) {
          return tProfile("testimony.statusNotSaved");
        }
        return tProfile("testimony.statusError", { detail: o.detail ?? "" });
      default:
        return "";
    }
  };

  const currentScan = scan && current && scan.findingKey === current.findingKey ? scan : null;
  const currentLocate = locate && current && locate.findingKey === current.findingKey ? locate : null;
  const currentEdit = edit && current && edit.key === current.findingKey ? edit : null;
  const currentTestimony = testimony && current && testimony.key === current.findingKey ? testimony.outcome : null;

  /* ---------------------------------------------------- answer buttons */

  const answerBtn =
    "flex w-full items-center gap-2.5 min-h-11 rounded-xl border border-outline-variant bg-surface-bright px-3 py-2 text-left text-[13px] font-semibold text-on-surface hover:bg-surface-container disabled:opacity-50";
  const sub = "block text-xs font-normal text-on-surface-variant";
  const spin = <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />;

  const removeButton = (row: Group1Row, labelKey: "remove" | "noRemove", withSub: boolean) =>
    isStemOnly(row.item?.targets) ? null : (
      <button
        type="button"
        data-testid="review-answer-remove"
        disabled={busy !== null}
        aria-busy={busy === "remove"}
        onClick={() => void runRemove(row)}
        className={`${answerBtn} text-critical`}
      >
        {busy === "remove" ? spin : <Trash2 className="h-4 w-4 shrink-0" aria-hidden="true" />}
        <span>
          {t(`delivery.${labelKey}`)}
          {withSub && <span className={sub}>{t(isLetter ? "delivery.removeSubLetter" : "delivery.removeSubCv")}</span>}
        </span>
      </button>
    );

  /** The question a resolver asks, and its answers (ADR-099 cl. 6). */
  const question = (row: Group1Row): { q: string | null; why: string | null; answers: ReactNode } => {
    const resolver = row.item?.resolver ?? null;
    const oracleWhy = row.item ? itemOracleDetail(row.item, tOracle) : null;
    const yesBox = (labelKey: "yesWhere" | "yesAchieved", subKey: "yesWhereSub" | "yesAchievedSub") => (
      <button
        type="button"
        data-testid="review-answer-yes"
        disabled={busy !== null}
        onClick={() => {
          endPhoneLocate();
          setAnswerOpen(true);
        }}
        className={answerBtn}
      >
        <Check className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>
          {t(`delivery.${labelKey}`)}
          <span className={sub}>{t(`delivery.${subKey}`)}</span>
        </span>
      </button>
    );

    if (resolver === "ask_station") {
      const own = stations && stations.key === row.findingKey ? stations : null;
      return {
        q: t("delivery.qStation"),
        why: t(isLetter ? "delivery.whyStationLetter" : "delivery.whyStationCv"),
        answers: (
          <>
            {own?.list === null && (
              <p data-testid="review-stations-loading" className="text-xs text-on-surface-variant">
                {t("delivery.stationsLoading")}
              </p>
            )}
            {own?.list?.map((s) => (
              <button
                key={s.station_id}
                type="button"
                data-testid="review-answer-station"
                data-station-id={s.station_id}
                disabled={busy !== null}
                aria-busy={busy === "move"}
                onClick={() => void runMove(row, s.station_id)}
                className={answerBtn}
              >
                {busy === "move" ? spin : <CornerDownRight className="h-4 w-4 shrink-0" aria-hidden="true" />}
                <span>
                  {[s.title, s.organization].filter(Boolean).join(" · ")}
                  <span className={sub}>{t(isLetter ? "delivery.stationSubLetter" : "delivery.stationSubCv")}</span>
                </span>
              </button>
            ))}
            {own && own.list !== null && own.list.length === 0 && !own.failed && (
              <p data-testid="review-stations-none" className="text-xs text-on-surface-variant">
                {t("delivery.stationsNone")}
              </p>
            )}
            {own?.failed && (
              <>
                <p data-testid="review-stations-unsure" className="text-xs text-on-surface-variant">
                  {t("delivery.stationsUnsure")}
                </p>
                <button
                  type="button"
                  data-testid="review-stations-recheck"
                  disabled={busy !== null || !documentId}
                  onClick={() => void runRecheck()}
                  className={answerBtn}
                >
                  {busy === "recheck" ? spin : <RefreshCw className="h-4 w-4 shrink-0" aria-hidden="true" />}
                  <span>{t("delivery.recheck")}</span>
                </button>
              </>
            )}
            {removeButton(row, "remove", true)}
          </>
        ),
      };
    }

    if (resolver === "ask_true") {
      const onlyRemove = removeOnlyKeys.has(row.findingKey) || removeOnly(row);
      if (onlyRemove) {
        // U-13 (D-10 generalised to every class-A "Stimmt das?" card): the
        // answer changed nothing, and the re-audit of the card's own producer
        // still lists it. "Entfernen" must never be the only exit for a true
        // statement — the profile, in the person's words, plus a recheck is
        // the action that CAN clear it.
        const skill = noChangeExit(row) === "skill";
        // U-13 (delivery run 5): ONE statement of the state — the profile names
        // it in other words, or it does not name it at all (never both).
        const found = noChangeFound(row, testimony && testimony.key === row.findingKey ? testimony.outcome.status : null);
        const whyKey = skill
          ? found ? "delivery.skillOtherWords" : "delivery.skillNotFound"
          : found ? "delivery.factOtherWords" : "delivery.factNotFound";
        return {
          q: t("delivery.qTrue"),
          why: t(whyKey, { skill: row.label }),
          answers: (
            <>
              <Link href="/profile" data-testid="review-nochange-profile" className={answerBtn}>
                <CornerDownRight className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span>
                  {t("delivery.skillToProfile")}
                  <span className={sub}>{t(skill ? "delivery.skillToProfileSub" : "delivery.factToProfileSub")}</span>
                </span>
              </Link>
              <button
                type="button"
                data-testid="review-nochange-recheck"
                disabled={busy !== null || !documentId}
                onClick={() => void runRecheck()}
                className={answerBtn}
              >
                {busy === "recheck" ? spin : <RefreshCw className="h-4 w-4 shrink-0" aria-hidden="true" />}
                <span>{t("delivery.recheck")}</span>
              </button>
              {removeButton(row, "remove", false)}
            </>
          ),
        };
      }
      return {
        q: t("delivery.qTrue"),
        why: oracleWhy,
        answers: (
          <>
            {yesBox("yesWhere", "yesWhereSub")}
            {removeButton(row, "noRemove", false)}
          </>
        ),
      };
    }

    if (resolver === "ask_achieved") {
      return {
        q: t("delivery.qAchieved"),
        why: oracleWhy,
        answers: (
          <>
            {yesBox("yesAchieved", "yesAchievedSub")}
            <button
              type="button"
              data-testid="review-answer-as-goal"
              disabled={busy !== null}
              aria-busy={busy === "goal"}
              onClick={() => void runAsGoal(row)}
              className={answerBtn}
            >
              {busy === "goal" ? spin : <RotateCcw className="h-4 w-4 shrink-0" aria-hidden="true" />}
              <span>
                {t("delivery.asGoal")}
                <span className={sub}>{t("delivery.asGoalSub")}</span>
              </span>
            </button>
          </>
        ),
      };
    }

    if (resolver === "remove_only") {
      const why = row.item?.employerSentence
        ? t("cardEmployerSentence", { term: row.label })
        : row.item?.denials && row.item.denials.length > 0
          ? t("cardDenied", { denials: row.item.denials.join(t("cardMatchedJoin")) })
          : oracleWhy;
      return { q: t("delivery.qRemove"), why, answers: removeButton(row, "remove", false) };
    }

    // Unclassified (a report persisted before ADR-099, or a kind the registry
    // does not know): today's handles — the Oracle's keyed detail, "Stimmt",
    // "Nimm es für mich heraus" (ruling C-1 (2): no editor handle on the card).
    const canAdd = !(row.item?.denials && row.item.denials.length > 0) && !row.item?.employerSentence;
    const why = row.item?.employerSentence
      ? t("cardEmployerSentence", { term: row.label })
      : row.item?.denials && row.item.denials.length > 0
        ? t("cardDenied", { denials: row.item.denials.join(t("cardMatchedJoin")) })
        : oracleWhy;
    return {
      q: null,
      why,
      answers: (
        <>
          {canAdd && (
            <button
              type="button"
              data-testid="review-action-add"
              disabled={busy !== null}
              onClick={() => {
                endPhoneLocate();
                void runLegacyConfirm(row);
              }}
              className={answerBtn}
            >
              <Plus className="h-4 w-4 shrink-0" aria-hidden="true" />
              {t("actionAddToProfile")}
            </button>
          )}
          {!isStemOnly(row.item?.targets) && (
            <button
              type="button"
              data-testid="review-action-takeout"
              disabled={busy !== null}
              aria-busy={busy === "remove"}
              onClick={() => void runRemove(row)}
              className={answerBtn}
            >
              {busy === "remove" ? spin : <Trash2 className="h-4 w-4 shrink-0" aria-hidden="true" />}
              {t("actionTakeOut")}
            </button>
          )}
        </>
      ),
    };
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

  const editDoneKey: Record<EditResult["action"], string> = {
    taken_out: "",
    moved: "delivery.movedDone",
    as_goal: "delivery.asGoalDone",
    translated: "delivery.translateDone",
    shortened: "delivery.letterPageDone",
  };

  const renderChanges = (changes: SectionChange[]) =>
    changes.map((c, i) => (
      <div
        key={`${c.section_id}-${i}`}
        data-testid="review-takeout-change"
        className="flex flex-col gap-1.5 rounded-lg bg-surface-container px-3 py-2.5"
      >
        <span className="text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
          {sectionLabel ? sectionLabel(c.section_id) : c.section_id}
        </span>
        {changedPassages(c.before, c.after).map((h, k) => (
          <div key={k} data-testid="review-takeout-hunk" className="flex flex-col gap-1">
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            {h.elidedBefore && <span aria-hidden="true" className="text-xs text-on-surface-variant">…</span>}
            {h.before.map((u, x) => (
              <span key={`b${x}`} data-testid="review-takeout-before" className="text-xs leading-snug text-on-surface-variant line-through">
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
    ));

  // Distinct wordings, case-folded, in the catalog's quote convention (#797).
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
    return out.slice(0, 3).join(t("cardMatchedJoin"));
  };

  const renderCard = (row: Group1Row) => {
    const decided = row.status !== "open";
    const notMarkable = !decided && (!row.item?.targets || (currentScan !== null && currentScan.total === 0));
    const { q, why, answers } = decided ? { q: null, why: null, answers: null } : question(row);
    const border = decided ? "border-success" : "border-primary";
    const prompt = row.item?.resolver === "ask_achieved" ? t("delivery.achievedPrompt") : row.item?.resolver ? t("delivery.truePrompt") : t("addPrompt");
    return (
      <div
        ref={cardRef}
        data-testid="review-card"
        data-finding-key={row.findingKey}
        data-status={row.status}
        data-resolver={row.item?.resolver ?? "none"}
        aria-busy={busy !== null}
        className={`flex flex-col gap-2.5 rounded-xl border-2 ${border} bg-surface-bright p-3.5 shadow-sm`}
      >
        <span data-testid="review-card-position" className="text-[11px] font-bold tracking-wide text-on-surface-variant">
          {t("cardPosition", { index: currentIndex + 1, total: rows.length })}
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          {" · "}
          <span className="uppercase">{t("delivery.cardKind")}</span>
        </span>
        <span data-testid="review-card-claim" className="rounded-lg bg-surface-container px-2.5 py-2 text-sm leading-snug text-on-surface">
          {t("delivery.quoted", { text: row.label })}
        </span>

        {decided && !currentEdit && STATUS_KEY[row.status] && (
          <span data-testid="review-card-status" className="text-[13px] font-semibold text-success">
            {t(STATUS_KEY[row.status])}
          </span>
        )}

        {q && (
          <span data-testid="review-card-question" className="font-heading text-[16.5px] font-bold text-on-surface">
            {q}
          </span>
        )}
        {!decided && why && (
          <span data-testid="review-card-why" className="text-[13px] leading-snug text-on-surface-variant">
            {why}
          </span>
        )}
        {!decided && !why && currentScan && currentScan.total > 0 && (
          <span data-testid="review-card-matched" className="text-[13px] leading-snug text-on-surface-variant">
            {t("cardMatched", { wording: wordingOf(currentScan.texts), count: currentScan.total })}
          </span>
        )}

        {currentEdit && (
          <div className="flex flex-col gap-2" data-testid="review-takeout-result" data-action={currentEdit.action}>
            <span
              data-testid={currentEdit.stillListed ? "review-takeout-still" : "review-takeout-done"}
              className={`text-[13px] font-semibold ${currentEdit.stillListed ? "text-critical" : "text-success"}`}
            >
              {currentEdit.stillListed
                ? currentEdit.action === "taken_out"
                  ? t("takenOutStillFlagged")
                  : t("delivery.stillListed")
                : currentEdit.action === "taken_out"
                  ? t("takenOutDone", { count: currentEdit.places })
                  : t(editDoneKey[currentEdit.action])}
            </span>
            {renderChanges(currentEdit.changes)}
          </div>
        )}

        {currentTestimony && !(row.item?.resolver === "ask_true" && !decided && (removeOnlyKeys.has(row.findingKey) || removeOnly(row))) && (
          <p
            data-testid="review-testimony-result"
            data-status={currentTestimony.status}
            className={`text-[13px] ${currentTestimony.status === "error" ? "text-critical" : "text-on-surface"}`}
          >
            {testimonyMessage(currentTestimony)}
          </p>
        )}

        {!decided && answerOpen && (
          <div data-testid="review-add-box" className="flex flex-col gap-2">
            <label htmlFor={`review-add-${row.findingKey}`} className="text-[13px] font-semibold text-on-surface">
              {prompt}
            </label>
            <textarea
              id={`review-add-${row.findingKey}`}
              data-testid="review-add-text"
              value={answerText}
              onChange={(e) => setAnswerText(e.target.value)}
              disabled={busy !== null}
              rows={3}
              className="w-full rounded-lg border border-outline-variant bg-surface-bright px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
            />
            <div className="flex gap-2">
              <button
                type="button"
                data-testid="review-add-submit"
                disabled={busy !== null || !answerText.trim()}
                onClick={() => void runAnswer(row)}
                className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-white disabled:opacity-50"
              >
                {busy === "answer" && spin}
                {t("addSubmit")}
              </button>
              <button
                type="button"
                data-testid="review-add-cancel"
                disabled={busy !== null}
                onClick={() => {
                  setAnswerOpen(false);
                  setAnswerText("");
                }}
                className="min-h-11 rounded-full border border-outline-variant bg-surface-bright px-4 text-sm font-semibold text-on-surface"
              >
                {t("addCancel")}
              </button>
            </div>
          </div>
        )}

        {!decided && !answerOpen && !(currentEdit && !currentEdit.stillListed) && (
          <div className="flex flex-col gap-2" data-testid="review-answers">
            {answers}
          </div>
        )}

        {/* Ruling C-1 (2): "Zeig mir, wo" stays on the card, as a link. */}
        {!decided && !currentLocate && !notMarkable && !answerOpen && (
          <button
            type="button"
            data-testid="review-action-locate"
            disabled={busy !== null}
            onClick={() => showPlace(0)}
            className="self-start text-[13px] font-semibold text-primary underline underline-offset-2 disabled:opacity-50"
          >
            {t("actionShowWhere")}
          </button>
        )}
        {notMarkable && (
          <span data-testid="review-locate-not-found" className="text-xs leading-snug text-on-surface-variant">
            {t("locateNotFound")}
          </span>
        )}

        {!decided && currentLocate && currentLocate.total > 0 && layout === "panel" && (
          <div data-testid="review-locate-stepper" className="flex items-center gap-2 rounded-lg bg-gold-container py-1.5 pl-3 pr-1.5">
            <span className="flex-1 text-[13px] font-semibold text-on-surface" aria-live="polite">
              {t("locateShowing", { index: currentLocate.index + 1, total: currentLocate.total })}
            </span>
            {stepper(currentLocate, "card")}
          </div>
        )}

        {(decided || currentEdit) && (
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
            {row.decision?.undo && (decided || currentEdit) && (
              <button
                type="button"
                data-testid="review-action-undo"
                disabled={busy !== null}
                onClick={() => void runUndo(row.findingKey, row.findingKey)}
                className="flex min-h-11 items-center gap-2 whitespace-nowrap rounded-full border border-outline-variant bg-surface-bright px-4 text-sm font-semibold text-on-surface disabled:opacity-50"
              >
                {busy === "undo" && spin}
                {t("actionUndo")}
              </button>
            )}
            {currentEdit?.stillListed && currentEdit.action === "taken_out" && onEditFinding && (
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

        {error && error.scope === row.findingKey && (
          <p data-testid="review-action-error" role="alert" className="text-[13px] text-critical">
            {error.text}
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
                  <span data-testid="review-locate-place" aria-live="polite" className="text-xs text-on-surface-variant">
                    {t("locatePlace", {
                      wording: currentLocate.texts[currentLocate.index] ?? current.label,
                      index: currentLocate.index + 1,
                      total: currentLocate.total,
                    })}
                  </span>
                </div>
                {stepper(currentLocate, "sheet")}
              </div>
              {current.status === "open" && <div className="flex flex-col gap-2">{question(current).answers}</div>}
            </div>
          </div>,
          document.body,
        )
      : null;

  /* ------------------------------------------------------------ render */

  if (loading) {
    return (
      <section data-testid="review-surface" aria-label={t("title")} aria-busy="true" className="flex flex-col gap-4 p-3">
        <p data-testid="review-loading" role="status" className="text-sm text-on-surface-variant">
          {t("loading")}
        </p>
      </section>
    );
  }

  const runTranslate = async () => {
    if (!documentId || busy) return;
    setBusy("translate");
    setError(null);
    try {
      const res = await translateSpans(documentKind, documentId);
      setEdit({ key: SEND_LANGUAGE_KEY, action: "translated", changes: res.changes, stillListed: res.still_listed, places: res.changes.length });
      applyRefresh(res, res.changes.length > 0);
    } catch (e) {
      fail(SEND_LANGUAGE_KEY, e);
    } finally {
      setBusy(null);
    }
  };

  const runRecheck = async () => {
    if (!documentId || busy) return;
    setBusy("recheck");
    setError(null);
    try {
      const res = await recheckDocument(documentKind, documentId);
      setRechecked(true);
      applyRefresh(res, false);
      setStationsNonce((n) => n + 1);
    } catch (e) {
      fail("recheck", e);
    } finally {
      setBusy(null);
    }
  };

  const runLetterPage = async (choice: "keep" | "cut") => {
    if (!documentId || busy || !isLetter) return;
    setBusy("page");
    setError(null);
    try {
      if (choice === "cut") {
        const res = await shortenLetter(documentId);
        setEdit({ key: PAGE_LENGTH_KEY, action: "shortened", changes: res.changes, stillListed: res.still_listed, places: res.changes.length });
        applyRefresh(res, res.changes.length > 0);
      } else {
        applyRefresh(await keepLetterLength(documentId), false);
      }
    } catch (e) {
      fail(PAGE_LENGTH_KEY, e);
    } finally {
      setBusy(null);
    }
  };

  const producerName = (u: UnknownRow): string => {
    if (u.producer) return t(PRODUCER_LABEL_KEY[u.producer]);
    if (u.check && baseId(u.check.id) === "terminal-review") return t("delivery.producerTerminal");
    if (u.check) {
      const key = `checks.${baseId(u.check.id)}`;
      return t("delivery.producerCheck", { check: tAts.has(key) ? tAts(key) : u.check.id });
    }
    return "";
  };

  const boxClass = "flex flex-col gap-2 rounded-xl border border-outline-variant p-3";
  const primaryBtn =
    "flex min-h-11 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-white disabled:opacity-50";
  const outlineBtn =
    "flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border border-outline-variant bg-surface-bright px-3 text-sm font-semibold text-on-surface disabled:opacity-50";
  // A filled twin of outlineBtn. Never append colour classes to outlineBtn:
  // Tailwind resolves bg-*/text-* conflicts by stylesheet order, not class order
  // (the letter page card once rendered its "kürzen" button white on white).
  const filledBtn =
    "flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl border border-primary bg-primary px-3 text-sm font-semibold text-white disabled:opacity-50";

  const translateEdit = edit && edit.key === SEND_LANGUAGE_KEY ? edit : null;
  const pageEdit = edit && edit.key === PAGE_LENGTH_KEY ? edit : null;
  const translateUndo = reviewState?.decisions.some((d) => d.finding_key === SEND_LANGUAGE_KEY && d.undo);
  const pageUndo = reviewState?.decisions.some((d) => d.finding_key === PAGE_LENGTH_KEY && d.undo);

  return (
    <section data-testid="review-surface" aria-label={t("title")} className="flex flex-col gap-3 p-3">
      {/* #786 / T-2: a review step of the writing loop that did not run (class U, counted). */}
      <ReviewDegradationNote steps={degradation} onRegenerate={onRegenerate} />

      <div className="flex flex-col gap-1">
        <p data-testid="review-verdict" data-count={view.count} className="font-heading text-lg font-bold leading-snug text-on-surface">
          {view.count > 0 ? t("delivery.headline", { count: view.count }) : t("delivery.clear")}
        </p>
        {view.count > 0 && (
          <p data-testid="review-verdict-sub" className="text-sm leading-snug text-on-surface-variant">
            {t("delivery.headlineSub")}
          </p>
        )}
      </div>

      {current && renderCard(current)}

      {/* Ruling C-1 (1): one open card + the list. */}
      {rows.length > 1 && (
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
                      setEdit(null);
                      setTestimony(null);
                    }
                    cardRef.current?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
                  }}
                  className={`flex min-h-10 w-full items-center gap-2.5 border-b border-surface-container px-1 text-left ${
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
                    className={`min-w-0 flex-1 truncate text-sm ${
                      isCurrent ? "font-semibold text-primary" : r.status !== "open" ? "text-on-surface-variant" : "text-on-surface"
                    }`}
                  >
                    {r.label}
                  </span>
                  {r.status !== "open" && STATUS_KEY[r.status] && (
                    <span
                      data-testid={`review-row-status-${r.status}`}
                      className="shrink-0 rounded-full bg-surface-container px-2 py-0.5 text-[11px] font-semibold text-on-surface-variant"
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

      {/* ADR-099 cl. 9 — "Diese Stellen übersetzen". */}
      {view.translate.map((item) => {
        const p = item.check?.details_params ?? {};
        const examples = String(p.examples ?? "")
          .split("; ")
          .filter(Boolean);
        return (
          <div key={item.key} data-testid="review-translate" className={boxClass}>
            <h4 className="font-heading text-[14.5px] font-bold text-on-surface">
              {t("delivery.translateTitle", { count: Number(p.count ?? examples.length), language: String(p.language ?? "") })}
            </h4>
            {examples.length > 0 && (
              <p className="text-[13px] leading-snug text-on-surface-variant">
                {examples.map((x) => t("delivery.quoted", { text: x })).join(" · ")}
              </p>
            )}
            <button type="button" data-testid="review-translate-action" disabled={busy !== null} onClick={() => void runTranslate()} className={primaryBtn}>
              {busy === "translate" && spin}
              {t("delivery.translateAction")}
            </button>
            <p className="text-xs text-on-surface-variant">{t("delivery.translateNote")}</p>
            {error?.scope === SEND_LANGUAGE_KEY && (
              <p data-testid="review-action-error" role="alert" className="text-[13px] text-critical">
                {error.text}
              </p>
            )}
          </div>
        );
      })}
      {translateEdit && (
        <div data-testid="review-translate-result" className={boxClass}>
          <span className={`text-[13px] font-semibold ${translateEdit.stillListed ? "text-critical" : "text-success"}`}>
            {translateEdit.stillListed ? t("delivery.stillListed") : t("delivery.translateDone")}
          </span>
          {renderChanges(translateEdit.changes)}
          {translateUndo && (
            <button type="button" data-testid="review-translate-undo" disabled={busy !== null} onClick={() => void runUndo(SEND_LANGUAGE_KEY, SEND_LANGUAGE_KEY)} className={outlineBtn}>
              {t("actionUndo")}
            </button>
          )}
        </div>
      )}

      {/* ADR-099 cl. 7 — the letter's ONE page-length card (the CV's comes from the page). */}
      {isLetter && view.pageLength && (
        <div data-testid="review-letter-page" className={boxClass}>
          <h4 className="font-heading text-[14.5px] font-bold text-on-surface">
            {t("delivery.letterPageTitle", {
              pages: Number(view.pageLength.check?.details_params?.pages ?? 2),
              target: Number(view.pageLength.check?.details_params?.letterPages ?? 1),
            })}
          </h4>
          <p className="text-[13px] text-on-surface-variant">
            {t("delivery.letterPageBody", { target: Number(view.pageLength.check?.details_params?.letterPages ?? 1) })}
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" data-testid="review-letter-page-keep" disabled={busy !== null} onClick={() => void runLetterPage("keep")} className={outlineBtn}>
              {tCv("pageOverflowKeep", { actual: Number(view.pageLength.check?.details_params?.pages ?? 2) })}
            </button>
            <button
              type="button"
              data-testid="review-letter-page-cut"
              disabled={busy !== null}
              onClick={() => void runLetterPage("cut")}
              className={filledBtn}
            >
              {busy === "page" && spin}
              {tCv("pageOverflowCut", { target: Number(view.pageLength.check?.details_params?.letterPages ?? 1) })}
            </button>
          </div>
          <p className="text-xs text-on-surface-variant">{tCv("pageOverflowNote")}</p>
          {error?.scope === PAGE_LENGTH_KEY && (
            <p data-testid="review-action-error" role="alert" className="text-[13px] text-critical">
              {error.text}
            </p>
          )}
        </div>
      )}
      {pageEdit && (
        <div data-testid="review-letter-page-result" className={boxClass}>
          <span className={`text-[13px] font-semibold ${pageEdit.stillListed ? "text-critical" : "text-success"}`}>
            {pageEdit.stillListed ? t("delivery.stillListed") : t("delivery.letterPageDone")}
          </span>
          {renderChanges(pageEdit.changes)}
          {pageUndo && (
            <button type="button" data-testid="review-letter-page-undo" disabled={busy !== null} onClick={() => void runUndo(PAGE_LENGTH_KEY, PAGE_LENGTH_KEY)} className={outlineBtn}>
              {t("actionUndo")}
            </button>
          )}
        </div>
      )}

      {extraCards.map((c) => (
        <div key={c.key} data-testid={`review-extra-${c.key}`} data-counted={c.counted ? "true" : "false"}>
          {c.node}
        </div>
      ))}

      {/* ADR-099 cl. 2/9 — class U: "Nicht geprüft" never collapses. */}
      {view.unknown.map((u) => (
        <div key={u.key} data-testid="review-unknown" data-resolver={u.resolver} className={`${boxClass} border-dashed`}>
          <h4 className="font-heading text-[14.5px] font-bold text-on-surface">{t(UNKNOWN_TITLE_KEY[unknownState(u)], { producer: producerName(u) })}</h4>
          <p data-testid="review-unknown-body" data-state={unknownState(u)} className="text-[13px] leading-snug text-on-surface-variant">
            {unknownState(u) !== "notRun"
              ? t(UNKNOWN_BODY_KEY[unknownState(u) as Exclude<UnknownState, "notRun">])
              : u.resolver === "regenerate"
                ? u.check && baseId(u.check.id) === "terminal-review"
                  ? t("delivery.terminalNotRunBody")
                  : t("delivery.writingOnlyBody")
                : rechecked
                  ? t("delivery.recheckStill")
                  : t("delivery.unknownBody")}
          </p>
          {u.resolver === "regenerate" ? (
            onRegenerate && (
              <button type="button" data-testid="review-unknown-regenerate" onClick={onRegenerate} className={primaryBtn}>
                {t("delivery.regenerate")}
              </button>
            )
          ) : (
            <button type="button" data-testid="review-unknown-recheck" disabled={busy !== null || !documentId} onClick={() => void runRecheck()} className={primaryBtn}>
              {busy === "recheck" && spin}
              {t("delivery.recheck")}
            </button>
          )}
        </div>
      ))}
      {error?.scope === "recheck" && (
        <p data-testid="review-action-error" role="alert" className="text-[13px] text-critical">
          {error.text}
        </p>
      )}

      {/* An unclassified (or unregistered) failed check: today's row — its label
          and a KEYED detail only (cl. 12: never the server's prose). */}
      {view.fallback.length > 0 && (
        <ul data-testid="review-fallback" className="flex flex-col gap-1">
          {view.fallback.map(({ item, counted }) => (
            <FallbackRow key={item.key} item={item} counted={counted} />
          ))}
        </ul>
      )}

      {/* Group 3 — the link out (uncounted, as approved). */}
      {gapAnalysisHref && !group3.unknown && group3.items.length > 0 && (
        <Link
          href={gapAnalysisHref}
          data-testid="review-group3-link"
          className="flex min-h-11 w-full items-center gap-2.5 rounded-xl border border-outline-variant bg-surface-bright px-3 text-left"
        >
          <span className="flex min-w-0 flex-1 flex-col py-1.5">
            <span className="text-[13px] text-on-surface">{t("group3Title")}</span>
            <span className="text-xs font-semibold text-primary">{t("group3Link")}</span>
          </span>
          <span data-testid="review-group-count-3" className="shrink-0 rounded-full bg-surface-container px-2 py-0.5 text-[11px] font-bold text-on-surface">
            {group3.items.length}
          </span>
          <ChevronRight className="h-4 w-4 shrink-0 text-on-surface-variant" aria-hidden="true" />
        </Link>
      )}

      {/* ADR-099 cl. 3 — group 4 is ONE passed line; a U item never enters it. */}
      {atsReport && view.passed > 0 && (
        <p data-testid="review-passed-checks" className="flex items-center gap-1.5 text-[13px] text-success">
          <Check className="h-4 w-4" aria-hidden="true" />
          {t("delivery.passed", { count: view.passed })}
        </p>
      )}

      <RemovalLine
        removals={view.removals}
        documentKind={documentKind}
        documentId={documentId}
        reviewState={reviewState}
        sectionLabel={sectionLabel}
        onRefresh={(r) => applyRefresh(r, false)}
        testimonyMessage={testimonyMessage}
      />

      {children}
      {phoneLocate}
    </section>
  );
}

/* ---------------------------------------------------------- fallback row */

function FallbackRow({ item, counted }: { item: ReviewItem; counted: boolean }) {
  const tAts = useTranslations("ats");
  const c = item.check as ATSCheck | undefined;
  const key = c ? `checks.${baseId(c.id)}` : "";
  const label = c ? (tAts.has(key) ? tAts(key) : c.id) : item.label;
  const detail = c && usesLocalizedDetail(c) ? tAts(`checkDetails.${c.details_key}`, c.details_params ?? undefined) : null;
  return (
    <li data-testid={`review-fallback-${c?.id ?? item.key}`} data-counted={counted ? "true" : "false"} className="flex items-start gap-2 px-1 py-1.5">
      <span aria-hidden="true" className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${counted ? "bg-critical" : "bg-outline-variant"}`} />
      <span className="min-w-0 flex-1">
        <span className="block text-sm text-on-surface">{label}</span>
        {detail && <span className="block text-xs text-on-surface-variant">{detail}</span>}
      </span>
    </li>
  );
}

/* --------------------------------------------------------- removal line */

/**
 * ADR-099 cl. 5 (UAT-3i) — every claim the loop removed or narrowed because the
 * profile does not back it, in ONE collapsed, uncounted line. Opened, each item
 * has ONE exit "Doch, stimmt – wo?" → testimony (ADR-061). A removal that hits
 * a pinned fact opens the line by itself (ADR-077 cl. 2).
 */
function RemovalLine({
  removals,
  documentKind,
  documentId,
  reviewState,
  sectionLabel,
  onRefresh,
  testimonyMessage,
}: {
  removals: DeliveryRemoval[];
  documentKind: ReviewDocumentKind;
  documentId: string | null;
  reviewState: ReviewState | null;
  sectionLabel?: (sectionId: string) => string;
  onRefresh: (r: ReviewRefresh) => void;
  testimonyMessage: (o: TestimonyOutcome) => string;
}) {
  const t = useTranslations("documentReview");
  const tErrors = useTranslations("errors");
  const pinned = removals.some((r) => r.pinned);
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<Record<string, TestimonyOutcome>>({});
  // D-9: the stored answer the action returned (with the producer's re-check).
  const [answers, setAnswers] = useState<NonNullable<ReviewState["removal_answers"]>>({});
  const [error, setError] = useState<string | null>(null);
  if (removals.length === 0) return null;
  const expanded = open || pinned;

  const submit = async (r: DeliveryRemoval) => {
    if (!documentId || busy || !text.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await removalEvidence(documentKind, documentId, r.text, text.trim());
      setResults((m) => ({ ...m, [normQuote(r.text)]: res.testimony }));
      setAnswers((m) => ({ ...m, ...(res.review_state?.removal_answers ?? {}) }));
      setAsking(null);
      setText("");
      onRefresh({ review_state: res.review_state });
    } catch (e) {
      setError(tErrors("generic", { status: e instanceof ReviewActionError ? e.status || "—" : "—" }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="review-removals" data-expanded={expanded ? "true" : "false"} className="flex flex-col gap-1.5 border-t border-outline-variant pt-2">
      <button
        type="button"
        data-testid="review-removals-toggle"
        aria-expanded={expanded}
        disabled={pinned}
        onClick={() => setOpen((o) => !o)}
        className="flex items-start gap-2 text-left text-[13px] text-on-surface-variant"
      >
        <ChevronDown className={`mt-0.5 h-4 w-4 shrink-0 transition-transform ${expanded ? "" : "-rotate-90"}`} aria-hidden="true" />
        <span>{t("delivery.removalLine", { count: removals.length })}</span>
      </button>
      {expanded &&
        removals.map((r) => {
          const fold = normQuote(r.text);
          const answered = results[fold] ?? null;
          const stored = answers[fold] ?? reviewState?.removal_answers?.[fold];
          return (
            <div key={fold} data-testid="review-removal-item" data-pinned={r.pinned ? "true" : "false"} className="flex flex-col gap-1.5 rounded-lg border border-outline-variant px-2.5 py-2">
              <span className="text-[13px] text-on-surface line-through decoration-outline-variant">{t("delivery.quoted", { text: r.text })}</span>
              <span className="text-[11.5px] text-on-surface-variant">
                {r.pinned && (
                  <span className="mr-1.5 inline-block rounded-full border border-warning/40 bg-warning-container px-1.5 text-[11px] font-bold text-on-surface">
                    {t("delivery.removalPinned")}
                  </span>
                )}
                {[r.section && sectionLabel ? sectionLabel(r.section) : null, r.narrowed_to ? t("delivery.removalNarrowed", { text: r.narrowed_to }) : null]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              {stored && typeof stored.backed === "boolean" ? (
                // D-9: what the removal's producer says on the profile as it now stands.
                <p data-testid="review-removal-result" data-status={stored.status} data-backed={stored.backed ? "true" : "false"} className={`text-[13px] ${stored.backed ? "text-success" : "text-on-surface"}`}>
                  {stored.backed ? (
                    t("delivery.removalBackedNow", { text: r.text })
                  ) : (
                    <>
                      {t("delivery.removalNotBacked", { text: r.text })}{" "}
                      <Link href="/profile" data-testid="review-removal-profile" className="font-semibold text-primary underline underline-offset-2">
                        {t("delivery.removalOpenProfile")}
                      </Link>
                    </>
                  )}
                </p>
              ) : answered ? (
                <p data-testid="review-removal-result" data-status={answered.status} className="text-[13px] text-on-surface">
                  {testimonyMessage(answered)}
                </p>
              ) : stored && (stored.status === "applied" || stored.status === "partial") ? (
                <p data-testid="review-removal-result" data-status={stored.status} className="text-[13px] text-success">
                  {t("statusAdded")}
                </p>
              ) : stored && stored.status !== "error" ? (
                // D-9: an answer that changed nothing stays answered after a reload.
                <p data-testid="review-removal-result" data-status={stored.status} className="text-[13px] text-on-surface">
                  {t("delivery.removalAnsweredNoChange")}
                </p>
              ) : asking === fold ? (
                <div className="flex flex-col gap-2">
                  <label htmlFor={`removal-${fold}`} className="text-[13px] font-semibold text-on-surface">
                    {t("delivery.removalPrompt")}
                  </label>
                  <textarea
                    id={`removal-${fold}`}
                    data-testid="review-removal-text"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    disabled={busy}
                    rows={3}
                    className="w-full rounded-lg border border-outline-variant bg-surface-bright px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      data-testid="review-removal-submit"
                      disabled={busy || !text.trim()}
                      onClick={() => void submit(r)}
                      className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-white disabled:opacity-50"
                    >
                      {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                      {t("addSubmit")}
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setAsking(null);
                        setText("");
                      }}
                      className="min-h-11 rounded-full border border-outline-variant bg-surface-bright px-4 text-sm font-semibold text-on-surface"
                    >
                      {t("addCancel")}
                    </button>
                  </div>
                  {error && (
                    <p role="alert" className="text-[13px] text-critical">
                      {error}
                    </p>
                  )}
                </div>
              ) : (
                <button
                  type="button"
                  data-testid="review-removal-where"
                  disabled={!documentId}
                  onClick={() => {
                    setAsking(fold);
                    setText("");
                  }}
                  className="flex min-h-10 items-center justify-center rounded-xl border border-outline-variant bg-surface-bright px-3 text-[13px] font-semibold text-on-surface"
                >
                  {t("delivery.removalWhere")}
                </button>
              )}
            </div>
          );
        })}
    </div>
  );
}

/**
 * The Oracle's detail in the UI language — KEYED only. ADR-099 cl. 12: a detail
 * whose key the catalog does not hold never renders the server's prose.
 */
function itemOracleDetail(item: ReviewItem, tOracle: OracleDetailTranslator): string | null {
  if (!item.detailKey || !tOracle.has(item.detailKey)) return null;
  return oracleDetailText({ detail: null, detail_key: item.detailKey, detail_params: item.detailParams }, tOracle);
}
