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
 * ADR-090 clause 2 — *Show me where*: locate a group-1 finding's matched
 * wording in the document PREVIEW.
 *
 * The keyword check audits text extracted from the rendered PDF; the preview is
 * separately rendered HTML, so positions cannot be carried over. What IS
 * carried is the set of forms that passed `surface_present` on this document
 * (`keywords.present_unsupported_matches`, WP-A). This module searches the
 * preview's text nodes for those forms under a PORT of the audit's own
 * normalisation (`_norm` in `backend/applire/services/ats_audit.py`: soft-hyphen
 * drop, NFKC, dash fold, whitespace collapse, lower, strip) with an index map
 * back to the DOM, whole-token, and highlights every hit.
 *
 * The port is pinned to the Python function by `tests/files/ats_norm_vectors.json`
 * (read by a Python test and by `lib/__tests__/locate-in-preview.test.ts`).
 *
 * **A finding with no hit reports 0** — the card then says so
 * (*"This place could not be marked in the preview"*). A locate that silently
 * finds nothing would read as a fabricated finding (ADR-090 cl. 2).
 */

/** One form to look for. `stem` = the audit matched it only through the token-stem fallback. */
export interface LocateTarget {
  form: string;
  stem?: boolean;
}

/** A hit in the normalised text: [start, end). */
export interface Hit {
  start: number;
  end: number;
}

// Python's `str.isspace()` set — what `re.sub(r"\s+", …)` and `str.strip()`
// match on a `str`. Deliberately NOT JavaScript's `\s` (which adds U+FEFF and
// lacks U+001C–U+001F and U+0085).
const PY_WS_CLASS = "\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000";
const PY_WS_RUN = new RegExp(`[${PY_WS_CLASS}]+`, "g");
const PY_WS_CHAR = new RegExp(`^[${PY_WS_CLASS}]$`);
const PY_STRIP = new RegExp(`^[${PY_WS_CLASS}]+|[${PY_WS_CLASS}]+$`, "g");
// `-` U+002D, U+2010–U+2015, U+2212 — `_norm`'s dash class.
const DASH_RE = /[-\u2010-\u2015\u2212]/g;
const DASH_CHAR = /^[-\u2010-\u2015\u2212]$/;
const SOFT_HYPHEN = "\u00ad";
// Python's `\w` on `str` is alphanumeric-or-underscore; combining marks are
// included so a boundary never falls inside a decomposed letter.
const WORD_CHAR = /[\p{L}\p{N}\p{M}_]/u;

/** Port of `ats_audit._norm`. Step order is the Python order and must stay so. */
export function normAudit(s: string | null | undefined): string {
  let out = (s ?? "").split(SOFT_HYPHEN).join("");
  out = out.normalize("NFKC");
  out = out.replace(DASH_RE, " ");
  out = out.replace(PY_WS_RUN, " ").toLowerCase();
  return out.replace(PY_STRIP, "");
}

// --- the audit's morphological folds (ports, same guard rails) -------------

const FOLD_MIN_STEM = 4;
const VERB_SUFFIXES = ["ship", "ing", "ed", "es", "s"] as const;

/** Port of `_fold_variants`: singular/plural of the final token. */
export function foldVariants(needleNorm: string): string[] {
  const variants = [needleNorm];
  const parts = needleNorm.split(" ");
  const last = parts[parts.length - 1] ?? "";
  if (last.endsWith("s") && last.length - 1 >= FOLD_MIN_STEM) {
    variants.push(needleNorm.slice(0, -1));
  } else if (!last.endsWith("s") && last.length >= FOLD_MIN_STEM && /\p{L}$/u.test(last)) {
    variants.push(needleNorm + "s");
  }
  return variants;
}

/** Port of `_verb_stem`. */
export function verbStem(token: string): string {
  for (const suf of VERB_SUFFIXES) {
    if (token.endsWith(suf) && token.length - suf.length >= FOLD_MIN_STEM) {
      return token.slice(0, -suf.length);
    }
  }
  return token;
}

