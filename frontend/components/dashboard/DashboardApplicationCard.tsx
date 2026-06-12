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


import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { markApplicationHired } from "@/lib/profile-roles";

export type CardStatus = "in_progress" | "cv_ready" | "interrupted" | "tracking";

export interface DashboardApplicationCardProps {
  applicationId: string;
  roleTitle: string | null;
  companyName: string | null;
  workflowStatus: string;
  userStatus?: string;
  flowSessionId: string | null;
  updatedAt: string;
  onStartFlow?: () => void;
}

function deriveCardStatus(workflowStatus: string, updatedAt: string): CardStatus {
  if (workflowStatus === "completed") return "cv_ready";
  if (workflowStatus === "none") return "tracking";
  const hoursAgo = (Date.now() - new Date(updatedAt).getTime()) / 36e5;
  if (workflowStatus === "analyzing" || workflowStatus === "interviewing" || workflowStatus === "cv_generating") {
    return hoursAgo > 48 ? "interrupted" : "in_progress";
  }
  return "in_progress";
}

const PROGRESS: Record<CardStatus, number> = {
  in_progress: 50,
  cv_ready: 100,
  interrupted: 65,
  tracking: 0,
};

type ChipKey = "chipInProgress" | "chipCvReady" | "chipInterrupted" | "chipTracking";

const CHIP: Record<CardStatus, { labelKey: ChipKey; className: string }> = {
  in_progress:  { labelKey: "chipInProgress",  className: "bg-teal-container text-primary" },
  cv_ready:     { labelKey: "chipCvReady",     className: "bg-[#dcfce7] text-[#166534]" },
  interrupted:  { labelKey: "chipInterrupted", className: "bg-[#fef9c3] text-[#854d0e]" },
  tracking:     { labelKey: "chipTracking",    className: "bg-[#f1f5f9] text-[#64748b]" },
};

const PROGRESS_COLOR: Record<CardStatus, string> = {
  in_progress: "bg-primary",
  cv_ready:    "bg-[#22c55e]",
  interrupted: "bg-[#eab308]",
  tracking:    "bg-[#e2e5f0]",
};

export function DashboardApplicationCard({
  applicationId,
  roleTitle,
  companyName,
  workflowStatus,
  userStatus,
  flowSessionId,
  updatedAt,
  onStartFlow,
}: DashboardApplicationCardProps) {
  const router = useRouter();
  const tDash = useTranslations("dashboard");
  const status = deriveCardStatus(workflowStatus, updatedAt);
  const chip = CHIP[status];
  const initial = (companyName ?? roleTitle ?? "?")[0].toUpperCase();

  const tHired = useTranslations("profileUpdate.markHired");
  const [hiring, setHiring] = useState(false);
  const showMarkHired = workflowStatus === "completed" && userStatus !== "hired";

  const relativeTime = (() => {
    const h = Math.floor((Date.now() - new Date(updatedAt).getTime()) / 36e5);
    if (h < 1) return tDash("relativeJustNow");
    if (h < 24) return tDash("relativeHoursAgo", { h });
    return tDash("relativeDaysAgo", { d: Math.floor(h / 24) });
  })();

  function handleAction(e: React.MouseEvent) {
    e.stopPropagation();
    if (status === "tracking") {
      onStartFlow?.();
    } else if (flowSessionId) {
      // Always enter via the flow index — the layout guard redirects to the
      // backend's actual current_step; hard-coding a step desyncs the flow.
      router.push(`/flow/${flowSessionId}`);
    }
  }

  async function handleMarkHired(e: React.MouseEvent) {
    e.stopPropagation();
    setHiring(true);
    try {
      const res = await markApplicationHired(applicationId);
      router.push(res.redirect_url);
    } finally {
      setHiring(false);
    }
  }

  type ActionKey = "actionResume" | "actionOpen" | "actionContinue" | "actionStartFlow";
  const ACTION_LABEL: Record<CardStatus, ActionKey> = {
    in_progress: "actionResume",
    cv_ready:    "actionOpen",
    interrupted: "actionContinue",
    tracking:    "actionStartFlow",
  };

  // Icon names are non-user-facing Material Symbols identifiers — kept as JS vars to avoid JSX literal rule
  const actionIcon: string =
    status === "cv_ready" ? "open_in_new" : status === "tracking" ? "bolt" : "play_arrow";

  return (
    <div
      className={cn(
        "bg-white rounded-xl border-[1.5px] p-4 cursor-pointer transition-all",
        status === "interrupted" ? "border-dashed border-gray-300" : "border-gray-200",
        status === "cv_ready" ? "border-green-200 bg-green-50/30" : "",
        "hover:shadow-md hover:border-primary-container"
      )}
      onClick={() => router.push(`/applications/${applicationId}`)}
    >
      <div className="flex items-start justify-between mb-2.5">
        <div
          className={cn(
            "w-[34px] h-[34px] rounded-lg flex items-center justify-center text-[14px] font-extrabold font-manrope",
            status === "cv_ready"    ? "bg-[#e6f4ea] text-[#1e6b3a]" :
            status === "interrupted" ? "bg-[#fff3cc] text-[#584400]" :
            status === "tracking"    ? "bg-gray-100 text-gray-500" :
                                       "bg-primary-container text-primary"
          )}
        >
          {initial}
        </div>
        <span className={cn("text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wide", chip.className)}>
          {tDash(chip.labelKey)}
        </span>
      </div>

      <p className="text-[14px] font-bold text-gray-900 font-manrope leading-snug truncate">
        {roleTitle ?? tDash("unknownRole")}
      </p>
      <p className="text-[12px] text-gray-500 mt-0.5 truncate">{companyName ?? ""}</p>

      {/* Progress bar */}
      <div className="h-1 bg-gray-100 rounded-full mt-3 mb-3 overflow-hidden">
        <div
          className={cn("h-1 rounded-full", PROGRESS_COLOR[status])}
          style={{ width: `${PROGRESS[status]}%` }}
        />
      </div>

      <div className="flex items-center justify-between">
        <span className="text-[11.5px] text-gray-400">{relativeTime}</span>
        <div className="flex items-center gap-2">
          <button
            onClick={handleAction}
            data-testid={status === "cv_ready" ? "dashboard-card-open-btn" : undefined}
            className={cn(
              "text-[12px] font-bold px-3 py-1.5 rounded-lg flex items-center gap-1 transition-colors",
              status === "cv_ready"
                ? "bg-[#dcfce7] text-[#166534] hover:bg-[#bbf7d0]"
                : "bg-teal-dim text-white hover:bg-primary"
            )}
          >
            <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 14 }}>
              {actionIcon}
            </span>
            {tDash(ACTION_LABEL[status])}
          </button>
          {showMarkHired && (
            <button
              type="button"
              onClick={handleMarkHired}
              disabled={hiring}
              className="text-[12px] font-bold px-3 py-1.5 rounded-lg border border-primary text-primary hover:bg-primary-container disabled:opacity-50"
            >
              {hiring ? tHired("confirming") : tHired("button")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
