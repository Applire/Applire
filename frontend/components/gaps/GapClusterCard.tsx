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
import type {
  CardTone,
  ClusterView,
  CoveragePill,
  GapCluster,
  MemberState,
} from "@/lib/match-utils";

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

/** Edge, dot, hover ring and pill colour — the card's tone is its WORST
 * member (ruling C-1). Material-3 theme tokens only. */
const TONE: Record<CardTone, { border: string; dot: string; ring: string; pill: string; icon: string }> = {
  red: {
    border: "border-l-critical",
    dot: "bg-critical",
    ring: "hover:ring-critical",
    pill: "bg-critical-container text-on-surface",
    icon: "text-critical",
  },
  yellow: {
    border: "border-l-warning",
    dot: "bg-warning",
    ring: "hover:ring-warning",
    pill: "bg-warning-container text-on-surface",
    icon: "text-warning",
  },
  green: {
    border: "border-l-success",
    dot: "bg-success",
    ring: "hover:ring-success",
    pill: "bg-success-container text-on-surface",
    icon: "text-success",
  },
  grey: {
    border: "border-l-outline-variant",
    dot: "bg-outline-variant",
    ring: "hover:ring-outline-variant",
    pill: "bg-surface-container text-on-surface-variant",
    icon: "text-on-surface-variant",
  },
};

/** A requirement chip: green = covered, yellow = partial, red = open gap,
 * grey = declined (ruling C-1); `closed` = not re-read yet, neutral. */
const CHIP: Record<MemberState, string> = {
  covered: "bg-success-container text-on-surface",
  partial: "bg-warning-container text-on-surface",
  gap: "bg-critical-container text-on-surface",
  declined: "bg-surface-container text-on-surface-variant",
  closed: "bg-surface-container text-on-surface-variant opacity-70",
};

const PILL_TESTID: Record<CoveragePill, string> = {
  covered: "gap-resolved",
  partly: "gap-partly-covered",
  likely: "gap-likely-match",
  declined: "gap-declined",
};

function MemberChip({ term, state }: { term: string; state: MemberState }) {
  const t = useTranslations("gaps");
  const sr =
    state === "covered"
      ? t("memberCoveredSr")
      : state === "partial"
        ? t("memberPartialSr")
        : state === "gap"
          ? t("memberGapSr")
          : state === "declined"
            ? t("memberDeclinedSr")
            : null;
  return (
    <span
      data-testid="gap-member"
      data-state={state}
      className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs", CHIP[state])}
    >
      {state === "covered" && <Check aria-hidden="true" className="h-3 w-3 text-success" />}
      {state === "declined" && <Minus aria-hidden="true" className="h-3 w-3" />}
      {term}
      {sr && <span className="sr-only">{sr}</span>}
    </span>
  );
}

function CoverageBadge({ pill, tone }: { pill: CoveragePill; tone: CardTone }) {
  const t = useTranslations("gaps");
  const colours = TONE[tone];
  const label =
    pill === "covered"
      ? t("coverageCovered")
      : pill === "partly"
        ? t("coveragePartly")
        : pill === "likely"
          ? t("coverageLikely")
          : t("coverageDeclined");
  return (
    <span
      data-testid={PILL_TESTID[pill]}
      data-tone={tone}
      className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", colours.pill)}
    >
      {pill === "covered" && <Check aria-hidden="true" className={cn("h-3 w-3", colours.icon)} />}
      {pill === "declined" && <Minus aria-hidden="true" className={cn("h-3 w-3", colours.icon)} />}
      {(pill === "partly" || pill === "likely") && (
        <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", colours.dot)} />
      )}
      {label}
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
  const colours = TONE[view.tone];

  return (
    <div
      data-testid="gap-cluster-card"
      data-cluster-id={cluster.id}
      data-coverage={view.coverage}
      data-tone={view.tone}
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
            {view.pill && <CoverageBadge pill={view.pill} tone={view.tone} />}
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
