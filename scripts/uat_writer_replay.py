#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#672 (replay-harness) — in-process CV generation replay for F-4/F-5/F-9.

Runs the REAL ``applire.services.cv._render_cv_background`` pipeline, in-process
from this worktree, against a throwaway SQLite file DB, with a synthetic profile
built to provoke the three founder-UAT defects in one generation:

* **F-5** — a lone ``signature_stories`` entry at the profile root carrying a
  measured figure, to see whether the delivered document ever surfaces it.
* **F-9** — work-entry bullets containing literal JD-shaped noun-phrase
  fragments ("roadmap", "System Owner", …) that are NOT vault skills, to see
  whether they leak onto the page as skill chips.
* **F-4** — near-duplicate bullet content and a claimable-but-absent keyword,
  to give the terminal review something to plausibly block on.

Why in-process and not over REST (COMMON-BRIEF §9): the dev stack at :8001 runs
the MAIN tree's code, so a REST call there measures the baseline, not this
worktree's fixes. This script imports ``applire.services.cv`` directly from
``PYTHONPATH=backend`` inside THIS worktree instead.

**Hermetic by construction.** No ``.env`` is read (the worktree has none, and
Settings' ``env_file=".env"`` resolves relative to cwd, which never has one
here either). Every environment variable this script depends on is read
explicitly, below, and printed nowhere. ``--provider mock`` makes zero network
calls and needs no key. ``--provider openrouter`` reads ``LLM_PROVIDER`` /
``OPENROUTER_API_KEY`` / ``OPENROUTER_MODEL`` from the process environment only
(never from a file) and fails loudly, never silently, if the key is absent.

Usage::

    PYTHONPATH=backend python3 scripts/uat_writer_replay.py --provider mock --runs 1

    env $(grep -E '^(LLM_PROVIDER|OPENROUTER_API_KEY|OPENROUTER_MODEL|LLM_MODEL)=' \\
        ../applire-core/.env | xargs) \\
        PYTHONPATH=backend python3 scripts/uat_writer_replay.py \\
        --provider openrouter --runs 1 --log-dir /tmp/uat-writer-replay-real

Prints one ``RESULT_JSON <json>`` line per run to stdout (greppable).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_WORKTREE_ROOT = _SCRIPT_DIR.parent
_BACKEND = _WORKTREE_ROOT / "backend"
_JD_FILE = (
    _WORKTREE_ROOT.parent
    / "Documents"
    / "Runs"
    / "Nougat"
    / "final"
    / "founder-uat-2026-09-20"
    / "jd-kion-team-lead-genai-platform.txt"
)

# The two F-5 figures this harness seeds into the ONE signature story. Kept as
# module constants so both the seeding code and the story_figures_present
# check below cite the identical literal strings.
STORY_FIGURE_EFFORT = "roughly 80 % less validation effort"
STORY_FIGURE_TIMELINE = "first go-live in seven months"

# The seven F-9 fragment noun-phrases the profile's work-entry bullets carry
# verbatim, embedded in real sentences — and which must NOT appear anywhere in
# the vault's own `skills` list (so a fragment on the delivered page can only
# have come from the writer or a ledger row, never from
# `_guaranteed_vault_skills`).
FRAGMENT_PHRASES = [
    "roadmap",
    "budget estimation",
    "vendor selection",
    "application processes",
    "System Owner",
    "enterprise-scale",
    "AI automation use case",
]


def _die(msg: str) -> "None":
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=None, help="SQLite file path (default: fresh temp file per run)")
    p.add_argument("--log-dir", default=None, help="Base directory for the LLM debug log (default: fresh temp dir)")
    p.add_argument("--provider", choices=["mock", "openrouter"], default="mock")
    p.add_argument("--runs", type=int, default=1)
    return p.parse_args()


