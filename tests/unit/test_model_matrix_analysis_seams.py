# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#766 — the job-analysis and gap-analysis seams of ``scripts/model_matrix.py``.

The #688 prompt sitting (rows 3 and 5) allows a simplification of
``gap_analysis`` / ``job_analysis`` only with a before/after row; these seams
produce it. Mock provider only — nothing here reaches a network.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "model_matrix.py"


def _load_harness():
    for p in (str(REPO_ROOT / "backend"), str(REPO_ROOT / "scripts")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("model_matrix_seams_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mm = _load_harness()

_LOGGERS = ("applire.providers.llm", "applire.services.profile.reconcile.engine", "applire.llm.review")


@pytest.fixture(autouse=True)
def _restore_loggers():
    """``install_log_readers`` turns propagation off on the review logger; leaving it
    off would blind every later ``caplog`` test of REVIEW_* lines in this process."""
    saved = {n: (logging.getLogger(n).level, logging.getLogger(n).propagate, list(logging.getLogger(n).handlers))
             for n in _LOGGERS}
    yield
    for n, (level, propagate, handlers) in saved.items():
        lg = logging.getLogger(n)
        lg.setLevel(level)
        lg.propagate = propagate
        lg.handlers[:] = handlers


def test_cells_resolve_by_type_id_and_all_and_skip_the_smoke_fixture():
    same = mm.analysis_cells("same")
    assert [f"{d.parent.name}.{d.name}" for d in same] == [
        "controlling_emma_de.same_de", "it_backend_daniel.same_en",
        "nursing_priya_relocator.same_en", "operations_marcus_de.same_de",
    ]
    assert [d.name for d in mm.analysis_cells("it_backend_daniel.upward_en")] == ["upward_en"]
    everything = mm.analysis_cells("all")
    assert len(everything) == 14 and not any(d.parent.name.startswith("_") for d in everything)
    with pytest.raises(SystemExit):
        mm.analysis_cells("no_such_cell")


def test_review_lines_are_read_from_the_producers_own_log_calls():
    """Seam test on the real producer: the lines ``debug_log.log_review_*`` emit
    are what the reader parses — a wording change there must fail here."""
    from applire.providers.llm import debug_log

    mm.install_log_readers()
    events: list[dict] = []

    async def emit():
        mm._review_sink.set(events)
        debug_log.log_review_verdict("job_analysis", 1, 2, approved=False, issues_count=3)
        debug_log.log_review_verdict("job_analysis", 2, 2, approved=True, issues_count=0)
        debug_log.log_review_exhausted("gap_x", 2, 1)
        debug_log.log_review_cycle_detected("gap_y", 2, 3)
        debug_log.log_review_call_failed("gap_z", "reviewer", 1, "LLMTimeoutError")

    asyncio.run(emit())
    assert [(e["kind"], e["chain"], e["attempt"], e["approved"]) for e in events] == [
        ("verdict", "job_analysis", 1, False),
        ("verdict", "job_analysis", 2, True),
        ("exhausted", "gap_x", None, None),
        ("cycle_detected", "gap_y", 2, None),
        ("call_failed", "gap_z", 1, None),
    ]
    assert mm.review_outcome(events, "job_analysis") == {"rounds": 2, "path": "approved", "first_round_approved": False}
    assert mm.review_outcome(events, "gap_x")["path"] == "exhausted"
    assert mm.review_outcome(events, "gap_y")["path"] == "cycle"
    assert mm.review_outcome(events, "gap_z")["path"] == "call_failed"
    assert mm.review_outcome(events, "absent")["path"] == "no_review"
    # outside a run (no sink) the reader drops the line silently
    logging.getLogger("applire.llm.review").info("REVIEW_VERDICT chain=x attempt=1/2 approved=True issues=0")


def test_gap_scoring_against_the_frozen_reference():
    ref = {"keyword_ledger": [{"concept": "Python", "status": "direct"}, {"concept": "Kafka", "status": "gap"},
                              {"concept": "SLOs", "status": "partial"}]}
    got = {"keyword_ledger": [{"concept": "python", "status": "direct"}, {"concept": "Kafka", "status": "partial"},
                              {"concept": "Go", "status": "gap"}], "gap_clusters": [{}], "category_c": ["Go"]}
    m = mm.score_gap(got, ref, [])
    assert m["n_ledger"] == 3 and m["ledger_statuses"] == {"direct": 1, "gap": 1, "partial": 1}
    assert m["concepts_jaccard_vs_reference"] == 0.5          # python, kafka of 4 distinct
    assert m["status_agreement_vs_reference"] == 0.5         # python agrees, kafka does not
    assert m["n_clusters"] == 1 and m["n_category_c"] == 1


@pytest.mark.parametrize("seam", ["jd", "gap"])
def test_mock_run_end_to_end_through_the_product_entry_point(seam, tmp_path, monkeypatch):
    for key in ("LLM_PROVIDER", "LLM_DEBUG_LOG", "LLM_DEBUG_LOG_DIR"):
        monkeypatch.setenv(key, "")
    out = tmp_path / f"{seam}.jsonl"
    assert mm.main(["--seam", seam, "--provider", "mock", "--n", "1", "--cells", "it_backend_daniel.same_en",
                    "--concurrency", "1", "--out", str(out)]) == 0
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1 and "error" not in records[0], records[0].get("error")
    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["seam"] == seam and summary["overall"]["runs"] == 1 and summary["overall"]["errors"] == 0
    assert summary["meta"]["cells"] == ["it_backend_daniel.same_en"]
    prompts = summary["meta"]["prompts"]
    assert set(prompts) == ({"job_analysis", "job_analysis_refinement", "job_analysis_review"} if seam == "jd"
                            else {"gap_analysis", "gap_clustering"})
    if seam == "jd":
        assert records[0]["metrics"]["role_title_present"] is True
        assert records[0]["metrics"]["review"]["rounds"] >= 1          # the review loop ran and was read
    else:
        assert records[0]["metrics"]["n_ledger"] > 0
        # a FRESH analysis, never the frozen row read back (a seeded GapAnalysis
        # would make analyze_gaps a cache hit — the measured call would not happen)
        assert records[0]["metrics"]["concepts_jaccard_vs_reference"] < 1


def test_the_gap_seam_seeds_no_previous_analysis(tmp_path):
    """A seeded GapAnalysis would be the 'previous' row: analyze_gaps then carries
    its clusters forward (ADR-089 cl. 4) and skips the clustering call — the
    measurement would no longer be a first analysis."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import applire.models  # noqa: F401
    from applire import ownership
    from applire.db.session import Base
    from applire.models.flow import FlowSession
    from applire.models.gap import GapAnalysis
    from quality_bench.fixture import load_fixture
    from quality_bench.seed import seed_fixture

    async def go():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/s.sqlite3")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        fx = load_fixture(mm.analysis_cells("it_backend_daniel.same_en")[0])
        seeded = await seed_fixture(factory, fx, with_gap=False)
        with ownership.unscoped("tooling"):
            async with factory() as db:
                n = (await db.execute(select(func.count()).select_from(GapAnalysis))).scalar_one()
                flow = await db.get(FlowSession, seeded.flow_id)
        await engine.dispose()
        return n, flow.gap_analysis_id

    assert asyncio.run(go()) == (0, None)
