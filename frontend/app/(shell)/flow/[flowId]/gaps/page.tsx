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


import { useEffect, useRef, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { use } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScoreCircle } from "@/components/ui/score-circle";
import { StatCard } from "@/components/ui/stat-card";
import { JobEchoCard } from "@/components/gaps/JobEchoCard";
import { DocumentLanguageControl } from "@/components/gaps/DocumentLanguageControl";
import { CancelApplicationButton } from "@/components/flow/CancelApplicationButton";
import { PinnedFactsPanel } from "@/components/pins/PinnedFactsPanel";
import { cn } from "@/lib/utils";
import { GapClusterCard } from "@/components/gaps/GapClusterCard";
import { LiabilityPanel, type LiabilityEntry } from "@/components/gaps/LiabilityPanel";
import { ProfileDecisionsCard } from "@/components/gaps/ProfileDecisionsCard";
import { getProfileChanges, hasMergeReview, type ProfileChanges } from "@/lib/api/review";
import { analyzeGapsAsync, GapAnalysisError } from "@/lib/gap-analysis";
import {
  canonicalRequirementChips,
  clusterCoverageCounts,
  clusterView,
  gapCounts,
  isGapRefusalCode,
  scoreToPercent,
  type GapCluster,
  type GapRefusalCode,
  type LedgerChipEntry,
  type TurnClusterCoverage,
} from "@/lib/match-utils";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface GapAnalysis {
  id: string;
  match_score: number;
  category_a: string[];
  category_b: string[];
  category_c: string[];
  strengths: string[];
  gap_clusters: GapCluster[];
  // ADR-048 fit-slice — the canonical requirement set all counts derive from (#111)
  keyword_ledger?: LedgerChipEntry[];
  // #260 — derived server-side: required + claimable + no narrative anywhere.
  keyword_liabilities?: LiabilityEntry[];
}

interface FlowState {
  job_id: string | null;
  user_type: "new" | "returning";
  available_actions: Record<string, string>;
  gap_summary?: { gap_analysis_id: string } | null;
  job_summary?: { role_title: string } | null;
  /** Linked Application — enables the walk-away action (US222, Branch I). */
  application_id?: string | null;
  /** Flow creation time — scopes the merge pointer to THIS run's imports. */
  created_at?: string | null;
  /** #580: feeds PinnedFactsPanel's per-document fate markers. */
  cv_summary?: { cv_id: string } | null;
  cover_letter_summary?: { cover_letter_id: string } | null;
}

interface ProfileStats {
  positions: number;
  projects: number;
  certifications: number;
  data_points: number;
}

// Gap-Click micro-session state per cluster. There is no "resolved" status:
// whether a gap is covered is the server's record (ADR-089 clause 8), read
// from the analysis row — this state only tracks the open conversation.
// Anything but "idle" holds the page's one micro-session (clause 8: while one
// card has it, no other card may open one).
type GapStatus = "idle" | "loading" | "question" | "answering" | "refreshing";

interface GapClickState {
  status: GapStatus;
  sessionId: string | null;
  question: string | null;
  choices: string[] | null;
  answer: string;
  sending: boolean;
  error: string;
  /** The open members a follow-up turn is aimed at (contract item 4); null on
   * an opening question or a confirmation question. */
  followUpOpen: string[] | null;
  /** The record the last turn wrote (`cluster_coverage`), shown until the page
   * re-reads the analysis row that carries it. */
  overlay: TurnClusterCoverage | null;
  /** `POST /api/session` refused this cluster (HTTP 409, contract item 5). */
  refusal: GapRefusalCode | null;
}

/** The slice of `SessionMessageResponse` the gaps page reads. */
interface SessionTurnResponse {
  complete: boolean;
  question?: string | null;
  choices?: string[] | null;
  pending_conflicts?: unknown[] | null;
  pending_confirmations?: unknown[] | null;
  cluster_coverage?: TurnClusterCoverage | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function apiErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ");
    return res.statusText || `HTTP ${res.status}`;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

const EMPTY_GAP_STATE: GapClickState = {
  status: "idle",
  sessionId: null,
  question: null,
  choices: null,
  answer: "",
  sending: false,
  error: "",
  followUpOpen: null,
  overlay: null,
  refusal: null,
};

/** The contract's 409 `error_code`, when the body carries one. */
async function refusalCodeOf(res: Response): Promise<GapRefusalCode | null> {
  try {
    const body = await res.clone().json();
    const code = body?.detail?.error_code;
    return isGapRefusalCode(code) ? code : null;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// JD Recovery Banner — shown when jd_status query param is present (Sprint 26)
// ---------------------------------------------------------------------------

function JdRecoveryBannerInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const t = useTranslations("gaps");
  const tc = useTranslations("common");
  const [dismissed, setDismissed] = useState(false);

  const jdStatus = searchParams.get("jd_status");

  if (!jdStatus || dismissed) return null;

  const copy =
    jdStatus === "url_invalid"
      ? t("jdMissingBannerUrl")
      : t("jdMissingBannerFetch");

  return (
    <div
      data-testid="jd-recovery-banner"
      className="mb-6 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3"
    >
      <svg
        className="mt-0.5 h-4 w-4 shrink-0 text-amber-600"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
        />
      </svg>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-amber-800">{copy}</p>
        <button
          data-testid="jd-recovery-cta"
          type="button"
          className="mt-1 text-sm font-medium text-amber-700 underline hover:no-underline"
          onClick={() => router.push("/dashboard")}
        >
          {t("addJobDescription")}
        </button>
      </div>
      <button
        data-testid="jd-recovery-dismiss"
        type="button"
        aria-label={tc("ariaDismiss")}
        className="shrink-0 text-amber-500 hover:text-amber-700 transition-colors"
        onClick={() => setDismissed(true)}
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative close symbol */}
        {"×"}
      </button>
    </div>
  );
}

// Suspense boundary required by Next.js 15 for useSearchParams()
function JdRecoveryBanner() {
  return (
    <Suspense fallback={null}>
      <JdRecoveryBannerInner />
    </Suspense>
  );
}

// ---------------------------------------------------------------------------
// CV Parse-Status Banner — shown when some (not all) uploaded CVs failed to
// parse, surfaced via cv_parsed/cv_total query params (US153, FMEA JF-M-2.2)
// ---------------------------------------------------------------------------

function CvParseBannerInner() {
  const searchParams = useSearchParams();
  const t = useTranslations("gaps");
  const tc = useTranslations("common");
  const [dismissed, setDismissed] = useState(false);

  const parsedRaw = searchParams.get("cv_parsed");
  const totalRaw = searchParams.get("cv_total");
  if (!parsedRaw || !totalRaw || dismissed) return null;
  const parsed = Number(parsedRaw);
  const total = Number(totalRaw);
  if (!Number.isFinite(parsed) || !Number.isFinite(total) || parsed >= total) return null;

  return (
    <div
      data-testid="cv-parse-banner"
      className="mb-6 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3"
    >
      <svg
        className="mt-0.5 h-4 w-4 shrink-0 text-amber-600"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
        />
      </svg>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-amber-800">{t("cvParsedPartial", { parsed, total })}</p>
      </div>
      <button
        data-testid="cv-parse-dismiss"
        type="button"
        aria-label={tc("ariaDismiss")}
        className="shrink-0 text-amber-500 hover:text-amber-700 transition-colors"
        onClick={() => setDismissed(true)}
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative close symbol */}
        {"×"}
      </button>
    </div>
  );
}

