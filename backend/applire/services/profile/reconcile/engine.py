# Copyright (C) 2024-2026 Tobias Rosenbaum
#
# This file is part of Applire.
#
# Applire is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Applire is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Applire. If not, see <https://www.gnu.org/licenses/>.

"""ADR-046 — the single-call reconciler engine.

``reconcile`` turns (current profile + new info + source) into a typed
``ReconcileResult`` in ONE ``provider.aparse_json`` call (no multi-turn tool loop
— that was explicitly rejected in ADR-046). The deterministic applier
(``apply.py``) consumes the result; wiring into the interview / CV / ingest paths
is a later task (US184).

Parsing is DEFENSIVE at the op level: a single op that fails validation is
dropped, the rest are kept. A FAILED CALL is not noise (Hazelnut build 4, R2-13,
P-1): a provider/transport/parse error, or a reply that is not a JSON object, is
retried ONCE and then raised as ``ReconcileFailedError`` — never returned as an
empty ``ReconcileResult``, which every caller reads as "the answer held nothing
new" (the interview then asked a follow-up and the founder's answer was gone).
"""
from __future__ import annotations

import logging
import typing
from typing import Any

from pydantic import TypeAdapter, ValidationError

from applire.constants import RECONCILE_MAX_TOKENS
from applire.exceptions import LLMTimeoutError, LLMTruncatedError, ReconcileFailedError
from applire.prompts.reconcile import (
    RECONCILE_SYSTEM_PROMPT,
    build_reconcile_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import llm_log_stage
from applire.services.model_ids import ShortIdMap, ShortIdProvider
from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.attribution import enforce_attribution
from applire.services.profile.reconcile.position_terms import enforce_position_terms
from applire.services.profile.reconcile.ops import (
    ADAPTER_ONLY_CONFIRMATION_FIELDS,
    CODE_ONLY_CONTEXT_KEYS,
    CODE_ONLY_CONTEXT_KINDS,
    EmptyReason,
    ReconcileOp,
    ReconcileResult,
    RequestConfirmation,
)
from applire.services.profile.reconcile.stance import (
    demote_ops_for_denials,
    enforce_stance,
    offered_choice_gate,
)

logger = logging.getLogger(__name__)

#: The `empty_reason` vocabulary as a runtime set, derived from the ONE Literal
#: in `ops.py` — never a second hand-written list that could drift from it.
_EMPTY_REASONS: frozenset[str] = frozenset(typing.get_args(EmptyReason))

# Validates a single op against the MODEL-EMITTABLE union only (ADR-063 amended
# 2026-08-09 clause 1). Adapter-only ops (`DecisionOp`: today `DemoteSkill`) are
# deliberately absent here — a raw `{"op": "demote_skill", …}` in model output is
# a hallucinated negative statement about the candidate and is dropped, while the
# deterministic emitter below constructs the same op as a typed object and never
# crosses this seam (SF-VAULT.10, #480 PR 1).
_OP_ADAPTER: TypeAdapter[ReconcileOp] = TypeAdapter(ReconcileOp)

#: R2-13 (Hazelnut build 4) — one call plus ONE retry, then ReconcileFailedError.
#: Truncation is not retried here (the provider already retried it on a larger
#: budget, and the import door has its own segmented fallback for it); a
#: timeout is not retried either (RULING B4-4) — it fails at once.
_RECONCILE_ATTEMPTS = 2


async def reconcile(
    profile: MasterProfileData,
    new_info: Any,
    source: str,
    provider: LLMProvider,
    lang: str = "en",
) -> ReconcileResult:
    """Reconcile ``new_info`` into ``profile`` via one LLM call (ADR-046).

    Returns a ``ReconcileResult`` of typed ops + folded ambiguities.

    Error classification (truncation integrity fix):

    * ``LLMTruncatedError`` is RE-RAISED. A truncated reconcile means the model
      ran out of token budget mid-output, so some ops never materialised —
      swallowing it as an empty result silently drops a whole CV's content
      ("one-CV-wins" merge). Truncation is data loss and MUST surface so the
      caller fails that upload cleanly rather than persisting a half-merge. The
      provider already retried once with a larger budget (``retry_on_truncation``)
      before this propagates, so reaching here means the merge genuinely won't fit.
    * Every OTHER provider/transport/parse error — and a reply that is not a
      JSON object — is RETRIED ONCE (R2-13, Hazelnut build 4). If the retry fails
      too, ``ReconcileFailedError`` is raised (chained to the last cause). An
      ``LLMTimeoutError`` raises ``ReconcileFailedError`` WITHOUT a retry
      (RULING B4-4: the wait already spent the whole ``LLM_TIMEOUT``). This
      used to degrade to an empty result on the theory that "an additive merge
      that produced no ops is safe"; it is not: an empty result means "this
      answer held nothing new", so the interview asked a follow-up as if the
      answer were content-free and the answer was lost (P-1, founder instance,
      2026-10-23: ``JSONDecodeError: Extra data`` on ``gpt-6-luna``). Every
      caller now says "not saved" in its own receipt shape (interview turn
      state ``not_saved``, testimony / claim ``status: "error"``, import job
      ``error_code``), and none of them 500s.
    """
    # 783:L79 (ADR-078 amended 2026-10-20, the ADR-094 cl. 6 instrument): the
    # model addresses existing entities by short handles (`id_w1`, `id_s3`)
    # instead of copying 36-character UUIDs; every exact-string handle in the
    # response is mapped back before `_parse_ops`, so nothing downstream sees
    # one. The `id_` namespace keeps them apart from the reconciler's own local
    # refs (`w1`, `p1` — prompt rule 3). Replay of the E2E-3 capture
    # (Strawberry build 2, Luna, thinking off): a mistyped UUID handle in 2/10
    # runs as captured, 0/10 with short handles. A handle the model invents
    # stays unmapped and is receipted `target_unresolved` by the applier.
    llm = ShortIdProvider(
        provider,
        ShortIdMap.from_profile(profile.model_dump(mode="json"), namespace="id_"),
    )
    prompt = build_reconcile_prompt(profile, new_info, source)
    data: Any = None
    for attempt in range(1, _RECONCILE_ATTEMPTS + 1):
        try:
            with llm_log_stage("reconcile"):
                data = await llm.aparse_json(
                    prompt,
                    system=RECONCILE_SYSTEM_PROMPT,
                    temperature=0.1,
                    max_tokens=RECONCILE_MAX_TOKENS,
                    # M-3 — the op union as a SCHEMA, not only as prose, when the
                    # operator has turned it on. `None` keeps today's JSON mode exactly.
                    json_schema=_structured_output_schema(),
                )
        except LLMTruncatedError:
            # Data loss — never mask as an empty merge. Let the caller surface it
            # (the provider already retried once on a larger budget; the import
            # door switches to its segmented fallback on this error).
            logger.warning("reconcile: output truncated on the token budget; propagating (data loss)")
            raise
        except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
            failure: BaseException = exc
        else:
            if isinstance(data, dict):
                break
            # A JSON array / string / number is not a reconcile payload: the
            # call failed as surely as an unparseable reply did.
            failure = ReconcileFailedError(
                f"reconcile reply was a JSON {type(data).__name__}, not an object"
            )
        # RULING B4-4 (Hazelnut build 4): a timeout already spent the full
        # LLM_TIMEOUT budget — a retry would double the wait before the user
        # hears "not saved". The user's resend is the retry.
        retryable = not isinstance(failure, LLMTimeoutError)
        if retryable and attempt < _RECONCILE_ATTEMPTS:
            logger.warning(
                "reconcile: attempt %d failed (%s); retrying once (R2-13)",
                attempt, type(failure).__name__,
            )
            continue
        logger.error(
            "reconcile: failed after %d attempts (%s); raising ReconcileFailedError — "
            "the new information was NOT merged (R2-13)",
            attempt, type(failure).__name__,
        )
        raise ReconcileFailedError(
            "The reconcile call failed twice; nothing was merged."
        ) from failure

    # #370 — capture WHICH raw ops failed schema validation (not just drop
    # them), so the testimony write-loss witness can report an `op_rejected`
    # item instead of the loss being visible only at DEBUG log level.
    rejected_ops: list[str] = []
    ops = _parse_ops(data.get("ops"), rejected=rejected_ops)
    # U-1r (ADR-046 amended 2026-10-28) — a bullet conflict whose own declared
    # values do not hold is not raised; its incoming bullet goes the add_bullets
    # way (on an import, through the restatement check).
    from applire.services.profile.reconcile.conflict_values import route_unchecked_conflicts

    ops = route_unchecked_conflicts(ops)
    ambiguities = _parse_ambiguities(data.get("ambiguities"))
    # D-8 (Hazelnut build 5) — a model-asked "which position does this project
    # belong to?" carries prompt handles in its options; rebuilt as family 6
    # (both languages, `entry:<id>` keys) so its answer can write the parent.
    from applire.services.profile.reconcile.project_parent import (
        attached_in_batch,
        bind_project_parent_ask,
        check_parent_dates,
    )
    from applire.services.profile.reconcile.project_parent import _project_name

    def _project_name_of(rc):
        return _project_name(dict(rc.context or {}))

    # D-25 — a project's parent must be a position whose dates overlap the
    # project's; a non-overlapping parent is dropped (asked as family 6 when
    # two or more held positions overlap). Before D-16 reads the parents.
    ops, date_asks = check_parent_dates(ops, profile)
    asked = {
        (_project_name_of(a) or "").casefold()
        for a in [*ambiguities, *(o for o in ops if isinstance(o, RequestConfirmation))]
    }
    ambiguities = ambiguities + [a for a in date_asks if (_project_name_of(a) or "").casefold() not in asked]

    # D-16 — an ask about a project already attached to a held position is
    # redundant (its answer could change nothing) and is not raised.
    attached = attached_in_batch(ops, profile)
    ops = [
        bind_project_parent_ask(o, llm.id_map, profile, attached) if isinstance(o, RequestConfirmation) else o
        for o in ops
    ]
    ops = [o for o in ops if o is not None]
    ambiguities = [
        a for a in (bind_project_parent_ask(a, llm.id_map, profile, attached) for a in ambiguities)
        if a is not None
    ]
    denials = _parse_denials(data.get("denials"))
    # G-3 (ADR-064 / ADR-059 amended 2026-11-04; MD6-7) — an answer that is
    # ONLY a choice Applire offered carries no word of the candidate's own, so
    # its denials are not the candidate's no: dropped HERE, before the stance
    # guard, the demotion emitter and every door's `record_denials` read
    # `denials`. Beside any typed word the reconciler's judgement stands. The offered names the candidate's
    # own words do not repeat stay vetoes for the stance guard (`unclaimed`):
    # Applire's text names them WITHOUT claiming, so it cannot ground a claim.
    # The identity on every door but the built-in interview.
    denials, _dropped, unclaimed = offered_choice_gate(denials, new_info, source)
    # Stance guard (#127, ADR-061): the model's own denials outrank its ops;
    # interview-turn token claims are resolved via the testimony predicate
    # (deterministic accept + LLM adjudication of the uncertain band, citation
    # verified in code). `provider` carries the adjudication call — same
    # provider the reconcile call itself just used.
    ops = await enforce_stance(
        ops, denials=denials + [u for u in unclaimed if u not in denials],
        new_info=new_info, source=source, provider=provider,
    )
    # Attribution guard (#243): a multi-employer answer's clause must never
    # silently land on a DIFFERENT employer's entity — deterministic backstop
    # on top of the model's own (occasionally wrong) target choice.
    ops = enforce_attribution(ops, profile=profile, new_info=new_info, source=source)
    # #831 (ADR-046 amended 2026-11-13, RULING T-1) — a position title (a
    # deputy or unit-head title tied to an organisational unit) the person's
    # answer and the edited entry both lack is HELD and asked once: rule 4
    # forbids it, the model still wrote "Stellvertreterin" for "die operative
    # Nummer zwei" (UX audit 4). After stance and attribution, so it reads the
    # bullets that will actually land.
    ops = enforce_position_terms(ops, profile=profile, new_info=new_info, source=source)
    # RULING MD-6 (Sorbet WP-T, 2026-11-13) — a model-emitted engagement ask
    # (founder ruling V-5/V-6: "is this a separate position?") carries its own
    # entity op under `context["incoming"]` (+ `pending_bullets`), and the
    # interview door executes it on the answer through `commit_ops`, OUTSIDE
    # this function. So the carried content passes the SAME three guards here,
    # at park time, where the person's answer is known; whatever they hold
    # comes back as their own asks.
    stance_denials = denials + [u for u in unclaimed if u not in denials]
    guard_asks: list[RequestConfirmation] = []
    guarded_ops: list[ReconcileOp] = []
    for op in ops:
        if isinstance(op, RequestConfirmation):
            op, extra = await _guard_engagement_ask(
                op, profile=profile, new_info=new_info, source=source,
                provider=provider, denials=stance_denials,
            )
            guard_asks.extend(extra)
        guarded_ops.append(op)
    guarded_ambiguities: list[RequestConfirmation] = []
    for amb in ambiguities:
        amb, extra = await _guard_engagement_ask(
            amb, profile=profile, new_info=new_info, source=source,
            provider=provider, denials=stance_denials,
        )
        guard_asks.extend(extra)
        guarded_ambiguities.append(amb)
    ops = guarded_ops + guard_asks
    ambiguities = guarded_ambiguities
    # #485 / ADR-063 clause 8(e) (amended 2026-08-08) — a retraction of a skill
    # the vault already holds as `confirmed` emits a `demote_skill` op, so the
    # status move flows through the ONE write path (`apply_ops`) like every
    # other op instead of becoming a fourth bespoke vault write.
    #
    # HERE, and not in the three doors that call `record_denials`: ADR-066 puts
    # the emission rule in the core, not per door. Every caller of this function
    # (`interview_bridge`, `testimony_bridge`, `agent_bridge`, `import_bridge`)
    # already hands `result.ops` straight to `apply_ops`, so one edit gives all
    # four doors the identical rule with no seam at which they can drift.
    #
    # Appended LAST so a mixed turn ("Docker ja, Kubernetes nie angefasst")
    # resolves in the retraction's favour — never-claim beats claim (ADR-040).
    ops = list(ops) + demote_ops_for_denials(profile, denials)
    return ReconcileResult(
        ops=ops,
        ambiguities=ambiguities,
        denials=denials,
        rejected_ops=rejected_ops,
        # ADR-046 amended 2026-09-11 — read AFTER the guards, off the raw
        # payload: `ops` here has already been widened by `demote_skill`
        # emission, so "did the MODEL emit nothing" is a question only the
        # payload can answer. The witness gates on what actually landed.
        empty_reason=_parse_empty_reason(data.get("empty_reason")),
    )


#: The engagement sections whose ask carries an executable entity op
#: (`session._ENGAGEMENT_OPS`), by the op name the op parser reads.
_ENGAGEMENT_OP_NAMES = {
    "work_experience": "upsert_work",
    "projects": "upsert_project",
    "volunteer_activities": "upsert_volunteer",
}


async def _guard_engagement_ask(
    ask: RequestConfirmation,
    *,
    profile: MasterProfileData,
    new_info: Any,
    source: str,
    provider: LLMProvider,
    denials: list[str],
) -> tuple[RequestConfirmation, list[RequestConfirmation]]:
    """RULING MD-6 — run the stance, attribution and position guards over the
    entity op and bullets an engagement ask carries, exactly as over a batch.

    Returns the ask with its carried content replaced by what survived the
    guards, plus every ask the guards raised (their own builders). An ask whose
    entity op does not survive (or never parsed) loses ``incoming`` and
    ``pending_bullets`` entirely: the interview door then has nothing to
    execute (``_apply_engagement_confirmation`` returns ``False``)."""
    context = dict(ask.context or {})
    incoming = context.get("incoming")
    op_name = _ENGAGEMENT_OP_NAMES.get(str(context.get("section")))
    if op_name is None or not isinstance(incoming, dict):
        return ask, []
    ref = str(incoming.get("ref") or "confirmed-1")
    raw: list[dict[str, Any]] = [{**incoming, "op": op_name, "ref": ref}]
    # The ask is a near-duplicate at the SAME employer, and a "merge" answer
    # lands the bullets on the existing entry: so they are checked against that
    # entry when the ask names one the profile holds (attribution's own scope
    # is an existing target), else against the new entity's ref.
    held_ids = {
        e.id for e in [*profile.work_experience, *profile.projects, *profile.volunteer_activities]
    }
    existing = [str(i) for i in context.get("existing_ids") or [] if i]
    target = existing[0] if existing and existing[0] in held_ids else ref
    carried = context.get("pending_bullets")
    if isinstance(carried, dict) and any(carried.values()):
        raw.append({"op": "add_bullets", "target": target, **{
            f: list(carried.get(f) or [])
            for f in ("responsibilities", "achievements", "technologies")
        }})
    batch = _parse_ops(raw)
    batch = await enforce_stance(
        batch, denials=denials, new_info=new_info, source=source, provider=provider,
    )
    batch = enforce_attribution(batch, profile=profile, new_info=new_info, source=source)
    batch = enforce_position_terms(batch, profile=profile, new_info=new_info, source=source)

    entity = next((o for o in batch if getattr(o, "op", None) == op_name), None)
    bullets = [o for o in batch if getattr(o, "op", None) == "add_bullets"
               and getattr(o, "target", None) == target]
    raised = [o for o in batch if isinstance(o, RequestConfirmation)]
    context.pop("incoming", None)
    context.pop("pending_bullets", None)
    if entity is not None:
        context["incoming"] = entity.model_dump(exclude={"op"})
        kept = {
            f: [x for b in bullets for x in getattr(b, f)]
            for f in ("responsibilities", "achievements", "technologies")
        }
        if any(kept.values()):
            context["pending_bullets"] = kept
    if context != dict(ask.context or {}):
        logger.info(
            "reconcile: engagement ask's carried content changed by the guards "
            "(MD-6): entity=%s raised=%d", entity is not None, len(raised),
        )
    return ask.model_copy(update={"context": context}), raised


def _structured_output_schema() -> dict[str, Any] | None:
    """The reconciler's JSON schema when `LLM_STRUCTURED_OUTPUT=auto`, else None.

    Read at call time rather than at import: an operator who flips the setting
    should not have to know that a module-level constant cached the old answer.
    Never raises — a schema is an optimisation, and failing to build one may not
    fail a vault write.
    """
    try:
        from applire.config import settings

        if (settings.llm_structured_output or "off").lower() != "auto":
            return None
        from applire.services.profile.reconcile.schema_out import (
            reconcile_json_schema_param,
        )

        return reconcile_json_schema_param()
    except Exception:  # noqa: BLE001 — never let schema construction break a turn
        logger.debug("reconcile: could not build the response schema; using JSON mode")
        return None


def _strip_adapter_only(item: Any) -> Any:
    """Remove the confirmation fields only deterministic code may fill (#669).

    ADR-063's governing rule is *never widen an op the model can emit with a
    more powerful parameter*. ``RequestConfirmation.option_keys`` is the
    IDENTITY a vault WRITE resolves the candidate's answer on (ADR-063 amended
    2026-09-05), and ``question_i18n`` / ``options_i18n`` assert that the text
    exists in two languages. The model emits ``request_confirmation`` routinely
    (prompt rule 6), so leaving these fields reachable from raw model output
    would let a hallucinated key decide a merge, and let the model translate its
    own question unreviewed.

    Stripped rather than rejected: a model that volunteers the fields still gets
    a valid, useful confirmation — it just loses the half it is not entitled to.
    Rejecting the whole op would turn an over-eager model into silent data loss,
    which is the direction every guard in this module refuses to fail in.

    Non-dicts and every other op type pass through untouched.
    """
    if not isinstance(item, dict) or item.get("op") != "request_confirmation":
        return item
    return strip_confirmation_extras(item)


def strip_confirmation_extras(item: Any) -> Any:
    """``_strip_adapter_only`` without the op-type guard, for the SECOND raw
    path: the top-level ``"ambiguities"`` array, whose items are validated
    straight into ``RequestConfirmation`` by ``_parse_ambiguities`` and
    therefore carry no ``"op"`` key at all.

    Two raw paths, one rule — a strip applied at only one of them is the hole
    the guard was written to close (ADR-066: one logical operation, one
    implementation, at every seam that performs it).
    """
    if not isinstance(item, dict):
        return item
    item = _strip_code_only_context(item)
    if not any(k in item for k in ADAPTER_ONLY_CONFIRMATION_FIELDS):
        return item
    stripped = {k: v for k, v in item.items() if k not in ADAPTER_ONLY_CONFIRMATION_FIELDS}
    logger.warning(
        "reconcile: stripped adapter-only field(s) %s from a model-emitted "
        "confirmation — option_keys is a vault-write identity and is never the "
        "model's to supply (ADR-063 amended 2026-09-05, #669)",
        sorted(k for k in ADAPTER_ONLY_CONFIRMATION_FIELDS if k in item),
    )
    return stripped


def _strip_code_only_context(item: dict) -> dict:
    """MD-4 — drop the context keys only deterministic code may set: a ``kind``
    naming a code-built family, and family 7's ``items``. Same direction as the
    adapter-only strip: the confirmation survives, the forged half does not."""
    context = item.get("context")
    if not isinstance(context, dict):
        return item
    drop = [k for k in CODE_ONLY_CONTEXT_KEYS if k in context]
    if context.get("kind") in CODE_ONLY_CONTEXT_KINDS:
        drop.append("kind")
    if not drop:
        return item
    logger.warning(
        "reconcile: stripped code-only context key(s) %s from a model-emitted "
        "confirmation — the applier dispatches answers on them (MD-4)",
        sorted(drop),
    )
    return {**item, "context": {k: v for k, v in context.items() if k not in drop}}


def _parse_ops(raw: Any, *, rejected: list[str] | None = None) -> list[ReconcileOp]:
    """Validate each op independently; drop the ones that fail, keep the rest.

    ``rejected``, when supplied, is APPENDED the raw ``op`` field of every
    dropped item (``"<unknown>"`` when the item carries no string ``op`` key
    at all), in encounter order — an OPT-IN out-parameter (#370) so every
    existing direct caller of this function (a dozen op-family unit tests
    call it directly) keeps its exact pre-#370 signature and return shape;
    only ``reconcile()`` passes it, to populate ``ReconcileResult.
    rejected_ops`` for the testimony write-loss witness.
    """
    if not isinstance(raw, list):
        return []
    ops: list[ReconcileOp] = []
    for item in raw:
        try:
            ops.append(_OP_ADAPTER.validate_python(_strip_adapter_only(item)))
        except ValidationError:
            # #602 — WARNING, not DEBUG: a schema-rejected op is a silent data
            # loss for whichever batch emitted it (an entire incoming section
            # can go missing this way), and DEBUG is invisible under any
            # production log configuration. Named by its own `op` type so an
            # operator can tell WHICH shape the model drifted off of, without
            # re-running under DEBUG to even notice it happened.
            label = item.get("op") if isinstance(item, dict) else None
            label = label if isinstance(label, str) and label else "<unknown>"
            logger.warning("reconcile: dropped malformed op (op=%s): %r", label, item)
            if rejected is not None:
                rejected.append(label)
    return ops


def _parse_denials(raw: Any) -> list[str]:
    """Denied-token list from the payload; defensively typed (#127)."""
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item.strip()]


