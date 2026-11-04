# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""G-3 (Hazelnut build 6, ADR-064 / ADR-059 amended 2026-11-04) — a clicked
"…, aber zu X steht in meinem Profil noch nichts" choice is never the
candidate's no.

Founder UAT 2026-11-04: one pre-formulated R2-2 chip became one ``direct``
DeniedConcept per concept its clause named, and the letter's stated-limits rail
carried all of them as the candidate's own limits. The chip had DECLARED those
concepts in ``denied_terms`` ("named WITHOUT claiming"); the declaration never
reached the turn that answered it.

Layers pinned here, each where it can silently break:

1. ``choice_grounding.record_offered_choices`` / ``offered_choices_in_answer`` —
   the fact carried from the question to the answer (containment, not equality;
   a denial-level choice carries nothing; an edited chip matches nothing).
2. ``stance.offered_choice_gate`` — a denial named only inside the offered text
   is dropped; one the candidate typed is kept; the offered names stay vetoes.
3. One seam test per call site: the generator's two choice branches (MODE A and
   the transfer probe), ``session._send_message_turn``, the bridge's
   NEW INFORMATION, ``engine.reconcile`` (denials + the stance veto) and the
   real committer (nothing reaches ``metadata.denied_concepts``).

Synthetic data only (invented employers and names).
"""

import copy
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_backend = Path(__file__).parent.parent.parent / "backend"
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from tests.support.profile_factory import make_master_profile, retire_live_profiles  # noqa: E402

TERMS = ["Boomi", "Kafka", "AWS", "iPaaS", "ESB", "Referenzarchitekturen"]
CHIP = (
    "Bei der Veltrup Diagnostik habe ich die Anbindung der Labor-LIMS an SAP S/4HANA verantwortet "
    "und die Schnittstellenstrategie sowie die Integrations-Governance festgelegt, aber zu Boomi, "
    "Kafka, AWS, iPaaS, ESB und Referenzarchitekturen steht in meinem Profil noch nichts."
)
CHIP_EN = (
    "At Veltrup Diagnostik I owned the connection of the lab LIMS to SAP S/4HANA and set the "
    "interface strategy and the integration governance, but my profile does not yet document "
    "Boomi, Kafka, AWS, iPaaS, ESB or reference architectures."
)
OTHER = "Ich habe die Anbindung der Labor-LIMS an SAP S/4HANA über REST-Schnittstellen umgesetzt."


def _offered():
    return {
        CHIP: {"level": "partial", "names_without_claiming": list(TERMS)},
        OTHER: {"level": "direct", "names_without_claiming": []},
    }


def _new_info(answer, offered=None):
    from applire.services.choice_grounding import offered_choices_in_answer

    info = {"gap": "Integrationsplattform", "question": "Welche Erfahrung hast du?", "answer": answer}
    chosen = offered_choices_in_answer(answer, offered if offered is not None else _offered())
    if chosen:
        info["offered_choices"] = chosen
    return info


# ---------------------------------------------------------------------------
# 1. The fact: record_offered_choices / offered_choices_in_answer
# ---------------------------------------------------------------------------


def test_record_keeps_each_delivered_choice_with_its_own_declaration():
    from applire.services.choice_grounding import record_offered_choices

    state: dict = {}
    drafted = [
        {"text": CHIP, "level": "partial", "denied_terms": TERMS},
        {"text": OTHER, "level": "direct", "denied_terms": []},
        {"text": "A dropped overclaim", "level": "direct", "denied_terms": []},
    ]
    record_offered_choices(state, drafted, [CHIP, OTHER])
    assert state["offered_choice_terms"] == {
        CHIP: {"level": "partial", "names_without_claiming": TERMS},
        OTHER: {"level": "direct", "names_without_claiming": []},
    }, "only DELIVERED choices are remembered, each with the generator's own declaration"


def test_record_is_bounded_newest_last_and_a_no_op_without_a_delivery():
    from applire.services.choice_grounding import OFFERED_CHOICE_KEEP, record_offered_choices

    state: dict = {}
    record_offered_choices(state, [], None)
    record_offered_choices(state, [], [])
    assert "offered_choice_terms" not in state
    for i in range(OFFERED_CHOICE_KEEP + 3):
        record_offered_choices(state, [], [f"choice {i}"])
    record_offered_choices(state, [], ["choice 5"])  # re-offered: moves to the end
    keys = list(state["offered_choice_terms"])
    assert len(keys) == OFFERED_CHOICE_KEEP
    assert keys[-1] == "choice 5" and "choice 0" not in keys


def test_the_answer_contains_the_chip_even_when_the_candidate_extends_it():
    from applire.services.choice_grounding import offered_choices_in_answer

    extended = CHIP[:-1] + ", und Kafka kenne ich nur vom Namen."
    for answer in (CHIP, CHIP + " Mit Kafka habe ich nie gearbeitet.", extended, "  " + CHIP.upper()):
        got = offered_choices_in_answer(answer, _offered())
        assert [c["text"] for c in got] == [CHIP], answer
        assert got[0]["names_without_claiming"] == TERMS


def test_an_edited_chip_or_a_typed_answer_carries_no_fact():
    from applire.services.choice_grounding import offered_choices_in_answer

    edited = CHIP.replace("Bei der Veltrup Diagnostik", "Bei Veltrup")
    assert offered_choices_in_answer(edited, _offered()) == []
    assert offered_choices_in_answer("Mit Boomi habe ich nicht gearbeitet.", _offered()) == []
    assert offered_choices_in_answer(CHIP, {}) == []
    assert offered_choices_in_answer(CHIP, None) == []


def test_a_denial_level_choice_is_the_candidates_own_no_and_carries_nothing():
    from applire.services.choice_grounding import offered_choices_in_answer

    restated = "Mit Boomi habe ich nicht gearbeitet, wie gesagt."
    offered = {restated: {"level": "denial", "names_without_claiming": ["Boomi"]}}
    assert offered_choices_in_answer(restated, offered) == []


# ---------------------------------------------------------------------------
# 2. The gate: stance.offered_choice_gate
# ---------------------------------------------------------------------------


def test_the_founder_shape_every_clause_denial_is_dropped():
    """The as-built gpt-6-luna replay output for the chip (2/10 turns): every
    clause concept in `denials`. None of them is the candidate's own word."""
    from applire.services.profile.reconcile.stance import offered_choice_gate

    kept, dropped, unclaimed = offered_choice_gate(list(TERMS), _new_info(CHIP), "interview")
    assert kept == []
    assert dropped == TERMS
    assert unclaimed == TERMS