def _configure_environment(provider: str) -> None:
    """Set every env var this run needs, BEFORE any `applire` import.

    `applire.config.Settings` is instantiated once, at `applire.config` import
    time, into the module-level `settings` singleton — so anything read via
    `os.environ` here must land before the first `applire.*` import anywhere
    in this process (mirrors `backend/tests/unit/conftest.py`).
    """
    # Required field, no default — never actually connected to (every session
    # this script opens goes through a per-run engine/sessionmaker we install
    # below), but Settings() raises at import time without SOME value here.
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////nonexistent-placeholder.db")

    if provider == "mock":
        os.environ["LLM_PROVIDER"] = "mock"
    else:
        os.environ["LLM_PROVIDER"] = "openrouter"
        if not os.environ.get("OPENROUTER_API_KEY"):
            _die(
                "provider=openrouter but OPENROUTER_API_KEY is not set in the "
                "environment. This script never reads .env — export the key "
                "yourself, e.g.:\n"
                "  env $(grep -E '^(LLM_PROVIDER|OPENROUTER_API_KEY|OPENROUTER_MODEL|"
                "LLM_MODEL)=' ../applire-core/.env | xargs) "
                "PYTHONPATH=backend python3 scripts/uat_writer_replay.py "
                "--provider openrouter"
            )
        if not os.environ.get("OPENROUTER_MODEL"):
            _die("provider=openrouter but OPENROUTER_MODEL is not set in the environment.")

    # Debug log ON for the whole process — settings.llm_debug_log_dir is
    # overridden per run below (settings is a plain mutable singleton).
    os.environ["LLM_DEBUG_LOG"] = "true"


