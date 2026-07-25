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

// #260 — pre-generation keyword-liability panel: a JD hard-requirement
// concept the profile CAN claim (per the Keyword Ledger) but has no
// narrative anywhere in the vault — a bare skills-list echo a hiring panel
// discounts. Two honest exits, never a silent ship: (a) tell the story via
// the SAME resolve_gap micro-session machinery the gap-cluster cards use
// (the concept is folded into gap_clusters by services/gap.py's
// askable_gap_inputs augmentation, so it always owns a real cluster id once
// clustering has run), or (b) drop the keyword (downgrades the ledger entry
// to an honest gap, deterministic, no LLM).

import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { GapCluster } from "@/components/gaps/GapClusterCard";

export interface LiabilityEntry {
  concept: string;
  surface_forms?: string[];
  evidence?: string;
}

type ItemStatus = "idle" | "loading" | "question" | "sending" | "resolved" | "dropped";

interface ItemState {
  status: ItemStatus;
  sessionId: string | null;
  question: string | null;
  choices: string[] | null;
  answer: string;
  error: string;
}

const EMPTY_ITEM: ItemState = {
  status: "idle",
  sessionId: null,
  question: null,
  choices: null,
  answer: "",
  error: "",
};

function normConcept(s: string): string {
  return s.trim().toLowerCase();
}

/** The gap_cluster (if any) whose `gaps` list absorbed this liability concept
 * — services/gap.py's askable_gap_inputs augmentation folds every liability
 * concept into the SAME clustering input as an ordinary category-C gap, so
 * it always ends up owning a cluster id once clustering has finished. */
function findOwningCluster(concept: string, clusters: GapCluster[]): GapCluster | undefined {
  const target = normConcept(concept);
  return clusters.find((c) => c.gaps.some((g) => normConcept(g) === target));
}

