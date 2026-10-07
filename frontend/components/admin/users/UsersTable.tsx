"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The people list (US324 UI; W0-B mock admin-users §1–§2; columns = founder
 * ruling W0B-1: e-mail, role, status, last sign-in, applications, storage, AI
 * usage 30 days — no name column, a name is vault content).
 *
 * The row menu is portalled and `fixed` at the kebab button: the table scrolls
 * horizontally at 390 px, and an absolutely positioned menu inside that scroll
 * container would be clipped. The scroll wrapper is `relative` so the header's
 * `sr-only` cell stays inside it — without it the absolutely positioned span
 * escaped to the page and widened the 390 px layout to 841 px (screenshot,
 * 2026-10-05; pinned by tests/oq/mobile/admin-account.spec.ts).
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocale, useTranslations } from "next-intl";
import {
  KeyRound,
  MailPlus,
  EllipsisVertical,
  RotateCcwKey,
  ShieldCheck,
  ShieldMinus,
  Trash2,
  UserCheck,
  UserX,
  type LucideIcon,
} from "lucide-react";

import { RoleBadge } from "@/components/account/AccountCard";
import { formatCompact, formatDate, formatMegabytes, formatWhen } from "@/components/account/format";
import type { AdminUser, UserStatus } from "./api";

export type UserAction =
  | "reinvite"
  | "resetLink"
  | "makeAdmin"
  | "makeUser"
  | "disable"
  | "enable"
  | "revokeTokens"
  | "delete";

/** Which actions a row offers, by status and role (mock §2). Delete is always last. */
export function actionsFor(user: AdminUser): UserAction[] {
  if (user.status === "pending") return ["reinvite", "delete"];
  if (user.status === "disabled") return ["enable", "revokeTokens", "delete"];
  return [
    "resetLink",
    user.role === "admin" ? "makeUser" : "makeAdmin",
    "revokeTokens",
    "disable",
    "delete",
  ];
}

const ACTION_META: Record<UserAction, { labelKey: string; icon: LucideIcon }> = {
  reinvite: { labelKey: "actionReinvite", icon: MailPlus },
  resetLink: { labelKey: "actionResetLink", icon: RotateCcwKey },
  makeAdmin: { labelKey: "actionMakeAdmin", icon: ShieldCheck },
  makeUser: { labelKey: "actionMakeUser", icon: ShieldMinus },
  disable: { labelKey: "actionDisable", icon: UserX },
  enable: { labelKey: "actionEnable", icon: UserCheck },
  revokeTokens: { labelKey: "actionRevokeTokens", icon: KeyRound },
  delete: { labelKey: "actionDelete", icon: Trash2 },
};

