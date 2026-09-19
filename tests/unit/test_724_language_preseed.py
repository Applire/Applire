# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#724 — a German CV must not ship English vault bullets.

Founder's edge UAT 2026-09-18 (`triage:document-harm`): a delivered German CV
carried 4 of its 15 work bullets in English and an English `Branche:` line on
every role, and both blind panel reviewers read the document as unedited.
Nothing was wrong with any single pass. `_review_cv_language` runs on the PROSE
shape and is the last language authority; `_restore_ledger_bullets`,
`_apply_role_facts` and `_nest_projects` all run inside `_compose_document`, i.e.
strictly after it, and all three carry vault text verbatim by their own rules. On
every EN-vault -> DE-document generation the delivered document was bilingual by
construction.

ADR-072/ADR-062/ADR-067 amended 2026-09-18 (founder ruling W-1): "verbatim" is
verbatim INTO the draft. The restore's SELECTION runs before the language pass,
over the same provisional composition the tail reads, and the chosen text rides
into the prose draft so that pass translates it like any other bullet.

What these tests pin, in the order the properties matter:

* the selection is the SAME selection (ADR-066 — one instrument, so what gets
  restored cannot drift from what used to get restored);
* a same-language run produces an EMPTY plan and a byte-identical document — the
  change is inert everywhere except where the defect lived;
* the tail can never re-add a vault bullet the preseed already placed, which is
  the one way this fix could be worse than the defect (translation AND original);
