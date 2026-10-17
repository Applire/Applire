#!/usr/bin/env python3
# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""E066 Document Quality Bench — generation harness.

Runs, per run, the REAL CV pipeline and then the REAL cover-letter pipeline
in-process from this worktree, against a throwaway SQLite file DB seeded from a
frozen fixture (``tests/files/quality_bench/<case>/<cell>/``, format in
``tests/files/quality_bench/FIXTURE_FORMAT.md``), and writes one directory per
run. It generalises ``scripts/uat_writer_replay.py`` (hermetic env, explicit
provider env, per-run LLM debug-log dir) from "the CV half" to "CV + letter,
any fixture".

The pipelines are entered through their public service doors with no request
lifecycle — ``services.cv.generate_cv(..., background_tasks=None)`` and
``services.cover_letter.generate_cover_letter(..., background_tasks=None)`` —
the same inline path the MCP/agent channel takes. Those doors resolve and pin
the document language, create the row and call ``_render_cv_background`` /
``_render_cover_letter_background`` themselves, so nothing about the wiring is
re-implemented here. The harness imports product code and never patches it; the
only thing it repoints is the process-wide database handle.

Usage::

    PYTHONPATH=backend python3 scripts/quality_bench/run.py \\
        --fixture tests/files/quality_bench/_smoke/same_de \\
        --runs 1 --arm baseline --provider mock --out /tmp/bench-out

    # real provider (spends credit; the key comes from the PROCESS env only):
    OPENROUTER_API_KEY=... PYTHONPATH=backend python3 scripts/quality_bench/run.py \\
        --fixture tests/files/quality_bench/<case>/<cell> --runs 5 --arm baseline \\
        --provider openrouter --model openai/gpt-5.6-luna --out <dir> --concurrency 3

Run-output contract (the seam to the scorers — do not change unilaterally)::

    <out>/<arm>/<model_slug>/<cell_id>/<run_idx>/
      meta.json     arm, model, model_slug, cell_id, case, cell_type, document_language,
                    run_idx, git_sha, started, finished, status ("ok"|"failed"),
                    failed_stage (null | "seed" | "cv" | "letter" | "pages" | "collect"),
                    error, fixture_dir  (+ extras: provider, settings, duration_s, ids,
                    cv_status, letter_status, pages_error, git_dirty, usage)
      cv.json       GeneratedCV.tailored_data as persisted (absent unless the CV is ready)
      letter.json   GeneratedCoverLetter.letter_data as persisted (absent unless ready)
      reports.json  {"cv": {...}, "letter": {...}} — the rows' ats_report, truthfulness_report,
                    critic_report, review_state, plus "terminal_review" (the ats_report's
                    ``terminal-review`` check) — raw, as persisted
      pages.json    {"cv": int|null, "letter": int|null}  (rendered page counts of the
                    delivered documents; null when no browser is available)
      llm.jsonl     the per-run LLM debug log, exactly as providers/llm/debug_log.py wrote it
      ledger_effective.json   (extra) the keyword ledger AFTER the pipeline's own
                    refresh/re-score against the vault (services/keyword_ledger.py:2721)
      worker.log    (extra, process mode) the run's stdout/stderr
      error.txt     (extra, failed runs) the traceback

A run that raises, times out or ends in a failed status is recorded with
``status: "failed"``, ``failed_stage`` and ``error`` — never skipped. ``meta.json``
is written pessimistically (``failed``) before the run starts and rewritten at
every stage, so even a hard kill leaves an honest failed record.

Exit code: 0 when every run is ok, 1 when at least one run failed, 2 on a
configuration error (nothing was run).

Isolation: by default each run is its own subprocess (own settings singleton,
own SQLite file, own LLM log dir, a hard-killable timeout). ``--in-process``
runs in this interpreter (debugging, tests); it requires ``--concurrency 1``
because the product reads its database handle and debug-log dir from
process-wide state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _SCRIPT_DIR.parent
_WORKTREE_ROOT = _SCRIPTS_DIR.parent
_BACKEND = _WORKTREE_ROOT / "backend"


def _ensure_paths() -> None:
    """Make ``quality_bench`` and ``applire`` importable regardless of how we were started."""
    for p in (str(_SCRIPTS_DIR), str(_BACKEND)):
        if p not in sys.path:
            sys.path.insert(0, p)


