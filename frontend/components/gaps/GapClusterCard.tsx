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
import { cn } from "@/lib/utils";

export interface GapCluster {
  id: string;
  label: string;
  category: "B" | "C";
  gaps: string[];
  jd_skills: string[];
  jd_context: string;
}

interface GapClusterCardProps {
  cluster: GapCluster;
  resolved: boolean;
  onClick?: () => void;
  children?: React.ReactNode;
}

export function GapClusterCard({
  cluster,
  resolved,
  onClick,
  children,
}: GapClusterCardProps) {
  const t = useTranslations("gaps");

  const borderColor = resolved
    ? "border-l-green-500"
    : cluster.category === "C"
      ? "border-l-red-500"
      : "border-l-yellow-400";

  const dotColor = resolved
    ? "bg-green-500"
    : cluster.category === "C"
      ? "bg-red-500"
      : "bg-yellow-400";

  const hoverRing = resolved
    ? "hover:ring-2 hover:ring-green-500"
    : cluster.category === "C"
      ? "hover:ring-2 hover:ring-red-500"
      : "hover:ring-2 hover:ring-yellow-400";

  return (
    <div
      data-testid="gap-cluster-card"
      className={cn(
        "rounded-lg border border-gray-200 bg-white shadow-sm border-l-4 p-4 transition-all",
        borderColor,
        onClick && cn("cursor-pointer", hoverRing),
      )}
      onClick={onClick}
    >
      <div className="flex items-start gap-2 min-w-0">
        <span
          className={cn("mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full", dotColor)}
        />
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-neutral-dark text-sm">{cluster.label}</p>
          {cluster.jd_context && (
            <p className="text-xs text-gray-500 mt-0.5">{cluster.jd_context}</p>
          )}
          {cluster.gaps.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 mt-1.5">
              <span className="text-xs text-gray-400">{t("clusterCardConstituentLabel")}</span>
              {cluster.gaps.map((g) => (
                <span
                  key={g}
                  className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600"
                >
                  {g}
                </span>
              ))}
            </div>
          )}
          {resolved && (
            <p data-testid="gap-resolved" className="mt-1.5 text-xs font-medium text-green-600 flex items-center gap-1">
              {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
              <span aria-hidden="true">✓</span>
              {t("clusterStatusResolved")}
            </p>
          )}
        </div>
      </div>
      {children}
    </div>
  );
}
