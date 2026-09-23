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
import { Check, Lock, Minus } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ClusterView, GapCluster, MemberState } from "@/lib/match-utils";

// The persisted cluster shape lives with the helpers that read it; re-exported
// here for the existing importers (LiabilityPanel, the page).
export type { GapCluster } from "@/lib/match-utils";

interface GapClusterCardProps {
  cluster: GapCluster;
  /** ADR-089 clause 8 — everything the card shows about coverage, budget and
   * members comes from the server record (`clusterView`), never from what the
   * page remembers about clicks. */
  view: ClusterView;
  /** Undefined = not clickable (finished, budget spent, a session open here or
   * on another card). */
  onClick?: () => void;
  /** Another card (or the liability panel) holds the open micro-session. */
  locked?: boolean;
  children?: React.ReactNode;
}

/** Left edge + dot: the coverage colour; an OPEN cluster keeps its B/C
 * severity colour. Material-3 theme tokens only. */
function tone(view: ClusterView, category: GapCluster["category"]) {
  switch (view.coverage) {
    case "covered":
      return { border: "border-l-success", dot: "bg-success", ring: "hover:ring-success" };
    case "declined":
      return { border: "border-l-outline-variant", dot: "bg-outline-variant", ring: "hover:ring-outline-variant" };
    case "partly_covered":
      return { border: "border-l-warning", dot: "bg-warning", ring: "hover:ring-warning" };
    default:
      return category === "C"
        ? { border: "border-l-critical", dot: "bg-critical", ring: "hover:ring-critical" }
        : { border: "border-l-warning", dot: "bg-warning", ring: "hover:ring-warning" };
  }
}

function MemberChip({ term, state }: { term: string; state: MemberState }) {
  const t = useTranslations("gaps");
  return (
    <span
      data-testid="gap-member"
      data-state={state}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
        state === "covered" && "bg-success-container text-on-surface",
        state === "declined" && "bg-surface-container text-on-surface-variant",
        state === "open" && "bg-surface-container text-on-surface-variant",
        state === "closed" && "bg-surface-container text-on-surface-variant opacity-70",
      )}
    >
      {state === "covered" && <Check aria-hidden="true" className="h-3 w-3 text-success" />}
      {state === "declined" && <Minus aria-hidden="true" className="h-3 w-3" />}
      {term}
      {state === "covered" && <span className="sr-only">{t("memberCoveredSr")}</span>}
      {state === "declined" && <span className="sr-only">{t("memberDeclinedSr")}</span>}
    </span>
  );
}

export function GapClusterCard({
  cluster,
  view,
  onClick,
  locked = false,
  children,
}: GapClusterCardProps) {
  const t = useTranslations("gaps");
  const colours = tone(view, cluster.category);

  return (
    <div
      data-testid="gap-cluster-card"
      data-cluster-id={cluster.id}
      data-coverage={view.coverage}
      data-askable={view.askable ? "true" : "false"}
      className={cn(
        "rounded-lg border border-gray-200 bg-white shadow-sm border-l-4 p-4 transition-all",
        colours.border,
        onClick && cn("cursor-pointer hover:ring-2", colours.ring),
        locked && "opacity-60",
      )}
      onClick={onClick}
    >
      <div className="flex items-start gap-2 min-w-0">
        <span className={cn("mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full", colours.dot)} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <p className="font-semibold text-on-surface text-sm">{cluster.label}</p>
            {view.coverage === "covered" && (
              <span
                data-testid="gap-resolved"
                className="inline-flex items-center gap-1 rounded-full bg-success-container px-2 py-0.5 text-xs font-medium text-on-surface"
              >
                <Check aria-hidden="true" className="h-3 w-3 text-success" />
                {t("coverageCovered")}
              </span>
            )}
            {view.coverage === "partly_covered" && (
              <span
                data-testid="gap-partly-covered"
                className="inline-flex items-center gap-1 rounded-full bg-warning-container px-2 py-0.5 text-xs font-medium text-on-surface"
              >
                <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-warning" />
                {t("coveragePartly")}
              </span>
            )}
            {view.coverage === "declined" && (
              <span
                data-testid="gap-declined"
                className="inline-flex items-center gap-1 rounded-full bg-surface-container px-2 py-0.5 text-xs font-medium text-on-surface-variant"
              >
                <Minus aria-hidden="true" className="h-3 w-3" />
                {t("coverageDeclined")}
              </span>
            )}
          </div>
          {cluster.jd_context && (
            <p className="text-xs text-on-surface-variant mt-0.5">{cluster.jd_context}</p>
          )}
          {view.members.length > 0 && (
            <div className="flex flex-wrap items-center gap-1 mt-1.5">
              <span className="text-xs text-on-surface-variant">{t("clusterCardConstituentLabel")}</span>
              {view.members.map((m) => (
                <MemberChip key={m.term} term={m.term} state={m.state} />
              ))}
            </div>
          )}
          {view.budgetSpent && (
            <p
              data-testid="gap-budget-spent"
              className="mt-1.5 flex items-start gap-1 text-xs text-on-surface-variant"
            >
              <Lock aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
              {t("coverageBudgetSpent", { count: view.asked })}
            </p>
          )}
        </div>
      </div>
      {children}
    </div>
  );
}
