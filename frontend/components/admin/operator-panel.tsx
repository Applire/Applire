"use client";

// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * OperatorPanel — instance health summary for the self-hosting operator (E060 / US312).
 *
 * Lives on the **admin page**, not the dashboard (founder ruling O1-4,
 * 2026-09-09: that page "is the correct place to show this kind of settings; in
 * the admin release access to this page is tied to a user right"). The
 * dashboard is the candidate's pipeline; this is the operator's instance.
 *
 * Reads `GET /api/ops/health` once on mount (no polling — the backend's own
 * refresher keeps the cache warm; ADR-086 clause 9) and renders the aggregated
 * verdict `aggregate.collect` produces: one line when everything is fine,
 * expanded and attention-coloured the moment anything is not. Plain language
 * throughout (COPY.md, Documents/Runs/Nougat/build-1/o1/COPY.md) — the reader
 * is a person running `docker compose`, not a Kubernetes SRE.
 *
 * Kept self-contained and collapsible so a later ruling can reuse it for a
 * dashboard one-liner that only appears while the instance is degraded.
 *
 * `usage.by_document` / `usage.by_application` are deliberately not rendered
 * yet — their ids are opaque UUIDs and there is nothing human-readable to
 * show without a document/application lookup this version does not have.
 */

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ??
  (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

type Translator = ReturnType<typeof useTranslations>;

type OverallStatus = "ok" | "degraded" | "down";
type ComponentStatus = "ok" | "degraded" | "down" | "unknown";

type ComponentName =
  | "database"
  | "migrations"
  | "retention"
  | "disk"
  | "backup"
  | "provider"
  | "errors";

// Fixed iteration order (COPY.md / spec) — components not present in the
// response are simply skipped, never treated as an error.
const COMPONENT_ORDER: ComponentName[] = [
  "database",
  "migrations",
  "retention",
  "disk",
  "backup",
  "provider",
  "errors",
];

interface ComponentReport {
  status: ComponentStatus;
  message: string;
  detail: Record<string, unknown>;
}

interface UsageBucket {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  estimated_calls: number;
  fully_measured: boolean;
}

interface UsageReport {
  today?: UsageBucket;
  window_days?: number;
  window?: UsageBucket;
  by_document?: unknown[];
  by_application?: unknown[];
}

interface OpsHealthReport {
  status: OverallStatus;
  edition: string;
  version: string;
  llm_provider: string;
  checked_at: string;
  components: Partial<Record<ComponentName, ComponentReport>>;
  usage: UsageReport;
}

function formatGB(bytes: number): string {
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

/** The fact line beside a component row, or null when the status word alone says it all. */
function factLine(name: ComponentName, c: ComponentReport, t: Translator): string | null {
  const d = c.detail ?? {};
  switch (name) {
    case "disk":
      return t("diskFree", {
        percent: d.free_percent as number,
        free: formatGB(d.free_bytes as number),
        total: formatGB(d.total_bytes as number),
      });
    case "retention":
      return d.last_run_at == null
        ? t("retentionNever")
        : t("retentionLastRun", { hours: Math.floor((d.age_seconds as number) / 3600) });
    case "backup":
      return d.last_backup_at == null
        ? t("backupNever")
        : t("backupAge", { days: d.age_days as number });
    case "provider": {
      const reachability = (d.reachability as string) ?? "unknown";
      // `n/a` (this provider has no balance) and `unknown` (we do not have the
      // answer) are different states — founder ruling O1-6. The JSON key cannot
      // carry a slash, so `n/a` maps to `credit.na`.
      const creditState = ((d.credit as string) ?? "unknown").replace("n/a", "na");
      // The model id is published (founder ruling O1-2): it is what lets the
      // operator match their instance against docs/llm-models.md's list. Not a
      // translatable string — an identifier, shown verbatim.
      const model = (d.model as string) ?? "";
      return [t(`providerReach.${reachability}`), t(`credit.${creditState}`), model]
        .filter(Boolean)
        .join(" · ");
    }
    case "errors": {
      const counts = (d.counts as Record<string, number>) ?? {};
      return t("errorsInWindow", {
        count: counts.total ?? 0,
        minutes: d.window_minutes as number,
      });
    }
    case "migrations":
      return c.status !== "ok" ? t("migrationsBehind") : null;
    case "database":
    default:
      return null;
  }
}

function TokenBucket({
  labelKey,
  labelValues,
  bucket,
  t,
}: {
  labelKey: string;
  labelValues?: Record<string, string | number | Date>;
  bucket: UsageBucket;
  t: Translator;
}) {
  // Built with .join() rather than a raw JSX text node so the label/value
  // separator is not authored as untranslated literal JSX text.
  const line = [
    t(labelKey, labelValues),
    // The RAW number, formatted by the message's own locale via ICU
    // `{tokens, number}`. `toLocaleString()` formats by the BROWSER locale, which
    // on a German machine printed "2.000 tokens" inside the English catalogue —
    // two point zero in English. Caught on the pixel screenshot, 2026-09-09.
    t("tokens.value", { tokens: bucket.total_tokens, calls: bucket.calls }),
  ].join(": ");

  return (
    <p className="flex flex-col">
      <span className="text-on-surface">{line}</span>
      {!bucket.fully_measured && (
        <span className="text-[11px] text-on-surface-variant">
          {t("tokens.estimated", { count: bucket.estimated_calls })}
        </span>
      )}
    </p>
  );
}

function TokensBlock({ usage, t }: { usage: UsageReport | undefined; t: Translator }) {
  const today = usage?.today;
  const windowBucket = usage?.window;
  const isEmpty = !usage || Object.keys(usage).length === 0;
  const bothZero =
    !!today && !!windowBucket && today.calls === 0 && windowBucket.calls === 0;

  if (isEmpty || bothZero) {
    return <p className="text-on-surface-variant">{t("tokens.none")}</p>;
  }

  return (
    <div className="flex flex-col gap-1.5">
      {today && <TokenBucket labelKey="tokens.today" bucket={today} t={t} />}
      {windowBucket && (
        <TokenBucket
          labelKey="tokens.window"
          labelValues={{ days: usage?.window_days ?? 0 }}
          bucket={windowBucket}
          t={t}
        />
      )}
      {/* by_document / by_application intentionally not rendered here — see file header. */}
    </div>
  );
}

export function OperatorPanel() {
  const t = useTranslations("ops");
  const [report, setReport] = useState<OpsHealthReport | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/ops/health`);
        if (!res.ok) {
          if (!cancelled) setUnavailable(true);
          return;
        }
        const data = (await res.json()) as OpsHealthReport;
        if (cancelled) return;
        setReport(data);
        setExpanded(data.status !== "ok");
      } catch {
        if (!cancelled) setUnavailable(true);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  if (unavailable) {
    return (
      <div data-testid="operator-panel" className="text-[13px] text-on-surface-variant">
        {t("unavailable")}
      </div>
    );
  }

  if (!report) return null; // nothing to show before the first response lands

  const presentComponents = COMPONENT_ORDER.filter((name) => report.components[name]);
  const problemCount = presentComponents.filter(
    (name) => report.components[name]!.status !== "ok",
  ).length;

  const accent: "ok" | "warning" | "critical" =
    report.status === "down" ? "critical" : report.status === "degraded" ? "warning" : "ok";

  const summary =
    report.status === "ok"
      ? t("summaryOk")
      : report.status === "down"
        ? t("summaryDown", { count: problemCount })
        : t("summaryDegraded", { count: problemCount });

  const containerClass =
    accent === "ok"
      ? "border-outline-variant bg-white"
      : accent === "critical"
        ? "border-critical/40 bg-critical-container"
        : "border-warning/40 bg-warning-container";

  const dotClass =
    accent === "ok" ? "bg-success" : accent === "critical" ? "bg-critical" : "bg-warning";

  return (
    <div
      data-testid="operator-panel"
      className={`rounded-xl border p-4 text-[13px] font-manrope ${containerClass}`}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${dotClass}`} />
        <span className="font-bold text-on-surface">{t("title")}</span>
        <span className="text-on-surface-variant">{summary}</span>
        <button
          type="button"
          data-testid="operator-panel-toggle"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="ml-auto shrink-0 text-teal hover:underline"
        >
          {expanded ? t("hideDetails") : t("showDetails")}
        </button>
      </div>

      {expanded && (
        <div className="mt-3 flex flex-col gap-2 border-t border-outline-variant pt-3">
          {presentComponents.map((name) => {
            const c = report.components[name]!;
            const line = factLine(name, c, t);
            return (
              <div
                key={name}
                data-testid={`operator-panel-row-${name}`}
                className="flex items-center justify-between gap-3"
              >
                <span className="text-on-surface">{t(`component.${name}`)}</span>
                <span className="text-right text-on-surface-variant">
                  {/* .join() rather than a raw JSX text node — see TokenBucket above. */}
                  {[t(`status.${c.status}`), line].filter(Boolean).join(" — ")}
                </span>
              </div>
            );
          })}

          <div className="mt-2 border-t border-outline-variant pt-3">
            <p className="mb-1 font-bold text-on-surface">{t("tokens.heading")}</p>
            <TokensBlock usage={report.usage} t={t} />
          </div>

          <p className="mt-2 text-[11px] text-on-surface-variant">
            {t("checkedAt", { time: new Date(report.checked_at) })}
          </p>
        </div>
      )}
    </div>
  );
}
