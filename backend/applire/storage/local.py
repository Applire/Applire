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

"""Local filesystem StorageProvider — Community Edition default (ADR 014)."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from applire.storage.base import PathOutsideStorageError, StorageProvider

logger = logging.getLogger(__name__)


class LocalStorageProvider(StorageProvider):
    def __init__(self, upload_dir: str) -> None:
        self._base = Path(upload_dir)

    def _confined(self, file_path: str) -> Path | None:
        """The resolved path when it lies inside the upload dir, else ``None``.

        MD-30: every path this provider reads or deletes comes from a database
        row (the vault JSON's ``photo_url``, ``signature_path``, ``uploads``), and
        a row is data, not a capability. Both sides are ``resolve()``d — ``..``
        segments and symlinks are followed BEFORE the containment test, so
        ``<upload>/../x`` and a link pointing out of the directory are refused
        just like an absolute foreign path. A relative stored path resolves
        against the process cwd exactly as ``open()`` would have, so a row
        written by ``save()`` under a relative ``UPLOAD_DIR`` still matches.
        The base directory itself is not a file and is refused too.
        """
        try:
            base = self._base.resolve()
            target = Path(file_path).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        if target == base or not target.is_relative_to(base):
            return None
        return target

    async def save(self, file_bytes: bytes, filename: str) -> str:
        """Write *file_bytes* under a UUID-prefixed name; return the relative path."""
        dest_dir = self._base
        await asyncio.get_event_loop().run_in_executor(None, dest_dir.mkdir, 0o755, True, True)

        # Preserve extension, prefix with UUID to avoid collisions
        suffix = Path(filename).suffix
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        dest = dest_dir / stored_name

        def _write() -> None:
            dest.write_bytes(file_bytes)

        await asyncio.get_event_loop().run_in_executor(None, _write)
        return str(dest)

    async def delete(self, file_path: str) -> None:
        path = self._confined(file_path)
        if path is None:
            logger.warning(
                "storage: refused to delete %r — not inside the upload directory",
                file_path,
            )
            return

        def _delete() -> None:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        await asyncio.get_event_loop().run_in_executor(None, _delete)

    async def list_files(self) -> list[tuple[str, datetime]]:
        """Enumerate files under the upload dir as (path, mtime-UTC) pairs.

        Paths use the same form save() returns (str of base / name), so they
        compare directly against uploads.file_path / photo_url DB values.
        """
        base = self._base

        def _list() -> list[tuple[str, datetime]]:
            if not base.exists():
                return []
            return [
                (str(p), datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc))
                for p in base.rglob("*")
                if p.is_file()
            ]

        return await asyncio.get_running_loop().run_in_executor(None, _list)

    async def read(self, file_path: str) -> bytes:
        path = self._confined(file_path)
        if path is None:
            logger.warning(
                "storage: refused to read %r — not inside the upload directory",
                file_path,
            )
            raise PathOutsideStorageError(f"Path is outside the upload directory: {file_path}")

        def _read() -> bytes:
            if not path.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            return path.read_bytes()

        return await asyncio.get_running_loop().run_in_executor(None, _read)
