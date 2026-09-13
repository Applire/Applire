#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""M5.1.2 / M5.1.3 — the opt-in probe for the two CV-ingestion doors' prompt rules.

The model-matrix harness (``scripts/model_matrix.py``) measures the reconcile
seam. This is its sibling for the EXTRACTION seam: it calls one door's prompt
N times on one committed synthetic fixture and reports the property the rule
under test is about, so a prompt edit has a before/after number instead of an
argument.

**Opt-in.** Never a CI job — it needs a provider key. CI runs only the hermetic
smoke test (``tests/unit/test_extraction_probe.py``).

Two probes, matching the two fixtures in ``tests/files/extraction_parity/``:

* ``per_entry_tech`` — does a general ``KENNTNISSE`` item get backfilled onto a
  role whose own text never names it (#407), and does the sub-role inside a
  bullet become a third, company-less ``work_history`` entry?
* ``cert_issuer`` — is ``issuing_organization`` invented for a certification
  whose source line states no issuer?
* ``review_per_entry_tech`` — ruling E-1's question, one layer later: replayed
  over CAPTURED extractions (one that backfills, one that does not), does
  ``review_profile_extraction``'s check 5 BLOCK the violating one and name the
  role and the tool — without blocking the clean one, which would cost a
  memoryless regeneration of a correct extraction for nothing.

Both doors can be probed, because both are the same defect wearing two schemas:
``--door agent`` runs ``prompts/profile_extraction.py`` (MCP ``import_cv`` /
paste / LinkedIn), ``--door web`` runs ``prompts/cv_extraction.py`` (the
browser upload).

Usage::

    PYTHONPATH=backend python3 scripts/extraction_probe.py --dry-run --probe per_entry_tech

    env $(grep -E '^(OPENROUTER_API_KEY|LLM_TIMEOUT)=' .env | xargs) \
      LLM_PROVIDER=openrouter DATABASE_URL=sqlite+aiosqlite:///:memory: \
      PYTHONPATH=backend python3 scripts/extraction_probe.py \
      --probe per_entry_tech --door agent --model openai/gpt-5.6-luna --n 5 \
      --out runs/item7-before.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "files" / "extraction_parity"

#: probe -> (fixture file, the role whose technologies must stay clean, the
#: tokens that must not be backfilled onto it).
PROBES: dict[str, dict[str, Any]] = {
    "per_entry_tech": {
        "fixture": "multi_employer_kenntnisse.txt",
        "clean_company": "kaltenbach",
        "owning_company": "rheinstahl",
        "forbidden_tokens": ("sap", "excel"),
        "expected_entries": 2,
    },
    # The same shape with the general skills section FIRST and the clean role
    # carrying a technologies list of its OWN (Proficy, Grafana) — so the model
    # is not choosing between "a tool" and "no tools at all" but between the
    # role's own stack and a longer list that would swallow it. The soft version
    # measured 0/10 violations on two models, which is only informative if the
    # instrument can see a violation at all.
    "per_entry_tech_hard": {
        "fixture": "multi_employer_kenntnisse_hard.txt",
        "clean_company": "kaltenbach",
        "owning_company": "rheinstahl",
        "forbidden_tokens": ("sap", "excel"),
        "expected_entries": 2,
    },
    # E-1 (2026-09-13): the REVIEWER half of the same defect. The extraction
    # probes above ask "does the model backfill?"; this one asks "does the
    # door's second chance catch it?" — because ruling E-1 dispositioned #407's
    # measured residue on `review_profile_extraction.py` rather than on the
    # extraction rule the model ignores. It replays the reviewer over CAPTURED
    # extractions (build-2 `p2/runs/P7-after-hard-ministral.jsonl`, committed as
    # fixtures) instead of re-extracting, so the positive case is guaranteed to
    # contain the defect and the negative case guaranteed not to — an extraction
    # that came out clean would otherwise silently measure nothing.
    "review_per_entry_tech": {
        "kind": "review",
        "fixture": "multi_employer_kenntnisse_hard.txt",
        "cases": {
            "violating": "multi_employer_kenntnisse_hard.violating.json",
            "clean": "multi_employer_kenntnisse_hard.clean.json",
        },
        "clean_company": "kaltenbach",
        "forbidden_tokens": ("sap", "excel"),
    },
    "cert_issuer": {
        "fixture": "certification_heavy.txt",
        # The two source lines that DO state an issuer; anything else carrying
        # an `issuing_organization` invented one.
        "stated_issuers": ("ihk koblenz", "tuev rheinland", "tüv rheinland"),
        "expected_certs": 5,
    },
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def build_prompt(door: str, raw_text: str) -> tuple[str, str]:
    """(system, user) for one door — the production builders, never a copy."""
    if door == "agent":
        from applire.prompts.profile_extraction import SYSTEM_PROMPT, build_user_prompt

        return SYSTEM_PROMPT, build_user_prompt(raw_text)
    from applire.prompts.cv_extraction import (
        GENERIC_CV_EXTRACTION_PROMPT,
        build_generic_prompt,
    )

    return GENERIC_CV_EXTRACTION_PROMPT, build_generic_prompt(raw_text)


def _entries(data: dict[str, Any], door: str) -> list[dict[str, Any]]:
    key = "work_history" if door == "agent" else "work_experience"
    rows = data.get(key)
    if not isinstance(rows, list):
        rows = data.get("work_experience") if door == "agent" else data.get("work_history")
    return [r for r in (rows or []) if isinstance(r, dict)]


def score_per_entry_tech(data: dict[str, Any], door: str, cfg: dict[str, Any]) -> dict[str, Any]:
    rows = _entries(data, door)
    backfilled: list[str] = []
    owned: list[str] = []
    for row in rows:
        company = _norm(row.get("company"))
        techs = [_norm(t) for t in (row.get("technologies") or []) if isinstance(t, str)]
        hits = [t for t in techs if any(tok in t for tok in cfg["forbidden_tokens"])]
        if cfg["clean_company"] in company:
            backfilled.extend(hits)
        elif cfg["owning_company"] in company:
            owned.extend(hits)
    empty_company = sum(1 for row in rows if not _norm(row.get("company")))
    return {
        "backfilled_onto_clean_role": sorted(set(backfilled)),
        "backfill_violation": bool(backfilled),
        "owning_role_kept_the_tool": bool(owned),
        "n_entries": len(rows),
        "entry_count_violation": len(rows) != cfg["expected_entries"],
        "empty_company_entries": empty_company,
        "valid_entries_violation": bool(empty_company) or len(rows) != cfg["expected_entries"],
    }


def score_cert_issuer(data: dict[str, Any], door: str, cfg: dict[str, Any]) -> dict[str, Any]:
    certs = [c for c in (data.get("certifications") or []) if isinstance(c, dict)]
    invented: list[dict[str, str]] = []
    for cert in certs:
        org = _norm(cert.get("issuing_organization"))
        if not org:
            continue
        if not any(stated in org or org in stated for stated in cfg["stated_issuers"]):
            invented.append({"name": str(cert.get("name")), "issuing_organization": str(cert.get("issuing_organization"))})
    return {
        "n_certifications": len(certs),
        "cert_count_violation": len(certs) != cfg["expected_certs"],
        "invented_issuers": invented,
        "invented_issuer_violation": bool(invented),
    }


def score_review_per_entry_tech(
    review: dict[str, Any], case: str, cfg: dict[str, Any]
) -> dict[str, Any]:
    """Score ONE reviewer verdict over one captured extraction (E-1).

    Two properties, and the second is the one that decides whether the check is
    worth shipping:

    * on the **violating** extraction — does the reviewer BLOCK, and does it name
      the defect (the clean role and the tool that does not belong to it)? A
      block that names nothing is useless to the corrector, which is handed
      `feedback` and nothing else.
    * on the **clean** extraction — does it block anyway? Every false positive is
      one memoryless regeneration of a correct extraction, which ADR-021's
      severity contract calls a truthfulness risk, not a latency one.

    Deliberately blind to `location` / `check`: those fields are OPTIONAL today
    and are being removed from the rendered schema in this same build (M5.4.1),
    so a measurement that depended on them would be measuring the other work
    package.
    """
    approved = review.get("approved")
    issues = review.get("issues") or []
    text = _norm(json.dumps({"issues": issues, "feedback": review.get("feedback")},
                            ensure_ascii=False))
    blocking = approved is False
    names_role = cfg["clean_company"] in text
    names_tool = any(tok in text for tok in cfg["forbidden_tokens"])
    blocking_severities = sum(
        1 for i in issues if isinstance(i, dict) and _norm(i.get("severity")) == "blocking"
    )
    caught = bool(blocking and names_role and names_tool)
    return {
        "case": case,
        "approved": approved,
        "n_issues": len(issues),
        "n_blocking_issues": blocking_severities,
        "blocked": blocking,
        "names_the_role": names_role,
        "names_the_tool": names_tool,
        # The headline for the violating case…
        "caught_violation": caught if case == "violating" else None,
        "missed_violation_violation": (case == "violating" and not caught),
        # …and for the clean case: any block at all is a wasted round.
        "false_positive_violation": (case == "clean" and blocking),
    }


SCORERS = {
    "per_entry_tech": score_per_entry_tech,
    "per_entry_tech_hard": score_per_entry_tech,
    "cert_issuer": score_cert_issuer,
}


def build_review_prompt_for(case_file: str, raw_text: str) -> tuple[str, str]:
    """(system, user) for the extraction REVIEWER — the production builders."""
    from applire.prompts.review_profile_extraction import (
        REVIEW_SYSTEM_PROMPT,
        build_review_prompt,
    )

    extracted = json.loads((FIXTURE_DIR / case_file).read_text(encoding="utf-8"))
    return REVIEW_SYSTEM_PROMPT, build_review_prompt(raw_text, extracted)


async def run_one_review(
    provider: Any, probe: str, case: str, system: str, user: str, index: int
) -> dict[str, Any]:
    from applire.constants import REVIEW_VERDICT_MAX_TOKENS

    cfg = PROBES[probe]
    record: dict[str, Any] = {"probe": probe, "case": case, "run": index}
    started = time.time()
    try:
        # Same call shape as `services/reviewer.py:731` — temperature and the
        # verdict cap included, so the measurement is of the production call.
        review = await provider.aparse_json(
            user, system=system, temperature=0.1, max_tokens=REVIEW_VERDICT_MAX_TOKENS
        )
        if not isinstance(review, dict):
            record["error"] = f"non-dict response: {type(review).__name__}"
        else:
            record["metrics"] = score_review_per_entry_tech(review, case, cfg)
            record["raw"] = review
    except Exception as exc:  # noqa: BLE001 — a failed turn is data, not a crash
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["elapsed_s"] = round(time.time() - started, 2)
    return record


async def run_one(provider: Any, door: str, probe: str, system: str, user: str, index: int) -> dict[str, Any]:
    from applire.constants import CV_EXTRACTION_MAX_TOKENS

    cfg = PROBES[probe]
    record: dict[str, Any] = {"probe": probe, "door": door, "run": index}
    started = time.time()
    try:
        data = await provider.aparse_json(
            user, system=system, temperature=0.1, max_tokens=CV_EXTRACTION_MAX_TOKENS
        )
        if not isinstance(data, dict):
            record["error"] = f"non-dict response: {type(data).__name__}"
        else:
            record["metrics"] = SCORERS[probe](data, door, cfg)
            record["raw"] = data
    except Exception as exc:  # noqa: BLE001 — a failed turn is data, not a crash
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["elapsed_s"] = round(time.time() - started, 2)
    return record


def summarise(records: list[dict[str, Any]], probe: str) -> dict[str, Any]:
    ok = [r for r in records if r.get("metrics")]
    n = len(ok) or 1
    keys = [k for k in (ok[0]["metrics"] if ok else {}) if k.endswith("_violation")]
    summary: dict[str, Any] = {
        "probe": probe,
        "n": len(records),
        "errors": sum(1 for r in records if r.get("error")),
        "rates": {k: round(sum(1 for r in ok if r["metrics"][k]) / n, 3) for k in keys},
    }
    if PROBES.get(probe, {}).get("kind") == "review":
        # Per case, because a rate pooled over a positive and a negative case is
        # a number about nothing.
        summary["per_case"] = {}
        for case in sorted({r["metrics"]["case"] for r in ok}):
            rows = [r for r in ok if r["metrics"]["case"] == case]
            d = len(rows) or 1
            summary["per_case"][case] = {
                "n": len(rows),
                "blocked": round(sum(1 for r in rows if r["metrics"]["blocked"]) / d, 3),
                "names_the_role": round(
                    sum(1 for r in rows if r["metrics"]["names_the_role"]) / d, 3
                ),
                "names_the_tool": round(
                    sum(1 for r in rows if r["metrics"]["names_the_tool"]) / d, 3
                ),
                "caught": round(
                    sum(1 for r in rows if r["metrics"]["caught_violation"]) / d, 3
                ) if case == "violating" else None,
            }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extraction_probe")
    parser.add_argument("--probe", choices=sorted(PROBES), required=True)
    parser.add_argument("--door", choices=("agent", "web"), default="agent")
    parser.add_argument("--case", default=None, help="review probes: run one case only")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default=None)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--out", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    cfg = PROBES[args.probe]
    raw_text = (FIXTURE_DIR / cfg["fixture"]).read_text(encoding="utf-8")
    is_review = cfg.get("kind") == "review"
    if is_review:
        cases = {c: build_review_prompt_for(f, raw_text) for c, f in cfg["cases"].items()}
        if args.case:
            cases = {args.case: cases[args.case]}
        system, user = next(iter(cases.values()))
    else:
        system, user = build_prompt(args.door, raw_text)
    if args.dry_run:
        if is_review:
            for case, (s, u) in cases.items():
                print(f"probe={args.probe} case={case} system={len(s)} user={len(u)}")
            return 0
        print(f"probe={args.probe} door={args.door} system={len(system)} user={len(user)}")
        return 0

    os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ[{"openrouter": "OPENROUTER_MODEL", "mistral": "MISTRAL_MODEL",
                    "requesty": "REQUESTY_MODEL"}.get(args.provider, "OPENROUTER_MODEL")] = args.model
    from applire.config import settings
    from applire.providers.llm import get_provider

    settings.llm_provider = args.provider
    if args.model and args.provider == "openrouter":
        settings.openrouter_model = args.model
    provider = get_provider()

    async def _all() -> list[dict[str, Any]]:
        if is_review:
            out: list[dict[str, Any]] = []
            for case, (sys_p, usr_p) in cases.items():
                for i in range(1, args.n + 1):
                    out.append(
                        await run_one_review(provider, args.probe, case, sys_p, usr_p, i)
                    )
            return out
        return [
            await run_one(provider, args.door, args.probe, system, user, i)
            for i in range(1, args.n + 1)
        ]

    records = asyncio.run(_all())
    summary = summarise(records, args.probe)
    summary["meta"] = {"door": args.door, "provider": args.provider, "model": args.model,
                       "system_chars": len(system), "user_chars": len(user)}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
        out.with_suffix(".summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
