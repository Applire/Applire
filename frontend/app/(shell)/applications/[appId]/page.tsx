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

// E041/US231 — application cockpit. This page is the zone composition:
// banners (US218/US221/US222, byte-identical) → header (identity, chips,
// actions, collapsible JD summary) → documents zone (Task 3.1) → journey
// zone (Task 3.2, gated on an active flow session) → tracking sidebar
// (Task 3.3, US234, closes #164). The former stacked CRUD cards (Company &
// Role, Status Management, Details, Flow Progress, bottom Save) are gone —
// deadline/notes/source are editable again via the sidebar's per-field
// autosave, not a page-level save button.

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { USER_STATUS_OPTIONS, isStaleStatus, staleNextStatuses } from "@/lib/user-status";
import { patchApplicationStatus, type ApplicationPatchResponse } from "@/lib/api/applications";
import { UserStatusChipSelect } from "@/components/applications/UserStatusChipSelect";
import { StaleCvBanner } from "@/components/applications/StaleCvBanner";
import { DossierDocumentsZone } from "@/components/applications/DossierDocumentsZone";
import { DossierJourneyZone } from "@/components/applications/DossierJourneyZone";
import { DossierTrackingSidebar } from "@/components/applications/DossierTrackingSidebar";
import { encodeGained, type StaleCVInfo } from "@/lib/stale-cv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

type WorkflowStatusLabelKey = "statusAnalyzing" | "statusInterviewing" | "statusGeneratingCV" | "statusCVReady" | "statusTracking";

const WORKFLOW_STATUS_CONFIG: Record<string, { labelKey: WorkflowStatusLabelKey; className: string }> = {
  analyzing:    { labelKey: "statusAnalyzing",    className: "bg-teal text-white" },
  interviewing: { labelKey: "statusInterviewing", className: "bg-teal text-white" },
  cv_generating: { labelKey: "statusGeneratingCV", className: "bg-teal text-white" },
  completed:    { labelKey: "statusCVReady",       className: "bg-success text-white" },
  none:         { labelKey: "statusTracking",      className: "bg-gray-400 text-white" },
};

// Non-user-facing Material Symbols identifier — JS const to avoid the JSX literal rule
const SOURCE_LINK_ICON = "open_in_new";

export interface ApplicationDetail {
  id: string;
  job_analysis_id: string;
  role_title: string | null;
  company_name: string | null;
  workflow_status: string;
  user_status: string;
  notes: string | null;
  applied_at: string | null;
  deadline: string | null;
  source_url: string | null;
  submitted_cv_id: string | null;
  submitted_cv_created_at: string | null;
  submitted_cover_letter_id: string | null;
  stale_cv?: StaleCVInfo | null;
  flow_session_id: string | null;
  flow_current_step: string | null;
  created_at: string;
  updated_at: string;
  /** Removal date — the announced purge moment while cancelled (US222). */
  expires_at?: string | null;
}

/**
 * Shape of GET /api/cover-letter/by-job/{job_id} (CoverLetterStatusResponse).
 * The page owns the ONE fetch; Task 3.1's documents zone consumes this via a
 * `coverLetter` prop of exactly this type.
 */
export interface CoverLetterSummary {
  cover_letter_id: string;
  status: string;
  html_url?: string | null;
  pdf_url?: string | null;
  error_message?: string | null;
  expires_at: string;
  letter_data?: Record<string, unknown> | null;
}

/** Subset of GET /api/job/{job_id} (JobAnalysisResponse) rendered in the header JD summary. */
interface JobAnalysisSummary {
  role_title: string;
  seniority_level: string;
  required_skills: string[];
  nice_to_have_skills: string[];
  keywords: string[];
  language_requirement: string;
  company_name?: string | null;
}

