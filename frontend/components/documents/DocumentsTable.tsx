"use client";

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


import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

export interface DocumentItem {
  cv_id: string;
  flow_id: string | null;
  role_title: string | null;
  company_name: string | null;
  template: string;
  status: "ready" | "generating" | "expired" | "pending" | "failed";
  created_at: string;
  expires_at: string;
  /** E044/US252 (ADR-054): 'pipeline' | 'agent' — agent-rendered documents carry a badge. */
  origin?: string;
}

type StatusFilter = "all" | "ready" | "generating" | "expiring";
type SortMode = "newest" | "oldest" | "company";

// Template id → next-intl key in the `cv` namespace — the same mapping
// TemplateSelector.tsx and DossierDocumentsZone.tsx use. #311: this was a
// hard-coded English Record, so the German documents table showed
// "Compact Pro" beside otherwise-German prose.
const TEMPLATE_NAME_KEYS: Record<string, string> = {
  classic_german:   "templateClassic",
  modern_swiss:     "templateModern",
  executive:        "templateExecutive",
  tech_developer:   "templateTech",
  creative_sidebar: "templateCreative",
  academic:         "templateAcademic",
  compact_pro:      "templateCompact",
};

function daysUntilExpiry(expiresAt: string): number {
  return Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 864e5);
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "numeric" });
}

interface DocumentsTableProps {
  items: DocumentItem[];
  total: number;
  page: number;
  pageSize: number;
  onPageChange: (p: number) => void;
}

