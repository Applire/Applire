# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 — score bench run directories: four class judges + attribution.

Reads each run directory per the run-output contract (``meta.json``,
``cv.json``, ``letter.json``, ``reports.json``, ``llm.jsonl`` and the frozen
fixture named by ``meta["fixture_dir"]``), runs the L / U / D / S scorers over
the DELIVERED documents, attributes every issue to the stage that introduced it
and writes ``<run>/score.json``. Failed runs are not scored: they are reported
as failed by ``report.py``, never as zero issues.

Usage (PYTHONPATH=backend so the product's ``item_language_mismatch`` is
available as the L cross-check)::

    # what would the judges be asked? — no judge call, token estimate only
    PYTHONPATH=backend python3 scripts/quality_bench/score/score_run.py --dry-run RUN_DIR...

    # score (spends judge credit; cached on disk, re-scoring is free)
    JUDGE_MODEL=<openrouter id> OPENROUTER_API_KEY=... \\
      PYTHONPATH=backend python3 scripts/quality_bench/score/score_run.py 'out/baseline/*/*/*'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # executed as a script: make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "score"
    import score  # noqa: F401

from . import attribution, drafts as drafts_mod, inputs
from .common import CLASSES
from .duplicates import score_duplicates
from .items import flatten_cv, flatten_letter, index_by_id, select

#: Duplicate structures inside the founder's D class (Q-1).
D_STRUCTURES = frozenset({"skill~skill", "bullet~bullet"})


def render_view(cv: dict, lang: str | None) -> dict:
    """The CV as the renderer sees it — the product's own render-context steps."""
    from applire.schemas.cv import TailoredCVData
    from applire.services.cv import localize_languages, strip_empty_projects

    try:
        data = TailoredCVData.model_validate(cv)
    except Exception:  # noqa: BLE001 — a row the schema rejects is scored as stored
        return cv
    data = localize_languages(strip_empty_projects(data), lang)
    out = dict(cv)
    out.update(data.model_dump(mode="json", include={"languages", "work_history", "projects"}))
    return out
from .judge import FakeJudge, JudgeClient, JudgeError
from .language import score_language
from .underselling import find_removals, score_underselling
from .upselling import oracle_agreement, score_upselling

REPO_ROOT = Path(__file__).resolve().parents[3]
SCORE_VERSION = "1"


def _delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, Any]:
    keys = ("calls", "cache_hits", "retries", "prompt_tokens", "completion_tokens")
    return {k: after.get(k, 0) - before.get(k, 0) for k in keys}


