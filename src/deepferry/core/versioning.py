"""Version chain management for datasets — v1, v2, v3 auto-increment.

Each version lives in its own subdirectory ``{dataset_dir}/v{n}/``.  The chain
is inferred from the filesystem: scanning ``v*`` directories and finding the
maximum numeric suffix yields the next version label.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from deepferry.core.errors import DataSourceError

if TYPE_CHECKING:
    from pathlib import Path

_VERSION_RE = re.compile(r"^v(\d+)$")


class VersionChain:
    """Manages the version chain for a single dataset directory."""

    def __init__(self, dataset_dir: Path) -> None:
        self._dir = dataset_dir

    def next_version(self) -> str:
        """Return the next version label, e.g. ``'v1'`` then ``'v2'``."""
        existing = self.list_versions()
        if not existing:
            return "v1"
        max_n = max(int(v[1:]) for v in existing)
        return f"v{max_n + 1}"

    def list_versions(self) -> list[str]:
        """Return version labels sorted naturally (``['v1','v2',...]``)."""
        if not self._dir.is_dir():
            return []
        found: list[tuple[int, str]] = []
        for child in self._dir.iterdir():
            if not child.is_dir():
                continue
            match = _VERSION_RE.match(child.name)
            if match:
                found.append((int(match.group(1)), child.name))
        found.sort(key=lambda item: item[0])
        return [label for _, label in found]

    def version_dir(self, version: str) -> Path:
        """Return the path to a version's directory, validating the label."""
        if not _VERSION_RE.match(version):
            raise DataSourceError(
                code="INVALID_VERSION",
                message=f"Invalid version label {version!r}; expected 'vN' (e.g. 'v1').",
                suggestion="Use labels returned by list_versions() or next_version().",
            )
        return self._dir / version

    def latest_version(self) -> str | None:
        """Return the highest version label, or ``None`` if no snapshots exist."""
        versions = self.list_versions()
        return versions[-1] if versions else None
