# Copyright (C) 2026 Tobias Rosenbaum
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whether this instance can read an image CV (#789 L12).

The upload surfaces advertise images (JPG/PNG/...) because ``services/cv_parser.py``
parses them, but parsing an image goes through the configured OCR backend, which may not
be runnable: ``mistral_vision`` (the default) needs ``MISTRAL_API_KEY`` however the text
LLM provider is configured, and ``tesseract`` needs its Python module plus the system
binary. This module answers that one question as a boolean; it never returns, logs or
otherwise exposes a key, nor whether one is set beyond that bit.
"""

import shutil


def _tesseract_runnable() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def image_import_available() -> bool:
    """True when the configured ``OCR_BACKEND`` can actually run on this instance."""
    from applire.config import settings

    backend = (settings.ocr_backend or "").lower()
    if backend == "mistral_vision":
        return bool((settings.mistral_api_key or "").strip())
    if backend == "tesseract":
        return _tesseract_runnable()
    return False
