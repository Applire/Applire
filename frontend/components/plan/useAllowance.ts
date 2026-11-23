"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The signed-in person's allowance (`GET /api/me/allowance`). Gated on the known
 * user: a person-scoped call from an always-mounted component must not run signed
 * out (the global 401 redirect). A failed fetch (old backend 404, 401) yields
 * `data = null` and the UI renders nothing. Re-reads after a plan change or a
 * purchase, and after a refusal (the counters moved).
 */

import { useCallback, useEffect, useState } from "react";

import { useCurrentUser } from "@/lib/auth";
import { getAllowance, type Allowance } from "./api";
import { ALLOWANCE_CHANGED_EVENT, ALLOWANCE_REFUSED_EVENT } from "./events";

export interface AllowanceState {
  data: Allowance | null;
  loading: boolean;
  error: boolean;
  refresh: () => Promise<void>;
}

export function useAllowance(): AllowanceState {
  const { user } = useCurrentUser();
  const enabled = user !== null;
  const [data, setData] = useState<Allowance | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState(false);

  const refresh = useCallback(async () => {
    const next = await getAllowance();
    setData(next);
    setError(next === null);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const onEvent = () => void refresh();
    window.addEventListener(ALLOWANCE_CHANGED_EVENT, onEvent);
    window.addEventListener(ALLOWANCE_REFUSED_EVENT, onEvent);
    return () => {
      window.removeEventListener(ALLOWANCE_CHANGED_EVENT, onEvent);
      window.removeEventListener(ALLOWANCE_REFUSED_EVENT, onEvent);
    };
  }, [enabled, refresh]);

  return { data: enabled ? data : null, loading, error, refresh };
}