def test_a_typed_denial_after_the_chip_is_kept_and_only_it():
    from applire.services.profile.reconcile.stance import offered_choice_gate

    answer = CHIP + " Mit Kafka habe ich wirklich nie gearbeitet."
    kept, dropped, unclaimed = offered_choice_gate(list(TERMS), _new_info(answer), "interview")
    assert kept == ["Kafka"]
    assert "Kafka" not in dropped and len(dropped) == 5
    assert "Kafka" not in unclaimed, "a name the candidate typed is theirs to claim or deny"


def test_a_typed_denial_without_any_chip_is_the_identity():
    from applire.services.profile.reconcile.stance import offered_choice_gate

    typed = "Mit Boomi habe ich nicht gearbeitet; die Schnittstellenstrategie habe ich festgelegt."
    assert offered_choice_gate(["Boomi"], _new_info(typed), "interview") == (["Boomi"], [], [])


def test_a_denial_string_naming_a_clause_concept_is_dropped_too():
    from applire.services.profile.reconcile.stance import offered_choice_gate

    kept, dropped, _ = offered_choice_gate(
        ["hands-on Boomi", "AWS cloud"], _new_info(CHIP), "interview"
    )
    assert kept == [] and dropped == ["hands-on Boomi", "AWS cloud"]


def test_a_denial_of_the_chips_affirmed_half_is_dropped():
    """The chip states no limit by construction (R2-2): a denial of something
    only its affirmed half names has no source in the candidate's words."""
    from applire.services.profile.reconcile.stance import offered_choice_gate

    kept, dropped, _ = offered_choice_gate(["SAP S/4HANA"], _new_info(CHIP), "interview")
    assert kept == [] and dropped == ["SAP S/4HANA"]


def test_an_unrelated_denial_is_kept():
    from applire.services.profile.reconcile.stance import offered_choice_gate

    kept, dropped, _ = offered_choice_gate(["Kubernetes"], _new_info(CHIP), "interview")
    assert kept == ["Kubernetes"] and dropped == []


def test_the_english_chip_shape_is_gated_the_same_way():
    from applire.services.profile.reconcile.stance import offered_choice_gate

    en_terms = ["Boomi", "Kafka", "AWS", "iPaaS", "ESB", "reference architectures"]
    offered = {CHIP_EN: {"level": "partial", "names_without_claiming": en_terms}}
    kept, dropped, _ = offered_choice_gate(list(en_terms), _new_info(CHIP_EN, offered), "interview")
    assert kept == [] and dropped == en_terms


