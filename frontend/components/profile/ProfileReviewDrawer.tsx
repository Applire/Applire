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
// session engine (no JD).
//
// Hazelnut build 5 (UAT-1, U-2…U-5; ADR-099 cl. 11): the walk STARTS on the
// issue the user clicked (`issue_id`), a conflict renders as a card with its
// closed answer set (`ConflictCard`) and every chip sends a STRUCTURED outcome —
// never its label through the free-text matcher (build 4's "Beide behalten"
// sent a sentence the matcher read as keep-existing). A typed answer is
// interpreted by the backend into an outcome, a corrected value or ONE
// follow-up; after that follow-up only the outcome buttons remain — the same
// question never comes back verbatim.

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  type HealthIssue,
  type NotAppliedGroup,
  type Translator,
  describeConflictIssue,
  groupNotAppliedItems,
} from "@/components/profile/HealthPanel";
import { ConflictCard } from "@/components/profile/ConflictCard";
import {
  type CommitOutcome,
  type ConflictOutcome,
  type CurrentConflict,
  type ReviewReply,
  type ReviewTurn,
  ReviewApiError,
  answerWithOutcome,
  answerWithText,
  interpretedOutcome,
  mergeBecameOwnStatement,
  requestMergeProposal,
  startReview,
} from "@/lib/profile-review";
import { undoLastMerge } from "@/lib/profile-merge";

interface Message {
  role: "assistant" | "user";
  content: string;
}

interface Notice {
  tone: "ok" | "warn";
  text: string;
  /** A confirmed merge can be undone from the notice (one undo, ADR-099 cl. 11):
   *  the profile's `updated_at` right after the merge — the undo's basis (ADV-8). */
  undo?: string;
}

/**
 * #705 — the full not-retained receipt, grouped by section. A standalone
 * component (rather than an inline `.map()` in `ProfileReviewDrawer`'s own
 * render) because the React Compiler could not preserve `submit`'s existing
 * manual memoization (`[sessionId]`) once that `.map()` was inlined —
 * isolating it here keeps `ProfileReviewDrawer`'s own body analysis
 * unaffected (`react-hooks/preserve-manual-memoization` was clean before and
 * after this extraction).
 */