function isWordAt(text: string, i: number): boolean {
  if (i < 0 || i >= text.length) return false;
  return WORD_CHAR.test(text[i]);
}

/**
 * Every whole-token hit of the targets in an already-normalised text, sorted,
 * non-overlapping (an earlier or longer hit wins).
 */
export function findHits(textNorm: string, targets: LocateTarget[]): Hit[] {
  const raw: Hit[] = [];
  for (const target of targets) {
    const n = normAudit(target.form);
    if (!n) continue;
    for (const v of foldVariants(n)) {
      let from = 0;
      for (;;) {
        const i = textNorm.indexOf(v, from);
        if (i < 0) break;
        const j = i + v.length;
        if (!isWordAt(textNorm, i - 1) && !isWordAt(textNorm, j)) raw.push({ start: i, end: j });
        from = i + 1;
      }
    }
    // The stem fallback is single-token only, as in `_verb_form_present`.
    if (target.stem && !n.includes(" ")) {
      const needle = verbStem(n);
      const tokenRe = /[\p{L}\p{N}\p{M}_]+/gu;
      let m: RegExpExecArray | null;
      while ((m = tokenRe.exec(textNorm)) !== null) {
        if (verbStem(m[0]) === needle) raw.push({ start: m.index, end: m.index + m[0].length });
      }
    }
  }
  raw.sort((a, b) => a.start - b.start || b.end - a.end);
  const out: Hit[] = [];
  for (const h of raw) {
    const prev = out[out.length - 1];
    if (prev && h.start < prev.end) continue;
    out.push(h);
  }
  return out;
}

/** Count the hits of the targets in a plain string (a section's content, a `before` passage). */
export function countInText(text: string, targets: LocateTarget[]): number {
  return findHits(normAudit(text), targets).length;
}

// --- DOM index map ----------------------------------------------------------

interface MapEntry {
  node: Text;
  start: number;
  end: number;
}

/** The normalised text of a document plus, per output code unit, where it came from. */
export interface TextIndex {
  text: string;
  /** `null` = a virtual separator between blocks (no DOM position). */
  map: Array<MapEntry | null>;
}

const SKIP_TAGS = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "TITLE"]);
const BLOCK_TAGS = new Set([
  "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "BR", "DD", "DIV", "DL", "DT", "FIELDSET",
  "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "H1", "H2", "H3", "H4", "H5", "H6", "HEADER",
  "HR", "LI", "MAIN", "NAV", "OL", "P", "PRE", "SECTION", "TABLE", "TBODY", "TD", "TFOOT",
  "TH", "THEAD", "TR", "UL",
]);

/** Split a string into base-char + combining-mark clusters, keeping source offsets. */
function clusters(s: string): Array<{ text: string; start: number; end: number }> {
  const out: Array<{ text: string; start: number; end: number }> = [];
  const re = /\P{M}\p{M}*|\p{M}+/gu;
  let m: RegExpExecArray | null;
  while ((m = re.exec(s)) !== null) out.push({ text: m[0], start: m.index, end: m.index + m[0].length });
  return out;
}

/**
 * Build the normalised text of `root` with an index map back to its text nodes.
 * Block boundaries become a virtual space so "<li>SQL</li><li>dbt</li>" does not
 * read as "sqldbt". The folds run per cluster, so the result equals
 * `normAudit(<the concatenated text>)` on every input the vector file exercises.
 */
