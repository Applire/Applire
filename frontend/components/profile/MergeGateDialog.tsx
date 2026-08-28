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

"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import type { ImportNotAppliedItem } from "@/lib/import-cv";

// Empty string default lets Next.js rewrites handle /api/* routing in all environments
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";

export type MergeGate = "name_divergence" | "not_a_cv";
export type ResolveAction = "merge" | "discard";

export interface StagedResolveResult {
  staged_id: string;
  action: ResolveAction;
  profile_id?: string | null;
  completeness_score?: number | null;
  // #615 — set only when action === "merge" ("applied, []" on a discard).
  merge_status?: "applied" | "partial";
  not_applied?: ImportNotAppliedItem[];
}

interface MergeGateDialogProps {
  gate: MergeGate;
  stagedId: string;
  /** Existing profile's name — shown on a name divergence when known. */
  accountName?: string | null;
  /** Name extracted from the uploaded document. */
  cvName?: string | null;
  onResolved: (action: ResolveAction, data: StagedResolveResult) => void;
  onCancel: () => void;
}

async function readApiError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const detail = body.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail))
      return detail.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ");
  } catch {
    // ignore parse error
  }
  return res.statusText || `HTTP ${res.status}`;
}

export function MergeGateDialog({
  gate,
  stagedId,
  accountName,
  cvName,
  onResolved,
  onCancel,
}: MergeGateDialogProps) {
  const t = useTranslations("mergeGate");
  const [busy, setBusy] = useState<ResolveAction | null>(null);
  const [error, setError] = useState("");

  async function resolve(action: ResolveAction) {
    setError("");
    setBusy(action);
    try {
      const res = await fetch(`${API_BASE}/api/profile/staged/${stagedId}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as StagedResolveResult;
      onResolved(action, data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("resolveError"));
      setBusy(null);
    }
  }

  const isDivergence = gate === "name_divergence";
  const title = isDivergence ? t("titleDivergence") : t("titleNotCv");

  let body: string;
  if (isDivergence) {
    body = accountName
      ? t("bodyDivergence", { cvName: cvName ?? "", accountName })
      : t("bodyDivergenceNoAccount", { cvName: cvName ?? "" });
  } else {
    body = t("bodyNotCv");
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      data-testid="merge-gate-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="bg-white rounded-xl p-6 shadow-xl max-w-md w-full">
        <div className="flex items-start gap-3 mb-2">
          <div className="w-10 h-10 rounded-full bg-amber-100 flex items-center justify-center flex-shrink-0">
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
            <span aria-hidden="true" className="material-symbols-outlined text-amber-600 text-[22px]" style={{ fontVariationSettings: "'FILL' 1" }}>error</span>
          </div>
          <h3 className="text-base font-bold text-on-surface pt-1.5">{title}</h3>
        </div>
        <p className="text-sm text-on-surface-variant mb-5 leading-relaxed">{body}</p>

        {error && (
          <div
            data-testid="gate-error"
            className="mb-4 flex items-center gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2"
          >
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx -- Material Symbols icon name */}
            <span aria-hidden="true" className="material-symbols-outlined text-red-500 text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }}>error</span>
            <span className="text-[13px] font-semibold text-red-700">{error}</span>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <button
            type="button"
            data-testid="gate-merge-btn"
            onClick={() => resolve("merge")}
            disabled={busy !== null}
            className="w-full bg-primary text-white font-semibold py-2.5 rounded-lg text-sm hover:opacity-90 disabled:opacity-60"
          >
            {busy === "merge" ? t("resolving") : t("mergeAnyway")}
          </button>
          <button
            type="button"
            data-testid="gate-discard-btn"
            onClick={() => resolve("discard")}
            disabled={busy !== null}
            className="w-full border border-outline-variant text-on-surface font-semibold py-2.5 rounded-lg text-sm hover:bg-surface-container-low disabled:opacity-60"
          >
            {busy === "discard" ? t("resolving") : t("discard")}
          </button>
        </div>
        <button
          type="button"
          data-testid="gate-cancel-btn"
          onClick={onCancel}
          disabled={busy !== null}
          className="mt-3 w-full text-xs text-on-surface-variant hover:text-on-surface disabled:opacity-60"
        >
          {t("cancel")}
        </button>
      </div>
    </div>
  );
}
