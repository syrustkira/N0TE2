from __future__ import annotations

from pathlib import Path

from .activity import ActivityLog
from .evidence import EvidenceMemory
from .graph import SongKnowledgeMapService
from .lineage import LineageStore
from .provenance import ProvenanceLedger
from .recovery import RecoveryManager
from .twins import TwinAwareSongKnowledgeMapService, TwinEvidenceService


class HeadquartersMemory:
    """Composition root for canonical local memory. Owns no persistence itself."""

    def __init__(self, store: LineageStore):
        self.store = store
        self.evidence = EvidenceMemory(store)
        self.twins = TwinEvidenceService(self.evidence)
        self.activity = ActivityLog(store)
        self.provenance = ProvenanceLedger(store)
        self.recovery = RecoveryManager(store)
        self.knowledge = TwinAwareSongKnowledgeMapService(
            SongKnowledgeMapService(store), self.evidence
        )

    @classmethod
    def create(cls, root: str | Path, artist_name: str) -> "HeadquartersMemory":
        return cls(LineageStore.create(root, artist_name))

    @classmethod
    def open(cls, root: str | Path, profile_id: str) -> "HeadquartersMemory":
        return cls(LineageStore.open(root, profile_id))

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "HeadquartersMemory":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
