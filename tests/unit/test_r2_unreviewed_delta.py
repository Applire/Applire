# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""R-2 (UAT-3 review cards): the terminal-review check persists WHAT changed between
the document the last verdict read and the document that was delivered.

Unit tests for ``review_delta`` and ``build_terminal_review_check``, plus one seam
test per producer (CV, letter): the delta reaches the persisted ``ats_report``."""
import copy
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from applire.schemas.cv import TailoredCVData  # noqa: E402
from applire.services import review_delta  # noqa: E402
from applire.services.cv_section_editor import (  # noqa: E402
    _section_contents,
    build_content_snapshot,
    snapshot_entry_lines,
)
from applire.schemas.cv_sections import ContentSnapshot  # noqa: E402
from applire.services.terminal_review_outcome import (  # noqa: E402
    TerminalReviewOutcome,
    build_terminal_review_check,
)


def _cv(summary="Summary A.", bullets0=("Alpha one.",), bullets1=("Beta one.", "Beta two."),
        skills=("Python", "SQL")) -> TailoredCVData:
    return TailoredCVData.model_validate({
        "contact": {"name": "Anna Bauer"}, "summary": summary,
        "work_history": [
            {"id": "w-0", "company": "Acme", "role": "Dev", "start_date": "2020-01",
             "bullets": list(bullets0)},
            {"id": "w-1", "company": "Beta", "role": "Lead", "start_date": "2018-01",
             "end_date": "2019-12", "bullets": list(bullets1)},
        ],
        "skills": list(skills),
    })


def _contents(td: TailoredCVData, snapshot: dict) -> dict:
    snap = ContentSnapshot.model_validate(snapshot)
    return _section_contents(snap, {}, snapshot_entry_lines(snapshot, td.model_dump()))


def test_cv_delta_names_only_changed_sections():
    reviewed = _cv()
    delivered = _cv(summary="Summary B.", bullets1=("Beta one.", "Beta two corrected."))
    snapshot = build_content_snapshot(delivered)
    got = review_delta.cv_unreviewed_changes(reviewed, delivered, snapshot)
    assert len(got) == 2
    w1 = snapshot["positions"][1]["id"]
    assert {c["section_id"] for c in got} == {"introduction", f"position::{w1}"}
    before = _contents(reviewed, build_content_snapshot(reviewed))
    after = _contents(delivered, snapshot)
    by_id = {c["section_id"]: c for c in got}
    assert by_id["introduction"]["before"] == before["introduction"]
    assert by_id["introduction"]["after"] == after["introduction"]
    pos = by_id[f"position::{w1}"]
    assert pos["after"] == after[f"position::{w1}"]
    assert pos["before"] == "Beta one.\nBeta two."


def test_cv_delta_matches_positions_by_work_id_not_snapshot_id():
    a, b = _cv(), _cv()
    # two snapshots minted separately carry different uuid4 position ids
    assert build_content_snapshot(a)["positions"][0]["id"] != build_content_snapshot(b)["positions"][0]["id"]
    assert review_delta.cv_unreviewed_changes(a, b, build_content_snapshot(b)) == []


def test_cv_delta_is_bounded():
    big = "x" * 6000
    reviewed = TailoredCVData.model_validate({
        "contact": {"name": "Anna Bauer"}, "summary": "s",
        "work_history": [
            {"id": f"w-{i}", "company": "C", "role": "R", "start_date": "2020-01",
             "bullets": [f"old {i} " + big]} for i in range(12)
        ],
        "skills": [],
    })
    delivered = TailoredCVData.model_validate({
        "contact": {"name": "Anna Bauer"}, "summary": "s",
        "work_history": [
            {"id": f"w-{i}", "company": "C", "role": "R", "start_date": "2020-01",
             "bullets": [f"new {i} " + big]} for i in range(12)
        ],
        "skills": [],
    })
    got = review_delta.cv_unreviewed_changes(reviewed, delivered, build_content_snapshot(delivered))
    assert len(got) == 8
    assert all(len(c["before"]) <= 4000 and len(c["after"]) <= 4000 for c in got)


def test_cv_delta_excludes_ladder_cuts():
    """A skill cut by the page ladder AFTER the verdict is in neither the reviewed
    nor the final RAW compose difference — the page card already says it."""
    reviewed = _cv()
    final_raw = _cv()                       # the corrector changed nothing
    delivered = _cv(skills=("Python",))     # the ladder cut SQL afterwards
    got = review_delta.cv_unreviewed_changes(
        reviewed, delivered, build_content_snapshot(delivered), final_raw=final_raw
    )
    assert got == []
    # …while a real correction on the same document still shows
    final_raw2 = _cv(summary="Summary B.")
    delivered2 = _cv(summary="Summary B.", skills=("Python",))
    got2 = review_delta.cv_unreviewed_changes(
        reviewed, delivered2, build_content_snapshot(delivered2), final_raw=final_raw2
    )
    assert [c["section_id"] for c in got2] == ["introduction"]


def test_cv_delta_before_side_omits_ladder_cut_lines():
    reviewed = _cv(bullets1=("A", "B", "C"))
    final_raw = _cv(bullets1=("A", "B2", "C"))     # the corrector changed B -> B2
    delivered = _cv(bullets1=("A", "B2"))          # the ladder then cut C
    got = review_delta.cv_unreviewed_changes(
        reviewed, delivered, build_content_snapshot(delivered), final_raw=final_raw
    )
    assert len(got) == 1
    assert got[0]["before"] == "A\nB"
    assert got[0]["after"] == "A\nB2"
    assert "C" not in got[0]["before"].split("\n") + got[0]["after"].split("\n")


def _letter(*paragraphs):
    return {"header": {"name": "X"}, "body": {"paragraphs": list(paragraphs)}, "signature": {}}


def test_letter_delta_is_one_body_change():
    got = review_delta.letter_unreviewed_changes(_letter("Hi", "Old."), _letter("Hi", "New."))
    assert got == [{"section_id": "body", "before": "Hi\n\nOld.", "after": "Hi\n\nNew."}]
    assert review_delta.letter_unreviewed_changes(_letter("Hi"), _letter("Hi")) == []


def test_letter_delta_includes_post_loop_condense():
    """The delta is measured against the FINAL letter_data, so a rewrite made after
    the verdict (condense, floor, limit cut) is part of it."""
    from applire.services.cover_letter import _with_unreviewed_delta

    reviewed = _letter("Hi", "A long paragraph that the verdict read.")
    outcome = TerminalReviewOutcome(
        chain_id="letter_terminal_review", path="exhausted", approved=False,
        blocking_issues=("x",), minor_issues=(), rounds=1, reviewed_subject=reviewed,
    )

    class _CL:
        id = uuid.uuid4()
        letter_data = _letter("Hi", "Condensed.")

    got = _with_unreviewed_delta(outcome, _CL())
    assert got.unreviewed_changes == [
        {"section_id": "body", "before": "Hi\n\nA long paragraph that the verdict read.",
         "after": "Hi\n\nCondensed."}
    ]


_CHANGES = [{"section_id": "introduction", "before": "A", "after": "B"}]


def _unverified():
    # `exhausted` + no measured correction = a correction landed after the verdict
    return TerminalReviewOutcome(
        chain_id="cv_terminal_review", path="exhausted", approved=False,
        blocking_issues=("Claim A is unsupported.",), minor_issues=(), rounds=2,
    )


def test_unverified_check_carries_changes():
    outcome = _unverified()
    assert not outcome.findings_stand_against_delivered
    check = build_terminal_review_check(outcome, unreviewed_changes=_CHANGES)
    assert check.details_key == "terminal-review-unverified"
    assert [c.model_dump() for c in check.unreviewed_changes] == _CHANGES
    # a reviewed delivery never carries one
    reviewed = TerminalReviewOutcome(
        chain_id="cv_terminal_review", path="approved", approved=True,
        blocking_issues=(), minor_issues=(), rounds=1,
    )
    clean = build_terminal_review_check(reviewed, unreviewed_changes=_CHANGES)
    assert clean.unreviewed_changes is None and clean.unreviewed_changes_hash is None
    # nor does an empty delta persist as []
    assert build_terminal_review_check(outcome, unreviewed_changes=[]).unreviewed_changes is None


def test_carry_forward_keeps_changes():
    first = build_terminal_review_check(_unverified(), unreviewed_changes=_CHANGES)
    carried = build_terminal_review_check(None, previous=first.model_dump())
    assert [c.model_dump() for c in carried.unreviewed_changes] == _CHANGES
    assert carried.unreviewed_changes_hash == first.unreviewed_changes_hash


def test_changes_hash_is_stable_and_content_bound():
    a = build_terminal_review_check(_unverified(), unreviewed_changes=_CHANGES)
    b = build_terminal_review_check(_unverified(), unreviewed_changes=copy.deepcopy(_CHANGES))
    assert a.unreviewed_changes_hash == b.unreviewed_changes_hash
    assert len(a.unreviewed_changes_hash) == 16
    int(a.unreviewed_changes_hash, 16)
    other = [{**_CHANGES[0], "after": "C"}]
    c = build_terminal_review_check(_unverified(), unreviewed_changes=other)
    assert c.unreviewed_changes_hash != a.unreviewed_changes_hash


# --- seams: the producer wires the delta to the persisted report ----------------

def _sibling(name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cvt = _sibling("test_cv_terminal_review")
ltt = _sibling("test_letter_terminal_review")


@pytest.fixture(autouse=True)
def _v1_pipelines(monkeypatch):
    monkeypatch.setenv("CV_PIPELINE", "v1")
    monkeypatch.setenv("LETTER_PIPELINE", "v1")


db = cvt.db


@pytest.mark.asyncio
async def test_cv_producer_wires_delta(db):
    from applire.models.cv import GeneratedCV

    ids = await cvt._seed(db)
    captured: list = []
    rounds = {"n": 0}

    def _corrected(draft):
        rounds["n"] += 1
        out = copy.deepcopy(draft)
        out["work"][0]["bullets"] = [f"Correction {rounds['n']} nobody re-read."]
        return out

    await cvt._run_pipeline(
        db, ids, captured=captured,
        review_fake=cvt._fake_review_settling(
            captured, settled_draft=_corrected,
            blocking=("The bullet claims more than the profile supports.",),
        ),
    )
    record = await db.get(GeneratedCV, ids[2])
    check = {c["id"]: c for c in record.ats_report["checks"]}["terminal-review"]
    assert check["details_key"] == "terminal-review-unverified", check
    changes = check["unreviewed_changes"]
    assert len(changes) == 1, changes
    snap_id = record.content_snapshot["positions"][0]["id"]
    assert changes[0]["section_id"] == f"position::{snap_id}"
    assert "Correction 1" in changes[0]["before"]
    assert "Correction 2" in changes[0]["after"]
    assert len(check["unreviewed_changes_hash"]) == 16


@pytest.mark.asyncio
async def test_letter_producer_wires_delta(db):
    from applire.models.cover_letter import GeneratedCoverLetter
    from applire.services import cover_letter as cl_mod
    from applire.services.review_issues import ReviewSettle

    ids = await ltt._seed(db)
    n = {"i": 0}

    async def settling_fake(**kwargs):
        if kwargs.get("chain_id") != "letter_terminal_review":
            return kwargs["draft"]
        reviewed = kwargs["draft"]
        kwargs["reviewer_prompt_fn"](kwargs["source"], reviewed)
        n["i"] += 1
        body = dict(reviewed["body"])
        body["paragraphs"] = list(body["paragraphs"]) + [f"Correction {n['i']} nobody re-read."]
        settled = {**reviewed, "body": body}
        kwargs["on_settle"](ReviewSettle(
            path="exhausted", approved=False,
            blocking_issues=("The opening claims a figure without support.",),
            minor_issues=(), rounds=1, settled=settled,
        ))
        return settled

    seen: list = []
    real = cl_mod._update_ats_report_letter

    async def spy(cl, db_, *a, **kw):
        seen.append(kw.get("terminal_review"))
        return await real(cl, db_, *a, **kw)

    await ltt._run_pipeline(
        db, ids,
        extra_patches=(
            patch("applire.services.cover_letter.review_and_refine", side_effect=settling_fake),
            patch("applire.services.cover_letter._update_ats_report_letter", new=spy),
        ),
    )
    assert seen and seen[-1] is not None
    changes = seen[-1].unreviewed_changes
    assert changes and changes[0]["section_id"] == "body", changes
    assert "Correction" in changes[0]["after"]
    check = build_terminal_review_check(seen[-1])
    assert check.details_key == "terminal-review-unverified"
    assert check.unreviewed_changes[0].section_id == "body"
    assert len(check.unreviewed_changes_hash) == 16
    cl = await db.get(GeneratedCoverLetter, ids[2].id)
    persisted = {c["id"]: c for c in (cl.ats_report or {}).get("checks", [])}.get("terminal-review")
    assert persisted is not None, cl.ats_report
    assert persisted["unreviewed_changes"][0]["section_id"] == "body"
    assert len(persisted["unreviewed_changes_hash"]) == 16
