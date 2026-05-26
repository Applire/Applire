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
import { AppSidebar } from "@/components/shell/AppSidebar";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export default function ShellLayout({ children }: { children: React.ReactNode }) {
  const [userName, setUserName] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        setUserName(d?.profile?.personal_info?.name ?? null);
      })
      .catch(() => { /* leave null — strip stays hidden */ });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-surface-dim">
      <AppSidebar userName={userName} />
      <div className="flex flex-col flex-1 min-w-0 overflow-y-auto">
        {children}
      </div>
    </div>
  );
}