function NotAppliedGroupsList({
  groups,
  t,
  tHealth,
  onSectionAction,
}: {
  groups: NotAppliedGroup[];
  t: Translator;
  tHealth: Translator;
  onSectionAction?: (section: string, labels: string[]) => void;
}) {
  return (
    <div
      data-testid="profile-review-not-applied-groups"
      className="w-full max-h-[320px] overflow-y-auto flex flex-col gap-3 text-left"
    >
      {groups.map((group) => (
        <div
          key={group.section ?? "none"}
          data-testid="not-applied-group"
          className="rounded-lg border border-outline-variant bg-white p-3"
        >
          <p className="text-sm font-semibold text-neutral-dark">
            {tHealth("notAppliedGroupHeading", {
              section: group.sectionLabel,
              count: group.labels.length,
            })}
          </p>
          <p className="text-xs text-on-surface-variant mt-1">{group.labels.join(", ")}</p>
          <p className="text-xs text-on-surface-variant mt-1 italic">{group.reasonText}</p>
          {group.section && onSectionAction && (
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              data-testid="profile-review-section-action"
              onClick={() => onSectionAction(group.section!, group.labels)}
            >
              {t("goToSection", { section: group.sectionLabel })}
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}

export interface ProfileReviewDrawerProps {
  open: boolean;
  onClose: () => void;
  // F3b (run3): the health issue the user clicked "Resolve" on. When the
  // conflict-walk has nothing to do (gaps_total 0 — e.g. a merge-loss/accuracy
  // issue), the done state must show THIS issue and an action, not a generic
  // "All done" dead-end. onAction routes to the affected section to fix it.
  issue?: HealthIssue | null;
  onAction?: (issue: HealthIssue) => void;
  // #705 (founder UAT, 2026-09-15) — per-section navigation for a
  // `not_applied` issue carrying the full structured item list
  // (`not_applied_items`): one action per affected section, naming the
  // section and the labels it takes there, instead of `onAction`'s single
  // button landing on "one of the affected sections" chosen at random.
  onSectionAction?: (section: string, labels: string[]) => void;
}

export function ProfileReviewDrawer({
  open,
  onClose,
  issue,
  onAction,
  onSectionAction,
}: ProfileReviewDrawerProps) {
  const t = useTranslations("profileReview");
  // #626 — the same localized composition HealthPanel uses, so a `conflict`
  // issue shown here (the merge-loss/no-conflicts-to-walk state below) never
  // falls back to the backend's raw "section.field: 'x' vs 'y'" summary either.
  const tHealth = useTranslations("health");
  const tProfile = useTranslations("profile");
  const conflictIssue = issue ? describeConflictIssue(issue, tHealth, tProfile) : null;
  // #705 — the full per-item receipt, grouped by section. `[]` for every
  // issue except a `not_applied` one carrying `not_applied_items` (a record
  // from a backend predating #705 falls back to the single summary below).
  const notAppliedGroups =
    issue?.thread === "not_applied" ? groupNotAppliedItems(issue, tHealth, tProfile) : [];
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  // Choices of a NON-conflict question (a parked confirmation's options) —
  // these are still answered in words; a conflict uses `conflict` below.
  const [choices, setChoices] = useState<string[] | null>(null);
  const [conflict, setConflict] = useState<CurrentConflict | null>(null);
  const [followUpUsed, setFollowUpUsed] = useState(false);
  const [mergeUnavailable, setMergeUnavailable] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<Notice | null>(null);
  const [progress, setProgress] = useState<{ total: number; remaining: number; first: boolean } | null>(null);
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  // The conflict-walk had nothing to do (gaps_total 0). With an `issue` in hand
  // this is the merge-loss/accuracy case → show the issue + action, not all-clear.
  const [noConflicts, setNoConflicts] = useState(false);
  const [error, setError] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);

  const outcomeLabel = useCallback(
    (o: ConflictOutcome, c: CurrentConflict | null) =>
      o === "keep_both" &&
      c?.shape === "scalar" &&
      ["work_experience", "volunteer_activities"].includes(c.section ?? "")
        ? t("keepBoth")
        : t(`outcome.${o}`),
    [t],
  );

  // One turn of the walk: the item on screen is the card when the backend names
  // a conflict, otherwise the question bubble + its choices.
  const applyTurn = useCallback((turn: ReviewTurn) => {
    setChoices(turn.choices);
    setConflict(turn.current_conflict ?? null);
    setFollowUpUsed(Boolean(turn.follow_up_used));
    if (!turn.current_conflict && turn.question) {
      setMessages((prev) => [...prev, { role: "assistant", content: turn.question! }]);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setSessionId(null);
    setMessages([]);
    setChoices(null);
    setConflict(null);
    setFollowUpUsed(false);
    setMergeUnavailable(new Set());
    setNotice(null);
    setProgress(null);
    setAnswer("");
    setDone(false);
    setNoConflicts(false);
    setError("");
    setLoading(true);

    // U-2 — start on the issue the user clicked, not the generic walk.
    // An item closed meanwhile answers 404 issue_not_found → the generic walk.
    startReview(issue?.id ?? null)
      .catch((e: unknown) => {
        if (issue && e instanceof ReviewApiError && e.status === 404) return startReview(null);
        throw e;
      })
      .then((s) => {
        setSessionId(s.session_id);
        // gaps_total 0 → nothing to review: land straight on the all-clear state.
        if (s.gaps_total === 0) {
          setNoConflicts(true);
          setDone(true);
          return;
        }
        setProgress({ total: s.gaps_total, remaining: s.gaps_remaining, first: Boolean(issue) });
        applyTurn({ ...s, question: s.first_question });
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Error"))
      .finally(() => setLoading(false));
    // `issue` is read once per opening: the drawer is re-opened per click.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, applyTurn]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages, conflict, notice]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const afterReply = useCallback(
    (result: ReviewReply, onScreen: CurrentConflict | null, typed: boolean) => {
      const read = result.interpreted ?? null;
      if (mergeBecameOwnStatement(read)) {
        setNotice({ tone: "warn", text: t("mergeBecameTestimony") });
      } else if (read?.corrected_value) {
        setNotice({ tone: "ok", text: t("correctedSaved", { value: read.corrected_value }) });
      } else if (read?.follow_up) {
        // On a conflict the card stays and the follow-up is its own bubble; on
        // a plain question `question` already carries follow-up + question.
        if (result.current_conflict || !result.question) {
          setMessages((prev) => [...prev, { role: "assistant", content: read.follow_up! }]);
        }
      } else if (typed) {
        // Only a TYPED answer is echoed back; a tapped outcome needs no echo.
        const o = interpretedOutcome(read);
        if (o) setNotice({ tone: "ok", text: t("typedUnderstood", { outcome: outcomeLabel(o, onScreen) }) });
      }
      if (result.complete) {
        setDone(true);
        return;
      }
      setProgress((p) => (p ? { ...p, remaining: result.gaps_remaining ?? p.remaining, first: false } : p));
      applyTurn(result);
    },
    [applyTurn, outcomeLabel, t],
  );

  const run = useCallback(
    async (
      userLine: string,
      call: () => Promise<ReviewReply>,
      onScreen: CurrentConflict | null,
      typed: boolean,
    ) => {
      setLoading(true);
      setError("");
      setMessages((prev) => [...prev, { role: "user", content: userLine }]);
      try {
        afterReply(await call(), onScreen, typed);
      } catch (e: unknown) {
        if (e instanceof ReviewApiError && e.code === "outcome_required") {
          // The ONE follow-up was spent: only the buttons remain (U-4/U-5).
          setFollowUpUsed(true);
        } else {
          setError(e instanceof Error ? e.message : "Error");
        }
      } finally {
        setLoading(false);
      }
    },
    [afterReply],
  );

  const sendOutcome = (outcome: CommitOutcome, line: string, mergedText?: string) => {
    if (!sessionId) return;
    const onScreen = conflict;
    setNotice(null);
    void run(
      line,
      async () => {
        const result = await answerWithOutcome(sessionId, outcome, mergedText);
        // ADV-8: Undo only for a merge the server says it WROTE, and only with
        // its basis; the notice goes with the next answer (the next write).
        if (
          outcome === "merge_confirm" &&
          result.interpreted?.outcome === "merge" &&
          result.profile_updated_at
        ) {
          setNotice({ tone: "ok", text: t("mergeDone"), undo: result.profile_updated_at });
        }
        return result;
      },
      onScreen,
      false,
    );
  };

  const sendText = (text: string) => {
    const value = text.trim();
    if (!sessionId || !value) return;
    setAnswer("");
    setNotice(null);
    void run(value, () => answerWithText(sessionId, value), conflict, true);
  };

  const proposeMerge = async (): Promise<string | null> => {
    if (!sessionId || !conflict) return null;
    const id = conflict.conflict_id;
    try {
      const p = await requestMergeProposal(sessionId, id);
      if (p.verifier === "passed" && p.proposal) return p.proposal;
    } catch {
      /* a failed proposal call is the same as a failed check: no merge offered */
    }
    setMergeUnavailable((prev) => new Set(prev).add(id));
    return null;
  };

  const undo = async (basis: string) => {
    try {
      const r = await undoLastMerge(basis);
      setNotice({ tone: "ok", text: r.restored ? t("undone") : t("undoNothing") });
    } catch (e: unknown) {
      setNotice({
        tone: "warn",
        text: e instanceof ReviewApiError && e.status === 409 ? t("undoStale") : t("actionFailed"),
      });
    }
  };

  if (!open) return null;

  const current = progress ? progress.total - progress.remaining + 1 : 0;
  const textLocked = conflict !== null && followUpUsed;

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-50 bg-black/50" onClick={onClose} />

      {/* Drawer panel */}
      <div
        data-testid="profile-review-drawer"
        className="fixed inset-y-0 right-0 z-50 w-full sm:w-[600px] md:w-[700px] bg-white border-l border-gray-200 flex flex-col shadow-lg"
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

        {error && !sessionId && (
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
            className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-8 py-6 overflow-y-auto"
          >
            <p className="text-sm font-medium text-neutral-dark">{t("issueHeading")}</p>
            {conflictIssue ? (
              <div data-testid="profile-review-issue-conflict">
                <p className="text-sm text-on-surface-variant">{conflictIssue.heading}</p>
                <p className="text-xs text-on-surface-variant mt-1">{conflictIssue.existingRow}</p>
                <p className="text-xs text-on-surface-variant">{conflictIssue.incomingRow}</p>
              </div>
            ) : notAppliedGroups.length > 0 ? (
              // #705 — the full receipt, grouped by section: which items, in
              // which section, and why — scrollable when it runs long, with
              // one "go to section" action per group instead of a single
              // button that lands on an unnamed, randomly-chosen section.
              <NotAppliedGroupsList
                groups={notAppliedGroups}
                t={t}
                tHealth={tHealth}
                onSectionAction={onSectionAction}
              />
            ) : (
              <p className="text-sm text-on-surface-variant">{issue.summary}</p>
            )}
            <div className="flex flex-col items-center gap-2">
              {notAppliedGroups.length === 0 && onAction && (
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
            {notice && (
              <ReviewNotice notice={notice} onUndo={undo} undoLabel={t("undo")} />
            )}
            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
            <div className="text-3xl text-success" aria-hidden="true">✓</div>
            <p className="text-sm font-medium text-neutral-dark">{t("done")}</p>
            <Button variant="outline" onClick={onClose}>{t("close")}</Button>
          </div>
        )}

        {!done && sessionId && (
          <div className="flex flex-1 flex-col min-h-0">
            {/* Chat panel */}
            <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
              {progress && progress.total > 1 && (
                <p data-testid="profile-review-progress" className="text-xs text-on-surface-variant">
                  {progress.first
                    ? t("progressClicked", { current, total: progress.total })
                    : t("progress", { current, total: progress.total })}
                </p>
              )}
              {notice && <ReviewNotice notice={notice} onUndo={undo} undoLabel={t("undo")} />}
              {conflict && (
                <ConflictCard
                  key={conflict.conflict_id}
                  conflict={conflict}
                  busy={loading}
                  mergeUnavailable={mergeUnavailable.has(conflict.conflict_id)}
                  onOutcome={(o) => sendOutcome(o, outcomeLabel(o, conflict))}
                  onRequestMerge={proposeMerge}
                  onConfirmMerge={(text) => sendOutcome("merge_confirm", text, text)}
                />
              )}
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={cn(
                    "max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed break-words",
                    m.role === "assistant"
                      ? "self-start bg-surface-container border-l-2 border-teal text-neutral-dark"
                      : "self-end bg-teal-container text-neutral-dark text-right",
                  )}
                >
                  {m.content}
                </div>
              ))}
              {error && (
                <p role="alert" className="text-sm text-critical">
                  {error}
                </p>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Input area */}
            <div className="shrink-0 border-t border-gray-200 p-3 flex flex-col gap-2">
              {!conflict && choices && choices.length > 0 && (
                <div className="flex flex-col gap-2">
                  {choices.map((choice) => (
                    <Button
                      key={choice}
                      variant="outline"
                      size="sm"
                      className="h-auto min-h-8 justify-start whitespace-normal py-1.5 text-left"
                      onClick={() => sendText(choice)}
                      disabled={loading}
                    >
                      {choice}
                    </Button>
                  ))}
                </div>
              )}
              {textLocked ? (
                <p data-testid="profile-review-outcome-required" className="text-xs text-on-surface-variant">
                  {t("outcomeRequired")}
                </p>
              ) : (
                <>
                  <textarea
                    data-testid="profile-review-answer"
                    className="w-full resize-none rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-neutral-dark placeholder:text-gray-400 focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20 min-h-[56px]"
                    placeholder={
                      conflict
                        ? conflict.shape === "list"
                          ? t("typedPlaceholder")
                          : t("typedPlaceholderScalar")
                        : t("placeholder")
                    }
                    value={answer}
                    onChange={(e) => setAnswer(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) sendText(answer);
                    }}
                    disabled={loading}
                  />
                  <div className="flex justify-end">
                    <Button
                      size="sm"
                      data-testid="profile-review-send"
                      onClick={() => sendText(answer)}
                      disabled={loading || !answer.trim()}
                    >
                      {loading ? t("sending") : t("send")}
                    </Button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function ReviewNotice({
  notice,
  onUndo,
  undoLabel,
}: {
  notice: Notice;
  onUndo: (basis: string) => void;
  undoLabel: string;
}) {
  return (
    <div
      role="status"
      data-testid="profile-review-notice"
      className={cn(
        "flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-left text-sm text-on-surface",
        notice.tone === "ok"
          ? "border-success/30 bg-success-container"
          : "border-warning/40 bg-warning-container",
      )}
    >
      <span>{notice.text}</span>
      {notice.undo && (
        <Button size="sm" variant="ghost" data-testid="profile-review-undo" onClick={() => onUndo(notice.undo!)}>
          {undoLabel}
        </Button>
      )}
    </div>
  );
}