@pytest.mark.parametrize("source", ["agent_interview", "cv_import", "testimony", "import"])
def test_off_the_built_in_interview_door_it_is_the_identity(source):
    """Only Applire's own interview turn carries Applire-written choice text; a
    claimant-authored `offered_choices` key on another door exempts nothing."""
    from applire.services.profile.reconcile.stance import offered_choice_gate

    assert offered_choice_gate(list(TERMS), _new_info(CHIP), source) == (TERMS, [], [])


def test_malformed_offered_choices_are_ignored():
    from applire.services.profile.reconcile.stance import offered_choice_gate

    info = {"answer": CHIP, "offered_choices": ["not a dict", {"text": 3}, None]}
    assert offered_choice_gate(["Boomi"], info, "interview") == (["Boomi"], [], [])
    assert offered_choice_gate(["Boomi"], {"answer": CHIP, "offered_choices": "x"}, "interview") == (
        ["Boomi"], [], [],
    )


# ---------------------------------------------------------------------------
# 3a. Seam — engine.reconcile (denials, stance veto, prompt input)
# ---------------------------------------------------------------------------


_PROFILE = {
    "personal_info": {"name": "Lena Marquardt", "email": "lena@example.org"},
    "skills": [{"name": "Python", "category": "technical", "status": "confirmed"}],
    "work_experience": [{
        "id": "w-veltrup", "company": "Veltrup Diagnostik GmbH", "role": "Leiterin Labor-IT",
        "start_date": "2019-04", "is_current": True,
        "responsibilities": ["Leitung der Labor-IT mit einem Team von sechs Mitarbeitenden."],
    }],
    "metadata": {},
}


class _Reply:
    """A provider whose reconcile reply denies every clause concept and also
    claims one of them as a skill — the worst-case model output."""

    def __init__(self, denials=None, ops=None):
        self.payload = {"ops": ops or [], "ambiguities": [], "denials": list(denials or [])}
        self.prompts: list[str] = []

    async def aparse_json(self, prompt, **kw):
        if "profile reconciler" not in (kw.get("system") or ""):
            return {"verdict": "unconfirmed"}  # the stance adjudicator, not under test
        self.prompts.append(prompt)
        return copy.deepcopy(self.payload)

    async def acomplete(self, prompt, **kw):
        return ""


@pytest.mark.asyncio
async def test_engine_drops_the_clause_denials_and_vetoes_a_claim_on_them():
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.engine import reconcile

    skill = {"op": "upsert_skill", "name": "Kafka", "category": "technical"}
    provider = _Reply(denials=TERMS, ops=[skill])
    result = await reconcile(
        MasterProfileData.model_validate(_PROFILE), _new_info(CHIP), "interview", provider, "de"
    )
    assert result.denials == []
    assert not any(getattr(o, "name", None) == "Kafka" for o in result.ops), (
        "Applire's text names Kafka WITHOUT claiming it — it cannot ground a claim"
    )
    assert '"offered_choices"' in provider.prompts[0], "the fact reaches the model's input"
    assert '"names_without_claiming"' in provider.prompts[0]


@pytest.mark.asyncio
async def test_engine_without_the_fact_keeps_the_models_denials():
    """The control: an edited chip carries no fact and the gate has nothing to
    stand on — only rule 9 protects it (measured on real providers)."""
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.engine import reconcile

    edited = CHIP.replace("Bei der Veltrup Diagnostik", "Bei Veltrup")
    result = await reconcile(
        MasterProfileData.model_validate(_PROFILE), _new_info(edited), "interview",
        _Reply(denials=TERMS), "de",
    )
    assert result.denials == TERMS


@pytest.mark.asyncio
async def test_engine_keeps_a_typed_denial_beside_the_chip_and_demotes_on_it():
    from applire.schemas.profile import MasterProfileData
    from applire.services.profile.reconcile.engine import reconcile

    prof = copy.deepcopy(_PROFILE)
    prof["skills"].append({"name": "Kafka", "category": "technical", "status": "confirmed"})
    answer = CHIP + " Mit Kafka habe ich wirklich nie gearbeitet."
    result = await reconcile(
        MasterProfileData.model_validate(prof), _new_info(answer), "interview",
        _Reply(denials=TERMS), "de",
    )
    assert result.denials == ["Kafka"]
    assert [o.name for o in result.ops if type(o).__name__ == "DemoteSkill"] == ["Kafka"]


def test_the_offered_choices_never_become_grounding_text():
    """`stance._raw_grounding_text` reads the string fields only: the list key
    cannot turn Applire's choice text into testimony a second time."""
    from applire.services.profile.reconcile.stance import _raw_grounding_text

    info = _new_info(CHIP + " Danach mehr.")
    raw = _raw_grounding_text(info, "interview")
    assert raw.count("Referenzarchitekturen") == 1, "once, from the answer itself"