def _build_profile_json() -> dict[str, Any]:
    """A twin of the founder-UAT shape, built from scratch — invented employer
    and person, never the founder's own data (§1 of the binding rules)."""
    work_ids = [str(uuid.uuid4()) for _ in range(9)]

    def entry(idx, **kw):
        base = {
            "id": work_ids[idx],
            "company": kw.pop("company"),
            "role": kw.pop("role"),
            "start_date": kw.pop("start_date"),
            "end_date": kw.pop("end_date", None),
            "is_current": kw.pop("is_current", False),
            "responsibilities": kw.pop("responsibilities", []),
            "achievements": kw.pop("achievements", []),
            "technologies": kw.pop("technologies", []),
        }
        base.update(kw)
        return base

    work_experience = [
        entry(
            0,
            company="Aventra Diagnostics Group",
            role="Head of Quality Systems, GxP Digitalisation",
            start_date="2021-04",
            end_date=None,
            is_current=True,
            responsibilities=[
                "Owned the platform roadmap for three regulated diagnostics sites and "
                "drove annual budget estimation for the shared computerised-systems "
                "investment plan.",
                "Led a cross-functional team of six validation engineers delivering "
                "one enterprise-scale release train instead of three local ones.",
                "Chaired the change-control board for all GxP application processes "
                "spanning quality, manufacturing and logistics.",
            ],
            achievements=[
                "Designed and rolled out one shared computerised-system validation "
                "strategy across three sites during the COVID-19 diagnostics ramp, "
                "instead of three parallel site-local efforts.",
            ],
            technologies=["Veeva Vault QMS", "TrackWise", "Python"],
            team_size=6,
            budget_managed="€1.2M",
        ),
        entry(
            1,
            company="Aventra Diagnostics Group",
            role="Senior Validation Engineer",
            start_date="2018-09",
            end_date="2021-03",
            responsibilities=[
                "Led vendor selection for the laboratory information management "
                "system serving all quality application processes across the "
                "network.",
                "Acted as System Owner for the quality application processes of "
                "three manufacturing sites, coordinating enterprise-scale "
                "validation waves.",
            ],
            achievements=[
                "Reduced deviation closure time by 35% by standardising the CAPA "
                "workflow across all three sites.",
            ],
            technologies=["LIMS", "SQL"],
        ),
        entry(
            2,
            company="Aventra Diagnostics Group",
            role="Validation Engineer",
            start_date="2016-02",
            end_date="2018-08",
            responsibilities=[
                "Piloted the first AI automation use case in the quality "
                "organisation, screening batch records for anomalies before "
                "human review.",
                "Authored computerised system validation protocols for laboratory "
                "instrumentation.",
            ],
            achievements=[
                "Piloted the first AI automation use case in the quality "
                "organisation and cut manual batch-record screening time in half.",
            ],
            technologies=["Databricks", "Python"],
        ),
        entry(
            3,
            company="Ridgeline Biologics AG",
            role="Quality Engineer",
            start_date="2013-06",
            end_date="2016-01",
            responsibilities=[
                "Supported the roadmap and budget estimation exercise for a new "
                "electronic batch record system.",
                "Reviewed vendor selection proposals for a replacement document "
                "management system.",
            ],
            achievements=[],
            technologies=["Excel", "Minitab"],
        ),
        entry(
            4,
            company="Ridgeline Biologics AG",
            role="Junior Quality Engineer",
            start_date="2011-08",
            end_date="2013-05",
            responsibilities=[
                "Executed IQ/OQ/PQ protocols for enterprise-scale manufacturing "
                "equipment.",
                "Maintained the internal audit tracker for quality application "
                "processes.",
            ],
            achievements=[],
            technologies=["Excel"],
        ),
        entry(
            5,
            company="Ridgeline Biologics AG",
            role="Quality Intern",
            start_date="2010-09",
            end_date="2011-07",
            responsibilities=[
                "Assisted with document control for the quality application "
                "processes team.",
            ],
            achievements=[],
            technologies=[],
        ),
        entry(
            6,
            company="Nordkern Analytics GmbH",
            role="Data Analyst (Working Student)",
            start_date="2008-10",
            end_date="2010-08",
            responsibilities=[
                "Built reporting dashboards for the quality management team.",
                "Supported budget estimation for the department's tooling "
                "refresh.",
            ],
            achievements=[],
            technologies=["Excel", "SQL"],
        ),
        entry(
            7,
            company="Nordkern Analytics GmbH",
            role="Junior Data Analyst",
            start_date="2007-04",
            end_date="2008-09",
            responsibilities=[
                "Maintained the vendor selection scorecard for laboratory "
                "suppliers.",
            ],
            achievements=[],
            technologies=["Excel"],
        ),
        entry(
            8,
            company="Nordkern Analytics GmbH",
            role="Trainee",
            start_date="2006-01",
            end_date="2007-03",
            responsibilities=[
                "Supported the roadmap planning workshops for the analytics "
                "team.",
            ],
            achievements=[],
            technologies=[],
        ),
    ]

    profile_json: dict[str, Any] = {
        "personal_info": {
            "name": "Petra Lindqvist",
            "email": "petra.lindqvist@example.invalid",
            "phone": "+49 151 00000000",
            "location": "Frankfurt am Main",
        },
        "professional_summary": {
            "de": "",
            "en": (
                "Quality systems leader with 15+ years in regulated diagnostics, "
                "now driving digitalisation and AI-enabled automation across "
                "enterprise-scale quality operations."
            ),
        },
        "work_experience": work_experience,
        "education": [],
        "certifications": [],
        # F-9: ONLY proper competence names — no fragment phrase may appear
        # here, so a fragment on the delivered page cannot have come from
        # `_guaranteed_vault_skills`.
        "skills": [
            {"name": "Computerised System Validation", "category": "domain"},
            {"name": "Databricks", "category": "technical"},
            {"name": "Requirements Engineering", "category": "technical"},
            {"name": "GxP Compliance", "category": "domain"},
            {"name": "Stakeholder Management", "category": "soft"},
            {"name": "Python", "category": "technical"},
            {"name": "Data Governance", "category": "domain"},
            {"name": "Vendor Management", "category": "soft"},
            {"name": "Quality Risk Management", "category": "domain"},
        ],
        "languages": [
            {"language": "German", "level": "Native", "is_native": True},
            {"language": "English", "level": "Fluent"},
        ],
        "publications": [],
        "projects": [],
        "volunteer_activities": [],
        # F-5: ONE signature story at the profile root, anchored to the FIRST
        # work entry, carrying the two measured figures verbatim.
        "signature_stories": [
            {
                "id": str(uuid.uuid4()),
                "title": "One shared validation strategy for a three-site COVID ramp",
                "challenge": (
                    "Three regulated diagnostics sites needed to scale batch "
                    "release testing simultaneously during the COVID-19 "
                    "diagnostics ramp, each planning its own validation "
                    "approach in parallel."
                ),
                "mechanism": (
                    "Designed and rolled out one shared computerised-system "
                    "validation strategy across all three sites instead of "
                    "three parallel site-local efforts."
                ),
                "outcome": (
                    f"{STORY_FIGURE_EFFORT}, with the {STORY_FIGURE_TIMELINE}."
                ),
                "benchmark": None,
                "experience_refs": [work_ids[0]],
                "source": "synthetic-replay-fixture",
            }
        ],
        "metadata": {
            "completeness_score": 0.8,
            "created_via": "manual",
            "denied_concepts": [
                {
                    "concept": "Kubernetes",
                    "statement": "I have not personally administered Kubernetes clusters.",
                    "source": "interview",
                    "date": "2026-09-01",
                    "denial_level": "direct",
                }
            ],
        },
    }
    return profile_json, work_ids


