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
import { usePathname, useRouter } from "next/navigation";
import { use } from "react";
import { useTranslations } from "next-intl";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { CancelApplicationButton } from "@/components/flow/CancelApplicationButton";
import { cn } from "@/lib/utils";
import { resolveFlowRedirect } from "@/lib/flow-routing";
import { flowStepHref, flowStepState } from "@/lib/flow-steps";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

const STEP_KEYS: { step: "cv_import" | "gap_analysis" | "interview" | "cv_generation"; labelKey: string }[] = [
  { step: "cv_import",     labelKey: "flow.stepProfile" },
  { step: "gap_analysis",  labelKey: "flow.stepGaps" },
  { step: "interview",     labelKey: "flow.stepInterview" },
  { step: "cv_generation", labelKey: "flow.stepCV" },
];

interface FlowState {
  flow_id: string;
  user_type: "new" | "returning";
  current_step: string;
  available_actions: Record<string, string>;
  job_summary?: { role_title: string } | null;
  profile_completeness?: number | null;
  /** Linked Application — enables the walk-away action (US222, Branch I). */
  application_id?: string | null;
  /** The flow's interview session, if one was started (M7: "done" needs it complete). */
  interview_summary?: { status: string } | null;
}


export default function FlowLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations("flow");
  const [flowState, setFlowState] = useState<FlowState | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    async function loadAndGuard() {
      try {
        const res = await fetch(`${API_BASE}/api/flow/${flowId}/state`);
        if (!res.ok) {
          router.replace("/");
          return;
        }
        const state: FlowState = await res.json();
        setFlowState(state);

        // Redirect guard: ensure URL matches backend step.  At jd_analysis
        // this bounces sub-routes back to the flow index, which advances the
        // state machine — landing on a step page directly would desync it.
        const redirect = resolveFlowRedirect(flowId, pathname, state.current_step);
        if (redirect) {
          router.replace(redirect);
          return;
        }
      } catch {
        router.replace("/");
        return;
      }
      setReady(true);
    }
    void loadAndGuard();
  }, [flowId, pathname, router]);

  const currentSegment = pathname.split("/").pop() ?? "";
  const documentRoute = currentSegment === "cv" || currentSegment === "cover-letter";

  // Ruling UX-3 (M7): a passed step is "done" by position, except the
  // interview — skipped via "Generate CV Now", it is never drawn as finished.
  const steps = STEP_KEYS.map(({ step, labelKey }) => ({
    key: step,
    labelKey,
    state: flowStepState(step, flowState, currentSegment),
    href: flowStepHref(flowId, step, flowState),
  }));

  return (
    <div className="flex flex-col flex-1 min-h-screen bg-surface-dim">
      <AppTopbar
        mode="flow"
        steps={steps}
        trailingBadge={flowState?.job_summary?.role_title}
        trailing={
          documentRoute && flowState?.application_id ? (
            <CancelApplicationButton applicationId={flowState.application_id} />
          ) : undefined
        }
      />
      {/* US222 (Branch I): the walk-away is reachable from EVERY flow step —
          discreet, never competing with the step's primary actions. On the
          document routes (#789) it sits in the top bar from md up: as a strip
          here it overlapped the refinement panel's tab row. */}
      {flowState?.application_id && (
        <div
          data-testid="flow-cancel-strip"
          className={cn(
            "w-full max-w-[960px] mx-auto px-5 pt-3 flex justify-end",
            documentRoute && "md:hidden",
          )}
        >
          <CancelApplicationButton applicationId={flowState.application_id} />
        </div>
      )}
      {/* #789 a11y — the flow pages had no <main> landmark. */}
      <main className={cn(
        "flex-1 w-full overflow-y-auto",
        documentRoute
          ? ""
          : "max-w-[960px] mx-auto px-5 py-8"
      )}>
        {ready ? children : (
          <div className="flex items-center justify-center min-h-[60vh] text-sm text-gray-500">
            {t("loading")}
          </div>
        )}
      </main>
    </div>
  );
}