function CvParseBanner() {
  return (
    <Suspense fallback={null}>
      <CvParseBannerInner />
    </Suspense>
  );
}

// ---------------------------------------------------------------------------
// Input Warnings Banner — name-mismatch (US155/2.4), document-type (US154/2.3)
// and per-CV completeness (US157/2.7), surfaced via query params from upload.
// ---------------------------------------------------------------------------

function InputWarningsBannerInner() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const t = useTranslations("gaps");
  const tc = useTranslations("common");
  const [dismissed, setDismissed] = useState(false);

  const nameWarning = searchParams.get("name_warning") === "1";
  const docWarning = searchParams.get("doc_warning") === "1";
  const undatedRaw = searchParams.get("undated");
  const undated = undatedRaw ? Number(undatedRaw) : 0;
  const hasUndated = Number.isFinite(undated) && undated > 0;

  if (dismissed || (!nameWarning && !docWarning && !hasUndated)) return null;

  return (
    <div
      data-testid="input-warnings-banner"
      className="mb-6 flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3"
    >
      <svg
        className="mt-0.5 h-4 w-4 shrink-0 text-amber-600"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"
        />
      </svg>
      <div className="min-w-0 flex-1 space-y-1">
        {nameWarning && (
          <p data-testid="warn-name" className="text-sm text-amber-800">{t("nameMismatchWarning")}</p>
        )}
        {docWarning && (
          <p data-testid="warn-doc" className="text-sm text-amber-800">{t("docTypeWarning")}</p>
        )}
        {hasUndated && (
          <p data-testid="warn-undated" className="text-sm text-amber-700">
            {t("undatedPositionsNote", { count: undated })}
          </p>
        )}
        {(nameWarning || docWarning) && (
          <button
            data-testid="input-warnings-review"
            type="button"
            className="text-sm font-medium text-amber-700 underline hover:no-underline"
            onClick={() => router.push("/profile")}
          >
            {t("reviewProfile")}
          </button>
        )}
      </div>
      <button
        data-testid="input-warnings-dismiss"
        type="button"
        aria-label={tc("ariaDismiss")}
        className="shrink-0 text-amber-500 transition-colors hover:text-amber-700"
        onClick={() => setDismissed(true)}
      >
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative close symbol */}
        {"×"}
      </button>
    </div>
  );
}

function InputWarningsBanner() {
  return (
    <Suspense fallback={null}>
      <InputWarningsBannerInner />
    </Suspense>
  );
}

// ---------------------------------------------------------------------------
// GapClickPanel — inline micro-session for a single cluster
// ---------------------------------------------------------------------------

