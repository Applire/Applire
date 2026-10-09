"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/** An accessible on/off switch (role="switch"), Material-3 tokens; label via `labelledBy`. */
export function Toggle({
  checked,
  disabled,
  onChange,
  labelledBy,
  testId,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  labelledBy: string;
  testId: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-labelledby={labelledBy}
      data-testid={testId}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative h-[22px] w-10 shrink-0 rounded-full transition-colors disabled:opacity-60 ${checked ? "bg-teal" : "bg-outline-variant"}`}
    >
      <span
        aria-hidden="true"
        className={`absolute top-[3px] h-4 w-4 rounded-full bg-white shadow transition-all ${checked ? "left-[21px]" : "left-[3px]"}`}
      />
    </button>
  );
}
