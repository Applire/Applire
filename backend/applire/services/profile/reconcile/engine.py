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

Parsing is DEFENSIVE: a single op that fails validation is dropped, the rest are
kept; a wholly-unusable payload yields an empty ``ReconcileResult``. The engine
never raises on LLM noise.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import TypeAdapter, ValidationError

from applire.constants import RECONCILE_MAX_TOKENS
from applire.exceptions import LLMTruncatedError
from applire.prompts.reconcile import (
    RECONCILE_SYSTEM_PROMPT,
    build_reconcile_prompt,
)
from applire.providers.llm.base import LLMProvider
from applire.providers.llm.debug_log import llm_log_stage
from applire.schemas.profile import MasterProfileData
from applire.services.profile.reconcile.attribution import enforce_attribution
from applire.services.profile.reconcile.ops import (
    ADAPTER_ONLY_CONFIRMATION_FIELDS,
    ReconcileOp,
    ReconcileResult,
    RequestConfirmation,
)
from applire.services.profile.reconcile.stance import (
    demote_ops_for_denials,
    enforce_stance,
)

logger = logging.getLogger(__name__)

# Validates a single op against the MODEL-EMITTABLE union only (ADR-063 amended
# 2026-08-09 clause 1). Adapter-only ops (`DecisionOp`: today `DemoteSkill`) are
# deliberately absent here — a raw `{"op": "demote_skill", …}` in model output is
# a hallucinated negative statement about the candidate and is dropped, while the
# deterministic emitter below constructs the same op as a typed object and never
# crosses this seam (SF-VAULT.10, #480 PR 1).
_OP_ADAPTER: TypeAdapter[ReconcileOp] = TypeAdapter(ReconcileOp)


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
    * Every OTHER provider/transport/parse error still degrades to an empty result
      (unchanged intent): an additive merge that produced no ops is safe — the
      existing profile is untouched — so transient LLM noise never 500s the upload.
    """
    try:
        with llm_log_stage("reconcile"):
            data = await provider.aparse_json(
                build_reconcile_prompt(profile, new_info, source),
                system=RECONCILE_SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=RECONCILE_MAX_TOKENS,
                # M-3 — the op union as a SCHEMA, not only as prose, when the
                # operator has turned it on. `None` keeps today's JSON mode exactly.
                json_schema=_structured_output_schema(),
            )
    except LLMTruncatedError:
        # Data loss — never mask as an empty merge. Let the caller surface it.
        logger.warning("reconcile: output truncated on the token budget; propagating (data loss)")
        raise
    except Exception:  # noqa: BLE001 — never let other LLM/transport errors escape
        logger.exception("reconcile: provider.aparse_json failed; returning empty result")
        return ReconcileResult()

    if not isinstance(data, dict):
        return ReconcileResult()

    # #370 — capture WHICH raw ops failed schema validation (not just drop
    # them), so the testimony write-loss witness can report an `op_rejected`
    # item instead of the loss being visible only at DEBUG log level.
    rejected_ops: list[str] = []
    ops = _parse_ops(data.get("ops"), rejected=rejected_ops)
    ambiguities = _parse_ambiguities(data.get("ambiguities"))
    denials = _parse_denials(data.get("denials"))
    # Stance guard (#127, ADR-061): the model's own denials outrank its ops;
    # interview-turn token claims are resolved via the testimony predicate
    # (deterministic accept + LLM adjudication of the uncertain band, citation
    # verified in code). `provider` carries the adjudication call — same
    # provider the reconcile call itself just used.
    ops = await enforce_stance(
        ops, denials=denials, new_info=new_info, source=source, provider=provider,
    )
    # Attribution guard (#243): a multi-employer answer's clause must never
    # silently land on a DIFFERENT employer's entity — deterministic backstop
    # on top of the model's own (occasionally wrong) target choice.
    ops = enforce_attribution(ops, profile=profile, new_info=new_info, source=source)
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
        ops=ops, ambiguities=ambiguities, denials=denials, rejected_ops=rejected_ops
    )


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
