"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The one-line instance signal on the candidate dashboard (#694; c2 mock
 * user-dashboard-signal §1/§2): "Instanz: 2 Hinweise → Zur Administration".
 * Admins only — a non-admin never calls `/api/admin/notices` (contract §5.2) —
 * and only while there is something to say. No dismiss: it disappears with its
 * cause. Closes the push gap of Operator journey Step 5 (JF-O-3.1/5.1/7.1).
 */

import Link from "next/link";
import { useTranslations } from "next-intl";

import { signalFrom } from "@/lib/api/admin";
import { useAdminNotices } from "@/lib/api/admin-hooks";
import { useCurrentUser } from "@/lib/auth";

export function InstanceSignal() {
  const t = useTranslations("ops");
  const { isAdmin } = useCurrentUser();
  const notices = useAdminNotices(isAdmin);
  if (!isAdmin || notices.status !== "ready") return null;
  const s = signalFrom(notices.data);
  if (!s) return null;
  return (
    <div
      data-testid="instance-signal"
      data-critical={s.critical}
      role="status"
      className={`mb-4 flex items-center gap-2.5 rounded-lg border px-3.5 py-2 text-[13px] text-neutral-dark ${
        s.critical ? "border-critical/35 bg-critical-container" : "border-warning/45 bg-warning-container"
      }`}
    >
      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${s.critical ? "bg-critical" : "bg-warning"}`} />
      <span className="min-w-0">{s.critical ? t("dashboardSignalDown", { count: s.count }) : t("dashboardSignal", { count: s.count })}</span>
      <Link
        href="/admin/overview"
        data-testid="instance-signal-link"
        className="ml-auto whitespace-nowrap font-semibold text-teal underline underline-offset-2 hover:no-underline"
      >
        {t("dashboardSignalLink")}
      </Link>
    </div>
  );
}
