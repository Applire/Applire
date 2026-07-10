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


import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { USER_STATUS_OPTIONS, isStaleStatus, staleNextStatuses } from "@/lib/user-status";
import { patchApplicationStatus } from "@/lib/api/applications";
import { SubmittedDocumentsCard } from "@/components/applications/SubmittedDocumentsCard";
import { StaleCvBanner } from "@/components/applications/StaleCvBanner";
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

interface ApplicationDetail {
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
}

export default function ApplicationDetailPage() {
  const router = useRouter();
  const params = useParams();
  const t = useTranslations("applications");
  const tDash = useTranslations("dashboard");
  const appId = params.appId as string;

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [application, setApplication] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  // Editable fields
  const [userStatus, setUserStatus] = useState("");
  const [notes, setNotes] = useState("");
  const [deadline, setDeadline] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");

  // Stale-status refresh prompt (E039/US218, JF-E-P2.1) — session-local dismiss.
  const [staleDismissed, setStaleDismissed] = useState(false);

  // Stale-CV re-tailor nudge (E039/US221, Branch H) — dismissal is PERSISTED
  // server-side (stale_cv_dismissed_at), unlike the session-local one above.
  const [retailoring, setRetailoring] = useState(false);

  useEffect(() => {
    async function loadApplication() {
      try {
        const res = await fetch(`${API_BASE}/api/applications/${appId}`);
        if (res.ok) {
          const data: ApplicationDetail = await res.json();
          setApplication(data);
          setUserStatus(data.user_status);
          setNotes(data.notes || "");
          setDeadline(data.deadline ? data.deadline.slice(0, 16) : "");
          setSourceUrl(data.source_url || "");
        } else {
          setError(t("notFound"));
        }
      } catch (err) {
        console.error("Failed to load application:", err);
        setError(t("loadFailed"));
      } finally {
        setLoading(false);
      }
    }
    loadApplication();
  }, [appId]);

  const handleSave = async () => {
    setSaving(true);
    setError("");
    setSuccess("");
    try {
      const payload: Record<string, unknown> = {};
      if (userStatus) payload.user_status = userStatus;
      if (notes !== application?.notes) payload.notes = notes || null;
      if (deadline) {
        payload.deadline = new Date(deadline).toISOString();
      } else if (application?.deadline) {
        payload.deadline = null;
      }
      if (sourceUrl.trim() !== (application?.source_url ?? "")) {
        payload.source_url = sourceUrl.trim() || null;
      }

      if (Object.keys(payload).length === 0) {
        setSuccess(t("noChanges"));
        setSaving(false);
        return;
      }

      const res = await fetch(`${API_BASE}/api/applications/${appId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (res.ok) {
        const updated: ApplicationDetail = await res.json();
        setApplication(updated);
        setSuccess(t("saveSuccess"));
      } else {
        const err = await res.json();
        setError(err.detail || t("saveFailed"));
      }
    } catch (err) {
      console.error("Save failed:", err);
      setError(t("saveFailed"));
    } finally {
      setSaving(false);
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
    setError("");
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
        setError(t("staleCvRetailorFailed"));
        return;
      }
      const gained = encodeGained(application.stale_cv.gained);
      router.push(
        `/flow/${application.flow_session_id}/cv${gained ? `?retailored=${encodeURIComponent(gained)}` : "?retailored=1"}`
      );
    } catch {
      setError(t("staleCvRetailorFailed"));
    } finally {
      setRetailoring(false);
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
        <div className="max-w-4xl mx-auto space-y-6">
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

          {/* Success/Error Messages */}
          {success && (
            <div className="p-4 rounded-lg bg-success/10 border border-success/20">
              <p className="text-sm text-success">{success}</p>
            </div>
          )}
          {error && (
            <div className="p-4 rounded-lg bg-critical/10 border border-critical/20">
              <p className="text-sm text-critical">{error}</p>
            </div>
          )}

          <div className="max-w-4xl mx-auto flex items-center gap-2 mb-4">
            <Badge className={workflowConfig.className}>{tDash(workflowConfig.labelKey)}</Badge>
            <Badge className={USER_STATUS_OPTIONS.find(s => s.value === userStatus)?.className ?? "bg-gray-400 text-white"}>
              {(() => {
                const opt = USER_STATUS_OPTIONS.find(s => s.value === userStatus);
                return opt ? tDash(opt.labelKey) : userStatus;
              })()}
            </Badge>
          </div>

          {/* Company Info */}
          <Card className="p-6">
            <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
              {t("companyAndRole")}
            </h2>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-sm font-medium text-gray-500">{t("company")}</label>
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                <p className="text-base text-neutral-dark mt-1">{application.company_name || "—"}</p>
              </div>
              <div>
                <label className="text-sm font-medium text-gray-500">{t("role")}</label>
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                <p className="text-base text-neutral-dark mt-1">{application.role_title || "—"}</p>
              </div>
            </div>
          </Card>

          {/* Submitted documents — Branch G recall (E039/US219): "what exactly
              do THEY have?". Keyed on the pin so a PATCH elsewhere on this page
              (e.g. via save) doesn't leave a stale card. */}
          <SubmittedDocumentsCard
            key={application.submitted_cv_id ?? "unpinned"}
            applicationId={application.id}
            jobAnalysisId={application.job_analysis_id}
            submittedCvId={application.submitted_cv_id}
            submittedCvCreatedAt={application.submitted_cv_created_at}
            submittedCoverLetterId={application.submitted_cover_letter_id}
          />

          {/* Status Management */}
          <Card className="p-6">
            <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
              {t("statusManagement")}
            </h2>
            <div className="space-y-4">
              {/* User Status Dropdown */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t("applicationStatus")}
                </label>
                <select
                  value={userStatus}
                  onChange={(e) => setUserStatus(e.target.value)}
                  className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20"
                >
                  {USER_STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {tDash(option.labelKey)}
                    </option>
                  ))}
                </select>
              </div>

              {/* Deadline */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t("deadline")}
                </label>
                <Input
                  type="datetime-local"
                  value={deadline}
                  onChange={(e) => setDeadline(e.target.value)}
                  className="max-w-xs"
                />
                {daysUntilDeadline !== null && (
                  <p className={cn(
                    "text-xs mt-1",
                    daysUntilDeadline > 0 ? "text-gray-500" : "text-critical"
                  )}>
                    {daysUntilDeadline > 0
                      ? t("deadlineDaysRemaining", { count: daysUntilDeadline })
                      : t("deadlineHasPassed")}
                  </p>
                )}
              </div>

              {/* Source link (E039/US216 — where the posting was found) */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t("sourceLink")}
                </label>
                <div className="flex items-center gap-2 max-w-xl">
                  <Input
                    type="url"
                    value={sourceUrl}
                    onChange={(e) => setSourceUrl(e.target.value)}
                    placeholder={t("sourceLinkPlaceholder")}
                    className="flex-1"
                  />
                  {application.source_url && (
                    <a
                      href={application.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={tDash("sourceLinkLabel")}
                      title={tDash("sourceLinkLabel")}
                      className="shrink-0 text-primary hover:text-teal-dim flex items-center"
                    >
                      <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 18 }}>
                        {SOURCE_LINK_ICON}
                      </span>
                    </a>
                  )}
                </div>
              </div>

              {/* Notes */}
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  {t("notes")}
                </label>
                <textarea
                  className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm min-h-[100px] focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={t("notesPlaceholder")}
                />
              </div>

              {/* Save Button */}
              <div className="flex justify-end">
                <Button onClick={handleSave} disabled={saving}>
                  {saving ? t("saving") : t("saveChanges")}
                </Button>
              </div>
            </div>
          </Card>

          {/* Flow Progress */}
          {application.flow_session_id && (
            <Card className="p-6">
              <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
                {t("flowProgress")}
              </h2>
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-500">{t("currentStep")}</span>
                  {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                  <span className="text-sm font-medium text-neutral-dark">{application.flow_current_step || "—"}</span>
                </div>
                <div className="flex gap-2">
                  {application.flow_current_step && application.flow_current_step !== "complete" && (
                    <Button variant="secondary" onClick={handleResume}>
                      {t("resumeFlow")}
                    </Button>
                  )}
                  {application.workflow_status === "completed" && (
                    <Button variant="outline" onClick={handleViewCV}>
                      {t("viewCV")}
                    </Button>
                  )}
                </div>
              </div>
            </Card>
          )}

          {/* Metadata */}
          <Card className="p-6">
            <h2 className="font-heading text-xl font-bold text-neutral-dark mb-4">
              {t("details")}
            </h2>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <label className="text-gray-500">{t("created")}</label>
                <p className="text-neutral-dark mt-1">
                  {new Date(application.created_at).toLocaleDateString()}
                </p>
              </div>
              <div>
                <label className="text-gray-500">{t("lastUpdated")}</label>
                <p className="text-neutral-dark mt-1">
                  {new Date(application.updated_at).toLocaleDateString()}
                </p>
              </div>
              {application.applied_at && (
                <div>
                  <label className="text-gray-500">{t("appliedAt")}</label>
                  <p className="text-neutral-dark mt-1">
                    {new Date(application.applied_at).toLocaleDateString()}
                  </p>
                </div>
              )}
            </div>
          </Card>
        </div>
      </main>

    </div>
  );
}