function StatusBadge({ status }: { status: UserStatus }) {
  const t = useTranslations("adminUsers");
  const style: Record<UserStatus, { box: string; dot: string; key: string }> = {
    active: { box: "bg-success-container text-on-surface", dot: "bg-success", key: "statusActive" },
    pending: { box: "bg-gold-container text-on-surface", dot: "bg-gold-dim", key: "statusPending" },
    disabled: { box: "bg-surface-container-high text-on-surface-variant", dot: "bg-on-surface-variant", key: "statusDisabled" },
  };
  const s = style[status];
  return (
    <span
      data-testid="user-status"
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11.5px] font-bold ${s.box}`}
    >
      <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {t(s.key)}
    </span>
  );
}

export function UsersTable({
  users,
  meId,
  onAction,
}: {
  users: AdminUser[];
  meId: string | null;
  onAction: (action: UserAction, user: AdminUser) => void;
}) {
  const t = useTranslations("adminUsers");
  const tAccount = useTranslations("account");
  const locale = useLocale();
  const now = new Date();

  return (
    <div className="relative overflow-x-auto rounded-xl bg-white p-2 shadow-soft">
      <table data-testid="admin-users-table" className="w-full min-w-[860px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-outline-variant text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
            <th className="px-3 py-2.5">{t("colEmail")}</th>
            <th className="px-3 py-2.5">{t("colRole")}</th>
            <th className="px-3 py-2.5">{t("colStatus")}</th>
            <th className="px-3 py-2.5">{t("colLastLogin")}</th>
            <th className="px-3 py-2.5 text-right">{t("colApplications")}</th>
            <th className="px-3 py-2.5 text-right">{t("colStorage")}</th>
            <th className="px-3 py-2.5 text-right">{t("colAiUsage")}</th>
            <th className="px-3 py-2.5">
              <span className="sr-only">{t("colActions")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const inviteExpired =
              u.status === "pending" && u.invite_expires_at !== null && new Date(u.invite_expires_at) < now;
            return (
              <tr
                key={u.id}
                data-testid="admin-user-row"
                data-email={u.email}
                className="border-b border-outline-variant/60 last:border-0 hover:bg-surface-container"
              >
                <td className="px-3 py-3">
                  <div className="font-semibold text-on-surface">
                    {u.email}
                    {u.id === meId && (
                      <span data-testid="admin-user-you" className="ml-1 font-normal text-on-surface-variant">
                        {["(", t("you"), ")"].join("")}
                      </span>
                    )}
                  </div>
                  {u.status === "pending" && (u.invite_expires_at || inviteExpired) && (
                    <div data-testid="admin-user-invite" className="mt-0.5 text-[11.5px] text-on-surface-variant">
                      {inviteExpired
                        ? t("inviteExpired")
                        : t("inviteExpiresOn", { date: formatDate(u.invite_expires_at!, locale) })}
                    </div>
                  )}
                </td>
                <td className="px-3 py-3">
                  <RoleBadge role={u.role} />
                </td>
                <td className="px-3 py-3">
                  <StatusBadge status={u.status} />
                </td>
                <td className="whitespace-nowrap px-3 py-3 text-on-surface">
                  {u.last_login_at ? (
                    formatWhen(u.last_login_at, locale, (k, v) => tAccount(k, v))
                  ) : (
                    <span className="text-on-surface-variant">{t("never")}</span>
                  )}
                </td>
                <td className="px-3 py-3 text-right tabular-nums text-on-surface">{u.metadata.application_count}</td>
                <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums text-on-surface">
                  {u.metadata.storage_bytes === null ? t("notMeasured") : formatMegabytes(u.metadata.storage_bytes, locale)}
                </td>
                <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums text-on-surface">
                  {u.metadata.ai_tokens_30d === null
                    ? t("notMeasured")
                    : t("tokensUnit", { count: formatCompact(u.metadata.ai_tokens_30d, locale) })}
                </td>
                <td className="px-3 py-3 text-right">
                  <RowMenu user={u} onAction={onAction} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RowMenu({ user, onAction }: { user: AdminUser; onAction: (a: UserAction, u: AdminUser) => void }) {
  const t = useTranslations("adminUsers");
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const open = pos !== null;

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      setPos(null);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setPos(null);
    }
    // Follow the kebab button when anything scrolls (the table scrolls sideways at
    // 390 px, the page vertically) instead of closing: a touch scroll that settles
    // right after the tap would otherwise swallow the menu.
    function onScroll() {
      const r = buttonRef.current?.getBoundingClientRect();
      if (r) setPos({ top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right) });
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    menuRef.current?.querySelector<HTMLElement>("button")?.focus({ preventScroll: true });
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  const actions = actionsFor(user);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-label={t("actionsAria", { email: user.email })}
        aria-haspopup="menu"
        aria-expanded={pos !== null}
        data-testid="admin-user-menu"
        onClick={() => {
          if (pos) return setPos(null);
          const r = buttonRef.current!.getBoundingClientRect();
          setPos({ top: r.bottom + 4, right: Math.max(8, window.innerWidth - r.right) });
        }}
        className="inline-flex h-8 w-8 items-center justify-center rounded-full text-on-surface-variant hover:bg-surface-container-high"
      >
        <EllipsisVertical className="h-4 w-4" aria-hidden />
      </button>
      {pos &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={menuRef}
            role="menu"
            data-testid="admin-user-menu-list"
            style={{ top: pos.top, right: pos.right }}
            className="fixed z-[65] min-w-[240px] rounded-xl border border-outline-variant bg-white py-1.5 shadow-card"
          >
            {actions.map((action) => {
              const meta = ACTION_META[action];
              const Icon = meta.icon;
              const danger = action === "delete";
              return (
                <div key={action}>
                  {danger && <hr className="my-1 border-outline-variant" />}
                  <button
                    type="button"
                    role="menuitem"
                    data-testid={`admin-user-action-${action}`}
                    onClick={() => {
                      setPos(null);
                      onAction(action, user);
                    }}
                    className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-[13px] hover:bg-surface-container ${
                      danger ? "text-critical" : "text-on-surface"
                    }`}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden />
                    {t(meta.labelKey)}
                  </button>
                </div>
              );
            })}
          </div>,
          document.body,
        )}
    </>
  );
}