def test_reconcile_rule_9_names_the_profile_silence_clause_and_the_fact():
    """Prompt contract, not a wording test: the rule and the input key it
    explains must agree on the key's name."""
    from applire.prompts.reconcile import RECONCILE_SYSTEM_PROMPT as P
    from applire.services.profile.reconcile.stance import OFFERED_CHOICES_KEY

    assert f'"{OFFERED_CHOICES_KEY}"' in P
    assert '"names_without_claiming"' in P
    assert "steht in meinem Profil noch nichts" in P
    assert "my profile does not yet document" in P


# ---------------------------------------------------------------------------
# 3b. Seam — the bridge against the REAL committer
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    from applire.db.session import Base
    import applire.models.application  # noqa: F401
    import applire.models.color_profile  # noqa: F401
    import applire.models.company  # noqa: F401
    import applire.models.cover_letter  # noqa: F401
    import applire.models.cv  # noqa: F401
    import applire.models.flow  # noqa: F401
    import applire.models.gap  # noqa: F401
    import applire.models.job  # noqa: F401
    import applire.models.profile  # noqa: F401
    import applire.models.session  # noqa: F401
    import applire.models.user  # noqa: F401
    import applire.models.user_settings  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


async def _bridge_turn(db, answer, offered_choices):
    from applire.services.profile.reconcile.interview_bridge import reconcile_interview_turn

    await retire_live_profiles(db)
    record = make_master_profile(profile_json=copy.deepcopy(_PROFILE))
    db.add(record)
    await db.commit()
    provider = _Reply(denials=TERMS)
    out = await reconcile_interview_turn(
        db, profile_record=record, gap="Integrationsplattform", question="Welche Erfahrung hast du?",
        answer=answer, provider=provider, session_id="s-g3", lang="de",
        offered_choices=offered_choices,
    )
    await db.commit()
    return out, provider


@pytest.mark.asyncio
async def test_bridge_a_clicked_chip_records_no_denial(db):
    from applire.services.choice_grounding import offered_choices_in_answer

    out, provider = await _bridge_turn(db, CHIP, offered_choices_in_answer(CHIP, _offered()))
    assert out.denial_recorded is False
    assert out.denied_concepts == []
    assert (out.profile_dict.get("metadata") or {}).get("denied_concepts", []) == []
    assert '"offered_choices"' in provider.prompts[0]


@pytest.mark.asyncio
async def test_bridge_without_offered_choices_is_unchanged(db):
    """The control the seam test needs: the same reply WITHOUT the fact records
    the six denials (the as-built behaviour), and the NEW INFORMATION carries
    no `offered_choices` key at all."""
    out, provider = await _bridge_turn(db, CHIP, None)
    assert sorted(out.denied_concepts) == sorted(TERMS)
    recorded = [d["concept"] for d in out.profile_dict["metadata"]["denied_concepts"]]
    assert sorted(recorded) == sorted(TERMS)
    assert '"offered_choices"' not in provider.prompts[0]


# ---------------------------------------------------------------------------
# 3c. Seam — session._send_message_turn hands the fact to the bridge
# ---------------------------------------------------------------------------


def _session_state(job_id, profile_id, offered):
    return {
        "mode": "targeted", "job_id": str(job_id), "gap_analysis_id": None,
        "profile_id": str(profile_id),
        "critical_gaps": ["Integrationsplattform", "Teamführung"],
        "gap_categories": {"Integrationsplattform": "C", "Teamführung": "C"},
        "gap_clusters_by_id": {
            g: {"id": g, "label": g, "gaps": [g], "jd_skills": [], "jd_context": ""}
            for g in ("Integrationsplattform", "Teamführung")
        },
        "addressed_gaps": [], "current_gap_index": 0,
        "current_question": "Welche Erfahrung hast du?",
        "current_choices": list(offered),
        "offered_choice_terms": offered,
        "messages": [{"role": "assistant", "content": "Welche Erfahrung hast du?"}],
        "questions_asked": 1, "hard_ceiling": 12, "questions_per_gap": {},
        "skipped_gaps": [], "full_gaps": [],
    }


