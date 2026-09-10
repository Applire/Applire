# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later

"""#669 — pins ADR-063's 2026-09-05 amendment (stable option keys, a
language-independent persisted confirmation, door parity) as BUILT.

Ground commits: ``d1fc784e`` (the build) — key new module
``reconcile/confirmations.py``.

Six properties, one section each:

1. The MEASUREMENT CONTRACT the amendment fixes: every deterministic
   ``RequestConfirmation`` construction in ``backend/applire/`` lives in
   ``reconcile/confirmations.py`` (four builder functions) or is validated
   (not built) by ``reconcile/engine.py``'s ``_parse_ambiguities``. Found by
   an AST scan of the whole tree, so a tenth inline builder added anywhere
   else fails this test on sight.
2. One seam test per ask family (4): a German and an English rendering of the
   SAME confirmation carry different text but resolve to the SAME
   ``option_key`` via ``resolve_option_key``.
3. The defect itself, closed: ``_skill_confirmation_decision`` on the GERMAN
   "keep" text resolves correctly WITH the key, and reproduces the pre-#669
   mis-resolution WITHOUT it (``option_key=None``) — pinning both is what
   shows the key is what fixed it.
4. Back-compat: a ``PendingConfirmation`` shaped like a pre-#669 record
   (no i18n payloads, empty ``option_keys``) validates and renders unchanged.
5. The adapter-only strip fires at BOTH raw paths — ``engine._parse_ops``
   (the ``"ops"`` array) and ``engine._parse_ambiguities`` (the
   ``"ambiguities"`` array, whose items carry no ``"op"`` key) — stripping
   the three fields but KEEPING the op.
6. ``stance.record_denials``' new-denial receipt carries
   ``rationale_key="denial_noted"``.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from applire.schemas.profile import PendingConfirmation, ProfileMetadata
from applire.services.profile.reconcile.confirmations import (
    attribution_confirmation,
    entity_dupe_confirmation,
    skill_containment_confirmation,
    skill_overlap_confirmation,
)
from applire.services.profile.reconcile.engine import _parse_ambiguities, _parse_ops
from applire.services.profile.reconcile.stance import record_denials
from applire.services.session import (
    _confirmation_state,
    _skill_confirmation_decision,
    resolve_option_key,
)

# backend/tests/unit/test_669_confirmation_option_keys.py -> backend/applire
APPLIRE_ROOT = Path(__file__).resolve().parents[2] / "applire"
CONFIRMATIONS_MODULE = Path("services/profile/reconcile/confirmations.py")
ENGINE_MODULE = Path("services/profile/reconcile/engine.py")


# ── 1. the measurement contract, re-derived from the tree ─────────────────────


def _find_request_confirmation_calls() -> list[tuple[Path, int, str | None]]:
    """Every AST ``Call`` in ``backend/applire/**/*.py`` whose callee is
    ``RequestConfirmation`` itself, either a direct construction
    (``RequestConfirmation(...)``) or an attribute call on it
    (``RequestConfirmation.model_validate(...)``).

    Returns ``(path relative to backend/applire, line, enclosing function name)``.
    """

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.func_stack: list[str] = []
            self.hits: list[tuple[int, str | None]] = []

        def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            self.func_stack.append(node.name)
            self.generic_visit(node)
            self.func_stack.pop()

        visit_FunctionDef = _visit_func
        visit_AsyncFunctionDef = _visit_func

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            is_hit = isinstance(func, ast.Name) and func.id == "RequestConfirmation"
            if not is_hit and isinstance(func, ast.Attribute):
                is_hit = (
                    isinstance(func.value, ast.Name)
                    and func.value.id == "RequestConfirmation"
                )
            if is_hit:
                self.hits.append((node.lineno, self.func_stack[-1] if self.func_stack else None))
            self.generic_visit(node)

    all_hits: list[tuple[Path, int, str | None]] = []
    for path in sorted(APPLIRE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "RequestConfirmation" not in source:
            continue
        tree = ast.parse(source, filename=str(path))
        visitor = _Visitor()
        visitor.visit(tree)
        rel = path.relative_to(APPLIRE_ROOT)
        all_hits.extend((rel, lineno, fn) for lineno, fn in visitor.hits)
    return all_hits


def test_only_confirmations_module_and_parse_ambiguities_construct_request_confirmation():
    """No module outside ``reconcile/confirmations.py`` constructs a
    ``RequestConfirmation`` — the nine builders (apply.py x8, attribution.py x1)
    all route through its four builder functions instead of building the op
    inline. ``engine._parse_ambiguities`` is allowed: it VALIDATES raw model
    output, it does not build a deterministic confirmation.

    This is the regression gate: a tenth inline builder appearing anywhere else
    in the tree is a new hit whose path fails the allow-list below.
    """
    hits = _find_request_confirmation_calls()
    assert len(hits) >= 2, (
        "expected at least the confirmations.py builder call and engine.py's "
        f"_parse_ambiguities call; the AST scan found: {hits!r}"
    )

    def _allowed(path: Path, func: str | None) -> bool:
        if path == CONFIRMATIONS_MODULE:
            return True
        return path == ENGINE_MODULE and func == "_parse_ambiguities"

    violations = [hit for hit in hits if not _allowed(hit[0], hit[2])]
    assert violations == [], (
        "a RequestConfirmation is constructed outside reconcile/confirmations.py "
        "(and outside engine.py's _parse_ambiguities, which only validates raw "
        f"model output, never builds one): {violations!r}"
    )


def test_confirmations_module_exposes_exactly_four_builder_functions():
    """Four ask families, four public builders — the count the commit message
    (and this module's own docstring) claims."""
    source = (APPLIRE_ROOT / CONFIRMATIONS_MODULE).read_text(encoding="utf-8")
    tree = ast.parse(source)
    builder_names = sorted(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    )
    assert builder_names == [
        "attribution_confirmation",
        "entity_dupe_confirmation",
        "skill_containment_confirmation",
        "skill_overlap_confirmation",
    ]


def test_each_builder_returns_positionally_aligned_option_lists():
    """Every builder's op carries ``option_keys``, ``options`` and
    ``options_i18n`` as three lists of the SAME length — the positional
    alignment ``resolve_option_key`` and ``rendered()`` both depend on."""
    ops = [
        entity_dupe_confirmation(
            section="work_experience",
            incoming_label="Senior Developer",
            existing_labels=["Developer @ Acme"],
            context={},
        ),
        skill_overlap_confirmation(
            incoming_skill="Python",
            overlapping=["Python (Basic)", "Python 3"],
            context={},
        ),
        skill_containment_confirmation(
            incoming_skill="SAP PP", related=["SAP"], context={},
        ),
        attribution_confirmation(
            sample="Led a team of five engineers.",
            anchor_text="Acme GmbH",
            target_display="Beta Inc",
            context={},
        ),
    ]
    for op in ops:
        assert op.options_i18n is not None
        assert len(op.option_keys) == len(op.options) == len(op.options_i18n) > 0


# ── 2. one seam test per ask family ────────────────────────────────────────────


def _assert_de_and_en_resolve_to_same_keys(op, expected_keys: list[str]) -> None:
    state = _confirmation_state(op)
    assert state["option_keys"] == expected_keys

    de_question, de_options = op.rendered("de")
    en_question, en_options = op.rendered("en")
    assert de_question != en_question
    assert len(de_options) == len(en_options) == len(expected_keys)

    for idx, key in enumerate(expected_keys):
        assert de_options[idx] != en_options[idx], (
            f"option {idx} ({key}) renders identically in de/en — the seam this "
            "test exists to exercise is not actually being exercised"
        )
        de_key = resolve_option_key(state, de_options[idx])
        en_key = resolve_option_key(state, en_options[idx])
        assert de_key == key, f"German rendering {de_options[idx]!r} resolved to {de_key!r}, expected {key!r}"
        assert en_key == key, f"English rendering {en_options[idx]!r} resolved to {en_key!r}, expected {key!r}"


def test_entity_dupe_family_de_and_en_resolve_to_same_key():
    # Two different sections so the German gendering ("Dieselbe Position" vs.
    # "Dieselbe Ausbildung") is actually exercised, not just one noun's inflection.
    for section, existing in (
        ("work_experience", ["Developer @ Acme"]),
        ("education", ["B.Sc. Informatik @ TU Berlin"]),
    ):
        op = entity_dupe_confirmation(
            section=section,
            incoming_label="Senior Developer",
            existing_labels=existing,
            context={},
        )
        _assert_de_and_en_resolve_to_same_keys(op, ["merge", "distinct"])


def test_skill_overlap_family_de_and_en_resolve_to_same_key():
    op = skill_overlap_confirmation(
        incoming_skill="Python",
        overlapping=["Python (Basic)", "Python 3"],
        context={},
    )
    _assert_de_and_en_resolve_to_same_keys(op, ["merge", "keep"])


def test_skill_containment_family_de_and_en_resolve_to_same_key():
    op = skill_containment_confirmation(
        incoming_skill="SAP PP", related=["SAP"], context={},
    )
    _assert_de_and_en_resolve_to_same_keys(op, ["distinct", "merge"])


def test_attribution_family_de_and_en_resolve_to_same_key():
    op = attribution_confirmation(
        sample="Led a team of five engineers.",
        anchor_text="Acme GmbH",
        target_display="Beta Inc",
        context={},
    )
    _assert_de_and_en_resolve_to_same_keys(op, ["move", "keep_here", "discard"])


# ── 3. the defect itself, now closed ───────────────────────────────────────────


def test_skill_confirmation_decision_resolves_the_german_keep_text_via_the_key():
    """The exact defect ADR-063's amendment fixes: the German rendering of
    'Keep the existing skills' contains neither 'keep' nor 'existing', so the
    pre-#669 English substring matcher mis-resolved it to 'distinct' — the
    vault GAINING a skill the candidate asked it to discard.

    With the resolved ``option_key``, the decision is correct. With
    ``option_key=None`` (the back-compat path — a record persisted before
    #669, or a model-emitted confirmation), the SAME German text still
    mis-resolves to 'distinct'. Pinning both is what shows the key, not
    something else, is what fixed it.
    """
    op = skill_overlap_confirmation(
        incoming_skill="Python",
        overlapping=["Python (Basic)", "Python 3"],
        context={},
    )
    _, de_options = op.rendered("de")
    keep_idx = op.option_keys.index("keep")
    keep_text_de = de_options[keep_idx]
    assert keep_text_de == "Die vorhandenen Fähigkeiten behalten"
    assert "keep" not in keep_text_de.lower()
    assert "existing" not in keep_text_de.lower()

    resolved_key = resolve_option_key(_confirmation_state(op), keep_text_de)
    assert resolved_key == "keep"
    assert _skill_confirmation_decision(keep_text_de, resolved_key) == "keep"

    # Back-compat / pre-#669 path: no key survives (persisted-before-#669 record,
    # or a model-emitted confirmation) — the English substring matcher is all
    # that is left, and it still gets this one wrong. That IS the defect.
    assert _skill_confirmation_decision(keep_text_de, None) == "distinct"


# ── 4. back-compat: a pre-#669 PendingConfirmation ─────────────────────────────


def test_pre_669_pending_confirmation_validates_and_renders_unchanged():
    legacy = PendingConfirmation(
        question="Is 'Projektleiter' the same role as 'Project Lead'?",
        options=["Same role — merge them", "Different — keep both"],
        context={"section": "work_experience"},
    )
    assert legacy.question_i18n is None
    assert legacy.options_i18n is None
    assert legacy.option_keys == []

    question, options = legacy.rendered("de")
    assert question == legacy.question
    assert options == legacy.options


# ── 5. the adapter-only strip, at BOTH raw paths ───────────────────────────────


_RAW_MODEL_CONFIRMATION = {
    "question": "Same role?",
    "options": ["Merge", "Keep separate"],
    "context": {"section": "work_experience"},
    # Everything below is adapter-only — a model must never be able to supply it.
    "option_keys": ["merge"],
    "question_i18n": {"de": "Gleiche Rolle?", "en": "Same role?"},
    "options_i18n": [
        {"de": "Zusammenführen", "en": "Merge"},
        {"de": "Getrennt behalten", "en": "Keep separate"},
    ],
}


def test_parse_ops_strips_adapter_only_fields_from_a_raw_confirmation_but_keeps_it():
    raw = [dict(_RAW_MODEL_CONFIRMATION, op="request_confirmation")]
    ops = _parse_ops(raw)
    assert len(ops) == 1, "the op must be STRIPPED, not rejected"
    op = ops[0]
    assert op.option_keys == []
    assert op.question_i18n is None
    assert op.options_i18n is None
    # The plain fields the model IS entitled to supply survive untouched.
    assert op.question == "Same role?"
    assert op.options == ["Merge", "Keep separate"]


def test_parse_ambiguities_strips_adapter_only_fields_from_a_raw_ambiguity_but_keeps_it():
    # The ambiguities array's items carry NO "op" key at all — the second raw
    # path the strip has to cover independently of the first.
    raw = [dict(_RAW_MODEL_CONFIRMATION)]
    assert "op" not in raw[0]
    ambiguities = _parse_ambiguities(raw)
    assert len(ambiguities) == 1, "the ambiguity must be STRIPPED, not dropped"
    amb = ambiguities[0]
    assert amb.option_keys == []
    assert amb.question_i18n is None
    assert amb.options_i18n is None
    assert amb.question == "Same role?"
    assert amb.options == ["Merge", "Keep separate"]


# ── 6. stance.py's denial receipt carries rationale_key ────────────────────────


def test_record_denials_new_denial_receipt_carries_denial_noted_rationale_key():
    metadata = ProfileMetadata()
    changes = record_denials(
        metadata,
        ["LegalTech"],
        statement="Ich habe keine direkte LegalTech-Erfahrung.",
        source="interview",
    )
    assert len(changes) == 1
    change = changes[0]
    assert change.rationale_key == "denial_noted"
    assert change.new_value == "LegalTech"
    assert change.section == "metadata"
    assert change.field == "denied_concepts"
    assert len(metadata.denied_concepts) == 1
    assert metadata.denied_concepts[0].statement == "Ich habe keine direkte LegalTech-Erfahrung."
