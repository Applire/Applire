"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Token building blocks shared by Settings → "Tokens für Agenten und Skripte"
 * (agent + api, US327/RD-10) and Administration → Monitoring (probe tokens,
 * US329/S-16): the list, the create dialog, the shown-once dialog with its setup
 * snippet, and the revoke confirmation (W0-B mock tokens.html §1–§7).
 *
 * The secret exists in the browser exactly once — in the create response — and
 * lives only in the shown-once dialog's state; closing the dialog drops it. The
 * list shows the 8-character prefix the server returns, never more.
 */

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Eye } from "lucide-react";

import type { TokenCreated, TokenItem } from "./api";
import { CopyField } from "./CopyField";
import { ActionButton, Dialog, DialogError } from "./Dialog";
import { formatDate, formatWhen } from "./format";

/** `apl_<prefix>_…` — what the list and the monitoring hint show of a token. */
export function maskedToken(prefix: string): string {
  return `apl_${prefix}_…`;
}

export function origin(): string {
  return typeof window === "undefined" ? "" : window.location.origin;
}

/** README's `mcpServers` block + the token (ADR-091 cl. 17: `-e APPLIRE_AGENT_TOKEN` + env). */
export function agentSnippet(token: string): string {
  return [
    "{",
    '  "mcpServers": {',
    '    "applire": {',
    '      "command": "docker",',
    '      "args": [',
    '        "compose", "-f", "/absolute/path/to/applire/docker-compose.yml",',
    '        "run", "--rm", "-T", "-e", "APPLIRE_AGENT_TOKEN", "mcp"',
    "      ],",
    `      "env": { "APPLIRE_AGENT_TOKEN": "${token}" }`,
    "    }",
    "  }",
    "}",
  ].join("\n");
}

export function apiSnippet(token: string): string {
  return `curl -H "Authorization: Bearer ${token}" \\\n     ${origin()}/api/applications`;
}

export function probeSnippet(token: string): string {
  return `curl -H "Authorization: Bearer ${token}" ${origin()}/api/ops/health`;
}

