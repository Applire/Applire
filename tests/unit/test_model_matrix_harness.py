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
    assert len(fixtures.names()) == 10
    for shape in fixtures.names():
        data = fixtures.profile_data(shape)
        assert data["personal_info"]["name"] == "Lena Fischer"
        info = fixtures.new_info(shape)
        # Key ORDER is what the model reads — the spike's order, not a set.
        assert list(info) == ["gap", "question", "answer"]
        # S10 deliberately expects NO stations — its correct outcome is no ops
        # at all (a restated fact), so an empty list is not a fixture bug there.
        if shape == "S10_restated_fact_nothing_new":
            assert fixtures.expected_stations(shape) == []
        else:
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


def test_new_shapes_reproduce_their_own_goldens_byte_identically(fixtures):
    """S9 and S10 are DRIFT PINS for the two shapes added in Nougat build 2
    (WP-P2), not a comparison against the 2026-09-06 spike: there was never a
    spike run for a bullet-conflict turn or a restated-fact turn, so unlike
    ``test_fixtures_reproduce_the_spike_prompts_byte_identically`` above, this
    only proves the fixture keeps rendering today what it rendered the day the
    golden was committed."""
    for shape in (
        "S9_bullet_conflict_nova",
        "S10_restated_fact_nothing_new",
    ):
        golden = (mm.FIXTURE_DIR / "golden" / f"{shape}.user_prompt.txt").read_text(
            encoding="utf-8"
        )
        _, user = mm.render_prompts(fixtures, shape)
        assert mm.canonical_prompt(user) == golden, f"{shape} drifted from its own golden"


def test_shape_selection_accepts_prefixes_and_all(fixtures):
    assert mm.resolve_shapes(fixtures, "S6,S7") == [
        "S6_incident_shape_all_present",
        "S7_incident_shape_current_only",
    ]
    assert mm.resolve_shapes(fixtures, "all") == fixtures.names()
    # "S9" is now a real prefix (S9_bullet_conflict_nova, added build 2) — probe
    # with a token that stays unknown.
    with pytest.raises(SystemExit):
        mm.resolve_shapes(fixtures, "S99")


def test_shape_selection_resolves_s9_and_s10_by_prefix(fixtures):
    assert mm.resolve_shapes(fixtures, "S9,S10") == [
        "S9_bullet_conflict_nova",
        "S10_restated_fact_nothing_new",
    ]


def test_s9_profile_differs_from_lena_full_by_exactly_one_bullet(fixtures):
    """The S9 fixture (`profiles/lena_bullets.json`) must be a byte-faithful copy
    of `profiles/lena_full.json` with exactly one change: the `w-nova` entry gains
    one `achievements` bullet. Nothing else — not the ids, not the key order, not
    any other field — may differ."""
    with (mm.FIXTURE_DIR / "profiles" / "lena_full.json").open(encoding="utf-8") as fh:
        full = json.load(fh)
    with (mm.FIXTURE_DIR / "profiles" / "lena_bullets.json").open(encoding="utf-8") as fh:
        bullets = json.load(fh)
    assert full != bullets

    added = "Reduced batch-release turnaround for individualized therapies by 40%"
    nova_bullets = next(w for w in bullets["work_experience"] if w["id"] == "w-nova")
    assert nova_bullets["achievements"] == [added]

    import copy

    stripped = copy.deepcopy(bullets)
    stripped_nova = next(w for w in stripped["work_experience"] if w["id"] == "w-nova")
    stripped_nova["achievements"] = []
    assert stripped == full


def test_s10_answer_restates_a_line_already_in_the_vault(fixtures):
    """S10's correct outcome is no ops at all — verify the shape cannot silently
    stop being a "restated fact" shape: the vault must already carry, verbatim,
    the responsibility the answer restates."""
    shape = "S10_restated_fact_nothing_new"
    info = fixtures.new_info(shape)
    data = fixtures.profile_data(shape)
    nova = next(w for w in data["work_experience"] if w["id"] == "w-nova")
    restated = "Own technical strategy and roadmap for manufacturing-adjacent IT"
    assert restated in nova["responsibilities"]
    assert "technical strategy and roadmap for manufacturing-adjacent IT" in info["answer"]


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


def test_a_turn_that_only_asks_a_question_back_is_a_lost_turn(fixtures):
    """`cohere/command-r7b-12-2024` scored "qualified" on the first pass by
    emitting one `request_confirmation` and nothing else on 21 of 30 turns: it
    produced ops, so `zero_op` was False, and no bar caught it. The vault got
    nothing either way."""
    shape = "S6_incident_shape_all_present"
    asked = mm.classify(
        fixtures,
        shape,
        [{"op": "request_confirmation", "question": "Do you have insulin experience?",
          "options": ["Yes", "No"]}],
        [],
        None,
    )
    assert asked["zero_op"] is False       # it emitted something
    assert asked["no_write"] is True       # ...that wrote nothing
    assert asked["n_writing_ops"] == 0

    wrote = mm.classify(
        fixtures,
        shape,
        [{"op": "request_confirmation", "question": "?"},
         {"op": "add_bullets", "target": "w-helv", "technologies": ["MES"]}],
        [],
        None,
    )
    assert wrote["no_write"] is False


