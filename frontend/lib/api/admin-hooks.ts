// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

"use client";

/**
 * React hooks over the Epic C admin client (`./admin`). One generic loader
 * (`useAdminResource`) gives every admin page the same four states, so a 403 is
 * always rendered as the "for admins" state and never as "unavailable".
 * No polling: an admin page loads once and on an explicit `reload()` (ADR-086
 * cl. 9 — the backend keeps its own caches warm).
 */

import { useCallback, useEffect, useState } from "react";

import {
  getAuditPage,
  getDashboard,
  getInstanceSettings,
  getNotices,
  getUsage,
  type AdminFailure,
  type AdminResult,
  type AuditEventItem,
  type AuditQuery,
  type UsagePeriod,
} from "./admin";

export type AdminLoadState<T> =
  | { status: "loading"; data: null }
  | { status: "ready"; data: T }
  | { status: "forbidden"; data: null }
  | { status: "error"; data: null; failure: AdminFailure };

export function toLoadState<T>(r: AdminResult<T>): AdminLoadState<T> {
  if (r.ok) return { status: "ready", data: r.data };
  if (r.kind === "forbidden") return { status: "forbidden", data: null };
  return { status: "error", data: null, failure: r };
}

export function useAdminResource<T>(
  loader: () => Promise<AdminResult<T>>,
  deps: readonly unknown[],
  enabled = true,
): AdminLoadState<T> & { reload: () => void; setData: (d: T) => void } {
  const [state, setState] = useState<AdminLoadState<T>>({ status: "loading", data: null });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    // The loader is re-created every render; `deps` name what it closes over.
    void loader().then((r) => {
      if (!cancelled) setState(toLoadState(r));
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const setData = useCallback((d: T) => setState({ status: "ready", data: d }), []);
  return { ...state, reload, setData };
}

export function useAdminDashboard() {
  return useAdminResource(getDashboard, []);
}

export function useInstanceSettings() {
  return useAdminResource(getInstanceSettings, []);
}

export function useAdminUsage(days: UsagePeriod) {
  return useAdminResource(() => getUsage(days), [days]);
}

/** Only fetched for admins (contract §5.2): a non-admin never calls the route. */
export function useAdminNotices(isAdmin: boolean) {
  return useAdminResource(getNotices, [], isAdmin);
}

/**
 * The audit view: first page for a filter, then "load older" appends the next
 * keyset page. Changing the filter starts over.
 */
export function useAuditLog(filter: Omit<AuditQuery, "cursor">) {
  const key = JSON.stringify(filter);
  const [page, setPage] = useState<{
    key: string | null;
    status: "ready" | "forbidden" | "error";
    items: AuditEventItem[];
    actions: string[];
    cursor: string | null;
  }>({ key: null, status: "ready", items: [], actions: [], cursor: null });
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void getAuditPage(JSON.parse(key) as AuditQuery).then((r) => {
      if (cancelled) return;
      if (!r.ok) {
        setPage((p) => ({ ...p, key, status: r.kind === "forbidden" ? "forbidden" : "error" }));
        return;
      }
      setPage({ key, status: "ready", items: r.data.items, actions: r.data.actions, cursor: r.data.next_cursor });
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  const cursor = page.key === key ? page.cursor : null;
  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setLoadingMore(true);
    const r = await getAuditPage({ ...(JSON.parse(key) as AuditQuery), cursor });
    setLoadingMore(false);
    if (!r.ok) return;
    setPage((p) => (p.key === key ? { ...p, items: [...p.items, ...r.data.items], cursor: r.data.next_cursor } : p));
  }, [cursor, key]);

  // A filter change shows "loading" until the page for the NEW filter arrives.
  const status = page.key === key ? page.status : "loading";
  return {
    status,
    items: page.key === key ? page.items : [],
    // The action list is filter-independent: keep the last one for the dropdown.
    actions: page.actions,
    hasMore: cursor !== null,
    loadMore,
    loadingMore,
  };
}
