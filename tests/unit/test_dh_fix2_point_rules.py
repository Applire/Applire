# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-G32 fix round 2 (Sorbet invite-gate adversarial pass X-3…X-10): what is a point.

* X-3: a deterministic code fact about TRUTH is a point whatever any finding's class is;
  every ``cv.*``/``letter.*`` check key sits on one of two lists, an unlisted key is a
  point and fails the maintenance test below. The hold is monotone.
* X-4: a point is never hidden behind "… and N more"; hidden advice counts nothing.
* X-5: a fold of two outcomes resolves a class conflict for one finding to truth.
* X-6: a class is the exact word; a hedge or a conflicting duplicate index is truth.
* X-9: the agent relays a sentence, never the corrector's instruction or a profile id.
* X-10: the class call's usage row carries its own stage.
The rows run through the producer, the served check and the agent envelope."""
from __future__ import annotations

import itertools
import json
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.ats import ATSReport  # noqa: E402
from applire.services import point_rules, review_signals  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    TerminalNote,
    TerminalReviewOutcome,
    build_terminal_review_check,
)

_SERVICES = _backend / "applire" / "services"


def _producer_keys() -> set[str]:
    """Every check key a CV or letter deterministic check can raise, read from the
    producers' source (``check="cv.…"``, ``key="letter.…"``, the letter v2 families)."""
    keys: set[str] = set()
    for f in ("cv_v2.py", "cv.py", "cover_letter.py"):
        src = (_SERVICES / f).read_text(encoding="utf-8")
        keys |= set(re.findall(r'check="((?:cv|letter)\.[a-z_]+)"', src))
        # a TerminalNote key only when the note is a code fact (kind "note"/"finding" are not)
        for m in re.finditer(r'key="((?:cv|letter)\.[a-z_]+)"', src):
            if 'kind="code"' in src[max(0, m.start() - 200):m.start()]:
                keys.add(m.group(1))
        if f == "cover_letter.py":
            keys |= {f"letter.{fam}" for fam in re.findall(r'LetterFinding\(\s*"([a-z_]+)"', src)}
    return keys


def test_every_code_key_is_on_exactly_one_list():
    keys = _producer_keys()
    assert len(keys) >= 30  # the scan sees the producers
    unlisted = keys - set(point_rules.TRUTH_CODE_KEYS) - set(point_rules.ADVICE_CODE_KEYS)
    assert unlisted == set(), f"put each on TRUTH_CODE_KEYS or ADVICE_CODE_KEYS with a reason: {unlisted}"
    assert set(point_rules.TRUTH_CODE_KEYS) & set(point_rules.ADVICE_CODE_KEYS) == set()
    assert all(r.strip() for r in [*point_rules.TRUTH_CODE_KEYS.values(), *point_rules.ADVICE_CODE_KEYS.values()])


def test_an_unknown_code_key_is_a_point():
    assert point_rules.is_point({"kind": "code", "key": "cv.something_new", "text": "x"})


# ── X-3: monotone hold, through the producer and the served check ────────────


def _held(blocking, tags, code_keys):
    # as the CV loop writes it: an open code fact is also a "[key] text" blocking issue
    outcome = TerminalReviewOutcome(
        chain_id="cv_terminal_review", path="cycle_detected", approved=False,
        blocking_issues=tuple(blocking) + tuple(f"[{k}] {k} fact" for k in code_keys), minor_issues=(), rounds=3,
        notes=tuple(TerminalNote(kind="code", key=k, text=f"{k} fact", subjects=("x",)) for k in code_keys),
        point_tags=tuple(tags),
    )
    check = build_terminal_review_check(outcome).model_dump(mode="json", exclude_none=True)
    ats = {"version": 1, "document": "cv", "passed": 0, "failed": 1, "not_applicable": 0,
           "checks": [check], "keywords": {"present": [], "missing": [], "present_unsupported": []}}
    ats = json.loads(json.dumps(ATSReport.model_validate(ats).model_dump(mode="json")))
    rec = SimpleNamespace(ats_report=ats, review_state=None, truthfulness_report=None, origin="pipeline",
                          audit_state=None)
    served = review_signals.open_points_check(rec)
    return 0 if served is None else served["details_params"]["count"]


def test_a_truth_code_fact_holds_next_to_advice_findings():
    """The X-3 case: two coverage findings + a truth code fact — held by the fact."""
    tags = [("gap a", "coverage"), ("gap b", "coverage")]
    assert _held(["gap a", "gap b"], tags, ["cv.skill_untied", "cv.skills_cap"]) == 1
    assert _held(["gap a", "gap b"], tags, ["cv.skills_cap"]) == 0


