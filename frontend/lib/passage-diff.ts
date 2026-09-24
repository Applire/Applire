// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later
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

/**
 * D-3 (ADR-090 delivery run) — "This is what changed" shows the CHANGED
 * passages (ADR-090 cl. 3), not the whole section. A section is split into
 * units — lines, then sentences within a line — the two versions are aligned
 * unit by unit (longest common subsequence), and only the runs that differ
 * are returned, each with what stood there before and what stands there now.
 * Unchanged text is elided by the caller ("…").
 *
 * Presentation only: it never decides what the rewrite did — `before` and
 * `after` are the server's own texts, shown verbatim unit by unit.
 */

export interface PassageHunk {
  /** Units removed or replaced, in order (struck through). */
  before: string[];
  /** Units that replaced them, in order (may be empty: a pure removal). */
  after: string[];
  /** Unchanged text precedes / follows this hunk (render an ellipsis). */
  elidedBefore: boolean;
  elidedAfter: boolean;
}

/** Lines, then sentences within each line; empty units dropped. */
export function passageUnits(text: string): string[] {
  const out: string[] = [];
  for (const line of text.replace(/\r\n?/g, "\n").split("\n")) {
    for (const sentence of line.split(/(?<=[.!?…])\s+/)) {
      const s = sentence.trim();
      if (s) out.push(s);
    }
  }
  return out;
}

const same = (a: string, b: string) => a.replace(/\s+/g, " ") === b.replace(/\s+/g, " ");

/** The differing runs between two versions of one section. */
export function changedPassages(beforeText: string, afterText: string): PassageHunk[] {
  const a = passageUnits(beforeText);
  const b = passageUnits(afterText);
  // LCS table over units.
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = same(a[i], b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const hunks: PassageHunk[] = [];
  let i = 0;
  let j = 0;
  let cur: { before: string[]; after: string[]; start: number } | null = null;
  const close = (endIndex: number) => {
    if (!cur) return;
    hunks.push({
      before: cur.before,
      after: cur.after,
      elidedBefore: cur.start > 0,
      elidedAfter: endIndex < n,
    });
    cur = null;
  };
  while (i < n || j < m) {
    if (i < n && j < m && same(a[i], b[j])) {
      close(i);
      i++;
      j++;
    } else if (j >= m || (i < n && dp[i + 1][j] >= dp[i][j + 1])) {
      cur ??= { before: [], after: [], start: i };
      cur.before.push(a[i]);
      i++;
    } else {
      cur ??= { before: [], after: [], start: i };
      cur.after.push(b[j]);
      j++;
    }
  }
  close(n);
  return hunks;
}
