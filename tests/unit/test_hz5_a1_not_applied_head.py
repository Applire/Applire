# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hazelnut build 5 delivery run — D-1 (the not_applied question reads the
newest IMPORT record, so no other act hides it) and D-2 (an item the vault
already holds under another name is silent and never adoptable)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from applire.schemas.profile import (
    EnrichmentRecord,
    FieldChange,
    ImportNotApplied,
    MasterProfileData,
    ProfileMetadata,
)
from applire.services.profile.health import IMPORT_SOURCES, assess_health

T0 = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)

#: The founder's shape, synthetic analog: the vault holds the German names, the
#: LinkedIn import carried the English ones, and no alias rule binds them.
EDU = [
    {"id": "e1", "institution": "Universität Hohenfeld", "degree": "Diplom"},
    {"id": "e2", "institution": "Berufsakademie Talwerk", "degree": "Fachinformatiker"},
]


def _item(label, entry, **kw):
    return ImportNotApplied(section="education", label=label, reason="no_op_carried_entry",
                            item_key=f"education:{label[:6]}", entry=entry, **kw)


def _import_record(items, minutes=0, source="linkedin_import"):
    return EnrichmentRecord(timestamp=T0 + timedelta(minutes=minutes), source=source, not_applied=items)


def _act(minutes, key, source="manual_edit"):
    return EnrichmentRecord(timestamp=T0 + timedelta(minutes=minutes), source=source, changes=[
        FieldChange(section="work_experience", field="end_date", action="updated",
                    old_value="a", new_value="b", rationale="r", rationale_key=key)])


def _profile(history):
    return MasterProfileData.model_validate({
        "personal_info": {"name": "Lena Brandt"},
        "education": EDU,
        "metadata": ProfileMetadata(enrichment_history=history).model_dump(mode="json"),
    })


def _na(profile):
    return [i for i in assess_health(profile).issues if i.thread == "not_applied"]


NEW = _item("Hochschule Nord / M.Sc.", {"institution": "Hochschule Nord", "degree": "M.Sc."})


# ── D-1 ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", ["conflict_resolved", "match_confirmed", "entries_merged",
                                 "entries_kept_separate", None])
def test_no_later_non_import_act_hides_the_not_applied_question(key):
    """The delivery run: a conflict answered as keep_current (3 issues → 1). Any
    act that is not an import — whatever its rationale key — leaves it standing."""
    p = _profile([_import_record([NEW]), _act(5, key)])
    (issue,) = _na(p)
    assert issue.not_applied_items[0].label == NEW.label


def test_a_later_import_still_clears_it():
    """V-6 stays: the question belongs to the newest import; one that applied
    everything means nothing is outstanding."""
    p = _profile([_import_record([NEW]), _import_record([], minutes=10, source="cv_upload")])
    assert _na(p) == []


def test_import_sources_are_the_document_doors():
    assert IMPORT_SOURCES == {"cv_upload", "cv_paste", "linkedin_import", "xing_import"}


# ── D-2 ───────────────────────────────────────────────────────────────────────


PRESENT = [
    _item("University of Hohenfeld / German Diploma",
          {"institution": "University of Hohenfeld", "degree": "German Diploma"},
          already_present_as="e1"),
    _item("Talwerk Bildung GmbH / Computer System Developer",
          {"institution": "Talwerk Bildung GmbH", "degree": "Computer System Developer"},
          already_present_as="e2"),
]


def test_items_already_in_the_vault_are_silent_and_never_adoptable():
    (issue,) = _na(_profile([_import_record(PRESENT)]))
    assert (issue.delivery_class, issue.resolver) == ("S", None)
    assert not any(i.adoptable for i in issue.not_applied_items)


def test_mixed_issue_asks_only_the_new_item():
    (issue,) = _na(_profile([_import_record([*PRESENT, NEW])]))
    assert (issue.delivery_class, issue.resolver) == ("A", "adopt_not_applied")
    assert [i.label for i in issue.not_applied_items] == [NEW.label]
    assert issue.not_applied_count == 1


def test_unadoptable_new_item_keeps_complete_section():
    scalar = ImportNotApplied(section="education", label="x", reason="no_op_carried_entry")
    (issue,) = _na(_profile([_import_record([scalar])]))
    assert (issue.delivery_class, issue.resolver) == ("A", "complete_section")


def test_adopting_an_already_present_item_is_refused():
    from applire.services.profile.reconcile.apply import (
        NotAppliedNotAdoptableError,
        find_adoptable_item,
    )

    p = _profile([_import_record(PRESENT)])
    rec = p.metadata.enrichment_history[0]
    with pytest.raises(NotAppliedNotAdoptableError) as exc:
        find_adoptable_item(p, rec.id, PRESENT[0].item_key)
    assert exc.value.code == "not_adoptable"


class _Judge:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, 0

    async def aparse_json(self, prompt, system=None, **kw):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return self.answer


@pytest.mark.asyncio
async def test_mark_already_present_keeps_only_offered_ids_of_the_items_section():
    from applire.services.profile.not_applied_presence import mark_already_present

    items = [i.model_copy(update={"already_present_as": None}) for i in PRESENT] + [NEW.model_copy()]
    merged = _profile([])
    judge = _Judge({"matches": [
        {"item": "n0", "existing": "e1"},
        {"item": "n1", "existing": "w-forged"},   # not an offered education id
        {"item": "n2", "existing": None},
    ]})
    assert await mark_already_present(items, merged, judge) == 1
    assert [i.already_present_as for i in items] == ["e1", None, None]
    assert judge.calls == 1


@pytest.mark.asyncio
async def test_a_failed_judge_hides_nothing():
    from applire.services.profile.not_applied_presence import mark_already_present

    items = [NEW.model_copy()]
    assert await mark_already_present(items, _profile([]), _Judge(fail=True)) == 0
    assert items[0].already_present_as is None