_ensure_paths()

from quality_bench.fixture import Fixture, FixtureError, load_fixture  # noqa: E402

#: Provider API-key variables scrubbed from the environment in mock mode, so a
#: mock run cannot reach (or leak) a real key even by accident.
_KEY_ENV_RE = re.compile(r"(API_KEY|TOKEN|SECRET)", re.IGNORECASE)

#: Settings recorded in meta.json (never a secret).
_SETTINGS_SNAPSHOT = (
    "llm_provider",
    "openrouter_model",
    "openrouter_disable_thinking",
    "openrouter_reasoning_effort",
    "llm_timeout",
)
_CONSTANTS_SNAPSHOT = (
    "LLM_REVIEW_MAX_RETRIES",
    "CV_TERMINAL_REVIEW_MAX_RETRIES",
    "CV_LANGUAGE_REVIEW_MAX_RETRIES",
    "LETTER_TERMINAL_REVIEW_MAX_RETRIES",
)

STAGES = ("seed", "cv", "letter", "pages", "collect")


class ConfigError(Exception):
    """Invalid invocation or environment — nothing is run."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def model_slug(model: str) -> str:
    """A single safe path component for a model id (``openai/gpt-5.6-luna`` -> ``openai-gpt-5.6-luna``)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-.")
    return slug or "unknown-model"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_info(worktree: Path = _WORKTREE_ROOT) -> tuple[str | None, bool | None]:
    """``(HEAD sha, dirty?)`` of the worktree; ``(None, None)`` when git is unavailable."""
    try:
        sha = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=20, check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=no", "--", "backend"],
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout.strip()
        )
        return sha, dirty
    except Exception:
        return None, None


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# environment / provider selection
# --------------------------------------------------------------------------- #
def resolve_model(provider: str, model: str | None) -> str:
    """The model id this run records. Mock is always ``"mock"``; openrouter needs one."""
    if provider == "mock":
        return "mock"
    resolved = model or os.environ.get("OPENROUTER_MODEL") or ""
    if not resolved:
        raise ConfigError("provider=openrouter needs --model or OPENROUTER_MODEL in the process environment")
    return resolved


def configure_environment(provider: str, model: str | None) -> str:
    """Set every env var the run depends on, BEFORE any ``applire`` import (and mirror them
    onto ``applire.config.settings`` when it is already imported — the unit tests do that).

    Nothing is read from ``.env``. Returns the resolved model id.
    """
    resolved = resolve_model(provider, model)
    # Required field with no default — never connected to (every session goes through the
    # per-run engine installed in execute_run), but Settings() raises at import without it.
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////nonexistent-placeholder.db")
    # The debug log is how the scorers see every LLM exchange; ON for the whole process.
    os.environ["LLM_DEBUG_LOG"] = "true"

    overrides: dict[str, Any] = {"llm_debug_log": True}
    if provider == "mock":
        os.environ["LLM_PROVIDER"] = "mock"
        for name in [n for n in os.environ if _KEY_ENV_RE.search(n)]:
            del os.environ[name]
        overrides["llm_provider"] = "mock"
    else:
        existing = os.environ.get("LLM_PROVIDER")
        if existing and existing.lower() != "openrouter":
            raise ConfigError(
                f"--provider openrouter conflicts with LLM_PROVIDER={existing!r} in the environment"
            )
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise ConfigError(
                "provider=openrouter but OPENROUTER_API_KEY is not set in the process environment "
                "(this harness never reads .env — export it yourself)"
            )
        os.environ["LLM_PROVIDER"] = "openrouter"
        os.environ["OPENROUTER_MODEL"] = resolved
        # Reasoning OFF is the shipped default (ADR-009 amended); an explicit env value wins.
        os.environ.setdefault("OPENROUTER_DISABLE_THINKING", "true")
        overrides.update(
            llm_provider="openrouter",
            openrouter_model=resolved,
            openrouter_disable_thinking=os.environ["OPENROUTER_DISABLE_THINKING"].lower()
            in ("1", "true", "yes", "on"),
        )

    if "applire.config" in sys.modules:  # settings is already instantiated: mirror
        from applire.config import settings

        for key, value in overrides.items():
            setattr(settings, key, value)
    return resolved