export function DocumentsTable({ items, total, page, pageSize, onPageChange }: DocumentsTableProps) {
  const t = useTranslations("documents");
  const tApp = useTranslations("applications");
  const tCv = useTranslations("cv");
  const router = useRouter();

  const templateLabel = (template: string): string => {
    const key = TEMPLATE_NAME_KEYS[template];
    return key ? tCv(key) : template;
  };

  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [sort, setSort] = useState<SortMode>("newest");

  const filtered = useMemo(() => {
    let rows = [...items];

    if (statusFilter === "ready")      rows = rows.filter((r) => r.status === "ready");
    if (statusFilter === "generating") rows = rows.filter((r) => r.status === "generating" || r.status === "pending");
    if (statusFilter === "expiring")   rows = rows.filter((r) => r.status === "ready" && daysUntilExpiry(r.expires_at) <= 7);

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      rows = rows.filter(
        (r) =>
          (r.role_title ?? "").toLowerCase().includes(q) ||
          (r.company_name ?? "").toLowerCase().includes(q)
      );
    }

    if (sort === "oldest")  rows.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    if (sort === "company") rows.sort((a, b) => (a.company_name ?? "").localeCompare(b.company_name ?? ""));

    return rows;
  }, [items, statusFilter, searchQuery, sort]);

  const totalPages = Math.ceil(total / pageSize);
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div>
      {/* Filter bar */}
      <div className="flex items-center gap-2.5 mb-3.5 flex-wrap">
        {(["all", "ready", "generating", "expiring"] as StatusFilter[]).map((f) => (
          <button
            key={f}
            onClick={() => setStatusFilter(f)}
            className={cn(
              "px-3.5 py-1 rounded-full text-[12px] font-bold border-[1.5px] transition-colors",
              statusFilter === f
                ? "bg-primary text-white border-primary"
                : "bg-white text-gray-500 border-gray-200 hover:border-primary-container hover:text-primary"
            )}
          >
            {t(f === "all" ? "filterAll" : f === "ready" ? "filterReady" : f === "generating" ? "filterGenerating" : "filterExpiring")}
          </button>
        ))}

        {/* Text search */}
        <div className="flex items-center gap-1.5 bg-white border-[1.5px] border-gray-200 rounded-full px-3.5 py-1 focus-within:border-primary transition-all">
          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
          <span className="material-symbols-outlined text-gray-400" aria-hidden="true" style={{ fontSize: 15 }}>search</span>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="border-none outline-none bg-transparent text-[12px] text-gray-800 placeholder:text-gray-400 w-36"
          />
        </div>

        <div className="flex-1" />

        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortMode)}
          className="text-[12px] text-gray-500 border-[1.5px] border-gray-200 rounded-lg px-2.5 py-1 bg-white outline-none cursor-pointer"
        >
          <option value="newest">{t("sortNewest")}</option>
          <option value="oldest">{t("sortOldest")}</option>
          <option value="company">{t("sortCompany")}</option>
        </select>
      </div>

      {/* Table */}
      <div className="bg-white rounded-[14px] border-[1.5px] border-gray-200 overflow-hidden">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-surface-dim border-b-[1.5px] border-gray-100">
              <th className="text-left px-4 py-2.5 text-[11px] font-bold text-gray-500 uppercase tracking-wider">{t("colDocument")}</th>
              <th className="text-left px-4 py-2.5 text-[11px] font-bold text-gray-500 uppercase tracking-wider">{t("colTemplate")}</th>
              <th className="text-left px-4 py-2.5 text-[11px] font-bold text-gray-500 uppercase tracking-wider">{t("colStatus")}</th>
              <th className="text-left px-4 py-2.5 text-[11px] font-bold text-gray-500 uppercase tracking-wider">{t("colExpires")}</th>
              <th className="px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={5} className="text-center py-12 text-[13px] text-gray-400">
                  {t("noDocuments")}
                </td>
              </tr>
            ) : (
              filtered.map((item) => {
                const days = daysUntilExpiry(item.expires_at);
                const isReady = item.status === "ready";
                const isGenerating = item.status === "generating" || item.status === "pending";
                // Material icon names stored as variables — not user-facing text
                const docIcon = "description";
                return (
                  <tr
                    key={item.cv_id}
                    className="border-b border-gray-50 last:border-none hover:bg-surface-container transition-colors cursor-pointer"
                  >
                    <td className="px-4 py-3.5">
                      <div className="flex items-center gap-3">
                        <div className={cn(
                          "w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0",
                          isGenerating ? "bg-amber-50" : "bg-teal-container"
                        )}>
                          <span
                            className="material-symbols-outlined"
                            aria-hidden="true"
                            style={{ fontSize: 20, color: isGenerating ? "#8b5000" : "var(--color-primary)" }}
                          >
                            {docIcon}
                          </span>
                        </div>
                        <div>
                          <p className="text-[13.5px] font-semibold text-gray-900 leading-tight">
                            {item.role_title ?? tApp("unknownRole")}
                          </p>
                          <p className="text-[11.5px] text-gray-500 mt-0.5">
                            {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                            {item.company_name ?? ""} · {t("generatedOn", { date: formatDate(item.created_at) })}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3.5">
                      <span className="text-[11px] font-semibold px-2 py-1 rounded-md bg-surface-container text-gray-600">
                        {templateLabel(item.template)}
                      </span>
                      {item.origin === "agent" && (
                        <span
                          data-testid="documents-origin-agent"
                          className="ml-1.5 inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-1 rounded-md bg-primary/10 text-primary"
                        >
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 13 }}>smart_toy</span>
                          {t("agentAuthored")}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3.5">
                      {isReady && (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-1 rounded-full bg-[#e6f4ea] text-[#1e6b3a]">
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 13 }}>check_circle</span>
                          {t("statusReady")}
                        </span>
                      )}
                      {isGenerating && (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-1 rounded-full bg-amber-50 text-amber-700">
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 13 }}>hourglass_top</span>
                          {t("statusGenerating")}
                        </span>
                      )}
                      {item.status === "expired" && (
                        <span className="inline-flex items-center gap-1 text-[11px] font-bold px-2 py-1 rounded-full bg-gray-100 text-gray-500">
                          {t("statusExpired")}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3.5">
                      {isReady && days <= 7 ? (
                        <span className="flex items-center gap-1 text-[12px] font-semibold text-amber-600">
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 14 }}>warning</span>
                          {days <= 0 ? t("expiresToday") : t("expiresIn", { days })}
                        </span>
                      ) : isReady ? (
                        <span className="text-[12px] text-gray-500">{formatDate(item.expires_at)}</span>
                      ) : (
                        // eslint-disable-next-line formatjs/no-literal-string-in-jsx
                        <span className="text-[12px] text-gray-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3.5 text-right">
                      {isReady && item.flow_id ? (
                        <button
                          data-testid="documents-table-open-btn"
                          onClick={(e) => {
                            e.stopPropagation();
                            router.push(`/flow/${item.flow_id}/cv`);
                          }}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 border-[1.5px] border-gray-200 rounded-lg text-[12px] font-semibold text-gray-600 hover:border-primary hover:text-primary hover:bg-surface-container transition-all"
                        >
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 15 }}>open_in_new</span>
                          {t("openButton")}
                        </button>
                      ) : isGenerating ? (
                        <button
                          disabled
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 border-[1.5px] border-gray-200 rounded-lg text-[12px] font-semibold text-gray-400 opacity-50 cursor-not-allowed"
                        >
                          {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 15 }}>hourglass_top</span>
                          {t("generatingButton")}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>

        {/* Pagination */}
        {total > pageSize && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100 text-[12px] text-gray-500">
            <span>{t("showing", { from, to, total })}</span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => onPageChange(page - 1)}
                disabled={page === 1}
                className="w-[30px] h-[30px] rounded-md border-[1.5px] border-gray-200 flex items-center justify-center disabled:opacity-40 hover:border-primary hover:text-primary transition-colors"
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 16 }}>chevron_left</span>
              </button>
              {[...Array(totalPages)].map((_, i) => (
                <button
                  key={i}
                  onClick={() => onPageChange(i + 1)}
                  className={cn(
                    "w-[30px] h-[30px] rounded-md border-[1.5px] text-[12px] font-semibold transition-colors",
                    page === i + 1
                      ? "bg-primary text-white border-primary"
                      : "border-gray-200 text-gray-500 hover:border-primary hover:text-primary"
                  )}
                >
                  {i + 1}
                </button>
              ))}
              <button
                onClick={() => onPageChange(page + 1)}
                disabled={page === totalPages}
                className="w-[30px] h-[30px] rounded-md border-[1.5px] border-gray-200 flex items-center justify-center disabled:opacity-40 hover:border-primary hover:text-primary transition-colors"
              >
                {/* eslint-disable-next-line formatjs/no-literal-string-in-jsx */}
                <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 16 }}>chevron_right</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
