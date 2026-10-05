"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Settings → "Konto" (US326/US323 UI, W0-B mock settings-account §1–§7, founder
 * gate G-1/G-2): e-mail + role, password change, SSO link/unlink, sign out, and
 * self-delete (password, or an SSO re-auth for a password-less account — MD-7).
 *
 * The IdP round-trips come back to `/settings` (MD-25): `?oidc=linked`,
 * `?oidc=failed`, `?reauth=account.delete|oidc.unlink`. They are read once on
 * mount and stripped from the address bar, so a reload does not replay them.
 * `window.location` rather than `useSearchParams` — the latter needs a Suspense
 * boundary on a statically built page.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { CircleCheck, Link2, LogOut } from "lucide-react";

import {
  clearUserBrowserState,
  readErrorCode,
  suppressAuthRedirect,
  useCurrentUser,
  type CurrentUser,
} from "@/lib/auth";
import {
  changePassword,
  deleteMyAccount,
  followAuthorizeUrl,
  rememberOidcIntent,
  startOidcLink,
  startReauth,
  takeOidcIntent,
  unlinkOidc,
} from "./api";
import { ActionButton, Dialog, DialogError, DialogSuccess } from "./Dialog";

type Notice = { tone: "ok" | "error"; text: string } | null;

const PASSWORD_MIN = 12;
const PASSWORD_MAX = 256;

/** Read the IdP callback markers once, then drop them from the address bar. */
function consumeCallbackParams(): { reauth: string | null; oidc: string | null } {
  if (typeof window === "undefined") return { reauth: null, oidc: null };
  const url = new URL(window.location.href);
  const reauth = url.searchParams.get("reauth");
  const oidc = url.searchParams.get("oidc");
  if (reauth || oidc) {
    url.searchParams.delete("reauth");
    url.searchParams.delete("oidc");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }
  return { reauth, oidc };
}

