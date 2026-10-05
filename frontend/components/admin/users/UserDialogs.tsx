"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * The people page's dialogs (W0-B mock admin-users §3–§13): add person, the
 * issued invite/reset link, the confirmations, and delete-by-typing-the-email
 * (G-2). Each dialog is mounted only while open (the caller renders it
 * conditionally), so its fields start empty without a reset effect. RD-6: no string here says anything about operator access to data; RD-5:
 * the add dialog names the controller role and points at the retention TTLs.
 */

import { useState } from "react";
import { useTranslations } from "next-intl";
import { TriangleAlert } from "lucide-react";

import { CopyField } from "@/components/account/CopyField";
import { ActionButton, Dialog, DialogError } from "@/components/account/Dialog";
import type { IssuedLink } from "./api";

const INPUT =
  "w-full rounded-lg border bg-white px-3 py-2 text-[14px] text-on-surface outline-none focus:border-teal focus:ring-2 focus:ring-teal/20";

export function AddPersonDialog({
  open,
  smtpEnabled,
  onClose,
  onSubmit,
}: {
  open: boolean;
  smtpEnabled: boolean;
  onClose: () => void;
  /** Resolves with null on success, else the copy to show under the field. */
  onSubmit: (email: string, role: "admin" | "user", sendMail: boolean) => Promise<string | null>;
}) {
  const t = useTranslations("adminUsers");
  const tAccount = useTranslations("account");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "user">("user");
  const [sendMail, setSendMail] = useState(smtpEnabled);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!email.trim()) return;
    setBusy(true);
    setError(null);
    const message = await onSubmit(email.trim(), role, smtpEnabled && sendMail);
    setBusy(false);
    if (message) setError(message);
  }

  const radio = (value: "user" | "admin", label: string) => (
    <label
      className={`flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 text-[13.5px] text-on-surface ${
        role === value ? "border-teal bg-teal-container-light" : "border-outline-variant bg-white"
      }`}
    >
      <input
        type="radio"
        name="admin-add-role"
        value={value}
        data-testid={`admin-add-role-${value}`}
        checked={role === value}
        onChange={() => setRole(value)}
        className="mt-0.5 accent-teal"
      />
      {label}
    </label>
  );

  return (
    <Dialog
      open={open}
      onClose={onClose}
      wide
      title={t("addDialogTitle")}
      testId="admin-add-dialog"
      actions={
        <>
          <ActionButton onClick={onClose}>{t("cancel")}</ActionButton>
          <ActionButton
            tone="primary"
            data-testid="admin-add-submit"
            disabled={busy || !email.trim()}
            onClick={() => void submit()}
          >
            {t("createInvite")}
          </ActionButton>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="flex flex-col gap-3"
      >
        <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-on-surface">
          {tAccount("emailLabel")}
          <input
            type="email"
            autoComplete="off"
            data-testid="admin-add-email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={error !== null}
            className={`${INPUT} font-normal ${error ? "border-critical" : "border-outline-variant"}`}
          />
          {error && (
            <span role="alert" data-testid="admin-add-error" className="text-[12.5px] font-normal text-critical">
              {error}
            </span>
          )}
        </label>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1.5 text-[13px] font-semibold text-on-surface">{t("roleLabel")}</legend>
          {radio("user", t("roleUserOption"))}
          {radio("admin", t("roleAdminOption"))}
        </fieldset>
        <label
          className={`flex items-start gap-2.5 text-[13.5px] ${smtpEnabled ? "cursor-pointer text-on-surface" : "text-on-surface-variant"}`}
        >
          <input
            type="checkbox"
            data-testid="admin-add-send-mail"
            checked={smtpEnabled && sendMail}
            disabled={!smtpEnabled}
            onChange={(e) => setSendMail(e.target.checked)}
            className="mt-0.5 accent-teal"
          />
          {smtpEnabled ? t("sendMail") : t("sendMailNoSmtp")}
        </label>
        <p data-testid="admin-add-controller-note" className="rounded-lg bg-surface-container px-3 py-2.5 text-[12.5px] text-on-surface-variant">
          {t("controllerNote")}
        </p>
      </form>
    </Dialog>
  );
}