function GapClickPanel({
  state,
  onUpdate,
  onSubmit,
  onCancel,
}: {
  state: GapClickState;
  onUpdate: (patch: Partial<GapClickState>) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const t = useTranslations("gaps");
  const tc = useTranslations("common");

  if (state.status === "idle") {
    // The card click starts the session; an idle card only speaks when the
    // server refused it or the start failed.
    if (state.refusal) {
      return (
        <p data-testid="gap-refusal" className="mt-2 text-xs text-on-surface-variant">
          {state.refusal === "gap_budget_spent" ? t("refusalBudgetSpent") : t("refusalAlreadyCovered")}
        </p>
      );
    }
    return state.error ? (
      <p data-testid="gap-error" className="mt-2 text-xs text-critical">{state.error}</p>
    ) : null;
  }

  if (state.status === "loading" || state.status === "refreshing") {
    return (
      <div className="mt-2 flex items-center gap-2">
        <div className="animate-spin h-3 w-3 border-2 border-teal border-t-transparent rounded-full" />
        <span className="text-xs text-on-surface-variant">
          {state.status === "loading" ? t("loadingQuestion") : t("refreshingAnalysis")}
        </span>
      </div>
    );
  }

  return (
    <div className="mt-3 rounded-lg border border-teal/30 bg-teal/5 p-3 space-y-2">
      {state.followUpOpen && state.followUpOpen.length > 0 && (
        <p data-testid="gap-follow-up-label" className="text-xs font-medium text-teal">
          {t("followUpLabel", { items: state.followUpOpen.join(", ") })}
        </p>
      )}
      <p data-testid="gap-question" className="text-sm font-medium text-neutral-dark">{state.question}</p>
      {state.error && <p className="text-xs text-critical">{state.error}</p>}
      {state.choices && state.choices.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs text-gray-400">{t("choiceCardHint")}</p>
          <div className="flex flex-col gap-1">
            {state.choices.map((choice) => (
              <button
                key={choice}
                type="button"
                data-testid="gap-choice"
                className={cn(
                  "w-full text-left rounded border border-teal/30 px-3 py-2 text-xs text-neutral-dark",
                  "hover:bg-teal/5 transition-colors",
                  state.answer === choice ? "bg-teal/10 border-teal/60 font-medium" : "bg-white",
                )}
                onClick={() => onUpdate({ answer: choice, status: "answering" })}
              >
                {choice}
              </button>
            ))}
          </div>
        </div>
      )}
      <textarea
        data-testid="gap-answer-textarea"
        className={cn(
          "w-full resize-none text-xs font-body border border-gray-200 rounded px-2 py-1.5",
          "focus:outline-none focus:ring-1 focus:ring-teal/50 focus:border-teal",
          "disabled:opacity-50 min-h-[72px]",
        )}
        placeholder={t("answerPlaceholder")}
        value={state.answer}
        onChange={(e) => onUpdate({ answer: e.target.value, status: "answering" })}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSubmit(); }
        }}
        disabled={state.sending}
        rows={2}
      />
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="outline" className="text-xs py-1 h-auto"
          disabled={state.sending}
          onClick={onCancel}>
          {tc("cancel")}
        </Button>
        <Button data-testid="gap-submit-button" size="sm" className="text-xs py-1 h-auto"
          disabled={!state.answer.trim() || state.sending}
          onClick={onSubmit}>
          {state.sending ? t("savingAnswer") : t("submitAnswer")}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function GapsPage({
  params,
}: {
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  // #686 — bumped after every answered gap so the decisions card re-reads.
  const [decisionsToken, setDecisionsToken] = useState(0);
  const router = useRouter();
  const t = useTranslations("gaps");

  const [gaps, setGaps] = useState<GapAnalysis | null>(null);
  const [flowState, setFlowState] = useState<FlowState | null>(null);
  const [profileStats, setProfileStats] = useState<ProfileStats>({
    positions: 0, projects: 0, certifications: 0, data_points: 0,
  });
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  // #67: the slim merge-review pointer needs the trail; whether it shows is
  // derived below, scoped to THIS flow's imports (Spaghettieis UAT).
  const [profileTrail, setProfileTrail] = useState<ProfileChanges | null>(null);

  // Gap-Click state keyed by cluster ID — the open conversation only; a card's
  // coverage is the server's record (ADR-089 clause 8).
  const [gapStates, setGapStates] = useState<Record<string, GapClickState>>({});
  // The liability panel's "tell the story" opens the SAME kind of micro-session;
  // while it holds one, no card may open another (and vice versa).
  const [liabilityActive, setLiabilityActive] = useState(false);
  // ADR-089 clause 8 — the page holds at most ONE open micro-session: a card
  // whose state is anything but idle, or the liability panel's story. The ref
  // closes the double-click window before the lock re-renders.
  const openClusterId =
    Object.keys(gapStates).find((id) => gapStates[id].status !== "idle") ?? null;
  const sessionOpen = openClusterId !== null || liabilityActive;
  const sessionOpenRef = useRef(false);
  useEffect(() => {
    sessionOpenRef.current = sessionOpen;
  }, [sessionOpen]);
  // Animated match score (refreshed after gap resolution)
  const [matchScore, setMatchScore] = useState(0);
  // Parsed-JD echo for the pre-interview review surface (US158, FMEA 4.3/4.4)
  const [jobEcho, setJobEcho] = useState<{
    role_title: string;
    company_name: string | null;
    education_requirement: string | null;
    required_skills: string[];
    nice_to_have_skills: string[];
    jd_language: "de" | "en" | null;
  } | null>(null);
  // E054/US288: the application's persisted document-language override.
  // undefined = still loading (control hidden), null = loaded, no override.
  const [appLanguageOverride, setAppLanguageOverride] = useState<
    "de" | "en" | null | undefined
  >(undefined);
  // US225: below md the decision bar (interview/generate/explore/cancel)
  // becomes `fixed` at the viewport bottom; this spacer reserves the same
  // height in normal flow so the fixed bar never permanently covers the
  // match-score/gap cards above it. Measured (not hard-coded) because the
  // bar's content varies across the three flow-context modes (Case 1/2/3)
  // and with/without the cancel action.
  const decisionBarRef = useRef<HTMLDivElement>(null);
  const [decisionBarHeight, setDecisionBarHeight] = useState(0);
  useEffect(() => {
    const el = decisionBarRef.current;
    if (!el) return;
    const measure = () => setDecisionBarHeight(el.offsetHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
    // `loading` matters here, not just its usual suspects: decisionBarRef only
    // attaches once the loading branch (an early return, above) stops
    // rendering — without it in the deps, the one re-run triggered by `gaps`
    // arriving can land on the render where `loading` is still true and the
    // ref is still null, and never fires again.
  }, [gaps, flowState, actionLoading, loading]);

  // #67: fetch the trail once; the merge pointer derives from it per flow context.
  useEffect(() => {
    let cancelled = false;
    getProfileChanges()
      .then((trail) => { if (!cancelled) setProfileTrail(trail); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    async function load() {
      try {
        const fsRes = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
        if (!fsRes.ok) throw new Error("Flow not found");
        const fs: FlowState = await fsRes.json();
        setFlowState(fs);

        // CV-only ingestion run (no job on the flow): there is nothing to
        // analyze — the page shows the profile summary + merge review only.
        // Fetching /api/job/null/gaps here used to surface a raw 422 error.
        if (!fs.job_id) {
          try {
            const profileRes = await fetch(`${API_BASE}/api/profile`);
            if (profileRes.ok) {
              const profileData = await profileRes.json();
              const stats = profileData.stats ?? {};
              setProfileStats({
                positions: stats.positions ?? 0,
                projects: stats.projects ?? 0,
                certifications: stats.certifications ?? 0,
                data_points: stats.data_points ?? 0,
              });
            }
          } catch {
            // Keep defaults
          }
          return;
        }

        // Best-effort: echo what we read from the job ad (US158, FMEA 4.3/4.4).
        if (fs.job_id) {
          try {
            const jRes = await fetch(`${API_BASE}/api/job/${fs.job_id}`);
            if (jRes.ok) {
              const j = await jRes.json();
              setJobEcho({
                role_title: j.role_title ?? "",
                company_name: j.company_name ?? null,
                education_requirement: j.education_requirement ?? null,
                required_skills: j.required_skills ?? [],
                nice_to_have_skills: j.nice_to_have_skills ?? [],
                jd_language: j.jd_language ?? null,
              });
            }
          } catch {
            // echo is non-critical; never block gap analysis on it
          }
          // E054/US288: current document-language override for the control's
          // prefill. Best-effort like the echo above.
          if (fs.application_id) {
            try {
              const aRes = await fetch(
                `${API_BASE}/api/applications/${fs.application_id}`
              );
              if (aRes.ok) {
                const a = await aRes.json();
                setAppLanguageOverride(a.language_override ?? null);
              }
            } catch {
              // control simply stays hidden
            }
          }
        }

        // E037 PQ #3 (match-score stability): a screen load READS the cached
        // analysis — it must never recompute, or the deterministic score wobbles
        // across mounts. GET returns the latest stored row; only when none exists
        // yet (404) do we create exactly one with a POST. (The backend POST is
        // now idempotent too, so even a stray POST returns the same row — this is
        // belt-and-braces.) Recompute is reserved for the explicit retry button
        // and the post-interview-answer /gaps/refresh path.
        let gapData: GapAnalysis;
        const gRes = await fetch(`${API_BASE}/api/job/${fs.job_id}/gaps`);
        if (gRes.ok) {
          gapData = await gRes.json();
        } else if (gRes.status === 404) {
          // No cached analysis yet — kick off the async job and poll instead of blocking
          // on a synchronous ~2-min LLM call. Resilient: a 504 mid-analysis no longer
          // wedges the screen (it lands as a failed poll → retry state). Idempotency is
          // preserved — the background task reuses a fingerprint-matching row, no re-run.
          // analyzeGapsAsync resolves the loose GapAnalysisResult envelope; the
          // backend result is a full gap analysis, so bridge through unknown.
          gapData = (await analyzeGapsAsync(fs.job_id, {
            apiBase: API_BASE,
          })) as unknown as GapAnalysis;
        } else {
          throw new Error(await apiErrorMessage(gRes));
        }
        setGaps(gapData);
        setMatchScore(scoreToPercent(gapData.match_score, 0));

        try {
          const profileRes = await fetch(`${API_BASE}/api/profile`);
          if (profileRes.ok) {
            const profileData = await profileRes.json();
            const stats = profileData.stats ?? {};
            setProfileStats({
              positions: stats.positions ?? 0,
              projects: stats.projects ?? 0,
              certifications: stats.certifications ?? 0,
              data_points: stats.data_points ?? 0,
            });
          }
        } catch {
          // Keep defaults
        }
      } catch (e: unknown) {
        setError(
          e instanceof GapAnalysisError
            ? t("analysisError")
            : e instanceof Error
              ? e.message
              : "Failed to load analysis",
        );
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [flowId]);

  // Belt-and-braces for a legacy half-built analysis row (clusters written in
  // a second commit before this fix): when items exist but clusters haven't
  // landed, re-read the analysis a few times instead of showing a dead
  // "Analyzing your profile…" forever. New rows always publish clustered.
  const clusterPolls = useRef(0);
  useEffect(() => {
    const jobId = flowState?.job_id;
    if (!jobId || !gaps) return;
    if ((gaps.gap_clusters?.length ?? 0) > 0) return;
    const items = (gaps.category_b?.length ?? 0) + (gaps.category_c?.length ?? 0);
    if (items === 0 || clusterPolls.current >= 10) return;
    let cancelled = false;
    const id = setTimeout(async () => {
      clusterPolls.current += 1;
      try {
        const res = await fetch(`${API_BASE}/api/job/${jobId}/gaps`);
        if (res.ok && !cancelled) {
          setGaps(await res.json());
        }
      } catch {
        // transient — the next poll (if any) retries
      }
    }, 3000);
    return () => { cancelled = true; clearTimeout(id); };
  }, [flowState?.job_id, gaps]);

  function updateGapState(clusterId: string, patch: Partial<GapClickState>) {
    setGapStates((prev) => ({
      ...prev,
      [clusterId]: { ...(prev[clusterId] ?? EMPTY_GAP_STATE), ...patch },
    }));
  }

  // ADR-089 clause 8 — the page shows the analysis ROW, whole: score, category
  // lists, clusters with their coverage record. Every re-read replaces all of
  // it (never only the score, which is how the old page kept rendering the
  // pre-answer cluster list until the user navigated away and back). The
  // per-turn overlays are dropped with it: the row now carries those turns.
  // Reads can overlap (a follow-up turn's re-read, then the completion's
  // refresh): only a response to a read STARTED after the last applied one may
  // replace the page — an older row landing late must never undo a newer one.
  const analysisReadSeq = useRef(0);
  const analysisAppliedSeq = useRef(0);
  function beginAnalysisRead(): number {
    analysisReadSeq.current += 1;
    return analysisReadSeq.current;
  }

  function replaceAnalysis(next: GapAnalysis, seq: number) {
    if (seq < analysisAppliedSeq.current) return;
    analysisAppliedSeq.current = seq;
    setGaps(next);
    setMatchScore((prev) => scoreToPercent(next.match_score, prev));
    setGapStates((prev) => {
      const out: Record<string, GapClickState> = {};
      for (const [id, st] of Object.entries(prev)) out[id] = { ...st, overlay: null };
      return out;
    });
  }

  /** GET the latest row — a DB read, never a recompute (E037 PQ #3). A turn's
   * outcome is written onto that row in the turn's own transaction. */
  async function reReadAnalysis() {
    if (!flowState?.job_id) return;
    const seq = beginAnalysisRead();
    try {
      const res = await fetch(`${API_BASE}/api/job/${flowState.job_id}/gaps`);
      if (res.ok) replaceAnalysis((await res.json()) as GapAnalysis, seq);
    } catch {
      // Non-critical — the card keeps the turn's own record meanwhile.
    }
  }

  /** POST /gaps/refresh (answer-driven, ADR-089 clause 5) and adopt the whole
   * recomputed row; a failed refresh falls back to re-reading the latest row
   * (the session's completion already recomputed it). */
  async function refreshAnalysis() {
    if (!flowState?.job_id) return;
    const seq = beginAnalysisRead();
    try {
      const res = await fetch(`${API_BASE}/api/job/${flowState.job_id}/gaps/refresh`, {
        method: "POST",
      });
      if (res.ok) {
        replaceAnalysis((await res.json()) as GapAnalysis, seq);
        return;
      }
    } catch {
      // fall through to the plain re-read
    }
    await reReadAnalysis();
  }

  // #260: dropping/storying a liability changes the ledger's claimable/gap
  // split, so the headline match score can genuinely move (down for a drop,
  // up for a story) — POST /gaps/refresh, same as an ordinary gap-cluster
  // answer. Idempotency (E037 PQ #3) makes this safe for BOTH exits: a drop
  // mutates the ledger directly without touching profile_json, so the
  // fingerprint is unchanged and refresh just re-reads the already-downgraded
  // row (no LLM re-run); a story answer reconciles into the vault's narrative
  // fields, which DOES change the fingerprint, so refresh genuinely
  // recomputes narrative_backed from the fresh profile — the only path that
  // can honestly clear a liability.
  async function refreshAfterLiabilityAction() {
    setDecisionsToken((n) => n + 1);
    await refreshAnalysis();
  }

  async function startMicroSession(clusterId: string, jobId: string) {
    // Clause 8: one open micro-session per page. `_create_micro_session`
    // completes any active session, which would silently end a pending
    // follow-up on another card.
    if (sessionOpenRef.current) return;
    sessionOpenRef.current = true;
    updateGapState(clusterId, { ...EMPTY_GAP_STATE, status: "loading" });
    try {
      const res = await fetch(`${API_BASE}/api/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, mode: "targeted", target_gap: clusterId }),
      });
      if (res.status === 409) {
        const code = await refusalCodeOf(res);
        if (code) {
          // The server's word on this cluster — say it inline, then re-read
          // the row so the card shows the record behind the refusal.
          updateGapState(clusterId, { ...EMPTY_GAP_STATE, refusal: code });
          void reReadAnalysis();
          return;
        }
      }
      if (!res.ok) throw new Error(await apiErrorMessage(res));
      const data = await res.json();
      // Ruling B-3: a click on a cluster whose micro-session is waiting on a
      // follow-up RESUMES it (same session, the waiting follow-up as
      // `question`). Its open members are the row's `gaps` — the same record
      // the answer turn reported — so the follow-up header is honest here too.
      const cluster = gaps?.gap_clusters?.find((c) => c.id === clusterId);
      const resumedFollowUp =
        data.resumed === true && (cluster?.outcome?.asked ?? 0) > 0 && (cluster?.gaps?.length ?? 0) > 0;
      updateGapState(clusterId, {
        status: "question",
        sessionId: data.session_id,
        question: data.question ?? data.first_question,
        choices: data.choices ?? null,
        followUpOpen: resumedFollowUp && cluster ? cluster.gaps : null,
      });
    } catch (e: unknown) {
      updateGapState(clusterId, {
        status: "idle",
        error: e instanceof Error ? e.message : "Failed to start",
      });
    }
  }

  async function sendGapAnswer(clusterId: string) {
    const st = gapStates[clusterId];
    if (!st?.sessionId || !st.answer.trim() || st.sending) return;
    updateGapState(clusterId, { sending: true, error: "" });
    let data: SessionTurnResponse;
    try {
      const res = await fetch(`${API_BASE}/api/session/${st.sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: st.answer.trim() }),
      });
      if (!res.ok) throw new Error(await apiErrorMessage(res));
      data = (await res.json()) as SessionTurnResponse;
    } catch (e: unknown) {
      updateGapState(clusterId, {
        sending: false,
        error: e instanceof Error ? e.message : "Failed to send",
      });
      return;
    }

    // #686 (JF-M-3.5) — a turn can park a dispute (`pending_conflicts`) or owe
    // a confirmation (`pending_confirmations`). Both reach the candidate
    // through the decisions stack, which reads `/api/profile/health` (the
    // durable surface, so a reload still shows them) — re-read it every turn.
    setDecisionsToken((n) => n + 1);

    const turn =
      data.cluster_coverage && data.cluster_coverage.cluster_id === clusterId
        ? data.cluster_coverage
        : null;

    if (!data.complete) {
      // A follow-up (ADR-089 clause 2) or a confirmation question — asked
      // inline, in the same card, with the same textarea flow.
      const isConfirmation = (data.pending_confirmations?.length ?? 0) > 0;
      updateGapState(clusterId, {
        status: "question",
        sending: false,
        answer: "",
        question: data.question ?? st.question,
        choices: data.choices ?? null,
        followUpOpen: !isConfirmation && turn ? turn.open_concepts : null,
        overlay: turn ?? st.overlay,
      });
      void reReadAnalysis();
      return;
    }

    // The micro-session is complete: adopt the whole recomputed analysis.
    updateGapState(clusterId, { ...EMPTY_GAP_STATE, status: "refreshing", overlay: turn });
    await refreshAnalysis();
    updateGapState(clusterId, { status: "idle" });
  }

  function cancelGapSession(clusterId: string) {
    // The turn record already written stays (it is the server's); only the
    // open conversation is closed on this page.
    setGapStates((prev) => ({
      ...prev,
      [clusterId]: { ...EMPTY_GAP_STATE, overlay: prev[clusterId]?.overlay ?? null },
    }));
  }

  async function advance(target: "interview" | "cv_generation") {
    if (!gaps) return;
    setError("");
    setActionLoading(true);
    try {
      if (target === "interview") {
        const sessionRes = await fetch(`${API_BASE}/api/session`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ job_id: flowState?.job_id }),
        });
        if (!sessionRes.ok) {
          const errData = await sessionRes.json();
          throw new Error(
            typeof errData.detail === "string" ? errData.detail : "Failed to create interview session"
          );
        }
        const sessionData = await sessionRes.json();
        const sessionId = sessionData.id || sessionData.session_id;

        const advRes = await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: target, artifact_id: sessionId }),
        });
        if (!advRes.ok) {
          const errData = await advRes.json();
          throw new Error(
            errData.detail?.allowed_transitions
              ? `Invalid step. Allowed: ${errData.detail.allowed_transitions.join(", ")}`
              : typeof errData.detail === "string" ? errData.detail : "Error"
          );
        }
        router.push(`/flow/${flowId}/interview`);
      } else {
        // cv_generation does not require an artifact_id — the CV is generated
        // from the CV page.  Advance the flow first so the layout guard allows
        // the navigation (otherwise it snaps back to /gaps).
        const advRes = await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ step: "cv_generation" }),
        });
        if (!advRes.ok) {
          const errData = await advRes.json().catch(() => ({}));
          throw new Error(
            typeof errData.detail === "string" ? errData.detail : "Failed to advance to CV"
          );
        }
        router.push(`/flow/${flowId}/cv`);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setActionLoading(false);
    }
  }

  async function retryGapAnalysis() {
    if (!flowState?.job_id) return;
    setError("");
    setLoading(true);
    try {
      const data = (await analyzeGapsAsync(flowState.job_id, {
        apiBase: API_BASE,
      })) as unknown as GapAnalysis;
      setGaps(data);
      setMatchScore(scoreToPercent(data.match_score, 0));
    } catch (e: unknown) {
      setError(
        e instanceof GapAnalysisError
          ? t("analysisError")
          : e instanceof Error
            ? e.message
            : "Failed to load analysis",
      );
    } finally {
      setLoading(false);
    }
  }

  // -------------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------------

  if (loading) {
    return (
      <div data-testid="loading-indicator" className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <div className="animate-spin h-8 w-8 border-4 border-teal border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-sm text-gray-500">{t("analyzing")}</p>
        </div>
      </div>
    );
  }

  const roleTitle = flowState?.job_summary?.role_title ?? "the target role";
  // One source for every gap count so the badge and the heading never disagree
  // (F1): `gaps` is the canonical gap number; `itemsToAddress` (partials + gaps)
  // still gates the section and the interview CTA. All of it is the server's
  // row (ADR-089 clause 8) — no client-side "resolved" set.
  const counts = gapCounts(gaps);
  const clusterCounts = clusterCoverageCounts(gaps?.gap_clusters);

  // Flow context cases (Spaghettieis UAT, ADR-016 amended 2026-07-13):
  //   Case 1  JD + CVs (first run, "new")  → hero + this-run merge pointer + analysis
  //   Case 2  CVs only (no job)            → profile summary + merge pointer only
  //   Case 3  JD only (follow-up)          → analysis only; no onboarding chrome
  const hasJob = Boolean(flowState?.job_id);
  const isIngestionRun = !hasJob || flowState?.user_type === "new";
  // Merge pointer: only on ingestion runs, and only when THIS run merged
  // (the trail is user-global — an onboarding merge must not haunt follow-ups).
  const hasMerge =
    isIngestionRun && profileTrail
      ? hasMergeReview(profileTrail, { since: flowState?.created_at ?? undefined })
      : false;
  // The interview offer is gap-driven, never user-type-driven: gaps detected →
  // the user gets the mitigation option; a clean sweep goes straight to CV.
  const offerInterview = hasJob && counts.itemsToAddress > 0;

  return (
    <div data-testid="gap-analysis-page" className="max-w-4xl mx-auto">
      {/* #686 (founder ruling V-1) — a profile dispute raised from a gap answer
          reaches the user as a corner-anchored toast STACK, not as a card in the
          page flow: no reserved UI space, one popup per pending decision, and it
          disappears on its own after the operator's auto-dismiss window. The
          component renders `position: fixed`, so this mount point only decides
          WHEN it is alive, never where it sits. */}
      <ProfileDecisionsCard
        apiBase={API_BASE}
        flowId={flowId}
        refreshToken={decisionsToken}
        bottomOffsetPx={decisionBarHeight}
      />
      <JdRecoveryBanner />
      <CvParseBanner />
      <InputWarningsBanner />
      {/* Section 1: Master Profile Summary — ingestion runs only (cases 1+2).
          A JD-only follow-up imported nothing; opening it with onboarding
          chrome misread as a "CV merge report" (Spaghettieis UAT). */}
      {isIngestionRun && (
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-4">
            <div className="flex h-6 w-6 items-center justify-center rounded-full bg-success text-white">
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="font-heading text-xl font-bold text-neutral-dark">
              {flowState?.user_type === "new" ? t("masterProfileCreated") : t("masterProfileUpdated")}
            </h2>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard value={profileStats.positions} label={t("statPositions")} />
            <StatCard value={profileStats.projects} label={t("statProjects")} />
            <StatCard value={profileStats.certifications} label={t("statCertifications")} />
            <StatCard value={profileStats.data_points} label={t("statDataPoints")} />
          </div>
        </div>
      )}

      {/* Section 1b: merge review moved out of the flow (#67) — slim pointer, only when a merge happened. */}
      {hasMerge && (
        <div className="mb-8" data-testid="profile-review-section">
          <div className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/5 p-4">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{t("mergeMovedTitle")}</p>
              <p className="mt-1 text-sm text-muted-foreground">{t("mergeMovedBody")}</p>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              data-testid="merge-review-link"
              onClick={() => router.push("/profile#import-log")}
            >
              {t("mergeMovedLink")}
            </Button>
          </div>
        </div>
      )}

      {/* Section 1c: Parsed-JD echo — what we read from the job ad (US158, FMEA 4.3/4.4).
          Chips + count come from the ledger fit-slice so the echo total always
          equals the badge math below (#111); raw JD lists only pre-ledger. */}
      {jobEcho &&
        (() => {
          const echoChips = canonicalRequirementChips(
            gaps?.keyword_ledger,
            jobEcho.required_skills,
            jobEcho.nice_to_have_skills,
          );
          return (
            <>
              <JobEchoCard
                companyName={jobEcho.company_name}
                roleTitle={jobEcho.role_title}
                educationRequirement={jobEcho.education_requirement}
                requiredSkills={echoChips.required}
                niceToHaveSkills={echoChips.niceToHave}
              />
              {/* E054/US288: leading document language — detection prefills,
                  the user decides (ADR-038 amendment 2026-08-23). Rendered
                  once the persisted override finished loading so the
                  control's initial state is honest. */}
              {flowState?.application_id && appLanguageOverride !== undefined && (
                <div className="-mt-6 mb-8">
                  <DocumentLanguageControl
                    applicationId={flowState.application_id}
                    detectedLanguage={jobEcho.jd_language}
                    initialOverride={appLanguageOverride}
                    apiBase={API_BASE}
                  />
                </div>
              )}
            </>
          );
        })()}

      {/* Section 2: Match Score — only when this flow analyses a job */}
      {hasJob && gaps && (
      <Card className="p-6 mb-8">
        <div className="flex flex-col lg:flex-row items-center gap-6">
          <ScoreCircle score={matchScore} size={100} />
          <div className="flex-1 text-center lg:text-left">
            <h3 className="font-heading text-lg font-bold text-neutral-dark mb-2">{roleTitle}</h3>
            <p data-testid="match-score-display" className="text-sm text-gray-500 mb-4">
              {t("matchScoreDisplay", { label: t("matchScore"), score: matchScore })}
            </p>
            <div className="flex flex-wrap gap-2 justify-center lg:justify-start">
              {counts.directMatches > 0 && (
                <Badge variant="success">{t("directMatchesBadge", { count: counts.directMatches })}</Badge>
              )}
              {counts.likelyMatches > 0 && (
                <Badge variant="warning">{t("likelyMatchesBadge", { count: counts.likelyMatches })}</Badge>
              )}
              {counts.gaps > 0 && (
                <Badge variant="critical">{t("gapsToAddress", { count: counts.gaps })}</Badge>
              )}
              {clusterCounts.covered > 0 && (
                <Badge variant="success" data-testid="covered-badge">
                  {t("resolvedBadge", { count: clusterCounts.covered })}
                </Badge>
              )}
            </div>
          </div>
        </div>
      </Card>
      )}

      {/* Section 3: Cluster-based gap display. Also shown when every cluster
          is finished: a worked cluster stays in the list with its coverage so
          the user sees what was worked (ADR-089 clause 4). */}
      {hasJob && (counts.itemsToAddress > 0 || (gaps?.gap_clusters?.length ?? 0) > 0) && (
        <div data-testid="gaps-section" className="mb-8">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-heading text-lg font-bold text-neutral-dark">
              {t("gapsIdentified", { count: counts.gaps })}
            </h3>
            {sessionOpen ? (
              <p data-testid="gaps-locked-hint" className="text-xs text-on-surface-variant">
                {t("gapsLockedHint")}
              </p>
            ) : (
              clusterCounts.askable > 0 && (
                <p className="text-xs text-gray-400">{t("clickGapHint")}</p>
              )
            )}
          </div>

          {/* Cluster-based gap display */}
          {gaps?.gap_clusters && gaps.gap_clusters.length > 0 ? (
            <div className="space-y-3">
              <p className="text-xs text-gray-500 mb-3">
                {t("clustersToAddress", { count: clusterCounts.askable })}
              </p>
              {/* Order is by severity only (C before B), never by coverage: a
                  card stays where it was while its state changes, so re-entry
                  shows the same list the user left (ADR-089 clause 4). */}
              {[...gaps.gap_clusters]
                .sort((a, b) => {
                  if (a.category === "C" && b.category !== "C") return -1;
                  if (a.category !== "C" && b.category === "C") return 1;
                  return 0;
                })
                .map((cluster) => {
                  const clusterState = gapStates[cluster.id] ?? EMPTY_GAP_STATE;
                  const view = clusterView(cluster, clusterState.overlay);
                  const clickable =
                    view.askable &&
                    !sessionOpen &&
                    clusterState.status === "idle" &&
                    !clusterState.refusal;
                  return (
                    <GapClusterCard
                      key={cluster.id}
                      cluster={cluster}
                      view={view}
                      locked={sessionOpen && openClusterId !== cluster.id}
                      onClick={
                        clickable
                          ? () => void startMicroSession(cluster.id, flowState?.job_id ?? "")
                          : undefined
                      }
                    >
                      <div onClick={(e) => e.stopPropagation()}>
                        <GapClickPanel
                          state={clusterState}
                          onUpdate={(patch) => updateGapState(cluster.id, patch)}
                          onSubmit={() => void sendGapAnswer(cluster.id)}
                          onCancel={() => cancelGapSession(cluster.id)}
                        />
                      </div>
                    </GapClusterCard>
                  );
                })}
            </div>
          ) : (
            /* No clusters yet (analysis still running) */
            ((gaps?.category_c && gaps.category_c.length > 0) || (gaps?.category_b && gaps.category_b.length > 0)) ? (
              <p className="text-sm text-gray-500">{t("analyzing")}</p>
            ) : null
          )}
        </div>
      )}

      {/* #260: pre-generation keyword-liability summary — a JD hard
          requirement the profile can claim but has no narrative anywhere.
          Placed after the gap clusters (real gaps first) and before the
          detailed A/B/C breakdown, so both honest exits are visible
          BEFORE the user moves on to CV generation. */}
      {hasJob && flowState?.job_id && gaps?.keyword_liabilities && gaps.keyword_liabilities.length > 0 && (
        <LiabilityPanel
          jobId={flowState.job_id}
          liabilities={gaps.keyword_liabilities}
          clusters={gaps.gap_clusters ?? []}
          apiBase={API_BASE}
          onDropped={() => void refreshAfterLiabilityAction()}
          onStoryAdded={() => void refreshAfterLiabilityAction()}
          onFollowUpTurn={() => {
            setDecisionsToken((n) => n + 1);
            void reReadAnalysis();
          }}
          locked={openClusterId !== null}
          onActiveChange={setLiabilityActive}
        />
      )}

      {/* Detailed breakdown */}
      {hasJob && gaps && (
      <details className="mb-8">
        <summary className="cursor-pointer text-sm text-teal hover:underline mb-2">
          {t("viewBreakdown")}
        </summary>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-4">
          <Card className="p-4 border-t-4 border-t-success">
            <h4 className="font-semibold text-sm mb-2 text-success">{t("categoryALabel")}</h4>
            <div className="flex flex-wrap gap-1">
              {gaps?.category_a?.length ? (
                gaps.category_a.map((item, i) => (
                  <Badge key={i} variant="success" className="text-xs">{item}</Badge>
                ))
              ) : (
                <span className="text-xs text-gray-400 italic">{t("none")}</span>
              )}
            </div>
          </Card>
          <Card className="p-4 border-t-4 border-t-warning">
            <h4 className="font-semibold text-sm mb-2 text-warning">{t("categoryBLabel")}</h4>
            <div className="flex flex-wrap gap-1">
              {gaps?.category_b?.length ? (
                gaps.category_b.map((item, i) => (
                  <Badge key={i} variant="warning" className="text-xs">{item}</Badge>
                ))
              ) : (
                <span className="text-xs text-gray-400 italic">{t("none")}</span>
              )}
            </div>
          </Card>
          <Card className="p-4 border-t-4 border-t-critical">
            <h4 className="font-semibold text-sm mb-2 text-critical">{t("categoryCLabel")}</h4>
            <div className="flex flex-wrap gap-1">
              {gaps?.category_c?.length ? (
                gaps.category_c.map((item, i) => (
                  <Badge key={i} variant="critical" className="text-xs">{item}</Badge>
                ))
              ) : (
                <span className="text-xs text-gray-400 italic">{t("none")}</span>
              )}
            </div>
          </Card>
        </div>
      </details>
      )}

      {/* Error */}
      {error && (
        <div data-testid="error-message" className="mb-6 p-4 rounded-lg bg-critical/10 border border-critical/20">
          <p className="text-sm text-critical">{error}</p>
          {!gaps && (
            <Button variant="outline" size="sm" onClick={retryGapAnalysis} className="mt-2">
              {t("retryAnalysis")}
            </Button>
          )}
        </div>
      )}

      {/* CTAs — the interview offer is GAP-driven (ADR-016 amended): anyone
          with items to address gets the mitigation option. It leads for new
          users; returning users keep Generate as the primary path (Emma
          journey: "optional but tempting"), and a clean sweep goes straight
          to generation.
          US225: below md the whole decision cluster (interview/generate/
          explore/cancel) becomes a FIXED bottom bar — "pursue or not" stays
          within thumb reach at all times, not just once scrolled to the end.
          A spacer (sized to the bar's real, content-dependent height via
          ResizeObserver) keeps it from covering the match-score/gap cards
          that sit earlier in the same scrollable column — position:sticky
          was tried first and rejected: nested inside the flow layout's own
          scrollable column, it pinned as soon as the page loaded, overlapping
          the match-score card above it instead of waiting for real scroll.
          md and up: unchanged inline layout. */}
      {/* E056/ADR-077 cl. 6 — fact pins, met as an OFFER before generation
          (COPY.md §B, mock/Main): the last card of the scroll flow, directly
          above the decision cluster, so "is there anything that must be in
          there?" is asked while the answer can still change the documents.
          D-1: below md it stays in the scroll flow above the spacer — it never
          joins the fixed bottom bar, which is reserved for the pursue-or-not
          decision. */}
      {flowState?.application_id && (
        <PinnedFactsPanel
          variant="teaser"
          applicationId={flowState.application_id}
          apiBase={API_BASE}
          cvId={flowState.cv_summary?.cv_id ?? null}
          coverLetterId={flowState.cover_letter_summary?.cover_letter_id ?? null}
        />
      )}

      <div aria-hidden="true" className="md:hidden" style={{ height: decisionBarHeight }} />
      <div
        ref={decisionBarRef}
        data-testid="gaps-decision-bar"
        className={cn(
          "fixed inset-x-0 bottom-0 z-30 px-5 pt-4",
          "pb-[max(1rem,env(safe-area-inset-bottom))]",
          "bg-surface-dim/95 backdrop-blur-sm border-t border-gray-200",
          "md:static md:z-auto md:mx-0 md:mt-8 md:px-0 md:pt-0 md:pb-0",
          "md:bg-transparent md:backdrop-blur-none md:border-t-0",
        )}
      >
        <div className="flex flex-col sm:flex-row gap-4 items-center justify-center">
          {offerInterview && (
            <div className="flex flex-col items-center w-full sm:w-auto">
              <Button
                variant={flowState?.user_type === "new" ? "primary" : "secondary"}
                size="lg"
                onClick={() => void advance("interview")}
                disabled={actionLoading}
                className="w-full sm:w-auto sm:min-w-[240px]"
                data-testid="interview-button"
              >
                {t("startInterview")}
              </Button>
              <p className="text-xs italic text-gray-500 mt-2">
                {t("gapInterviewHint")}
              </p>
            </div>
          )}
          {hasJob && (
            <Button
              variant={flowState?.user_type === "returning" || counts.itemsToAddress === 0 ? "primary" : "secondary"}
              size="lg"
              onClick={() => void advance("cv_generation")}
              disabled={actionLoading}
              className="w-full sm:w-auto sm:min-w-[200px]"
              data-testid="generate-cv-button"
            >
              {t("generateCV")}
            </Button>
          )}
          <a
            href="/profile"
            className="text-sm text-teal underline hover:no-underline"
            onClick={(e) => { e.preventDefault(); router.push("/profile"); }}
          >
            {t("exploreProfile")}
          </a>
        </div>

        {/* US222 (Branch I): the honest walk-away at the match-score decision —
            a quiet tertiary action; the score may tell Emma this isn't worth it. */}
        {flowState?.application_id && (
          <div className="flex justify-center mt-5">
            <CancelApplicationButton
              applicationId={flowState.application_id}
              variant="inline"
            />
          </div>
        )}
      </div>
    </div>
  );
}
