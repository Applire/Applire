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

"""LLM provider ABC — ADR 009.

All concrete implementations live alongside this file:
  mistral.py     — Mistral AI (EU-hosted default)
  openai.py      — OpenAI / OpenAI-compatible (LM Studio, etc.)
  openrouter.py  — OpenRouter (multi-model gateway, Iter 16 addition)
  ollama.py      — Ollama local server (fully offline)

Contract for implementations:
  - Enforce self._timeout on every SDK call via asyncio.wait_for.
  - Retry up to 3 times on provider-specific rate-limit errors (tenacity).
  - After retry exhaustion raise LLMRateLimitError.
  - On timeout raise LLMTimeoutError.
  - Ensure JSON output uses ensure_ascii=False (German umlaut preservation).
"""

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from applire.exceptions import LLMProviderUnavailableError, LLMTruncatedError

logger = logging.getLogger(__name__)

# ── M-3 structured-output rejection wording (shared by openrouter.py and
# requesty.py's `_note_schema_rejection`, so a wording fix lands once) ────────
#
# A bare substring test on any of these three words latches structured output
# off for the rest of the process the moment ANY 400 happens to mention one —
# including an UNRELATED error whose message merely echoes the request's own
# field names ("...your request (model, messages, response_format, max_tokens)
# exceeded the context window..." is a context-length error, not a schema
# rejection, and a gateway that lists every field it received in its error
# text is common). Found adversarially 2026-09-10: a constructed context-length
# 400 naming `response_format` among six other listed fields latched the
# schema off for the whole process based on a misdiagnosis.
#
# Requiring the trigger word to sit near a REJECTION-shaped word — "not
# support(ed)", "unsupported", "invalid", "cannot", … — keeps every phrasing
# this codebase has actually observed a gateway use (`response_format
# json_schema is not supported`, the F-B/M-3 shape the existing tests pin)
# while no longer firing on a field name that is merely present in an
# unrelated diagnostic dump. This does not close the OTHER direction (a real
# rejection phrased with none of these words at all still slips through
# un-latched, paying a failed round-trip on every call) — that half needs a
# real captured gateway error body to ground a fix against, which is why it
# is a collector-line proposal rather than a guess encoded here.
_SCHEMA_REJECTION_TRIGGER = r"(?:json_schema|response_format|structured output)"
_SCHEMA_REJECTION_VERDICT = (
    r"(?:not support(?:ed)?|unsupported|invalid|not allow(?:ed)?|cannot|can't|"
    r"not compatible|not recogni[sz]ed|unrecogni[sz]ed|not permitted)"
)
_SCHEMA_REJECTION_RE = re.compile(
    rf"(?:{_SCHEMA_REJECTION_TRIGGER}.{{0,60}}?{_SCHEMA_REJECTION_VERDICT})"
    rf"|(?:{_SCHEMA_REJECTION_VERDICT}.{{0,60}}?{_SCHEMA_REJECTION_TRIGGER})",
    re.IGNORECASE | re.DOTALL,
)


def is_schema_rejection_message(msg: str) -> bool:
    """True when ``msg`` reads as a gateway rejecting the response schema.

    The ONE place both structured-output providers ask this question — see
    the module comment above for what changed and why.
    """
    return bool(_SCHEMA_REJECTION_RE.search(msg or ""))

# Stop/finish reasons that mean "I ran out of token budget", normalised across
# vendors: OpenAI-style 'length', Anthropic 'max_tokens', Ollama done_reason 'length'.
_TRUNCATION_REASONS = frozenset({"length", "max_tokens"})

# Hard ceiling for the auto-retry-on-truncation safety net (below). A one-off
# truncation is retried once with a doubled budget, but never above this — past
# here the prompt is genuinely too large for a single call and doubling forever
# would only waste tokens and latency before failing anyway.
#
# This MUST sit strictly ABOVE the largest tuned per-chain budget, or the retry net
# is a no-op for that chain: if budget == ceiling, ``bigger = min(2*budget, ceiling)``
# collapses to ``budget`` and ``retry_on_truncation`` re-raises immediately with no
# headroom. The reconciler's per-call budget rose to 32768 (RECONCILE_MAX_TOKENS) to
# fit a rich two-CV + JD merge, so this is set one doubling above that (65536) so a
# freak one-off overflow on that already-large budget can still be retried once.
# Tradeoff: the retry only ever fires on an actual truncation, and max_tokens is a
# *ceiling* billed on real output, so the larger headroom costs nothing on normal
# calls and just buys one extra recovery step on the worst case.
TRUNCATION_RETRY_CEILING: int = 65536

_T = TypeVar("_T")


def clamp_output_budget(requested: int, *, ceiling: int | None = None) -> int:
    """Clamp a requested ``max_tokens`` to the operator-declared output cap (ADR-047 §2).

    Cap-aware budgeting: asking a hard-capped model for more than it can emit does
    not help — it only swaps truncation for a timeout on the slower oversized call.
    When the operator declares the model's real ceiling (``LLM_MAX_OUTPUT_TOKENS``),
    every budget is clamped to it. ``ceiling`` defaults to that setting; pass it
    explicitly in tests. ``0`` / unset means "no known cap" → the request passes
    through unchanged (segmentation, not clamping, covers capped-but-unknown models).
    """
    if ceiling is None:
        from applire.config import settings

        ceiling = settings.llm_max_output_tokens
    if ceiling and ceiling > 0:
        return min(requested, ceiling)
    return requested


