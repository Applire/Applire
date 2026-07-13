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
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { ImportInProgressBanner } from "@/components/dashboard/ImportInProgressBanner";
import { QuickTailorWidget } from "@/components/dashboard/QuickTailorWidget";
import { ProfileStrengthCard } from "@/components/dashboard/ProfileStrengthCard";
import { DashboardApplicationCard } from "@/components/dashboard/DashboardApplicationCard";
import { USER_STATUS_OPTIONS, countByUserStatus, splitCancelled } from "@/lib/user-status";
import { patchApplicationStatus } from "@/lib/api/applications";
import { cn } from "@/lib/utils";
import type { StaleCVInfo } from "@/lib/stale-cv";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const MAX_CARDS = 6;

interface Application {
  id: string;
  role_title: string | null;
  company_name: string | null;
  workflow_status: string;
  user_status?: string;
  flow_session_id: string | null;
  updated_at: string;
  source_url?: string | null;
  submitted_cv_id?: string | null;
  submitted_cv_created_at?: string | null;
  stale_cv?: StaleCVInfo | null;
  /** Removal date of a cancelled application (US222, ADR-005 short clock). */
  expires_at?: string | null;
}

export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const router = useRouter();
  const [applications, setApplications] = useState<Application[]>([]);
  const [userName, setUserName] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // Pipeline filter (E039/US218) — null = all statuses.
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  // Bumped when background CV imports finish (PQ F1) — remounts the Profile
  // Strength card so it re-fetches instead of keeping the pre-import score.
  const [profileRefreshKey, setProfileRefreshKey] = useState(0);

  useEffect(() => {
    async function load() {
      try {
        const [appsRes, profileRes] = await Promise.all([
          fetch(`${API_BASE}/api/applications`),
          fetch(`${API_BASE}/api/profile`),
        ]);
        if (appsRes.ok) {
          const d = await appsRes.json();
          setApplications(d.items ?? []);
        }
        if (profileRes.ok) {
          const d = await profileRes.json();
          setUserName(d.profile?.personal_info?.name ?? null);
        }
      } catch {
        // non-fatal
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, []);

  async function handleStartFlow(appId: string) {
    try {
      const res = await fetch(`${API_BASE}/api/applications/${appId}/start`, { method: "POST" });
      if (res.ok) {
        const d = await res.json();
        if (d.flow_session_id) router.push(`/flow/${d.flow_session_id}/import`);
      }
    } catch {
      // non-fatal
    }
  }

  function handleStatusChange(appId: string, userStatus: string) {
    setApplications((apps) =>
      apps.map((a) => (a.id === appId ? { ...a, user_status: userStatus } : a))
    );
  }

  async function handleRestore(appId: string) {
    try {
      await patchApplicationStatus(appId, "tracking");
      setApplications((apps) =>
        apps.map((a) => (a.id === appId ? { ...a, user_status: "tracking" } : a))
      );
    } catch {
      // non-fatal — the row simply stays in the cancelled section
    }
  }

  const firstName = userName?.split(" ")[0] ?? null;
  // US222: cancelled applications leave the active portfolio entirely — own
  // collapsed section below, excluded from counts, chips and the grid.
  const { active: activeApplications, cancelled: cancelledApplications } =
    splitCancelled(applications);
  const inProgress = activeApplications.filter((a) => a.workflow_status !== "none").length;
  const statusCounts = countByUserStatus(activeApplications);
  const visibleApplications = statusFilter
    ? activeApplications.filter((a) => (a.user_status ?? "tracking") === statusFilter)
    : activeApplications;

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <AppTopbar mode="section" titleKey="shell.dashboard" />

      <main className="flex-1 overflow-y-auto px-8 py-7">
        {/* PQ F1: truthful dashboard — CV imports may still be running server-side
            (e.g. after a refresh interrupted onboarding). Say so instead of showing
            a half-imported profile as complete; refresh the strength card when done. */}
        <ImportInProgressBanner onAllDone={() => setProfileRefreshKey((k) => k + 1)} />

        {/* Page header */}
        <div className="mb-5">
          <h1 className="text-[22px] font-extrabold text-neutral-dark font-manrope tracking-tight">
            {firstName ? t("welcomeBackUser", { name: firstName }) : t("welcomeBack")}
          </h1>
          <p className="text-[13px] text-gray-500 mt-0.5">
            {t("activeApplicationsSubtitle", { count: inProgress })}
          </p>
        </div>

        {/* Top row: Quick Tailor + Profile Strength */}
        <div className="grid grid-cols-[1fr_260px] gap-4 mb-6">
          <QuickTailorWidget />
          <ProfileStrengthCard key={profileRefreshKey} />
        </div>

        {/* Active applications */}
        <div className="flex items-center justify-between mb-3.5">
          <h2 className="text-[15px] font-extrabold text-neutral-dark font-manrope">
            {t("activeApplications", { count: activeApplications.length })}
          </h2>
          {activeApplications.length > MAX_CARDS && (
            <button
              onClick={() => router.push("/documents")}
              className="text-[12px] font-bold text-teal hover:underline"
            >
              {t("viewAllInDocuments")}
            </button>
          )}
        </div>

        {/* Pipeline filter chips (E039/US218) — only statuses that exist get a chip */}
        {activeApplications.length > 0 && (
          <div className="flex items-center gap-1.5 mb-3.5 flex-wrap" data-testid="status-filter-chips">
            <button
              onClick={() => setStatusFilter(null)}
              className={cn(
                "text-[11px] font-bold px-2.5 py-1 rounded-full border transition-colors",
                statusFilter === null
                  ? "bg-neutral-dark text-white border-neutral-dark"
                  : "bg-white text-gray-500 border-gray-200 hover:border-gray-400"
              )}
            >
              {t("filterAll", { count: activeApplications.length })}
            </button>
            {USER_STATUS_OPTIONS.filter((o) => statusCounts[o.value]).map((option) => (
              <button
                key={option.value}
                onClick={() =>
                  setStatusFilter((f) => (f === option.value ? null : option.value))
                }
                className={cn(
                  "text-[11px] font-bold px-2.5 py-1 rounded-full border transition-colors",
                  statusFilter === option.value
                    ? cn(option.className, "border-transparent")
                    : "bg-white text-gray-500 border-gray-200 hover:border-gray-400"
                )}
              >
                {t("filterStatusChip", {
                  label: t(option.labelKey),
                  count: statusCounts[option.value],
                })}
              </button>
            ))}
          </div>
        )}

        {loading ? (
          <div className="grid grid-cols-2 gap-3.5">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-36 bg-white rounded-xl border border-gray-200 animate-pulse" />
            ))}
          </div>
        ) : activeApplications.length === 0 && cancelledApplications.length === 0 ? (
          <div className="flex items-center justify-center h-40 bg-white rounded-xl border border-dashed border-gray-300">
            <p className="text-[13px] text-gray-400">{t("noApplications")}</p>
          </div>
        ) : visibleApplications.length === 0 ? (
          <div className="flex items-center justify-center h-40 bg-white rounded-xl border border-dashed border-gray-300">
            <p className="text-[13px] text-gray-400">{t("noApplicationsForFilter")}</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3.5">
            {visibleApplications.slice(0, MAX_CARDS).map((app) => (
              <DashboardApplicationCard
                key={app.id}
                applicationId={app.id}
                roleTitle={app.role_title}
                companyName={app.company_name}
                workflowStatus={app.workflow_status}
                userStatus={app.user_status}
                flowSessionId={app.flow_session_id}
                updatedAt={app.updated_at}
                sourceUrl={app.source_url}
                submittedCvId={app.submitted_cv_id}
                submittedCvCreatedAt={app.submitted_cv_created_at}
                staleCv={app.stale_cv}
                onStartFlow={() => handleStartFlow(app.id)}
                onStatusChange={(s) => handleStatusChange(app.id, s)}
              />
            ))}
          </div>
        )}

        {/* Cancelled applications (US222, Branch I) — collapsed, out of the
            active portfolio. Each row announces its removal date (ADR-005
            short clock) and offers Restore until the purge. */}
        {cancelledApplications.length > 0 && (
          <details className="mt-6" data-testid="cancelled-section">
            <summary className="cursor-pointer text-[13px] font-bold text-gray-500 hover:text-gray-700 select-none">
              {t("cancelledSection", { count: cancelledApplications.length })}
            </summary>
            <div className="mt-3 flex flex-col gap-2">
              {cancelledApplications.map((app) => (
                <div
                  key={app.id}
                  data-testid="cancelled-row"
                  className="flex items-center justify-between bg-white rounded-xl border border-gray-200 px-4 py-3"
                >
                  <div className="min-w-0">
                    <p className="text-[13px] font-bold text-gray-700 truncate">
                      {app.role_title ?? t("unknownRole")}
                    </p>
                    <p className="text-[12px] text-gray-400 truncate">
                      {app.company_name ?? ""}
                    </p>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {app.expires_at && (
                      <span className="text-[11.5px] text-gray-400">
                        {t("cancelledRemovalDate", {
                          date: new Date(app.expires_at).toLocaleDateString(),
                        })}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => void handleRestore(app.id)}
                      className="text-[12px] font-bold px-3 py-1.5 rounded-lg border border-primary text-primary hover:bg-primary-container"
                    >
                      {t("cancelledRestore")}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </details>
        )}
      </main>
    </div>
  );
}
