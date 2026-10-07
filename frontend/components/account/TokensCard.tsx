"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Settings → "Tokens für Agenten und Skripte" (US327 UI, RD-10; mock tokens.html
 * §1–§6). Two lists from one `GET /api/me/tokens`, split by scope: agent tokens
 * (stdio MCP) and API tokens (REST scripts).
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";

import { createMyToken, listMyTokens, revokeMyToken, type PersonalTokenScope, type TokenCreated, type TokenItem } from "./api";
import { ActionButton } from "./Dialog";
import { agentSnippet, apiSnippet, CreateTokenDialog, RevokeDialog, ShownOnceDialog, TokenTable } from "./tokens";

export function TokensCard() {
  const t = useTranslations("account");
  const [tokens, setTokens] = useState<TokenItem[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [creating, setCreating] = useState<PersonalTokenScope | null>(null);
  const [created, setCreated] = useState<TokenCreated | null>(null);
  const [revoking, setRevoking] = useState<TokenItem | null>(null);

  const load = useCallback(async () => {
    const list = await listMyTokens();
    setLoadFailed(list === null);
    if (list) setTokens(list);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const agent = (tokens ?? []).filter((tok) => tok.scope === "agent");
  const api = (tokens ?? []).filter((tok) => tok.scope === "api");

  const section = (scope: PersonalTokenScope, list: TokenItem[]) => (
    <div data-testid={`tokens-${scope}`} className="rounded-lg border border-outline-variant p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h3 className="font-semibold text-on-surface">{scope === "agent" ? t("agentSection") : t("apiSection")}</h3>
          <p className="text-[13px] text-on-surface-variant">
            {scope === "agent" ? t("agentSectionHint") : t("apiSectionHint")}
          </p>
        </div>
        <ActionButton
          data-testid={`tokens-${scope}-create`}
          className="shrink-0 self-start"
          onClick={() => setCreating(scope)}
        >
          <Plus className="h-4 w-4" aria-hidden />
          {scope === "agent" ? t("createAgentToken") : t("createApiToken")}
        </ActionButton>
      </div>
      {tokens !== null && <TokenTable tokens={list} onRevoke={setRevoking} testId={`tokens-${scope}-table`} />}
    </div>
  );

  return (
    <section data-testid="tokens-card" className="rounded-xl bg-white p-6 shadow-soft">
      <h2 className="font-heading text-xl font-bold text-on-surface">{t("tokensTitle")}</h2>
      <p className="mt-1 text-[14px] text-on-surface-variant">{t("tokensIntro")}</p>
      {loadFailed && (
        <p role="alert" className="mt-3 text-[13px] text-critical">
          {t("tokensLoadFailed")}
        </p>
      )}
      <div className="mt-4 flex flex-col gap-3">
        {section("agent", agent)}
        {section("api", api)}
      </div>

      <CreateTokenDialog
        open={creating !== null}
        title={creating === "api" ? t("createDialogTitleApi") : t("createDialogTitleAgent")}
        placeholder={t("tokenNamePlaceholder")}
        testId="tokens-create-dialog"
        onClose={() => setCreating(null)}
        onCreate={async (name) => {
          if (!creating) return { error: t("errorGeneric") };
          try {
            const res = await createMyToken(name, creating);
            if (!res.ok) return { error: t("errorGeneric") };
            const body = (await res.json()) as TokenCreated;
            setCreating(null);
            setCreated(body);
            void load();
            return body;
          } catch {
            return { error: t("errorGeneric") };
          }
        }}
      />

      <ShownOnceDialog
        token={created?.token ?? null}
        testId="tokens-shown-once"
        setupHint={created?.scope === "api" ? t("apiSetupHint") : t("agentSetupHint")}
        snippet={created ? (created.scope === "api" ? apiSnippet(created.token) : agentSnippet(created.token)) : ""}
        after={created?.scope === "agent" ? t("agentSetupRestart") : undefined}
        onDone={() => setCreated(null)}
      />

      <RevokeDialog
        token={revoking}
        testId="tokens-revoke-dialog"
        body={revoking?.scope === "api" ? t("revokeApiBody") : t("revokeAgentBody")}
        onClose={() => setRevoking(null)}
        onConfirm={async (tok) => {
          try {
            const res = await revokeMyToken(tok.id);
            if (!res.ok && res.status !== 404) return false;
            await load();
            return true;
          } catch {
            return false;
          }
        }}
      />
    </section>
  );
}