def test_adding_advice_never_releases_a_point():
    advice_findings = [("gap a", "coverage"), ("form b", "form")]
    advice_codes = ["cv.skills_cap", "cv.claim_missing", "letter.closing"]
    for truth_codes in ([], ["cv.skill_untied"], ["letter.new_figure", "cv.recorded_name"]):
        for truth_findings in ([], [("claim c", "truth")], [("claim d", "")]):
            base = _held([t for t, _ in truth_findings], truth_findings, truth_codes)
            for n_f, n_c in itertools.product(range(3), range(4)):
                more_f = truth_findings + advice_findings[:n_f]
                got = _held([t for t, _ in more_f], more_f, truth_codes + advice_codes[:n_c])
                assert got >= base, (truth_codes, truth_findings, n_f, n_c)
                assert got == len(truth_codes) + len(truth_findings)


# ── X-4: a point is never behind "… and N more" ─────────────────────────────


def test_many_points_are_all_listed_and_counted_and_hidden_advice_counts_nothing():
    truth = [f"untrue claim {i}." for i in range(8)]
    advice = [f"posting gap {i}." for i in range(9)]
    tags = [(t, "truth") for t in truth] + [(a, "coverage") for a in advice]
    assert _held(truth + advice, tags, []) == 8
    outcome = TerminalReviewOutcome(
        chain_id="cv_terminal_review", path="cycle_detected", approved=False,
        blocking_issues=tuple(truth + advice), minor_issues=(), rounds=3, point_tags=tuple(tags))
    notes = [n.model_dump(mode="json", exclude_none=True) for n in build_terminal_review_check(outcome).notes]
    assert [n["text"] for n in notes if n.get("text") in truth] == truth
    (more,) = [n for n in notes if n.get("key") == "more"]
    assert {k: more.get(k) for k in ("kind", "key", "count", "advice")} == {
        "kind": "finding", "key": "more", "count": 3, "advice": True}
    assert not point_rules.is_point(more)


# ── X-5: a fold resolves a class conflict to truth ──────────────────────────


def _o(tags, notes=()):
    return TerminalReviewOutcome(chain_id="letter_terminal_review", path="cycle_detected", approved=False,
                                 blocking_issues=("the claim.",), minor_issues=(), rounds=1,
                                 point_tags=tuple(tags), notes=tuple(notes))


@pytest.mark.parametrize("order", [0, 1])
def test_a_fold_with_conflicting_classes_is_truth(order):
    a, b = _o([("the claim.", "form")]), _o([("the claim.", "truth")])
    folded = (a.worse_of(b) if order == 0 else b.worse_of(a))
    assert dict(folded.point_tags) == {"the claim.": "truth"}
    folded = (a.after(b) if order == 0 else b.after(a))
    assert dict(folded.point_tags)["the claim."] == "truth"


def test_a_fold_keeps_one_note_per_finding_text():
    n_form = TerminalNote(kind="finding", key="letter.post_condense", text="Figure unanchored.", tag="form")
    n_truth = TerminalNote(kind="finding", key="letter.post_condense", text="Figure unanchored.", tag="truth")
    folded = _o([], [n_form]).worse_of(_o([], [n_truth]))
    assert [(n.text, n.tag) for n in folded.notes] == [("Figure unanchored.", "truth")]
    same = _o([], [n_form]).worse_of(_o([], [n_form]))
    assert [(n.text, n.tag) for n in same.notes] == [("Figure unanchored.", "form")]


# ── X-6: exact class words ──────────────────────────────────────────────────


@pytest.mark.parametrize("answer,expected", [
    ([{"index": 0, "class": " Coverage "}], ["coverage"]),
    ([{"index": 0, "class": "form, but the claim is also untrue"}], ["truth"]),
    ([{"index": 0, "class": "coverage/truth"}], ["truth"]),
    ([{"index": 0, "class": "coverage (wanted by the posting)"}], ["truth"]),
    ([{"index": 0, "class": "form"}, {"index": 0, "class": "truth"}], ["truth"]),
    ([{"index": 0, "class": "form"}, {"index": 0, "class": "coverage"}], ["truth"]),
    ([{"index": 0, "class": "form"}, {"index": 0, "class": "form"}], ["form"]),
    ([{"index": True, "class": "form"}], [""]),
])
def test_a_class_is_the_exact_word(answer, expected):
    from applire.services.point_class import parse_classes

    assert parse_classes({"classes": answer}, 1) == expected


# ── X-9: the agent relays a sentence ────────────────────────────────────────

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_INSTRUCTION = re.compile(r"\b(Return these|Rewrite|Remove them|Drop entries|by id|never translate)\b")


