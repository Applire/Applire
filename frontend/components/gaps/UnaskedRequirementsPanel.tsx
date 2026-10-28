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

// ADR-074 (#526) — the requirement nobody asked about.
//
// A JD hard requirement the vault holds nothing on, under any name, and that the
// candidate was never asked about has NO truthful expression in a cover letter:
// asserting the term is ungrounded, staying silent breaks the writer's own
// instruction, and denying it invents a limit the candidate never stated. So the
// letter is written as though the requirement had not been named, and the fact is
// brought here instead.
//
// The wording is the decision. This panel says what is true of APPLIRE — "the
// posting asks for this, we hold nothing on it, and you were never asked" — and
// never what might be true of the candidate. "You lack X" is a claim about a
// person that no evidence in this system supports; it is exactly the inversion of
// the truthfulness promise the product exists for. Anyone editing these strings
// should read that sentence again before changing them.

import { useState } from "react";
import { HelpCircle, Loader2 } from "lucide-react";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export interface UnaskedRequirement {
  concept: string;
  surface_forms?: string[];
}

interface Turn {
  sessionId: string;
  question: string;
  choices: string[] | null;
}

/**
 * ADR-074 (amended by ADR-099 cl. 9, UAT-3e): the requirements the job asks
 * for that nobody asked the candidate about. With `jobId` the panel carries ONE
 * action, "Jetzt beantworten": a short interview on exactly these requirements
 * (`POST /api/session` with `requirement_concepts`, INTERFACE A1), asked and
 * answered in place. When it completes, `onAnswered` re-reads the list — what
 * was answered disappears; the next version may use it.
 */
export default function UnaskedRequirementsPanel({
  requirements,
  jobId = null,
  onAnswered,
}: {
  requirements: UnaskedRequirement[] | null | undefined;
  /** The job the interview is on — absent = display only (the pre-ADR-099 panel). */
  jobId?: string | null;
  onAnswered?: () => void;
}) {
  const t = useTranslations("unaskedRequirements");
  const tD = useTranslations("documentReview.delivery");
  const tR = useTranslations("documentReview");
  const [turn, setTurn] = useState<Turn | null>(null);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [done, setDone] = useState(false);

  // An exception surface, not a permanent panel: with nothing unasked there is
  // nothing to say (ADR-074's silence is still logged on LETTER_UNASKED_REQUIREMENTS).
  if (!requirements || requirements.length === 0) {
    return done ? (
      <p data-testid="unasked-requirements-done" className="text-[13px] text-success">
        {tD("unaskedDone")}
      </p>
    ) : null;
  }
  const actionable = Boolean(jobId);

  async function start() {
    if (!jobId || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const res = await fetch(`${API_BASE}/api/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, requirement_concepts: requirements!.map((r) => r.concept) }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as { session_id: string; question?: string; first_question?: string; choices?: string[] | null };
      setTurn({ sessionId: data.session_id, question: data.question ?? data.first_question ?? "", choices: data.choices ?? null });
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  async function send(text: string) {
    if (!turn || busy || !text.trim()) return;
    setBusy(true);
    setFailed(false);
    try {
      const res = await fetch(`${API_BASE}/api/session/${turn.sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text.trim() }),
      });
      if (!res.ok) throw new Error(String(res.status));
      const data = (await res.json()) as { complete: boolean; question?: string | null; choices?: string[] | null };
      setAnswer("");
      if (data.complete) {
        setTurn(null);
        setDone(true);
        onAnswered?.();
      } else {
        setTurn({ sessionId: turn.sessionId, question: data.question ?? turn.question, choices: data.choices ?? null });
      }
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      data-testid="unasked-requirements-panel"
      aria-label={t("title")}
      className="rounded-xl border border-outline-variant surface-glass px-4 py-2.5"
    >
      <div className="flex items-start gap-2">
        <HelpCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1 space-y-0.5">
          <p data-testid="unasked-requirements-title" className="text-sm font-medium text-on-surface">
            {t("title")}
          </p>
          <p data-testid="unasked-requirements-subtitle" className="text-xs text-on-surface-variant">
            {t("subtitle")}
          </p>
        </div>
      </div>

      <ul className="mt-2 space-y-1 border-t border-outline-variant pt-2">
        {requirements.map((requirement, index) => (
          <li
            key={`${requirement.concept}-${index}`}
            data-testid={`unasked-requirement-${index}`}
            className="text-sm text-on-surface"
          >
            {requirement.concept}
          </li>
        ))}
      </ul>

      {!actionable && (
        <p data-testid="unasked-requirements-reassurance" className="mt-2 text-xs text-on-surface-variant">
          {t("reassurance")}
        </p>
      )}

      {actionable && !turn && (
        <div className="mt-2 flex flex-col gap-1.5">
          <button
            type="button"
            data-testid="unasked-requirements-answer"
            disabled={busy}
            onClick={() => void start()}
            className="flex min-h-11 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-white disabled:opacity-50"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {tD("unaskedAnswer")}
          </button>
          <p className="text-xs text-on-surface-variant">{tD("unaskedNote")}</p>
        </div>
      )}

      {turn && (
        <div data-testid="unasked-requirements-turn" className="mt-2 flex flex-col gap-2 border-t border-outline-variant pt-2">
          <span className="text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">{tD("unaskedQuestion")}</span>
          <p data-testid="unasked-requirements-question" className="text-sm text-on-surface">
            {turn.question}
          </p>
          {turn.choices && turn.choices.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {turn.choices.map((c) => (
                <button
                  key={c}
                  type="button"
                  disabled={busy}
                  onClick={() => void send(c)}
                  className="rounded-full border border-outline-variant bg-surface-bright px-3 py-1.5 text-[13px] font-semibold text-on-surface disabled:opacity-50"
                >
                  {c}
                </button>
              ))}
            </div>
          )}
          <textarea
            data-testid="unasked-requirements-text"
            aria-label={turn.question}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            disabled={busy}
            rows={3}
            className="w-full rounded-lg border border-outline-variant bg-surface-bright px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
          />
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="unasked-requirements-send"
              disabled={busy || !answer.trim()}
              onClick={() => void send(answer)}
              className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-full bg-primary px-4 text-sm font-semibold text-white disabled:opacity-50"
            >
              {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {tD("unaskedSend")}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setTurn(null);
                setAnswer("");
                onAnswered?.();
              }}
              className="min-h-11 rounded-full border border-outline-variant bg-surface-bright px-4 text-sm font-semibold text-on-surface"
            >
              {tR("addCancel")}
            </button>
          </div>
        </div>
      )}

      {failed && (
        <p data-testid="unasked-requirements-error" role="alert" className="mt-2 text-[13px] text-critical">
          {tD("unaskedFailed")}
        </p>
      )}
    </section>
  );
}
