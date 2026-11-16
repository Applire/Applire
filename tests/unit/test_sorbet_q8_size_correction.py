# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Q8-6 (founder ruling, Sorbet wave 2b) — a corrected team size meets a bullet
that still names the old number: one follow-up per bullet, "Auf N ändern" /
"So lassen", and the CV never prints both numbers.

Found by the delivery run on 82154267: Stefan answered "Disziplinarisch – 12
Personen" over a stored 14; the CV printed "Führung von 12 Mitarbeitenden"
beside his bullet "Schicht mit 14 Mitarbeitenden" (two panel readers named it).
"""
from __future__ import annotations

import pytest

from applire.schemas.cv import TailoredCVData
from applire.schemas.profile import MasterProfileData
from applire.services.interview.scope_question import (
    SIZE_BULLET_ASKED_STATE_KEY,
    leadership_kind_question,
    queue_size_bullet_asks,
    size_bullet_decision,
    size_bullet_question,
)
from applire.services.profile.leadership_kind import leadership_kind_op
from applire.services.profile.reconcile.apply import apply_ops
from applire.services.profile.reconcile.ops import (
    MergeEntries,
    ReplaceSection,
    ResolveSizeCorrection,
    SetField,
)
from applire.services.profile.size_correction import (
    SizeCorrectionNotFound,
    changed_text,
    qualifying_spans,
    size_line_withheld,
)
from applire.services.prompt_view import prompt_profile_view

SHIFT = "Führung einer Schicht mit 14 Mitarbeitenden in der Blechumformung."
MACHINES = "Rollout auf 14 Spritzgussmaschinen."
OTHER = "Moderation von 5S- und Kaizen-Workshops."


def _profile(**over) -> MasterProfileData:
    work = {"id": "w1", "company": "Rasselstein Umformtechnik GmbH",
            "role": "Fertigungsmeister / Schichtleiter", "team_size": 14,
            "responsibilities": [SHIFT, OTHER], "achievements": [MACHINES]}
    work.update(over)
    return MasterProfileData.model_validate({"work_experience": [work]})


def _correct(profile=None, size=12, option="disciplinary"):
    return apply_ops(profile or _profile(),
                     [leadership_kind_op("w1", option, answer=f"Disziplinarisch – {size} Personen",
                                         team_size=size)], "interview")


# ── which bullets qualify (structural; a doubt asks) ─────────────────────────

@pytest.mark.parametrize(("text", "ok"), [
    (SHIFT, True),                                                    # (a) people word next
    ("Führung von 14 direkten Mitarbeitern", True),                   # (a) within two words
    ("Leitete ein 14-köpfiges Team", True),                           # (a) "köpfig"
    ("Led a team of 14 across two plants", True),                     # (b) doubt: a lead stem
    ("Team von 14 im Werk Kassel", True),                             # (b) "team" is a lead stem
    ("Leitete 14 Schichtleiter", True),                               # people before the "schicht" unit
    ("Führte 14 im Werk Kassel", True),                               # (b) doubt: a lead stem, no unit
    (MACHINES, False),                                                # no lead word, a machine
    ("Leitete die Fertigung an 14 Maschinen", False),                 # a non-people unit
    ("Senkte den Ausschuss um 14 % in der Schicht", False),           # a percentage
    ("Seit 2014 Teamleiter", False),                                  # not a whole token
    ("Budget von 14,5 Mio €", False),                                 # a decimal part
    ("Leitete 140 Mitarbeitende", False),                             # another number
])
def test_which_bullets_qualify(text, ok):
    assert bool(qualifying_spans(text, 14)) is ok


def test_change_touches_only_the_qualifying_number():
    text = "Leitete 2014 bis 2019 eine Schicht mit 14 Mitarbeitenden an 14 Maschinen"
    assert changed_text(text, 14, 12) == "Leitete 2014 bis 2019 eine Schicht mit 12 Mitarbeitenden an 14 Maschinen"


# ── the kind answer that corrects the size writes the records ────────────────

def test_a_corrected_size_records_each_bullet_naming_the_old_number():
    res = _correct()
    [w] = res.profile.work_experience
    assert w.team_size == 12
    assert [(r.text, r.old, r.new, r.status) for r in w.size_corrections] == [(SHIFT, 14, 12, "open")]
    assert size_line_withheld(w)


@pytest.mark.parametrize(("stored", "answered"), [(14, 14), (None, 12)])
def test_a_confirmed_or_first_size_records_nothing(stored, answered):
    res = _correct(_profile(team_size=stored), size=answered)
    assert res.profile.work_experience[0].size_corrections == []


# ── the person's answer: change / keep ──────────────────────────────────────

def test_change_puts_the_new_number_into_that_bullet_only():
    p = _correct().profile
    res = apply_ops(p, [ResolveSizeCorrection(entry_id="w1", text=SHIFT, decision="change")], "interview")
    [w] = res.profile.work_experience
    new = "Führung einer Schicht mit 12 Mitarbeitenden in der Blechumformung."
    assert w.responsibilities == [new, OTHER]
    assert w.achievements == [MACHINES]
    assert w.size_corrections == [] and not size_line_withheld(w)
    [c] = [c for c in res.changes if c.field == "responsibilities"]
    assert (c.old_value, c.new_value, c.rationale_key) == (SHIFT, new, "team_size_bullet_changed")


def test_keep_leaves_the_bullet_and_withholds_the_line():
    p = _correct().profile
    res = apply_ops(p, [ResolveSizeCorrection(entry_id="w1", text=SHIFT, decision="keep")], "interview")
    [w] = res.profile.work_experience
    assert w.responsibilities == [SHIFT, OTHER]
    assert [r.status for r in w.size_corrections] == ["kept"]
    assert size_line_withheld(w)


def test_a_missing_record_names_no_bullet_text():
    with pytest.raises(SizeCorrectionNotFound) as exc:
        apply_ops(_profile(), [ResolveSizeCorrection(entry_id="w1", text=SHIFT, decision="change")],
                  "interview")
    assert "Mitarbeitenden" not in str(exc.value)


def test_an_edited_bullet_ends_the_record():
    p = _correct().profile
    payload = [{**p.work_experience[0].model_dump(mode="json"),
                "responsibilities": ["Schichtführung in der Blechumformung.", OTHER]}]
    res = apply_ops(p, [ReplaceSection(section="work_experience", value=payload)], "manual_edit")
    [w] = res.profile.work_experience
    assert w.responsibilities[0] == "Schichtführung in der Blechumformung."
    assert w.size_corrections == [] and not size_line_withheld(w)


# ── forged writes ────────────────────────────────────────────────────────────

FORGED = [{"text": OTHER, "field": "responsibilities", "old": 5, "new": 9, "status": "kept"}]


@pytest.mark.parametrize("source", ["interview", "agent_interview", "testimony", "cv_upload", "manual_edit"])
def test_a_set_field_cannot_forge_or_wipe_a_record(source):
    p = _correct().profile
    forged = apply_ops(p, [SetField(target="w1", field="size_corrections", value=FORGED)], source)
    assert [r.text for r in forged.profile.work_experience[0].size_corrections] == [SHIFT]
    wiped = apply_ops(p, [SetField(target="w1", field="size_corrections", value=[])], source)
    assert [r.text for r in wiped.profile.work_experience[0].size_corrections] == [SHIFT]


def test_the_section_door_carries_the_stored_records():
    p = _correct().profile
    payload = [{**p.work_experience[0].model_dump(mode="json"), "size_corrections": FORGED}]
    res = apply_ops(p, [ReplaceSection(section="work_experience", value=payload)], "manual_edit")
    assert [r.text for r in res.profile.work_experience[0].size_corrections] == [SHIFT]
    payload = [{k: v for k, v in p.work_experience[0].model_dump(mode="json").items()
                if k != "size_corrections"}]
    res = apply_ops(p, [ReplaceSection(section="work_experience", value=payload)], "manual_edit")
    assert [r.text for r in res.profile.work_experience[0].size_corrections] == [SHIFT]


def test_the_resolve_op_is_never_model_emittable():
    from applire.services.profile.reconcile.engine import _parse_ops

    raw = [{"op": "resolve_size_correction", "entry_id": "w1", "text": SHIFT, "decision": "change"}]
    assert _parse_ops(raw) == []


def test_a_merge_moves_the_records_to_the_surviving_entry():
    p = _correct().profile
    p = MasterProfileData.model_validate({"work_experience": [
        {"id": "w0", "company": "Rasselstein", "role": "Schichtleiter", "responsibilities": [OTHER]},
        p.work_experience[0].model_dump(mode="json"),
    ]})
    res = apply_ops(p, [MergeEntries(section="work_experience", target_id="w0", source_id="w1",
                                     bullets={"responsibilities": [OTHER, SHIFT]})], "manual_edit")
    [w] = res.profile.work_experience
    assert [r.text for r in w.size_corrections] == [SHIFT]


# ── the two numbers never appear together ────────────────────────────────────

def test_the_cv_prints_no_size_line_while_a_record_names_a_bullet():
    from applire.services.cv import _apply_role_facts

    tailored = TailoredCVData.model_validate({
        "contact": {"name": "Synthetic Candidate"}, "summary": "", "skills": [], "education": [],
        "work_history": [{"id": "w1", "company": "Rasselstein", "role": "Schichtleiter",
                          "start_date": "2004-09", "bullets": ["x"]}],
    })
    for decision, withheld in (("keep", True), ("change", False)):
        p = _correct().profile
        p = apply_ops(p, [ResolveSizeCorrection(entry_id="w1", text=SHIFT, decision=decision)],
                      "interview").profile
        got = _apply_role_facts(tailored, p.model_dump(mode="json")).work_history[0]
        assert (got.team_size is None) is withheld
        assert (got.leadership_kind is None) is withheld
    open_ = _apply_role_facts(tailored, _correct().profile.model_dump(mode="json")).work_history[0]
    assert open_.team_size is None   # unanswered follow-up: withheld too


def test_no_prompt_view_carries_the_size_or_the_records():
    view = prompt_profile_view(_correct().profile.model_dump(mode="json"))
    [w] = view["work_experience"]
    assert "team_size" not in w and "size_corrections" not in w


# ── the interview follow-up ──────────────────────────────────────────────────

def _cluster():
    item = {"entry_id": "w1", "company": "Rasselstein", "role": "Schichtleiter",
            "text": SHIFT, "field": "responsibilities", "old": 14, "new": 12}
    return {"id": "size-bullet-1", "kind": "size_bullet", "entry": item, "entries": [item]}


def test_the_question_and_its_closed_answers():
    de = size_bullet_question(_cluster(), "de")
    assert de["question"] == ("Du hast die Teamgröße auf 12 korrigiert. Dein Punkt nennt noch 14: "
                              f"»{SHIFT}«")
    assert de["choices"] == ["Auf 12 ändern", "So lassen"]
    en = size_bullet_question(_cluster(), "en")
    assert en["question"] == f"You corrected the team size to 12. Your bullet still says 14: “{SHIFT}”"
    assert en["choices"] == ["Change to 12", "Leave as is"]


@pytest.mark.parametrize(("message", "decision"), [
    ("Auf 12 ändern", "change"), ("auf 12 ändern.", "change"), ("Change to 12", "change"),
    ("So lassen", "keep"), ("Leave as is", "keep"),
    ("Auf 13 ändern", None), ("ja, ändern", None), ("So lassen, aber eigentlich 12", None), ("", None),
])
def test_only_the_exact_closed_answer_decides(message, decision):
    assert size_bullet_decision(_cluster(), message) == decision


def test_the_follow_ups_are_queued_right_after_the_current_cluster_once():
    profile_json = _correct().profile.model_dump(mode="json")
    state = {"critical_gaps": ["a", "leadership-kind", "b"], "gap_clusters_by_id": {},
             "gap_categories": {}, "hard_ceiling": 5}
    assert queue_size_bullet_asks(state, profile_json, 1) == 1
    assert state["critical_gaps"] == ["a", "leadership-kind", "size-bullet-1", "b"]
    assert state["hard_ceiling"] == 6
    assert state["gap_clusters_by_id"]["size-bullet-1"]["entry"]["text"] == SHIFT
    assert queue_size_bullet_asks(state, profile_json, 1) == 0
    assert len(state[SIZE_BULLET_ASKED_STATE_KEY]) == 1


def test_a_kept_record_is_not_asked_again():
    p = apply_ops(_correct().profile,
                  [ResolveSizeCorrection(entry_id="w1", text=SHIFT, decision="keep")], "interview").profile
    state = {"critical_gaps": [], "gap_clusters_by_id": {}, "gap_categories": {}, "hard_ceiling": 1}
    assert queue_size_bullet_asks(state, p.model_dump(mode="json"), 0) == 0


# ── delivery observation (1): the kind ask follows the size stored NOW ──────

def test_the_kind_ask_planned_unsized_is_shown_with_the_size_stored_since():
    """Katrin, delivery run on 82154267: planned unsized at session start, her
    scope answer stored 3 before the ask was shown — she gets the confirm shape."""
    entry = {"entry_id": "w1", "company": "Schwarzwald Präzision GmbH", "role": "Financial Controller",
             "team_size": None, "said": "Fachliche Anleitung von zwei Werkstudierenden."}
    cluster = {"id": "leadership-kind", "kind": "leadership_kind", "entry": entry, "entries": [entry]}
    profile_json = {"work_experience": [{"id": "w1", "team_size": 3}]}
    q = leadership_kind_question(cluster, "de", profile_json)
    assert "Im Profil stehen 3 Personen – stimmt das?" in q["question"]
    assert q["choices"][1] == "Fachliche Anleitung – 3 Personen"
    unsized = leadership_kind_question(cluster, "de", {"work_experience": [{"id": "w1"}]})
    assert unsized["choices"][1].endswith("Anzahl Personen:")



def test_the_class_guard_alone_puts_a_forged_record_back():
    """The committer's guard on its own (the section door's carry is a second,
    independent defence): an untouched entry keeps what it held before."""
    from applire.services.profile.size_correction import restore_size_corrections

    before = _correct().profile
    after = before.model_copy(deep=True)
    after.work_experience[0].size_corrections = []
    restore_size_corrections(before, after, [], set())
    assert [r.text for r in after.work_experience[0].size_corrections] == [SHIFT]
    after.work_experience[0].size_corrections = []
    restore_size_corrections(before, after, [], {"w1"})       # a touched entry: its write stands
    assert after.work_experience[0].size_corrections == []


def test_the_section_door_carry_alone_keeps_the_stored_records():
    from applire.services.profile.size_correction import keep_size_corrections

    held = [{"id": "w1", "size_corrections": [{"text": SHIFT, "old": 14, "new": 12}]}]
    out = keep_size_corrections("work_experience", held, [{"id": "w1", "size_corrections": FORGED},
                                                          {"id": "new", "size_corrections": FORGED}])
    assert out[0]["size_corrections"] == held[0]["size_corrections"]
    assert "size_corrections" not in out[1]


def test_a_stale_record_in_raw_json_is_never_asked():
    from applire.services.profile.size_correction import open_corrections

    raw = {"work_experience": [{"id": "w1", "responsibilities": ["anders"],
                                "size_corrections": [{"text": SHIFT, "old": 14, "new": 12}]}]}
    assert open_corrections(raw) == []