export function buildTextIndex(root: Node): TextIndex {
  // Pass 1: per-cluster fold (soft hyphen, NFKC, dash, lower) with source spans.
  const units: Array<{ ch: string; src: MapEntry | null }> = [];
  // The same text before lowercasing — the reference for context-sensitive case folds.
  let preLower = "";
  const pushSeparator = () => {
    units.push({ ch: " ", src: null });
    preLower += " ";
  };

  const walk = (node: Node) => {
    if (node.nodeType === 3) {
      const textNode = node as Text;
      for (const c of clusters(textNode.data)) {
        if (c.text === SOFT_HYPHEN) continue;
        let folded = c.text.split(SOFT_HYPHEN).join("").normalize("NFKC");
        folded = folded.replace(DASH_RE, " ");
        preLower += folded;
        folded = folded.toLowerCase();
        const src = { node: textNode, start: c.start, end: c.end };
        for (const ch of folded) {
          for (let k = 0; k < ch.length; k++) units.push({ ch: ch[k], src });
        }
      }
      return;
    }
    if (node.nodeType !== 1 && node.nodeType !== 9 && node.nodeType !== 11) return;
    const el = node as Element;
    const tag = node.nodeType === 1 ? el.tagName.toUpperCase() : "";
    if (SKIP_TAGS.has(tag)) return;
    const block = BLOCK_TAGS.has(tag);
    if (block) pushSeparator();
    node.childNodes.forEach(walk);
    if (block) pushSeparator();
  };
  walk(root);

  // Pass 2: whitespace collapse + strip, carrying the map.
  let text = "";
  const map: Array<MapEntry | null> = [];
  for (const u of units) {
    const ws = PY_WS_CHAR.test(u.ch) || DASH_CHAR.test(u.ch);
    if (ws) {
      if (text.length === 0 || text[text.length - 1] === " ") continue;
      text += " ";
      map.push(u.src);
    } else {
      text += u.ch;
      map.push(u.src);
    }
  }
  if (text.endsWith(" ")) {
    text = text.slice(0, -1);
    map.pop();
  }
  // Context-sensitive lowercasing (Greek final sigma) cannot be done per
  // cluster. The whole-string fold of the same text is the reference; when it
  // has the same length, it replaces the per-cluster result position for position.
  const whole = normAudit(preLower);
  if (whole.length === text.length) text = whole;
  return { text, map };
}

// --- highlight --------------------------------------------------------------

const MARK_ATTR = "data-applire-hit";
const CURRENT_ATTR = "data-applire-current";
const STYLE_ID = "applire-locate-style";

// Inside the preview iframe (not Tailwind): the canvas' two marks — every hit
// underlined on a pale ground, the current one on the ink ground with an outline.
const MARK_CSS = `mark[${MARK_ATTR}]{background:#fff8d6;color:inherit;border-bottom:2px solid #e5a832;padding:0 1px}
mark[${MARK_ATTR}][${CURRENT_ATTR}]{background:#fecb00;outline:2px solid #003399;outline-offset:1px;border-radius:2px}`;

/** Remove every mark this module placed, restoring the original text nodes. */
export function clearHighlights(doc: Document): void {
  const marks = Array.from(doc.querySelectorAll(`mark[${MARK_ATTR}]`));
  const parents = new Set<Node>();
  for (const mark of marks) {
    const parent = mark.parentNode;
    if (!parent) continue;
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark);
    parent.removeChild(mark);
    parents.add(parent);
  }
  parents.forEach((p) => p.normalize());
}

function ensureStyle(doc: Document): void {
  if (doc.getElementById(STYLE_ID)) return;
  const style = doc.createElement("style");
  style.id = STYLE_ID;
  style.textContent = MARK_CSS;
  (doc.head ?? doc.documentElement).appendChild(style);
}

/** The per-text-node segments of one hit. */
function segmentsOf(index: TextIndex, hit: Hit): MapEntry[] {
  const segs: MapEntry[] = [];
  for (let i = hit.start; i < hit.end; i++) {
    const e = index.map[i];
    if (!e) continue;
    const last = segs[segs.length - 1];
    if (last && last.node === e.node) {
      last.start = Math.min(last.start, e.start);
      last.end = Math.max(last.end, e.end);
    } else {
      segs.push({ node: e.node, start: e.start, end: e.end });
    }
  }
  return segs;
}

