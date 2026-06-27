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

// US165 (E033 / ADR-041) — the standalone profile-review interview surface.
// Launched from the Health panel's "Resolve" action: walks the user's open
// Tier-2 conflicts and resolves each through the ADR-013 merge. Drives the real
// session engine (no JD) via the profileReview API client.

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { HealthIssue } from "@/components/profile/HealthPanel";
import {
  type ProfileReviewMessageResult,
  sendProfileReviewMessage,
  startProfileReview,
} from "@/lib/api/profileReview";

interface Message {
  role: "assistant" | "user";
  content: string;
}

// F4 (#73): submitted as a natural-language answer to the review interview
// (sendProfileReviewMessage), so the LLM resolves the conflict by keeping both
// entries as two separate roles rather than forcing an either/or date pick.
// This is the conversational counterpart to #71's deterministic same-title gate
// on the CV-upload merge — not a structured payload the merge engine parses.
// Kept locale-independent so the model sees stable wording; the button label is
// localized chrome.
const KEEP_BOTH_INTENT =
  "These are two separate roles — keep both (e.g. I was promoted; do not merge them into one).";

export interface ProfileReviewDrawerProps {
  open: boolean;
  onClose: () => void;
  // F3b (run3): the health issue the user clicked "Resolve" on. When the
  // conflict-walk has nothing to do (gaps_total 0 — e.g. a merge-loss/accuracy
  // issue), the done state must show THIS issue and an action, not a generic
  // "All done" dead-end. onAction routes to the affected section to fix it.
  issue?: HealthIssue | null;
  onAction?: (issue: HealthIssue) => void;
}

export function ProfileReviewDrawer({ open, onClose, issue, onAction }: ProfileReviewDrawerProps) {
  const t = useTranslations("profileReview");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [choices, setChoices] = useState<string[] | null>(null);
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  // The conflict-walk had nothing to do (gaps_total 0). With an `issue` in hand
  // this is the merge-loss/accuracy case → show the issue + action, not all-clear.
  const [noConflicts, setNoConflicts] = useState(false);
  const [error, setError] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setSessionId(null);
    setMessages([]);
    setChoices(null);
    setAnswer("");
    setDone(false);
    setNoConflicts(false);
    setError("");
    setLoading(true);

    startProfileReview()
      .then((s) => {
        setSessionId(s.session_id);
        // gaps_total 0 → nothing to review: land straight on the all-clear state.
        if (s.gaps_total === 0) {
          setNoConflicts(true);
          setDone(true);
          return;
        }
        setMessages([{ role: "assistant", content: s.first_question }]);
        setChoices(s.choices);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Error"))
      .finally(() => setLoading(false));
  }, [open]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const submit = useCallback(
    async (text: string, displayAs?: string) => {
      const value = text.trim();
      if (!sessionId || !value) return;
      setAnswer("");
      setChoices(null);
      setLoading(true);
      setMessages((prev) => [
        ...prev,
        { role: "user", content: (displayAs ?? value).trim() },
      ]);
      try {
        const result: ProfileReviewMessageResult = await sendProfileReviewMessage(
          sessionId,
          value,
        );
        if (result.complete) {
          setDone(true);
          return;
        }
        if (result.question) {
          setMessages((prev) => [
            ...prev,
            { role: "assistant", content: result.question! },
          ]);
        }
        setChoices(result.choices);
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Error");
      } finally {
        setLoading(false);
      }
    },
    [sessionId],
  );

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-50 bg-black/50" onClick={onClose} />

      {/* Drawer panel */}
      <div
        data-testid="profile-review-drawer"
        className="fixed inset-y-0 right-0 z-50 w-[90vw] sm:w-[600px] md:w-[700px] bg-white border-l border-gray-200 flex flex-col shadow-lg"
      >
        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between shrink-0">
          <span className="text-sm font-semibold text-neutral-dark">{t("title")}</span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-lg leading-none"
            aria-label={t("close")}
          >
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            <span aria-hidden="true">✕</span>
          </button>
        </div>

        {loading && !sessionId && (
          <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">
            {t("loading")}
          </div>
        )}

        {error && (
          <div className="flex-1 flex items-center justify-center text-red-600 text-sm px-4 text-center">
            {error}
          </div>
        )}

        {done && issue && noConflicts && (
          // The conflict-walk had nothing to resolve, but the user came here
          // from a real flagged issue (e.g. merge dropped skills). Show the
          // actual problem + an action — never a generic all-clear (F3b run3).
          <div
            data-testid="profile-review-issue"
            className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-8"
          >
            <p className="text-sm font-medium text-neutral-dark">{t("issueHeading")}</p>
            <p className="text-sm text-on-surface-variant">{issue.summary}</p>
            <div className="flex flex-col items-center gap-2">
              {onAction && (
                <Button
                  data-testid="profile-review-action"
                  onClick={() => onAction(issue)}
                >
                  {t("issueAction")}
                </Button>
              )}
              <p className="text-xs text-gray-500">{t("issueActionHint")}</p>
              <Button variant="ghost" size="sm" onClick={onClose}>{t("close")}</Button>
            </div>
          </div>
        )}

        {done && !(issue && noConflicts) && (
          <div
            data-testid="profile-review-done"
            className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-8"
          >
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            <div className="text-3xl text-success" aria-hidden="true">✓</div>
            <p className="text-sm font-medium text-neutral-dark">{t("done")}</p>
            <Button variant="outline" onClick={onClose}>{t("close")}</Button>
          </div>
        )}

        {!done && !error && sessionId && (
          <div className="flex flex-1 flex-col min-h-0">
            {/* Chat panel */}
            <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={cn(
                    "max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed",
                    m.role === "assistant"
                      ? "self-start bg-surface-container border-l-2 border-teal text-neutral-dark"
                      : "self-end bg-teal-container text-neutral-dark text-right",
                  )}
                >
                  {m.content}
                </div>
              ))}
              <div ref={chatEndRef} />
            </div>

            {/* Input area */}
            <div className="shrink-0 border-t border-gray-200 p-3 flex flex-col gap-2">
              {choices && choices.length > 0 && (
                <div className="flex flex-col gap-2">
                  {choices.map((choice) => (
                    <Button
                      key={choice}
                      variant="outline"
                      size="sm"
                      className="justify-start text-left"
                      onClick={() => submit(choice)}
                      disabled={loading}
                    >
                      {choice}
                    </Button>
                  ))}
                  {/* F4 (#73): escape the either/or framing — keep both as two roles. */}
                  <Button
                    data-testid="profile-review-keep-both"
                    variant="ghost"
                    size="sm"
                    className="justify-start text-left text-teal hover:text-teal/80"
                    onClick={() => submit(KEEP_BOTH_INTENT, t("keepBoth"))}
                    disabled={loading}
                  >
                    {t("keepBoth")}
                  </Button>
                  <p className="text-xs text-gray-500 leading-snug">{t("keepBothHint")}</p>
                </div>
              )}
              <textarea
                className="w-full resize-none rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-neutral-dark placeholder:text-gray-400 focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20 min-h-[56px]"
                placeholder={t("placeholder")}
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(answer);
                }}
                disabled={loading}
              />
              <div className="flex justify-end">
                <Button
                  size="sm"
                  onClick={() => submit(answer)}
                  disabled={loading || !answer.trim()}
                >
                  {loading ? t("sending") : t("send")}
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
