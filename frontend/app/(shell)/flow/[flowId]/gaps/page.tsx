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


import { useEffect, useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { use } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScoreCircle } from "@/components/ui/score-circle";
import { StatCard } from "@/components/ui/stat-card";
import { cn } from "@/lib/utils";
import { GapClusterCard, type GapCluster } from "@/components/gaps/GapClusterCard";

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
}

interface FlowState {
  job_id: string;
  user_type: "new" | "returning";
  available_actions: Record<string, string>;
  gap_summary?: { gap_analysis_id: string } | null;
  job_summary?: { role_title: string } | null;
}

interface ProfileStats {
  positions: number;
  projects: number;
  certifications: number;
  data_points: number;
}

// Gap-Click mode state per gap
type GapStatus = "idle" | "loading" | "question" | "answering" | "resolved";

interface GapClickState {
  status: GapStatus;
  sessionId: string | null;
  question: string | null;
  choices: string[] | null;
  answer: string;
  sending: boolean;
  error: string;
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
};

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
        ×
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
// GapClickPanel — inline micro-session for a single cluster
// ---------------------------------------------------------------------------

function GapClickPanel({
  state,
  onUpdate,
  onResolved,
}: {
  state: GapClickState;
  onUpdate: (patch: Partial<GapClickState>) => void;
  onResolved: () => void;
}) {
  const t = useTranslations("gaps");
  const tc = useTranslations("common");
  const tc = useTranslations("common");

  async function sendAnswer() {
    if (!state.sessionId || !state.answer.trim() || state.sending) return;
    onUpdate({ sending: true, error: "" });
    try {
      const res = await fetch(`${API_BASE}/api/session/${state.sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: state.answer.trim() }),
      });
      if (!res.ok) throw new Error(await apiErrorMessage(res));
      onUpdate({ sending: false });
      onResolved();
    } catch (e: unknown) {
      onUpdate({
        sending: false,
        error: e instanceof Error ? e.message : "Failed to send",
      });
    }
  }

  if (state.status === "idle") {
    return null; // card click handles session start
  }

  if (state.status === "loading") {
    return (
      <div className="mt-2 flex items-center gap-2">
        <div className="animate-spin h-3 w-3 border-2 border-teal border-t-transparent rounded-full" />
        <span className="text-xs text-gray-500">{t("loadingQuestion")}</span>
      </div>
    );
  }

  if (state.status === "question" || state.status === "answering") {
    return (
      <div className="mt-3 rounded-lg border border-teal/30 bg-teal/5 p-3 space-y-2">
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
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void sendAnswer(); }
          }}
          disabled={state.sending}
          rows={2}
        />
        <div className="flex justify-end gap-2">
          <Button size="sm" variant="outline" className="text-xs py-1 h-auto"
            onClick={() => onUpdate(EMPTY_GAP_STATE)}>
            {tc("cancel")}
          </Button>
          <Button data-testid="gap-submit-button" size="sm" className="text-xs py-1 h-auto"
            disabled={!state.answer.trim() || state.sending}
            onClick={() => void sendAnswer()}>
            {state.sending ? t("savingAnswer") : t("submitAnswer")}
          </Button>
        </div>
      </div>
    );
  }

  return null; // "resolved" state — parent shows green checkmark
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
  const router = useRouter();
  const t = useTranslations("gaps");
  const tc = useTranslations("common");

  const [gaps, setGaps] = useState<GapAnalysis | null>(null);
  const [flowState, setFlowState] = useState<FlowState | null>(null);
  const [profileStats, setProfileStats] = useState<ProfileStats>({
    positions: 5, projects: 12, certifications: 3, data_points: 47,
  });
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");

  // Gap-Click state keyed by cluster ID
  const [gapStates, setGapStates] = useState<Record<string, GapClickState>>({});
  // Resolved gaps — removed from active list and shown as green
  const [resolvedGaps, setResolvedGaps] = useState<Set<string>>(new Set());
  // Animated match score (refreshed after gap resolution)
  const [matchScore, setMatchScore] = useState(0);

  useEffect(() => {
    async function load() {
      try {
        const fsRes = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
        if (!fsRes.ok) throw new Error("Flow not found");
        const fs: FlowState = await fsRes.json();
        setFlowState(fs);

        let gapData: GapAnalysis;
        if (fs.gap_summary?.gap_analysis_id) {
          const gRes = await fetch(`${API_BASE}/api/job/${fs.job_id}/gaps`);
          if (gRes.ok) {
            gapData = await gRes.json();
          } else {
            const postRes = await fetch(`${API_BASE}/api/job/${fs.job_id}/gaps`, { method: "POST" });
            if (!postRes.ok) throw new Error(await apiErrorMessage(postRes));
            gapData = await postRes.json();
          }
        } else {
          const postRes = await fetch(`${API_BASE}/api/job/${fs.job_id}/gaps`, { method: "POST" });
          if (!postRes.ok) throw new Error(await apiErrorMessage(postRes));
          gapData = await postRes.json();
        }
        setGaps(gapData);
        setMatchScore(gapData.match_score ? Math.round(gapData.match_score * 100) : 0);

        try {
          const profileRes = await fetch(`${API_BASE}/api/profile`);
          if (profileRes.ok) {
            const profileData = await profileRes.json();
            setProfileStats({
              positions: profileData.positions_count ?? 5,
              projects: profileData.projects_count ?? 12,
              certifications: profileData.certifications_count ?? 3,
              data_points: profileData.data_points_count ?? 47,
            });
          }
        } catch {
          // Keep defaults
        }
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Failed to load analysis");
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [flowId]);

  function updateGapState(clusterId: string, patch: Partial<GapClickState>) {
    setGapStates((prev) => ({
      ...prev,
      [clusterId]: { ...(prev[clusterId] ?? EMPTY_GAP_STATE), ...patch },
    }));
  }

  async function handleGapResolved(gap: string) {
    setResolvedGaps((prev) => new Set([...prev, gap]));
    setGapStates((prev) => ({ ...prev, [gap]: { ...EMPTY_GAP_STATE, status: "resolved" } }));

    // Refresh match score (19.11)
    if (flowState?.job_id) {
      try {
        const refreshRes = await fetch(`${API_BASE}/api/job/${flowState.job_id}/gaps/refresh`, {
          method: "POST",
        });
        if (refreshRes.ok) {
          const refreshed: GapAnalysis = await refreshRes.json();
          const newScore = refreshed.match_score ? Math.round(refreshed.match_score * 100) : matchScore;
          setMatchScore(newScore);
        }
      } catch {
        // Non-critical — keep existing score
      }
    }
  }

  async function startMicroSession(clusterId: string, jobId: string) {
    updateGapState(clusterId, { status: "loading", error: "" });
    try {
      const res = await fetch(`${API_BASE}/api/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, mode: "targeted", target_gap: clusterId }),
      });
      if (!res.ok) throw new Error(await apiErrorMessage(res));
      const data = await res.json();
      updateGapState(clusterId, {
        status: "question",
        sessionId: data.session_id,
        question: data.question ?? data.first_question,
        choices: data.choices ?? null,
      });
    } catch (e: unknown) {
      updateGapState(clusterId, {
        status: "idle",
        error: e instanceof Error ? e.message : "Failed to start",
      });
    }
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
    if (!flowState) return;
    setError("");
    setLoading(true);
    try {
      const postRes = await fetch(`${API_BASE}/api/job/${flowState.job_id}/gaps`, { method: "POST" });
      if (!postRes.ok) throw new Error(await apiErrorMessage(postRes));
      const data: GapAnalysis = await postRes.json();
      setGaps(data);
      setMatchScore(data.match_score ? Math.round(data.match_score * 100) : 0);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load analysis");
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
  const activeGapC = (gaps?.category_c ?? []).filter((g) => !resolvedGaps.has(g));
  const activeGapB = (gaps?.category_b ?? []).filter((g) => !resolvedGaps.has(g));
  const totalGaps = activeGapC.length + activeGapB.length;

  return (
    <div data-testid="gap-analysis-page" className="max-w-4xl mx-auto">
      <JdRecoveryBanner />
      {/* Section 1: Master Profile Summary */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-success text-white">
            <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="font-heading text-xl font-bold text-neutral-dark">{t("masterProfileCreated")}</h2>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard value={profileStats.positions} label={t("statPositions")} />
          <StatCard value={profileStats.projects} label={t("statProjects")} />
          <StatCard value={profileStats.certifications} label={t("statCertifications")} />
          <StatCard value={profileStats.data_points} label={t("statDataPoints")} />
        </div>
      </div>

      {/* Section 2: Match Score */}
      <Card className="p-6 mb-8">
        <div className="flex flex-col lg:flex-row items-center gap-6">
          <ScoreCircle score={matchScore} size={100} />
          <div className="flex-1 text-center lg:text-left">
            <h3 className="font-heading text-lg font-bold text-neutral-dark mb-2">{roleTitle}</h3>
            <p data-testid="match-score-display" className="text-sm text-gray-500 mb-4">
              {t("matchScore")}: {matchScore}%
            </p>
            <div className="flex flex-wrap gap-2 justify-center lg:justify-start">
              {gaps?.category_a && gaps.category_a.length > 0 && (
                <Badge variant="success">{t("directMatchesBadge", { count: gaps.category_a.length })}</Badge>
              )}
              {activeGapB.length > 0 && (
                <Badge variant="warning">{t("likelyMatchesBadge", { count: activeGapB.length })}</Badge>
              )}
              {activeGapC.length > 0 && (
                <Badge variant="critical">{t("gapsToAddress", { count: activeGapC.length })}</Badge>
              )}
              {resolvedGaps.size > 0 && (
                <Badge variant="success">{t("resolvedBadge", { count: resolvedGaps.size })}</Badge>
              )}
            </div>
          </div>
        </div>
      </Card>

      {/* Section 3: Cluster-based gap display */}
      {totalGaps > 0 && (
        <div data-testid="gaps-section" className="mb-8">
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-heading text-lg font-bold text-neutral-dark">
              {t("gapsIdentified", { count: totalGaps })}
            </h3>
            {resolvedGaps.size === 0 && (
              <p className="text-xs text-gray-400">{t("clickGapHint")}</p>
            )}
          </div>

          {/* Cluster-based gap display */}
          {gaps?.gap_clusters && gaps.gap_clusters.length > 0 ? (
            <div className="space-y-3">
              <p className="text-xs text-gray-500 mb-3">
                {t("clustersToAddress", {
                  count: gaps.gap_clusters.filter(
                    (c) => gapStates[c.id]?.status !== "resolved"
                  ).length,
                })}
              </p>
              {[...gaps.gap_clusters]
                .sort((a, b) => {
                  if (a.category === "C" && b.category !== "C") return -1;
                  if (a.category !== "C" && b.category === "C") return 1;
                  return 0;
                })
                .map((cluster) => {
                  const clusterState = gapStates[cluster.id] ?? EMPTY_GAP_STATE;
                  const isResolved = clusterState.status === "resolved";
                  return (
                    <GapClusterCard
                      key={cluster.id}
                      cluster={cluster}
                      resolved={isResolved}
                      onClick={
                        !isResolved && clusterState.status === "idle"
                          ? () => void startMicroSession(cluster.id, flowState?.job_id ?? "")
                          : undefined
                      }
                    >
                      {!isResolved && (
                        <div className="mt-3" onClick={(e) => e.stopPropagation()}>
                          <GapClickPanel
                            state={clusterState}
                            onUpdate={(patch) => updateGapState(cluster.id, patch)}
                            onResolved={() => void handleGapResolved(cluster.id)}
                          />
                        </div>
                      )}
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

      {/* Detailed breakdown */}
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

      {/* CTAs */}
      <div className="flex flex-col sm:flex-row gap-4 items-center justify-center">
        {flowState?.user_type === "new" && totalGaps > 0 && (
          <div className="flex flex-col items-center">
            <Button
              size="lg"
              onClick={() => void advance("interview")}
              disabled={actionLoading}
              className="min-w-[240px]"
              data-testid="interview-button"
            >
              {t("startInterview")}
            </Button>
            <p className="text-xs italic text-gray-500 mt-2">
              {t("gapInterviewHint")}
            </p>
          </div>
        )}
        <Button
          variant={flowState?.user_type === "returning" || totalGaps === 0 ? "primary" : "secondary"}
          size="lg"
          onClick={() => void advance("cv_generation")}
          disabled={actionLoading}
          className="min-w-[200px]"
          data-testid="generate-cv-button"
        >
          {t("generateCV")}
        </Button>
        <a
          href="/profile"
          className="text-sm text-teal underline hover:no-underline"
          onClick={(e) => { e.preventDefault(); router.push("/profile"); }}
        >
          {t("exploreProfile")}
        </a>
      </div>
    </div>
  );
}
