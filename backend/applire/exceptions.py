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

"""Application-wide exception hierarchy.

LLM errors are raised by provider implementations and caught by routers.
Providers translate vendor-specific errors (openai.RateLimitError, httpx 429, etc.)
into these unified types so routers never import SDK-specific exceptions.
"""


class LLMError(Exception):
    """Base class for all LLM provider errors."""


class LLMRateLimitError(LLMError):
    """Provider returned 429 after all retries exhausted.

    Retry strategy: 3 attempts, exponential backoff starting at 2s (tenacity).
    Routers should surface this as HTTP 503 with Retry-After guidance.
    """


class LLMTimeoutError(LLMError):
    """A single LLM call exceeded the provider's configured timeout.

    Default timeout is 30s per call (set on LLMProvider.__init__).
    Routers should surface this as HTTP 504.
    """


class LLMProviderUnavailableError(LLMError):
    """The provider is unable to serve the request right now (issue #256).

    Covers two crash shapes actually observed in production (run-4, 2026-07-24):
      - A genuine HTTP 5xx from the gateway/provider (raised by the vendor SDK
        as e.g. ``openai.InternalServerError`` / ``anthropic.InternalServerError``).
      - The OpenRouter/Requesty "200 OK with no usable completion" quirk: the
        upstream inference provider (e.g. Mistral) 503s, but the gateway itself
        answers 200 with an ``choices``-less/None body instead of raising —
        blind ``response.choices[0]`` indexing on that shape crashed with a raw
        ``TypeError: 'NoneType' object is not subscriptable``.

    Retryable: no partial state is ever persisted for the failed turn (routers
    map this to HTTP 503 + a stable ``provider_unavailable`` code). Provider
    implementations MUST NOT embed the raw provider response body/JSON in this
    exception's message — only a short, static, human-safe description — since
    routers/callers may render ``str(exc)`` in logs or (historically) in error
    responses.
    """


class LLMTruncatedError(LLMError):
    """The model stopped because it hit the token budget, not because it finished.

    Raised by providers when the completion's stop reason indicates length
    exhaustion ('length' / 'max_tokens' / 'done_reason=length'). This converts a
    silent half-generated output (a truncated question, or a CV/cover letter that
    closed its JSON early) into a loud, retryable failure — see ADR-009 amendment
    (2026-06-24). Routers should surface this as HTTP 502/retry, never persist the
    partial artifact.
    """
