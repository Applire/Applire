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


import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  type EnrichActionResult,
  type EnrichSession,
  type GapItem,
  markGapNA,
  respondToEnrich,
  skipGap,
  startEnrichSession,
} from "@/lib/api/enrich";

interface Message {
  role: "assistant" | "user";
  content: string;
}

export interface EnrichmentDrawerProps {
  open: boolean;
  scope?: string;
  onClose: () => void;
}

export function EnrichmentDrawer({ open, scope, onClose }: EnrichmentDrawerProps) {
  const t = useTranslations("enrich");
  const tc = useTranslations("common");
  const [session, setSession] = useState<EnrichSession | null>(null);
  const [gaps, setGaps] = useState<GapItem[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState("");
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    setSession(null);
    setGaps([]);
    setMessages([]);
    setAnswer("");
    setDone(false);
    setError("");
    setLoading(true);

    startEnrichSession(scope)
      .then((s) => {
        setSession(s);
        setGaps(s.gaps);
        setMessages([{ role: "assistant", content: s.first_question }]);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "Error"))
      .finally(() => setLoading(false));
  }, [open, scope]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const applyResult = (
    result: EnrichActionResult,
    userMessage?: string
  ) => {
    setGaps(result.gaps);
    if (result.done) {
      setDone(true);
      return;
    }
    if (userMessage) {
      setMessages((prev) => [...prev, { role: "user", content: userMessage }]);
    }
    if (result.next_question) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: result.next_question! },
      ]);
    }
  };

  const handleSend = async () => {
    if (!session || !answer.trim()) return;
    const text = answer.trim();
    setAnswer("");
    setLoading(true);
    try {
      const result = await respondToEnrich(session.session_id, text);
      applyResult(result, text);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  };

  const handleSkip = async () => {
    if (!session) return;
    setLoading(true);
    try {
      const result = await skipGap(session.session_id);
      applyResult(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  };

  const handleNA = async () => {
    if (!session) return;
    setLoading(true);
    try {
      const result = await markGapNA(session.session_id);
      applyResult(result);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setLoading(false);
    }
  };

  const statusColor: Record<GapItem["status"], string> = {
    pending: "text-muted-foreground",
    active: "text-primary font-medium",
    done: "text-green-500",
    na: "text-muted-foreground line-through",
    skipped: "text-muted-foreground",
  };

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/50"
        onClick={onClose}
      />

      {/* Drawer panel */}
      <div className="fixed inset-y-0 right-0 z-50 w-[90vw] sm:w-[600px] md:w-[700px] bg-white border-l border-gray-200 flex flex-col shadow-lg">

        {/* Header */}
        <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between shrink-0">
          <div>
            <span className="text-sm font-semibold text-neutral-dark">{t("title")}</span>
            {scope && (
              <span className="ml-2 text-xs text-gray-500 font-normal">
                {scope.split(":").slice(1).join(" @ ")}
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-lg leading-none"
            aria-label={tc("ariaClose")}
          >
            ✕
          </button>
        </div>

        {loading && !session && (
          <div className="flex-1 flex items-center justify-center text-gray-500 text-sm">
            {t("loading")}
          </div>
        )}

        {error && (
          <div className="flex-1 flex items-center justify-center text-red-600 text-sm px-4 text-center">
            {error}
          </div>
        )}

        {done && (
          <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center px-8">
            <div className="text-3xl text-success">✓</div>
            <p className="text-sm font-medium text-neutral-dark">{t("done")}</p>
            <Button variant="outline" onClick={onClose}>{t("close")}</Button>
          </div>
        )}

        {session && !done && !error && (
          <div className="flex flex-1 min-h-0">
            {/* Gap list */}
            <div className="w-44 shrink-0 border-r border-gray-200 overflow-y-auto p-3 flex flex-col gap-1">
              <p className="text-[10px] uppercase tracking-wide text-gray-400 mb-2">
                {t("gaps")}
              </p>
              {gaps.map((g) => (
                <div
                  key={g.id}
                  className={cn(
                    "text-xs rounded px-2 py-1.5 leading-tight",
                    g.status === "active" && "bg-teal-container font-medium text-teal",
                    g.status === "pending" && "text-gray-500",
                    g.status === "done" && "text-success",
                    (g.status === "na" || g.status === "skipped") && "text-gray-400 line-through",
                  )}
                >
                  {g.label}
                </div>
              ))}
            </div>

            {/* Chat panel */}
            <div className="flex-1 flex flex-col min-h-0">
              <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
                {messages.map((m, i) => (
                  <div
                    key={i}
                    className={cn(
                      "max-w-[85%] rounded-lg px-3 py-2 text-sm leading-relaxed",
                      m.role === "assistant"
                        ? "self-start bg-surface-container border-l-2 border-teal text-neutral-dark"
                        : "self-end bg-teal-container text-neutral-dark text-right"
                    )}
                  >
                    {m.content}
                  </div>
                ))}
                <div ref={chatEndRef} />
              </div>

              {/* Input area */}
              <div className="shrink-0 border-t border-gray-200 p-3 flex flex-col gap-2">
                <textarea
                  className="w-full resize-none rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-neutral-dark placeholder:text-gray-400 focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/20 min-h-[72px]"
                  placeholder={t("placeholder")}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) handleSend();
                  }}
                  disabled={loading}
                />
                <div className="flex justify-between items-center">
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-xs h-7 text-gray-500 hover:text-gray-700"
                      onClick={handleSkip}
                      disabled={loading}
                    >
                      {t("skip")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-xs h-7 text-gray-400 hover:text-gray-600"
                      onClick={handleNA}
                      disabled={loading}
                    >
                      {t("markNA")}
                    </Button>
                  </div>
                  <Button
                    size="sm"
                    onClick={handleSend}
                    disabled={loading || !answer.trim()}
                  >
                    {loading ? t("sending") : t("send")}
                  </Button>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
