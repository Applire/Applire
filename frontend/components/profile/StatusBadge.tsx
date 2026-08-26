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

// US291 (H2.1) — a read-only provenance badge for a skill/language/
// certification entry's `status`. Never a form control: the editors below
// echo whatever status an existing entry already carries and never let a
// form edit change it.

import { useTranslations } from "next-intl";
import type { EntryStatus } from "@/lib/profile-entries";

const STATUS_KEYS: Record<EntryStatus, "statusConfirmed" | "statusUnconfirmed" | "statusDenied"> = {
  confirmed: "statusConfirmed",
  unconfirmed: "statusUnconfirmed",
  denied: "statusDenied",
};

const STATUS_STYLES: Record<EntryStatus, string> = {
  confirmed: "bg-success-container text-on-surface",
  unconfirmed: "bg-warning-container text-on-surface",
  denied: "bg-critical-container text-critical",
};

interface StatusBadgeProps {
  status?: EntryStatus | string | null;
}

export function StatusBadge({ status }: StatusBadgeProps) {
  const t = useTranslations("profile");
  if (status !== "confirmed" && status !== "unconfirmed" && status !== "denied") return null;
  return (
    <span
      data-testid="status-badge"
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${STATUS_STYLES[status]}`}
    >
      {t(`entryEditor.${STATUS_KEYS[status]}`)}
    </span>
  );
}
