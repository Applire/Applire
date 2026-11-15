# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""MD-Q8 (Sorbet WP-Q3, ruling Q3-5 A): a skills chip added after the truth
round's audit, reported untied by the code and backed by nothing the Oracle's
deterministic producer reads, is dropped at delivery with a removal row.

Built from the captured delivery-1b run (ba8b1d7c, synthetic persona Katrin
Hoffmann): reviewer rec 312 asked for "Gruppenebene" (check 6), the last,
unreviewed corrector round rec 318 added it as a chip and in the summary; the
delivered Oracle graded the chip unbacked → stop_and_fix."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

FIX = json.loads(
    (Path(__file__).resolve().parents[1] / "files/q3_md_q8_katrin_delivery_1b.json").read_text()
)


def _late(**over):
    from applire.services.attribution_round import late_unbacked_chips

    kw = dict(
        audited_skills=FIX["audited"]["skills"],
        untied=FIX["untied_on_shipped"],
        profile_json=FIX["profile"],
        keyword_ledger=FIX["keyword_ledger"],
    )
    kw.update(over)
    return late_unbacked_chips(FIX["shipped"]["skills"], **kw)


def test_the_captured_late_chip_is_dropped_and_its_backed_sibling_stays():
    # before (ba8b1d7c): both chips shipped, "Gruppenebene" graded unbacked
    assert {"Gruppenebene", "Erste Führungserfahrung"} <= set(FIX["shipped"]["skills"])
    # after: only the chip nothing backs; "Erste Führungserfahrung" is untied too,
    # but the Oracle's ledger arm backs it (the delivered Oracle graded it grounded)
    assert _late() == ["Gruppenebene"]


def test_a_chip_the_truth_round_audited_is_not_dropped():
    # "Projektcontrolling" is deterministic-unbacked, but the audited draft held it:
    # it keeps the ruled class-A residue path, even if the tie check named it
    assert "Projektcontrolling" in FIX["audited"]["skills"]
    assert "Projektcontrolling" not in _late(untied=["Projektcontrolling", "Gruppenebene"])


def test_a_late_unbacked_chip_the_tie_check_does_not_name_stays():
    audited = [s for s in FIX["audited"]["skills"] if s != "Projektcontrolling"]
    assert _late(audited_skills=audited, untied=["Gruppenebene"]) == ["Gruppenebene"]


def test_an_unknown_audit_baseline_drops_nothing():
    assert _late(audited_skills=None) == []


def _record_and_v2():
    from applire.services.cv_v2 import compose_v2
    from applire.services.model_ids import ShortIdMap

    prof = FIX["profile"]
    prose = ShortIdMap.from_profile(prof).restore(json.loads(json.dumps(FIX["shipped"])))
    composed = compose_v2(prose, prof, raw_profile_json=prof, document_language="de")
    record = SimpleNamespace(id="cv-1", tailored_data=composed.model_dump(), content_snapshot=None)

    class _V2:
        notes_by_draft: dict = {}

        def delivered_findings(self, _prose, _data):
            return [SimpleNamespace(check="cv.skill_untied", subjects=tuple(FIX["untied_on_shipped"]))]

    return record, prose, composed, _V2()


def test_the_drop_reaches_the_delivered_data_the_prose_and_the_raw_compose():
    from applire.services.cv import _drop_late_unbacked_chips

    record, prose, composed, v2 = _record_and_v2()
    new_prose, new_raw, late = _drop_late_unbacked_chips(
        record, prose, composed, v2,
        audited_skills=FIX["audited"]["skills"],
        profile_json=FIX["profile"], keyword_ledger=FIX["keyword_ledger"],
    )
    assert "Gruppenebene" not in record.tailored_data["skills"]
    assert "Gruppenebene" not in new_prose["skills"] and "Gruppenebene" not in new_raw.skills
    assert "Erste Führungserfahrung" in record.tailored_data["skills"]
    # the summary sentence the delivered Oracle grounds stays as written
    assert "auf Gruppenebene" in record.tailored_data["summary"]
    # run 1's profile already recorded the denial "eigene Projektcontrolling-Projekte"
    # (A4), so the widened arm takes that chip too (the run-1 delivered Oracle had
    # grounded it through a model seam; the denial now decides)
    assert [(f.kind, f.text, f.family) for f in late] == [
        ("oracle:skillNoEvidence", "Projektcontrolling", "unbacked"),
        ("oracle:skillNoEvidence", "Gruppenebene", "unbacked"),
    ]


def test_the_removal_row_is_the_receipt_even_when_the_word_lives_on_in_a_sentence():
    from applire.services.attribution_round import TruthRoundResult, late_chip_findings
    from applire.services.finding_registry import delivered_report

    record, *_ = _record_and_v2()
    data = dict(record.tailored_data)
    data["skills"] = [s for s in data["skills"] if s != "Gruppenebene"]
    tr = TruthRoundResult({}, [], [])
    tr.late_removed.extend(late_chip_findings(["Gruppenebene"], FIX["shipped"]["skills"]))
    out = delivered_report({}, review_degradation=None, truthfulness=None,
                           delivered_data=data, truth_round=tr)
    rows = out["delivery"]["removals"]
    assert [(r["text"], r["kind"], r["section"]) for r in rows] == [
        ("Gruppenebene", "oracle:skillNoEvidence", "skills")
    ]


