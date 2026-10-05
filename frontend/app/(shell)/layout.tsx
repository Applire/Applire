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

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { AppSidebar } from "@/components/shell/AppSidebar";
import { HarnessBanner } from "@/components/shell/HarnessBanner";
import { ShellUserProvider } from "@/components/shell/ShellUserContext";
import { loginPathFor, useCurrentUser } from "@/lib/auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

/**
 * The signed-in app shell. US330 (ADR-091): nothing under (shell) renders for a
 * signed-out visitor — `/api/auth/me` answering 401 sends them to
 * `/login?next=<where they were>`. An unreachable backend ("error") still
 * renders the pages: an outage must not look like a sign-out, and each page
 * shows its own load errors.
 */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  const { status, authState } = useCurrentUser();
  const router = useRouter();
  const pathname = usePathname();
  const [userName, setUserName] = useState<string | null>(null);
  const ready = status === "authenticated" || status === "error";

  useEffect(() => {
    if (status !== "unauthenticated") return;
    if (authState?.setup_required) {
      router.replace("/setup");
      return;
    }
    router.replace(loginPathFor({ pathname: pathname ?? "/", search: window.location.search }));
  }, [status, authState?.setup_required, pathname, router]);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    fetch(`${API_BASE}/api/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        setUserName(d?.profile?.personal_info?.name ?? null);
      })
      .catch(() => { /* leave null — strip shows the e-mail only */ });
    return () => { cancelled = true; };
  }, [ready]);

  if (!ready) {
    return <div className="h-screen bg-surface-dim" data-testid="shell-auth-pending" aria-busy="true" />;
  }

  return (
    <ShellUserProvider userName={userName}>
      <div className="flex h-screen flex-col overflow-hidden bg-surface-dim">
        <HarnessBanner />
        <div className="flex flex-1 min-h-0 overflow-hidden">
          <AppSidebar userName={userName} />
          <div className="flex flex-col flex-1 min-w-0 overflow-y-auto">
            {children}
          </div>
        </div>
      </div>
    </ShellUserProvider>
  );
}
