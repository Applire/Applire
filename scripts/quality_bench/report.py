#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (report) — issues per 100 generated documents, with bootstrap intervals.

Aggregates N scored run directories (``score.json`` next to ``meta.json``) into
one number per defect class:

* **headline** — issues per 100 documents, *per occurrence* (founder O-1: four
  English bullets are four issues), beside **% of documents with >= 1 issue**
  of that class (robust where the first is sensitive);
* split **overall / per cell_type / per model**, and **cv vs letter** (the
  denominator then counts only that document type). A run-level finding that
  belongs to neither document alone (a vault fact omitted from BOTH, doc
  ``"both"``) is in the per-100 rate of ``all`` and in no document's "% with
  >= 1 issue";
* **95 % CI by bootstrap resampling RUNS** (1000 resamples, fixed seed, ratio
  estimator ``100 * sum(issues) / sum(documents)``) — documents of one run share
  a vault, a posting and a model call chain, so they are not independent;
* **failed runs** (``meta.status == "failed"``) and runs that are ``ok`` but
  unscored are listed separately and are **never** a zero: they are in no
  denominator;
* the **stage histogram** per class (where issues are introduced, from
  ``attribution.py``);
* **page length vs template budget** per document type (US368): rendered pages
  (``pages.json``) against the budget the product applies (CV: the row's
  resolved ``target_pages``; letter: the region's ``letter_pages``), as % over
  budget with a run-bootstrap CI;
* the **arm-diff table**: per class, ``rate(arm B) - rate(arm A)`` with an
  independent-resample bootstrap CI; a difference is called only when the CI
  excludes zero.

Writes ``report.md`` and ``report.json`` into ``--out``.

Usage::

    python3 scripts/quality_bench/report.py 'out/*/*/*/*' --out Documents/Runs/Strawberry/quality-bench/baseline
    python3 scripts/quality_bench/report.py out --baseline-arm baseline --calibration calibration.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from .score.common import CLASSES, CLASS_NAMES
    from .score.inputs import find_run_dirs, read_json
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from score.common import CLASSES, CLASS_NAMES  # type: ignore[no-redef]
    from score.inputs import find_run_dirs, read_json  # type: ignore[no-redef]

SEED = 20261012
RESAMPLES = 1000
DOC_FILTERS = ("all", "cv", "letter")
METRIC_CLASSES = (*CLASSES, "total")


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_runs(patterns: list[str]) -> dict[str, Any]:
    """Classify run dirs into scored / failed / unscored."""
    scored: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    unscored: list[dict[str, Any]] = []
    for rd in find_run_dirs(patterns):
        meta = read_json(rd / "meta.json", {}) or {}
        base = {
            "run_dir": str(rd),
            "arm": meta.get("arm", "?"),
            "model": meta.get("model"),
            "model_slug": meta.get("model_slug", "?"),
            "cell_id": meta.get("cell_id", "?"),
            "cell_type": meta.get("cell_type", "?"),
            "run_idx": meta.get("run_idx"),
        }
        if meta.get("status") != "ok":
            failed.append({**base, "failed_stage": meta.get("failed_stage"), "error": meta.get("error")})
            continue
        score = read_json(rd / "score.json")
        if not isinstance(score, dict) or score.get("status") != "scored":
            unscored.append({**base, "reason": "no score.json" if score is None else score.get("status")})
            continue
        docs = {d: d in score.get("docs", []) for d in ("cv", "letter")}
        issues = score.get("issues", [])
        pages = read_json(rd / "pages.json", {}) or {}
        reports = read_json(rd / "reports.json", {}) or {}
        scored.append({**base, "docs": docs, "issues": issues, "exempt": len(score.get("exempt", [])),
                       "cross_checks": score.get("cross_checks", {}), "oracle": score.get("oracle_agreement", {}),
                       "pages": {d: pages.get(d) for d in ("cv", "letter")},
                       "target_pages": (reports.get("cv") or {}).get("target_pages"),
                       "judge_model": (score.get("instrument") or {}).get("judge_model")})
    return {"scored": scored, "failed": failed, "unscored": unscored}


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #


def _indices(n: int, key: str, *, B: int = RESAMPLES, seed: int = SEED) -> list[list[int]]:
    rng = random.Random(seed ^ zlib.crc32(key.encode("utf-8")))
    return [[rng.randrange(n) for _ in range(n)] for _ in range(B)]


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def ratio_ci(
    nums: list[float], dens: list[float], idxs: list[list[int]], scale: float = 100.0
) -> tuple[float | None, float | None, float | None]:
    """Point estimate and 95 % percentile CI of ``scale * sum(num)/sum(den)``
    with runs resampled by ``idxs``. ``(None, None, None)`` when no documents."""
    tot = sum(dens)
    if tot <= 0:
        return None, None, None
    point = scale * sum(nums) / tot
    vals = []
    for ix in idxs:
        d = sum(dens[i] for i in ix)
        if d > 0:
            vals.append(scale * sum(nums[i] for i in ix) / d)
    vals.sort()
    return point, _pct(vals, 0.025), _pct(vals, 0.975)


def bootstrap_diff(
    a_nums: list[float], a_dens: list[float], b_nums: list[float], b_dens: list[float], key: str,
    *, B: int = RESAMPLES, seed: int = SEED,
) -> dict[str, Any]:
    """``rate(B) - rate(A)`` per 100 documents; A and B resampled independently."""
    ia, ib = _indices(len(a_nums), key + "|A", B=B, seed=seed), _indices(len(b_nums), key + "|B", B=B, seed=seed)
    if sum(a_dens) <= 0 or sum(b_dens) <= 0:
        return {"diff": None, "lo": None, "hi": None, "significant": None}
    diff = 100 * sum(b_nums) / sum(b_dens) - 100 * sum(a_nums) / sum(a_dens)
    vals = []
    for xa, xb in zip(ia, ib):
        da, db = sum(a_dens[i] for i in xa), sum(b_dens[i] for i in xb)
        if da > 0 and db > 0:
            vals.append(100 * sum(b_nums[i] for i in xb) / db - 100 * sum(a_nums[i] for i in xa) / da)
    vals.sort()
    lo, hi = _pct(vals, 0.025), _pct(vals, 0.975)
    return {"diff": diff, "lo": lo, "hi": hi, "significant": bool(lo > 0 or hi < 0)}


# --------------------------------------------------------------------------- #
# Per-run vectors
# --------------------------------------------------------------------------- #


def _count(run: dict[str, Any], cls: str, doc: str) -> int:
    n = 0
    for iss in run["issues"]:
        if cls != "total" and iss["class"] != cls:
            continue
        if doc != "all" and iss["doc"] != doc:
            continue
        n += 1
    return n


def _docs(run: dict[str, Any], doc: str) -> int:
    return sum(1 for d, present in run["docs"].items() if present and doc in ("all", d))


def _docs_with_issue(run: dict[str, Any], cls: str, doc: str) -> int:
    hit = set()
    for iss in run["issues"]:
        if iss["doc"] not in ("cv", "letter"):
            continue
        if cls != "total" and iss["class"] != cls:
            continue
        if doc != "all" and iss["doc"] != doc:
            continue
        hit.add(iss["doc"])
    return len(hit)


def _group_stats(runs: list[dict[str, Any]], key: str) -> dict[str, Any]:
    out: dict[str, Any] = {"runs": len(runs)}
    if not runs:
        return out
    idxs = _indices(len(runs), key)
    for doc in DOC_FILTERS:
        dens = [float(_docs(r, doc)) for r in runs]
        block: dict[str, Any] = {"documents": int(sum(dens))}
        for cls in METRIC_CLASSES:
            nums = [float(_count(r, cls, doc)) for r in runs]
            rate, lo, hi = ratio_ci(nums, dens, idxs)
            hit = [float(_docs_with_issue(r, cls, doc)) for r in runs]
            pct, plo, phi = ratio_ci(hit, dens, idxs)
            block[cls] = {
                "issues": int(sum(nums)), "rate": rate, "lo": lo, "hi": hi,
                "docs_with_issue": int(sum(hit)), "pct_docs": pct, "pct_lo": plo, "pct_hi": phi,
            }
        out[doc] = block
    return out


# --------------------------------------------------------------------------- #
# Page length vs the template budget (US368)
# --------------------------------------------------------------------------- #


def page_budget(run: dict[str, Any], doc: str) -> int | None:
    """The page budget the product itself applies (ADR-051 §1: never a literal here).

    CV: the row's resolved ``target_pages`` (``reports.json``), else the region
    standard; letter: the region's ``letter_pages``. ``None`` when the product's
    norms are not importable (run with ``PYTHONPATH=backend``)."""
    try:
        from applire.norms import DEFAULT_REGION, REGION_NORMS
    except ImportError:
        return None
    norm = REGION_NORMS[DEFAULT_REGION]
    if doc == "cv":
        tp = run.get("target_pages")
        return int(tp) if isinstance(tp, int) and tp > 0 else norm.cv_standard_pages
    return norm.letter_pages


def page_length(runs: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Per document type: rendered pages vs budget — % over budget with a run-bootstrap CI.

    A document whose page count was not measured (``pages.json`` null — no
    browser) is listed as unmeasured and is in no denominator."""
    out: dict[str, Any] = {}
    for doc in ("cv", "letter"):
        present = [r for r in runs if r["docs"].get(doc)]
        measured = [r for r in present if isinstance((r.get("pages") or {}).get(doc), int)]
        budgets = [page_budget(r, doc) for r in measured]
        known = [(r, b) for r, b in zip(measured, budgets) if b is not None]
        over = [float(r["pages"][doc] > b) for r, b in known]
        dens = [1.0] * len(known)
        pct, lo, hi = ratio_ci(over, dens, _indices(len(known), f"{key}|pages|{doc}")) if known else (None, None, None)
        hist = Counter(r["pages"][doc] for r in measured)
        out[doc] = {
            "documents": len(present), "measured": len(measured), "unmeasured": len(present) - len(measured),
            "budget_unknown": len(measured) - len(known),
            "budgets": dict(sorted(Counter(b for _, b in known).items())),
            "over_budget": int(sum(over)), "pct_over": pct, "pct_lo": lo, "pct_hi": hi,
            "mean_pages": (sum(r["pages"][doc] for r in measured) / len(measured)) if measured else None,
            "pages_histogram": {str(k): v for k, v in sorted(hist.items())},
        }
    return out


def aggregate(scored: list[dict[str, Any]]) -> dict[str, Any]:
    arms: dict[str, Any] = {}
    for arm in sorted({r["arm"] for r in scored}):
        runs = [r for r in scored if r["arm"] == arm]
        groups: dict[str, Any] = {"overall": _group_stats(runs, f"{arm}|overall")}
        for ct in sorted({r["cell_type"] for r in runs}):
            groups[f"cell_type:{ct}"] = _group_stats([r for r in runs if r["cell_type"] == ct], f"{arm}|ct|{ct}")
        for m in sorted({r["model_slug"] for r in runs}):
            groups[f"model:{m}"] = _group_stats([r for r in runs if r["model_slug"] == m], f"{arm}|m|{m}")
        hist: dict[str, Counter] = {c: Counter() for c in METRIC_CLASSES}
        for r in runs:
            for iss in r["issues"]:
                hist[iss["class"]][iss.get("stage") or "unattributed"] += 1
                hist["total"][iss.get("stage") or "unattributed"] += 1
        oracle = Counter()
        cross = Counter()
        for r in runs:
            for o in r["oracle"].values():
                if isinstance(o, dict) and o.get("available"):
                    oracle.update(o.get("counts", {}))
            for cc in r["cross_checks"].values():
                if isinstance(cc, dict):
                    for k in ("both", "deterministic_only", "judge_only"):
                        cross[k] += len(cc.get(k, []))
        docs_total = sum(_docs(r, "all") for r in runs)
        arms[arm] = {
            "runs_scored": len(runs),
            "groups": groups,
            "stage_histogram": {c: dict(h.most_common()) for c, h in hist.items()},
            "exempt_per_100_docs": (100 * sum(r["exempt"] for r in runs) / docs_total) if docs_total else None,
            "oracle_agreement": dict(oracle),
            "language_cross_check": dict(cross),
            "page_length": page_length(runs, arm),
            "judge_models": sorted({str(r.get("judge_model")) for r in runs}),
        }
    return arms


def judge_instruments(scored: list[dict[str, Any]], arm: str) -> set[str]:
    return {str(r.get("judge_model")) for r in scored if r["arm"] == arm}


def arm_diffs(
    scored: list[dict[str, Any]], baseline: str | None, *, allow_mixed_judge: bool = False,
    refused: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Per-class arm differences. An arm pair scored by different judge instruments
    is refused (listed in ``refused``) unless ``allow_mixed_judge``: a Haiku-scored
    arm against a Sonnet-scored baseline measures the judge, not the pipeline."""
    arms = sorted({r["arm"] for r in scored})
    if len(arms) < 2:
        return []
    base = baseline if baseline in arms else ("baseline" if "baseline" in arms else arms[0])
    out = []
    for other in arms:
        if other == base:
            continue
        ja, jb = judge_instruments(scored, base), judge_instruments(scored, other)
        if len(ja | jb) > 1 and not allow_mixed_judge:
            if refused is not None:
                refused.append({"arm_a": base, "arm_b": other, "judge_a": sorted(ja), "judge_b": sorted(jb)})
            continue
        a_runs = [r for r in scored if r["arm"] == base]
        b_runs = [r for r in scored if r["arm"] == other]
        scopes = [("overall", lambda r: True)]
        scopes += [(f"cell_type:{ct}", (lambda ct: lambda r: r["cell_type"] == ct)(ct)) for ct in sorted({r["cell_type"] for r in scored})]
        scopes += [(f"model:{m}", (lambda m: lambda r: r["model_slug"] == m)(m)) for m in sorted({r["model_slug"] for r in scored})]
        for name, pred in scopes:
            ar, br = [r for r in a_runs if pred(r)], [r for r in b_runs if pred(r)]
            if not ar or not br:
                continue
            row: dict[str, Any] = {"arm_a": base, "arm_b": other, "scope": name, "runs_a": len(ar), "runs_b": len(br)}
            for cls in METRIC_CLASSES:
                row[cls] = bootstrap_diff(
                    [float(_count(r, cls, "all")) for r in ar], [float(_docs(r, "all")) for r in ar],
                    [float(_count(r, cls, "all")) for r in br], [float(_docs(r, "all")) for r in br],
                    f"diff|{base}|{other}|{name}|{cls}",
                )
            out.append(row)
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _f(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _cell(block: dict[str, Any], untrusted: bool) -> str:
    if untrusted:
        return "instrument not trusted"
    if block.get("rate") is None:
        return "n/a"
    return f"{_f(block['rate'])} [{_f(block['lo'])}-{_f(block['hi'])}] ({_f(block['pct_docs'])}%)"


def _table(group: dict[str, Any], doc: str, untrusted: set[str]) -> str:
    block = group.get(doc)
    if not block:
        return "_no data_"
    head = "| docs | " + " | ".join(f"{c} {CLASS_NAMES[c]}" if c in CLASS_NAMES else c for c in METRIC_CLASSES) + " |"
    sep = "|---|" + "---|" * len(METRIC_CLASSES)
    row = f"| {block['documents']} | " + " | ".join(_cell(block[c], c in untrusted) for c in METRIC_CLASSES) + " |"
    return "\n".join([head, sep, row])


def _page_table(pl: dict[str, Any]) -> str:
    rows = ["| doc | measured | budget (pages: docs) | over budget | % over [95% CI] | mean pages | pages histogram |",
            "|---|---|---|---|---|---|---|"]
    for doc, b in pl.items():
        budgets = ", ".join(f"{k}: {v}" for k, v in b["budgets"].items()) or "unknown"
        pct = "n/a" if b["pct_over"] is None else f"{_f(b['pct_over'])} [{_f(b['pct_lo'])}-{_f(b['pct_hi'])}]"
        hist = ", ".join(f"{k}p: {v}" for k, v in b["pages_histogram"].items()) or "-"
        rows.append(f"| {doc} | {b['measured']}/{b['documents']} | {budgets} | {b['over_budget']} | {pct} | {_f(b['mean_pages'], 2)} | {hist} |")
    return "\n".join(rows)


def render_markdown(report: dict[str, Any]) -> str:
    untrusted = set(report.get("untrusted_classes", []))
    L: list[str] = ["# E066 Document Quality Bench — report", ""]
    L.append(
        "Cells read `issues per 100 documents [95% bootstrap CI over runs] (% of documents with >= 1 issue)`. "
        f"Seed {report['seed']}, {report['resamples']} resamples. Failed and unscored runs are in no denominator."
    )
    if untrusted:
        L.append(f"\n**Instrument not trusted (calibration precision < 0.8): {', '.join(sorted(untrusted))}** — shown as such, not as a number.")
    L += ["", f"Runs: {report['runs']['scored']} scored, {report['runs']['failed']} failed, {report['runs']['unscored']} unscored.", ""]
    for arm, a in report["arms"].items():
        L += [f"## Arm `{arm}` ({a['runs_scored']} scored runs)", "", "### Overall", ""]
        for doc in DOC_FILTERS:
            L += [f"**{doc}**", "", _table(a["groups"]["overall"], doc, untrusted), ""]
        for prefix, title in (("cell_type:", "Per cell type"), ("model:", "Per model")):
            L += [f"### {title}", ""]
            for name, g in a["groups"].items():
                if name.startswith(prefix):
                    L += [f"**{name}** ({g['runs']} runs)", "", _table(g, "all", untrusted), ""]
        L += ["### Stage histogram (where issues are introduced)", ""]
        for cls in METRIC_CLASSES:
            h = a["stage_histogram"].get(cls, {})
            if h:
                L.append(f"- **{cls}**: " + ", ".join(f"`{s}` {n}" for s, n in h.items()))
        L.append("")
        if a["exempt_per_100_docs"] is not None:
            L.append(f"L exempt items (proper nouns, titles, acronyms; O-2): {_f(a['exempt_per_100_docs'])} per 100 documents. ")
        if a["language_cross_check"]:
            L.append(f"L cross-check vs `item_language_mismatch`: {a['language_cross_check']}. ")
        if a["oracle_agreement"]:
            L.append(f"S vs the product's Oracle (items): {a['oracle_agreement']}.")
        L += ["", "### Page length vs template budget", "", _page_table(a["page_length"]), ""]
        L.append(f"Judge model(s): {', '.join(a['judge_models'])}.")
        L.append("")
    if report["arm_diffs"]:
        L += ["## Arm diff (B - A, issues per 100 documents)", "",
              "| A | B | scope | " + " | ".join(METRIC_CLASSES) + " |", "|---|---|---|" + "---|" * len(METRIC_CLASSES)]
        for row in report["arm_diffs"]:
            cells = []
            for cls in METRIC_CLASSES:
                d = row[cls]
                if d["diff"] is None:
                    cells.append("n/a")
                else:
                    cells.append(f"{d['diff']:+.1f} [{_f(d['lo'])}, {_f(d['hi'])}]" + (" *" if d["significant"] else ""))
            L.append(f"| {row['arm_a']} | {row['arm_b']} | {row['scope']} | " + " | ".join(cells) + " |")
        L += ["", "`*` = the interval excludes zero (a difference is reported only then).", ""]
    for x in report.get("arm_diffs_refused") or []:
        L += [f"**Arm diff `{x['arm_a']}` vs `{x['arm_b']}` refused:** scored by different judge instruments "
              f"({', '.join(x['judge_a'])} vs {', '.join(x['judge_b'])}) — re-score one arm with the other's judge env.", ""]
    L += ["## Failed runs (not scored, never counted as zero)", ""]
    if report["failed_runs"]:
        L += ["| arm | model | cell | run | failed stage | error |", "|---|---|---|---|---|---|"]
        for f in report["failed_runs"]:
            L.append(f"| {f['arm']} | {f['model_slug']} | {f['cell_id']} | {f['run_idx']} | {f.get('failed_stage')} | {str(f.get('error') or '')[:120]} |")
    else:
        L.append("none")
    L += ["", "## Runs ok but not scored", ""]
    if report["unscored_runs"]:
        for u in report["unscored_runs"]:
            L.append(f"- {u['arm']}/{u['model_slug']}/{u['cell_id']}/{u['run_idx']}: {u.get('reason')}")
    else:
        L.append("none")
    return "\n".join(L) + "\n"


def build_report(
    patterns: list[str], *, baseline_arm: str | None = None, calibration: str | None = None,
    allow_mixed_judge: bool = False,
) -> dict[str, Any]:
    runs = load_runs(patterns)
    refused: list[dict[str, Any]] = []
    untrusted: list[str] = []
    if calibration:
        cal = read_json(Path(calibration), {}) or {}
        untrusted = sorted(c for c, v in (cal.get("classes") or {}).items() if v.get("trusted") is False)
    return {
        "seed": SEED,
        "resamples": RESAMPLES,
        "runs": {k: len(v) for k, v in runs.items()},
        "failed_runs": runs["failed"],
        "unscored_runs": runs["unscored"],
        "untrusted_classes": untrusted,
        "arms": aggregate(runs["scored"]),
        "arm_diffs": arm_diffs(runs["scored"], baseline_arm, allow_mixed_judge=allow_mixed_judge, refused=refused),
        "arm_diffs_refused": refused,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="run directories / globs / a root holding them")
    ap.add_argument("--out", default=".", help="directory for report.md and report.json")
    ap.add_argument("--baseline-arm", default=None, help="arm A of the diff table (default: 'baseline', else first)")
    ap.add_argument("--calibration", default=None, help="calibration.json from calibrate.py")
    ap.add_argument("--allow-mixed-judge", action="store_true", help="diff arms scored by different judge instruments")
    args = ap.parse_args(argv)
    report = build_report(args.runs, baseline_arm=args.baseline_arm, calibration=args.calibration,
                          allow_mixed_judge=args.allow_mixed_judge)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"report: {report['runs']} -> {out}/report.md, report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
