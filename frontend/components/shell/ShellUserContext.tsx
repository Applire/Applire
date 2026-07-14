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

import { createContext, useContext, type ReactNode } from "react";

interface ShellUserContextValue {
  /** Profile display name, or null while loading / if the fetch failed. */
  userName: string | null;
}

const ShellUserContext = createContext<ShellUserContextValue>({ userName: null });

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
  return (
    <ShellUserContext.Provider value={{ userName }}>
      {children}
    </ShellUserContext.Provider>
  );
}

export function useShellUser(): ShellUserContextValue {
  return useContext(ShellUserContext);
}
