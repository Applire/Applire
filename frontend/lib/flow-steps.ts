// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
//
// This file is part of Applire (AGPL-3.0-or-later). See LICENSE.

/**
 * The flow stepper's per-step state (ruling UX-3, M7).
 *
 * A step before the flow's current step used to read "done" by position alone,
 * so "Generate CV Now" from the gaps page turned "3 Interview" green although
 * no interview happened. The interview step is done only when the flow carries
 * a completed interview session; passed without one it is "skipped" — drawn
 * like a step not reached yet, never like a finished one.
 */
import { STEP_ROUTE, activeStepSegment } from "@/lib/flow-routing";

export type FlowStepState = "active" | "done" | "pending" | "skipped";

export const FLOW_STEP_ORDER = ["cv_import", "gap_analysis", "interview", "cv_generation"] as const;

export interface FlowStepInput {
  current_step: string;
  interview_summary?: { status: string } | null;
}

function stepIndex(step: string): number {
  // `complete` lies past every step of the stepper.
  if (step === "complete") return FLOW_STEP_ORDER.length;
  return (FLOW_STEP_ORDER as readonly string[]).indexOf(step);
}

export function flowStepState(
  step: (typeof FLOW_STEP_ORDER)[number],
  flow: FlowStepInput | null,
  currentSegment: string,
): FlowStepState {
  if (STEP_ROUTE[step] === activeStepSegment(currentSegment)) return "active";
  if (!flow) return "pending";
  const passed = stepIndex(flow.current_step) > stepIndex(step);
  if (!passed) return "pending";
  if (step === "interview" && flow.interview_summary?.status !== "complete") return "skipped";
  return "done";
}

/**
 * R2-3 (ruling G-1): where a PASSED stepper pill links to, or ``undefined``
 * when it is not a link. The gap view is the single human entry back into the
 * interview (ADR-016 amended 2026-10-24: `cv_generation`/`complete` →
 * `interview`), so both "2 Abgleich" and a skipped/taken "3 Interview" lead
 * there — a DONE interview opens its own summary instead (783:195); the CV
 * pill leads to the documents once they exist. A step not yet
 * reached is never a link (the layout guard would bounce it anyway).
 */
export function flowStepHref(
  flowId: string,
  step: (typeof FLOW_STEP_ORDER)[number],
  flow: FlowStepInput | null,
): string | undefined {
  if (!flow) return undefined;
  const past = flow.current_step === "cv_generation" || flow.current_step === "complete";
  const base = `/flow/${flowId}`;
  if (step === "gap_analysis") {
    return stepIndex(flow.current_step) > stepIndex("gap_analysis") ? `${base}/gaps` : undefined;
  }
  if (step === "interview") {
    if (!past) return undefined;
    // 783:195 (lead I): a DONE interview opens its reopenable summary; a
    // skipped one leads to the gap view, the entry that can start it.
    return flow.interview_summary?.status === "complete" ? `${base}/interview` : `${base}/gaps`;
  }
  if (step === "cv_generation") return past ? `${base}/cv` : undefined;
  return undefined;
}
