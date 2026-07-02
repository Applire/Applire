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

/**
 * "Profile import still in progress" indicator (PQ F1 AC3 — truthful dashboard).
 *
 * With CV import jobs queued server-side up-front, a refresh mid-import lands the user
 * here while the backend is still merging CVs into the Master Profile. Without this
 * banner the dashboard confidently presented a half-imported profile as complete. It
 * checks GET /api/profile/import-jobs?active=true once on mount; while any job is
 * pending/processing it shows the banner and keeps polling, and once the queue drains
 * it hides itself and fires `onAllDone` (so the parent can refresh stale profile data).
 */

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

interface Props {
  /** Called once when previously-active imports have all finished. */
  onAllDone?: () => void;
  /** Poll interval while imports are active; defaults to 5s. */
  pollMs?: number;
}

export function ImportInProgressBanner({ onAllDone, pollMs = 5000 }: Props) {
  const t = useTranslations("dashboard");
  const [activeCount, setActiveCount] = useState(0);
  const wasActive = useRef(false);
  // Latest-callback ref so the poll effect never restarts just because the parent
  // re-rendered with a fresh inline closure.
  const onAllDoneRef = useRef(onAllDone);
  useEffect(() => {
    onAllDoneRef.current = onAllDone;
  }, [onAllDone]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function check() {
      let count = 0;
      try {
        const res = await fetch(`${API_BASE}/api/profile/import-jobs?active=true`);
        if (res.ok) {
          const items = (await res.json()) as unknown[];
          count = Array.isArray(items) ? items.length : 0;
        }
      } catch {
        // Network hiccup — treat as "nothing active" rather than a stuck banner.
      }
      if (stopped) return;
      setActiveCount(count);
      if (count > 0) {
        wasActive.current = true;
        timer = setTimeout(check, pollMs); // keep watching until the queue drains
      } else if (wasActive.current) {
        wasActive.current = false;
        onAllDoneRef.current?.(); // let the parent refresh now-stale profile data
      }
    }

    void check();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [pollMs]);

  if (activeCount === 0) return null;

  return (
    <div
      data-testid="import-in-progress-banner"
      className="flex items-center gap-2.5 rounded-xl border border-outline-variant bg-warning-container px-4 py-3 mb-4"
    >
      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-neutral-dark" aria-hidden />
      <p className="text-[13px] text-neutral-dark">{t("importInProgress")}</p>
    </div>
  );
}
