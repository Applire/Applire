// Copyright (C) 2026 Tobias Rosenbaum
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * U-6 (founder UAT-2, WP-C): visible page breaks in the on-screen preview of a
 * CV or a letter. The preview is the template's HTML on screen, where `@page`
 * never applies — so a reader could not see where the PDF breaks. This module
 * paginates the preview document the way the PDF engine does, closely enough
 * to see it:
 *
 * - the content box of every page is A4 minus the template's OWN `@page`
 *   margins (read from its stylesheet); the screen `.page` padding is aligned
 *   to those margins so lines wrap as they do in the PDF;
 * - a block the template keeps together (`break-inside: avoid`) moves whole to
 *   the next page, as the PDF moves it; a block taller than a page is split
 *   between its children;
 * - side-by-side columns break at the same height (per column, the first block
 *   that crosses the page edge moves down);
 * - each break inserts in-flow spacers (never touching the template's text)
 *   and ONE full-width band "— SEITE k VON n —" in the gap between pages. The
 *   band's label is CSS `content`, so the preview's text — which the locator
 *   searches — is unchanged.
 *
 * The result is a preview aid. The PDF stays the authority for the page count
 * (the page-length check and card read the rendered PDF).
 */

const PX_PER_MM = 96 / 25.4;
// The PDF engine's A4 sheet is 595.92 × 842.88 pt (measured on the rendered
// PDF), a hair larger than 210 × 297 mm — use ITS size, or a line that just
// fits in the PDF wraps in the preview.
const A4_WIDTH_PX = (595.92 * 4) / 3;
const A4_HEIGHT_PX = (842.88 * 4) / 3;
/** The grey gap drawn between two pages, in px. */
export const BAND_HEIGHT_PX = 30;
const MARK = "data-applire-page-break";
/** A flex/grid row shorter than this (≈ four lines) moves to the next page whole. */
const SHORT_ROW_PX = 80;

export interface PageMargins {
  top: number;
  bottom: number;
  left: number;
  right: number;
  /** `@page :first { margin-top }` — the letter templates start page 1 higher. */
  firstTop: number;
}

function toPx(value: string, doc: Document): number | null {
  const v = value.trim();
  if (!v) return null;
  const m = v.match(/^(-?[\d.]+)(mm|cm|in|pt|px)?$/);
  if (m) {
    const n = Number(m[1]);
    switch (m[2] ?? "px") {
      case "mm":
        return n * PX_PER_MM;
      case "cm":
        return n * 10 * PX_PER_MM;
      case "in":
        return n * 96;
      case "pt":
        return (n * 96) / 72;
      default:
        return n;
    }
  }
  // Anything else (calc, em): let the engine resolve it on a probe.
  const probe = doc.createElement("div");
  probe.style.cssText = `position:absolute;visibility:hidden;height:${v}`;
  doc.body.appendChild(probe);
  const h = probe.getBoundingClientRect().height;
  probe.remove();
  return Number.isFinite(h) ? h : null;
}

/** The template's `@page` margins (plus a `:first` top override), or `null`. */
export function pageMargins(doc: Document): PageMargins | null {
  let base: Omit<PageMargins, "firstTop"> | null = null;
  let firstTop: number | null = null;
  for (const sheet of Array.from(doc.styleSheets)) {
    let rules: CSSRuleList;
    try {
      rules = sheet.cssRules;
    } catch {
      continue;
    }
    for (const rule of Array.from(rules)) {
      // CSSPageRule — `type === 6` where the constructor is not exposed.
      if (rule.type !== 6 && !(typeof CSSPageRule !== "undefined" && rule instanceof CSSPageRule)) continue;
      const page = rule as CSSPageRule;
      const style = page.style;
      const selector = (page.selectorText ?? "").trim();
      if (selector === ":first") {
        firstTop = toPx(style.getPropertyValue("margin-top"), doc) ?? firstTop;
        continue;
      }
      if (selector !== "" || base) continue;
      const top = toPx(style.getPropertyValue("margin-top"), doc);
      const bottom = toPx(style.getPropertyValue("margin-bottom"), doc);
      if (top === null || bottom === null) continue;
      base = {
        top,
        bottom,
        left: toPx(style.getPropertyValue("margin-left"), doc) ?? 0,
        right: toPx(style.getPropertyValue("margin-right"), doc) ?? 0,
      };
    }
  }
  return base ? { ...base, firstTop: firstTop ?? base.top } : null;
}

interface Unit {
  el: HTMLElement;
  /** A table row moves by its cells' padding (no spacer element fits inside a table). */
  row: boolean;
  /** No block children — its text is its content. */
  leaf: boolean;
}

function keepsTogether(cs: CSSStyleDeclaration): boolean {
  return cs.breakInside === "avoid" || cs.getPropertyValue("page-break-inside") === "avoid";
}

/** The blocks a page edge may fall between, in document order. */
function collectUnits(root: HTMLElement, contentHeight: number, win: Window): Unit[] {
  const out: Unit[] = [];
  const walk = (el: HTMLElement) => {
    for (const child of Array.from(el.children) as HTMLElement[]) {
      if (child.hasAttribute(MARK)) continue;
      const cs = win.getComputedStyle(child);
      if (cs.display === "none" || cs.position === "absolute" || cs.position === "fixed") continue;
      const h = child.getBoundingClientRect().height;
      if (h === 0) continue;
      const d = cs.display;
      if (d === "table-row") {
        out.push({ el: child, row: true, leaf: false });
        continue;
      }
      if (d === "table" || d === "table-row-group" || d === "table-header-group" || d === "table-footer-group") {
        walk(child);
        continue;
      }
      const hasBlockKids = Array.from(child.children).some((k) => {
        const kd = win.getComputedStyle(k).display;
        return kd !== "inline" && kd !== "none" && !kd.startsWith("inline");
      });
      const atomic =
        !hasBlockKids ||
        (keepsTogether(cs) && h <= contentHeight) ||
        // A short flex/grid row (chips, a date beside a title) moves whole; a tall
        // one splits between its columns' blocks, as the PDF engine splits it.
        ((d === "flex" || d === "inline-flex" || d === "grid") && cs.flexDirection !== "column" && h < SHORT_ROW_PX);
      if (atomic) out.push({ el: child, row: false, leaf: !hasBlockKids });
      else walk(child);
    }
  };
  walk(root);
  return out;
}

/**
 * The box a leaf block's CONTENT occupies. A grid/flex cell stretched to its
 * row's height (a date column beside a long entry) has a tall box but a short
 * content — only its content can cross a page edge.
 */
function inkBox(u: Unit, doc: Document): { top: number; bottom: number; left: number; right: number } {
  const box = u.el.getBoundingClientRect();
  if (!u.leaf) return box;
  const range = doc.createRange();
  range.selectNodeContents(u.el);
  const ink = range.getBoundingClientRect();
  if (ink.height === 0) return box;
  return { top: box.top, bottom: Math.min(box.bottom, ink.bottom), left: box.left, right: box.right };
}

function topOf(el: HTMLElement, win: Window): number {
  return el.getBoundingClientRect().top + win.scrollY;
}

/** Push `unit` down by `delta` px — a spacer before it, or (a table row) its cells' padding. */
function pushDown(unit: Unit, delta: number, doc: Document): HTMLElement[] {
  if (delta <= 0) return [];
  if (unit.row) {
    const cells = Array.from(unit.el.children) as HTMLElement[];
    for (const c of cells) {
      if (!c.hasAttribute("data-applire-pad")) c.setAttribute("data-applire-pad", c.style.paddingTop || "");
      const cur = parseFloat(doc.defaultView!.getComputedStyle(c).paddingTop) || 0;
      c.style.paddingTop = `${cur + delta}px`;
    }
    return [];
  }
  // Inside a grid or a flex row a spacer would become a cell of its own —
  // move the block by its top margin there instead.
  const parent = unit.el.parentElement!;
  const pd = doc.defaultView!.getComputedStyle(parent);
  if (pd.display.includes("grid") || (pd.display.includes("flex") && !pd.flexDirection.startsWith("column"))) {
    if (!unit.el.hasAttribute("data-applire-margin")) unit.el.setAttribute("data-applire-margin", unit.el.style.marginTop || "");
    const cur = parseFloat(doc.defaultView!.getComputedStyle(unit.el).marginTop) || 0;
    unit.el.style.marginTop = `${cur + delta}px`;
    return [];
  }
  const spacer = doc.createElement("div");
  spacer.setAttribute(MARK, "spacer");
  spacer.setAttribute("aria-hidden", "true");
  spacer.style.cssText = `display:block;height:${delta}px;margin:0;padding:0;border:0;flex:none;break-inside:auto`;
  unit.el.parentElement!.insertBefore(spacer, unit.el);
  return [spacer];
}

/** Remove every spacer, band and padding this module added (a re-run starts clean). */
export function clearPagination(doc: Document): void {
  doc.querySelectorAll(`[${MARK}]`).forEach((n) => n.remove());
  doc.querySelectorAll<HTMLElement>("[data-applire-pad]").forEach((c) => {
    c.style.paddingTop = c.getAttribute("data-applire-pad") ?? "";
    c.removeAttribute("data-applire-pad");
  });
  doc.querySelectorAll<HTMLElement>("[data-applire-margin]").forEach((c) => {
    c.style.marginTop = c.getAttribute("data-applire-margin") ?? "";
    c.removeAttribute("data-applire-margin");
  });
  doc.getElementById("applire-page-style")?.remove();
}

export interface PaginationResult {
  pages: number;
  /** Page-edge positions (document px) where a band was drawn. */
  breaks: number[];
}

/**
 * Paginate the preview document in place. `label(page, total)` names a band
 * (the catalog's "Seite {page} von {total}"). Returns `null` when the document
 * carries no `.page` root or no `@page` margins (nothing to model).
 */
export function paginatePreview(doc: Document, label: (page: number, total: number) => string): PaginationResult | null {
  const win = doc.defaultView;
  if (!win || !doc.body) return null;
  clearPagination(doc);
  const root = doc.querySelector<HTMLElement>(".page");
  const margins = pageMargins(doc);
  if (!root || !margins) return null;

  // Lay the preview out as the PDF engine does: the template's own print rules
  // apply on screen too (#621 moved the paper margin into @page and zeroes the
  // screen padding in print — some templates also re-inset bands and columns
  // there), and the paper margins become the page padding.
  const printRules: string[] = [];
  for (const sheet of Array.from(doc.styleSheets)) {
    let rules: CSSRuleList;
    try {
      rules = sheet.cssRules;
    } catch {
      continue;
    }
    for (const rule of Array.from(rules)) {
      const media = (rule as CSSMediaRule).media;
      if (rule.type === 4 && media && Array.from(media).includes("print")) {
        for (const inner of Array.from((rule as CSSMediaRule).cssRules)) printRules.push(inner.cssText);
      }
    }
  }
  const style = doc.createElement("style");
  style.id = "applire-page-style";
  style.setAttribute(MARK, "style");
  style.textContent = `
    ${printRules.join("\n")}
    @media screen {
      html, body { overflow-x: hidden; }
      .page { width: ${A4_WIDTH_PX}px !important; margin: 0 auto !important; padding: ${margins.firstTop}px ${margins.right}px ${margins.bottom}px ${margins.left}px !important; position: relative; box-sizing: border-box; }
      [${MARK}="band"] {
        position: absolute; left: 0; right: 0; height: ${BAND_HEIGHT_PX}px;
        background: #e9ecf2; border-top: 1px dashed #9aa3bd; border-bottom: 1px dashed #9aa3bd;
        display: flex; align-items: center; justify-content: center; pointer-events: none; z-index: 5;
        font: 700 11px/1 Inter, Arial, sans-serif; letter-spacing: .04em; color: #5b6684; text-transform: uppercase;
      }
      [${MARK}="band"]::after { content: attr(data-label); }
    }`;
  doc.head.appendChild(style);

  const contentHeight = A4_HEIGHT_PX - margins.top - margins.bottom;
  const gap = margins.bottom + BAND_HEIGHT_PX + margins.top;
  const units = collectUnits(root, contentHeight, win);
  const rootTop = topOf(root, win);
  let pageContentTop = rootTop + margins.firstTop;
  const breaks: number[] = [];

  for (let guard = 0; guard < 40; guard++) {
    // Page 1's content box starts at the `:first` margin, the rest at `top`.
    const limit = guard === 0 ? rootTop + A4_HEIGHT_PX - margins.bottom : pageContentTop + contentHeight;
    // Per column, the first block that crosses (or starts beyond) the page edge.
    const crossing = units
      .map((u) => ({ u, r: inkBox(u, doc) }))
      .filter(({ r }) => r.bottom + win.scrollY > limit + 0.5)
      .sort((a, b) => a.r.top - b.r.top);
    if (crossing.length === 0) break;
    const chosen: Array<{ u: Unit; top: number }> = [];
    const spans: Array<[number, number]> = [];
    for (const { u, r } of crossing) {
      if (spans.some(([l, rr]) => r.left < rr - 1 && r.right > l + 1)) continue;
      spans.push([r.left, r.right]);
      chosen.push({ u, top: r.top + win.scrollY });
    }
    const nextContentTop = limit + gap;
    for (const { u, top } of chosen) {
      // A block that starts on this page above the edge moves to the next page
      // whole; a block taller than a page (it cannot) stays where it is.
      const height = u.el.getBoundingClientRect().height;
      if (height > contentHeight && top < limit) continue;
      const spacers = pushDown(u, nextContentTop - top, doc);
      // Margin collapsing may have eaten part of the push — correct once.
      const landed = topOf(u.el, win);
      const miss = nextContentTop - landed;
      if (Math.abs(miss) > 0.5) {
        if (spacers[0]) spacers[0].style.height = `${Math.max(0, parseFloat(spacers[0].style.height) + miss)}px`;
        else pushDown(u, miss, doc);
      }
    }
    breaks.push(limit + margins.bottom);
    pageContentTop = nextContentTop;
  }

  // Make sure the last page is a whole A4 tall, like the sheets above it.
  const total = breaks.length + 1;
  const docEnd = rootTop + (total * A4_HEIGHT_PX) + breaks.length * BAND_HEIGHT_PX;
  root.style.minHeight = `${Math.max(0, docEnd - rootTop)}px`;
  breaks.forEach((y, i) => {
    const band = doc.createElement("div");
    band.setAttribute(MARK, "band");
    band.setAttribute("aria-hidden", "true");
    band.setAttribute("data-label", `— ${label(i + 2, total)} —`);
    band.style.top = `${y - rootTop}px`;
    root.appendChild(band);
  });
  return { pages: total, breaks };
}
