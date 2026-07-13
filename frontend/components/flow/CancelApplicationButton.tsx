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

/**
 * US222 / issue #158 — "Not a fit — cancel this application" (journey Branch I).
 *
 * The flow-side walk-away: available on every flow step (flow layout) and as
 * an inline tertiary action on the gap/match screen. Confirm dialog → PATCH
 * user_status=cancelled → dashboard, where the application sits in the
 * collapsed Cancelled section with its removal date and a Restore action
 * until the ADR-005 grace window ends.
 */

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { patchApplicationStatus } from "@/lib/api/applications";

// Non-user-facing Material Symbols identifier — JS const to avoid the JSX literal rule
const CANCEL_ICON = "block";

export interface CancelApplicationButtonProps {
  applicationId: string;
  /** "chrome" = discreet topbar action (default); "inline" = tertiary link under the CTAs. */
  variant?: "chrome" | "inline";
}

export function CancelApplicationButton({
  applicationId,
  variant = "chrome",
}: CancelApplicationButtonProps) {
  const t = useTranslations("flow");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function handleConfirm() {
    setBusy(true);
    setFailed(false);
    try {
      await patchApplicationStatus(applicationId, "cancelled");
      router.push("/dashboard");
    } catch {
      setFailed(true);
      setBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          variant === "chrome"
            ? "inline-flex items-center gap-1 text-[12px] font-semibold text-gray-400 hover:text-critical transition-colors"
            : "text-sm text-gray-500 underline hover:no-underline hover:text-critical"
        )}
      >
        {variant === "chrome" && (
          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 14 }}>
            {CANCEL_ICON}
          </span>
        )}
        {variant === "chrome" ? t("cancelApplication") : t("cancelApplicationInline")}
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label={t("cancelDialogTitle")}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
        >
          <div className="bg-white rounded-xl p-6 shadow-xl max-w-md w-full">
            <h3 className="text-base font-bold text-on-surface mb-2">
              {t("cancelDialogTitle")}
            </h3>
            <p className="text-sm text-on-surface-variant mb-5 leading-relaxed">
              {t("cancelDialogBody")}
            </p>
            {failed && (
              <p className="mb-4 text-[13px] font-semibold text-critical">
                {t("cancelDialogError")}
              </p>
            )}
            <div className="flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={busy}
                className="text-[13px] font-bold px-4 py-2 rounded-lg border border-outline-variant text-on-surface hover:bg-surface-container disabled:opacity-50"
              >
                {t("cancelDialogKeep")}
              </button>
              <button
                type="button"
                onClick={() => void handleConfirm()}
                disabled={busy}
                className="text-[13px] font-bold px-4 py-2 rounded-lg bg-critical text-white hover:opacity-90 disabled:opacity-50"
              >
                {busy ? t("cancelDialogBusy") : t("cancelDialogConfirm")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
