"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The one modal shell of the account + admin surfaces (Strawberry 2b).
 *
 * Portalled to `document.body` (a `fixed` overlay inside a transformed ancestor is
 * trapped — applire-i18n rule 5) at `z-[70]`, above the shell sidebar's `z-[60]`
 * (rule 4). Solid `bg-white` card — no undefined `bg-surface` (rule 2).
 */

import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export interface DialogProps {
  open: boolean;
  title?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** The buttons row, right-aligned. */
  actions?: ReactNode;
  wide?: boolean;
  testId?: string;
}

export function Dialog({ open, title, onClose, children, actions, wide, testId }: DialogProps) {
  const titleId = useId();
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    // Focus the first field (or the card) so keyboard users land inside.
    const first = cardRef.current?.querySelector<HTMLElement>("input, textarea, select, button");
    (first ?? cardRef.current)?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-neutral-dark/50 px-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
        data-testid={testId}
        className={`w-full ${wide ? "max-w-xl" : "max-w-md"} max-h-[90vh] overflow-y-auto rounded-xl bg-white p-6 shadow-card outline-none`}
      >
        {title && (
          <h2 id={titleId} className="mb-3 text-[17px] font-bold text-on-surface">
            {title}
          </h2>
        )}
        <div className="text-[14px] text-on-surface-variant">{children}</div>
        {actions && <div className="mt-5 flex flex-wrap justify-end gap-2">{actions}</div>}
      </div>
    </div>,
    document.body,
  );
}

type Tone = "secondary" | "primary" | "danger";

const TONE: Record<Tone, string> = {
  secondary: "border border-outline-variant bg-white text-on-surface hover:bg-surface-container",
  primary: "bg-teal text-white hover:bg-teal/90",
  danger: "bg-critical text-white hover:bg-critical/90",
};

/** The compact button of dialogs and card rows (the mocks' `.dbtn` / `.btn`). */
export function ActionButton({
  tone = "secondary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { tone?: Tone }) {
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-2 text-[13px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${TONE[tone]} ${className}`}
    />
  );
}

/** A red callout inside a dialog (last admin, wrong password …). */
export function DialogError({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className="mt-3 rounded-lg border border-critical/30 bg-critical-container px-3 py-2.5 text-[13px] text-critical"
    >
      {children}
    </div>
  );
}

/** A green confirmation callout (re-auth confirmed, password changed …). */
export function DialogSuccess({ children, testId }: { children: ReactNode; testId?: string }) {
  return (
    <div
      role="status"
      data-testid={testId}
      className="mt-3 rounded-lg border border-success/40 bg-success-container px-3 py-2.5 text-[13px] text-on-surface"
    >
      {children}
    </div>
  );
}