def test_the_agent_envelope_carries_sentences_not_instructions():
    from applire.mcp.server import _advice_line

    notes = [
        {"kind": "code", "key": "cv.vault_field_missing", "subjects": [],
         "text": "Return these profile fields, by id, written in the document language: 3f2a9c1e-0b7d-4e55-9a61-2c8d1f0e7b42"},
        {"kind": "code", "key": "cv.skills_cap", "subjects": ["S3", "Go"],
         "text": "The skills list is over its cap of 18 entries; … Drop entries until it fits."},
        {"kind": "code", "key": "cv.skill_untied", "subjects": ["Kubernetes"],
         "text": "Skills entries with no tie to the profile: Kubernetes. Remove them from the skills list."},
        {"kind": "finding", "key": None, "text": "w1 (3f2a9c1e-0b7d-4e55-9a61-2c8d1f0e7b42): unbacked.", "tag": "coverage"},
        {"kind": "finding", "key": "more", "count": 4, "advice": True},
        {"kind": "code", "key": "cv.something_new", "subjects": ["x"], "text": "Rewrite it by id 3f2a9c1e-0b7d-4e55-9a61-2c8d1f0e7b42"},
    ]
    for n in notes:
        line = _advice_line(n)
        assert line["plain"] and not _UUID.search(line["plain"]) and not _INSTRUCTION.search(line["plain"]), line
    assert _advice_line(notes[1])["plain"] == "The skills list is longer than planned – the least relevant entries: S3, Go."
    assert _advice_line(notes[4])["plain"] == "… and 4 more."


@pytest.mark.asyncio
async def test_the_served_envelope_findings_carry_sentences():
    import importlib

    server = importlib.import_module("applire.mcp.server")
    rec_notes = [
        {"kind": "finding", "key": None, "text": "w1: Rewrite the bullet; profile id 3f2a9c1e-0b7d-4e55-9a61-2c8d1f0e7b42.",
         "plain": None, "subjects": []},
        {"kind": "code", "key": "cv.skill_untied", "text": "Skills entries … Remove them from the skills list.",
         "subjects": ["Kubernetes"]},
    ]
    ats = {"version": 1, "document": "cv", "passed": 0, "failed": 1, "not_applicable": 0,
           "checks": [{"id": "terminal-review", "status": "fail", "details": "x", "details_key": "terminal-review-open",
                       "details_params": {"count": 2}, "notes": rec_notes, "open_points_hash": "c" * 16}],
           "keywords": {"present": [], "missing": [], "present_unsupported": []}}
    rec = SimpleNamespace(id=uuid.uuid4(), ats_report=ats, review_state=None, truthfulness_report=None,
                          origin="pipeline", audit_state=None)
    result: dict = {}
    assert server._render_held(result, rec) is True
    findings = result["open_points"]["findings"]
    assert len(findings) == 2 == result["open_points"]["count"]
    for f in findings:
        assert f["plain"] and not _UUID.search(f["plain"]) and not _INSTRUCTION.search(f["plain"]), f
    assert findings[1]["plain"] == (
        "“Kubernetes” is in the skills list but is not a skill in your profile. "
        "If your profile backs it, say it in the bullet with the evidence.")


# ── X-10: the class call's usage row names its stage ────────────────────────


@pytest.mark.asyncio
async def test_the_class_call_usage_row_carries_its_stage():
    from applire.providers.llm import usage
    from applire.services.point_class import STAGE, classify_open_points

    rows: list[dict] = []

    async def sink(row):
        rows.append(row)

    class _Inner:
        _model = "m"

        async def aparse_json(self, prompt, *, system=None, **kw):
            return {"classes": [{"index": 0, "class": "coverage"}]}

    doc_id, app_id = uuid.uuid4(), uuid.uuid4()
    usage.set_sink(sink)
    try:
        provider = usage.UsageRecordingProvider(_Inner())
        outcome = TerminalReviewOutcome(chain_id="cv_terminal_review", path="cycle_detected", approved=False,
                                        blocking_issues=("The CV omits SAP PM.",), minor_issues=(), rounds=1)
        with usage.llm_usage_context(stage="cv", document_kind="cv", document_id=doc_id, application_id=app_id):
            got = await classify_open_points(outcome, document={"summary": "x"}, provider=provider)
            assert usage.current_attribution().stage == "cv"  # restored after the call
    finally:
        usage.restore_sink()
    assert dict(got.point_tags) == {"The CV omits SAP PM.": "coverage"}
    (row,) = rows
    assert row["stage"] == STAGE and row["document_id"] == doc_id and row["application_id"] == app_id
    assert row["document_kind"] == "cv"
