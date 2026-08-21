from __future__ import annotations

from pathlib import Path

from .activity import ActivityLog
from .context import ContextIsolationService
from .evidence import EvidenceMemory
from .focus import FocusContextService
from .friction import FrictionMemory
from .graph import SongKnowledgeMapService
from .learning import LearningMemory
from .lineage import LineageStore
from .operations import OperationJournal
from .provenance import ProvenanceLedger
from .reconcile import ReconciliationService
from .recovery import RecoveryManager
from .session import SessionMemory
from .shadow import HostShadow
from .skills import SkillMemory
from .transactions import TransactionCoordinator
from .twins import TwinAwareSongKnowledgeMapService, TwinEvidenceService
from .workspace import WorkspaceMemory


class HeadquartersMemory:
    """Composition root for canonical local memory. Owns no persistence itself."""

    def __init__(self, store: LineageStore):
        self.store = store
        self.evidence = EvidenceMemory(store)
        self.twins = TwinEvidenceService(self.evidence)
        self.activity = ActivityLog(store)
        self.workspaces = WorkspaceMemory(store)
        self.shadow = HostShadow(store, self.workspaces)
        self.reconciliation = ReconciliationService(store, self.twins, self.shadow)
        self.focus = FocusContextService(self.workspaces)
        self.operations = OperationJournal(store, self.activity)
        self.transactions = TransactionCoordinator(self.operations)
        self.sessions = SessionMemory(store, self.evidence)
        self.skills = SkillMemory(store, self.evidence, self.sessions)
        self.learning = LearningMemory(store, self.sessions)
        self.friction = FrictionMemory(store, self.learning)
        self.provenance = ProvenanceLedger(store)
        self.recovery = RecoveryManager(store)
        self.context = ContextIsolationService(store, self.evidence)
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