def settings_snapshot() -> dict[str, Any]:
    """Non-secret configuration the run actually saw (recorded in meta.json)."""
    from applire import constants
    from applire.config import settings

    snap: dict[str, Any] = {k: getattr(settings, k, None) for k in _SETTINGS_SNAPSHOT}
    snap.update({k: getattr(constants, k, None) for k in _CONSTANTS_SNAPSHOT})
    # US368: an arm is only readable with the pipeline switches it ran under
    # (CV_PIPELINE and the letter's own switch, E068) — every *_PIPELINE value the
    # process saw, from the environment and from the settings singleton.
    for name, value in sorted(os.environ.items()):
        if name.endswith("_PIPELINE"):
            snap[name] = value
    for name in dir(settings):
        if name.endswith("_pipeline") and not name.startswith("_"):
            snap[f"settings.{name}"] = getattr(settings, name, None)
    return snap


# --------------------------------------------------------------------------- #
# run description
# --------------------------------------------------------------------------- #
@dataclass
class RunSpec:
    fixture: Fixture
    arm: str
    provider: str
    model: str
    run_idx: int
    out_root: Path
    timeout: float = 1800.0
    measure_pages: bool = True
    keep_db: bool = False

    @property
    def slug(self) -> str:
        return model_slug(self.model)

    @property
    def run_dir(self) -> Path:
        return self.out_root / self.arm / self.slug / self.fixture.cell_id / str(self.run_idx)


@dataclass
class _State:
    """Mutable progress of one run, shared with the timeout/exception handlers."""

    stage: str = "seed"
    extra: dict[str, Any] = field(default_factory=dict)


def _base_meta(spec: RunSpec, started: str) -> dict[str, Any]:
    fx = spec.fixture
    sha, dirty = git_info()
    return {
        "arm": spec.arm,
        "model": spec.model,
        "model_slug": spec.slug,
        "cell_id": fx.cell_id,
        "case": fx.case,
        "cell_type": fx.cell_type,
        "document_language": fx.document_language,
        "run_idx": spec.run_idx,
        "git_sha": sha,
        "git_dirty": dirty,
        "started": started,
        "finished": None,
        "status": "failed",
        "failed_stage": "seed",
        "error": "run did not complete (process killed or crashed before it could record an outcome)",
        "fixture_dir": str(fx.dir),
        "provider": spec.provider,
    }


# --------------------------------------------------------------------------- #
# DB handle
# --------------------------------------------------------------------------- #
def _repoint_database(engine, session_factory) -> None:
    """Point every module-level ``AsyncSessionLocal`` / ``engine`` at the per-run DB.

    The product caches the sessionmaker by name in several modules at import time
    (``from applire.db.session import AsyncSessionLocal`` in services/cv.py,
    services/cover_letter.py, services/cover_letter_pdf.py, ...), so reassigning
    ``applire.db.session.AsyncSessionLocal`` alone would not reach them. Rather than
    list them (and rot), every loaded ``applire.*`` module holding the ORIGINAL
    object is repointed.
    """
    import applire.db.session as db_session

    original_factory = db_session.AsyncSessionLocal
    original_engine = db_session.engine
    for name, mod in list(sys.modules.items()):
        if mod is None or not name.startswith("applire."):
            continue
        if getattr(mod, "AsyncSessionLocal", None) is original_factory:
            mod.AsyncSessionLocal = session_factory
        if getattr(mod, "engine", None) is original_engine:
            mod.engine = engine
    db_session.AsyncSessionLocal = session_factory
    db_session.engine = engine


# --------------------------------------------------------------------------- #
# artefact collection
# --------------------------------------------------------------------------- #
def _terminal_review_check(ats_report: dict[str, Any] | None) -> dict[str, Any] | None:
    for check in (ats_report or {}).get("checks") or []:
        if isinstance(check, dict) and check.get("id") == "terminal-review":
            return check
    return None