export function LinkDialog({
  link,
  email,
  onClose,
}: {
  link: IssuedLink | null;
  email: string;
  onClose: () => void;
}) {
  const t = useTranslations("adminUsers");
  const tAccount = useTranslations("account");
  const isReset = link?.purpose === "reset";
  return (
    <Dialog
      open={link !== null}
      onClose={onClose}
      wide
      testId="admin-link-dialog"
      title={isReset ? t("resetDialogTitle", { email }) : t("inviteCreatedTitle", { email })}
      actions={
        <ActionButton tone="primary" data-testid="admin-link-close" onClick={onClose}>
          {t("close")}
        </ActionButton>
      }
    >
      {link && (
        <>
          {link.mail_failed && (
            <div
              role="alert"
              data-testid="admin-link-mail-failed"
              className="mb-3 flex items-start gap-2 rounded-lg border border-warning/50 bg-warning-container px-3 py-2.5 text-[13px] text-on-surface"
            >
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              {t("mailFailed")}
            </div>
          )}
          <p data-testid="admin-link-text">
            {isReset
              ? t("resetDialogBody", { email })
              : link.mailed
                ? t("inviteMailed")
                : t("inviteNotMailed", { email })}
          </p>
          <CopyField
            value={link.url}
            copyLabel={t("copyLink")}
            copiedLabel={tAccount("copied")}
            testId="admin-link-url"
          />
        </>
      )}
    </Dialog>
  );
}

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  danger,
  error,
  busy,
  onClose,
  onConfirm,
  testId,
}: {
  open: boolean;
  title: string;
  body?: string;
  confirmLabel?: string;
  danger?: boolean;
  /** When set, the dialog shows only this error and a close button (mock §12). */
  error?: string | null;
  busy?: boolean;
  onClose: () => void;
  onConfirm?: () => void;
  testId: string;
}) {
  const t = useTranslations("adminUsers");
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      testId={testId}
      actions={
        error ? (
          <ActionButton data-testid={`${testId}-close`} onClick={onClose}>
            {t("close")}
          </ActionButton>
        ) : (
          <>
            <ActionButton onClick={onClose}>{t("cancel")}</ActionButton>
            <ActionButton
              tone={danger ? "danger" : "primary"}
              data-testid={`${testId}-confirm`}
              disabled={busy}
              onClick={onConfirm}
            >
              {confirmLabel}
            </ActionButton>
          </>
        )
      }
    >
      {error ? <DialogError testId={`${testId}-error`}>{error}</DialogError> : body && <p>{body}</p>}
    </Dialog>
  );
}

export function DeleteUserDialog({
  email,
  error,
  busy,
  onClose,
  onConfirm,
}: {
  email: string | null;
  error: string | null;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const t = useTranslations("adminUsers");
  const [typed, setTyped] = useState("");

  const matches = email !== null && typed.trim().toLowerCase() === email.toLowerCase();

  return (
    <Dialog
      open={email !== null}
      onClose={onClose}
      testId="admin-delete-dialog"
      title={email ? t("deleteTitle", { email }) : undefined}
      actions={
        <>
          <ActionButton onClick={onClose}>{error ? t("close") : t("cancel")}</ActionButton>
          {!error && (
            <ActionButton
              tone="danger"
              data-testid="admin-delete-confirm"
              disabled={busy || !matches}
              onClick={onConfirm}
            >
              {t("deleteConfirm")}
            </ActionButton>
          )}
        </>
      }
    >
      {error ? (
        <DialogError testId="admin-delete-error">{error}</DialogError>
      ) : (
        <>
          <p>{t("deleteBody")}</p>
          <label className="mt-3 flex flex-col gap-1.5 text-[13px] text-on-surface">
            {t("deleteTypeEmail")}
            <input
              type="text"
              autoComplete="off"
              data-testid="admin-delete-email"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              className={`${INPUT} border-outline-variant`}
            />
          </label>
        </>
      )}
    </Dialog>
  );
}
