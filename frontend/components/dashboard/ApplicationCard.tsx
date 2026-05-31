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


import { useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type WorkflowStatusKey = "statusAnalyzing" | "statusInterviewing" | "statusGeneratingCV" | "statusCVReady" | "statusTracking";
type UserStatusKey = "statusTracking" | "statusApplied" | "statusRejected" | "statusOffer";

const WORKFLOW_STATUS_CONFIG: Record<string, { labelKey: WorkflowStatusKey; className: string }> = {
  analyzing:    { labelKey: "statusAnalyzing",    className: "bg-teal text-white" },
  interviewing: { labelKey: "statusInterviewing", className: "bg-teal text-white" },
  cv_generating: { labelKey: "statusGeneratingCV", className: "bg-teal text-white" },
  completed:    { labelKey: "statusCVReady",       className: "bg-success text-white" },
  none:         { labelKey: "statusTracking",      className: "bg-gray-400 text-white" },
};

const USER_STATUS_CONFIG: Record<string, { labelKey: UserStatusKey; className: string }> = {
  tracking: { labelKey: "statusTracking",  className: "bg-gray-400 text-white" },
  applied:  { labelKey: "statusApplied",   className: "bg-blue-500 text-white" },
  rejected: { labelKey: "statusRejected",  className: "bg-critical text-white" },
  offer:    { labelKey: "statusOffer",     className: "bg-success text-white" },
};

export interface ApplicationCardProps {
  id: string;
  roleTitle: string | null;
  companyName: string | null;
  workflowStatus: string;
  userStatus: string;
  flowCurrentStep: string | null;
  updatedAt: string;
  deadline?: string | null;
  onResume?: () => void;
  onViewCV?: () => void;
  onResubmit?: () => void;
  onDelete?: () => void;
  onClick?: () => void;
}

export function ApplicationCard({
  id,
  roleTitle,
  companyName,
  workflowStatus,
  userStatus,
  flowCurrentStep,
  updatedAt,
  deadline,
  onResume,
  onViewCV,
  onResubmit,
  onDelete,
  onClick,
}: ApplicationCardProps) {
  const t = useTranslations("dashboard");
  const workflowConfig = WORKFLOW_STATUS_CONFIG[workflowStatus] || WORKFLOW_STATUS_CONFIG.none;
  const userConfig = USER_STATUS_CONFIG[userStatus] || USER_STATUS_CONFIG.tracking;

  const showResume = flowCurrentStep && flowCurrentStep !== "complete";
  const showViewCV = workflowStatus === "completed";
  const showResubmit = workflowStatus === "completed";

  const daysUntilDeadline = deadline
    ? Math.ceil((new Date(deadline).getTime() - Date.now()) / (1000 * 60 * 60 * 24))
    : null;

  return (
    <Card
      className={cn(
        "p-4 cursor-pointer transition-all duration-200 hover:shadow-card",
        "border border-gray-200 hover:border-teal/30"
      )}
      onClick={onClick}
    >
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h3 className="font-heading text-base font-semibold text-neutral-dark truncate">
            {roleTitle || t("unknownRole")}
          </h3>
          {companyName && (
            <p className="text-sm text-gray-500 truncate">{companyName}</p>
          )}
        </div>

        <div className="flex items-center gap-2 flex-wrap sm:flex-nowrap">
          <Badge className={workflowConfig.className}>
            {t(workflowConfig.labelKey)}
          </Badge>

          <Badge className={userConfig.className}>
            {t(userConfig.labelKey)}
          </Badge>

          {daysUntilDeadline !== null && daysUntilDeadline > 0 && (
            <span className="text-xs text-amber-600 whitespace-nowrap">
              {t("deadlineDays", { count: daysUntilDeadline })}
            </span>
          )}
          {daysUntilDeadline !== null && daysUntilDeadline <= 0 && (
            <span className="text-xs text-critical whitespace-nowrap">
              {t("deadlinePassed")}
            </span>
          )}

          <div className="flex items-center gap-1">
            {showResume && onResume && (
              <Button
                variant="secondary"
                size="sm"
                onClick={(e: React.MouseEvent) => {
                  e.stopPropagation();
                  onResume();
                }}
              >
                {t("resume")}
              </Button>
            )}
            {showViewCV && onViewCV && (
              <Button
                variant="outline"
                size="sm"
                onClick={(e: React.MouseEvent) => {
                  e.stopPropagation();
                  onViewCV();
                }}
              >
                {t("viewCV")}
              </Button>
            )}
            {showResubmit && onResubmit && (
              <Button
                variant="ghost"
                size="sm"
                onClick={(e: React.MouseEvent) => {
                  e.stopPropagation();
                  onResubmit();
                }}
              >
                {t("resubmit")}
              </Button>
            )}
            {onDelete && (
              <Button
                variant="ghost"
                size="sm"
                onClick={(e: React.MouseEvent) => {
                  e.stopPropagation();
                  onDelete();
                }}
                className="text-critical hover:text-critical/80"
                aria-label={t("deleteConfirm")}
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- delete emoji, not user-facing text */}
                <span aria-hidden="true">🗑</span>
              </Button>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
