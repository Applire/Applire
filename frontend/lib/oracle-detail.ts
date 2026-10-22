// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * ADR-052 amended 2026-10-22 (WP-T, tier C; #790 line "Oracle explanation is
 * engineer-speak, partly in another language"): a verdict detail that carries a
 * `detail_key` is said in the UI language (`oracleDetail.<key>`); every other
 * detail is shown as the backend wrote it. A key the catalog does not know falls
 * back to the English text — never a raw key on screen.
 */

export interface OracleDetailSource {
  detail?: string | null;
  detail_key?: string | null;
  detail_params?: Record<string, string> | null;
}

/** The slice of a next-intl translator this helper needs. */
export interface OracleDetailTranslator {
  (key: string, values?: Record<string, string>): string;
  has(key: string): boolean;
}

export function oracleDetailText(src: OracleDetailSource | null | undefined, t: OracleDetailTranslator): string | null {
  if (!src) return null;
  const key = src.detail_key;
  if (key && t.has(key)) return t(key, src.detail_params ?? {});
  return src.detail ?? null;
}
