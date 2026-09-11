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


SCORERS = {"per_entry_tech": score_per_entry_tech, "cert_issuer": score_cert_issuer}


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
    return {
        "probe": probe,
        "n": len(records),
        "errors": sum(1 for r in records if r.get("error")),
        "rates": {k: round(sum(1 for r in ok if r["metrics"][k]) / n, 3) for k in keys},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="extraction_probe")
    parser.add_argument("--probe", choices=sorted(PROBES), required=True)
    parser.add_argument("--door", choices=("agent", "web"), default="agent")
    parser.add_argument("--provider", default="mock")
    parser.add_argument("--model", default=None)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--out", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    raw_text = (FIXTURE_DIR / PROBES[args.probe]["fixture"]).read_text(encoding="utf-8")
    system, user = build_prompt(args.door, raw_text)
    if args.dry_run:
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
