from __future__ import annotations
"""Local JSON repository for ThesisSnapshot records."""


import json
import os
from pathlib import Path

from backend.domain.thesis_extensions import ThesisSnapshot


def snapshots_enabled() -> bool:
    """Return whether thesis snapshot persistence is explicitly enabled."""
    return (os.getenv("THESIS_ENABLE_SNAPSHOTS", "false") or "").strip().lower() == "true"


class ThesisSnapshotRepository:
    """Persists sanitized thesis snapshots as local JSON files when enabled."""

    def __init__(
        self: ThesisSnapshotRepository,
        base_path: str | Path = "data/snapshots",
    ) -> None:
        self.base_path = Path(base_path)

    def save(self: ThesisSnapshotRepository, snapshot: ThesisSnapshot) -> Path | None:
        """Persist a snapshot, or no-op when THESIS_ENABLE_SNAPSHOTS is false."""
        if not snapshots_enabled():
            return None
        self.base_path.mkdir(parents=True, exist_ok=True)
        path = self.base_path / f"{snapshot.snapshot_id}.json"
        path.write_text(
            json.dumps(snapshot.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def load(self: ThesisSnapshotRepository, snapshot_id: str) -> ThesisSnapshot | None:
        """Load a snapshot by id from the local JSON store."""
        path = self.base_path / f"{snapshot_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ThesisSnapshot.model_validate(data)
