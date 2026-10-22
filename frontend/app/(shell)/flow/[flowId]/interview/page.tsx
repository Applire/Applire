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


import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { use } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ProgressLinear } from "@/components/ui/progress";
import { DecisionTrailReview } from "@/components/review/DecisionTrailReview";
import { cn, displayValue } from "@/lib/utils";
import { describeConflict } from "@/lib/conflict-display";
import { Archive, Check, Clock, Contrast, HelpCircle, Lock, Square } from "lucide-react";
import { TONE } from "@/components/gaps/GapClusterCard";
import { NoWriteReceipt, NoWriteReceiptLine } from "@/components/interview/NoWriteReceipt";
import {
  clusterView,
  openThemeCount,
  requirementCounts,
  type GapCluster,
  type LedgerChipEntry,
  type MemberState,
  type TurnClusterCoverage,
} from "@/lib/match-utils";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FlowState {
  job_id: string;
  current_step: string;
  job_summary?: { role_title: string; required_skills?: string[] } | null;
  interview_summary?: { session_id: string; mode: string } | null;
}

interface GapAnalysisData {
  id: string;
  match_score: number;
  gap_clusters: GapCluster[];
  // #789 (ruling F1-2) — the open-requirement count reads the same row the
  // gaps page reads: the ledger's fit-weighted rows, updated per turn.
  keyword_ledger?: LedgerChipEntry[];
  category_a?: string[];
  category_b?: string[];
  category_c?: string[];
}

interface SessionCreateResponse {
  session_id: string;
  mode: "targeted" | "guided";
  first_question: string;
  question: string;
  estimated_questions: number;
  // issue #245 (NEW-4) — the real configured ceiling (InterviewState.
  // hard_ceiling). estimated_questions is a soft midpoint that a real
  // session can legitimately overshoot by a wide margin; the progress
  // copy/bar now derives its total from this honest field instead.
  hard_ceiling?: number;
  gaps_total: number;
  gaps_remaining: number;
  choices: string[] | null;
  resumed: boolean;
  // issue #241 item 1 — the cluster-tracker anchor the backend actually
  // tracks (InterviewState.current_gap_index / addressed_gaps). current_gap_id
  // is the critical_gaps entry being asked about right now; for a real MODE A
  // gap-cluster session it is exactly the gap-cluster id used below. It is
  // undefined/null for degenerate sessions (no critical gaps) — treated the
  // same as "unknown" by the tracker.
  current_gap_id?: string | null;
  addressed_gap_ids?: string[];
  // #259 run-4 finding 9 — the server-tracked question count. On a fresh
  // session this is 1; on a RESUMED session (page refresh mid-interview) it
  // is the real count, so the counter doesn't reset to "1 of up to N" while
  // the server has already tracked real progress.
  questions_asked?: number;
}

interface ConflictSummary {
  conflict_id: string;
  field: string;
  old_value: string;
  new_value: string;
  // #604 — the entry the dispute hangs off, so this card can say WHICH job.
  // Optional: absent for a profile-level conflict, for a stale entity id, and
  // for any response produced before the backend carried them.
  entity_label?: string | null;
  section?: string | null;
  source?: string | null;
}

interface PendingConfirmation {
  question: string;
  options: string[];
  context?: Record<string, unknown>;
}

interface MessageResponse {
  complete: boolean;
  question?: string;
  gaps_remaining?: number;
  choices?: string[] | null;
  reason?: "gaps_resolved" | "user_ended" | "max_questions_reached";
  questions_asked?: number;
  gaps_resolved?: number;
  completeness_score?: number;
  pending_conflicts?: ConflictSummary[];
  pending_confirmations?: PendingConfirmation[];
  // issue #241 item 1 — see SessionCreateResponse; same honest anchor, per-turn.
  current_gap_id?: string | null;
  addressed_gap_ids?: string[] | null;
  // ADR-089 contract item 4 — the record this turn wrote on the cluster it
  // answered (coverage, open members, remaining budget).
  cluster_coverage?: TurnClusterCoverage | null;
  // #763 — the turn stated something and wrote nothing: the receipt's reason
  // (the same item the profile's Health hub shows). Absent/null = no receipt.
  no_write_receipt?: string | null;
}

/** One cluster-tracker row's state. `resolved` keeps its pre-ADR-089 meaning
 * for a legacy analysis (no coverage record); with a record it means COVERED —
 * a cluster the session merely moved past no longer shows a check mark. */
type TrackerStatus =
  | "current"
  | "resolved"
  | "partly_covered"
  | "declined"
  | "spent"
  | "left_open"
  | "pending";

/** Decorative, aria-hidden — the status is also spoken via an sr-only label.
 * `spent`, `left_open` and `partly_covered` draw a lucide icon instead (a
 * text ◐ rendered as a barely visible sliver in the UI font). */
/** Tracker chips — the gap card's member colours (ruling C-1), smaller. */
const TRACKER_CHIP: Record<MemberState, string> = {
  covered: "bg-success-container text-on-surface",
  partial: "bg-warning-container text-on-surface",
  gap: "bg-critical-container text-on-surface",
  declined: "bg-surface-container text-on-surface-variant line-through",
  closed: "bg-surface-container text-on-surface-variant",
};
const MEMBER_ORDER: Record<MemberState, number> = { gap: 0, partial: 1, closed: 2, covered: 3, declined: 4 };

const TRACKER_GLYPH: Record<Exclude<TrackerStatus, "spent" | "left_open" | "partly_covered">, string> = {
  resolved: "✓",
  current: "►",
  declined: "–",
  pending: "○",
};