async function apiErrorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return typeof body.detail === "string" ? body.detail : res.statusText || `HTTP ${res.status}`;
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export function LiabilityPanel({
  jobId,
  liabilities,
  clusters,
  apiBase,
  onDropped,
  onStoryAdded,
}: {
  jobId: string;
  liabilities: LiabilityEntry[];
  clusters: GapCluster[];
  apiBase: string;
  onDropped: (concept: string) => void;
  onStoryAdded: (concept: string) => void;
}) {
  const t = useTranslations("gaps");
  const tc = useTranslations("common");
  const [items, setItems] = useState<Record<string, ItemState>>({});

  const visible = liabilities.filter(
    (l) => (items[l.concept]?.status ?? "idle") !== "dropped",
  );
  if (visible.length === 0) return null;

  function update(concept: string, patch: Partial<ItemState>) {
    setItems((prev) => ({ ...prev, [concept]: { ...(prev[concept] ?? EMPTY_ITEM), ...patch } }));
  }

  async function tellStory(concept: string) {
    const cluster = findOwningCluster(concept, clusters);
    if (!cluster) {
      update(concept, { error: t("liabilityUnavailable") });
      return;
    }
    update(concept, { status: "loading", error: "" });
    try {
      const res = await fetch(`${apiBase}/api/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, mode: "targeted", target_gap: cluster.id }),
      });
      if (!res.ok) throw new Error(await apiErrorMessage(res));
      const data = await res.json();
      update(concept, {
        status: "question",
        sessionId: data.session_id,
        question: data.question ?? data.first_question,
        choices: data.choices ?? null,
      });
    } catch (e: unknown) {
      update(concept, { status: "idle", error: e instanceof Error ? e.message : "Failed to start" });
    }
  }

  async function submitAnswer(concept: string) {
    const item = items[concept];
    if (!item?.sessionId || !item.answer.trim()) return;
    update(concept, { status: "sending", error: "" });
    try {
      const res = await fetch(`${apiBase}/api/session/${item.sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: item.answer.trim() }),
      });
      if (!res.ok) throw new Error(await apiErrorMessage(res));
      update(concept, { status: "resolved" });
      onStoryAdded(concept);
    } catch (e: unknown) {
      update(concept, { status: "question", error: e instanceof Error ? e.message : "Failed to send" });
    }
  }

  async function dropKeyword(concept: string) {
    update(concept, { error: "" });
    try {
      const res = await fetch(`${apiBase}/api/job/${jobId}/gaps/liabilities/downgrade`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ concept }),
      });
      if (!res.ok) throw new Error(await apiErrorMessage(res));
      update(concept, { status: "dropped" });
      onDropped(concept);
    } catch {
      update(concept, { error: t("liabilityDropError") });
    }
  }

  return (
    <div data-testid="liability-panel" className="mb-8">
      <div className="flex items-center gap-2 mb-1">
        <h3 className="font-heading text-lg font-bold text-neutral-dark">{t("liabilityTitle")}</h3>
        <Badge variant="warning" data-testid="liability-badge">
          {t("liabilityBadge", { count: visible.length })}
        </Badge>
      </div>
      <p className="text-xs text-gray-500 mb-3">{t("liabilitySubtitle")}</p>

      <div className="space-y-3">
        {visible.map((l) => {
          const item = items[l.concept] ?? EMPTY_ITEM;
          const resolved = item.status === "resolved";
          return (
            <Card
              key={l.concept}
              data-testid={`liability-card-${l.concept}`}
              className={cn(
                "p-4 border-l-4",
                resolved ? "border-l-success" : "border-l-warning",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-semibold text-neutral-dark text-sm">{l.concept}</p>
                  {l.evidence && (
                    <p className="text-xs text-gray-500 mt-0.5">{l.evidence}</p>
                  )}
                </div>
                {resolved ? (
                  <p
                    data-testid={`liability-resolved-${l.concept}`}
                    className="shrink-0 text-xs font-medium text-success flex items-center gap-1"
                  >
                    {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- decorative checkmark */}
                    <span aria-hidden="true">✓</span>
                    {t("liabilityResolved")}
                  </p>
                ) : (
                  item.status !== "question" && item.status !== "loading" && item.status !== "sending" && (
                    <div className="flex shrink-0 gap-2">
                      <Button
                        size="sm"
                        variant="secondary"
                        data-testid={`liability-tell-story-${l.concept}`}
                        onClick={() => void tellStory(l.concept)}
                      >
                        {t("liabilityTellStory")}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        data-testid={`liability-drop-${l.concept}`}
                        onClick={() => void dropKeyword(l.concept)}
                      >
                        {t("liabilityDrop")}
                      </Button>
                    </div>
                  )
                )}
              </div>

              {item.error && (
                <p className="mt-2 text-xs text-critical">{item.error}</p>
              )}

              {item.status === "loading" && (
                <div className="mt-2 flex items-center gap-2">
                  <div className="animate-spin h-3 w-3 border-2 border-teal border-t-transparent rounded-full" />
                  <span className="text-xs text-gray-500">{t("loadingQuestion")}</span>
                </div>
              )}

              {(item.status === "question" || item.status === "sending") && (
                <div className="mt-3 rounded-lg border border-teal/30 bg-teal/5 p-3 space-y-2">
                  <p
                    data-testid={`liability-question-${l.concept}`}
                    className="text-sm font-medium text-neutral-dark"
                  >
                    {item.question}
                  </p>
                  {item.choices && item.choices.length > 0 && (
                    <div className="space-y-1.5">
                      <p className="text-xs text-gray-400">{t("choiceCardHint")}</p>
                      <div className="flex flex-col gap-1">
                        {item.choices.map((choice) => (
                          <button
                            key={choice}
                            type="button"
                            className={cn(
                              "w-full text-left rounded border border-teal/30 px-3 py-2 text-xs text-neutral-dark",
                              "hover:bg-teal/5 transition-colors",
                              item.answer === choice ? "bg-teal/10 border-teal/60 font-medium" : "bg-white",
                            )}
                            onClick={() => update(l.concept, { answer: choice })}
                          >
                            {choice}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <textarea
                    data-testid={`liability-answer-textarea-${l.concept}`}
                    className={cn(
                      "w-full resize-none text-xs font-body border border-gray-200 rounded px-2 py-1.5",
                      "focus:outline-none focus:ring-1 focus:ring-teal/50 focus:border-teal",
                      "disabled:opacity-50 min-h-[72px]",
                    )}
                    placeholder={t("answerPlaceholder")}
                    value={item.answer}
                    onChange={(e) => update(l.concept, { answer: e.target.value })}
                    disabled={item.status === "sending"}
                    rows={2}
                  />
                  <div className="flex justify-end gap-2">
                    <Button size="sm" variant="outline" className="text-xs py-1 h-auto"
                      onClick={() => update(l.concept, EMPTY_ITEM)}>
                      {tc("cancel")}
                    </Button>
                    <Button
                      size="sm"
                      className="text-xs py-1 h-auto"
                      data-testid={`liability-submit-${l.concept}`}
                      disabled={!item.answer.trim() || item.status === "sending"}
                      onClick={() => void submitAnswer(l.concept)}
                    >
                      {item.status === "sending" ? t("savingAnswer") : t("submitAnswer")}
                    </Button>
                  </div>
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