def test_a_transport_failure_is_not_counted_as_model_silence():
    """`reconcile()` swallows a timeout into an empty result, so a call that never
    returned looks exactly like a model that chose to say nothing. It made
    `qwen/qwen3.8-flash` read as 100 % zero-op on a shape where 10 of 10 calls had
    simply timed out."""
    shape = "S7_incident_shape_current_only"
    lost = _record(shape, 1, no_write=True, zero_op=True)
    lost["usage"] = {"calls": 0, "detail": []}
    assert mm.is_transport_failure(lost) is True

    answered = _record(shape, 2, no_write=True, zero_op=True)
    answered["usage"] = {"calls": 1, "detail": [{"completion_tokens": 178}]}
    assert mm.is_transport_failure(answered) is False

    # An empty body is the same class one layer up.
    empty = _record(shape, 3)
    empty["usage"] = {"calls": 1, "detail": [{"completion_tokens": 0}]}
    assert mm.is_transport_failure(empty) is True

    rows = [lost] * 5 + [
        dict(answered, run=i, usage={"calls": 1, "detail": [{"completion_tokens": 178}]})
        for i in range(5, 10)
    ]
    rates = mm.summarise(rows, [shape])["per_shape"][shape]
    assert rates["n"] == 10
    assert rates["valid"] == 5
    assert rates["transport_turns"] == 5
    assert rates["error_rate"] == 0.5
    # 5 of 5 ANSWERED turns lost the data — not 10 of 10.
    assert rates["zero_op_rate"] == 1.0


def test_score_reclassifies_from_the_ops_not_from_stored_metrics(tmp_path, fixtures):
    """Re-scoring exists so a classifier change reaches records already paid for."""
    shape = "S6_incident_shape_all_present"
    path = tmp_path / "stale.jsonl"
    path.write_text(
        json.dumps(
            {
                "shape": shape,
                "run": 1,
                "elapsed_s": 1.0,
                "ops": [{"op": "request_confirmation", "question": "?"}],
                "rejected_ops": [],
                # A metrics block from BEFORE `no_write` existed.
                "metrics": {"n_ops": 1, "zero_op": False, "malformed_ops": 0,
                            "wrong_slot": [], "disputes": 0, "set_summary": 0,
                            "station_coverage": 1.0, "skill_evidence_total": 0,
                            "skills_with_years_experience": 0, "kinds": []},
                "usage": {"calls": 1, "detail": [{"completion_tokens": 85}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    records, _ = mm.score_file(fixtures, path)
    assert records[0]["metrics"]["no_write"] is True


def test_s10_expects_no_stations_and_classifies_a_correct_empty_turn(fixtures):
    """S10's correct output is no ops at all. `expected_stations` must stay `[]`
    so `station_coverage` reads `None` rather than a misleading 0/0, and the
    resulting turn must classify as `no_write` — NOT as a model failure — so a
    caller that folds S10 into a `no_write` qualification verdict would
    misclassify the shape's own correct behaviour as a lost turn."""
    shape = "S10_restated_fact_nothing_new"
    assert fixtures.expected_stations(shape) == []
    metrics = mm.classify(fixtures, shape, [], [], None)
    assert metrics["station_coverage"] is None
    assert metrics["no_write"] is True
    assert metrics["zero_op"] is True


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
        "n_writing_ops": 1,
        "no_write": False,
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
    # A usage record with real completion tokens = the model answered. Without it
    # every synthetic row would classify as a transport failure.
    return {
        "shape": shape,
        "run": run,
        "elapsed_s": 1.0,
        "metrics": base,
        "usage": {"calls": 1, "detail": [{"prompt_tokens": 5000, "completion_tokens": 200}]},
    }


def test_verdict_is_subpar_once_a_threshold_is_crossed():
    shape = "S6_incident_shape_all_present"
    # 1 lost turn in 10 = 10 % > the 5 % bar. An empty batch is both zero_op
    # (nothing emitted) and no_write (nothing reached the vault).
    records = [_record(shape, 1, zero_op=True, no_write=True, n_writing_ops=0)] + [
        _record(shape, i) for i in range(2, 11)
    ]
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
    # The system prompt's size is printed per shape — a sanity anchor, not a pin:
    # #684 (Nougat) grew the reconcile prompt from 15,118 to 16,244 chars, and the
    # harness must report whatever the tree's prompt measures today.
    import re as _re
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT as _sys_prompt
    sizes = {int(m) for m in _re.findall(r"lena_\w+\s+(\d{4,6})\s+\d+", out)}
    assert sizes == {len(_sys_prompt)}, (sizes, len(_sys_prompt))


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
    # ADR-046 amended 2026-09-11 — the key is recorded on every run, `None`
    # included, so a measurement can tell "the model omitted it" from "the
    # harness never looked".
    assert "empty_reason" in record

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


def test_a_swallowed_schema_rejection_is_recorded_on_the_summary():
    """The harness silences `applire.providers.llm` (propagate=False), so the
    provider's structured-output fallback WARNING would vanish — and a row could
    say `llm_structured_output: auto` while every call after the first ran on
    plain JSON mode. Same instrument-defect class that hid
    `provider.aparse_json failed` on the first matrix.

    MUTATION KILL: remove the `_SCHEMA_REJECT_RE` branch from `_LogReader.emit`
    and the recorded list stays empty.
    """
    import logging

    mm._schema_rejections.clear()
    reader = mm._LogReader()
    reader.emit(
        logging.LogRecord(
            "applire.providers.llm.openrouter", logging.WARNING, __file__, 1,
            "model=%s rejected the response json_schema; falling back to plain "
            "JSON mode for this process (%s)",
            ("some/model", "400"), None,
        )
    )
    assert mm._schema_rejections, "the fallback WARNING was swallowed"
    assert "rejected the response json_schema" in mm._schema_rejections[0]
    mm._schema_rejections.clear()
