// Copyright (C) 2024-2026 Tobias Rosenbaum
//
// This file is part of Applire.
//
// Applire is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published
// by the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Applire is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
// GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License
// along with Applire. If not, see <https://www.gnu.org/licenses/>.

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DocumentsTable, type DocumentItem } from "../DocumentsTable";

vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string, params?: Record<string, unknown>) => {
    if (params) return `${key}:${JSON.stringify(params)}`;
    return key;
  },
}));
const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const FAR_FUTURE = new Date(Date.now() + 60 * 24 * 36e5).toISOString();
const NEAR_FUTURE = new Date(Date.now() + 3 * 24 * 36e5).toISOString();

const ITEMS: DocumentItem[] = [
  {
    cv_id: "cv-1",
    flow_id: "flow-1",
    role_title: "Head of Validation",
    company_name: "Roche",
    template: "classic_german",
    status: "ready",
    created_at: new Date().toISOString(),
    expires_at: FAR_FUTURE,
  },
  {
    cv_id: "cv-2",
    flow_id: "flow-2",
    role_title: "QA Lead",
    company_name: "Bayer",
    template: "modern_swiss",
    status: "ready",
    created_at: new Date().toISOString(),
    expires_at: NEAR_FUTURE,
  },
  {
    cv_id: "cv-3",
    flow_id: null,
    role_title: "Director of QA",
    company_name: "Novartis",
    template: "classic_german",
    status: "generating",
    created_at: new Date().toISOString(),
    expires_at: FAR_FUTURE,
  },
];

function renderTable(overrides?: Partial<React.ComponentProps<typeof DocumentsTable>>) {
  return render(
    <DocumentsTable
      items={ITEMS}
      total={3}
      page={1}
      pageSize={10}
      onPageChange={vi.fn()}
      {...overrides}
    />
  );
}

describe("DocumentsTable", () => {
  it("renders all rows by default", () => {
    renderTable();
    expect(screen.getByText("Head of Validation")).toBeInTheDocument();
    expect(screen.getByText("QA Lead")).toBeInTheDocument();
    expect(screen.getByText("Director of QA")).toBeInTheDocument();
  });

  // #311: template names were a hard-coded English Record here, so the German
  // documents table showed "Compact Pro" / "Modern Swiss" next to German prose.
  // The mocked translator echoes the key, so the key itself is the assertion.
  it("renders template names through the cv catalog, not hard-coded English", () => {
    renderTable();
    expect(screen.getAllByText("templateClassic").length).toBeGreaterThan(0);
    expect(screen.getAllByText("templateModern").length).toBeGreaterThan(0);
    expect(screen.queryByText("Classic German")).not.toBeInTheDocument();
    expect(screen.queryByText("Modern Swiss")).not.toBeInTheDocument();
  });

  it("falls back to the raw template id for an unknown template", () => {
    renderTable({
      items: [{ ...ITEMS[0], cv_id: "cv-x", template: "some_future_template" }],
    });
    expect(screen.getByText("some_future_template")).toBeInTheDocument();
  });

  // E044/US252 (ADR-054): agent-rendered documents are never presented as
  // Applire-authored — origin='agent' rows carry a badge, pipeline rows don't.
  it("shows the agent-authored badge only for origin='agent' rows", () => {
    renderTable({
      items: [
        { ...ITEMS[0], origin: "agent" },
        { ...ITEMS[1], origin: "pipeline" },
        ITEMS[2], // origin absent (legacy payload) — no badge
      ],
    });
    expect(screen.getAllByTestId("documents-origin-agent")).toHaveLength(1);
    expect(screen.getByTestId("documents-origin-agent")).toHaveTextContent("agentAuthored");
  });

  it("text search filters rows by company", () => {
    renderTable();
    fireEvent.change(screen.getByPlaceholderText("searchPlaceholder"), {
      target: { value: "Roche" },
    });
    expect(screen.getByText("Head of Validation")).toBeInTheDocument();
    expect(screen.queryByText("QA Lead")).not.toBeInTheDocument();
  });

  it("text search is case-insensitive", () => {
    renderTable();
    fireEvent.change(screen.getByPlaceholderText("searchPlaceholder"), {
      target: { value: "bayer" },
    });
    expect(screen.getByText("QA Lead")).toBeInTheDocument();
    expect(screen.queryByText("Head of Validation")).not.toBeInTheDocument();
  });

  it("Generating filter hides ready rows", () => {
    renderTable();
    fireEvent.click(screen.getByText("filterGenerating"));
    expect(screen.getByText("Director of QA")).toBeInTheDocument();
    expect(screen.queryByText("Head of Validation")).not.toBeInTheDocument();
  });

  it("Expiring filter shows only rows expiring within 7 days", () => {
    renderTable();
    fireEvent.click(screen.getByText("filterExpiring"));
    expect(screen.getByText("QA Lead")).toBeInTheDocument();
    expect(screen.queryByText("Head of Validation")).not.toBeInTheDocument();
  });

  it("Open button is disabled for generating rows", () => {
    renderTable();
    const buttons = screen.getAllByRole("button");
    const generatingBtn = buttons.find((b) => b.textContent?.includes("generatingButton"));
    expect(generatingBtn).toBeDisabled();
  });

  // #604 — a `failed` row rendered an EMPTY status cell: the type union has
  // carried "failed" all along, and every branch in the cell skipped it, so My
  // Documents was the one surface that never said a generation had died
  // (edge UAT 2026-08-29). Every other status is asserted alongside it, so a
  // future branch cannot be dropped silently the same way.
  describe("status cell covers every status in the union (#604)", () => {
    const FAILED_ITEM: DocumentItem = {
      cv_id: "cv-4",
      flow_id: null,
      role_title: "Head of CMC",
      company_name: "Lonza",
      template: "classic_german",
      status: "failed",
      created_at: new Date().toISOString(),
      expires_at: FAR_FUTURE,
    };
    const EXPIRED_ITEM: DocumentItem = { ...FAILED_ITEM, cv_id: "cv-5", status: "expired" };

    it("a failed row says so instead of showing nothing", () => {
      renderTable({ items: [FAILED_ITEM], total: 1 });
      expect(screen.getByTestId("documents-status-failed")).toBeInTheDocument();
      expect(screen.getByText("statusFailed")).toBeInTheDocument();
    });

    it.each([
      ["ready", "statusReady"],
      ["generating", "statusGenerating"],
      ["pending", "statusGenerating"],
      ["expired", "statusExpired"],
      ["failed", "statusFailed"],
    ] as const)("status %s renders a label", (status, label) => {
      renderTable({ items: [{ ...EXPIRED_ITEM, status }], total: 1 });
      expect(screen.getByText(label)).toBeInTheDocument();
    });
  });
});