/** The document's own wording of one hit, as rendered. */
function hitText(index: TextIndex, hit: Hit): string {
  let out = "";
  let lastSrc: MapEntry | null = null;
  for (let i = hit.start; i < hit.end; i++) {
    const e = index.map[i];
    if (!e) {
      out += " ";
      lastSrc = null;
      continue;
    }
    if (e === lastSrc) continue;
    out += e.node.data.slice(e.start, e.end);
    lastSrc = e;
  }
  return out.replace(PY_WS_RUN, " ").replace(PY_STRIP, "");
}

export interface LocateResult {
  /** Number of places found. 0 = could not be marked. */
  total: number;
  /** The document's own wording at each place, in order. */
  texts: string[];
}

/** Count (and read) the places without touching the DOM. */
export function scanDocument(doc: Document, targets: LocateTarget[]): LocateResult {
  const root = doc.body ?? doc.documentElement;
  if (!root) return { total: 0, texts: [] };
  const index = buildTextIndex(root);
  const hits = findHits(index.text, targets);
  return {
    total: hits.length,
    texts: hits.map((h) => hitText(index, h)),
  };
}

/**
 * Highlight every place, emphasise place `current` (0-based) and scroll it into
 * view. Returns the same result as `scanDocument`.
 */
export function highlightInDocument(
  doc: Document,
  targets: LocateTarget[],
  current: number,
): LocateResult {
  clearHighlights(doc);
  const root = doc.body ?? doc.documentElement;
  if (!root) return { total: 0, texts: [] };
  const index = buildTextIndex(root);
  const hits = findHits(index.text, targets);
  const texts = hits.map((h) => hitText(index, h));
  if (hits.length === 0) return { total: 0, texts };
  ensureStyle(doc);

  // Wrap in REVERSE document order: splitting a text node keeps the original
  // node as the prefix, so earlier offsets stay valid.
  const jobs: Array<{ seg: MapEntry; hitIndex: number; order: number }> = [];
  let order = 0;
  hits.forEach((h, hitIndex) => {
    for (const seg of segmentsOf(index, h)) jobs.push({ seg, hitIndex, order: order++ });
  });
  for (let k = jobs.length - 1; k >= 0; k--) {
    const { seg, hitIndex } = jobs[k];
    const range = doc.createRange();
    range.setStart(seg.node, seg.start);
    range.setEnd(seg.node, seg.end);
    const mark = doc.createElement("mark");
    mark.setAttribute(MARK_ATTR, String(hitIndex));
    if (hitIndex === current) mark.setAttribute(CURRENT_ATTR, "true");
    try {
      range.surroundContents(mark);
    } catch {
      // A segment that cannot be wrapped stays unmarked; the count is unchanged.
    }
  }
  const first = doc.querySelector(`mark[${MARK_ATTR}="${current}"]`) as HTMLElement | null;
  first?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  return { total: hits.length, texts };
}

/** What a document preview exposes to the review surface. `null` doc = not loaded yet. */
export interface PreviewLocator {
  scan(targets: LocateTarget[]): LocateResult | null;
  show(targets: LocateTarget[], current: number): LocateResult | null;
  clear(): void;
}

/** A locator over whatever document `getDoc` returns at call time. */
export function makePreviewLocator(getDoc: () => Document | null | undefined): PreviewLocator {
  const ready = (): Document | null => {
    const doc = getDoc();
    if (!doc || !doc.body) return null;
    return doc;
  };
  return {
    scan: (targets) => {
      const doc = ready();
      return doc ? scanDocument(doc, targets) : null;
    },
    show: (targets, current) => {
      const doc = ready();
      return doc ? highlightInDocument(doc, targets, current) : null;
    },
    clear: () => {
      const doc = ready();
      if (doc) clearHighlights(doc);
    },
  };
}

/**
 * The preview iframe's document, found by its test id. The previews render a
 * same-origin `srcDoc` iframe (`sandbox="allow-same-origin"`), so its document
 * is readable. Looked up at call time — never during render.
 */
export function iframeDocument(testId: string): Document | null {
  if (typeof document === "undefined") return null;
  const frame = document.querySelector<HTMLIFrameElement>(`iframe[data-testid="${testId}"]`);
  return frame?.contentDocument ?? null;
}