export function TokenTable({
  tokens,
  onRevoke,
  testId,
}: {
  tokens: TokenItem[];
  onRevoke: (token: TokenItem) => void;
  testId: string;
}) {
  const t = useTranslations("account");
  const locale = useLocale();
  const tAccount = (key: string, values?: Record<string, string | number>) => t(key, values);

  if (tokens.length === 0) {
    return (
      <p data-testid={`${testId}-empty`} className="mt-2 text-[13px] text-on-surface-variant">
        {t("tokensEmpty")}
      </p>
    );
  }

  return (
    <div className="mt-3 overflow-x-auto">
      <table data-testid={testId} className="w-full min-w-[480px] text-left text-[13px]">
        <thead>
          <tr className="border-b border-outline-variant text-[11px] font-bold uppercase tracking-wide text-on-surface-variant">
            <th className="px-2 py-2">{t("colName")}</th>
            <th className="px-2 py-2">{t("colCreated")}</th>
            <th className="px-2 py-2">{t("colLastUsed")}</th>
            <th className="px-2 py-2" />
          </tr>
        </thead>
        <tbody>
          {tokens.map((tok) => (
            <tr key={tok.id} data-testid={`${testId}-row`} className="border-b border-outline-variant/60 last:border-0">
              <td className="px-2 py-2.5">
                <span className="mr-1.5 font-semibold text-on-surface">{tok.name}</span>
                <code className="font-mono text-[11px] text-on-surface-variant">{maskedToken(tok.prefix)}</code>
              </td>
              <td className="whitespace-nowrap px-2 py-2.5 text-on-surface">{formatDate(tok.created_at, locale)}</td>
              <td className="whitespace-nowrap px-2 py-2.5 text-on-surface">
                {tok.last_used_at ? formatWhen(tok.last_used_at, locale, tAccount) : t("neverUsed")}
              </td>
              <td className="px-2 py-2.5 text-right">
                <button
                  type="button"
                  data-testid={`${testId}-revoke`}
                  onClick={() => onRevoke(tok)}
                  className="rounded-md border border-critical/30 bg-white px-2.5 py-1 text-[12px] font-semibold text-critical hover:bg-critical-container"
                >
                  {t("revoke")}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CreateTokenDialog({
  open,
  title,
  placeholder,
  onClose,
  onCreate,
  testId,
}: {
  open: boolean;
  title: string;
  placeholder: string;
  onClose: () => void;
  /** Resolves with the created token, or a message to show. */
  onCreate: (name: string) => Promise<TokenCreated | { error: string }>;
  testId: string;
}) {
  const t = useTranslations("account");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setName("");
    setError(null);
    onClose();
  };

  async function submit() {
    const trimmed = name.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    const result = await onCreate(trimmed);
    setBusy(false);
    if ("error" in result) setError(result.error);
    else setName("");
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={title}
      testId={testId}
      actions={
        <>
          <ActionButton onClick={close}>{t("cancel")}</ActionButton>
          <ActionButton
            tone="primary"
            data-testid={`${testId}-submit`}
            disabled={busy || !name.trim()}
            onClick={() => void submit()}
          >
            {t("createButton")}
          </ActionButton>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <label className="flex flex-col gap-1.5 text-[13px] font-semibold text-on-surface">
          {t("tokenNameLabel")}
          <input
            type="text"
            maxLength={80}
            data-testid={`${testId}-name`}
            value={name}
            placeholder={placeholder}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-lg border border-outline-variant bg-white px-3 py-2 text-[14px] font-normal outline-none focus:border-teal focus:ring-2 focus:ring-teal/20"
          />
        </label>
      </form>
      {error && <DialogError>{error}</DialogError>}
    </Dialog>
  );
}

export function ShownOnceDialog({
  token,
  setupHint,
  snippet,
  after,
  onDone,
  testId,
}: {
  token: string | null;
  setupHint: string;
  snippet: string;
  after?: string;
  onDone: () => void;
  testId: string;
}) {
  const t = useTranslations("account");
  return (
    <Dialog
      open={token !== null}
      onClose={onDone}
      title={t("shownOnceTitle")}
      testId={testId}
      wide
      actions={
        <ActionButton tone="primary" data-testid={`${testId}-done`} onClick={onDone}>
          {t("done")}
        </ActionButton>
      }
    >
      <div className="flex items-center gap-2 rounded-lg border border-warning/50 bg-warning-container px-3 py-2 text-[13px] text-on-surface">
        <Eye className="h-4 w-4 shrink-0" aria-hidden />
        {t("shownOnceWarning")}
      </div>
      {token && (
        <>
          <CopyField
            value={token}
            tone="secret"
            copyLabel={t("copy")}
            copiedLabel={t("copied")}
            testId={`${testId}-secret`}
          />
          <p className="mt-3 text-on-surface">{setupHint}</p>
          <CopyField value={snippet} tone="code" copyLabel={t("copy")} copiedLabel={t("copied")} />
          {after && <p className="mt-2 text-on-surface">{after}</p>}
        </>
      )}
    </Dialog>
  );
}

export function RevokeDialog({
  token,
  body,
  onClose,
  onConfirm,
  testId,
}: {
  token: TokenItem | null;
  body: string;
  onClose: () => void;
  onConfirm: (token: TokenItem) => Promise<boolean>;
  testId: string;
}) {
  const t = useTranslations("account");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const close = () => {
    setFailed(false);
    onClose();
  };

  return (
    <Dialog
      open={token !== null}
      onClose={close}
      title={token ? t("revokeAgentTitle", { name: token.name }) : undefined}
      testId={testId}
      actions={
        <>
          <ActionButton onClick={close}>{t("cancel")}</ActionButton>
          <ActionButton
            tone="danger"
            data-testid={`${testId}-confirm`}
            disabled={busy}
            onClick={async () => {
              if (!token) return;
              setBusy(true);
              setFailed(false);
              const ok = await onConfirm(token);
              setBusy(false);
              if (ok) close();
              else setFailed(true);
            }}
          >
            {t("revokeConfirm")}
          </ActionButton>
        </>
      }
    >
      <p>{body}</p>
      {failed && <DialogError>{t("errorGeneric")}</DialogError>}
    </Dialog>
  );
}
