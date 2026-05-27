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

// frontend/components/cv/AssistMicroSession.tsx
"use client";

import { useState, useEffect } from "react";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface GapHintItem {
  id: string;
  label: string;
}

interface AssistMicroSessionProps {
  cvId: string;
  sectionId: string;
  gap: GapHintItem;
  onAccept: (suggestion: string) => void;
  onEdit: (suggestion: string) => void;
  onReject: () => void;
}

type Phase = "loading" | "question" | "submitting" | "suggestion" | "error";

export function AssistMicroSession({
  cvId,
  sectionId,
  gap,
  onAccept,
  onEdit,
  onReject,
}: AssistMicroSessionProps) {
  const t = useTranslations("cv");
  const [phase, setPhase] = useState<Phase>("loading");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    void startSession();
  }, []);

  async function startSession() {
    setPhase("loading");
    setErrorMsg(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/cv/${cvId}/sections/${sectionId}/assist`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ gap_id: gap.id }),
        }
      );
      if (!res.ok) throw new Error(`${res.status}`);
      const data: { session_id: string; question: string } = await res.json();
      setSessionId(data.session_id);
      setQuestion(data.question);
      setPhase("question");
    } catch {
      setErrorMsg(t("assistQuestionError"));
      setPhase("error");
    }
  }

  async function submitAnswer() {
    if (!sessionId || !answer.trim()) return;
    setPhase("submitting");
    setErrorMsg(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/cv/${cvId}/sections/${sectionId}/assist`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, answer }),
        }
      );
      if (!res.ok) throw new Error(`${res.status}`);
      const data: { suggestion: string } = await res.json();
      setSuggestion(data.suggestion);
      setPhase("suggestion");
    } catch {
      setErrorMsg(t("assistSuggestionError"));
      setPhase("question");
    }
  }

  return (
    <div className="mt-2 rounded-lg border border-teal/40 bg-teal/5 p-3 text-xs">
      <p className="text-xs font-semibold text-teal mb-2">{t("kaileHelperLabel")}</p>

      {phase === "loading" && (
        <div data-testid="assist-loading" className="flex items-center gap-2 text-gray-500">
          <span className="animate-pulse">{t("assistLoading")}</span>
        </div>
      )}

      {(phase === "question" || phase === "submitting") && (
        <>
          <p data-testid="assist-question" className="text-gray-700 mb-2">
            {question}
          </p>
          <textarea
            data-testid="assist-answer"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder={t("assistPlaceholder")}
            className="w-full min-h-[60px] resize-y text-xs border border-gray-200 rounded p-2 focus:outline-none focus:ring-1 focus:ring-teal"
            disabled={phase === "submitting"}
          />
          {errorMsg && <p className="text-critical text-xs mt-1">{errorMsg}</p>}
          <div className="flex gap-2 mt-2">
            <button
              type="button"
              data-testid="assist-submit"
              onClick={() => void submitAnswer()}
              disabled={phase === "submitting" || !answer.trim()}
              className="flex-1 bg-teal text-white py-1.5 rounded text-xs font-semibold disabled:opacity-40"
            >
              {phase === "submitting" ? t("assistSending") : t("assistSend")}
            </button>
            <button
              type="button"
              onClick={onReject}
              className="text-xs text-gray-500 underline hover:opacity-70"
            >
              {t("cancel")}
            </button>
          </div>
        </>
      )}

      {phase === "suggestion" && (
        <>
          <p className="text-gray-500 mb-1">{t("assistSuggests")}</p>
          <p className="text-gray-800 bg-white border border-gray-100 rounded p-2 mb-3">
            {suggestion}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              data-testid="assist-accept"
              onClick={() => onAccept(suggestion)}
              className="flex-1 bg-teal text-white py-1.5 rounded text-xs font-semibold hover:opacity-90"
            >
              {t("apply")}
            </button>
            <button
              type="button"
              data-testid="assist-edit"
              onClick={() => onEdit(suggestion)}
              className="flex-1 border border-teal text-teal py-1.5 rounded text-xs font-semibold hover:opacity-90"
            >
              {t("assistEditBtn")}
            </button>
            <button
              type="button"
              data-testid="assist-reject"
              onClick={onReject}
              className="flex-1 border border-gray-300 text-gray-500 py-1.5 rounded text-xs font-semibold hover:opacity-90"
            >
              {t("assistReject")}
            </button>
          </div>
        </>
      )}

      {phase === "error" && (
        <>
          <p className="text-critical text-xs mb-2">{errorMsg}</p>
          <button
            type="button"
            onClick={() => void startSession()}
            className="text-xs text-teal underline"
          >
            {t("assistRetry")}
          </button>
        </>
      )}
    </div>
  );
}