describe("DocumentsTable — #789 the letter belongs on the page too", () => {
  it("a row with a ready letter names it and opens it on its own page", () => {
    push.mockClear();
    const withLetter: DocumentItem[] = [
      { ...ITEMS[0], cover_letter: { cover_letter_id: "cl-1", status: "ready", created_at: ITEMS[0].created_at, expires_at: FAR_FUTURE } },
      ITEMS[1],
    ];
    renderTable({ items: withLetter, total: 2 });
    const lines = screen.getAllByTestId("documents-letter");
    expect(lines).toHaveLength(1);
    expect(lines[0].textContent).toContain("letterReady");
    fireEvent.click(screen.getByTestId("documents-letter-open"));
    expect(push).toHaveBeenCalledWith("/flow/flow-1/cover-letter");
  });

  it("a letter still being written has no open action", () => {
    const generating: DocumentItem[] = [
      { ...ITEMS[0], cover_letter: { cover_letter_id: "cl-1", status: "generating", created_at: ITEMS[0].created_at, expires_at: FAR_FUTURE } },
    ];
    renderTable({ items: generating, total: 1 });
    expect(screen.getByTestId("documents-letter").textContent).toContain("letterGenerating");
    expect(screen.queryByTestId("documents-letter-open")).toBeNull();
  });

  it("dates follow the UI language, not a hard-coded de-DE", () => {
    const fixed: DocumentItem[] = [{ ...ITEMS[0], created_at: "2026-03-05T10:00:00Z" }];
    renderTable({ items: fixed, total: 1 });
    expect(screen.getByText(/generatedOn/).textContent).toContain("5 Mar 2026");
  });
});

describe("DocumentsTable — 789:187 a letter with no CV is listed", () => {
  const LETTER_ONLY: DocumentItem = {
    kind: "cover_letter",
    cv_id: null,
    flow_id: "flow-9",
    role_title: "Werkleiter",
    company_name: "Nordlicht AG",
    template: "classic_german",
    status: "ready",
    created_at: new Date().toISOString(),
    expires_at: FAR_FUTURE,
    cover_letter: {
      cover_letter_id: "cl-9",
      status: "ready",
      created_at: new Date().toISOString(),
      expires_at: FAR_FUTURE,
    },
  };

  it("renders the letter as its own row that opens the letter page", () => {
    push.mockClear();
    renderTable({ items: [LETTER_ONLY], total: 1 });
    const row = screen.getByTestId("documents-row");
    expect(row).toHaveAttribute("data-kind", "cover_letter");
    expect(screen.getByText("Werkleiter")).toBeInTheDocument();
    expect(screen.getByText("typeLetter")).toBeInTheDocument();
    // the letter is the row, not a second line under it
    expect(screen.queryByTestId("documents-letter")).toBeNull();
    fireEvent.click(screen.getByTestId("documents-table-open-btn"));
    expect(push).toHaveBeenCalledWith("/flow/flow-9/cover-letter");
  });

  it("CV rows keep their template label and open the CV", () => {
    push.mockClear();
    renderTable({ items: [ITEMS[0], LETTER_ONLY], total: 2 });
    const rows = screen.getAllByTestId("documents-row");
    expect(rows.map((r) => r.getAttribute("data-kind"))).toEqual(["cv", "cover_letter"]);
    fireEvent.click(screen.getAllByTestId("documents-table-open-btn")[0]);
    expect(push).toHaveBeenCalledWith("/flow/flow-1/cv");
  });
});
