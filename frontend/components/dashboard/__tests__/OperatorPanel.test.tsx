// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * OperatorPanel (E060 / US312) — the self-hosting operator's dashboard health
 * summary. Covers: the quiet collapsed line when everything is fine, the
 * expanded/attention-coloured view when it is not, that `credit: "unknown"`
 * is a DISPLAYED state (not a field that quietly disappears — founder
 * question O1-6), the `usage: {}` / fetch-failure / missing-component
 * no-crash paths.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OperatorPanel } from "../OperatorPanel";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, params?: Record<string, unknown>) =>
    params ? `${key}:${Object.values(params).join(",")}` : key,
}));

function res(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}

afterEach(() => vi.restoreAllMocks());

const HEALTHY = {
  status: "ok",
  edition: "community",
  version: "0.41.1",
  llm_provider: "openrouter",
  checked_at: "2026-09-08T19:00:00+00:00",
  components: {
    database: { status: "ok", message: "", detail: { latency_ms: 3 } },
    migrations: { status: "ok", message: "", detail: { code_head: "0063", db_head: "0063" } },
    retention: {
      status: "ok",
      message: "",
      detail: {
        last_run_at: "2026-09-08T10:00:00+00:00",
        age_seconds: 3600,
        expected_interval_seconds: 86400,
        last_run_ok: true,
        deleted: { uploads_deleted: 0 },
      },
    },
    disk: {
      status: "ok",
      message: "",
      detail: {
        free_bytes: 161382166528,
        total_bytes: 271654584320,
        free_percent: 59.4,
        warn_below_percent: 10,
      },
    },
    backup: {
      status: "ok",
      message: "",
      detail: { last_backup_at: "2026-09-01T00:00:00+00:00", age_days: 7, warn_after_days: 30 },
    },
    // credit: "unknown" — most providers (Ollama, any OpenAI-compatible
    // endpoint) publish no balance at all. This is the case founder question
    // O1-6 is about: it must render, not silently vanish.
    provider: {
      status: "ok",
      message: "",
      detail: {
        provider: "openrouter",
        reachability: "ok",
        credit: "unknown",
        credit_reason: "provider publishes no balance",
        checked_at: "2026-09-08T19:00:00+00:00",
      },
    },
    errors: {
      status: "ok",
      message: "",
      detail: { window_minutes: 60, counts: { total: 0 }, scope: "this backend process since start" },
    },
  },
  usage: {
    today: {
      prompt_tokens: 1200,
      completion_tokens: 800,
      total_tokens: 2000,
      calls: 9,
      estimated_calls: 0,
      fully_measured: true,
    },
    window_days: 7,
    window: {
      prompt_tokens: 50000,
      completion_tokens: 21000,
      total_tokens: 71000,
      calls: 210,
      estimated_calls: 12,
      fully_measured: false,
    },
    by_document: [{ id: "doc-1", kind: "cv", total_tokens: 18000, calls: 41 }],
    by_application: [{ id: "app-1", total_tokens: 42000, calls: 98 }],
  },
};

const DEGRADED = {
  ...HEALTHY,
  status: "degraded",
  components: {
    ...HEALTHY.components,
    retention: {
      status: "degraded",
      message: "no run for 51 h",
      detail: {
        last_run_at: "2026-09-06T16:00:00+00:00",
        age_seconds: 183600,
        expected_interval_seconds: 86400,
        last_run_ok: true,
        deleted: { uploads_deleted: 0 },
      },
    },
    backup: {
      status: "degraded",
      message: "no backup has ever been recorded",
      detail: { last_backup_at: null, age_days: null, warn_after_days: 30 },
    },
  },
};

const EMPTY_USAGE = {
  status: "ok",
  edition: "community",
  version: "0.41.1",
  llm_provider: "openrouter",
  checked_at: "2026-09-08T19:00:00+00:00",
  components: {
    database: { status: "ok", message: "", detail: { latency_ms: 3 } },
  },
  usage: {},
};

const componentsWithoutBackup: Record<string, unknown> = { ...HEALTHY.components };
delete componentsWithoutBackup.backup;
const MISSING_BACKUP = {
  ...HEALTHY,
  components: componentsWithoutBackup,
};

describe("OperatorPanel", () => {
  it("healthy response: collapsed by default, shows the ok summary, rows hidden until toggled", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res(HEALTHY));
    render(<OperatorPanel />);

    await waitFor(() => expect(screen.getByTestId("operator-panel")).toBeInTheDocument());
    expect(screen.getByText("summaryOk")).toBeInTheDocument();
    expect(screen.queryByTestId("operator-panel-row-database")).toBeNull();

    const toggle = screen.getByTestId("operator-panel-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await userEvent.click(toggle);
    expect(screen.getByTestId("operator-panel-row-database")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("degraded response: expanded by default, right count in the summary, retention row shows hours", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res(DEGRADED));
    render(<OperatorPanel />);

    await waitFor(() =>
      expect(screen.getByTestId("operator-panel-row-retention")).toBeInTheDocument(),
    );
    // Two non-ok components (retention, backup) — degraded summary carries that count.
    expect(screen.getByText("summaryDegraded:2")).toBeInTheDocument();
    expect(screen.getByTestId("operator-panel-toggle")).toHaveAttribute("aria-expanded", "true");

    // age_seconds 183600 -> floor(183600 / 3600) = 51 hours.
    expect(screen.getByTestId("operator-panel-row-retention")).toHaveTextContent(
      "retentionLastRun:51",
    );
  });

  it('credit: "unknown" is rendered, not hidden — this is a displayed state', async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res(HEALTHY));
    render(<OperatorPanel />);

    await waitFor(() => expect(screen.getByTestId("operator-panel")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("operator-panel-toggle"));

    expect(screen.getByTestId("operator-panel-row-provider")).toHaveTextContent(
      "credit.unknown",
    );
  });

  it("usage: {} shows the no-usage-yet string and does not crash", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res(EMPTY_USAGE));
    render(<OperatorPanel />);

    await waitFor(() => expect(screen.getByTestId("operator-panel")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("operator-panel-toggle"));

    expect(screen.getByText("tokens.none")).toBeInTheDocument();
  });

  it("fetch rejecting shows the unavailable line and does not crash", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network error"));
    render(<OperatorPanel />);

    await waitFor(() => expect(screen.getByTestId("operator-panel")).toBeInTheDocument());
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });

  it("a response missing the backup component entirely does not crash; other rows still render", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(res(MISSING_BACKUP));
    render(<OperatorPanel />);

    await waitFor(() => expect(screen.getByTestId("operator-panel")).toBeInTheDocument());
    await userEvent.click(screen.getByTestId("operator-panel-toggle"));

    expect(screen.queryByTestId("operator-panel-row-backup")).toBeNull();
    expect(screen.getByTestId("operator-panel-row-database")).toBeInTheDocument();
    expect(screen.getByTestId("operator-panel-row-disk")).toBeInTheDocument();
    expect(screen.getByTestId("operator-panel-row-provider")).toBeInTheDocument();
  });
});