* a preseeded restoration keeps #315's load-bearing placement and the ceiling;
* the settle guard turns the refiner's *instruction* not to drop a bullet into a
  verified fact, and falls back to the pre-#724 behaviour rather than losing a
  claimable concept (#229 — an instruction is not a guarantee);
* the ORDER itself: a faithful translator double shows the restored bullet
  delivered translated, and the swapped-back arm shows it delivered untranslated.

Pure functions, faithful doubles, no LLM, no DB. Every German and English string
here is synthetic and was checked against `detect_language` before being used.
"""
from __future__ import annotations

import sys
from pathlib import Path

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

SENIOR = "11111111-1111-1111-1111-111111111111"
JUNIOR = "22222222-2222-2222-2222-222222222222"

# --- vault text (English) ----------------------------------------------------
EN_ISO = "Ran the ISO 9001 audit programme for the whole plant."
EN_MES = "Rolled out MES across fourteen machines and tracked OEE for the shift teams."
EN_SMED = "Reduced changeover time with SMED on the packaging line."
EN_INDUSTRY = "Manufacturing of technical plastics and packaging"

# --- the same facts in German (the same-language control) --------------------
DE_ISO = "Leitete das ISO-9001-Auditprogramm fuer das gesamte Werk."
DE_MES = "Fuehrte MES auf vierzehn Maschinen ein und verfolgte die OEE der Schichtteams."
DE_SMED = "Reduzierte die Ruestzeit mit SMED an der Verpackungslinie."
DE_INDUSTRY = "Herstellung technischer Kunststoffe und Verpackungen"

# --- what the writer produced (German prose, ledger concepts missing) --------
DRAFT_GENERIC_1 = "Verantwortete die Fertigung an zwei Standorten."
DRAFT_GENERIC_2 = "Fuehrte die Schichtplanung fuer achtzig Mitarbeitende."


def _ledger_entry(concept: str, *, load_bearing: bool = False) -> dict:
    return {
        "concept": concept,
        "surface_forms": [concept],
        "claimable": True,
        "status": "direct",
        "sources": ["required"],
        "fit_weight": 1.0,
        "evidence": f"vault evidence for {concept}",
        **({"load_bearing": True} if load_bearing else {}),
    }


LEDGER = [_ledger_entry("ISO 9001"), _ledger_entry("MES"), _ledger_entry("SMED")]
FORMS = ("ISO 9001", "MES", "SMED")


def _profile(*, english: bool = True, industry: bool = True) -> dict:
    iso, mes, smed = (EN_ISO, EN_MES, EN_SMED) if english else (DE_ISO, DE_MES, DE_SMED)
    ind = (EN_INDUSTRY if english else DE_INDUSTRY) if industry else None
    return {
        "contact": {"full_name": "Testperson", "email": "kontakt@applire.de"},
        "work_experience": [
            {
                "id": SENIOR,
                "company": "Nordwerk Kunststoff GmbH",
                "role": "Werkleiter",
                "start_date": "2020-01",
                "end_date": None,
                "industry_context": ind,
                "responsibilities": [iso, mes],
                "achievements": [smed],
            },
            {
                "id": JUNIOR,
                "company": "Suedpack Systeme AG",
                "role": "Schichtleiter",
                "start_date": "2016-01",
                "end_date": "2019-12",
                "responsibilities": [],
                "achievements": [],
            },
        ],
        "projects": [],
        "education": [],
        "languages": [],
        "skills": [],
        "certifications": [],
    }


def _prose(bullets=None, *, projects=None, top_projects=None) -> dict:
    return {
        "summary": "Werkleiter in der Kunststoffverarbeitung.",
        "skills": [],
        "work": [
            {
                "id": SENIOR,
                "bullets": list(bullets if bullets is not None else [DRAFT_GENERIC_1, DRAFT_GENERIC_2]),
                "projects": list(projects or []),
            },
            {"id": JUNIOR, "bullets": [], "projects": []},
        ],
        "projects": list(top_projects or []),
    }


def _budget(max_bullets: int = 5):
    from applire.services.cv_budget import BudgetResult, BulletTier, RoleBudget

    tiers = {
        "top": BulletTier("top", max_bullets, max(0, max_bullets - 1)),
        "mid": BulletTier("mid", 3, 2),
        "bottom": BulletTier("bottom", 1, 0),
    }
    return BudgetResult(
        roles={
            SENIOR: RoleBudget(work_entry_id=SENIOR, tier="top", max_bullets=max_bullets),
            JUNIOR: RoleBudget(work_entry_id=JUNIOR, tier="mid", max_bullets=3),
        },
        tiers=tiers,
        target_pages=2,
        region="DACH",
        claimable_forms=FORMS,
    )


def _compose(prose, profile, budget, *, preseed=None):
    from applire.services.cv import _compose_document

    return _compose_document(
        prose,
        profile,
        raw_profile_json=profile,
        keyword_ledger=LEDGER,
        budget=budget,
        job_dict={},
        language="de",
        preseed=preseed,
    )


def _plan(prose, profile, budget, language="de", job_dict=None):
    from applire.services.cv import _plan_language_preseed

    return _plan_language_preseed(
        prose,
        profile,
        keyword_ledger=LEDGER,
        budget=budget,
        job_dict=job_dict or {},
        document_language=language,
    )


def _senior_bullets(doc) -> list[str]:
    return [w for w in doc.work_history if w.id == SENIOR][0].bullets


def _fake_translate(prose: dict, mapping: dict[str, str]) -> dict:
    """A faithful double of the `cv_language` refiner: translates in place,
    keeps every list's length and every id, touches nothing else.

    Faithful, not convenient — the real refiner's contract is exactly this, and
    the settle guard exists because a real model may break it. The tests that
    care about breakage use a DIFFERENT, deliberately unfaithful double.
    """
    import copy

    out = copy.deepcopy(prose)
    for entry in out.get("work") or []:
        entry["bullets"] = [mapping.get(b, b) for b in entry.get("bullets") or []]
        if isinstance(entry.get("industry_context"), str):
            entry["industry_context"] = mapping.get(
                entry["industry_context"], entry["industry_context"]
            )
        for proj in entry.get("projects") or []:
            proj["bullets"] = [mapping.get(b, b) for b in proj.get("bullets") or []]
    for proj in out.get("projects") or []:
        proj["bullets"] = [mapping.get(b, b) for b in proj.get("bullets") or []]
    return out


TRANSLATIONS = {EN_ISO: DE_ISO, EN_MES: DE_MES, EN_SMED: DE_SMED, EN_INDUSTRY: DE_INDUSTRY}


# ─────────────────────────────────────────────────────────────────────────────
# 1. the selection is the same selection
# ─────────────────────────────────────────────────────────────────────────────

def test_the_preseed_selects_exactly_what_the_tail_would_have_restored():
    """ADR-066: one instrument. Moving WHEN the restore is chosen may not change
    WHAT is chosen — otherwise this fix would quietly re-scope #234."""
    profile, budget = _profile(), _budget()
    prose = _prose()

    _, plan = _plan(prose, profile, budget)
    preseeded = sorted(p.vault_text for p in plan.by_entry[SENIOR])

    tail_only = _compose(prose, profile, budget, preseed=None)
    restored_by_tail = sorted(
        b for b in _senior_bullets(tail_only) if b in {EN_ISO, EN_MES, EN_SMED}
    )

    assert preseeded == restored_by_tail
    assert preseeded == sorted([EN_ISO, EN_MES, EN_SMED])


# ─────────────────────────────────────────────────────────────────────────────
# 2. inert where the defect never lived
# ─────────────────────────────────────────────────────────────────────────────

def test_a_german_vault_feeding_a_german_document_produces_an_empty_plan():
    """The gate is per item, against the document's own language. A same-language
    run must be byte-identical to yesterday's pipeline."""
    profile, budget = _profile(english=False), _budget()
    prose = _prose()

    new_prose, plan = _plan(prose, profile, budget)

    assert plan.is_empty()
    assert new_prose == prose
    assert _compose(new_prose, profile, budget, preseed=plan).model_dump() == _compose(
        prose, profile, budget, preseed=None
    ).model_dump()


def test_an_english_document_from_an_english_vault_also_produces_an_empty_plan():
    """The check is AGREEMENT with the document's language, not a preference for
    German."""
    profile, budget = _profile(), _budget()

    _, plan = _plan(_prose(), profile, budget, language="en")

    assert plan.is_empty()


# ─────────────────────────────────────────────────────────────────────────────
# 3. the injection itself
# ─────────────────────────────────────────────────────────────────────────────

def test_an_english_vault_bullet_is_injected_into_the_prose_draft():
    profile, budget = _profile(), _budget()
    prose = _prose()

    new_prose, plan = _plan(prose, profile, budget)
    senior = [e for e in new_prose["work"] if e["id"] == SENIOR][0]

    assert senior["bullets"][:2] == [DRAFT_GENERIC_1, DRAFT_GENERIC_2]
    assert set(senior["bullets"][2:]) == {EN_ISO, EN_MES, EN_SMED}
    assert plan._pre_lengths[SENIOR] == 2
    assert prose["work"][0]["bullets"] == [DRAFT_GENERIC_1, DRAFT_GENERIC_2]  # unmutated


def test_the_english_industry_line_is_injected_and_the_german_one_is_not():
    en_profile, de_profile, budget = _profile(), _profile(english=False), _budget()

    en_prose, en_plan = _plan(_prose(), en_profile, budget)
    _, de_plan = _plan(_prose(), de_profile, budget)

    assert en_plan.industry_context[SENIOR] == EN_INDUSTRY
    assert [e for e in en_prose["work"] if e["id"] == SENIOR][0]["industry_context"] == EN_INDUSTRY
    assert de_plan.industry_context == {}


# ─────────────────────────────────────────────────────────────────────────────
# 4. the guard that keeps the fix from being worse than the defect
# ─────────────────────────────────────────────────────────────────────────────

def test_the_tail_never_re_adds_a_vault_bullet_the_preseed_placed():
    """The failure this exclusion exists for: a claimable surface form that does
    NOT survive translation leaves the concept "missing" again, and an unguarded
    tail would restore the English original next to its own German translation.

    The double here deliberately drops the surface form, which is exactly the
    shape the language pass's own coverage wrapper is meant to prevent and cannot
    be relied on to.
    """
    profile, budget = _profile(), _budget(max_bullets=8)
    prose = _prose()

    new_prose, plan = _plan(prose, profile, budget)
    lossy = {
        EN_ISO: "Leitete das Auditprogramm fuer das gesamte Werk.",  # 'ISO 9001' gone
        EN_MES: DE_MES,
        EN_SMED: DE_SMED,
    }
    settled = _fake_translate(new_prose, lossy)
    from applire.services.cv import _settle_language_preseed

    _settle_language_preseed(settled, plan)

    delivered = _senior_bullets(_compose(settled, profile, budget, preseed=plan))

    assert EN_ISO not in delivered
    assert "Leitete das Auditprogramm fuer das gesamte Werk." in delivered
    assert len([b for b in delivered if "Auditprogramm" in b]) == 1


def test_without_the_exclusion_the_english_original_comes_back(monkeypatch):
    """The baseline this guard rescues — asserted, not assumed. With the plan's
    exclusion neutered, the same lossy translation delivers BOTH copies."""
    from applire.services.cv import _settle_language_preseed
    from applire.services.ledger_restore import PreseedPlan

    profile, budget = _profile(), _budget(max_bullets=8)
    new_prose, plan = _plan(_prose(), profile, budget)
    settled = _fake_translate(
        new_prose,
        {
            EN_ISO: "Leitete das Auditprogramm fuer das gesamte Werk.",
            EN_MES: DE_MES,
            EN_SMED: DE_SMED,
        },
    )
    _settle_language_preseed(settled, plan)
    monkeypatch.setattr(PreseedPlan, "excluded_vault_norms", lambda self: {})

    delivered = _senior_bullets(_compose(settled, profile, budget, preseed=plan))

    assert EN_ISO in delivered
    assert "Leitete das Auditprogramm fuer das gesamte Werk." in delivered


# ─────────────────────────────────────────────────────────────────────────────
# 5. the ordering and the ceiling this ADR already specifies
# ─────────────────────────────────────────────────────────────────────────────

def test_a_preseeded_restoration_is_ordered_ahead_of_a_no_hit_draft_bullet():
    """#234's hit-first order must survive the move. Without the plan being read
    in `_restore_ledger_bullets`, the entry takes the cap-only branch and keeps
    the writer's generic bullets in front."""
    profile, budget = _profile(), _budget(max_bullets=8)
    new_prose, plan = _plan(_prose(), profile, budget)
    settled = _fake_translate(new_prose, TRANSLATIONS)
    from applire.services.cv import _settle_language_preseed

    _settle_language_preseed(settled, plan)

    delivered = _senior_bullets(_compose(settled, profile, budget, preseed=plan))

    restored_positions = [delivered.index(t) for t in (DE_ISO, DE_MES, DE_SMED)]
    generic_positions = [delivered.index(DRAFT_GENERIC_1), delivered.index(DRAFT_GENERIC_2)]
    assert max(restored_positions) < min(generic_positions)


def test_a_tight_ceiling_still_binds_and_keeps_the_restorations():
    """The ceiling is unconditional (ADR-051 §3) and coverage outranks filler."""
    profile, budget = _profile(), _budget(max_bullets=3)
    new_prose, plan = _plan(_prose(), profile, budget)
    settled = _fake_translate(new_prose, TRANSLATIONS)
    from applire.services.cv import _settle_language_preseed

    _settle_language_preseed(settled, plan)

    delivered = _senior_bullets(_compose(settled, profile, budget, preseed=plan))

    assert len(delivered) == 3
    assert set(delivered) == {DE_ISO, DE_MES, DE_SMED}


# ─────────────────────────────────────────────────────────────────────────────
# 6. the settle guard — an instruction is not a guarantee (#229)
# ─────────────────────────────────────────────────────────────────────────────

def test_the_settle_guard_records_the_translated_text_when_the_shape_is_kept():
    profile, budget = _profile(), _budget()
    new_prose, plan = _plan(_prose(), profile, budget)
    from applire.services.cv import _settle_language_preseed

    _settle_language_preseed(_fake_translate(new_prose, TRANSLATIONS), plan)

    assert sorted(p.translated_text for p in plan.by_entry[SENIOR]) == sorted(
        [DE_ISO, DE_MES, DE_SMED]
    )


def test_the_settle_guard_re_appends_a_bullet_the_language_pass_dropped(caplog):
    """The refiner is told never to drop an entry. When it does anyway, the vault
    text goes back verbatim — the pre-#724 behaviour — and a claimable concept is
    never the price of this fix."""
    import logging

    from applire.services.cv import _settle_language_preseed

    profile, budget = _profile(), _budget()
    new_prose, plan = _plan(_prose(), profile, budget)
    settled = _fake_translate(new_prose, TRANSLATIONS)
    senior = [e for e in settled["work"] if e["id"] == SENIOR][0]
    senior["bullets"] = [b for b in senior["bullets"] if b != DE_MES]  # the model lost one

    with caplog.at_level(logging.WARNING, logger="applire.services.cv"):
        _settle_language_preseed(settled, plan)

    assert EN_MES in [e for e in settled["work"] if e["id"] == SENIOR][0]["bullets"]
    assert any(
        "LANGUAGE_PRESEED_SETTLE_FALLBACK" in r.getMessage() for r in caplog.records
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. the role-facts furniture line stays vault-or-plan, never draft
# ─────────────────────────────────────────────────────────────────────────────

def test_the_industry_line_is_delivered_in_the_document_language():
    profile, budget = _profile(), _budget()
    new_prose, plan = _plan(_prose(), profile, budget)
    settled = _fake_translate(new_prose, TRANSLATIONS)
    from applire.services.cv import _settle_language_preseed

    _settle_language_preseed(settled, plan)

    doc = _compose(settled, profile, budget, preseed=plan)
    senior = [w for w in doc.work_history if w.id == SENIOR][0]

    assert senior.industry_context == DE_INDUSTRY


def test_an_industry_line_the_draft_invented_is_never_laundered_onto_the_page():
    """ADR-062 clause 1's single-writer guarantee is structural, not an
    instruction: only a value this plan carried from the vault can come back."""
    profile, budget = _profile(english=False), _budget()
    prose = _prose()
    prose["work"][0]["industry_context"] = "Space logistics for the lunar market"

    new_prose, plan = _plan(prose, profile, budget)
    doc = _compose(new_prose, profile, budget, preseed=plan)
    senior = [w for w in doc.work_history if w.id == SENIOR][0]

    assert senior.industry_context == DE_INDUSTRY


def test_a_vault_without_an_industry_line_still_renders_none():
    profile, budget = _profile(industry=False), _budget()
    new_prose, plan = _plan(_prose(), profile, budget)
    doc = _compose(new_prose, profile, budget, preseed=plan)

    assert [w for w in doc.work_history if w.id == SENIOR][0].industry_context is None


# ─────────────────────────────────────────────────────────────────────────────
# 8. the third bilingual class — vault project bullets (ruling W-1b)
# ─────────────────────────────────────────────────────────────────────────────

def test_a_foreign_vault_project_is_preseeded_once_and_not_appended_twice():
    profile, budget = _profile(), _budget(max_bullets=8)
    profile["projects"] = [
        {
            "name": "Linie 4 Modernisierung",
            "associated_experience": SENIOR,
            "responsibilities": ["Rebuilt the line control system with the shift teams."],
            "achievements": [],
        }
    ]
    new_prose, plan = _plan(_prose(), profile, budget)
    senior_prose = [e for e in new_prose["work"] if e["id"] == SENIOR][0]

    assert [p["name"] for p in senior_prose["projects"]] == ["Linie 4 Modernisierung"]

    settled = _fake_translate(
        new_prose,
        {
            **TRANSLATIONS,
            "Rebuilt the line control system with the shift teams.":
                "Erneuerte die Liniensteuerung gemeinsam mit den Schichtteams.",
        },
    )
    from applire.services.cv import _settle_language_preseed

    _settle_language_preseed(settled, plan)
    doc = _compose(settled, profile, budget, preseed=plan)
    senior = [w for w in doc.work_history if w.id == SENIOR][0]

    assert len(senior.projects) == 1
    assert senior.projects[0].bullets == [
        "Erneuerte die Liniensteuerung gemeinsam mit den Schichtteams."
    ]


def test_a_german_vault_project_is_left_to_the_deterministic_join():
    profile, budget = _profile(english=False), _budget(max_bullets=8)
    profile["projects"] = [
        {
            "name": "Linie 4 Modernisierung",
            "associated_experience": SENIOR,
            "responsibilities": ["Erneuerte die Liniensteuerung mit den Schichtteams."],
            "achievements": [],
        }
    ]
    new_prose, plan = _plan(_prose(), profile, budget)
    senior_prose = [e for e in new_prose["work"] if e["id"] == SENIOR][0]

    assert senior_prose["projects"] == []
    doc = _compose(new_prose, profile, budget, preseed=plan)
    assert len([w for w in doc.work_history if w.id == SENIOR][0].projects) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 9. THE seam: restore-before-language, and the swapped-back baseline
# ─────────────────────────────────────────────────────────────────────────────

def test_the_restored_bullet_is_delivered_translated_because_it_ran_before_the_pass():
    """The property the whole change exists for, on the DELIVERED document."""
    from applire.services.cv import _settle_language_preseed
    from applire.services.ats_audit import foreign_language_items

    profile, budget = _profile(), _budget(max_bullets=8)
    new_prose, plan = _plan(_prose(), profile, budget)
    settled = _fake_translate(new_prose, TRANSLATIONS)
    _settle_language_preseed(settled, plan)

    doc = _compose(settled, profile, budget, preseed=plan)

    assert foreign_language_items(doc.model_dump(), "de") == []


def test_the_swapped_back_order_delivers_the_untranslated_vault_bullet():
    """The baseline, asserted rather than assumed: run the language pass FIRST and
    the restore after it — the pre-#724 order — and the same twin delivers English
    bullets and an English industry line."""
    from applire.services.ats_audit import foreign_language_items

    profile, budget = _profile(), _budget(max_bullets=8)
    settled = _fake_translate(_prose(), TRANSLATIONS)  # nothing to translate yet

    doc = _compose(settled, profile, budget, preseed=None)

    foreign = foreign_language_items(doc.model_dump(), "de")
    assert sorted(text for _where, text in foreign) == sorted(
        [EN_INDUSTRY, EN_ISO, EN_MES, EN_SMED]
    )
