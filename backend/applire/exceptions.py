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


class LLMProviderError(LLMError):
    """A provider call failed in a way the provider module does not classify.

    Error detail hardening: providers raise this in place of a raw SDK/HTTP
    client exception, whose text is not ours and may quote request details.
    ``str(exc)`` is a short static description — the provider, the SDK
    exception's type name and, when there is one, the HTTP status — so callers
    that classify on the status (the ops probe's 401/402/429 reading) still
    work. The original text, scrubbed, is kept on :attr:`detail` for the log.
    """

    def __init__(
        self,
        provider: str,
        *,
        sdk_type: str,
        status_code: int | None = None,
        detail: str = "",
    ) -> None:
        self.provider = provider
        self.sdk_type = sdk_type
        self.status_code = status_code
        self.detail = detail
        status = f", HTTP {status_code}" if status_code is not None else ""
        super().__init__(f"{provider} call failed ({sdk_type}{status}).")


#: SDK exception class names of an unclassified provider error that mean "the
#: provider could not be reached" (no HTTP status by construction).
_CONNECTION_SDK_TYPES = frozenset({
    "APIConnectionError", "ConnectError", "ConnectTimeout", "ReadError",
    "ReadTimeout", "RemoteProtocolError", "ServiceUnavailableError",
})


def is_provider_outage(exc: BaseException) -> bool:
    """Is ``exc`` a provider OUTAGE — the provider could not serve this call right
    now — rather than a configuration error? (Hazelnut build 3, WP-FR, MD3-2.)

    True for :class:`LLMProviderUnavailableError` (a 5xx / the 200-with-no-choices
    quirk) and for an unclassified :class:`LLMProviderError` with a 5xx status or a
    connection-class SDK type. False for 401/402/404 and everything else: a
    misconfigured key or model fails the next call too, so it must fail loudly.
    """
    if isinstance(exc, LLMProviderUnavailableError):
        return True
    if isinstance(exc, LLMProviderError):
        if exc.status_code is not None:
            return exc.status_code >= 500
        return exc.sdk_type in _CONNECTION_SDK_TYPES
    return False


class OCRUnavailableError(RuntimeError):
    """The configured OCR backend cannot run on this instance (#789 L12).

    ``OCR_BACKEND=mistral_vision`` (the default) needs ``MISTRAL_API_KEY``; ``tesseract``
    needs its Python module and system binary. An image or scanned-PDF upload on an
    instance without that raises this instead of an opaque provider/SDK failure, so every
    door can answer with a stable ``ocr_unavailable`` code the frontend translates. The
    message names the setting for the operator's log; it is never sent to the client.
    """
