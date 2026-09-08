# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""US311 / #688 — hermetic smoke test for the opt-in model-matrix harness.

The harness itself (``scripts/model_matrix.py``) only ever runs by hand against a
real provider. Without this test it would rot silently the first time the
reconcile op schema, the engine signature or a fixture field moves — and the rot
would only be discovered mid-measurement, with credit already spent. So CI runs
the whole pipeline on the mock provider (n=1, one shape) and pins the two things
the harness cannot re-derive at runtime:

* the fixtures render the SPIKE's S6/S7 prompts byte-identically (the goldens in
  ``tests/files/model_matrix/golden/`` were generated from
  ``Documents/Runs/Nougat/summary-seed-spike-2026-09-06/replay_multistation.py``
  itself, modulo the per-run UUIDs that spike re-minted on every invocation), and
* the classifier's own arithmetic — a wrong-slot op, a zero-op turn and a
  malformed op each have to move exactly the rate they claim to move.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "model_matrix.py"


def _load_harness():
    """Import the script by path — it is deliberately not an importable package."""
    backend = str(REPO_ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    spec = importlib.util.spec_from_file_location("model_matrix_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mm = _load_harness()


@pytest.fixture(scope="module")
def fixtures():
    return mm.Fixtures(mm.FIXTURE_DIR)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def test_every_shape_resolves_to_a_committed_profile(fixtures):
    assert len(fixtures.names()) == 8
    for shape in fixtures.names():
        data = fixtures.profile_data(shape)
        assert data["personal_info"]["name"] == "Lena Fischer"
        info = fixtures.new_info(shape)
        # Key ORDER is what the model reads — the spike's order, not a set.
        assert list(info) == ["gap", "question", "answer"]
        assert fixtures.expected_stations(shape)


def test_the_vault_fixture_is_synthetic(fixtures):
    """No real profile may ever enter a committed fixture (E060 §3.1 boundary)."""
    for shape in fixtures.names():
        blob = json.dumps(fixtures.profile_data(shape), ensure_ascii=False)
        assert "example.com" in blob
        assert "@" not in blob.replace("lena.fischer@example.com", "")


def test_fixtures_reproduce_the_spike_prompts_byte_identically(fixtures):
    for shape in (
        "S6_incident_shape_all_present",
        "S7_incident_shape_current_only",
    ):
        golden = (mm.FIXTURE_DIR / "golden" / f"{shape}.user_prompt.txt").read_text(
            encoding="utf-8"
        )
        _, user = mm.render_prompts(fixtures, shape)
        assert mm.canonical_prompt(user) == golden, f"{shape} drifted from the spike"


def test_shape_selection_accepts_prefixes_and_all(fixtures):
    assert mm.resolve_shapes(fixtures, "S6,S7") == [
        "S6_incident_shape_all_present",
        "S7_incident_shape_current_only",
    ]
    assert mm.resolve_shapes(fixtures, "all") == fixtures.names()
    with pytest.raises(SystemExit):
        mm.resolve_shapes(fixtures, "S9")


# --------------------------------------------------------------------------- #
# Classifier
# --------------------------------------------------------------------------- #
def test_wrong_slot_fires_only_when_the_text_names_another_station(fixtures):
    shape = "S6_incident_shape_all_present"
    right = mm.classify(
        fixtures,
        shape,
        [{"op": "add_bullets", "target": "w-helv", "responsibilities": ["MES at Helvetia Pharma"]}],
        [],
        None,
    )
    assert right["wrong_slot"] == []
    assert right["stations_covered"] == ["helv"]

    wrong = mm.classify(
        fixtures,
        shape,
        [{"op": "add_bullets", "target": "w-helv", "responsibilities": ["blood bags at NovaRNA"]}],
        [],
        None,
    )
    assert len(wrong["wrong_slot"]) == 1
    assert wrong["wrong_slot"][0]["target_station"] == "helv"
    assert wrong["wrong_slot"][0]["text_stations"] == ["bsd", "nova"]

    # A bullet that names no station at all is UNKNOWN, never wrong-slot.
    silent = mm.classify(
        fixtures, shape, [{"op": "add_bullets", "target": "w-helv", "technologies": ["MES"]}], [], None
    )
    assert silent["wrong_slot"] == []


def test_zero_op_and_malformed_are_counted_separately(fixtures):
    shape = "S6_incident_shape_all_present"
    empty = mm.classify(fixtures, shape, [], ["upsert_work"], None)
    assert empty["zero_op"] is True
    assert empty["malformed_ops"] == 1
    assert empty["station_coverage"] == 0.0


def test_skill_metrics_read_years_experience_defensively(fixtures):
    """#684 adds ``years_experience`` to ``UpsertSkill``; the harness must not
    require it today and must count it the day it lands."""
    shape = "S6_incident_shape_all_present"
    before = mm.classify(
        fixtures, shape, [{"op": "upsert_skill", "name": "GMP", "evidence": ["w-helv", "w-bsd"]}], [], None
    )
    assert before["skill_evidence_total"] == 2
    assert before["skills_with_years_experience"] == 0

    after = mm.classify(
        fixtures,
        shape,
        [{"op": "upsert_skill", "name": "GMP", "evidence": ["w-helv"], "years_experience": 15}],
        [],
        None,
    )
    assert after["skills_with_years_experience"] == 1


# --------------------------------------------------------------------------- #
# Summary and verdict
# --------------------------------------------------------------------------- #
def _record(shape, run, **metrics):
    base = {
        "n_ops": 1,
        "zero_op": False,
        "malformed_ops": 0,
        "wrong_slot": [],
        "disputes": 0,
        "set_summary": 0,
        "station_coverage": 1.0,
        "skill_evidence_total": 0,
        "skills_with_years_experience": 0,
        "kinds": [],
    }
    base.update(metrics)
    return {"shape": shape, "run": run, "elapsed_s": 1.0, "metrics": base, "usage": {"calls": 1}}


def test_verdict_is_subpar_once_a_threshold_is_crossed():
    shape = "S6_incident_shape_all_present"
    # 1 zero-op turn in 10 = 10 % > the 5 % zero-op bar.
    records = [_record(shape, 1, zero_op=True)] + [_record(shape, i) for i in range(2, 11)]
    summary = mm.summarise(records, [shape])
    assert summary["per_shape"][shape]["zero_op_rate"] == 0.1
    assert summary["verdict"]["label"] == "sub-par"
    assert "zero_op_rate" in summary["verdict"]["crossings"][0]


def test_verdict_is_caveat_when_a_loss_stays_under_every_bar():
    shape = "S6_incident_shape_all_present"
    # 1 malformed turn in 10 = 10 %, which is AT the bar, not over it.
    records = [_record(shape, 1, malformed_ops=2)] + [_record(shape, i) for i in range(2, 11)]
    summary = mm.summarise(records, [shape])
    assert summary["per_shape"][shape]["malformed_op_rate"] == 0.1
    assert summary["verdict"] == {
        "label": "caveat",
        "crossings": [],
        "thresholds": dict(mm.THRESHOLDS),
    }


def test_verdict_is_qualified_on_a_clean_sweep():
    shape = "S6_incident_shape_all_present"
    summary = mm.summarise([_record(shape, i) for i in range(1, 11)], [shape])
    assert summary["verdict"]["label"] == "qualified"


def test_an_errored_turn_counts_as_an_error_not_a_zero_op():
    shape = "S6_incident_shape_all_present"
    records = [{"shape": shape, "run": 1, "elapsed_s": 1.0, "error": "TimeoutError: x"}] + [
        _record(shape, i) for i in range(2, 11)
    ]
    summary = mm.summarise(records, [shape])
    rates = summary["per_shape"][shape]
    assert rates["error_rate"] == 0.1
    # A turn that never reached the model is NOT a turn the model lost.
    assert rates["zero_op_rate"] == 0.0
    assert summary["verdict"]["label"] == "caveat"

    # Two of ten crosses the 10 % error bar.
    records = [
        {"shape": shape, "run": i, "elapsed_s": 1.0, "error": "TimeoutError: x"} for i in (1, 2)
    ] + [_record(shape, i) for i in range(3, 11)]
    assert mm.summarise(records, [shape])["verdict"]["label"] == "sub-par"


# --------------------------------------------------------------------------- #
# End to end on the mock provider
# --------------------------------------------------------------------------- #
def test_dry_run_needs_no_provider(capsys):
    assert mm.main(["--dry-run", "--shapes", "all"]) == 0
    out = capsys.readouterr().out
    assert "S8_incident_shape_big_profile" in out
    # The system prompt's measured size (#688's table) — a sanity anchor, not a pin.
    assert " 15118" in out


def test_end_to_end_against_the_mock_provider(tmp_path, capsys):
    out = tmp_path / "smoke.jsonl"
    assert (
        mm.main(
            [
                "--provider",
                "mock",
                "--n",
                "1",
                "--shapes",
                "S6",
                "--concurrency",
                "1",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["shape"] == "S6_incident_shape_all_present"
    assert not record.get("error")
    assert record["metrics"]["n_ops"] > 0
    assert record["metrics"]["zero_op"] is False
    assert "apply" in record["metrics"]  # apply_ops ran in memory, no DB

    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["provider"] == "mock"
    # The provider the run actually built, not the one it was asked for: inside a
    # suite that has already imported applire.config, setting the environment is
    # too late and --provider was silently ignored (a run mislabelled as another
    # model). force_settings() makes the singleton agree or refuses to run.
    assert summary["meta"]["settings"]["llm_provider"] == "mock"
    assert summary["per_shape"]["S6_incident_shape_all_present"]["n"] == 1
    assert summary["verdict"]["label"] in ("qualified", "caveat", "sub-par")
    assert "VERDICT:" in capsys.readouterr().out


def test_score_mode_reads_the_spike_record_format(tmp_path, capsys, fixtures):
    """`--score` must accept a raw spike record (ops + rejected_ops, no metrics),
    because the published table's baseline rows are re-derived from those files."""
    shape = "S7_incident_shape_current_only"
    path = tmp_path / "spike.jsonl"
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"shape": shape, "run": 1, "ops": [], "rejected_ops": ["upsert_work"], "elapsed_s": 1.3},
                {
                    "shape": shape,
                    "run": 2,
                    "elapsed_s": 1.1,
                    "ops": [
                        {
                            "op": "add_bullets",
                            "target": "w-nova",
                            "achievements": ["blood bags at the blood donation service"],
                        }
                    ],
                    "rejected_ops": [],
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert mm.main(["--score", str(path)]) == 0
    out = capsys.readouterr().out
    assert "re-scored" in out
    records, shapes = mm.score_file(fixtures, path)
    assert shapes == [shape]
    assert records[0]["metrics"]["zero_op"] is True
    assert records[0]["metrics"]["malformed_ops"] == 1
    # The second turn parks two other employers' facts on the current station.
    assert len(records[1]["metrics"]["wrong_slot"]) == 1


def test_provider_override_wins_over_an_already_imported_settings(tmp_path):
    """The regression the full unit suite found: with ``applire.config`` already
    imported, setting the environment is too late — the singleton kept
    ``LLM_PROVIDER=mistral``, ``--provider mock`` was ignored, and the "mock" run
    built a MistralProvider and reached for the network. A matrix row that can be
    mislabelled with another model is worse than no row."""
    from applire.config import settings

    before = (settings.llm_provider, settings.mistral_model)
    settings.llm_provider = "mistral"
    settings.mistral_model = "mistral-large-latest"
    out = tmp_path / "override.jsonl"
    try:
        assert (
            mm.main(
                ["--provider", "mock", "--n", "1", "--shapes", "S6", "--concurrency", "1",
                 "--out", str(out)]
            )
            == 0
        )
    finally:
        settings.llm_provider, settings.mistral_model = before

    summary = json.loads(out.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["settings"]["llm_provider"] == "mock"
    record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    # The mock answered; the un-overridden path produced an empty result instead.
    assert not record.get("error")
    assert record["metrics"]["n_ops"] > 0


def test_rejection_is_explained_by_field_and_error_type():
    """"12 malformed ops" is not actionable; "ref missing" and "team_size not an
    int" are different prompt problems with different fixes."""
    missing_ref = mm.explain_rejection(
        "upsert_work", "{'op': 'upsert_work', 'company': 'NovaRNA', 'role': 'Lead'}"
    )
    assert missing_ref["errors"] == ["upsert_work.ref:missing"]
    assert missing_ref["keys"] == ["company", "op", "role"]

    bad_type = mm.explain_rejection(
        "upsert_work",
        "{'op': 'upsert_work', 'ref': 'w1', 'company': 'X', 'role': 'Y', 'team_size': '38 people'}",
    )
    assert bad_type["errors"] == ["upsert_work.team_size:int_parsing"]

    # An unparseable payload degrades, it never raises.
    assert "explain_error" in mm.explain_rejection("upsert_work", "not a literal <object>")


def test_log_reader_captures_the_engines_dropped_op_warning():
    """`ReconcileResult.rejected_ops` carries only the op LABEL — the payload
    survives nowhere but engine.py's #602 warning line."""
    import logging

    mm.install_log_readers()
    sink: list[dict] = []
    token = mm._reject_sink.set(sink)
    try:
        logging.getLogger("applire.services.profile.reconcile.engine").warning(
            "reconcile: dropped malformed op (op=%s): %r",
            "upsert_work",
            {"op": "upsert_work", "company": "NovaRNA", "role": "Lead"},
        )
    finally:
        mm._reject_sink.reset(token)
    assert len(sink) == 1
    assert sink[0]["errors"] == ["upsert_work.ref:missing"]

    records = [
        {
            "shape": "S6_incident_shape_all_present",
            "run": 1,
            "elapsed_s": 1.0,
            "metrics": _record("S6_incident_shape_all_present", 1, malformed_ops=1)["metrics"],
            "rejected_detail": sink,
            "usage": {"calls": 1},
        }
    ]
    summary = mm.summarise(records, ["S6_incident_shape_all_present"])
    assert summary["rejection_reasons"] == {"upsert_work.ref:missing": 1}


def test_usage_handler_reads_the_providers_own_token_line():
    """The counts come from the provider's `response.usage` INFO line — the
    harness adds no seam in ``providers/llm/`` (WP-O1's territory this flavour)."""
    import logging

    mm.install_log_readers()
    sink: list[dict] = []
    token = mm._usage_sink.set(sink)
    try:
        logging.getLogger("applire.providers.llm.openrouter").info(
            "LLM response [aparse_json] model=%s latency=%.2fs "
            "prompt_tokens=%s completion_tokens=%s finish=%s",
            "z-ai/glm-5.3-flash",
            3.5,
            4321,
            876,
            "stop",
        )
        # Requesty logs the same line without the `finish=` tail.
        logging.getLogger("applire.providers.llm.requesty").info(
            "LLM response [acomplete] model=%s latency=%.2fs "
            "prompt_tokens=%s completion_tokens=%s",
            "openai/gpt-5.6-luna",
            1.0,
            10,
            20,
        )
    finally:
        mm._usage_sink.reset(token)
    assert [(u["prompt_tokens"], u["completion_tokens"]) for u in sink] == [(4321, 876), (10, 20)]
    assert sink[0]["model"] == "z-ai/glm-5.3-flash"