async def _send(db, answer, offered):
    from applire.models.job import JobAnalysis
    from applire.models.session import InterviewSession
    from applire.services.profile.reconcile.interview_bridge import InterviewTurnResult
    from applire.services.session import send_message

    await retire_live_profiles(db)
    job = JobAnalysis(
        raw_text_hash=uuid.uuid4().hex, raw_text="Head of Integration Platform.",
        role_title="Head of Integration Platform", required_skills=["Boomi"],
        nice_to_have_skills=[], keywords=[], seniority_level="Senior",
        company_culture_signals=[], language_requirement="German", jd_language="de",
    )
    profile = make_master_profile(profile_json=copy.deepcopy(_PROFILE))
    db.add_all([job, profile])
    await db.flush()
    record = InterviewSession(
        job_analysis_id=job.id, gap_analysis_id=None, profile_id=profile.id, mode="targeted",
        status="active", state=_session_state(job.id, profile.id, offered), hard_ceiling=12,
        questions_asked=1, expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db.add(record)
    await db.commit()

    bridge = AsyncMock(return_value=InterviewTurnResult(
        profile_dict=copy.deepcopy(_PROFILE), changes=[], addressed=False,
    ))
    provider = MagicMock()
    provider.acomplete = AsyncMock(return_value="unused")
    provider.aparse_json = AsyncMock(return_value={"question": "Weiter?", "choices": None})
    with (
        patch("applire.services.session.reconcile_interview_turn", new=bridge),
        patch(
            "applire.services.session.question_generator_with_profile",
            new=AsyncMock(return_value={"question": "Weiter?", "choices": None}),
        ),
        patch("applire.services.session.analyze_gaps", new=AsyncMock()),
    ):
        await send_message(record.id, answer, db, provider)
    return bridge.await_args.kwargs["offered_choices"]


@pytest.mark.asyncio
async def test_session_turn_passes_the_selected_chip_to_the_bridge(db):
    got = await _send(db, CHIP + " Mehr dazu gern im Gespräch.", _offered())
    assert got == [{"text": CHIP, "names_without_claiming": TERMS}]


@pytest.mark.asyncio
async def test_session_turn_passes_nothing_for_a_typed_answer(db):
    got = await _send(db, "Mit Boomi habe ich nicht gearbeitet.", _offered())
    assert got == []


# ---------------------------------------------------------------------------
# 3d. Seam — the generator records the fact on BOTH choice branches
# ---------------------------------------------------------------------------


_GEN_PROFILE = {
    "skills": [{"name": "SAP S/4HANA", "category": "technical"}],
    "work_experience": [{
        "company": "Veltrup Diagnostik GmbH", "role": "Leiterin Labor-IT",
        "responsibilities": ["Anbindung der Labor-LIMS an SAP S/4HANA verantwortet"],
        "technologies": ["SAP S/4HANA"],
    }],
}
_GEN_CLUSTER = {"id": "c-int", "label": "Integrationsplattform", "gaps": ["Boomi"],
                "jd_skills": ["Boomi"], "jd_context": ""}
_GEN_CHIP = ("Ich habe die Anbindung der Labor-LIMS an SAP S/4HANA verantwortet, "
             "aber zu Boomi steht in meinem Profil noch nichts.")


def _gen_state():
    return {
        "mode": "targeted", "critical_gaps": ["c-int"], "current_gap_index": 0,
        "gap_clusters_by_id": {"c-int": _GEN_CLUSTER}, "messages": [],
    }


def _gen_provider():
    provider = AsyncMock()
    provider.aparse_json.return_value = {
        "question": "Welche Integrationsplattform hast du betrieben?",
        "choices": [{"text": _GEN_CHIP, "level": "partial", "denied_terms": ["Boomi"]}],
    }
    return provider


@pytest.mark.asyncio
async def test_mode_a_records_the_delivered_choice_and_its_declaration():
    from applire.services import interview_graph as g

    state = _gen_state()
    with patch.object(g, "_review_question_language", new=AsyncMock(side_effect=lambda d, *a, **k: d)):
        out = await g.question_generator_with_profile(
            state, _GEN_PROFILE, _gen_provider(), gap_category="C", lang="de"
        )
    assert out["choices"] == [_GEN_CHIP], "the API contract stays list[str]"
    assert state["offered_choice_terms"] == {
        _GEN_CHIP: {"level": "partial", "names_without_claiming": ["Boomi"]}
    }


@pytest.mark.asyncio
async def test_the_transfer_probe_branch_records_it_too():
    from applire.services import interview_graph as g

    state = _gen_state()
    with patch.object(g, "_review_question_language", new=AsyncMock(side_effect=lambda d, *a, **k: d)):
        out = await g.question_generator_with_profile(
            state, _GEN_PROFILE, _gen_provider(), gap_category="C", lang="de",
            follow_up_hint="Boomi", denial_probe=True,
        )
    assert out["choices"] == [_GEN_CHIP]
    assert state["offered_choice_terms"][_GEN_CHIP]["names_without_claiming"] == ["Boomi"]