export function AccountCard() {
  const t = useTranslations("account");
  const { user, authState, refresh, signOut } = useCurrentUser();
  const label = authState?.oidc_button_label ?? "";
  const oidcEnabled = Boolean(authState?.oidc_enabled);

  const [ssoNotice, setSsoNotice] = useState<Notice>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirmed, setDeleteConfirmed] = useState(false);
  const [deleteInitialError, setDeleteInitialError] = useState<string | null>(null);
  const [unlinkConfirmed, setUnlinkConfirmed] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  // One pass over the callback markers (MD-25). `label` may arrive after the first
  // render, so the copy is resolved here with whatever is known; the label is in
  // `/api/auth/state`, fetched together with `/api/auth/me` by the provider.
  useEffect(() => {
    const { reauth, oidc } = consumeCallbackParams();
    if (oidc === "linked") {
      setSsoNotice({ tone: "ok", text: "linked" });
      void refresh();
    } else if (oidc === "failed") {
      const intent = takeOidcIntent();
      if (intent === "account.delete") {
        setDeleteInitialError("reauth");
        setDeleteOpen(true);
      } else if (intent === "oidc.unlink") {
        setSsoNotice({ tone: "error", text: "reauth" });
      } else {
        setSsoNotice({ tone: "error", text: "link" });
      }
    }
    if (reauth === "account.delete") {
      setDeleteConfirmed(true);
      setDeleteOpen(true);
    } else if (reauth === "oidc.unlink") {
      setUnlinkConfirmed(true);
    }
    // Mount-only by design: the markers are one-shot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!user) return null;

  const ssoText = (n: NonNullable<Notice>): string => {
    switch (n.text) {
      case "linked":
        return t("ssoLinkedToast", { label });
      case "unlinked":
        return t("ssoUnlinkedToast", { label });
      case "reauth":
        return t("errorReauthFailed", { label });
      case "link":
        return t("errorOidcLinkFailed", { label });
      case "unavailable":
        return t("errorOidcUnavailable", { label });
      case "lastCredential":
        return t("errorLastCredential", { label });
      default:
        return t("errorGeneric");
    }
  };

  return (
    <section data-testid="account-card" className="rounded-xl bg-white p-6 shadow-soft">
      <h2 className="font-heading text-xl font-bold text-on-surface">{t("title")}</h2>

      <dl className="mt-3 grid grid-cols-[max-content_1fr] gap-x-8 gap-y-1.5 text-[14px]">
        <dt className="text-on-surface-variant">{t("emailLabel")}</dt>
        <dd data-testid="account-email" className="min-w-0 break-all font-semibold text-on-surface">
          {user.email}
        </dd>
        <dt className="text-on-surface-variant">{t("roleLabel")}</dt>
        <dd>
          <RoleBadge role={user.role} />
        </dd>
      </dl>

      <div className="mt-4 flex flex-col gap-3">
        <PasswordRow user={user} label={label} />

        {oidcEnabled && (
          <SsoRow
            user={user}
            label={label}
            notice={ssoNotice}
            noticeText={ssoNotice ? ssoText(ssoNotice) : ""}
            setNotice={setSsoNotice}
            unlinkConfirmed={unlinkConfirmed}
            setUnlinkConfirmed={setUnlinkConfirmed}
            onChanged={refresh}
          />
        )}

        <Row title={t("signOutTitle")} hint={t("signOutHint")}>
          <ActionButton
            data-testid="account-sign-out"
            disabled={signingOut}
            onClick={() => {
              setSigningOut(true);
              void signOut();
            }}
          >
            <LogOut className="h-4 w-4" aria-hidden />
            {t("signOutButton")}
          </ActionButton>
        </Row>

        <div className="flex flex-col gap-3 rounded-lg border border-critical/25 bg-critical-container/50 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h3 className="font-semibold text-critical">{t("deleteAccountTitle")}</h3>
            <p className="text-[13px] text-on-surface-variant">{t("deleteAccountHint")}</p>
            <p className="mt-1.5 text-[12px] text-on-surface-variant">{t("deleteAccountDistinction")}</p>
          </div>
          <ActionButton
            tone="danger"
            data-testid="account-delete-open"
            className="shrink-0 self-start sm:self-center"
            onClick={() => {
              setDeleteInitialError(null);
              setDeleteOpen(true);
            }}
          >
            {t("deleteAccountButton")}
          </ActionButton>
        </div>
      </div>

      {deleteOpen && (
        <DeleteAccountDialog
          open
          user={user}
          label={label}
          confirmed={deleteConfirmed}
          setConfirmed={setDeleteConfirmed}
          initialError={deleteInitialError}
          onClose={() => {
            setDeleteOpen(false);
            setDeleteInitialError(null);
          }}
        />
      )}
    </section>
  );
}

export function RoleBadge({ role }: { role: string }) {
  const t = useTranslations("account");
  const admin = role === "admin";
  return (
    <span
      data-testid="role-badge"
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-bold ${
        admin ? "bg-teal-container text-teal-dim" : "bg-surface-container-high text-on-surface-variant"
      }`}
    >
      {admin ? t("roleAdmin") : t("roleUser")}
    </span>
  );
}

function Row({
  title,
  hint,
  children,
  testId,
}: {
  title: React.ReactNode;
  hint?: React.ReactNode;
  children?: React.ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="flex flex-col gap-3 rounded-lg bg-surface-container p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="min-w-0">
        <h3 className="font-semibold text-on-surface">{title}</h3>
        {hint && <div className="text-[13px] text-on-surface-variant">{hint}</div>}
      </div>
      {children && <div className="shrink-0 self-start sm:self-center">{children}</div>}
    </div>
  );
}

// --- password ---------------------------------------------------------------

function PasswordRow({ user, label }: { user: CurrentUser; label: string }) {
  const t = useTranslations("account");
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<{ field: "current" | "new"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [changed, setChanged] = useState(false);

  if (!user.has_password) {
    return <Row testId="account-password-row" title={t("passwordTitle")} hint={t("noPasswordHint", { label })} />;
  }

  const reset = () => {
    setCurrent("");
    setNext("");
    setRepeat("");
    setError(null);
  };

  async function save() {
    setChanged(false);
    if (next.length < PASSWORD_MIN) return setError({ field: "new", text: t("errorPasswordTooShort") });
    if (next.length > PASSWORD_MAX) return setError({ field: "new", text: t("errorPasswordTooLong") });
    if (next !== repeat) return setError({ field: "new", text: t("errorPasswordMismatch") });
    if (next.trim().toLowerCase() === user.email.trim().toLowerCase()) {
      return setError({ field: "new", text: t("errorPasswordIsEmail") });
    }
    setSaving(true);
    setError(null);
    try {
      const res = await changePassword(current, next);
      if (res.ok) {
        reset();
        setOpen(false);
        setChanged(true);
        return;
      }
      const code = await readErrorCode(res);
      if (code === "invalid_credentials") setError({ field: "current", text: t("errorWrongCurrent") });
      else if (code === "password_policy" || res.status === 422)
        setError({ field: "new", text: t("errorPasswordIsEmail") });
      else setError({ field: "new", text: t("errorGeneric") });
    } catch {
      setError({ field: "new", text: t("errorGeneric") });
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <Row
        testId="account-password-row"
        title={t("passwordTitle")}
        hint={
          <>
            {t("passwordSetHint")}
            {changed && (
              <span data-testid="account-password-changed" className="mt-1 flex items-center gap-1.5 text-success">
                <CircleCheck className="h-4 w-4" aria-hidden />
                {t("passwordChanged")}
              </span>
            )}
          </>
        }
      >
        <ActionButton data-testid="account-password-open" onClick={() => setOpen(true)}>
          {t("changePassword")}
        </ActionButton>
      </Row>
    );
  }

  const field = "w-full rounded-lg border bg-white px-3 py-2 text-[14px] text-on-surface outline-none focus:border-teal focus:ring-2 focus:ring-teal/20";

  return (
    <form
      data-testid="account-password-form"
      className="rounded-lg bg-surface-container p-4"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <h3 className="mb-2 font-semibold text-on-surface">{t("changePassword")}</h3>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-[13px] text-on-surface">
          {t("currentPassword")}
          <input
            type="password"
            autoComplete="current-password"
            data-testid="account-password-current"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            aria-invalid={error?.field === "current"}
            className={`${field} ${error?.field === "current" ? "border-critical" : "border-outline-variant"}`}
          />
        </label>
        <label className="flex flex-col gap-1 text-[13px] text-on-surface">
          {t("newPassword")}
          <input
            type="password"
            autoComplete="new-password"
            data-testid="account-password-new"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            aria-invalid={error?.field === "new"}
            className={`${field} ${error?.field === "new" ? "border-critical" : "border-outline-variant"}`}
          />
        </label>
        <label className="flex flex-col gap-1 text-[13px] text-on-surface">
          {t("repeatPassword")}
          <input
            type="password"
            autoComplete="new-password"
            data-testid="account-password-repeat"
            value={repeat}
            onChange={(e) => setRepeat(e.target.value)}
            className={`${field} border-outline-variant`}
          />
        </label>
      </div>
      {error && (
        <p role="alert" data-testid="account-password-error" className="mt-2 text-[12.5px] text-critical">
          {error.text}
        </p>
      )}
      <p className="mt-2 text-[12px] text-on-surface-variant">{t("passwordPolicyHint")}</p>
      <div className="mt-3 flex justify-end gap-2">
        <ActionButton
          onClick={() => {
            reset();
            setOpen(false);
          }}
        >
          {t("cancel")}
        </ActionButton>
        <ActionButton
          tone="primary"
          type="submit"
          data-testid="account-password-save"
          disabled={saving || !current || !next || !repeat}
        >
          {t("savePassword")}
        </ActionButton>
      </div>
    </form>
  );
}

// --- SSO --------------------------------------------------------------------

function SsoRow({
  user,
  label,
  notice,
  noticeText,
  setNotice,
  unlinkConfirmed,
  setUnlinkConfirmed,
  onChanged,
}: {
  user: CurrentUser;
  label: string;
  notice: Notice;
  noticeText: string;
  setNotice: (n: Notice) => void;
  unlinkConfirmed: boolean;
  setUnlinkConfirmed: (v: boolean) => void;
  onChanged: () => Promise<void>;
}) {
  const t = useTranslations("account");
  const [busy, setBusy] = useState(false);
  const [needsReauth, setNeedsReauth] = useState(false);

  const link = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      rememberOidcIntent("link");
      const res = await startOidcLink();
      if (res.ok && (await followAuthorizeUrl(res))) return;
      setNotice({ tone: "error", text: res.status === 503 ? "unavailable" : "link" });
    } catch {
      setNotice({ tone: "error", text: "unavailable" });
    }
    setBusy(false);
  }, [setNotice]);

  const reauth = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      rememberOidcIntent("oidc.unlink");
      const res = await startReauth("oidc.unlink", user.id);
      if (res.ok && (await followAuthorizeUrl(res))) return;
      setNotice({ tone: "error", text: res.status === 503 ? "unavailable" : "generic" });
    } catch {
      setNotice({ tone: "error", text: "unavailable" });
    }
    setBusy(false);
  }, [setNotice, user.id]);

  const unlink = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const res = await unlinkOidc();
      if (res.ok) {
        setUnlinkConfirmed(false);
        setNeedsReauth(false);
        setNotice({ tone: "ok", text: "unlinked" });
        await onChanged();
      } else {
        const code = await readErrorCode(res);
        if (code === "reauth_required") {
          setUnlinkConfirmed(false);
          setNeedsReauth(true);
        } else if (code === "last_credential") {
          setNotice({ tone: "error", text: "lastCredential" });
        } else {
          setNotice({ tone: "error", text: "generic" });
        }
      }
    } catch {
      setNotice({ tone: "error", text: "generic" });
    }
    setBusy(false);
  }, [onChanged, setNotice, setUnlinkConfirmed]);

  const hint = user.oidc_linked ? (
    <span className="flex items-center gap-1.5">
      <CircleCheck className="h-4 w-4 shrink-0 text-success" aria-hidden />
      {t("ssoLinked", { label })}
    </span>
  ) : (
    t("ssoNotLinked", { label })
  );

  // Unlinking is offered only while a password exists — without one the binding
  // is the account's only way in (409 last_credential; mock §3).
  const button = user.oidc_linked ? (
    user.has_password ? (
      <ActionButton data-testid="account-sso-unlink" disabled={busy} onClick={() => void unlink()}>
        {t("ssoUnlinkButton")}
      </ActionButton>
    ) : null
  ) : (
    <ActionButton data-testid="account-sso-link" disabled={busy} onClick={() => void link()}>
      <Link2 className="h-4 w-4" aria-hidden />
      {t("ssoLinkButton", { label })}
    </ActionButton>
  );

  return (
    <div data-testid="account-sso-row" className="rounded-lg bg-surface-container p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h3 className="font-semibold text-on-surface">{t("ssoTitle", { label })}</h3>
          <div className="text-[13px] text-on-surface-variant">{hint}</div>
        </div>
        {button && <div className="shrink-0 self-start sm:self-center">{button}</div>}
      </div>
      {unlinkConfirmed && user.oidc_linked && (
        <DialogSuccess testId="account-sso-confirmed">{t("unlinkOidcConfirmed", { label })}</DialogSuccess>
      )}
      {needsReauth && (
        <div data-testid="account-sso-reauth" className="mt-3 flex flex-col gap-2 rounded-lg bg-white p-3 text-[13px] sm:flex-row sm:items-center sm:justify-between">
          <span className="text-on-surface-variant">{t("unlinkConfirmPrompt", { label })}</span>
          <ActionButton tone="primary" disabled={busy} onClick={() => void reauth()} className="shrink-0">
            {t("deleteContinueOidc", { label })}
          </ActionButton>
        </div>
      )}
      {notice &&
        (notice.tone === "ok" ? (
          <DialogSuccess testId="account-sso-notice">{noticeText}</DialogSuccess>
        ) : (
          <DialogError testId="account-sso-notice">{noticeText}</DialogError>
        ))}
    </div>
  );
}

// --- self-delete ------------------------------------------------------------

function DeleteAccountDialog({
  open,
  user,
  label,
  confirmed,
  setConfirmed,
  initialError,
  onClose,
}: {
  open: boolean;
  user: CurrentUser;
  label: string;
  confirmed: boolean;
  setConfirmed: (v: boolean) => void;
  initialError: string | null;
  onClose: () => void;
}) {
  const t = useTranslations("account");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  // Mounted only while open (see AccountCard), so the initial state IS the reset.
  const [error, setError] = useState<string | null>(
    initialError === "reauth" ? t("errorReauthFailed", { label }) : null,
  );
  const [lastAdmin, setLastAdmin] = useState(false);

  const close = () => {
    setPassword("");
    setError(null);
    setLastAdmin(false);
    onClose();
  };

  async function erase() {
    setBusy(true);
    setError(null);
    try {
      const res = await deleteMyAccount(user.has_password ? password : undefined);
      if (res.ok) {
        // The cookie is cleared by the response; the next request would 401 —
        // go to the sign-in page directly instead of through the 401 redirect.
        suppressAuthRedirect(true);
        clearUserBrowserState();
        window.location.assign("/login?signed_out=1");
        return;
      }
      const code = await readErrorCode(res);
      if (code === "last_admin") setLastAdmin(true);
      else if (code === "invalid_credentials") setError(t("errorWrongPassword"));
      else if (code === "reauth_required") {
        setConfirmed(false);
        setError(t("errorReauthFailed", { label }));
      } else setError(t("errorGeneric"));
    } catch {
      setError(t("errorGeneric"));
    }
    setBusy(false);
  }

  async function continueToIdp() {
    setBusy(true);
    setError(null);
    try {
      rememberOidcIntent("account.delete");
      const res = await startReauth("account.delete", user.id);
      if (res.ok && (await followAuthorizeUrl(res))) return;
      setError(res.status === 503 ? t("errorOidcUnavailable", { label }) : t("errorGeneric"));
    } catch {
      setError(t("errorOidcUnavailable", { label }));
    }
    setBusy(false);
  }

  if (lastAdmin) {
    return (
      <Dialog
        open={open}
        onClose={close}
        title={t("deleteDialogTitle")}
        testId="account-delete-dialog"
        actions={<ActionButton onClick={close}>{t("close")}</ActionButton>}
      >
        <DialogError testId="account-delete-last-admin">{t("errorLastAdmin")}</DialogError>
      </Dialog>
    );
  }

  const oidcOnly = !user.has_password;

  return (
    <Dialog
      open={open}
      onClose={close}
      title={t("deleteDialogTitle")}
      testId="account-delete-dialog"
      actions={
        <>
          <ActionButton onClick={close}>{t("cancel")}</ActionButton>
          {oidcOnly && !confirmed ? (
            <ActionButton
              tone="primary"
              data-testid="account-delete-continue-oidc"
              disabled={busy}
              onClick={() => void continueToIdp()}
            >
              {t("deleteContinueOidc", { label })}
            </ActionButton>
          ) : (
            <ActionButton
              tone="danger"
              data-testid="account-delete-confirm"
              disabled={busy || (!oidcOnly && !password)}
              onClick={() => void erase()}
            >
              {t("deleteConfirmButton")}
            </ActionButton>
          )}
        </>
      }
    >
      <p>{t("deleteDialogBody")}</p>
      <ul className="mt-2 list-disc space-y-0.5 pl-5">
        <li>{t("deleteItemProfile")}</li>
        <li>{t("deleteItemApplications")}</li>
        <li>{t("deleteItemTokens")}</li>
        <li>{t("deleteItemAccount")}</li>
      </ul>
      {!oidcOnly && (
        <label className="mt-4 flex flex-col gap-1.5 text-[13px] text-on-surface">
          {t("deleteConfirmPassword")}
          <input
            type="password"
            autoComplete="current-password"
            data-testid="account-delete-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-[14px] outline-none focus:border-teal focus:ring-2 focus:ring-teal/20"
          />
        </label>
      )}
      {oidcOnly && !confirmed && (
        <div className="mt-4 rounded-lg bg-surface-container px-3 py-2.5 text-[13px] text-on-surface">
          {t("deleteConfirmOidc", { label })}
        </div>
      )}
      {oidcOnly && confirmed && (
        <DialogSuccess testId="account-delete-confirmed">{t("deleteOidcConfirmed", { label })}</DialogSuccess>
      )}
      {error && <DialogError testId="account-delete-error">{error}</DialogError>}
    </Dialog>
  );
}