def _build_job_fields(jd_text: str) -> dict[str, Any]:
    """Derived from the public KION posting — required/nice-to-have/keywords
    per `schemas/job.py`'s shape."""
    return {
        "role_title": "Team Lead GenAI Platform",
        "required_skills": [
            "AI/ML architecture",
            "LLMs",
            "vector databases",
            "RAG methods",
            "GenAI lifecycle management",
            "IT governance",
            "product management",
            "team leadership",
            "budget planning",
            "DevOps/LLMOps/AgentOps",
        ],
        "nice_to_have_skills": ["German language proficiency"],
        "keywords": [
            "roadmap",
            "budget estimation",
            "vendor selection",
            "application processes",
            "System Owner",
            "enterprise-scale",
            "AI automation use case",
            "GenAI Platform",
            "cloud-native",
            "Service Level Agreement",
        ],
        "seniority_level": "senior",
        "company_culture_signals": ["cross-functional", "global team"],
        "language_requirement": "Very good English; German is a plus",
        "jd_language": "en",
        "company_name": "KION Group",
        "raw_text": jd_text,
    }


def _build_keyword_ledger() -> list[dict[str, Any]]:
    """Real-shape rows (see `services/keyword_ledger.py`'s field set and
    `_VALID_STATUS`). At least one `denied` row and one claimable row whose
    surface forms are JD-echo noun phrases (roadmap, budget estimation)."""
    return [
        {
            "concept": "Roadmap & Budget Ownership",
            "surface_forms": ["roadmap", "budget estimation"],
            "claimable": True,
            "status": "direct",
            "evidence": (
                "Owned the platform roadmap for three regulated diagnostics "
                "sites and drove annual budget estimation for the shared "
                "computerised-systems investment plan."
            ),
            "sources": ["required"],
            "fit_weight": 1.0,
        },
        {
            "concept": "AI Automation Delivery",
            "surface_forms": ["AI automation use case", "LLMOps"],
            "claimable": True,
            "status": "partial",
            "evidence": (
                "Piloted the first AI automation use case in the quality "
                "organisation, screening batch records for anomalies before "
                "human review."
            ),
            "sources": ["required"],
            "fit_weight": 1.0,
        },
        {
            "concept": "Kubernetes",
            "surface_forms": ["Kubernetes"],
            "claimable": False,
            "status": "denied",
            "evidence": "Candidate explicitly stated a limit here (interview).",
            "sources": ["nice_to_have"],
            "fit_weight": 0.5,
        },
        {
            "concept": "AgentOps",
            "surface_forms": ["AgentOps", "agentic workflow orchestration"],
            "claimable": False,
            "status": "gap",
            "evidence": "",
            "sources": ["required"],
            "fit_weight": 1.0,
        },
    ]


