"use client";

// Copyright (C) 2026 Tobias Rosenbaum
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

import { useEffect, useState } from "react";

/** Seconds a turn may run before the page says it is taking longer. The audit's
 * reconcile call timed out at 180 s; the processing overlay's heartbeat turns
 * to elapsed seconds at 20 s. */
export const SLOW_TURN_AFTER_SECONDS = 25;

/**
 * 787:205 — seconds a turn has been running while `active`, 0 when idle. The
 * page shows its "this takes longer" state from {@link SLOW_TURN_AFTER_SECONDS}
 * on, so a long wait is never a bare spinner.
 */
export function useTurnElapsed(active: boolean): number {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!active) {
      setElapsed(0);
      return;
    }
    const startedAt = Date.now();
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    return () => clearInterval(id);
  }, [active]);
  return active ? elapsed : 0;
}