function trackerStatus(
  cluster: GapCluster,
  opts: { current: boolean; addressed: boolean; turn: TurnClusterCoverage | null },
): TrackerStatus {
  if (opts.current) return "current";
  const hasRecord = opts.turn !== null || typeof cluster.coverage === "string";
  if (!hasRecord) return opts.addressed ? "resolved" : "pending";
  const view = clusterView(cluster, opts.turn);
  if (view.coverage === "covered") return "resolved";
  if (view.coverage === "declined") return "declined";
  if (view.budgetSpent) return "spent";
  // Ruling K-1/K-1e — the tracker keeps every cluster; one the candidate left
  // open on the gaps page is marked, never asked.
  if (view.leftOpen) return "left_open";
  if (view.coverage === "partly_covered" && (opts.addressed || view.asked > 0 || opts.turn)) {
    return "partly_covered";
  }
  return "pending";
}

interface Message {
  role: "assistant" | "user";
  content: string;
  // #763 — the no-write receipt this answer got, kept under it in the history.
  receipt?: string | null;
}

interface CompletionData {
  reason: "gaps_resolved" | "user_ended" | "max_questions_reached";
  questions_asked: number;
  gaps_resolved: number;
  // null = the response carried no score: render no gauge rather than a
  // made-up "0 %" (789:L47, M3).
  completeness_score: number | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// issue #256 — the backend's `detail` (whether a bare string, an array of
// Pydantic validation errors, or the structured `{error_code, message}` body
// routers now emit for provider failures) must NEVER be rendered verbatim:
// a raw Python exception message (the run-4 `'NoneType' object is not
// subscriptable` traceback) and a raw provider-JSON error payload have both
// reached the chat surface this way. Only the HTTP status and a structured
// `error_code` (never free text) are read off the response — translateError
// below maps those to a fully local, translated string. ADR-038: chrome UI
// text is `labels[lang]`, never backend-authored prose.
interface ApiErrorInfo {
  status: number;
  code?: string;
}

async function parseApiError(res: Response): Promise<ApiErrorInfo> {
  let code: string | undefined;
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof detail.error_code === "string") {
      code = detail.error_code;
    }
  } catch {
    // No body / invalid JSON — fall back to status-only classification below.
  }
  return { status: res.status, code };
}

function translateError(info: ApiErrorInfo, t: ReturnType<typeof useTranslations>): string {
  if (info.code === "provider_unavailable") return t("providerUnavailable");
  switch (info.status) {
    case 504: return t("http504");
    case 503: return t("http503");
    case 502: return t("http502");
    default:  return t("generic", { status: info.status });
  }
}

function getReasonLabel(reason: string, t: ReturnType<typeof useTranslations>): string {
  if (reason === "gaps_resolved") return t("reasonGapsResolved");
  if (reason === "user_ended") return t("reasonUserEnded");
  return t("reasonMaxReached");
}

// ---------------------------------------------------------------------------
// Animated completion gauge (SVG circle)
// ---------------------------------------------------------------------------

