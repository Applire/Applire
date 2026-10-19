"use client";

// Copyright (C) 2026 Tobias Rosenbaum
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
 * #763 (founder ruling, FMEA walk 2026-10-10, SF-LLM.7 / SF-LLM.3) — the
 * "nothing was recorded" receipt under the candidate's answer.
 *
 * The backend decides (`witness.compute_no_write`, the same item the Health
 * hub shows) and sends the receipt's reason on the turn response
 * (`no_write_receipt`); this component only picks the copy. Three reasons,
 * three sentences — the model's own `empty_reason` chose among them (ruling
 * M5.1.4); `no_write` is both "nothing actionable" and the fail-safe, which
 * is why it is the one that says what to do next and the only one drawn as a
 * warning.
 */

import { useTranslations } from "next-intl";
import { CircleAlert, Info } from "lucide-react";
import { cn } from "@/lib/utils";

export type NoWriteReason = "no_write" | "no_write_already_known" | "no_write_question_only";

const REASON_KEY: Record<NoWriteReason, "nothingRecorded" | "alreadyKnown" | "questionOnly"> = {
  no_write: "nothingRecorded",
  no_write_already_known: "alreadyKnown",
  no_write_question_only: "questionOnly",
};

/** The catalog key under `interview.noWriteReceipt` for a reason, or null for
 * anything the backend did not send as a receipt (an unknown value never
 * renders a sentence the candidate cannot trust). */
export function noWriteReceiptKey(reason: string | null | undefined) {
  if (!reason || !(reason in REASON_KEY)) return null;
  return REASON_KEY[reason as NoWriteReason];
}

/** The receipt sentence alone — under a chat bubble or in the history. */
export function NoWriteReceiptLine({
  reason,
  className,
  live = false,
}: {
  reason: string | null | undefined;
  className?: string;
  /** Announce it (the turn's own receipt), not a history entry re-rendered. */
  live?: boolean;
}) {
  const t = useTranslations("interview.noWriteReceipt");
  const key = noWriteReceiptKey(reason);
  if (!key) return null;
  const warn = key === "nothingRecorded";
  const Icon = warn ? CircleAlert : Info;
  return (
    <p
      data-testid="no-write-receipt"
      data-reason={reason}
      role={live ? "status" : undefined}
      className={cn(
        "flex items-start gap-1.5 text-xs leading-relaxed",
        warn ? "text-on-surface" : "text-on-surface-variant",
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", warn ? "text-warning" : "text-on-surface-variant")}
      />
      <span>{t(key)}</span>
    </p>
  );
}

/** The candidate's last answer, echoed with its receipt — shown between the
 * answer and the next question, where the turn happened. */
export function NoWriteReceipt({ answer, reason }: { answer: string; reason: string | null | undefined }) {
  const t = useTranslations("interview.noWriteReceipt");
  const key = noWriteReceiptKey(reason);
  if (!key) return null;
  const warn = key === "nothingRecorded";
  return (
    <div
      data-testid="no-write-receipt-turn"
      className={cn(
        "mb-4 rounded-lg border border-outline-variant bg-white px-4 py-3",
        warn ? "border-l-4 border-l-warning" : "border-l-4 border-l-outline-variant",
      )}
    >
      <p className="text-[11px] font-medium text-on-surface-variant mb-1.5">{t("label")}</p>
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-xl rounded-br-sm bg-primary px-3 py-2 text-sm text-white">
          <p className="line-clamp-3">{answer}</p>
        </div>
      </div>
      <NoWriteReceiptLine reason={reason} live className="mt-2.5" />
    </div>
  );
}
