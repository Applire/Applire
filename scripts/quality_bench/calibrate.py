#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 (calibration) — judge precision / recall against a hand-labelled sample.

Spec 1.5: before any number counts, 20 documents (stratified across cell types)
are labelled by a human independently of the judge; per class the judge's
precision and recall against those labels are reported, and a class whose
judge precision is below 0.8 is reported as **"instrument not trusted"** instead
of as a number.

**Hand-label file** — JSON Lines, two kinds of line::

    {"kind": "doc",   "run": "baseline/luna/same_it_backend_daniel/0", "doc": "cv"}
    {"kind": "issue", "run": "baseline/luna/same_it_backend_daniel/0", "doc": "cv",
     "class": "L", "loc": "cv.skills[3]", "text": "Machine Learning", "note": "English chip in a German CV"}

* a ``doc`` line declares that this document of this run is **fully labelled**:
  every defect in it is listed as an ``issue`` line, so an unlisted judge
  finding on it is a false positive and an unlisted defect a miss. A document
  with no defects is a ``doc`` line and nothing else.
* an ``issue`` line is one defect the human marks (``class`` L/U/D/S; ``loc`` as
  in the score file, or the vault path for a U-omit; ``text`` helps matching).
  ``doc`` may be ``"both"`` for a vault fact omitted from both documents (counted
  only for runs whose delivered documents are all labelled).
* ``run`` is the run id ``<arm>/<model_slug>/<cell_id>/<run_idx>`` (any unique
  path suffix of the run directory also works). Lines are matched against each
  run's ``score.json`` — only labelled documents enter the numbers.

A judge issue matches a human issue of the same run / document / class when the
``loc`` is equal, or the texts overlap (normalised containment, or similarity
>= 0.7), or — class D — the human's loc is a member of the judge's group.
Matching is one-to-one.

Writes ``calibration.json`` (consumed by ``report.py --calibration``).

Usage::

    python3 scripts/quality_bench/calibrate.py labels.jsonl 'out/baseline/*/*/*' --out calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from .score.common import CLASSES
    from .score.inputs import find_run_dirs, read_json
    from .score.items import norm
except ImportError:  # executed as a script
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from score.common import CLASSES  # type: ignore[no-redef]
    from score.inputs import find_run_dirs, read_json  # type: ignore[no-redef]
    from score.items import norm  # type: ignore[no-redef]

PRECISION_FLOOR = 0.8
LOW_SUPPORT = 5


def load_labels(path: Path) -> tuple[set[tuple[str, str]], list[dict[str, Any]]]:
    docs: set[tuple[str, str]] = set()
    issues: list[dict[str, Any]] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rec = json.loads(line)
        kind = rec.get("kind")
        if kind == "doc":
            docs.add((rec["run"], rec["doc"]))
        elif kind == "issue":
            if rec.get("class") not in CLASSES:
                raise ValueError(f"{path}:{n}: class must be one of {CLASSES}")
            issues.append(rec)
        else:
            raise ValueError(f"{path}:{n}: unknown kind {kind!r}")
    return docs, issues


def _texts_match(a: str, b: str) -> bool:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= 0.7


def matches(human: dict[str, Any], judge: dict[str, Any]) -> bool:
    if human.get("loc") and human["loc"] == judge.get("loc"):
        return True
    if human.get("text") and _texts_match(str(human["text"]), str(judge.get("text") or "")):
        return True
    ev = judge.get("evidence") if isinstance(judge.get("evidence"), dict) else {}
    return judge.get("class") == "D" and human.get("loc") in (ev.get("group_locs") or [])


def _run_index(run_dirs: list[Path]) -> dict[str, dict[str, Any]]:
    """run id (and every path suffix of the dir) -> {"score": ..., "docs": [...]}."""
    index: dict[str, dict[str, Any]] = {}
    for rd in run_dirs:
        score = read_json(rd / "score.json")
        if not isinstance(score, dict) or score.get("status") != "scored":
            continue
        entry = {"score": score, "dir": str(rd)}
        index[score["run_id"]] = entry
        parts = rd.resolve().parts
        for k in range(1, min(5, len(parts)) + 1):
            index.setdefault("/".join(parts[-k:]), entry)
    return index


