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