async def _seed_and_run(
    *, db_path: Path, run_log_dir: Path, cv_mod, db_session_mod, models
) -> uuid.UUID:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire.config import settings

    settings.llm_debug_log_dir = str(run_log_dir)
    run_log_dir.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(db_session_mod.Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # cv.py cached `AsyncSessionLocal` as a name in ITS OWN module namespace at
    # import time (`from applire.db.session import AsyncSessionLocal`) — a
    # later reassignment of `applire.db.session.AsyncSessionLocal` alone would
    # not reach it, so both are repointed (mirrors
    # `test_cv_terminal_review.py`'s `_run_pipeline` patch of
    # `applire.services.cv.AsyncSessionLocal`). `providers/llm/usage.py`'s
    # `_persist` does a FRESH `from applire.db.session import AsyncSessionLocal`
    # inside the function on every call, so patching the module attribute alone
    # is enough for it to pick up this run's engine.
    db_session_mod.engine = engine
    db_session_mod.AsyncSessionLocal = session_factory
    cv_mod.AsyncSessionLocal = session_factory

    from tests.support.profile_factory import make_master_profile

    profile_json, work_ids = _build_profile_json()
    jd_text = _JD_FILE.read_text(encoding="utf-8")
    job_fields = _build_job_fields(jd_text)
    ledger = _build_keyword_ledger()

    job_id, profile_id, cv_id, gap_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    )
    now = datetime.now(timezone.utc)

    async with session_factory() as db:
        db.add_all([
            models.job.JobAnalysis(
                id=job_id,
                raw_text_hash=str(job_id),
                **job_fields,
            ),
            make_master_profile(
                id=profile_id,
                profile_json=profile_json,
                created_at=now,
                updated_at=now,
            ),
            models.gap.GapAnalysis(
                id=gap_id,
                job_analysis_id=job_id,
                profile_id=profile_id,
                match_score=50.0,
                keyword_ledger=ledger,
                created_at=now,
            ),
            models.cv.GeneratedCV(
                id=cv_id,
                job_analysis_id=job_id,
                profile_id=profile_id,
                tailored_data={},
                template="classic_german",
                status="pending",
                target_pages=2,
                created_at=now,
                expires_at=now.replace(year=now.year + 1),
            ),
        ])
        await db.commit()

    await cv_mod._render_cv_background(cv_id, job_id, profile_id, "classic_german")

    await engine.dispose()
    return cv_id, job_id, profile_id, session_factory, engine


