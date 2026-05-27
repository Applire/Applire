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


import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { use } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ProgressLinear } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface FlowState {
  job_id: string;
  job_summary?: { role_title: string; required_skills?: string[] } | null;
  interview_summary?: { session_id: string; mode: string } | null;
}

interface GapCluster {
  id: string;
  label: string;
  category: "B" | "C";
  gaps: string[];
  jd_skills: string[];
  jd_context: string;
}

interface GapAnalysisData {
  id: string;
  match_score: number;
  gap_clusters: GapCluster[];
}

interface SessionCreateResponse {
  session_id: string;
  mode: "targeted" | "guided";
  first_question: string;
  question: string;
  estimated_questions: number;
  gaps_total: number;
  gaps_remaining: number;
  choices: string[] | null;
  resumed: boolean;
}

interface ConflictSummary {
  conflict_id: string;
  field: string;
  old_value: string;
  new_value: string;
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
}

interface Message {
  role: "assistant" | "user";
  content: string;
}

interface CompletionData {
  reason: "gaps_resolved" | "user_ended" | "max_questions_reached";
  questions_asked: number;
  gaps_resolved: number;
  completeness_score: number;
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

function translateError(status: number, t: ReturnType<typeof useTranslations>, detail?: string): string {
  switch (status) {
    case 504: return t("http504");
    case 503: return t("http503");
    case 502: return t("http502");
    default:  return detail ?? t("generic", { status });
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

function CompletenessGauge({ score }: { score: number }) {
  const t = useTranslations("interview");
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
    <div className="flex flex-col items-center gap-2">
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
      <p className="text-xs text-gray-500 font-body">{t("profileCompleteness")}</p>
    </div>
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
      <p className="text-xs text-gray-600 mb-3">
        {t("conflictDetail", { field: conflict.field, oldValue: conflict.old_value, newValue: conflict.new_value })}
      </p>
      <div className="flex gap-2">
        <Button
          data-testid="conflict-keep-old"
          size="sm"
          variant="outline"
          onClick={() => void resolve("existing")}
          disabled={resolving}
          className="text-xs"
        >
          {t("keepOld", { value: conflict.old_value })}
        </Button>
        <Button
          data-testid="conflict-use-new"
          size="sm"
          variant="outline"
          onClick={() => void resolve("incoming")}
          disabled={resolving}
          className="text-xs"
        >
          {t("useNew", { value: conflict.new_value })}
        </Button>
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
  const tErrors = useTranslations("errors");
  const tCommon = useTranslations("common");
  const chatEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const [flowState, setFlowState] = useState<FlowState | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [estimatedQuestions, setEstimatedQuestions] = useState(5);
  const [questionsAsked, setQuestionsAsked] = useState(1);
  const [gapsTotal, setGapsTotal] = useState(0);
  const [gapsRemaining, setGapsRemaining] = useState(0);
  const [resumed, setResumed] = useState(false);
  const [showResumeBanner, setShowResumeBanner] = useState(false);
  const [isCategoryB, setIsCategoryB] = useState(false);

  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const [completion, setCompletion] = useState<CompletionData | null>(null);
  const [advancingToCV, setAdvancingToCV] = useState(false);

  const [pendingConflicts, setPendingConflicts] = useState<ConflictSummary[]>([]);
  const [showDoneConfirm, setShowDoneConfirm] = useState(false);

  // Split-screen state
  const [gapAnalysis, setGapAnalysis] = useState<GapAnalysisData | null>(null);
  const [resolvedClusterIds, setResolvedClusterIds] = useState<Set<string>>(new Set());
  const [currentClusterId, setCurrentClusterId] = useState<string | null>(null);
  const [choices, setChoices] = useState<string[] | null>(null);
  const [matchScore, setMatchScore] = useState<number | null>(null);

  useEffect(() => {
    async function init() {
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
        if (!sessionRes.ok) throw new Error(await apiErrorMessage(sessionRes));
        const sessionData: SessionCreateResponse = await sessionRes.json();

        setSessionId(sessionData.session_id);
        setEstimatedQuestions(sessionData.estimated_questions || 5);
        setGapsTotal(sessionData.gaps_total ?? 0);
        setGapsRemaining(sessionData.gaps_remaining ?? 0);
        setMessages([{ role: "assistant", content: sessionData.question ?? sessionData.first_question }]);
        setChoices(sessionData.choices ?? null);

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
        // 409 = already at interview step — expected on resume
        if (!advRes.ok && advRes.status !== 409) {
          console.warn("advance_flow:", await advRes.text());
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : tErrors("failedToStart"));
      } finally {
        setLoading(false);
      }
    }
    void init();
  }, [flowId]);

  // Set first cluster as current once gap analysis is loaded
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
    setShowDoneConfirm(false);
    setSending(true);
    setPendingConflicts([]);
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);

    try {
      const res = await fetch(`${API_BASE}/api/session/${sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userMsg }),
      });
      if (!res.ok) {
        const msg = await apiErrorMessage(res);
        throw new Error(translateError(res.status, tErrors, msg));
      }
      const data: MessageResponse = await res.json();

      if (data.complete) {
        setCompletion({
          reason: data.reason ?? "gaps_resolved",
          questions_asked: data.questions_asked ?? questionsAsked,
          gaps_resolved: data.gaps_resolved ?? 0,
          completeness_score: data.completeness_score ?? 0,
        });
      } else {
        const nextQ = data.question ?? "";
        setMessages((prev) => [...prev, { role: "assistant", content: nextQ }]);
        setGapsRemaining(data.gaps_remaining ?? 0);
        setQuestionsAsked((q) => q + 1);

        // Detect cultural sensitivity hint (category B) from question tone
        const lowerQ = nextQ.toLowerCase();
        setIsCategoryB(lowerQ.includes("confirm") || lowerQ.includes("likely") || lowerQ.includes("based on your background"));

        if (data.pending_conflicts?.length) {
          setPendingConflicts(data.pending_conflicts);
        }

        // Update choices
        setChoices(data.choices ?? null);

        // Advance cluster tracker
        if (gapAnalysis && data.gaps_remaining !== undefined) {
          const totalClusters = gapAnalysis.gap_clusters.length;
          const resolvedCount = totalClusters - (data.gaps_remaining ?? 0);
          const nextCluster = gapAnalysis.gap_clusters[resolvedCount];
          if (currentClusterId) {
            setResolvedClusterIds((prev) => new Set([...prev, currentClusterId]));
          }
          if (nextCluster) {
            setCurrentClusterId(nextCluster.id);
          }
        }

        if (data.completeness_score !== undefined) {
          setMatchScore(data.completeness_score);
        }
      }
    } catch (e: unknown) {
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

  const progressPct = Math.min(
    Math.round((questionsAsked / Math.max(estimatedQuestions, 1)) * 100),
    100,
  );

  const roleTitle = flowState?.job_summary?.role_title ?? "the target role";

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
  // Completion screen (19.2)
  // -------------------------------------------------------------------------

  if (completion) {
    return (
      <div data-testid="completion-screen" className="max-w-2xl mx-auto animate-fade-in">
        <Card className="p-8 text-center">
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
          <div className="flex justify-center mb-8">
            <CompletenessGauge score={completion.completeness_score} />
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
            <a
              href="#"
              className="inline-flex items-center justify-center text-sm text-teal underline hover:no-underline px-4 py-2"
              onClick={(e) => e.preventDefault()}
            >
              {t("viewProfile")}
            </a>
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
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
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

          {/* Header */}
          <div className="mb-4">
            <div className="flex items-center justify-between mb-1">
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              <p className="text-xs font-medium text-gray-500 font-body">{t("questionOf", { current: questionsAsked, total: estimatedQuestions })} — {t("closingGapsFor")} <span className="text-primary font-semibold">{roleTitle}</span></p>
              {gapsTotal > 0 && (
                <p className="text-xs text-gray-400 font-body">
                  {t("gapsRemaining", { count: gapsRemaining })}
                </p>
              )}
            </div>
            <ProgressLinear value={progressPct} className="h-1.5" />
          </div>

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
          </Card>

          {/* Message history (collapsed, scrollable) */}
          {messages.length > 2 && (
            <details className="mb-4">
              <summary className="cursor-pointer text-xs text-teal hover:underline mb-2">
                {t("viewHistory", { count: exchangeCount })}
              </summary>
              <div className="max-h-48 overflow-y-auto space-y-2 px-1 py-2">
                {messages.slice(0, -1).map((msg, i) => (
                  <div
                    key={i}
                    className={cn(
                      "max-w-[85%] rounded-xl px-3 py-2 text-sm",
                      msg.role === "user"
                        ? "ml-auto bg-primary text-white rounded-br-sm"
                        : "bg-gray-100 text-neutral-dark rounded-bl-sm",
                    )}
                  >
                    {msg.content}
                  </div>
                ))}
              </div>
            </details>
          )}

          {/* Error */}
          {error && (
            <div className="mb-3 px-3 py-2 rounded-lg bg-critical/10 border border-critical/20">
              <p className="text-sm text-critical">{error}</p>
            </div>
          )}

          {/* Done-signal confirmation (19.6) */}
          {showDoneConfirm && (
            <div data-testid="done-confirm" className="mb-3 px-4 py-3 rounded-lg bg-amber/10 border border-amber/30">
              <p className="text-sm font-medium text-neutral-dark mb-2">
                {t("gapsRemainingConfirm", { count: gapsRemaining })}
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

          {/* Choice cards — shown when backend provides choices and session is active */}
          {choices && choices.length > 0 && !completion && (
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
        </div>

        {/* RIGHT PANEL — 35%, hidden on mobile */}
        <aside className="hidden md:flex md:w-[35%] flex-col border-l border-gray-200 bg-white overflow-y-auto p-6">
          {/* Match score gauge */}
          {matchScore !== null && (
            <CompletenessGauge score={matchScore} />
          )}

          {/* Cluster tracker */}
          {gapAnalysis && gapAnalysis.gap_clusters.length > 0 && (
            <div className="mt-6 space-y-2">
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
                {t("roleRequirements")}
              </p>
              {gapAnalysis.gap_clusters.map((cluster) => {
                const isResolved = resolvedClusterIds.has(cluster.id);
                const isCurrent = cluster.id === currentClusterId;
                return (
                  <div
                    key={cluster.id}
                    className={cn(
                      "rounded-md px-3 py-2 text-xs border-l-2 transition-colors",
                      isResolved
                        ? "border-l-green-500 bg-green-50 text-gray-500"
                        : isCurrent
                          ? "border-l-teal bg-teal/5 text-neutral-dark font-medium"
                          : "border-l-gray-200 text-gray-400",
                    )}
                  >
                    <div className="flex items-center gap-2">
                      {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                      <span aria-hidden="true">{isResolved ? "✓" : isCurrent ? "►" : "○"}</span>
                      <span className="truncate">{cluster.label}</span>
                    </div>
                    {cluster.jd_skills.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1 ml-5">
                        {cluster.jd_skills.slice(0, 3).map((skill) => (
                          <span
                            key={skill}
                            className="rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500"
                          >
                            {skill}
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
