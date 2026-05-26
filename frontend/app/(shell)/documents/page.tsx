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


import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { AppTopbar } from "@/components/shell/AppTopbar";
import { DocumentsTable, type DocumentItem } from "@/components/documents/DocumentsTable";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");
const PAGE_SIZE = 10;

export default function DocumentsPage() {
  const t = useTranslations("documents");
  const [items, setItems] = useState<DocumentItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [searchValue, setSearchValue] = useState("");

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const res = await fetch(
          `${API_BASE}/api/documents?page=${page}&page_size=${PAGE_SIZE}`
        );
        if (res.ok) {
          const d = await res.json();
          setItems(d.items ?? []);
          setTotal(d.total ?? 0);
        }
      } catch {
        // non-fatal
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [page]);

  const expiringCount = items.filter(
    (i) => i.status === "ready" && Math.ceil((new Date(i.expires_at).getTime() - Date.now()) / 864e5) <= 7
  ).length;

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      <AppTopbar
        mode="section"
        titleKey="shell.documents"
        showSearch
        searchValue={searchValue}
        onSearchChange={setSearchValue}
        searchPlaceholderKey="documents.searchPlaceholder"
      />

      <main className="flex-1 overflow-y-auto px-8 py-7">
        <div className="mb-5">
          <h1 className="text-[22px] font-extrabold text-neutral-dark font-manrope">{t("title")}</h1>
          <p className="text-[13px] text-gray-500 mt-0.5">{t("subtitle")}</p>
        </div>

        {/* Stats strip */}
        <div className="grid grid-cols-2 gap-3.5 mb-5">
          {[
            { icon: "description", label: t("totalDocs"),     value: total,         bg: "bg-teal-container",  iconColor: "var(--color-primary)" },
            { icon: "schedule",    label: t("expiringCount"),  value: expiringCount, bg: "bg-amber-50",        iconColor: "#8b5000" },
          ].map(({ icon, label, value, bg, iconColor }) => (
            <div key={label} className="bg-white rounded-xl border-[1.5px] border-gray-200 px-4 py-3.5 flex items-center gap-3.5">
              <div className={`w-10 h-10 rounded-[10px] ${bg} flex items-center justify-center flex-shrink-0`}>
                <span className="material-symbols-outlined" style={{ fontSize: 22, color: iconColor }}>{icon}</span>
              </div>
              <div>
                <p className="text-[24px] font-extrabold text-gray-900 font-manrope leading-none">{value}</p>
                <p className="text-[12px] text-gray-500 mt-0.5">{label}</p>
              </div>
            </div>
          ))}
        </div>

        {loading ? (
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="h-14 bg-white rounded-xl border border-gray-200 animate-pulse" />
            ))}
          </div>
        ) : (
          <DocumentsTable
            items={items}
            total={total}
            page={page}
            pageSize={PAGE_SIZE}
            onPageChange={setPage}
          />
        )}
      </main>
    </div>
  );
}