def document_reports(row: Any, *, extra_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    """The raw persisted reports of a CV / letter row (reports.json half)."""
    out: dict[str, Any] = {
        "status": row.status,
        "error_message": getattr(row, "error_message", None),
        "document_language": getattr(row, "document_language", None),
        "template": getattr(row, "template", None),
        "ats_report": row.ats_report,
        "truthfulness_report": row.truthfulness_report,
        "critic_report": row.critic_report,
        "review_state": row.review_state,
        "terminal_review": _terminal_review_check(row.ats_report),
    }
    for name in extra_fields:
        out[name] = getattr(row, name, None)
    return out


async def measure_pages(cv_id, cl_id, user_id, state: _State) -> dict[str, int | None]:
    """Page counts of the DELIVERED documents, rendered fresh from the persisted rows.

    Needs Chromium (Playwright). Fail-open like the pipeline's own measure steps: a
    missing browser yields ``null`` plus ``meta.pages_error``, never a failed run.
    """
    from applire import ownership
    from applire.db.session import AsyncSessionLocal
    from applire.services import cv as cv_mod
    from applire.services.ats_audit import extract_text_and_pages
    from applire.services.cover_letter_pdf import render_pdf

    pages: dict[str, int | None] = {"cv": None, "letter": None}
    errors: dict[str, str] = {}
    with ownership.owner_context(user_id):
        if cv_id is not None:
            try:
                async with AsyncSessionLocal() as db:
                    html = await cv_mod.get_cv_html(cv_id, db, user_id=user_id)
                _, pages["cv"] = extract_text_and_pages(await cv_mod._html_to_pdf(html))
            except Exception as exc:  # noqa: BLE001 — fail-open by contract
                errors["cv"] = f"{type(exc).__name__}: {exc}"[:300]
        if cl_id is not None:
            try:
                _, pages["letter"] = extract_text_and_pages(await render_pdf(cl_id, user_id=user_id))
            except Exception as exc:  # noqa: BLE001
                errors["letter"] = f"{type(exc).__name__}: {exc}"[:300]
    if errors:
        state.extra["pages_error"] = errors
    return pages


async def _usage_summary(session_factory) -> dict[str, Any] | None:
    """Token totals from the run's ``llm_usage`` rows (one row per provider call)."""
    try:
        from sqlalchemy import select

        from applire import ownership
        from applire.models.llm_usage import LlmUsage

        with ownership.unscoped("tooling"):
            async with session_factory() as db:
                rows = (await db.execute(select(LlmUsage))).scalars().all()
        return {
            "calls": len(rows),
            "prompt_tokens": sum(r.prompt_tokens for r in rows),
            "completion_tokens": sum(r.completion_tokens for r in rows),
            "reasoning_tokens": sum(r.reasoning_tokens for r in rows),
            "failed_calls": sum(1 for r in rows if not r.ok),
        }
    except Exception:  # noqa: BLE001 — accounting must never fail a run
        return None


def finalize_llm_log(run_dir: Path) -> int:
    """Fold ``_llmlog/<date>.jsonl`` into ``llm.jsonl`` (records untouched) and drop the dir.

    Idempotent; also called by the parent after a killed child. Returns the record count.
    """
    log_dir = run_dir / "_llmlog"
    target = run_dir / "llm.jsonl"
    count = 0
    lines: list[str] = []
    if log_dir.is_dir():
        for f in sorted(log_dir.glob("*.jsonl")):
            for ln in f.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    lines.append(ln)
        shutil.rmtree(log_dir, ignore_errors=True)
    if lines or not target.exists():
        target.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
    if target.exists():
        count = sum(1 for ln in target.read_text(encoding="utf-8").splitlines() if ln.strip())
    return count


# --------------------------------------------------------------------------- #
# one run
# --------------------------------------------------------------------------- #
async def _pipeline(spec: RunSpec, run_dir: Path, meta: dict[str, Any], state: _State) -> None:
    """seed -> CV -> letter -> pages -> collect. Raises on any failure; ``state.stage`` says where."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from applire import ownership
    from applire.db.session import Base
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.models.cv import GeneratedCV
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from applire.providers.llm import get_provider
    from applire.schemas.cover_letter import CoverLetterGenerateRequest
    from applire.services import cover_letter as letter_mod
    from applire.services import cv as cv_mod
    from quality_bench.seed import seed_fixture

    fx = spec.fixture
    work_dir = run_dir / "_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    db_path = work_dir / "bench.sqlite3"

    def advance(stage: str) -> None:
        state.stage = stage
        meta["failed_stage"] = stage
        _write_json(run_dir / "meta.json", meta)

    # ---- seed ------------------------------------------------------------
    advance("seed")
    # The product reads the debug-log dir from the settings singleton on EVERY record.
    from applire.config import settings

    log_dir = run_dir / "_llmlog"
    log_dir.mkdir(parents=True, exist_ok=True)
    settings.llm_debug_log_dir = str(log_dir)
    settings.llm_debug_log = True
    meta["settings"] = settings_snapshot()

    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    try:
        async with engine.begin() as conn:  # DDL names owned tables: create before the guard exists
            await conn.run_sync(Base.metadata.create_all)
        from applire.ownership import install_guard

        install_guard(engine)  # same ADR-092 statement guard the application engine has
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        _repoint_database(engine, session_factory)
        seeded = await seed_fixture(session_factory, fx)
        meta["ids"] = {
            "user_id": str(seeded.user_id), "profile_id": str(seeded.profile_id),
            "job_id": str(seeded.job_id), "application_id": str(seeded.application_id),
        }
        uid = seeded.user_id
        provider = get_provider()

        with ownership.owner_context(uid):
            # ---- CV --------------------------------------------------------
            advance("cv")
            async with session_factory() as db:
                cv_resp = await cv_mod.generate_cv(
                    seeded.job_id, db, provider, None,
                    template=fx.template, target_pages=fx.target_pages, user_id=uid,
                )
            async with session_factory() as db:
                cv_row = await db.get(GeneratedCV, cv_resp.cv_id)
                reports: dict[str, Any] = {"cv": document_reports(cv_row, extra_fields=("target_pages", "error_code"))}
                meta["ids"]["cv_id"] = str(cv_row.id)
                meta["cv_status"] = cv_row.status
                cv_data, cv_ready = cv_row.tailored_data, cv_row.status == "ready"
                cv_err = cv_row.error_message or cv_row.error_code
                cv_id = cv_row.id
            _write_json(run_dir / "reports.json", reports)
            if not cv_ready:
                raise RuntimeError(f"CV generation ended in status {meta['cv_status']!r}: {cv_err}")
            _write_json(run_dir / "cv.json", cv_data)

            # ---- letter ----------------------------------------------------
            # The letter reads the delivered CV through the flow row, as in production
            # (services/cover_letter.py:176-196); the flow orchestrator sets the pointer.
            advance("letter")
            async with session_factory() as db:
                flow = await db.get(FlowSession, seeded.flow_id)
                flow.generated_cv_id = cv_id
                await db.commit()
            async with session_factory() as db:
                letter_resp = await letter_mod.generate_cover_letter(
                    CoverLetterGenerateRequest(job_id=seeded.job_id, **fx.letter_inputs),
                    db, provider, None, user_id=uid,
                )
            async with session_factory() as db:
                cl_row = await db.get(GeneratedCoverLetter, letter_resp.cover_letter_id)
                reports["letter"] = document_reports(cl_row, extra_fields=("pre_gen_inputs",))
                meta["ids"]["cover_letter_id"] = str(cl_row.id)
                meta["letter_status"] = cl_row.status
                letter_data, letter_ready = cl_row.letter_data, cl_row.status == "ready"
                letter_err = cl_row.error_message
                cl_id = cl_row.id
            _write_json(run_dir / "reports.json", reports)
            if not letter_ready:
                raise RuntimeError(f"cover letter generation ended in status {meta['letter_status']!r}: {letter_err}")
            _write_json(run_dir / "letter.json", letter_data)

            # ---- pages -----------------------------------------------------
            advance("pages")
            pages: dict[str, int | None] = {"cv": None, "letter": None}
            if spec.measure_pages:
                pages = await measure_pages(cv_id, cl_id, uid, state)
            _write_json(run_dir / "pages.json", pages)

            # ---- collect ---------------------------------------------------
            advance("collect")
            async with session_factory() as db:
                gap = await db.get(GapAnalysis, seeded.gap_id)
                _write_json(run_dir / "ledger_effective.json", gap.keyword_ledger if gap else None)
            meta["usage"] = await _usage_summary(session_factory)
    finally:
        await engine.dispose()
        if not spec.keep_db:
            shutil.rmtree(work_dir, ignore_errors=True)


async def execute_run(spec: RunSpec) -> dict[str, Any]:
    """Run one generation (CV + letter) in THIS process and write its run directory.

    Never raises for a pipeline problem: the outcome is the returned/written meta.
    """
    run_dir = spec.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    started = _now()
    t0 = time.monotonic()
    meta = _base_meta(spec, started)
    _write_json(run_dir / "meta.json", meta)  # pessimistic: a hard kill leaves an honest failure
    state = _State()

    try:
        await asyncio.wait_for(_pipeline(spec, run_dir, meta, state), timeout=spec.timeout)
        meta.update(status="ok", failed_stage=None, error=None)
    except asyncio.TimeoutError:
        meta.update(status="failed", failed_stage=state.stage, error=f"timeout after {spec.timeout:.0f}s in stage {state.stage!r}")
    except BaseException as exc:  # noqa: BLE001 — KeyboardInterrupt is recorded too, then re-raised below
        (run_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        meta.update(status="failed", failed_stage=state.stage, error=f"{type(exc).__name__}: {exc}"[:2000])
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            _finish(meta, run_dir, t0, state)
            raise
    _finish(meta, run_dir, t0, state)
    return meta


def _finish(meta: dict[str, Any], run_dir: Path, t0: float, state: _State) -> None:
    meta.update(state.extra)
    meta["llm_records"] = finalize_llm_log(run_dir)
    meta["finished"] = _now()
    meta["duration_s"] = round(time.monotonic() - t0, 1)
    _write_json(run_dir / "meta.json", meta)


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fixture", action="append", required=True, metavar="DIR",
                   help="fixture directory tests/files/quality_bench/<case>/<cell> (repeatable)")
    p.add_argument("--runs", type=int, default=1, help="runs per fixture (default 1)")
    p.add_argument("--start-idx", type=int, default=0, help="first run index (default 0; use to add runs to an arm)")
    p.add_argument("--arm", required=True, help="arm name, e.g. baseline | A | B | C")
    p.add_argument("--provider", choices=["mock", "openrouter"], default="mock")
    p.add_argument("--model", default=None,
                   help="OpenRouter model id (sets OPENROUTER_MODEL for the run; recorded in meta.json)")
    p.add_argument("--out", required=True, help="output root directory")
    p.add_argument("--concurrency", type=int, default=1, help="parallel runs (process mode only; default 1)")
    p.add_argument("--timeout", type=float, default=1800.0, help="per-run timeout in seconds (default 1800)")
    p.add_argument("--no-pages", action="store_true", help="skip the post-run page-count render (no Chromium)")
    p.add_argument("--keep-db", action="store_true", help="keep each run's SQLite file under <run>/_work/")
    p.add_argument("--resume", action="store_true", help="skip runs whose meta.json already says ok")
    p.add_argument("--overwrite", action="store_true", help="replace existing run directories")
    p.add_argument("--in-process", action="store_true",
                   help="run in this interpreter instead of one subprocess per run (concurrency must be 1)")
    return p.parse_args(argv)


def _plan(args: argparse.Namespace, model: str) -> tuple[list[RunSpec], list[Path]]:
    """Specs to execute and the already-ok run dirs skipped by --resume."""
    specs: list[RunSpec] = []
    skipped: list[Path] = []
    for fixture_dir in args.fixture:
        try:
            fx = load_fixture(fixture_dir)
        except FixtureError as exc:
            raise ConfigError(f"fixture {fixture_dir}: {exc}") from exc
        for idx in range(args.start_idx, args.start_idx + args.runs):
            spec = RunSpec(
                fixture=fx, arm=args.arm, provider=args.provider, model=model, run_idx=idx,
                out_root=Path(args.out).resolve(), timeout=args.timeout,
                measure_pages=not args.no_pages, keep_db=args.keep_db,
            )
            rd = spec.run_dir
            if rd.exists():
                done = False
                try:
                    done = _read_json(rd / "meta.json").get("status") == "ok"
                except Exception:
                    pass
                if args.resume and done:
                    skipped.append(rd)
                    continue
                if not args.overwrite and not args.resume:
                    raise ConfigError(f"{rd} already exists (use --resume to skip finished runs, --overwrite to replace)")
                shutil.rmtree(rd)
            specs.append(spec)
    return specs, skipped


async def _run_subprocess(spec: RunSpec, sem: asyncio.Semaphore) -> dict[str, Any]:
    """One run as its own interpreter: own settings, own DB, killable on timeout."""
    async with sem:
        run_dir = spec.run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, str(Path(__file__).resolve()),
            "--fixture", str(spec.fixture.dir), "--runs", "1", "--start-idx", str(spec.run_idx),
            "--arm", spec.arm, "--provider", spec.provider, "--out", str(spec.out_root),
            "--timeout", str(spec.timeout), "--in-process", "--overwrite",
        ]
        if spec.provider == "openrouter":
            cmd += ["--model", spec.model]
        if not spec.measure_pages:
            cmd.append("--no-pages")
        if spec.keep_db:
            cmd.append("--keep-db")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_BACKEND), env.get("PYTHONPATH", "")) if p)
        env["PYTHONUNBUFFERED"] = "1"
        # The child's stdout/stderr go to worker.log; the run dir is wiped by --overwrite in
        # the child, so the log lives beside it in a sibling file.
        log_path = run_dir.parent / f"{run_dir.name}.worker.log"
        killed = False
        with open(log_path, "wb") as log:
            # cwd = the out root: no `.env` can be picked up from the caller's cwd.
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=log, stderr=asyncio.subprocess.STDOUT, env=env, cwd=str(spec.out_root),
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=spec.timeout + 90)
            except asyncio.TimeoutError:
                killed = True
                proc.kill()
                await proc.wait()
        # Fold the worker log into the run dir (extra artefact) once the child is done.
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(log_path), str(run_dir / "worker.log"))
        except OSError:
            pass
        try:
            meta = _read_json(run_dir / "meta.json")
        except Exception:
            meta = _base_meta(spec, _now())
        if killed or (proc.returncode not in (0, 1) and meta.get("status") != "ok"):
            reason = (
                f"worker killed after {spec.timeout + 90:.0f}s (timeout backstop)"
                if killed else f"worker exited with code {proc.returncode}"
            )
            meta.update(status="failed", error=f"{reason}; last stage {meta.get('failed_stage')!r}", finished=_now())
            meta["llm_records"] = finalize_llm_log(run_dir)
            shutil.rmtree(run_dir / "_work", ignore_errors=True)
            _write_json(run_dir / "meta.json", meta)
        return meta


def _summarise(meta: dict[str, Any], run_dir: Path) -> str:
    tail = "" if meta.get("status") == "ok" else f" stage={meta.get('failed_stage')} error={str(meta.get('error'))[:160]}"
    return f"{meta['cell_id']} run {meta['run_idx']}: {meta['status']}{tail}  -> {run_dir}"


async def _main_async(args: argparse.Namespace, specs: list[RunSpec]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if args.in_process:
        for spec in specs:
            results.append(await execute_run(spec))
        return results
    sem = asyncio.Semaphore(max(1, args.concurrency))
    tasks = [asyncio.create_task(_run_subprocess(s, sem)) for s in specs]
    for spec, task in zip(specs, tasks):
        meta = await task
        print(_summarise(meta, spec.run_dir), flush=True)
        results.append(meta)
    return results


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.runs < 1 or args.concurrency < 1:
            raise ConfigError("--runs and --concurrency must be >= 1")
        if args.in_process and args.concurrency != 1:
            raise ConfigError("--in-process requires --concurrency 1 (the product's DB handle and debug-log dir are process-wide)")
        model = configure_environment(args.provider, args.model)
        specs, skipped = _plan(args, model)
        if Path(".env").exists() and args.in_process and not os.environ.get("_QB_CHILD"):
            print("WARNING: ./.env exists in the cwd and pydantic Settings would read it; run from a "
                  "directory without one for a hermetic run.", file=sys.stderr)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for rd in skipped:
        print(f"skip (already ok): {rd}", flush=True)
    results = asyncio.run(_main_async(args, specs))
    if args.in_process:
        for spec, meta in zip(specs, results):
            print(_summarise(meta, spec.run_dir), flush=True)
    failed = [m for m in results if m.get("status") != "ok"]
    print(f"{len(results) - len(failed)} ok, {len(failed)} failed, {len(skipped)} skipped", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
