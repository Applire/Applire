// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
"use client";

/**
 * US330 — the account menu on the topbar avatar (W0-B mock user-menu.html,
 * screens 1–2): who is signed in + role, "Konto und Tokens" (→ /settings#account),
 * "Administration" for admins only, "Abmelden".
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";

import { useCurrentUser } from "@/lib/auth/current-user";
import { cn } from "@/lib/utils";

interface UserMenuProps {
  /** The avatar content (letters), rendered inside the trigger button. */
  children: React.ReactNode;
  triggerClassName?: string;
}

export function UserMenu({ children, triggerClassName }: UserMenuProps) {
  const t = useTranslations("shell");
  const router = useRouter();
  const { user, isAdmin, signOut } = useCurrentUser();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const firstItemRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    firstItemRef.current?.focus();
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function go(href: string) {
    setOpen(false);
    router.push(href);
  }

  async function handleSignOut() {
    setSigningOut(true);
    await signOut();
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label={t("userMenuAria")}
        aria-haspopup="menu"
        aria-expanded={open}
        data-testid="user-menu-trigger"
        onClick={() => setOpen((v) => !v)}
        className={triggerClassName}
      >
        {children}
      </button>
      {open && (
        <div
          role="menu"
          data-testid="user-menu"
          className="absolute right-0 top-[calc(100%+8px)] z-[70] w-max min-w-[232px] max-w-[min(340px,calc(100vw-24px))] rounded-xl border border-gray-100 bg-white py-1.5 shadow-card"
        >
          {user ? (
            <div className="border-b border-gray-100 px-3.5 pb-2.5 pt-1.5">
              <p className="text-[11px] text-gray-500">{t("userMenuSignedInAs")}</p>
              <div className="mt-0.5 flex items-center gap-2">
                <p className="min-w-0 truncate text-[13px] font-bold text-gray-900" title={user.email} data-testid="user-menu-email">
                  {user.email}
                </p>
                <span
                  data-testid="user-menu-role"
                  className={cn(
                    "flex-shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-semibold",
                    isAdmin ? "bg-teal-container text-teal" : "bg-gray-100 text-gray-600",
                  )}
                >
                  {isAdmin ? t("roleAdmin") : t("roleUser")}
                </span>
              </div>
            </div>
          ) : null}
          <div className="px-1.5 py-1">
            <MenuItem
              ref={firstItemRef}
              icon={ACCOUNT_ICON}
              label={t("userMenuAccount")}
              testId="user-menu-account"
              onClick={() => go("/settings#account")}
            />
            {isAdmin && (
              <MenuItem
                icon={ADMIN_ICON}
                label={t("userMenuAdmin")}
                testId="user-menu-admin"
                onClick={() => go("/admin/users")}
              />
            )}
          </div>
          <div className="border-t border-gray-100 px-1.5 pt-1">
            <MenuItem
              icon={SIGN_OUT_ICON}
              label={signingOut ? t("signingOut") : t("userMenuSignOut")}
              testId="user-menu-sign-out"
              onClick={handleSignOut}
              disabled={signingOut}
            />
          </div>
        </div>
      )}
    </div>
  );
}

interface MenuItemProps {
  icon: string;
  label: string;
  testId: string;
  onClick: () => void;
  disabled?: boolean;
}

const MenuItem = ({ ref, icon, label, testId, onClick, disabled }: MenuItemProps & { ref?: React.Ref<HTMLButtonElement> }) => (
  <button
    ref={ref}
    type="button"
    role="menuitem"
    data-testid={testId}
    onClick={onClick}
    disabled={disabled}
    className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] text-gray-800 hover:bg-surface-container focus:bg-surface-container focus:outline-none disabled:opacity-60"
  >
    <span className="material-symbols-outlined text-gray-500" style={{ fontSize: 18 }} aria-hidden="true">
      {icon}
    </span>
    {label}
  </button>
);

const ACCOUNT_ICON = "manage_accounts";
const ADMIN_ICON = "shield_person";
const SIGN_OUT_ICON = "logout";
