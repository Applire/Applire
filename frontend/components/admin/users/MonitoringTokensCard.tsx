"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Administration → Monitoring → "Monitoring-Tokens" (US329 UI, S-16; mock
 * tokens.html §7). A probe token reads `/api/ops/health` and nothing else.
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Plus } from "lucide-react";

import {
  createProbeToken,
  listProbeTokens,
  revokeProbeToken,
  type TokenCreated,
  type TokenItem,
} from "@/components/account/api";
import { CopyField } from "@/components/account/CopyField";
import {
  CreateTokenDialog,
  maskedToken,
  probeSnippet,
  RevokeDialog,
  ShownOnceDialog,
  TokenTable,
} from "@/components/account/tokens";

export function MonitoringTokensCard() {
  const t = useTranslations("adminUsers");
  const tAccount = useTranslations("account");
  const [tokens, setTokens] = useState<TokenItem[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<TokenCreated | null>(null);
  const [revoking, setRevoking] = useState<TokenItem | null>(null);

  const load = useCallback(async () => {
    const list = await listProbeTokens();
    setLoadFailed(list === null);
    if (list) setTokens(list);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section data-testid="monitoring-tokens" className="rounded-xl bg-white p-6 shadow-soft">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="font-heading text-xl font-bold text-on-surface">{t("monitoringTitle")}</h2>
          <p className="mt-1 text-[14px] text-on-surface-variant">{t("monitoringIntro")}</p>
        </div>
        <button
          type="button"
          data-testid="monitoring-create"
          onClick={() => setCreating(true)}
          className="inline-flex shrink-0 items-center gap-2 self-start rounded-lg bg-primary px-4 py-2.5 text-[13.5px] font-semibold text-white hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" aria-hidden />
          {t("createMonitoringToken")}
        </button>
      </div>

      {loadFailed && (
        <p role="alert" className="mt-3 text-[13px] text-critical">
          {tAccount("tokensLoadFailed")}
        </p>
      )}
      {tokens !== null && <TokenTable tokens={tokens} onRevoke={setRevoking} testId="monitoring-table" />}
      {tokens !== null && tokens.length > 0 && (
        <div className="mt-4">
          <p className="text-[13px] text-on-surface">{t("monitoringSetupHint")}</p>
          <CopyField
            tone="code"
            value={probeSnippet(maskedToken(tokens[0].prefix))}
            copyLabel={tAccount("copy")}
            copiedLabel={tAccount("copied")}
          />
        </div>
      )}

      <CreateTokenDialog
        open={creating}
        title={t("createMonitoringToken")}
        placeholder={t("monitoringNamePlaceholder")}
        testId="monitoring-create-dialog"
        onClose={() => setCreating(false)}
        onCreate={async (name) => {
          try {
            const res = await createProbeToken(name);
            if (!res.ok) return { error: tAccount("errorGeneric") };
            const body = (await res.json()) as TokenCreated;
            setCreating(false);
            setCreated(body);
            void load();
            return body;
          } catch {
            return { error: tAccount("errorGeneric") };
          }
        }}
      />

      <ShownOnceDialog
        token={created?.token ?? null}
        testId="monitoring-shown-once"
        setupHint={t("monitoringSetupHint")}
        snippet={created ? probeSnippet(created.token) : ""}
        onDone={() => setCreated(null)}
      />

      <RevokeDialog
        token={revoking}
        testId="monitoring-revoke-dialog"
        body={t("revokeMonitoringBody")}
        onClose={() => setRevoking(null)}
        onConfirm={async (tok) => {
          try {
            const res = await revokeProbeToken(tok.id);
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