def _collect_leaf_strings(node: Any, out: list[str]) -> None:
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_leaf_strings(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_leaf_strings(v, out)


def _story_figures_present(tailored_data: dict[str, Any]) -> dict[str, bool]:
    from applire.services.ats_audit import _norm, join_corpus_fragments, surface_present

    leaves: list[str] = []
    _collect_leaf_strings(tailored_data, leaves)
    text_norm = _norm(join_corpus_fragments(leaves))
    return {
        STORY_FIGURE_EFFORT: surface_present(STORY_FIGURE_EFFORT, text_norm),
        STORY_FIGURE_TIMELINE: surface_present(STORY_FIGURE_TIMELINE, text_norm),
    }


def _find_check(ats_report: dict[str, Any] | None, check_id: str) -> dict[str, Any] | None:
    if not ats_report:
        return None
    for check in ats_report.get("checks") or []:
        if isinstance(check, dict) and check.get("id") == check_id:
            return check
    return None


def _drafted_skills_from_log(run_log_dir: Path) -> tuple[Any, str | None]:
    """The writer/corrector's OWN last `cv_tailoring`-stage response before
    composition, read from the debug log this run wrote. Distinguished from a
    reviewer-verdict record (which has no `skills` key) by requiring one.
    Returns (skills_or_None, note)."""
    records: list[dict[str, Any]] = []
    if run_log_dir.exists():
        for f in sorted(run_log_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    candidates = [
        r for r in records
        if r.get("stage") == "cv_tailoring"
        and r.get("method") == "aparse_json"
        and isinstance(r.get("response"), dict)
        and "skills" in r["response"]
    ]
    if not candidates:
        return None, (
            "no cv_tailoring-stage debug-log record carried a 'skills' key — "
            "could not attribute drafted_skills"
        )
    candidates.sort(key=lambda r: r.get("ts") or "")
    return candidates[-1]["response"].get("skills"), None


async def _run_once(*, index: int, args: argparse.Namespace, cv_mod, db_session_mod, models) -> dict[str, Any]:
    if args.db and args.runs == 1:
        db_path = Path(args.db)
    else:
        fd, tmp = tempfile.mkstemp(prefix=f"uat-writer-replay-{index}-", suffix=".sqlite3")
        os.close(fd)
        os.unlink(tmp)  # let create_async_engine create it fresh
        db_path = Path(tmp)

    base_log_dir = Path(args.log_dir) if args.log_dir else Path(tempfile.mkdtemp(prefix="uat-writer-replay-log-"))
    run_log_dir = base_log_dir / f"run_{index}"

    cv_id, job_id, profile_id, session_factory, engine = await _seed_and_run(
        db_path=db_path, run_log_dir=run_log_dir,
        cv_mod=cv_mod, db_session_mod=db_session_mod, models=models,
    )

    # `_seed_and_run` disposed its own engine handle at the end of the render;
    # open a fresh one against the same file to read the persisted result.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    read_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    read_factory = async_sessionmaker(read_engine, expire_on_commit=False)
    async with read_factory() as db:
        record = await db.get(models.cv.GeneratedCV, cv_id)
    await read_engine.dispose()

    drafted_skills, drafted_skills_note = _drafted_skills_from_log(run_log_dir)

    llm_call_count = 0
    if run_log_dir.exists():
        for f in run_log_dir.glob("*.jsonl"):
            llm_call_count += sum(1 for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip())

    tailored_data = record.tailored_data if record else {}
    ats_report = record.ats_report if record else None

    result = {
        "run_index": index,
        "db_path": str(db_path),
        "log_dir": str(run_log_dir),
        "cv_id": str(cv_id),
        "status": record.status if record else None,
        "error_message": record.error_message if record else "record not found",
        "delivered_skills": (tailored_data or {}).get("skills"),
        "drafted_skills": drafted_skills,
        "drafted_skills_note": drafted_skills_note,
        "story_figures_present": _story_figures_present(tailored_data or {}),
        "terminal_review_check": _find_check(ats_report, "terminal-review"),
        "page_length_check": _find_check(ats_report, "page-length"),
        "ats_pass_fail_na": {
            "passed": (ats_report or {}).get("passed"),
            "failed": (ats_report or {}).get("failed"),
            "not_applicable": (ats_report or {}).get("not_applicable"),
        } if ats_report is not None else None,
        "keywords_present_denied": (ats_report or {}).get("keywords", {}).get("present_denied")
        if ats_report is not None else None,
        "llm_call_count": llm_call_count,
    }
    return result


def main() -> None:
    args = _parse_args()
    if not _JD_FILE.exists():
        _die(f"public JD file not found: {_JD_FILE}")

    _configure_environment(args.provider)

    if str(_BACKEND) not in sys.path:
        sys.path.insert(0, str(_BACKEND))

    import asyncio

    # Import once; each run repoints the AsyncSessionLocal/engine attributes.
    import applire.db.session as db_session_mod
    import applire.models as models
    import applire.models.cover_letter  # noqa: F401 — not in models/__init__.py yet
    import applire.services.cv as cv_mod

    async def _main() -> None:
        for i in range(args.runs):
            result = await _run_once(
                index=i, args=args, cv_mod=cv_mod, db_session_mod=db_session_mod, models=models,
            )
            print("RESULT_JSON " + json.dumps(result, ensure_ascii=False, default=str))

    asyncio.run(_main())


if __name__ == "__main__":
    main()