def _parse_empty_reason(raw: Any) -> EmptyReason | None:
    """The model's own reason for an empty batch, or ``None`` (ADR-046 amended
    2026-09-11, ruling M5.1.4).

    Fails closed in the direction the receipt can survive: anything that is not
    one of the three vocabulary values — absent, misspelled, a sentence, a list,
    a model that ignored the ask — becomes ``None``, and ``None`` is what makes
    :func:`applire.services.profile.reconcile.witness.compute_no_write` fall
    back to the original ``no_write`` copy. The candidate is told the turn wrote
    nothing either way; only the EXPLANATION depends on the model complying.

    Never validated against whether ``ops`` is really empty: the prompt asks for
    the field only on an empty batch, but a model that volunteers it on a
    non-empty one costs nothing — the witness only runs when the turn produced
    no receipt of any kind.
    """
    if raw in _EMPTY_REASONS:
        return raw  # type: ignore[return-value]
    if raw is not None:
        logger.debug("reconcile: ignored unrecognised empty_reason %r", raw)
    return None


def _parse_ambiguities(raw: Any) -> list[RequestConfirmation]:
    """Validate each ambiguity as a RequestConfirmation; drop malformed ones."""
    if not isinstance(raw, list):
        return []
    ambiguities: list[RequestConfirmation] = []
    for item in raw:
        try:
            ambiguities.append(
                RequestConfirmation.model_validate(strip_confirmation_extras(item))
            )
        except ValidationError:
            logger.debug("reconcile: dropped malformed ambiguity %r", item)
    return ambiguities
