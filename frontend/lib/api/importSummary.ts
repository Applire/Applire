// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
/**
 * R2-8 (9) (Hazelnut build 4, ruling V-2 = A) — `GET /api/profile/import-summary`:
 * the last import in the user's terms (backend `receipts.summarize_last_import`).
 */
import type { KeptSeparateItem, MatchReceiptItem, RestatedReceiptItem } from "@/lib/import-cv";
import { getApiErrorMessage } from "./errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? (process.env.NODE_ENV === "development" ? "http://localhost:8001" : "");

export interface ImportSummaryGroup {
  section: string;
  count: number;
  entities: Array<{ label: string; count: number }>;
  preview: string[];
  more: number;
}

export interface ImportSummary {
  record_id: string | null;
  timestamp: string | null;
  source: string | null;
  file_name: string | null;
  added_total: number;
  groups: ImportSummaryGroup[];
  matched: MatchReceiptItem[];
  kept_separate: KeptSeparateItem[];
  restated: RestatedReceiptItem[];
  not_applied_total: number;
}

export async function fetchImportSummary(): Promise<ImportSummary> {
  const res = await fetch(`${API_BASE}/api/profile/import-summary`);
  if (!res.ok) throw new Error(await getApiErrorMessage(res));
  return res.json();
}
