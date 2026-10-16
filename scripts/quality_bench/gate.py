#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US368 — the pipeline gate: arm A (v1) vs arm B (v2) per class, on paired cells.

The E066 report compares arms with independent run resampling. The gate pairs
by **cell** (``model_slug`` + ``cell_id``): both arms ran the same frozen
vault + posting + model, so the cell is the unit of the comparison and the
cell-to-cell spread (a cross cell carries more L than a same cell) cancels
instead of widening the interval. Bootstrap, two levels, fixed seed:
resample cells with replacement (the same cells for A and B), then resample
runs inside each drawn cell independently per arm. Per class the gate reports
``rate(B) - rate(A)`` in issues per 100 documents with its 95 % interval, and
the same for

* **failed runs** — a failed run is in no issue denominator (E066), so a v2
  that fails more often could look cleaner; the gate shows the failed-run
  rate per 100 runs beside the classes;
* **page length** — % of documents over the template budget, per document type
  (``report.page_length``; the budget is the product's own, ADR-051 §1).

**The #778 criterion** (``us368``): L, D and S better, U no worse, page length
held, failed runs no worse — evaluated in two readings of "better" (the paired
difference interval below zero, and #778's literal "the arms' intervals do not
overlap"); both are shown, neither is chosen here.

**Verdict per row:** ``regressed`` when the interval lies above zero,
``improved`` when it lies below, else ``no difference``. A class the
calibration file marks untrusted, or listed with ``--trend``, is shown as a
**trend** (E066 §Calibration ruling: U is a trend until test-retest holds) and
never decides the mechanical verdict. The mechanical verdict ``no regression``
means: no decisive row regressed. Whether that flips a default is the
founder's call — the gate only provides the numbers.

**Instrument guard:** both arms must be scored by ONE judge model
(``score.json`` ``instrument.judge_model``). A Haiku-scored arm against a
Sonnet-scored baseline measures the judge, not the pipeline; the gate refuses
it (exit 2) unless ``--allow-mixed-judge``.

Usage::

    PYTHONPATH=backend python3 scripts/quality_bench/gate.py \\
        --a 'out/baseline/openai-gpt-5.6-luna/*/*' --b 'out/v2/openai-gpt-5.6-luna/*/*' \\
        --calibration calibration.json --out gate/
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from . import report as rep
    from .score.common import CLASSES
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import report as rep  # type: ignore[no-redef]
    from score.common import CLASSES  # type: ignore[no-redef]

SEED = 20261016
RESAMPLES = 2000
DECISIVE_DEFAULT_TREND = ("U",)


class GateError(RuntimeError):
    pass


def _cell_key(r: dict[str, Any]) -> str:
    return f"{r['model_slug']}|{r['cell_id']}"


def _by_cell(runs: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        out[_cell_key(r)].append(r)
    return out


def paired_diff(
    a: dict[str, list[tuple[float, float]]],
    b: dict[str, list[tuple[float, float]]],
    key: str,
    *,
    scale: float = 100.0,
    B: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, Any]:
    """``scale * (sum num_B / sum den_B - sum num_A / sum den_A)`` over the cells
    present in both arms; per cell a list of ``(num, den)`` run vectors.

    Two-level bootstrap: cells resampled (shared), runs within a cell resampled
    per arm. Deterministic for a given ``key``."""
    cells = sorted(set(a) & set(b))
    if not cells:
        return {"diff": None, "lo": None, "hi": None, "cells": 0, "a": None, "b": None}

    def rate(arm: dict[str, list[tuple[float, float]]], picks: list[tuple[str, list[int]]]) -> float | None:
        num = den = 0.0
        for c, ix in picks:
            for i in ix:
                n, d = arm[c][i]
                num += n
                den += d
        return scale * num / den if den > 0 else None

    full_a = [(c, list(range(len(a[c])))) for c in cells]
    full_b = [(c, list(range(len(b[c])))) for c in cells]
    ra, rb = rate(a, full_a), rate(b, full_b)
    if ra is None or rb is None:
        return {"diff": None, "lo": None, "hi": None, "cells": len(cells), "a": ra, "b": rb}
    rng = random.Random(seed ^ zlib.crc32(key.encode("utf-8")))
    vals: list[float] = []
    xs: list[float] = []
    ys: list[float] = []
    for _ in range(B):
        drawn = [cells[rng.randrange(len(cells))] for _ in cells]
        pa = [(c, [rng.randrange(len(a[c])) for _ in a[c]]) for c in drawn]
        pb = [(c, [rng.randrange(len(b[c])) for _ in b[c]]) for c in drawn]
        x, y = rate(a, pa), rate(b, pb)
        if x is not None and y is not None:
            vals.append(y - x)
            xs.append(x)
            ys.append(y)
    vals.sort()
    xs.sort()
    ys.sort()
    lo, hi = rep._pct(vals, 0.025), rep._pct(vals, 0.975)
    a_ci = (rep._pct(xs, 0.025), rep._pct(xs, 0.975))
    b_ci = (rep._pct(ys, 0.025), rep._pct(ys, 0.975))
    return {"diff": rb - ra, "lo": lo, "hi": hi, "cells": len(cells), "a": ra, "b": rb,
            "a_ci": list(a_ci), "b_ci": list(b_ci), "arm_cis_overlap": not (b_ci[1] < a_ci[0] or b_ci[0] > a_ci[1])}


def verdict(d: dict[str, Any]) -> str:
    if d.get("diff") is None:
        return "n/a"
    if d["lo"] > 0:
        return "regressed"
    if d["hi"] < 0:
        return "improved"
    return "no difference"


#: US368 (#778): "v2 better on language mix, duplicates and upselling, underselling
#: no worse, page length held". The failed-run row is added: a failed run is in no
#: issue denominator, so without it "better" could mean "fails more".
MUST_IMPROVE = ("L", "D", "S")
MUST_NOT_REGRESS = ("U",)


def us368_criterion(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The #778 gate, evaluated in two readings of "better".

    * ``diff_ci`` — the E066 rule: the 95 % interval of the paired difference lies
      below zero (the statistically matching reading);
    * ``literal`` — #778's wording: the two arms' own 95 % intervals do not
      overlap, B below A (stricter).
    "No worse" / "held" is the same in both readings: the difference interval
    does not lie above zero."""
    by = {(r["metric"], r["doc"]): r for r in rows}
    out: dict[str, Any] = {}
    for reading in ("diff_ci", "literal"):
        checks: dict[str, bool] = {}
        for cls in MUST_IMPROVE:
            r = by[(cls, "all")]
            if r["diff"] is None:
                checks[f"{cls} better"] = False
            elif reading == "diff_ci":
                checks[f"{cls} better"] = r["verdict"] == "improved"
            else:
                checks[f"{cls} better"] = (not r["arm_cis_overlap"]) and r["diff"] < 0
        for cls in MUST_NOT_REGRESS:
            checks[f"{cls} no worse"] = by[(cls, "all")]["verdict"] != "regressed"
        for doc in ("cv", "letter"):
            checks[f"page length held ({doc})"] = by[("over_page_budget", doc)]["verdict"] != "regressed"
        checks["failed runs no worse"] = by[("failed_runs", "runs")]["verdict"] != "regressed"
        out[reading] = {"pass": all(checks.values()), "checks": checks}
    return out


def _judge_models(runs: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("judge_model")) for r in runs}


def build_gate(
    a_patterns: list[str],
    b_patterns: list[str],
    *,
    calibration: str | None = None,
    trend: tuple[str, ...] | None = None,
    allow_mixed_judge: bool = False,
) -> dict[str, Any]:
    A, Bk = rep.load_runs(a_patterns), rep.load_runs(b_patterns)
    ja, jb = _judge_models(A["scored"]), _judge_models(Bk["scored"])
    mixed = len(ja | jb) > 1
    if mixed and not allow_mixed_judge:
        raise GateError(f"arms scored by different judge models: A {sorted(ja)} vs B {sorted(jb)} — re-score one arm")
    trend_classes = set(trend if trend is not None else DECISIVE_DEFAULT_TREND)
    if calibration:
        cal = rep.read_json(Path(calibration), {}) or {}
        trend_classes |= {c for c, v in (cal.get("classes") or {}).items() if v.get("trusted") is False}

    sa, sb = _by_cell(A["scored"]), _by_cell(Bk["scored"])
    all_a = _by_cell(A["scored"] + A["failed"])
    all_b = _by_cell(Bk["scored"] + Bk["failed"])
    paired = sorted(set(all_a) & set(all_b))
    rows: list[dict[str, Any]] = []

    for doc in ("all", "cv", "letter"):
        for cls in (*CLASSES, "total"):
            va = {c: [(float(rep._count(r, cls, doc)), float(rep._docs(r, doc))) for r in sa[c]] for c in sa}
            vb = {c: [(float(rep._count(r, cls, doc)), float(rep._docs(r, doc))) for r in sb[c]] for c in sb}
            d = paired_diff(va, vb, f"{cls}|{doc}")
            is_trend = cls in trend_classes or (cls == "total" and bool(trend_classes))
            rows.append({"metric": cls, "doc": doc, **d, "verdict": verdict(d), "decisive": not is_trend})

    fa = {c: [(float(r["arm_failed"]), 1.0) for r in runs] for c, runs in _flag_failed(all_a).items()}
    fb = {c: [(float(r["arm_failed"]), 1.0) for r in runs] for c, runs in _flag_failed(all_b).items()}
    d = paired_diff(fa, fb, "failed_runs")
    rows.append({"metric": "failed_runs", "doc": "runs", **d, "verdict": verdict(d), "decisive": True})

    for doc in ("cv", "letter"):
        def over(arm: dict[str, list[dict[str, Any]]]) -> dict[str, list[tuple[float, float]]]:
            out: dict[str, list[tuple[float, float]]] = {}
            for c, runs in arm.items():
                vec = []
                for r in runs:
                    n = (r.get("pages") or {}).get(doc)
                    b = rep.page_budget(r, doc)
                    if r["docs"].get(doc) and isinstance(n, int) and b is not None:
                        vec.append((float(n > b), 1.0))
                if vec:
                    out[c] = vec
            return out
        d = paired_diff(over(sa), over(sb), f"pages|{doc}")
        rows.append({"metric": "over_page_budget", "doc": doc, **d, "verdict": verdict(d), "decisive": True})

    regressed = [f"{r['metric']}:{r['doc']}" for r in rows if r["decisive"] and r["verdict"] == "regressed"]
    return {
        "seed": SEED,
        "resamples": RESAMPLES,
        "judge_models": {"a": sorted(ja), "b": sorted(jb), "mixed_allowed": mixed and allow_mixed_judge},
        "cells": {"paired": len(paired), "only_a": sorted(set(all_a) - set(all_b)), "only_b": sorted(set(all_b) - set(all_a))},
        "runs": {"a": {k: len(v) for k, v in A.items()}, "b": {k: len(v) for k, v in Bk.items()}},
        "trend_classes": sorted(trend_classes),
        "rows": rows,
        "page_length": {"a": rep.page_length(A["scored"], "gate|a"), "b": rep.page_length(Bk["scored"], "gate|b")},
        "mechanical_verdict": "regression" if regressed else "no regression",
        "us368": us368_criterion(rows),
        "regressed": regressed,
    }


def _flag_failed(cells: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Mark each run failed (1) or not (0); a scored record has ``issues``, a failed one not."""
    return {c: [{**r, "arm_failed": "issues" not in r} for r in runs] for c, runs in cells.items()}


def render_markdown(g: dict[str, Any]) -> str:
    f = rep._f
    L = ["# US368 gate — arm A vs arm B on paired cells", ""]
    L.append(f"Judge: A {', '.join(g['judge_models']['a'])} · B {', '.join(g['judge_models']['b'])}. "
             f"Paired cells: {g['cells']['paired']} (only A: {len(g['cells']['only_a'])}, only B: {len(g['cells']['only_b'])}). "
             f"Two-level bootstrap (cells shared, runs per arm), seed {g['seed']}, {g['resamples']} resamples.")
    L.append(f"Runs A: {g['runs']['a']} · B: {g['runs']['b']}.")
    L.append(f"Trend only (never decides the verdict): {', '.join(g['trend_classes']) or 'none'}.")
    L += ["", f"**Mechanical verdict: {g['mechanical_verdict']}**" + (f" — regressed: {', '.join(g['regressed'])}" if g["regressed"] else ""), ""]
    for reading, title in (("diff_ci", "difference CI excludes zero (E066 rule)"), ("literal", "arm CIs do not overlap (#778 wording)")):
        c = g["us368"][reading]
        L.append(f"**US368 criterion — {title}: {'PASS' if c['pass'] else 'FAIL'}** — "
                 + ", ".join(f"{k} {'yes' if v else 'NO'}" for k, v in c["checks"].items()))
        L.append("")
    L += ["| metric | doc | A [95% CI] | B [95% CI] | B − A [95% CI] | verdict |", "|---|---|---|---|---|---|"]
    for r in g["rows"]:
        unit = "per 100 runs" if r["metric"] == "failed_runs" else ("% docs" if r["metric"] == "over_page_budget" else "per 100 docs")
        ci = "n/a" if r["diff"] is None else f"{r['diff']:+.1f} [{f(r['lo'])}, {f(r['hi'])}]"
        v = r["verdict"] + ("" if r["decisive"] else " (trend)")
        ac = f" [{f(r['a_ci'][0])}, {f(r['a_ci'][1])}]" if r.get("a_ci") else ""
        bc = f" [{f(r['b_ci'][0])}, {f(r['b_ci'][1])}]" if r.get("b_ci") else ""
        L.append(f"| {r['metric']} ({unit}) | {r['doc']} | {f(r['a'])}{ac} | {f(r['b'])}{bc} | {ci} | {v} |")
    L += ["", "## Page length vs template budget", "", "**A**", "", rep._page_table(g["page_length"]["a"]), "",
          "**B**", "", rep._page_table(g["page_length"]["b"]), ""]
    if g["cells"]["only_a"] or g["cells"]["only_b"]:
        L += ["## Unpaired cells (not in the comparison)", "", f"- only A: {g['cells']['only_a']}", f"- only B: {g['cells']['only_b']}", ""]
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", nargs="+", required=True, help="arm A (v1) run dirs / globs")
    ap.add_argument("--b", nargs="+", required=True, help="arm B (v2) run dirs / globs")
    ap.add_argument("--calibration", default=None, help="calibration.json: untrusted classes become trend-only")
    ap.add_argument("--trend", default=None, help="comma list of trend-only classes (default: U)")
    ap.add_argument("--allow-mixed-judge", action="store_true", help="compare arms scored by different judges (not a gate)")
    ap.add_argument("--out", default=".", help="directory for gate.md and gate.json")
    args = ap.parse_args(argv)
    trend = tuple(c for c in args.trend.split(",") if c) if args.trend is not None else None
    try:
        g = build_gate(args.a, args.b, calibration=args.calibration, trend=trend, allow_mixed_judge=args.allow_mixed_judge)
    except GateError as exc:
        print(f"gate refused: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "gate.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "gate.md").write_text(render_markdown(g), encoding="utf-8")
    print(f"gate: {g['mechanical_verdict']} {g['regressed']} -> {out}/gate.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
