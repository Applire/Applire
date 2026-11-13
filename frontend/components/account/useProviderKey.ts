"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-096 — the signed-in person's provider-key state. Fetched once per mount
 * (and only while `enabled`: a person-scoped call from an always-mounted
 * provider must not run signed out), re-read when the key card saves/removes.
 */

import { useCallback, useEffect, useState } from "react";

import { PROVIDER_KEY_CHANGED_EVENT } from "@/lib/provider-key-events";
import { getProviderKey, type ProviderKeyState } from "./api";

export function useProviderKey(enabled: boolean): ProviderKeyState | null {
  const [state, setState] = useState<ProviderKeyState | null>(null);

  const load = useCallback(async () => {
    const next = await getProviderKey();
    setState(next);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    void load();
    const onChanged = () => void load();
    window.addEventListener(PROVIDER_KEY_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(PROVIDER_KEY_CHANGED_EVENT, onChanged);
  }, [enabled, load]);

  return enabled ? state : null;
}