def score_run(
    run_dir: Path | str,
    judge: Any,
    *,
    include_llm_stage: bool = False,
    fixture_bases: list[Path] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    run = inputs.load_run(run_dir)
    base_stats = judge.stats.snapshot()
    result: dict[str, Any] = {
        "score_version": SCORE_VERSION,
        "run_id": run.run_id,
        "meta": run.meta,
        "status": "scored",
        "docs": [],
        "issues": [],
        "exempt": [],
        "counts": {c: {"cv": 0, "letter": 0, "both": 0} for c in CLASSES},
        "cross_checks": {},
        "oracle_agreement": {},
        "details": {},
        "warnings": [],
    }
    if not run.ok:
        result["status"] = "not_scored_failed_run"
        return result

    fx = inputs.load_fixture(run.meta, fixture_bases or [Path.cwd(), REPO_ROOT, run.run_dir])
    result["warnings"].extend(fx.warnings)
    effective = inputs.read_json(run.run_dir / "ledger_effective.json")
    if isinstance(effective, list) and effective:
        fx.ledger = [r for r in effective if isinstance(r, dict)]
    lang = run.language

    # Score what reaches the PAGE, not the persisted row: the product applies its
    # render-context steps (``strip_empty_projects``, ``localize_languages`` — cv.py
    # ``get_cv_html``) at every renderer, never in ``tailored_data`` (ADR-067). The
    # pilot (2026-10-12) counted "Deutsch · Muttersprache" in an English CV as a
    # language mix although the page reads "German · Native".
    page_cv = render_view(run.cv, lang) if run.cv else None
    cv_items = flatten_cv(page_cv) if page_cv else []
    letter_items = flatten_letter(run.letter, sentences=True) if run.letter else []
    docs = [d for d, payload in (("cv", run.cv), ("letter", run.letter)) if payload]
    result["docs"] = docs
    if not docs:
        result["status"] = "not_scored_no_documents"
        result["warnings"].append("run is ok but delivered neither cv.json nor letter.json")
        return result

    records = drafts_mod.load_records(run.run_dir / "llm.jsonl")
    drafts = drafts_mod.extract_drafts(records, index_by_id(run.cv) if run.cv else None)
    items_by_doc = {"cv": cv_items, "letter": letter_items}
    issues: list[dict[str, Any]] = []

    # L ---------------------------------------------------------------------
    for doc in docs:
        l = score_language(judge, doc, items_by_doc[doc], lang)
        issues += l["issues"]
        result["exempt"] += l["exempt"]
        result["cross_checks"][f"L:{doc}"] = l["cross_check"]
        result["details"][f"L:{doc}"] = {k: l[k] for k in ("dropped_unknown_loc", "items_judged")}

    # D ---------------------------------------------------------------------
    for doc in docs:
        # Founder definition Q-1 (2026-10-12): a duplicate is the same competence or
        # fact listed twice in different wording — within the skills list, or the same
        # achievement in two bullets. A summary that restates a bullet, and a letter
        # that repeats a fact, are not in the class.
        if doc != "cv":
            continue
        pool = select(cv_items, {"bullet", "skill"})
        d = score_duplicates(judge, doc, pool, lang)
        d["issues"] = [i for i in d["issues"] if i.get("subclass") in D_STRUCTURES]
        issues += d["issues"]
        result["details"][f"D:{doc}"] = {"groups": d["groups"], "candidates": d["candidates"], "dropped_unknown_loc": d["dropped_unknown_loc"]}

    # S and U need the vault ---------------------------------------------------
    if not fx.vault:
        result["warnings"].append("vault unavailable: S and U not scored")
        result["classes_skipped"] = ["S", "U"]
    else:
        for doc in docs:
            s = score_upselling(judge, doc, items_by_doc[doc], lang, fx.vault)
            issues += s["issues"]
            report = (run.reports.get(doc if doc == "cv" else "letter") or {}).get("truthfulness_report")
            result["oracle_agreement"][doc] = oracle_agreement(doc, items_by_doc[doc], report, s["issues"])
            result["details"][f"S:{doc}"] = {k: s[k] for k in ("claims_judged", "dropped_unknown_loc", "dropped_bad_kind")}

        removals: list[dict[str, Any]] = []
        for doc in docs:
            removals += find_removals(doc, drafts[doc], items_by_doc[doc], include_llm_stage=include_llm_stage)
            if not drafts[doc]:
                result["warnings"].append(f"no model draft captured for {doc}: U-del skipped for it")
        u = score_underselling(
            judge, language=lang, vault=fx.vault, job=fx.job, ledger=fx.ledger,
            cv_items=cv_items, letter_items=letter_items, removals=removals,
        )
        issues += u["issues"]
        result["details"]["U"] = {
            "removals": [{k: v for k, v in r.items()} for r in u["removals"]],
            "removals_truncated": u["removals_truncated"],
            "removals_unjudged": u["removals_unjudged"],
            "dropped_unknown_id": u["dropped_unknown_id"],
            "dropped_unknown_loc": u["dropped_unknown_loc"],
            "dropped_unknown_vault_ref": u["dropped_unknown_vault_ref"],
        }

    attribution.attribute_issues(issues, drafts)
    for iss in issues:
        iss.pop("removal", None)  # full removal records live in details["U"]
        result["counts"][iss["class"]][iss["doc"]] += 1
    result["issues"] = issues
    result["instrument"] = {"judge_model": getattr(judge, "model", None), **_delta(judge.stats.snapshot(), base_stats)}
    result["drafts_captured"] = {d: [x.label for x in drafts[d]] for d in ("cv", "letter")}

    if write:
        (run.run_dir / "score.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="+", help="run directories or globs (a dir without meta.json is searched recursively)")
    ap.add_argument("--dry-run", action="store_true", help="build every prompt, call no judge, print a token estimate")
    ap.add_argument("--llm-removals", action="store_true", help="also list true content that a corrector dropped (dropped_by_llm)")
    ap.add_argument("--cache-dir", default=None, help="judge cache directory (default ./.quality-bench-judge-cache)")
    args = ap.parse_args(argv)

    run_dirs = inputs.find_run_dirs(args.runs)
    if not run_dirs:
        print("no run directories found", file=sys.stderr)
        return 2
    try:
        judge: Any = FakeJudge() if args.dry_run else JudgeClient(cache_dir=args.cache_dir)
    except JudgeError as exc:
        print(f"judge configuration error: {exc}", file=sys.stderr)
        return 2
    n_scored = 0
    for rd in run_dirs:
        res = score_run(rd, judge, include_llm_stage=args.llm_removals, write=not args.dry_run)
        n = {c: sum(v.values()) for c, v in res["counts"].items()}
        print(f"{res['run_id']}: {res['status']} issues={n}")
        n_scored += res["status"] == "scored"
    stats = judge.stats.snapshot()
    print(f"scored {n_scored}/{len(run_dirs)} runs; judge stats: {json.dumps({k: v for k, v in stats.items() if k != 'by_class'})}")
    if args.dry_run and n_scored:
        print(f"estimated judge prompt tokens per scored run: {stats['prompt_tokens'] // n_scored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
