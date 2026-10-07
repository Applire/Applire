"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Personen (US324/US326 UI). Lists people, adds/invites, and runs
 * the per-row actions; every mutation re-reads the list. `409 last_admin` is shown
 * in the action's own dialog (mock §12); the own row's "Löschen" goes to Settings,
 * where self-delete asks for the password / an SSO re-auth (G-2).
 */

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { UserPlus } from "lucide-react";

import { readErrorCode, useCurrentUser } from "@/lib/auth";
import {
  createUser,
  deleteUser,
  listUsers,
  patchUser,
  reinvite,
  resetLink,
  revokeTokens,
  type AdminUser,
  type IssuedLink,
} from "./api";
import { AddPersonDialog, ConfirmDialog, DeleteUserDialog, LinkDialog } from "./UserDialogs";
import { UsersTable, type UserAction } from "./UsersTable";

type Confirm = { action: "makeAdmin" | "disable" | "revokeTokens" | "makeUser"; user: AdminUser };

export function UsersAdmin() {
  const t = useTranslations("adminUsers");
  const router = useRouter();
  const { user: me, authState } = useCurrentUser();

  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [adding, setAdding] = useState(false);
  const [issued, setIssued] = useState<{ link: IssuedLink; email: string } | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<AdminUser | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const list = await listUsers();
    setLoadFailed(list === null);
    if (list) setUsers(list);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** One mapping from a failed response to copy, shared by every action. */
  const failure = useCallback(
    async (res: Response, email: string): Promise<string> => {
      const code = await readErrorCode(res);
      if (code === "last_admin") return t("errorLastAdmin", { email });
      if (code === "email_taken") return t("errorEmailTaken");
      return t("errorGeneric");
    },
    [t],
  );

  async function issue(fn: (id: string) => Promise<Response>, u: AdminUser) {
    try {
      const res = await fn(u.id);
      if (res.ok) {
        setIssued({ link: (await res.json()) as IssuedLink, email: u.email });
      } else {
        setConfirm({ action: "makeUser", user: u });
        setConfirmError(await failure(res, u.email));
      }
    } catch {
      setConfirm({ action: "makeUser", user: u });
      setConfirmError(t("errorGeneric"));
    }
    void load();
  }

  async function patch(u: AdminUser, body: { role?: "admin" | "user"; disabled?: boolean }): Promise<string | null> {
    try {
      const res = await patchUser(u.id, body);
      if (res.ok) return null;
      return await failure(res, u.email);
    } catch {
      return t("errorGeneric");
    }
  }

  function onAction(action: UserAction, u: AdminUser) {
    setConfirmError(null);
    switch (action) {
      case "reinvite":
        void issue(reinvite, u);
        return;
      case "resetLink":
        void issue(resetLink, u);
        return;
      case "enable":
        void patch(u, { disabled: false }).then((err) => {
          if (err) {
            setConfirm({ action: "makeUser", user: u });
            setConfirmError(err);
          }
          void load();
        });
        return;
      case "makeUser":
        // No confirmation in the mock — but a refusal (last admin) still needs a place.
        void patch(u, { role: "user" }).then((err) => {
          if (err) {
            setConfirm({ action: "makeUser", user: u });
            setConfirmError(err);
          }
          void load();
        });
        return;
      case "delete":
        if (me && u.id === me.id) {
          router.push("/settings");
          return;
        }
        setDeleteError(null);
        setDeleting(u);
        return;
      default:
        setConfirm({ action, user: u });
    }
  }

  async function runConfirm() {
    if (!confirm) return;
    const { action, user: u } = confirm;
    setBusy(true);
    let err: string | null = null;
    if (action === "makeAdmin") err = await patch(u, { role: "admin" });
    else if (action === "disable") err = await patch(u, { disabled: true });
    else if (action === "revokeTokens") {
      try {
        const res = await revokeTokens(u.id);
        err = res.ok ? null : await failure(res, u.email);
      } catch {
        err = t("errorGeneric");
      }
    }
    setBusy(false);
    if (err) setConfirmError(err);
    else setConfirm(null);
    void load();
  }

  async function runDelete() {
    if (!deleting) return;
    setBusy(true);
    try {
      const res = await deleteUser(deleting.id);
      if (res.ok) setDeleting(null);
      else setDeleteError(await failure(res, deleting.email));
    } catch {
      setDeleteError(t("errorGeneric"));
    }
    setBusy(false);
    void load();
  }

  const confirmCopy = (c: Confirm) => {
    const email = c.user.email;
    switch (c.action) {
      case "makeAdmin":
        return { title: t("makeAdminTitle", { email }), body: t("makeAdminBody"), label: t("makeAdminConfirm"), danger: false };
      case "disable":
        return { title: t("disableTitle", { email }), body: t("disableBody"), label: t("disableConfirm"), danger: true };
      case "revokeTokens":
        return { title: t("revokeTokensTitle", { email }), body: t("revokeTokensBody"), label: t("revokeTokensConfirm"), danger: true };
      default:
        // makeUser / enable / link refusals: no confirmation of their own — the
        // dialog only ever shows the error, titled with the person's e-mail.
        return { title: email, body: "", label: "", danger: false };
    }
  };
  const copy = confirm ? confirmCopy(confirm) : null;

  return (
    <div data-testid="admin-users" className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <p className="text-[14px] text-on-surface-variant">{t("intro")}</p>
        <button
          type="button"
          data-testid="admin-add-open"
          onClick={() => setAdding(true)}
          className="inline-flex shrink-0 items-center gap-2 self-start rounded-lg bg-primary px-4 py-2.5 text-[13.5px] font-semibold text-white hover:bg-primary/90 sm:self-auto"
        >
          <UserPlus className="h-4 w-4" aria-hidden />
          {t("addPerson")}
        </button>
      </div>

      {loadFailed && (
        <p role="alert" className="text-[13px] text-critical">
          {t("loadFailed")}
        </p>
      )}
      {users && <UsersTable users={users} meId={me?.id ?? null} onAction={onAction} />}

      {adding && (
        <AddPersonDialog
          open
          smtpEnabled={Boolean(authState?.smtp_enabled)}
          onClose={() => setAdding(false)}
          onSubmit={async (email, role, sendMail) => {
            try {
              const res = await createUser(email, role, sendMail);
              if (!res.ok) return await failure(res, email);
              const body = (await res.json()) as { user: AdminUser; link: IssuedLink };
              setAdding(false);
              setIssued({ link: body.link, email: body.user.email });
              void load();
              return null;
            } catch {
              return t("errorGeneric");
            }
          }}
        />
      )}

      <LinkDialog link={issued?.link ?? null} email={issued?.email ?? ""} onClose={() => setIssued(null)} />

      <ConfirmDialog
        open={confirm !== null}
        testId="admin-confirm-dialog"
        title={copy?.title ?? ""}
        body={copy?.body}
        confirmLabel={copy?.label}
        danger={copy?.danger}
        error={confirmError}
        busy={busy}
        onClose={() => {
          setConfirm(null);
          setConfirmError(null);
        }}
        onConfirm={() => void runConfirm()}
      />

      {deleting && (
        <DeleteUserDialog
          key={deleting.id}
          email={deleting.email}
          error={deleteError}
          busy={busy}
          onClose={() => {
            setDeleting(null);
            setDeleteError(null);
          }}
          onConfirm={() => void runDelete()}
        />
      )}
    </div>
  );
}
