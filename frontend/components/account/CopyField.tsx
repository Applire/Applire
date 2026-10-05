"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * A secret or link shown in full with a copy button — the shown-once token, an
 * invite or reset link, and the setup snippets. Monospace and `break-all`, so a
 * 60-character token wraps instead of widening the dialog at 390 px.
 */

import { useState } from "react";
import { Check, Copy } from "lucide-react";

export interface CopyFieldProps {
  value: string;
  copyLabel: string;
  copiedLabel: string;
  /** `secret` = the gold shown-once box; `link` = a neutral box; `code` = dark snippet. */
  tone?: "secret" | "link" | "code";
  testId?: string;
}

export function CopyField({ value, copyLabel, copiedLabel, tone = "link", testId }: CopyFieldProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard refused (insecure origin on a plain-http LAN install): the value is
      // on screen and selectable — nothing else to do.
    }
  }

  if (tone === "code") {
    return (
      <pre
        data-testid={testId}
        className="mt-2 whitespace-pre-wrap break-all rounded-lg bg-neutral-dark px-3 py-2.5 font-mono text-[12px] leading-relaxed text-white"
      >
        {value}
      </pre>
    );
  }

  const box =
    tone === "secret"
      ? "border-warning/50 bg-gold-container"
      : "border-outline-variant bg-surface-container";

  return (
    <div className={`mt-2 flex items-start gap-2 rounded-lg border px-3 py-2.5 ${box}`}>
      <code data-testid={testId} className="min-w-0 flex-1 break-all font-mono text-[12.5px] text-on-surface">
        {value}
      </code>
      <button
        type="button"
        onClick={() => void copy()}
        data-testid={testId ? `${testId}-copy` : undefined}
        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-outline-variant bg-white px-2.5 py-1 text-[12px] font-semibold text-on-surface hover:bg-surface-container"
      >
        {copied ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
        {copied ? copiedLabel : copyLabel}
      </button>
    </div>
  );
}