def calibrate(labels_path: Path | str, run_patterns: list[str]) -> dict[str, Any]:
    label_docs, label_issues = load_labels(Path(labels_path))
    index = _run_index(find_run_dirs(run_patterns))

    def resolve(run: str) -> dict[str, Any] | None:
        return index.get(run)

    # which (run, doc) pairs are evaluable
    labelled: dict[str, set[str]] = {}
    unresolved: set[str] = set()
    for run, doc in label_docs:
        entry = resolve(run)
        if entry is None:
            unresolved.add(run)
            continue
        labelled.setdefault(entry["score"]["run_id"], set()).add(doc)
    both_ok = {rid for rid, docs in labelled.items()
               if set(resolve(rid)["score"]["docs"]) <= docs}  # type: ignore[index]

    def in_scope(run_id: str, doc: str) -> bool:
        if doc == "both":
            return run_id in both_ok
        return doc in labelled.get(run_id, set())

    out: dict[str, Any] = {
        "precision_floor": PRECISION_FLOOR,
        "documents_labelled": sum(len(v) for v in labelled.values()),
        "runs_labelled": len(labelled),
        "unresolved_runs": sorted(unresolved),
        "classes": {},
    }
    human_by_class: dict[str, list[dict[str, Any]]] = {c: [] for c in CLASSES}
    for h in label_issues:
        entry = resolve(h["run"])
        if entry is None:
            unresolved.add(h["run"])
            continue
        rid = entry["score"]["run_id"]
        if in_scope(rid, h.get("doc", "")):
            human_by_class[h["class"]].append({**h, "_rid": rid})
        else:
            out.setdefault("ignored_labels_outside_labelled_docs", 0)
            out["ignored_labels_outside_labelled_docs"] += 1
    out["unresolved_runs"] = sorted(unresolved)

    for cls in CLASSES:
        judge_issues = [
            {**i, "_rid": rid}
            for rid in labelled
            for i in resolve(rid)["score"]["issues"]  # type: ignore[index]
            if i["class"] == cls and in_scope(rid, i["doc"])
        ]
        humans = human_by_class[cls]
        used: set[int] = set()
        tp = 0
        fp_list = []
        for j in judge_issues:
            hit = next(
                (k for k, h in enumerate(humans)
                 if k not in used and h["_rid"] == j["_rid"] and h.get("doc") == j["doc"] and matches(h, j)),
                None,
            )
            if hit is None:
                fp_list.append({"run": j["_rid"], "doc": j["doc"], "loc": j["loc"], "text": j["text"]})
            else:
                used.add(hit)
                tp += 1
        fn_list = [{"run": h["_rid"], "doc": h.get("doc"), "loc": h.get("loc"), "text": h.get("text")}
                   for k, h in enumerate(humans) if k not in used]
        n_j, n_h = len(judge_issues), len(humans)
        precision = tp / n_j if n_j else None
        recall = tp / n_h if n_h else None
        if precision is None:
            trusted, status = None, "no judge findings on the labelled documents (precision undefined)"
        elif precision < PRECISION_FLOOR:
            trusted, status = False, "instrument not trusted"
        else:
            trusted, status = True, "trusted"
        out["classes"][cls] = {
            "tp": tp, "fp": len(fp_list), "fn": len(fn_list),
            "n_judge": n_j, "n_human": n_h,
            "precision": precision, "recall": recall,
            "trusted": trusted, "status": status, "low_support": n_j < LOW_SUPPORT,
            "false_positives": fp_list, "misses": fn_list,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("labels", help="hand-label JSONL")
    ap.add_argument("runs", nargs="+", help="scored run directories / globs")
    ap.add_argument("--out", default="calibration.json")
    args = ap.parse_args(argv)
    res = calibrate(args.labels, args.runs)
    Path(args.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for cls, c in res["classes"].items():
        p = "n/a" if c["precision"] is None else f"{c['precision']:.2f}"
        r = "n/a" if c["recall"] is None else f"{c['recall']:.2f}"
        print(f"{cls}: precision {p} recall {r} (tp {c['tp']} fp {c['fp']} fn {c['fn']}) -> {c['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