async def retry_on_truncation(
    attempt: Callable[[int], Awaitable[_T]],
    *,
    max_tokens: int,
    ceiling: int = TRUNCATION_RETRY_CEILING,
    model: str = "",
) -> _T:
    """General safety net: run a single LLM attempt, retry ONCE on truncation.

    ``attempt`` is an async callable taking the ``max_tokens`` budget to use and
    returning the provider's raw result (it must itself ``raise_if_truncated`` so
    a budget stop surfaces as :class:`LLMTruncatedError`). If the first attempt
    truncates on the token budget, this retries exactly once with ``2 * max_tokens``
    (capped at ``ceiling``) before re-raising — so no chain silently 500s on a
    one-off truncation, yet an uncoverable truncation (already at/above ``ceiling``,
    or truncating again on the larger budget) still raises and never loops forever.

    Lives on the shared base path so every provider can opt in with a one-line
    change to its public ``aparse_json``/``acomplete`` — no ``LLMProvider`` ABC
    signature change, and the mock provider (which never truncates) is untouched.
    """
    # Cap-aware budgeting (ADR-047 §2): never request more than the operator's declared
    # output cap — asking a capped model for more only swaps truncation for a timeout on
    # the slower oversized call. No declared cap (0/unset) → no-op, existing behaviour.
    max_tokens = clamp_output_budget(max_tokens)
    try:
        return await attempt(max_tokens)
    except LLMTruncatedError:
        # Clamp the doubled budget to the operator cap too, so we never double *past* the
        # model's real ceiling. At the cap this collapses to max_tokens → re-raise (no
        # pointless timeout); the large-generation paths recover by switching to segmented.
        bigger = clamp_output_budget(min(2 * max_tokens, ceiling))
        if bigger <= max_tokens:
            # Already at/above the ceiling — retrying can't give more headroom.
            raise
        logger.warning(
            "model=%s truncated at max_tokens=%d; retrying once with max_tokens=%d "
            "(truncation safety net)",
            model or "?", max_tokens, bigger,
        )
        # A second truncation propagates LLMTruncatedError (no further retry).
        return await attempt(bigger)


def raise_if_truncated(stop_reason: Any, *, model: str = "") -> None:
    """Raise LLMTruncatedError if the model stopped on the token budget (ADR-009).

    Guards every provider against silently returning a half-generated output. The
    `isinstance(str)` check keeps it a no-op for unset/mocked stop reasons.
    """
    if isinstance(stop_reason, str) and stop_reason in _TRUNCATION_REASONS:
        raise LLMTruncatedError(
            f"Model {model or '?'} hit the token budget (stop_reason={stop_reason!r}); "
            "output is truncated. Raise max_tokens or reduce reasoning."
        )


def raise_if_no_completion(response: Any, *, model: str = "") -> None:
    """Raise LLMProviderUnavailableError when a chat-completion response has no
    usable choice (issue #256).

    OpenAI-compatible gateways (OpenRouter, Requesty) can answer HTTP 200 with
    an error embedded in the body — ``choices`` comes back empty or ``None`` —
    when the upstream inference provider they routed to is itself down (e.g. a
    Mistral 503 relayed through OpenRouter). The openai SDK does not raise for
    this shape, so callers that blindly index ``response.choices[0]`` crash
    with a raw, user-facing ``TypeError``. Guard every provider's completion
    path with this before indexing.

    Never includes the raw provider payload in the raised message — only logs
    it server-side — so a caller that (mis)renders ``str(exc)`` can't leak it.
    """
    if getattr(response, "choices", None):
        return
    provider_error = getattr(response, "error", None)
    logger.warning(
        "model=%s returned a completion with no choices (likely an upstream "
        "provider outage relayed as HTTP 200); provider-reported error=%r",
        model or "?", provider_error,
    )
    raise LLMProviderUnavailableError(
        f"{model or 'The LLM provider'} returned no completion (upstream outage). "
        "Retry the same request."
    )


class LLMProvider(ABC):
    """Abstract base class for all LLM provider implementations."""

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    @abstractmethod
    async def acomplete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
    ) -> str:
        """Send a prompt and return the text completion.

        Args:
            disable_thinking: Per-call override for reasoning/thinking budget.
                None = use the provider's configured default (thinking left ON for
                serious generations). True = suppress reasoning for short, near-
                deterministic "chrome" generations (interview questions, cv_assist)
                so the token budget goes to the answer, not the reasoning trace.
                Honoured by OpenRouter; accepted and ignored by providers without a
                reasoning toggle.

        Raises:
            LLMRateLimitError: provider is rate-limiting after all retries.
            LLMTimeoutError: call exceeded self._timeout seconds.
            LLMTruncatedError: model stopped on the token budget (output truncated).
        """

    @abstractmethod
    async def aparse_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        disable_thinking: bool | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a prompt and return a parsed JSON dict.

        Args:
            disable_thinking: see acomplete.
            json_schema: an OpenAI-style ``json_schema`` block (``{"name",
                "strict", "schema"}``) describing the expected output. Founder
                ruling M-3: the one call whose output is a typed union
                (`services/profile/reconcile/schema_out.py`) hands the model the
                shape instead of only describing it in prose. Honoured by the
                OpenAI-compatible gateways that support structured output and
                LATCHED off on the first rejection; accepted and ignored by every
                other provider, so a caller never has to ask who supports it.

        Raises:
            LLMRateLimitError: provider is rate-limiting after all retries.
            LLMTimeoutError: call exceeded self._timeout seconds.
            LLMTruncatedError: model stopped on the token budget (output truncated).
        """