def test_the_row_kind_is_the_one_the_review_card_regrounds():
    from applire.services.attribution_round import SKILL_CHIP_KIND
    from applire.services.review_actions import SKILL_REMOVAL_KIND

    assert SKILL_CHIP_KIND == SKILL_REMOVAL_KIND


@pytest.mark.parametrize("needle", [
    "truth_audit_skills = list(prose_draft.get(\"skills\") or [])",
    "await _late_chips()",
])
def test_the_generation_tail_wires_the_drop(needle):
    src = (Path(__file__).resolve().parents[2] / "backend/applire/services/cv.py").read_text()
    assert needle in src
    if needle == "await _late_chips()":
        # after the first terminal review AND after a re-entry round, before the hash
        assert src.count(needle) == 2
        assert src.index(needle) < src.index("verdict_hash = _subject_hash(record.tailored_data)")


# ── MD-Q9 R10: the baseline is the draft AFTER the truth round ────────────────
def test_a_chip_the_truth_round_removed_and_a_later_round_re_added_is_dropped_md_q9_r10():
    """Re-check R10: the truth round removes "Gruppenebene" (skillNoEvidence), a
    terminal corrector re-adds it. Against the PRE-round skills it read as audited
    and shipped; against the post-round draft it is late and dropped."""
    pre_round = FIX["audited"]["skills"] + ["Gruppenebene"]
    post_round = FIX["audited"]["skills"]
    assert _late(audited_skills=pre_round) == []  # the leak the re-check found
    assert _late(audited_skills=post_round) == ["Gruppenebene"]


def test_the_generation_tail_takes_the_baseline_after_the_round_md_q9_r10():
    src = (Path(__file__).resolve().parents[2] / "backend/applire/services/cv.py").read_text()
    after_round = src.index("prose_draft = truth_round.draft")
    baseline = src.rindex('truth_audit_skills = list(prose_draft.get("skills") or [])')
    assert after_round < baseline < src.index("await _late_chips()")


# ── MD-Q8 widened (delivery-1b re-run at 7017f7ab): a denied, unbacked chip ────
RERUN = json.loads(
    (Path(__file__).resolve().parents[1] / "files/q3_md_q8_katrin_rerun_denied.json").read_text()
)


def test_a_chip_a_recorded_denial_covers_and_nothing_backs_is_dropped():
    """Re-run: "Projektcontrolling" was in the reviewed draft (so not late) and the
    tie check did not name it; the interview recorded "Weitere eigene
    Projektcontrolling-Projekte geleitet" (A4) and the delivered Oracle graded the
    chip unbacked. "Kostenrechnung" is deterministic-unbacked too but no denial
    covers it (the delivered Oracle grounded it) — it stays."""
    from applire.services.attribution_round import denied_unbacked_chips

    assert "Projektcontrolling" in RERUN["reviewed_skills"]
    assert denied_unbacked_chips(RERUN["shipped_skills"], profile_json=RERUN["profile"],
                                 keyword_ledger=None) == ["Projektcontrolling"]


def test_a_chip_the_person_recorded_is_never_taken_even_under_a_denial():
    from applire.services.attribution_round import denied_unbacked_chips

    prof = json.loads(json.dumps(RERUN["profile"]))
    prof["skills"].append({"name": "Projektcontrolling", "status": "confirmed"})
    assert denied_unbacked_chips(RERUN["shipped_skills"], profile_json=prof, keyword_ledger=None) == []


def test_the_delivery_drop_takes_the_denied_chip_although_it_was_audited():
    from applire.services.cv import _drop_late_unbacked_chips

    record, prose, _composed, _v2 = _record_and_v2()
    record.tailored_data = {**record.tailored_data, "skills": list(RERUN["shipped_skills"])}
    prose = {**prose, "skills": list(RERUN["shipped_skills"])}

    class _NoUntied:
        notes_by_draft: dict = {}

        def delivered_findings(self, _p, _d):
            return []

    _p, _raw, late = _drop_late_unbacked_chips(
        record, prose, None, _NoUntied(),
        audited_skills=RERUN["reviewed_skills"],  # audited: the late arm takes nothing
        profile_json=RERUN["profile"], keyword_ledger=None,
    )
    assert [f.text for f in late] == ["Projektcontrolling"]
    assert "Projektcontrolling" not in record.tailored_data["skills"]
    assert "Kostenrechnung" in record.tailored_data["skills"]


def test_a_chip_the_ledger_backs_is_kept_under_a_denial_cover():
    """The producer decides with the denial: a claimable ledger row that names the
    chip beside a form the vault holds backs it (the job's recorded statement that
    they are one competence), so the chip stays although the denial text covers it."""
    from applire.services.attribution_round import denied_unbacked_chips
    from applire.services.oracle.audit import _claimable_index
    from applire.services.oracle.backing import _denied

    ledger = [{"concept": "Projektcontrolling", "surface_forms": ["Projektcontrolling", "Percentage of Completion"],
               "claimable": True, "status": "direct", "sources": ["required"]}]
    assert _denied(["Projektcontrolling"], _claimable_index(RERUN["profile"]))
    assert denied_unbacked_chips(["Projektcontrolling"], profile_json=RERUN["profile"],
                                 keyword_ledger=ledger) == []
