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
 * Free-text testimony intake (#258) — the UI door onto the same reconciler
 * chain the interview and the agent-door `submit_claims`/`submit_testimony`
 * MCP tools use (ADR-058 door-parity invariant). The user pastes "anything
 * else recruiters should know" as free text; Applire reconciles it into the
 * vault with receipts and reports what happened — including an honest
 * denial_recorded / needs_confirmation / conflict outcome, never a silent
 * no-op (ADR-059).
 *
 * A single reconcile call, same order of magnitude as one interview turn —
 * a direct synchronous POST, not the async-job pattern CV import needs for
 * its multi-call segmented extraction.
 */
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

type TestimonyStatus =
  | "error"
  | "needs_confirmation"
  | "conflict"
  | "partial"
  | "applied"
  | "denial_recorded"
  | "no_change";

interface NotAppliedItem {
  span: string;
  // #370 — figure/op only, no sentence-level channel (ADR-063 amendment:
  // token-overlap against an op's field value inherits the reconciler's own
  // paraphrase/translation/id-merge judgement, a judgement wearing a fact's
  // label — see backend `witness.py`'s module docstring).
  kind: "figure" | "op";
  reason: "figure_not_in_any_op" | "op_rejected";
}

interface TestimonyResult {
  submission_id: string;
  status: TestimonyStatus;
  changes: Array<{ section: string; field: string; action: string }>;
  confirmations: unknown[];
  conflicts: unknown[];
  /** #370 — which spans of the submission did not literally land, and why. */
  not_applied?: NotAppliedItem[];
  detail?: string | null;
}

interface Props {
  /** Called once a submission finished successfully, so the parent can reload the profile. */
  onSubmitted?: () => void;
}

export function TestimonyIntake({ onSubmitted }: Props) {
  const t = useTranslations("profile");
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<TestimonyResult | null>(null);

  const statusMessage = (r: TestimonyResult): string => {
    switch (r.status) {
      case "applied":
        return t("testimony.statusApplied", { count: r.changes.length });
      case "partial":
        // #370 — `applied` no longer means "applied some of it"; a
        // submission with visibly missing content gets its own honest
        // message instead of silently reading as a full success.
        return t("testimony.statusPartial", { count: r.not_applied?.length ?? 0 });
      case "no_change":
        return t("testimony.statusNoChange");
      case "denial_recorded":
        return t("testimony.statusDenialRecorded");
      case "needs_confirmation":
        return t("testimony.statusNeedsConfirmation");
      case "conflict":
        return t("testimony.statusConflict");
      case "error":
        return t("testimony.statusError", { detail: r.detail ?? "" });
      default:
        return "";
    }
  };

  const handleSubmit = async () => {
    const trimmed = text.trim();
    if (!trimmed) {
      setError(t("testimony.emptyError"));
      return;
    }
    setSubmitting(true);
    setError("");
    setResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/profile/testimony`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: trimmed }),
      });
      if (res.ok) {
        const data: TestimonyResult = await res.json();
        setResult(data);
        if (data.status !== "error") {
          setText("");
          onSubmitted?.();
        }
      } else {
        setError(t("testimony.submitFailed"));
      }
    } catch {
      setError(t("testimony.submitFailed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="p-4">
      <h3 className="font-heading text-base font-semibold text-neutral-dark mb-1">
        {t("testimony.title")}
      </h3>
      <p className="text-sm text-gray-600 mb-3">{t("testimony.description")}</p>
      <textarea
        data-testid="testimony-textarea"
        className="w-full rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm text-neutral-dark min-h-[120px] focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20"
        placeholder={t("testimony.placeholder")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={submitting}
      />
      {error && (
        <p data-testid="testimony-error" className="text-sm text-critical mt-2">
          {error}
        </p>
      )}
      <div className="mt-3">
        <Button onClick={handleSubmit} disabled={submitting}>
          {submitting ? t("testimony.submitting") : t("testimony.submit")}
        </Button>
      </div>
      {result && (
        <p
          data-testid="testimony-result"
          data-status={result.status}
          className={`text-sm mt-3 ${result.status === "error" ? "text-critical" : "text-gray-700"}`}
        >
          {statusMessage(result)}
        </p>
      )}
      {result && result.not_applied && result.not_applied.length > 0 && (
        // #370 — the witness's spans are NOT persisted anywhere (the Health
        // hub cannot show them), so the only honest place to surface them is
        // right here, next to the status that names them.
        <ul data-testid="testimony-not-applied" className="text-sm mt-1 list-disc pl-5 text-gray-700">
          {result.not_applied.map((item, i) => (
            <li key={`${item.kind}-${i}`} data-kind={item.kind}>
              {item.kind === "op"
                ? t("testimony.notAppliedOp", { op: item.span })
                : t("testimony.notAppliedFigure", { span: item.span })}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