export default function ApplicationDetailPage() {
  const router = useRouter();
  const params = useParams();
  const t = useTranslations("applications");
  const tDash = useTranslations("dashboard");
  const appId = params.appId as string;

  const [loading, setLoading] = useState(true);
  const [application, setApplication] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState("");
  // Inline header-action feedback (re-tailor / start). Replaces the page-top
  // success/error banners — each zone owns its own inline feedback now.
  const [actionError, setActionError] = useState("");

  // Optimistic pipeline status for the header chip (revert on PATCH failure).
  const [userStatus, setUserStatus] = useState("");

  // Cover letter for this job (ONE fetch, lifted to the page). null = none / 404.
  const [coverLetter, setCoverLetter] = useState<CoverLetterSummary | null>(null);
  // Collapsible JD summary source. null until fetched.
  const [jobAnalysis, setJobAnalysis] = useState<JobAnalysisSummary | null>(null);
  // Newest ready CV's template — the header Re-tailor payload (1.1 exposes it).
  const [newestReadyTemplate, setNewestReadyTemplate] = useState<string | null>(null);

  // Stale-status refresh prompt (E039/US218, JF-E-P2.1) — session-local dismiss.
  const [staleDismissed, setStaleDismissed] = useState(false);

  // Stale-CV re-tailor nudge (E039/US221, Branch H) — dismissal is PERSISTED
  // server-side (stale_cv_dismissed_at), unlike the session-local one above.
  const [retailoring, setRetailoring] = useState(false);
  // Header Re-tailor (US231) — distinct in-flight flag from the banner nudge.
  const [headerRetailoring, setHeaderRetailoring] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadApplication() {
      try {
        const res = await fetch(`${API_BASE}/api/applications/${appId}`);
        if (!res.ok) {
          if (!cancelled) setError(t("notFound"));
          return;
        }
        const data: ApplicationDetail = await res.json();
        if (cancelled) return;
        setApplication(data);
        setUserStatus(data.user_status);

        // Cover letter — the page owns ONE by-job lookup. 404 = none exists.
        void fetch(`${API_BASE}/api/cover-letter/by-job/${data.job_analysis_id}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((cl: CoverLetterSummary | null) => {
            if (!cancelled) setCoverLetter(cl);
          })
          .catch(() => {});

        // Job analysis for the collapsible JD summary (read-only, no LLM).
        void fetch(`${API_BASE}/api/job/${data.job_analysis_id}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((job: JobAnalysisSummary | null) => {
            if (!cancelled) setJobAnalysis(job);
          })
          .catch(() => {});

        // Newest ready CV's template — the header Re-tailor payload.
        void fetch(`${API_BASE}/api/cv?job_id=${data.job_analysis_id}`)
          .then((r) => (r.ok ? r.json() : []))
          .then((list: Array<{ status: string; template?: string | null }>) => {
            const newestReady = Array.isArray(list)
              ? list.find((cv) => cv.status === "ready")
              : undefined;
            if (!cancelled) setNewestReadyTemplate(newestReady?.template ?? null);
          })
          .catch(() => {});
      } catch (err) {
        console.error("Failed to load application:", err);
        if (!cancelled) setError(t("loadFailed"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void loadApplication();
    return () => {
      cancelled = true;
    };
  }, [appId]);

  // Header chip — optimistic PATCH; success re-syncs the banners (they key on
  // application.user_status / updated_at), failure reverts the chip.
  const handleStatusChange = async (next: string) => {
    const previous = userStatus;
    setUserStatus(next);
    try {
      const updated = await patchApplicationStatus(appId, next);
      setApplication((app) =>
        app ? { ...app, user_status: next, updated_at: updated.updated_at } : app
      );
    } catch {
      setUserStatus(previous);
    }
  };

  // Quick status set from the stale-status banner. "Still current" re-PATCHes
  // the unchanged status — that touches updated_at server-side, resetting the
  // staleness clock so the prompt doesn't nag on every visit.
  const handleQuickStatus = async (next: string) => {
    setStaleDismissed(true);
    try {
      const updated = await patchApplicationStatus(appId, next);
      setUserStatus(next);
      setApplication((app) =>
        app ? { ...app, user_status: next, updated_at: updated.updated_at } : app
      );
    } catch {
      // Nudge, not a gate — the regular status select + save still works.
    }
  };

  // One-click re-tailor (E039/US221): a NEW version through the EXISTING
  // generation pipeline (POST /api/cv/generate) with the stale version's
  // template, landing on the flow CV page which picks up the pending job.
  // The pinned submitted version is never touched. The gained delta rides
  // along as a query param so the new version can explain itself.
  const handleRetailor = async () => {
    if (!application?.stale_cv || !application.flow_session_id) return;
    setRetailoring(true);
    setActionError("");
    try {
      const res = await fetch(`${API_BASE}/api/cv/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: application.job_analysis_id,
          template: application.stale_cv.latest_cv_template,
        }),
      });
      if (!res.ok) {
        setActionError(t("staleCvRetailorFailed"));
        return;
      }
      const gained = encodeGained(application.stale_cv.gained);
      router.push(
        `/flow/${application.flow_session_id}/cv${gained ? `?retailored=${encodeURIComponent(gained)}` : "?retailored=1"}`
      );
    } catch {
      setActionError(t("staleCvRetailorFailed"));
    } finally {
      setRetailoring(false);
    }
  };

  // Header Re-tailor (US231): same pipeline as the stale-CV nudge, but the
  // template comes from the newest ready CV (1.1) and there is no stale_cv
  // precondition — a completed application can always be re-tailored.
  const handleHeaderRetailor = async () => {
    if (!application?.flow_session_id) return;
    setHeaderRetailoring(true);
    setActionError("");
    try {
      const body: Record<string, unknown> = { job_id: application.job_analysis_id };
      if (newestReadyTemplate) body.template = newestReadyTemplate;
      const res = await fetch(`${API_BASE}/api/cv/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setActionError(t("staleCvRetailorFailed"));
        return;
      }
      router.push(`/flow/${application.flow_session_id}/cv`);
    } catch {
      setActionError(t("staleCvRetailorFailed"));
    } finally {
      setHeaderRetailoring(false);
    }
  };

  // Apply a tracking-sidebar field save (E041/US234) directly from the PATCH
  // response — lighter than a full refetch since each save touches at most
  // one field (deadline/source_url/notes) and the response already carries
  // the new value + updated_at.
  const handleTrackingSaved = (patch: ApplicationPatchResponse) => {
    setApplication((app) =>
      app
        ? {
            ...app,
            deadline: patch.deadline,
            source_url: patch.source_url,
            notes: patch.notes,
            updated_at: patch.updated_at,
          }
        : app
    );
  };

  // Re-sync `application` after a pin/unpin PATCH in the documents zone
  // (E041/US232) — the zone holds no local pin state; it always renders off
  // this prop, so a refetch is the only way its highlight/chip updates.
  const refetchApplication = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/applications/${appId}`);
      if (res.ok) {
        const updated: ApplicationDetail = await res.json();
        setApplication(updated);
      }
    } catch {
      // Nudge, not a gate — the zone's own action already reported success/failure.
    }
  };

  const handleStaleCvDismiss = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/applications/${appId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dismiss_stale_cv: true }),
      });
      if (res.ok) {
        const updated: ApplicationDetail = await res.json();
        setApplication(updated);
      }
    } catch {
      // Nudge, not a gate — leaving the banner up is the worst case.
    }
  };

  const handleResume = () => {
    if (application?.flow_session_id) {
      router.push(`/flow/${application.flow_session_id}`);
    }
  };

  const handleViewCV = () => {
    if (application?.flow_session_id) {
      router.push(`/flow/${application.flow_session_id}/cv`);
    }
  };

  const handleCoverLetter = () => {
    if (coverLetter) {
      window.open(
        `${API_BASE}/api/cover-letter/${coverLetter.cover_letter_id}/pdf`,
        "_blank",
        "noopener,noreferrer"
      );
    }
  };

  // Start tailoring for a tracking-only application (no flow yet):
  // POST /api/applications/{id}/start, then enter via the /flow/{id} INDEX —
  // the layout guard redirects to the backend's actual current_step;
  // hard-coding a step route desyncs the flow state machine.
  const handleStartTailoring = async () => {
    setActionError("");
    try {
      const res = await fetch(`${API_BASE}/api/applications/${appId}/start`, { method: "POST" });
      if (!res.ok) {
        setActionError(t("startTailoringFailed"));
        return;
      }
      const d = await res.json();
      if (d.flow_session_id) {
        router.push(`/flow/${d.flow_session_id}`);
      } else {
        setActionError(t("startTailoringFailed"));
      }
    } catch {
      setActionError(t("startTailoringFailed"));
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col flex-1 items-center justify-center bg-surface-dim">
        <p className="text-gray-500">{t("loadingDetails")}</p>
      </div>
    );
  }

  if (error && !application) {
    return (
      <div className="flex flex-col flex-1 items-center justify-center bg-surface-dim">
        <p className="text-critical mb-4">{error}</p>
        <Button onClick={() => router.push("/dashboard")}>{t("backToDashboard")}</Button>
      </div>
    );
  }

  if (!application) return null;

  const workflowConfig = WORKFLOW_STATUS_CONFIG[application.workflow_status] ?? WORKFLOW_STATUS_CONFIG.none;
  const daysUntilDeadline = application.deadline
    ? Math.ceil((new Date(application.deadline).getTime() - Date.now()) / (1000 * 60 * 60 * 24))
    : null;

  const showStalePrompt =
    !staleDismissed && isStaleStatus(application.user_status, application.updated_at);
  const staleDays = Math.floor(
    (Date.now() - new Date(application.updated_at).getTime()) / (24 * 36e5)
  );
  const currentStatusOption = USER_STATUS_OPTIONS.find(
    (o) => o.value === application.user_status
  );

  const initial = (application.company_name ?? application.role_title ?? "?")[0].toUpperCase();
  const hasFlow = !!application.flow_session_id;
  const isCompleted = application.workflow_status === "completed";
  const isMidFlow = !!application.flow_current_step && application.flow_current_step !== "complete";

  return (
    <div className="flex flex-col flex-1 overflow-hidden bg-surface-dim">
      <AppTopbar
        mode="detail"
        backHref="/dashboard"
        backLabelKey="shell.dashboard"
        pageTitle={application.role_title || t("unknownRole")}
      />

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto px-4 py-8">
        <div className="max-w-5xl mx-auto space-y-6">
          {/* Cancelled banner (US222, Branch I): the deletion is announced,
              never silent — removal date + Restore until the purge. */}
          {application.user_status === "cancelled" && (
            <div
              className="p-4 rounded-lg bg-warning-container border border-warning/40 flex items-center justify-between gap-4"
              data-testid="cancelled-banner"
            >
              <p className="text-sm text-on-surface">
                {application.expires_at
                  ? t("cancelledBanner", {
                      date: new Date(application.expires_at).toLocaleDateString(),
                    })
                  : t("cancelledBannerNoDate")}
              </p>
              <button
                type="button"
                onClick={() => void handleQuickStatus("tracking")}
                className="shrink-0 text-[12px] font-bold px-3 py-1.5 rounded-lg border border-primary text-primary hover:bg-primary-container"
              >
                {tDash("cancelledRestore")}
              </button>
            </div>
          )}

          {/* Stale-status refresh prompt (E039/US218, JF-E-P2.1) */}
          {showStalePrompt && (
            <div
              className="p-4 rounded-lg bg-warning-container border border-warning/40"
              data-testid="stale-status-prompt"
            >
              <p className="text-sm text-on-surface mb-3">
                {t("staleStatusPrompt", {
                  status: currentStatusOption ? tDash(currentStatusOption.labelKey) : application.user_status,
                  days: staleDays,
                })}
              </p>
              <div className="flex items-center gap-2 flex-wrap">
                {staleNextStatuses(application.user_status).map((next) => {
                  const option = USER_STATUS_OPTIONS.find((o) => o.value === next);
                  if (!option) return null;
                  return (
                    <button
                      key={next}
                      type="button"
                      onClick={() => void handleQuickStatus(next)}
                      className={cn(
                        "text-xs font-bold px-3 py-1.5 rounded-full",
                        option.className
                      )}
                    >
                      {tDash(option.labelKey)}
                    </button>
                  );
                })}
                <button
                  type="button"
                  onClick={() => void handleQuickStatus(application.user_status)}
                  className="text-xs font-bold px-3 py-1.5 rounded-full border border-outline-variant text-on-surface-variant bg-white hover:bg-surface-container"
                >
                  {t("staleStatusStillCurrent")}
                </button>
              </div>
            </div>
          )}

          {/* Stale-CV re-tailor nudge (E039/US221, journey Branch H) */}
          {application.stale_cv && (
            <StaleCvBanner
              gained={application.stale_cv.gained}
              canRetailor={!!application.flow_session_id}
              retailoring={retailoring}
              onRetailor={() => void handleRetailor()}
              onDismiss={() => void handleStaleCvDismiss()}
            />
          )}

          {/* ── Header zone (US231): identity · chips · actions · JD summary ── */}
          <header className="rounded-2xl bg-white border border-outline-variant p-6 shadow-sm">
            <div className="flex items-start justify-between gap-4 flex-wrap">
              <div className="flex items-start gap-4 min-w-0">
                <div
                  className="w-12 h-12 shrink-0 rounded-xl bg-primary-container text-primary flex items-center justify-center text-lg font-extrabold"
                  aria-hidden="true"
                >
                  {initial}
                </div>
                <div className="min-w-0">
                  <h1
                    className="font-heading text-2xl font-bold text-neutral-dark truncate"
                    data-testid="dossier-header-title"
                  >
                    {application.role_title || t("unknownRole")}
                  </h1>
                  <div className="mt-1 flex items-center gap-2 flex-wrap text-sm text-on-surface-variant">
                    {application.company_name && (
                      <span className="truncate">{application.company_name}</span>
                    )}
                    {application.source_url && (
                      <>
                        <span className="w-1 h-1 rounded-full bg-outline-variant" aria-hidden="true" />
                        <a
                          href={application.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label={tDash("sourceLinkLabel")}
                          title={tDash("sourceLinkLabel")}
                          className="text-primary hover:text-teal-dim inline-flex items-center"
                        >
                          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 18 }}>
                            {SOURCE_LINK_ICON}
                          </span>
                        </a>
                      </>
                    )}
                    {daysUntilDeadline !== null && (
                      <>
                        <span className="w-1 h-1 rounded-full bg-outline-variant" aria-hidden="true" />
                        <span className={cn(daysUntilDeadline > 0 ? "text-on-surface-variant" : "text-critical font-medium")}>
                          {daysUntilDeadline > 0
                            ? t("deadlineDaysRemaining", { count: daysUntilDeadline })
                            : t("deadlineHasPassed")}
                        </span>
                      </>
                    )}
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <Badge className={workflowConfig.className}>{tDash(workflowConfig.labelKey)}</Badge>
                <UserStatusChipSelect value={userStatus} onChange={(next) => void handleStatusChange(next)} />
              </div>
            </div>

            {/* Actions row */}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              {isCompleted && (
                <Button onClick={handleViewCV}>{t("openCvAction")}</Button>
              )}
              {isMidFlow && (
                <Button onClick={handleResume}>{t("resumeAction")}</Button>
              )}
              {!hasFlow && (
                <Button onClick={() => void handleStartTailoring()}>{t("startTailoringAction")}</Button>
              )}
              {coverLetter && (
                <Button variant="outline" onClick={handleCoverLetter}>{t("coverLetterAction")}</Button>
              )}
              {isCompleted && hasFlow && (
                <Button
                  variant="outline"
                  onClick={() => void handleHeaderRetailor()}
                  disabled={headerRetailoring}
                >
                  {t("retailorAction")}
                </Button>
              )}
            </div>

            {actionError && (
              <p className="mt-3 text-sm text-critical" role="alert">{actionError}</p>
            )}

            {/* Collapsible JD summary — collapsed by default */}
            {jobAnalysis && (
              <details className="mt-4 border-t border-outline-variant pt-3">
                <summary className="cursor-pointer text-sm font-medium text-primary select-none">
                  {t("jdSummaryToggle")}
                </summary>
                <dl className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3 text-sm">
                  <div>
                    <dt className="text-on-surface-variant">{t("jdSeniority")}</dt>
                    <dd className="text-neutral-dark mt-0.5">{jobAnalysis.seniority_level}</dd>
                  </div>
                  <div>
                    <dt className="text-on-surface-variant">{t("jdLanguage")}</dt>
                    <dd className="text-neutral-dark mt-0.5">{jobAnalysis.language_requirement}</dd>
                  </div>
                  {jobAnalysis.required_skills.length > 0 && (
                    <div className="sm:col-span-2">
                      <dt className="text-on-surface-variant">{t("jdRequiredSkills")}</dt>
                      <dd className="text-neutral-dark mt-0.5">{jobAnalysis.required_skills.join(", ")}</dd>
                    </div>
                  )}
                  {jobAnalysis.nice_to_have_skills.length > 0 && (
                    <div className="sm:col-span-2">
                      <dt className="text-on-surface-variant">{t("jdNiceToHave")}</dt>
                      <dd className="text-neutral-dark mt-0.5">{jobAnalysis.nice_to_have_skills.join(", ")}</dd>
                    </div>
                  )}
                  {jobAnalysis.keywords.length > 0 && (
                    <div className="sm:col-span-2">
                      <dt className="text-on-surface-variant">{t("jdKeywords")}</dt>
                      <dd className="text-neutral-dark mt-0.5">{jobAnalysis.keywords.join(", ")}</dd>
                    </div>
                  )}
                </dl>
              </details>
            )}
          </header>

          {/* ── Cockpit body: documents + journey (main) · tracking (sidebar) ──
              Task 3.1 wires the documents zone (this section); 3.2/3.3 remain
              stubs. The tracking sidebar (3.3) restores deadline/notes/source edit. */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-6">
              <DossierDocumentsZone
                application={application}
                coverLetter={coverLetter}
                onError={setActionError}
                onPinChange={() => void refetchApplication()}
              />
              {application.flow_session_id && application.flow_current_step && (
                <DossierJourneyZone
                  flowSessionId={application.flow_session_id}
                  currentStep={application.flow_current_step}
                  workflowStatus={application.workflow_status}
                />
              )}
            </div>
            <aside className="lg:col-span-1">
              <DossierTrackingSidebar
                application={application}
                onSaved={handleTrackingSaved}
                onError={setActionError}
              />
            </aside>
          </div>
        </div>
      </main>
    </div>
  );
}