function ScoreGauge({
  score,
  label,
  note,
  testId,
}: {
  score: number;
  /** What the number IS — #789 "Interview sidebar ring": the match value is
   * never labelled as profile completeness, and vice versa. */
  label: string;
  note?: string;
  testId: string;
}) {
  const [displayed, setDisplayed] = useState(0);
  const pct = Math.round(score * 100);
  const radius = 54;
  const circumference = 2 * Math.PI * radius;

  useEffect(() => {
    let frame: number;
    const start = performance.now();
    const duration = 1200;
    function tick(now: number) {
      const progress = Math.min((now - start) / duration, 1);
      setDisplayed(Math.round(progress * pct));
      if (progress < 1) frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [pct]);

  const filled = circumference * (1 - displayed / 100);

  return (
    <div className="flex flex-col items-center gap-2" data-testid={testId} data-pct={pct}>
      <svg width="128" height="128" viewBox="0 0 128 128">
        <circle cx="64" cy="64" r={radius} fill="none" stroke="#e5e7eb" strokeWidth="10" />
        <circle
          cx="64" cy="64" r={radius}
          fill="none"
          stroke="#C9A84C"
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={filled}
          transform="rotate(-90 64 64)"
          style={{ transition: "stroke-dashoffset 0.05s linear" }}
        />
        {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
        <text x="64" y="68" textAnchor="middle" fontSize="22" fontWeight="700" fill="#1B4F72" fontFamily="Poppins, sans-serif">{displayed}%</text>
      </svg>
      <p className="text-xs font-semibold text-on-surface font-body text-center">{label}</p>
      {note && <p className="-mt-1 text-xs text-on-surface-variant font-body text-center">{note}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Interview intro (#789 "drops straight into Q1", mock F1-1 c, COPY.md §C)
// ---------------------------------------------------------------------------

function InterviewIntro({
  roleTitle,
  openCount,
  typicalQuestions,
  onStart,
  onBack,
}: {
  roleTitle: string | null;
  openCount: number;
  typicalQuestions: number | null;
  onStart: () => void;
  onBack: () => void;
}) {
  const t = useTranslations("interview");
  const rows: { icon: React.ReactNode; title: string; body: string }[] = [
    {
      icon: <HelpCircle aria-hidden="true" className="h-4 w-4" />,
      title: t("introWhyTitle"),
      body:
        openCount > 0 ? t("introWhyBody", { count: openCount }) : t("introWhyBodyNoneOpen"),
    },
    {
      icon: <Clock aria-hidden="true" className="h-4 w-4" />,
      title: t("introHowLongTitle"),
      body: typicalQuestions
        ? t("introHowLongBody", { count: typicalQuestions, low: typicalQuestions, high: typicalQuestions * 2 })
        : t("introHowLongBodyNoEstimate"),
    },
    {
      icon: <Square aria-hidden="true" className="h-4 w-4" />,
      title: t("introStopTitle"),
      body: t("introStopBody"),
    },
    {
      icon: <Check aria-hidden="true" className="h-4 w-4" />,
      title: t("introHonestTitle"),
      body: t("introHonestBody"),
    },
  ];
  return (
    <Card data-testid="interview-intro" className="p-6 md:p-7">
      <p className="text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
        {roleTitle ? t("introEyebrowRole", { role: roleTitle }) : t("introEyebrow")}
      </p>
      <h2 className="font-heading text-xl font-bold text-on-surface mt-1 mb-5">{t("introTitle")}</h2>
      <ul className="space-y-4 mb-6">
        {rows.map((row) => (
          <li key={row.title} className="flex gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-container text-primary">
              {row.icon}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-on-surface">{row.title}</p>
              <p className="text-sm text-on-surface-variant">{row.body}</p>
            </div>
          </li>
        ))}
      </ul>
      <div className="flex flex-col-reverse sm:flex-row sm:items-center gap-3">
        <Button data-testid="interview-intro-start" size="lg" onClick={onStart}>
          {t("introStart")}
        </Button>
        <button
          type="button"
          data-testid="interview-intro-back"
          onClick={onBack}
          className="text-sm text-on-surface-variant underline hover:no-underline"
        >
          {t("introBack")}
        </button>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Conflict card
// ---------------------------------------------------------------------------

function ConflictCard({
  conflict,
  onResolved,
}: {
  conflict: ConflictSummary;
  onResolved: () => void;
}) {
  const t = useTranslations("interview");
  // #604 — the conflict wording is shared with the Health hub's card, so both
  // surfaces name the entry the same way; the strings live in the `health`
  // namespace where #626 defined the convention (`lib/conflict-display.ts`).
  const tHealth = useTranslations("health");
  const tProfile = useTranslations("profile");
  const described = describeConflict(
    {
      entity_label: conflict.entity_label,
      section: conflict.section,
      field: conflict.field,
      existing_value_display: displayValue(conflict.old_value),
      incoming_value_display: displayValue(conflict.new_value),
      incoming_source: conflict.source,
    },
    tHealth,
    tProfile,
  );
  const [resolving, setResolving] = useState(false);

  async function resolve(resolution: "existing" | "incoming") {
    setResolving(true);
    try {
      await fetch(`${API_BASE}/api/profile/conflicts/${conflict.conflict_id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution }),
      });
    } catch {
      // best-effort
    } finally {
      setResolving(false);
      onResolved();
    }
  }

  return (
    <div data-testid="conflict-card" className="rounded-lg border border-warning/40 bg-warning/5 p-4 mt-3">
      <p className="text-sm font-semibold text-neutral-dark mb-1">{t("discrepancyDetected")}</p>
      {/* #604: was `conflictDetail` — the raw "end_date: 'x' vs 'y'" shape,
          which named the field but never WHICH entry. Same composition the
          Health hub has shown since #626. */}
      <div data-testid="conflict-detail" className="mb-3">
        <p className="text-xs font-medium text-neutral-dark">{described.heading}</p>
        <p className="text-xs text-gray-600 mt-1">{described.existingRow}</p>
        <p className="text-xs text-gray-600">{described.incomingRow}</p>
      </div>
      <div className="flex gap-2">
        <Button
          data-testid="conflict-keep-old"
          size="sm"
          variant="outline"
          onClick={() => void resolve("existing")}
          disabled={resolving}
          className="text-xs"
        >
          {t("keepOld", { value: displayValue(conflict.old_value) })}
        </Button>
        <Button
          data-testid="conflict-use-new"
          size="sm"
          variant="outline"
          onClick={() => void resolve("incoming")}
          disabled={resolving}
          className="text-xs"
        >
          {t("useNew", { value: displayValue(conflict.new_value) })}
        </Button>
      </div>
    </div>
  );
}

// US185 — the reconciler is unsure whether a fact matches an existing profile
// entry (synonym role, project-vs-position, DE↔EN employer). It asks; it never
// guesses. The question itself shows as the current question; this card turns
// the engine's options into one-tap answers.
function ConfirmationCard({
  confirmation,
  onChoose,
  disabled,
}: {
  confirmation: PendingConfirmation;
  onChoose: (option: string) => void;
  disabled: boolean;
}) {
  const t = useTranslations("interview");
  return (
    <div data-testid="confirmation-card" className="mt-4 space-y-2">
      <p className="text-xs text-gray-400">{t("confirmHint")}</p>
      <div className="flex flex-wrap gap-2">
        {confirmation.options.map((option) => (
          <Button
            key={option}
            type="button"
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={() => onChoose(option)}
            className="text-xs"
          >
            {option}
          </Button>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function InterviewPage({
  params,
}: {
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  const router = useRouter();
  const t = useTranslations("interview");
  const tGaps = useTranslations("gaps");
  const tErrors = useTranslations("errors");
  const tCommon = useTranslations("common");
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [flowState, setFlowState] = useState<FlowState | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [estimatedQuestions, setEstimatedQuestions] = useState(5);
  // #789 "drops straight into Q1" — the soft question estimate the intro
  // names ("about N questions"); `estimatedQuestions` holds the hard ceiling.
  const [typicalQuestions, setTypicalQuestions] = useState<number | null>(null);
  // The intro card before the first question (mock F1-1 c): a fresh session
  // only — a resumed session (or one past Q1) goes straight to its question.
  const [introOpen, setIntroOpen] = useState(false);
  const [questionsAsked, setQuestionsAsked] = useState(1);
  const [resumed, setResumed] = useState(false);
  const [showResumeBanner, setShowResumeBanner] = useState(false);
  const [isCategoryB, setIsCategoryB] = useState(false);

  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  // #256 — the exact user text a failed turn tried to send, so the Retry
  // affordance can re-send it verbatim instead of forcing a re-type. null
  // once there is nothing to retry (no failure yet, or it just succeeded).
  const [retryMessage, setRetryMessage] = useState<string | null>(null);

  const [completion, setCompletion] = useState<CompletionData | null>(null);
  const [advancingToCV, setAdvancingToCV] = useState(false);

  const [pendingConflicts, setPendingConflicts] = useState<ConflictSummary[]>([]);
  const [pendingConfirmations, setPendingConfirmations] = useState<PendingConfirmation[]>([]);
  const [showDoneConfirm, setShowDoneConfirm] = useState(false);
  // #763 — the last answer and its no-write receipt, shown between that answer
  // and the next question until the candidate answers again.
  const [lastReceipt, setLastReceipt] = useState<{ answer: string; reason: string } | null>(null);

  // Split-screen state
  const [gapAnalysis, setGapAnalysis] = useState<GapAnalysisData | null>(null);
  const [resolvedClusterIds, setResolvedClusterIds] = useState<Set<string>>(new Set());
  const [currentClusterId, setCurrentClusterId] = useState<string | null>(null);
  // ADR-089 — the record each turn wrote, per cluster, over the analysis the
  // page loaded (so the tracker shows "partly covered", not a check mark).
  const [turnCoverage, setTurnCoverage] = useState<Record<string, TurnClusterCoverage>>({});
  const [choices, setChoices] = useState<string[] | null>(null);
  const [matchScore, setMatchScore] = useState<number | null>(null);

  // #256 — extracted from the mount effect so the "couldn't start the
  // interview" screen below can call it again from a Retry button. Before
  // this fix a session-creation failure (e.g. a provider 503 on the very
  // first LLM call) left `sessionId` permanently null: the page rendered
  // normally with the error banner, but sendAnswer() silently no-ops forever
  // on a null sessionId — a full page reload was the only way out. Retry now
  // re-runs exactly the flow the initial mount ran.
  const initSession = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      // Load flow state
      const fsRes = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
      if (!fsRes.ok) throw new Error(tErrors("flowNotFound"));
      const fs: FlowState = await fsRes.json();
      setFlowState(fs);

      // Fetch gap analysis for cluster tracker
      fetch(`${API_BASE}/api/job/${fs.job_id}/gaps`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data: GapAnalysisData | null) => {
          if (data) {
            setGapAnalysis(data);
            setMatchScore(data.match_score);
          }
        })
        .catch(() => {});

      // Create or resume interview session
      const sessionRes = await fetch(`${API_BASE}/api/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: fs.job_id }),
      });
      if (!sessionRes.ok) {
        const info = await parseApiError(sessionRes);
        throw new Error(translateError(info, tErrors));
      }
      const sessionData: SessionCreateResponse = await sessionRes.json();

      setSessionId(sessionData.session_id);
      // #259 run-4 finding 9 — restore the real server-tracked count on
      // resume (page refresh mid-interview), instead of leaving the "1" the
      // state was initialised with. A fresh session's own questions_asked is
      // already 1, so this is a no-op on first creation.
      setQuestionsAsked(sessionData.questions_asked ?? 1);
      // issue #245 (NEW-4) — prefer the real hard_ceiling over the soft
      // "~7" midpoint estimate so "Question X of …" cannot be honestly
      // overshot by a session that legitimately runs to the true ceiling.
      setEstimatedQuestions(sessionData.hard_ceiling || sessionData.estimated_questions || 5);
      setTypicalQuestions(sessionData.estimated_questions || null);
      setIntroOpen(!sessionData.resumed && (sessionData.questions_asked ?? 1) <= 1);
      setMessages([{ role: "assistant", content: sessionData.question ?? sessionData.first_question }]);
      setChoices(sessionData.choices ?? null);

      // issue #241 item 1 — anchor the cluster tracker to the server's own
      // current_gap_id / addressed_gap_ids (InterviewState.current_gap_index /
      // addressed_gaps) rather than guessing from array index arithmetic. On
      // resume this also correctly restores already-resolved clusters instead
      // of starting the tracker's resolved set empty.
      if (sessionData.current_gap_id) {
        setCurrentClusterId(sessionData.current_gap_id);
      }
      if (sessionData.addressed_gap_ids?.length) {
        setResolvedClusterIds(new Set(sessionData.addressed_gap_ids));
      }

      if (sessionData.resumed) {
        setResumed(true);
        setShowResumeBanner(true);
      }

      // Advance flow to 'interview' step
      const advRes = await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ step: "interview", artifact_id: sessionData.session_id }),
      });
      // 409 is benign only when the flow really is at the interview step
      // (resume).  Otherwise the transition was rejected — the flow is on a
      // different step, so let the flow index re-route to the real one.
      if (advRes.status === 409 && fs.current_step !== "interview") {
        router.replace(`/flow/${flowId}`);
        return;
      }
      if (!advRes.ok && advRes.status !== 409) {
        console.warn("advance_flow:", await advRes.text());
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : tErrors("failedToStart"));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tErrors is not identity-stable; re-running would re-create sessions
  }, [flowId, router]);

  useEffect(() => {
    void initSession();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- initSession is stable enough for mount-only; re-running would re-create sessions
  }, [flowId]);

  async function refreshGapAnalysis() {
    const jobId = flowState?.job_id;
    if (!jobId) return;
    try {
      const r = await fetch(`${API_BASE}/api/job/${jobId}/gaps`);
      if (r.ok) setGapAnalysis((await r.json()) as GapAnalysisData);
    } catch {
      // best-effort: the count keeps the last row it read
    }
  }

  // Fallback only: if the session response didn't supply a current_gap_id
  // (e.g. a degenerate/legacy session), default to the first cluster once gap
  // analysis loads. The init() effect above sets the real server-supplied
  // value unconditionally once it arrives, so this never overrides it.
  useEffect(() => {
    if (gapAnalysis && gapAnalysis.gap_clusters.length > 0 && !currentClusterId) {
      setCurrentClusterId(gapAnalysis.gap_clusters[0].id);
    }
  }, [gapAnalysis, currentClusterId]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  async function sendAnswer(messageOverride?: string) {
    const userMsg = (messageOverride ?? answer).trim();
    if (!sessionId || !userMsg || sending) return;

    setAnswer("");
    setError("");
    setRetryMessage(null);
    setShowDoneConfirm(false);
    setSending(true);
    setPendingConflicts([]);
    setPendingConfirmations([]);
    setLastReceipt(null);
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);

    try {
      const res = await fetch(`${API_BASE}/api/session/${sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg }),
      });
      if (!res.ok) {
        const info = await parseApiError(res);
        throw new Error(translateError(info, tErrors));
      }
      const data: MessageResponse = await res.json();

      if (data.no_write_receipt) {
        const reason = data.no_write_receipt;
        setLastReceipt({ answer: userMsg, reason });
        setMessages((prev) =>
          prev.map((m, i) => (i === prev.length - 1 && m.role === "user" ? { ...m, receipt: reason } : m)),
        );
      }

      const cc = data.cluster_coverage;
      if (cc?.cluster_id) {
        setTurnCoverage((prev) => ({ ...prev, [cc.cluster_id]: cc }));
      }

      if (data.complete) {
        setCompletion({
          reason: data.reason ?? "gaps_resolved",
          questions_asked: data.questions_asked ?? questionsAsked,
          gaps_resolved: data.gaps_resolved ?? 0,
          completeness_score: data.completeness_score ?? null,
        });
      } else {
        const nextQ = data.question ?? "";
        setMessages((prev) => [...prev, { role: "assistant", content: nextQ }]);
        setQuestionsAsked((q) => q + 1);

        // Detect cultural sensitivity hint (category B) from question tone
        const lowerQ = nextQ.toLowerCase();
        setIsCategoryB(lowerQ.includes("confirm") || lowerQ.includes("likely") || lowerQ.includes("based on your background"));

        if (data.pending_conflicts?.length) {
          setPendingConflicts(data.pending_conflicts);
        }

        if (data.pending_confirmations?.length) {
          setPendingConfirmations(data.pending_confirmations);
        }

        // Update choices
        setChoices(data.choices ?? null);

        // #789 (ruling F1-2) — a turn updates the ledger rows in place; re-read
        // the row so the one open-requirement count moves with the answers.
        void refreshGapAnalysis();

        // Advance cluster tracker (issue #241 item 1) — driven by the server's
        // own current_gap_id / addressed_gap_ids, never by re-deriving progress
        // from gaps_remaining via array-index arithmetic (that conflated
        // fine-grained gap counts with coarse cluster indices: a follow-up
        // question on the SAME gap was marking it ✓, and skipped/gate/conflict
        // entries desynced the count from the cluster array). A turn that
        // stays on the current gap (follow-up) reports the same current_gap_id
        // and an unchanged addressed_gap_ids — so the tracker correctly leaves
        // that cluster un-resolved instead of guessing it moved on.
        if (data.addressed_gap_ids?.length) {
          setResolvedClusterIds((prev) => {
            const next = new Set(prev);
            for (const id of data.addressed_gap_ids ?? []) next.add(id);
            return next;
          });
        }
        if (data.current_gap_id) {
          setCurrentClusterId(data.current_gap_id);
        }

        // #789 — the sidebar ring shows the MATCH value of the analysis the
        // page loaded; a turn's `completeness_score` is profile completeness
        // (a different number, and null on a non-final turn), so it never
        // lands on this ring.
      }
    } catch (e: unknown) {
      // #256 — the backend confirmed a failed turn never partially commits
      // (single commit per turn, #179/#245 pattern — see the interview
      // service's resumability tests), so the exact same message is always
      // safe to resend as-is. Drop the optimistic user bubble the retry will
      // re-add, rather than leaving a duplicate once it succeeds.
      setMessages((prev) => (prev.at(-1)?.role === "user" ? prev.slice(0, -1) : prev));
      setRetryMessage(userMsg);
      setError(e instanceof Error ? e.message : tErrors("failedToStart"));
    } finally {
      setSending(false);
    }
  }

  async function advanceToCV() {
    setAdvancingToCV(true);
    try {
      await fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ step: "cv_generation" }),
      });
    } catch {
      // best-effort
    }
    router.push(`/flow/${flowId}/cv`);
  }

  // #789 (ruling F1-2) — ONE count, the same helper the gaps page reads: the
  // row's open requirements (the ledger, re-read after every turn).
  const openCount = requirementCounts(gapAnalysis).open;
  const themeCount = openThemeCount(gapAnalysis?.gap_clusters);
  const currentClusterLabel =
    (currentClusterId && gapAnalysis?.gap_clusters.find((c) => c.id === currentClusterId)?.label) || null;
  // Progress by topic, not by the hard question ceiling ("1 of up to 18"):
  // the share of the page's topics this session has moved past.
  const totalTopics = gapAnalysis?.gap_clusters.length ?? 0;
  const progressPct =
    totalTopics > 0
      ? Math.min(Math.round((resolvedClusterIds.size / totalTopics) * 100), 100)
      : Math.min(Math.round((questionsAsked / Math.max(estimatedQuestions, 1)) * 100), 100);

  // -------------------------------------------------------------------------
  // Loading
  // -------------------------------------------------------------------------

  if (loading) {
    return (
      <div data-testid="interview-loading" className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <div className="animate-spin h-8 w-8 border-4 border-teal border-t-transparent rounded-full mx-auto mb-4" />
          <p className="text-sm text-gray-500 font-body">{t("loading")}</p>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Session-start failure (#256) — a provider outage on the very first LLM
  // call (session creation) used to leave sessionId permanently null: the
  // interview screen below rendered anyway with only an error banner, and
  // sendAnswer() silently no-ops on a null sessionId — no error, no recovery
  // affordance, wedged until a manual reload (the pinned run-4 vector 3).
  // ------------------------------------------------------------------------
  if (!sessionId && error) {
    return (
      <div
        data-testid="interview-start-failed"
        className="flex items-center justify-center min-h-[60vh] px-4"
      >
        <div className="text-center max-w-sm">
          <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-critical/10 mb-4">
            <svg className="w-6 h-6 text-critical" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
            </svg>
          </div>
          <p className="font-heading text-lg font-semibold text-neutral-dark mb-2">
            {tErrors("failedToStart")}
          </p>
          <p className="text-sm text-gray-500 font-body mb-6">{error}</p>
          <Button data-testid="retry-session-button" onClick={() => void initSession()}>
            {tCommon("retry")}
          </Button>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Completion screen (19.2)
  // -------------------------------------------------------------------------

  if (completion) {
    return (
      <div data-testid="completion-screen" className="max-w-2xl mx-auto animate-fade-in">
        <Card className="p-8 text-center">
          {/* #763 — the final answer's receipt must survive the completion
              screen, or the last turn reads as "nothing changed". */}
          {lastReceipt && (
            <div className="text-left">
              <NoWriteReceipt answer={lastReceipt.answer} reason={lastReceipt.reason} />
            </div>
          )}
          <div className="mb-6">
            <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-success/10 mb-4">
              {completion.reason === "gaps_resolved" ? (
                <svg className="w-8 h-8 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                </svg>
              ) : (
                <svg className="w-8 h-8 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              )}
            </div>
            <h2 className="font-heading text-2xl font-bold text-neutral-dark mb-2">
              {getReasonLabel(completion.reason, t)}
            </h2>
            <p className="text-sm text-gray-500 font-body">
              {completion.reason === "gaps_resolved"
                ? t("completionGapsResolved")
                : t("completionOther")}
            </p>
          </div>

          {/* Stats row */}
          <div className="flex justify-center gap-8 mb-8">
            <div className="text-center">
              <p className="text-3xl font-bold font-heading text-primary">{completion.questions_asked}</p>
              <p className="text-xs text-gray-500 font-body mt-1">{t("questionsAnswered")}</p>
            </div>
            <div className="text-center">
              <p className="text-3xl font-bold font-heading text-success">{completion.gaps_resolved}</p>
              <p className="text-xs text-gray-500 font-body mt-1">{t("gapsResolved")}</p>
            </div>
          </div>

          {/* Animated gauge */}
          {completion.completeness_score !== null && (
            <div className="flex justify-center mb-8">
              <ScoreGauge
                score={completion.completeness_score}
                label={t("profileCompleteness")}
                testId="completeness-gauge"
              />
            </div>
          )}

          {/* What we added from your answers — before generation (ADR-040, US148 / JF-M-5.2) */}
          <div className="mb-8" data-testid="interview-summary-section">
            <DecisionTrailReview mode="interview" onFix={() => router.push("/profile")} />
          </div>

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row gap-3 justify-center">
            <Button
              data-testid="generate-cv-button"
              size="lg"
              onClick={() => void advanceToCV()}
              disabled={advancingToCV}
              className="min-w-[220px]"
            >
              {advancingToCV ? t("advancingToCV") : t("generateCV")}
            </Button>
            <Link
              href="/profile"
              data-testid="view-profile-link"
              className="inline-flex items-center justify-center text-sm text-teal underline hover:no-underline px-4 py-2"
            >
              {t("viewProfile")}
            </Link>
          </div>
        </Card>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Interview screen (19.1) — split-screen layout
  // -------------------------------------------------------------------------

  const currentQuestion = messages.filter((m) => m.role === "assistant").at(-1)?.content ?? "";
  const exchangeCount = messages.length - 1;

  return (
    <div data-testid="interview-page" className="min-h-screen bg-gray-50">
      {/* Mobile sticky header pill */}
      <div className="md:hidden sticky top-0 z-10 bg-white border-b border-gray-200 px-4 py-2 flex items-center justify-between">
        <span className="text-xs font-medium text-neutral-dark truncate max-w-[60%]">
          {currentClusterId
            ? (gapAnalysis?.gap_clusters.find((c) => c.id === currentClusterId)?.label ?? t("loading"))
            : t("loading")}
        </span>
        {matchScore !== null && (
          // eslint-disable-next-line formatjs/no-literal-string-in-jsx
          <span className="text-xs font-semibold text-teal">{Math.round(matchScore * 100)}%</span>
        )}
      </div>

      <div className="flex flex-col md:flex-row min-h-screen md:h-screen">
        {/* LEFT PANEL — 65% */}
        <div className="flex-1 md:w-[65%] overflow-y-auto p-4 md:p-8">
          {/* Resume banner (19.5) */}
          {showResumeBanner && (
            <div data-testid="resume-banner" className="mb-4 flex items-center justify-between px-4 py-3 rounded-lg bg-teal/10 border border-teal/30">
              <div className="flex items-center gap-2">
                <svg className="w-4 h-4 text-teal shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M12 2a10 10 0 100 20A10 10 0 0012 2z" />
                </svg>
                <p className="text-sm text-teal font-medium">{t("resumeBanner")}</p>
              </div>
              <button
                onClick={() => setShowResumeBanner(false)}
                className="text-teal hover:text-teal/70 text-lg leading-none"
                aria-label={tCommon("close")}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                <span aria-hidden="true">×</span>
              </button>
            </div>
          )}

          {introOpen ? (
            <InterviewIntro
              roleTitle={flowState?.job_summary?.role_title ?? null}
              openCount={openCount}
              typicalQuestions={typicalQuestions}
              onStart={() => {
                setIntroOpen(false);
                // The question is already loaded behind the intro.
                requestAnimationFrame(() => textareaRef.current?.focus());
              }}
              onBack={() => router.push(`/flow/${flowId}/gaps`)}
            />
          ) : (
          <>
          {/* Header — #789: one number (open requirements, ruling F1-2) and
              the topic being asked, instead of "Frage 1 von bis zu 18" and a
              fourth gap count. */}
          <div className="mb-4" data-testid="interview-header">
            <div className="flex items-start justify-between gap-3 mb-1">
              <p className="text-xs font-medium text-on-surface-variant font-body min-w-0">
                <span data-testid="interview-question-number">{t("questionNumber", { current: questionsAsked })}</span>
                {currentClusterLabel && (
                  <>
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                    <span aria-hidden="true">{" · "}</span>
                    <span data-testid="interview-current-topic">
                      {t("currentTopic", { label: currentClusterLabel })}
                    </span>
                  </>
                )}
              </p>
              {gapAnalysis && (
                <p data-testid="interview-open-count" className="shrink-0 text-xs font-semibold text-primary font-body">
                  {t("openRequirements", { count: openCount })}
                </p>
              )}
            </div>
            <ProgressLinear value={progressPct} className="h-1.5" />
          </div>

          {/* #763 — what became of the last answer, before the next question. */}
          {lastReceipt && <NoWriteReceipt answer={lastReceipt.answer} reason={lastReceipt.reason} />}

          {/* Question card */}
          <Card data-testid="question-card" className="p-6 mb-4 border-l-4 border-l-primary">
            {/* Cultural sensitivity badge (19.8) */}
            {isCategoryB && (
              <div data-testid="cultural-sensitivity-badge" className="flex items-center gap-2 mb-3 px-3 py-2 rounded-md bg-teal/10 border border-teal/20">
                <svg className="w-4 h-4 text-teal shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M12 2a10 10 0 100 20A10 10 0 0012 2z" />
                </svg>
                <p className="text-xs text-teal font-medium">
                  {t("categoryBBadge")}
                </p>
              </div>
            )}

            <p data-testid="interview-question" className="font-heading text-lg font-semibold text-neutral-dark leading-relaxed">
              {currentQuestion}
            </p>

            {/* Conflict card (19.7) */}
            {pendingConflicts.map((conflict) => (
              <ConflictCard
                key={conflict.conflict_id}
                conflict={conflict}
                onResolved={() => setPendingConflicts((prev) => prev.filter((c) => c.conflict_id !== conflict.conflict_id))}
              />
            ))}

            {/* Reconciliation confirmation (US185) — ask, never guess identity */}
            {pendingConfirmations.length > 0 && (
              <ConfirmationCard
                confirmation={pendingConfirmations[0]}
                disabled={sending}
                onChoose={(option) => void sendAnswer(option)}
              />
            )}
          </Card>

          {/* Message history (collapsed, scrollable) */}
          {messages.length > 2 && (
            <details className="mb-4">
              <summary className="cursor-pointer text-xs text-teal hover:underline mb-2">
                {t("viewHistory", { count: exchangeCount })}
              </summary>
              <div className="max-h-48 overflow-y-auto space-y-2 px-1 py-2">
                {messages.slice(0, -1).map((msg, i) => (
                  <div key={i}>
                    <div
                      className={cn(
                        "max-w-[85%] rounded-xl px-3 py-2 text-sm",
                        msg.role === "user"
                          ? "ml-auto bg-primary text-white rounded-br-sm"
                          : "bg-gray-100 text-neutral-dark rounded-bl-sm",
                      )}
                    >
                      {msg.content}
                    </div>
                    {msg.role === "user" && msg.receipt && (
                      <NoWriteReceiptLine
                        reason={msg.receipt}
                        className="ml-auto mt-1 max-w-[85%] justify-end text-right"
                      />
                    )}
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* Error (#256) — a failed turn's translated message, plus a Retry
              affordance that re-sends the exact same answer (the backend
              guarantees a failed turn never partially commits, so this is
              always safe — see the interview service's resumability tests). */}
          {error && (
            <div className="mb-3 px-3 py-2 rounded-lg bg-critical/10 border border-critical/20 flex items-center justify-between gap-3">
              <p className="text-sm text-critical">{error}</p>
              {retryMessage && (
                <Button
                  data-testid="retry-answer-button"
                  size="sm"
                  variant="outline"
                  disabled={sending}
                  onClick={() => void sendAnswer(retryMessage)}
                  className="shrink-0"
                >
                  {tCommon("retry")}
                </Button>
              )}
            </div>
          )}

          {/* Done-signal confirmation (19.6) */}
          {showDoneConfirm && (
            <div data-testid="done-confirm" className="mb-3 px-4 py-3 rounded-lg bg-amber/10 border border-amber/30">
              <p className="text-sm font-medium text-neutral-dark mb-2">
                {openCount > 0
                  ? t("openRequirementsConfirm", { count: openCount })
                  : t("endConfirm")}
              </p>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => void sendAnswer("done")}>
                  {t("endInterview")}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setShowDoneConfirm(false)}>
                  {t("continue")}
                </Button>
              </div>
            </div>
          )}

          {/* Choice cards — shown when backend provides choices and session is active.
              Suppressed while a confirmation is pending (its options render in the card). */}
          {choices && choices.length > 0 && !completion && pendingConfirmations.length === 0 && (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-gray-400">{t("choiceCardHint")}</p>
              {choices.map((choice) => (
                <button
                  key={choice}
                  type="button"
                  className={cn(
                    "w-full text-left rounded-lg border px-4 py-3 text-sm transition-colors",
                    answer === choice
                      ? "border-teal bg-teal/5 font-medium text-neutral-dark"
                      : "border-gray-200 bg-white text-gray-700 hover:border-teal/50 hover:bg-gray-50",
                  )}
                  onClick={() => setAnswer(choice)}
                >
                  {choice}
                </button>
              ))}
            </div>
          )}

          {/* Input area */}
          <div className="bg-white rounded-xl border border-gray-200 p-3 shadow-sm mt-4">
            <textarea
              data-testid="answer-textarea"
              ref={textareaRef}
              className={cn(
                "w-full resize-none font-body text-sm text-neutral-dark placeholder-gray-400",
                "border border-gray-200 rounded-lg px-3 py-2 min-h-[120px]",
                "focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary",
                "disabled:opacity-50 disabled:cursor-not-allowed transition-colors",
              )}
              placeholder={t("placeholder")}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendAnswer();
                }
              }}
              disabled={sending}
              rows={3}
            />
            <div className="flex items-center justify-between mt-2">
              <button
                data-testid="done-button"
                className={cn(
                  "text-xs text-gray-400 hover:text-gray-600 transition-colors",
                  "disabled:opacity-30 disabled:cursor-not-allowed",
                )}
                onClick={() => setShowDoneConfirm(true)}
                disabled={sending}
                type="button"
              >
                {t("iAmDone")}
              </button>
              <div className="flex items-center gap-2">
                {sending && (
                  <div className="animate-spin h-4 w-4 border-2 border-primary border-t-transparent rounded-full" />
                )}
                <Button
                  data-testid="send-button"
                  size="sm"
                  onClick={() => void sendAnswer()}
                  disabled={!answer.trim() || sending}
                >
                  {tCommon("send")}
                </Button>
              </div>
            </div>
          </div>
          </>
          )}
        </div>

        {/* RIGHT PANEL — 35%, hidden on mobile */}
        <aside className="hidden md:flex md:w-[35%] flex-col border-l border-gray-200 bg-white overflow-y-auto p-6">
          {/* Match score gauge */}
          {/* #789 — this ring is the analysis' MATCH value, labelled as such
              (it used to read "Profilvollständigkeit"). */}
          {matchScore !== null && (
            <ScoreGauge
              score={matchScore}
              label={t("matchWithJob")}
              note={t("matchBeforeInterview")}
              testId="match-gauge"
            />
          )}

          {/* Cluster tracker */}
          {gapAnalysis && gapAnalysis.gap_clusters.length > 0 && (
            <div className="mt-6 space-y-2">
              <p
                data-testid="tracker-open-count"
                className="text-xs font-semibold text-on-surface-variant uppercase tracking-wide mb-3"
              >
                {t("trackerHeading", { count: openCount, topics: themeCount })}
              </p>
              {gapAnalysis.gap_clusters.map((cluster) => {
                const turn = turnCoverage[cluster.id] ?? null;
                const status = trackerStatus(cluster, {
                  current: cluster.id === currentClusterId,
                  addressed: resolvedClusterIds.has(cluster.id),
                  turn,
                });
                // Ruling C-4 — the glyph and edge carry the SAME worst-
                // requirement tone as the cluster's card on the gaps page (one
                // helper, `clusterView`); the glyph SHAPE carries the state.
                const view = clusterView(cluster, turn);
                const tone = TONE[view.tone];
                const declinedTerms = view.members
                  .filter((m) => m.state === "declined")
                  .map((m) => m.term);
                // Members, open ones first; a legacy row with no members
                // falls back to the posting terms, shown neutrally.
                const shownMembers: { term: string; state: MemberState }[] =
                  view.members.length > 0
                    ? [...view.members]
                        .sort((a, b) => MEMBER_ORDER[a.state] - MEMBER_ORDER[b.state])
                        .slice(0, 4)
                    : cluster.jd_skills.slice(0, 3).map((term) => ({ term, state: "closed" as MemberState }));
                const srLabel =
                  status === "resolved" && typeof cluster.coverage === "string"
                    ? tGaps("coverageCovered")
                    : status === "partly_covered"
                      ? tGaps("coveragePartly")
                      : status === "declined"
                        ? tGaps("coverageDeclinedTerms", { terms: declinedTerms.join(", ") })
                        : status === "spent"
                          ? tGaps("trackerNoMoreQuestions")
                          : status === "left_open"
                            ? tGaps("trackerLeftOpen")
                            : null;
                return (
                  <div
                    key={cluster.id}
                    data-testid={`gap-cluster-${cluster.id}`}
                    data-status={status}
                    data-tone={view.tone}
                    className={cn(
                      "rounded-md px-3 py-2 text-xs border-l-2 transition-colors",
                      tone.border,
                      status === "resolved" && "bg-success-container/40 text-on-surface-variant",
                      status === "current" && "bg-teal/5 text-neutral-dark font-medium",
                      status === "partly_covered" && "text-on-surface",
                      (status === "declined" || status === "spent" || status === "left_open") &&
                        "text-on-surface-variant",
                      status === "pending" && "text-gray-400",
                    )}
                  >
                    <div className="relative flex items-center gap-2">
                      {status === "spent" ? (
                        <Lock aria-hidden="true" className={cn("h-3 w-3 shrink-0", tone.icon)} />
                      ) : status === "left_open" ? (
                        <Archive aria-hidden="true" className={cn("h-3 w-3 shrink-0", tone.icon)} />
                      ) : status === "partly_covered" ? (
                        <Contrast aria-hidden="true" className={cn("h-3 w-3 shrink-0", tone.icon)} />
                      ) : (
                        <span aria-hidden="true" className={tone.icon}>
                          {TRACKER_GLYPH[status]}
                        </span>
                      )}
                      <span className="truncate">{cluster.label}</span>
                      {srLabel && <span className="sr-only">{srLabel}</span>}
                    </div>
                    {/* #789 — the chips are the cluster's MEMBERS with their own
                        state (the gaps page's chips), never the raw posting
                        terms: a declined topic no longer files a confirmed
                        skill (ISO 9001 under the IFS/BRC denial) under
                        "nicht vorhanden". */}
                    {status === "declined" && declinedTerms.length > 0 && (
                      <p data-testid="tracker-declined-terms" aria-hidden="true" className="mt-1 ml-5 text-[11px] text-on-surface-variant">
                        {tGaps("coverageDeclinedTerms", { terms: declinedTerms.join(", ") })}
                      </p>
                    )}
                    {shownMembers.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1 ml-5">
                        {shownMembers.map((m) => (
                          <span
                            key={m.term}
                            data-testid="tracker-member"
                            data-state={m.state}
                            className={cn("rounded-full px-1.5 py-0.5 text-[10px]", TRACKER_CHIP[m.state])}
                          >
                            {m.term}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </aside>
      </div>

      <div ref={chatEndRef} />
    </div>
  );
}
