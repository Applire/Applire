"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The admin area frame (G-2/G-3): topbar titled with the active section, the
 * sub-nav built from `ADMIN_SECTIONS`, then the section page. Admin-only — a
 * signed-in non-admin sees the "for admins" card (c2 mock user-dashboard-signal
 * §4; Epic C) and no sub-nav, and no section page mounts, so no admin endpoint
 * is called (the backend answers 403 on every admin route regardless). A
 * signed-out visitor never gets here: the shell's 401 handling (2a) redirects
 * to /login.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";

import { AdminForbidden } from "@/components/admin/common";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { useCurrentUser } from "@/lib/auth";
import { ADMIN_SECTIONS, activeSection } from "./sections";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const t = useTranslations("adminNav");
  const pathname = usePathname();
  const { status, isAdmin } = useCurrentUser();
  const active = activeSection(pathname);

  if (status !== "authenticated") {
    return <div className="flex-1 bg-surface-dim" />;
  }

  if (!isAdmin) {
    return (
      <div className="flex flex-1 flex-col overflow-hidden bg-surface-dim">
        <AppTopbar mode="detail" backHref="/dashboard" backLabelKey="shell.dashboard" pageTitle={t("ariaLabel")} />
        <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">
          <AdminForbidden />
        </main>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-surface-dim">
      <AppTopbar mode="detail" backHref="/dashboard" backLabelKey="shell.dashboard" pageTitle={t(active.labelKey)} />
      <main className="flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        <div className="mx-auto max-w-6xl">
          <nav aria-label={t("ariaLabel")} className="mb-5 flex gap-1 overflow-x-auto border-b border-outline-variant">
            {ADMIN_SECTIONS.map((s) => {
              const on = s.key === active.key;
              return (
                <Link
                  key={s.key}
                  href={s.href}
                  data-testid={`admin-nav-${s.key}`}
                  aria-current={on ? "page" : undefined}
                  className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-[13.5px] font-semibold transition-colors ${
                    on ? "border-teal text-teal" : "border-transparent text-on-surface-variant hover:text-on-surface"
                  }`}
                >
                  {t(s.labelKey)}
                </Link>
              );
            })}
          </nav>
          {children}
        </div>
      </main>
    </div>
  );
}
