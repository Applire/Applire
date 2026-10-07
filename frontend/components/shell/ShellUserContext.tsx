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

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useCurrentUser } from "@/lib/auth/current-user";

interface ShellUserContextValue {
  /** Profile display name, or null while loading / if the fetch failed. */
  userName: string | null;
  /** US330: the signed-in account (from `/api/auth/me`), null while unknown. */
  id: string | null;
  email: string | null;
  role: "admin" | "user" | null;
}

const ShellUserContext = createContext<ShellUserContextValue>({ userName: null, id: null, email: null, role: null });

/**
 * Threads the profile display name — already fetched once in the (shell)
 * layout — down to AppTopbar's mobile avatar and MobileNavDrawer's header,
 * without prop-drilling it through every page.tsx (US223).
 */
export function ShellUserProvider({
  userName,
  children,
}: {
  userName: string | null;
  children: ReactNode;
}) {
  const { user } = useCurrentUser();
  const value = useMemo<ShellUserContextValue>(
    () => ({ userName, id: user?.id ?? null, email: user?.email ?? null, role: user?.role ?? null }),
    [userName, user],
  );
  return (
    <ShellUserContext.Provider value={value}>
      {children}
    </ShellUserContext.Provider>
  );
}

export function useShellUser(): ShellUserContextValue {
  return useContext(ShellUserContext);
}
