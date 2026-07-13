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

/**
 * Shared user-status pipeline config (E039/US218).
 *
 * Single source for every surface that renders or edits `user_status`
 * (dashboard card, application detail, filter chips) — mirrors the backend
 * `UserStatus` enum in pipeline order:
 * tracking → applied → interviewing → offer/rejected → hired.
 */

export interface UserStatusOption {
  value: string;
  /** Key in the `dashboard` i18n namespace. */
  labelKey: string;
  /** Badge / chip styling for this status. */
  className: string;
}

export const USER_STATUS_OPTIONS: UserStatusOption[] = [
  { value: "tracking",     labelKey: "statusTracking",     className: "bg-gray-400 text-white" },
  { value: "applied",      labelKey: "statusApplied",      className: "bg-blue-500 text-white" },
  { value: "interviewing", labelKey: "statusInterviewing", className: "bg-teal text-white" },
  { value: "offer",        labelKey: "statusOffer",        className: "bg-success text-white" },
  { value: "rejected",     labelKey: "statusRejected",     className: "bg-critical text-white" },
  { value: "hired",        labelKey: "statusHired",        className: "bg-[#166534] text-white" },
  // US222/issue #158 — terminal walk-away; short retention clock (ADR-005).
  { value: "cancelled",    labelKey: "statusCancelled",    className: "bg-gray-500 text-white" },
];

/**
 * US222: cancelled applications leave the active portfolio — they render in
 * their own collapsed dashboard section (removal date + restore) and are
 * excluded from active counts, filter chips and the card grid.
 */
export function splitCancelled<T extends { user_status?: string | null }>(
  apps: T[],
): { active: T[]; cancelled: T[] } {
  const active: T[] = [];
  const cancelled: T[] = [];
  for (const app of apps) {
    ((app.user_status ?? "tracking") === "cancelled" ? cancelled : active).push(app);
  }
  return { active, cancelled };
}

/** Days after which an unchanged active status counts as stale (JF-E-P2.1). */
export const STALE_STATUS_DAYS = 14;

// Statuses where the real world moves without the app noticing — a response
// arrives, an interview happens, an offer gets signed. tracking (not yet in
// the pipeline) and the terminal states can't go stale.
const ACTIVE_STATUSES = new Set(["applied", "interviewing", "offer"]);

/**
 * Cheap staleness detection: an active pipeline status that hasn't been
 * touched in STALE_STATUS_DAYS probably no longer reflects reality.
 */
export function isStaleStatus(
  userStatus: string | undefined,
  updatedAt: string,
  now: Date = new Date(),
): boolean {
  if (!userStatus || !ACTIVE_STATUSES.has(userStatus)) return false;
  const ageDays = (now.getTime() - new Date(updatedAt).getTime()) / (24 * 36e5);
  return ageDays > STALE_STATUS_DAYS;
}

/** The plausible next pipeline states offered by the stale-status refresh prompt. */
export function staleNextStatuses(userStatus: string): string[] {
  switch (userStatus) {
    case "applied":
      return ["interviewing", "rejected"];
    case "interviewing":
      return ["offer", "rejected"];
    case "offer":
      return ["hired", "rejected"];
    default:
      return [];
  }
}

/** Per-status counts for the dashboard filter chips; missing status = tracking. */
export function countByUserStatus(
  apps: Array<{ user_status?: string | null }>,
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const app of apps) {
    const status = app.user_status ?? "tracking";
    counts[status] = (counts[status] ?? 0) + 1;
  }
  return counts;
}
