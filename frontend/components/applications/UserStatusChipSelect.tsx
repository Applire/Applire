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

// Shared pipeline-status control (E041/US231) — pure presentation, lifted
// verbatim out of DashboardApplicationCard so the cockpit header can reuse
// the exact same chip-styled <select>. The caller owns the PATCH and any
// optimistic/revert state; this component never fetches.

import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";
import { USER_STATUS_OPTIONS } from "@/lib/user-status";

export interface UserStatusChipSelectProps {
  /** Current user_status value. */
  value: string;
  /** Caller owns the PATCH + optimistic/revert state. */
  onChange: (next: string) => void;
  /** True on the dashboard card, whose body is itself a click target/link. */
  stopClickPropagation?: boolean;
}

export function UserStatusChipSelect({
  value,
  onChange,
  stopClickPropagation,
}: UserStatusChipSelectProps) {
  const tDash = useTranslations("dashboard");
  const statusOption =
    USER_STATUS_OPTIONS.find((o) => o.value === value) ?? USER_STATUS_OPTIONS[0];

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onClick={stopClickPropagation ? (e) => e.stopPropagation() : undefined}
      aria-label={tDash("statusSelectLabel")}
      title={tDash("statusSelectLabel")}
      className={cn(
        "text-[10px] font-bold pl-2 pr-1 py-0.5 rounded-full uppercase tracking-wide cursor-pointer border-0",
        statusOption.className
      )}
    >
      {USER_STATUS_OPTIONS.map((option) => (
        <option key={option.value} value={option.value}>
          {tDash(option.labelKey)}
        </option>
      ))}
    </select>
  );
}
