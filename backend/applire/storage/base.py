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

"""StorageProvider ABC — pluggable file storage backend (ADR 014)."""

from abc import ABC, abstractmethod
from datetime import datetime


class PathOutsideStorageError(FileNotFoundError):
    """A path handed to a provider names no file this provider stores.

    A stored path is data (``personal_info.photo_url`` in the vault JSON,
    ``user_settings.signature_path``, ``uploads.file_path``), so a provider never
    trusts it to stay inside its own storage. A subclass of ``FileNotFoundError`` on purpose: every caller already
    treats "absent" as the safe outcome (the render omits the photo, the signature
    is not drawn), so a refused path fails CLOSED at every existing call site
    without each one having to learn a new exception.
    """


class StorageProvider(ABC):
    @abstractmethod
    async def save(self, file_bytes: bytes, filename: str) -> str:
        """Persist *file_bytes* and return the storage path (relative or URI)."""

    @abstractmethod
    async def delete(self, file_path: str) -> None:
        """Remove the file at *file_path*. No-op if not found.

        A path outside the provider's storage is refused (no-op, WARNING log) —
        never unlinked."""

    @abstractmethod
    async def read(self, file_path: str) -> bytes:
        """Return the raw bytes at *file_path*. Raises FileNotFoundError if absent,
        and :class:`PathOutsideStorageError` (a ``FileNotFoundError``) for a path
        outside the provider's storage."""

    async def list_files(self) -> list[tuple[str, datetime]] | None:
        """Enumerate stored files as ``(path, last_modified_utc)`` pairs.

        Optional capability (issue #152): the retention worker's orphan scan
        uses it to reclaim files no DB row references any more. Backends that
        cannot (or choose not to) enumerate keep this default and return
        ``None`` — callers must then skip the scan gracefully. Deliberately
        non-abstract so existing providers (e.g. Cloud's S3 backend) keep
        working unchanged.
        """
        return None
