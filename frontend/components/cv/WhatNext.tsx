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
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface WhatNextProps {
  flowId: string;
  roleTitle?: string | null;
}

export function WhatNext({ flowId, roleTitle }: WhatNextProps) {
  const router = useRouter();
  const t = useTranslations("cv");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    // Advance flow to complete on mount (Task 20.5) — best-effort
    fetch(`${API_BASE}/api/flow/${flowId}/advance`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step: "complete" }),
    }).catch(() => {});
  }, [flowId]);

  async function handleCopy() {
    const text = roleTitle
      ? t("shareWithRole", { roleTitle })
      : t("shareGeneric");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // clipboard API may be unavailable
    }
  }

  return (
    <div className="max-w-md mx-auto text-center animate-slide-up">
      <div className="w-16 h-16 rounded-full bg-success flex items-center justify-center text-white text-2xl mx-auto mb-4">
        ✓
      </div>

      <h1 className="text-2xl font-heading font-bold text-neutral-dark mb-2">
        {t("cvReady")}
      </h1>
      <p className="text-sm text-gray-500 mb-8">
        {t("cvReadyTagline")}
      </p>

      <div className="flex flex-col gap-3">
        <button
          type="button"
          onClick={() => void handleCopy()}
          className="w-full bg-gold text-white font-semibold py-3 rounded-lg text-sm hover:opacity-90 transition-opacity"
          data-testid="apply-button"
        >
          {copied ? t("summaryCopied") : t("copySummary")}
        </button>

        <button
          type="button"
          onClick={() => router.push("/dashboard")}
          className="w-full bg-teal text-white font-semibold py-3 rounded-lg text-sm hover:opacity-90 transition-colors"
        >
          {t("startNewApplication")}
        </button>

        <button
          type="button"
          onClick={() => router.push("/dashboard")}
          className="text-sm text-teal hover:underline"
        >
          {t("backToHome")}
        </button>
      </div>
    </div>
  );
}
