from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from .capability_evidence import CapabilityEvidenceMemory
from .evidence import EvidenceClaim, EvidenceMemory
from .lineage import LineageCorruptionError, LineageStore, NotFoundError, ValidationError
from .workspace import WorkspaceMemory

INTENT_CAPSULE_SCHEMA_VERSION = 1
INTENT_CAPSULE_KEY_PREFIX = "intent.capsule."
INTENT_KINDS = {
    "MUSICAL_STRUCTURE",
    "TIMING_FEEL",
    "SOUND_CHARACTER",
    "PROCESSING_BEHAVIOR",
    "AUTOMATION_BEHAVIOR",
    "ARRANGEMENT",
    "PERFORMANCE",
    "OTHER",
}
PRESERVATION_GOALS = {"EDITABLE", "AUDIBLE_RESULT", "REFERENCE_ONLY"}
FALLBACK_POLICIES = {
    "FREEZE_WITH_RECIPE",
    "MANUAL_REBUILD_WITH_RECIPE",
    "NONE",
}
CAPABILITY_STATES = {
    "AVAILABLE",
    "UNAVAILABLE",
    "UNKNOWN",
    "NO_EVIDENCE",
    "UNSPECIFIED",
}
TRANSFER_DISPOSITIONS = {
    "ROUTE_AVAILABLE",
    "NEEDS_EVIDENCE",
    "FREEZE_RECIPE_REQUIRED",
    "MANUAL_REBUILD_REQUIRED",
    "BLOCKED",
    "REFERENCE_ONLY",
}
TRANSFER_READINESS_STATES = {
    "ROUTES_IDENTIFIED",
    "FALLBACK_REQUIRED",
    "NEEDS_EVIDENCE",
    "BLOCKED",
    "REFERENCE_ONLY",
}
INTENT_CAPSULE_AUTHORITY = "EVIDENCE_ONLY"
_FACET_ID = re.compile(r"[a-z0-9][a-z0-9._:-]{0,79}\Z")


def _text(value: object, field_name: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text")
    text = " ".join(value.split())
    if not text:
        raise ValidationError(f"{field_name} must not be empty")
    if len(text) > maximum:
        raise ValidationError(f"{field_name} is too long")
    return text


def _optional_text(
    value: object | None,
    field_name: str,
    *,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    return _text(value, field_name, maximum=maximum)


def _enum(value: object, field_name: str, allowed: set[str]) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text")
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    if normalized not in allowed:
        raise ValidationError(f"unsupported {field_name}: {normalized}")
    return normalized


def _facet_identifier(value: object) -> str:
    if not isinstance(value, str):
        raise ValidationError("facet_id must be text")
    if value != value.strip() or not _FACET_ID.fullmatch(value):
        raise ValidationError(
            "facet_id must be a canonical lowercase semantic key"
        )
    return value


@dataclass(frozen=True)
class IntentFacet:
    """One artist-owned semantic facet that should survive a host move."""

    facet_id: str
    meaning: str
    required_capability: str | None
    preservation_goal: str
    fallback_policy: str

    def __post_init__(self) -> None:
        facet_id = _facet_identifier(self.facet_id)
        meaning = _text(self.meaning, "facet meaning", maximum=500)
        capability = _optional_text(
            self.required_capability,
            "required_capability",
            maximum=180,
        )
        goal = _enum(
            self.preservation_goal,
            "preservation_goal",
            PRESERVATION_GOALS,
        )
        fallback = _enum(
            self.fallback_policy,
            "fallback_policy",
            FALLBACK_POLICIES,
        )
        if goal == "REFERENCE_ONLY":
            if capability is not None:
                raise ValidationError(
                    "REFERENCE_ONLY facets must not fabricate a required capability"
                )
            if fallback != "NONE":
                raise ValidationError(
                    "REFERENCE_ONLY facets do not need an execution fallback policy"
                )
        elif capability is None:
            raise ValidationError(
                "EDITABLE/AUDIBLE_RESULT facets require an explicit capability name"
            )
        object.__setattr__(self, "facet_id", facet_id)
        object.__setattr__(self, "meaning", meaning)
        object.__setattr__(self, "required_capability", capability)
        object.__setattr__(self, "preservation_goal", goal)
        object.__setattr__(self, "fallback_policy", fallback)


@dataclass(frozen=True)
class IntentCapsule:
    id: str
    profile_id: str
    artist_id: str
    song_id: str
    version_id: str | None
    source_workspace_id: str | None
    source_workspace_observation_id: str | None
    source_runtime_fingerprint: str | None
    source_host_family: str | None
    intent_kind: str
    summary: str
    facets: tuple[IntentFacet, ...]
    source_claim_id: str
    source_kind: str
    source_ref: str | None
    source_twin_domain: str
    source_current: bool
    source_environment_current: bool
    representation_claim_id: str
    representation_sequence: int
    authority: str = INTENT_CAPSULE_AUTHORITY
    transfer_verified: bool = field(default=False, init=False)
    equivalence_verified: bool = field(default=False, init=False)
    render_or_freeze_performed: bool = field(default=False, init=False)
    recipe_created: bool = field(default=False, init=False)
    version_created: bool = field(default=False, init=False)
    mutation_authorized: bool = field(default=False, init=False)
    provider_authority_granted: bool = field(default=False, init=False)
    external_action_authority_granted: bool = field(default=False, init=False)

    @property
    def attention_state(self) -> str:
        if not self.source_current or not self.source_environment_current:
            return "NEEDS_REVALIDATION"
        return "AVAILABLE"


@dataclass(frozen=True)
class IntentTransferFacetAssessment:
    facet_id: str
    meaning: str
    required_capability: str | None
    preservation_goal: str
    fallback_policy: str
    capability_state: str
    availability_states: tuple[str, ...]
    capability_evidence_ids: tuple[str, ...]
    disposition: str
    transfer_verified: bool = field(default=False, init=False)
    equivalence_verified: bool = field(default=False, init=False)
    render_or_freeze_performed: bool = field(default=False, init=False)
    recipe_created: bool = field(default=False, init=False)
    mutation_authorized: bool = field(default=False, init=False)
    external_action_authority_granted: bool = field(default=False, init=False)


@dataclass(frozen=True)
class IntentTransferAssessment:
    capsule_id: str
    song_id: str
    source_workspace_id: str | None
    destination_workspace_id: str
    destination_workspace_observation_id: str
    destination_runtime_fingerprint: str
    destination_host_family: str
    workspace_lineage_state: str
    readiness_state: str
    facets: tuple[IntentTransferFacetAssessment, ...]
    authority: str = INTENT_CAPSULE_AUTHORITY
    transfer_verified: bool = field(default=False, init=False)
    equivalence_verified: bool = field(default=False, init=False)
    render_or_freeze_performed: bool = field(default=False, init=False)
    recipe_created: bool = field(default=False, init=False)
    version_created: bool = field(default=False, init=False)
    mutation_authorized: bool = field(default=False, init=False)
    provider_authority_granted: bool = field(default=False, init=False)
    external_action_authority_granted: bool = field(default=False, init=False)


class IntentCapsuleService:
    """Artist/Song semantic continuity above host-specific implementation.

    Capsules preserve what matters. Destination assessments consume only exact
    current Workspace and CapabilityEvidence facts. They never claim transfer,
    equivalence, rendering, freezing, recipe execution, mutation, or provider
    authority.
    """

    _EXPECTED_PAYLOAD_FIELDS = {
        "schema_version",
        "capsule_id",
        "song_id",
        "version_id",
        "source_workspace_id",
        "source_workspace_observation_id",
        "source_runtime_fingerprint",
        "source_host_family",
        "intent_kind",
        "summary",
        "facets",
        "source_claim_id",
        "source_kind",
        "source_ref",
        "source_twin_domain",
    }
    _EXPECTED_FACET_FIELDS = {
        "facet_id",
        "meaning",
        "required_capability",
        "preservation_goal",
        "fallback_policy",
    }

    def __init__(
        self,
        store: LineageStore,
        evidence: EvidenceMemory,
        workspaces: WorkspaceMemory,
        capability_evidence: CapabilityEvidenceMemory,
    ) -> None:
        if not isinstance(store, LineageStore):
            raise TypeError("IntentCapsuleService requires canonical LineageStore")
        if not isinstance(evidence, EvidenceMemory) or evidence.store is not store:
            raise TypeError(
                "IntentCapsuleService requires EvidenceMemory on the same LineageStore"
            )
        if not isinstance(workspaces, WorkspaceMemory) or workspaces.store is not store:
            raise TypeError(
                "IntentCapsuleService requires WorkspaceMemory on the same LineageStore"
            )
        if (
            not isinstance(capability_evidence, CapabilityEvidenceMemory)
            or capability_evidence.store is not store
            or capability_evidence.workspaces is not workspaces
        ):
            raise TypeError(
                "IntentCapsuleService requires CapabilityEvidenceMemory on the same WorkspaceMemory/LineageStore"
            )
        self.store = store
        self.evidence = evidence
        self.workspaces = workspaces
        self.capability_evidence = capability_evidence
        self._validate_existing()

    def _normalize_song(self, song_id: object) -> str:
        if not isinstance(song_id, str) or not song_id.strip():
            raise ValidationError("song_id must be non-empty text")
        normalized = song_id.strip()
        song = self.store.get_song(normalized)
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {normalized}"
            )
        if song.artist_id != self.store.primary_artist_id:
            raise ValidationError("Intent Capsule Song belongs to a different Artist")
        return song.id

    def _normalize_version(self, song_id: str, version_id: object | None) -> str | None:
        if version_id is None:
            return None
        if not isinstance(version_id, str) or not version_id.strip():
            raise ValidationError("version_id must be non-empty text or None")
        normalized = version_id.strip()
        version = self.store.get_version(normalized)
        if version is None:
            raise NotFoundError(f"version not found: {normalized}")
        if version.song_id != song_id:
            raise ValidationError("Intent Capsule Version belongs to a different Song")
        return version.id

    def _normalize_workspace(
        self,
        song_id: str,
        workspace_id: object | None,
    ) -> str | None:
        if workspace_id is None:
            return None
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValidationError("workspace_id must be non-empty text or None")
        normalized = workspace_id.strip()
        workspace = self.workspaces.get(normalized)
        if workspace is None:
            raise NotFoundError(f"workspace not found: {normalized}")
        if workspace.song_id != song_id:
            raise ValidationError("Intent Capsule Workspace belongs to a different Song")
        return workspace.id

    @staticmethod
    def _claim_is_active(evidence: EvidenceMemory, claim: EvidenceClaim) -> bool:
        return any(
            active.id == claim.id
            for active in evidence.active_claims(
                claim.scope_kind,
                claim.scope_id,
                claim.key,
            )
        )

    def _require_intent_source(
        self,
        claim_id: object,
        *,
        song_id: str,
        version_id: str | None,
    ) -> EvidenceClaim:
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValidationError("source_claim_id must be non-empty text")
        claim = self.evidence.get_claim(claim_id.strip())
        if claim is None:
            raise NotFoundError(f"evidence claim not found: {claim_id}")
        if claim.key.startswith(INTENT_CAPSULE_KEY_PREFIX):
            raise ValidationError(
                "Intent Capsules require independent artist-intent evidence"
            )
        if claim.source_kind != "USER_DECLARED":
            raise ValidationError(
                "Intent Capsule source must be explicit USER_DECLARED artist intent"
            )
        if claim.twin_domain != "CREATIVE":
            raise ValidationError(
                "Intent Capsule source must be CREATIVE evidence, not technical observation"
            )
        if claim.scope_kind == "SONG":
            if claim.scope_id != song_id:
                raise ValidationError("Intent Capsule source evidence crosses Song scope")
        elif claim.scope_kind == "VERSION":
            if version_id is None or claim.scope_id != version_id:
                raise ValidationError(
                    "Version-scoped Intent Capsule source requires that exact Version context"
                )
            version = self.store.get_version(claim.scope_id)
            if version is None or version.song_id != song_id:
                raise ValidationError("Intent Capsule source Version crosses Song scope")
        else:
            raise ValidationError(
                "Intent Capsule source must be exact Song or Version creative evidence"
            )
        if not self._claim_is_active(self.evidence, claim):
            raise ValidationError("Intent Capsule source must be currently active evidence")
        return claim

    @staticmethod
    def _normalize_facets(values: object) -> tuple[IntentFacet, ...]:
        if not isinstance(values, (tuple, list)):
            raise ValidationError("facets must be a tuple or list of IntentFacet values")
        result = tuple(values)
        if not result:
            raise ValidationError("Intent Capsule requires at least one semantic facet")
        if not all(isinstance(item, IntentFacet) for item in result):
            raise ValidationError("all facets must be IntentFacet values")
        ids = [item.facet_id for item in result]
        if len(ids) != len(set(ids)):
            raise ValidationError("Intent Capsule facet_id values must be unique")
        return result

    @staticmethod
    def _facet_payload(facet: IntentFacet) -> dict[str, object]:
        return {
            "facet_id": facet.facet_id,
            "meaning": facet.meaning,
            "required_capability": facet.required_capability,
            "preservation_goal": facet.preservation_goal,
            "fallback_policy": facet.fallback_policy,
        }

    def _source_workspace_binding(
        self,
        song_id: str,
        workspace_id: str | None,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        if workspace_id is None:
            return None, None, None, None
        normalized = self._normalize_workspace(song_id, workspace_id)
        assert normalized is not None
        state = self.workspaces.state(normalized)
        return (
            state.workspace.id,
            state.current_observation.id,
            state.current_observation.host_runtime_fingerprint,
            state.workspace.host_family,
        )

    @staticmethod
    def _new_capsule_id() -> str:
        return f"intent_{uuid.uuid4().hex}"

    def _owned_claim_ids(self) -> tuple[str, ...]:
        rows = self.store._conn.execute(
            "SELECT c.id FROM evidence_claims c "
            "WHERE c.key LIKE ? ORDER BY c.seq",
            (INTENT_CAPSULE_KEY_PREFIX + "%",),
        ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def _owned_supersession_exists(self) -> bool:
        return self.store._conn.execute(
            "SELECT 1 FROM evidence_supersessions s "
            "JOIN evidence_claims old ON old.id=s.old_claim_id "
            "JOIN evidence_claims new ON new.id=s.new_claim_id "
            "WHERE old.key LIKE ? OR new.key LIKE ? LIMIT 1",
            (
                INTENT_CAPSULE_KEY_PREFIX + "%",
                INTENT_CAPSULE_KEY_PREFIX + "%",
            ),
        ).fetchone() is not None

    def _parse(
        self,
        claim: EvidenceClaim,
        *,
        corruption: bool = False,
    ) -> IntentCapsule:
        error = LineageCorruptionError if corruption else ValidationError
        value = claim.value
        if not isinstance(value, dict) or set(value) != self._EXPECTED_PAYLOAD_FIELDS:
            raise error("Intent Capsule evidence payload shape is invalid")
        if value["schema_version"] != INTENT_CAPSULE_SCHEMA_VERSION:
            raise error("unsupported Intent Capsule schema version")
        capsule_id = value["capsule_id"]
        if not isinstance(capsule_id, str) or not capsule_id:
            raise error("Intent Capsule id is invalid")
        if claim.key != INTENT_CAPSULE_KEY_PREFIX + capsule_id:
            raise error("Intent Capsule identity/key binding is invalid")
        if claim.scope_kind != "SONG":
            raise error("Intent Capsule representation must remain Song-scoped")
        if claim.source_kind != "INFERRED" or not claim.source_ref:
            raise error(
                "Intent Capsule representation must remain inferred from artist-intent evidence"
            )
        if claim.twin_domain != "CREATIVE":
            raise error("Intent Capsule representation must remain CREATIVE evidence")

        song_id = value["song_id"]
        version_id = value["version_id"]
        source_workspace_id = value["source_workspace_id"]
        try:
            normalized_song = self._normalize_song(song_id)
            normalized_version = self._normalize_version(normalized_song, version_id)
            normalized_workspace = self._normalize_workspace(
                normalized_song,
                source_workspace_id,
            )
            intent_kind = _enum(value["intent_kind"], "intent_kind", INTENT_KINDS)
            summary = _text(value["summary"], "summary", maximum=1000)
        except (ValidationError, NotFoundError) as exc:
            raise error(str(exc)) from exc
        if claim.scope_id != normalized_song:
            raise error("Intent Capsule evidence scope does not match payload Song")
        if normalized_version != version_id or normalized_workspace != source_workspace_id:
            raise error("Intent Capsule context is not canonical")

        raw_facets = value["facets"]
        if not isinstance(raw_facets, list) or not raw_facets:
            raise error("Intent Capsule facets payload is invalid")
        facets: list[IntentFacet] = []
        try:
            for raw in raw_facets:
                if not isinstance(raw, dict) or set(raw) != self._EXPECTED_FACET_FIELDS:
                    raise ValidationError("Intent Capsule facet payload shape is invalid")
                facets.append(
                    IntentFacet(
                        facet_id=raw["facet_id"],
                        meaning=raw["meaning"],
                        required_capability=raw["required_capability"],
                        preservation_goal=raw["preservation_goal"],
                        fallback_policy=raw["fallback_policy"],
                    )
                )
            normalized_facets = self._normalize_facets(facets)
        except ValidationError as exc:
            raise error(str(exc)) from exc

        source_claim_id = value["source_claim_id"]
        try:
            source = self._require_intent_source(
                source_claim_id,
                song_id=normalized_song,
                version_id=normalized_version,
            )
        except ValidationError as exc:
            # Historical capsules remain readable when their independent source
            # was superseded. Scope/type/creative truth must still validate.
            if "currently active evidence" not in str(exc):
                raise error(str(exc)) from exc
            source = self.evidence.get_claim(str(source_claim_id))
            if source is None:
                raise error("Intent Capsule source evidence is missing")
            if source.source_kind != "USER_DECLARED" or source.twin_domain != "CREATIVE":
                raise error("Intent Capsule source truth class changed")
            if source.scope_kind == "SONG":
                if source.scope_id != normalized_song:
                    raise error("Intent Capsule source evidence crosses Song scope")
            elif source.scope_kind == "VERSION":
                if normalized_version is None or source.scope_id != normalized_version:
                    raise error("Intent Capsule source Version context is invalid")
            else:
                raise error("Intent Capsule source scope is invalid")
        except NotFoundError as exc:
            raise error(str(exc)) from exc

        if value["source_kind"] != source.source_kind:
            raise error("Intent Capsule source kind was rewritten")
        if value["source_ref"] != source.source_ref:
            raise error("Intent Capsule source provenance was rewritten")
        if value["source_twin_domain"] != source.twin_domain:
            raise error("Intent Capsule source Twin domain was rewritten")
        if claim.source_ref != source.id:
            raise error("Intent Capsule representation source binding is invalid")

        source_observation_id = value["source_workspace_observation_id"]
        source_runtime_fingerprint = value["source_runtime_fingerprint"]
        source_host_family = value["source_host_family"]
        source_environment_current = True
        if normalized_workspace is None:
            if any(
                item is not None
                for item in (
                    source_observation_id,
                    source_runtime_fingerprint,
                    source_host_family,
                )
            ):
                raise error("unbound Intent Capsule has fabricated source environment")
        else:
            if not all(
                isinstance(item, str) and item
                for item in (
                    source_observation_id,
                    source_runtime_fingerprint,
                    source_host_family,
                )
            ):
                raise error("Intent Capsule source environment binding is incomplete")
            workspace = self.workspaces.get(normalized_workspace)
            assert workspace is not None
            if source_host_family != workspace.host_family:
                raise error("Intent Capsule source host family was rewritten")
            history = self.workspaces.history(normalized_workspace)
            matches = tuple(
                item
                for item in history
                if item.id == source_observation_id
                and item.host_runtime_fingerprint == source_runtime_fingerprint
            )
            if len(matches) != 1:
                raise error("Intent Capsule source WorkspaceObservation is invalid")
            current = self.workspaces.state(normalized_workspace).current_observation
            source_environment_current = (
                current.id == source_observation_id
                and current.host_runtime_fingerprint == source_runtime_fingerprint
            )

        return IntentCapsule(
            id=capsule_id,
            profile_id=self.store.profile_id,
            artist_id=self.store.primary_artist_id,
            song_id=normalized_song,
            version_id=normalized_version,
            source_workspace_id=normalized_workspace,
            source_workspace_observation_id=source_observation_id,
            source_runtime_fingerprint=source_runtime_fingerprint,
            source_host_family=source_host_family,
            intent_kind=intent_kind,
            summary=summary,
            facets=normalized_facets,
            source_claim_id=source.id,
            source_kind=source.source_kind,
            source_ref=source.source_ref,
            source_twin_domain=source.twin_domain,
            source_current=self._claim_is_active(self.evidence, source),
            source_environment_current=source_environment_current,
            representation_claim_id=claim.id,
            representation_sequence=claim.sequence,
        )

    def _objects(self, *, corruption: bool = False) -> tuple[IntentCapsule, ...]:
        objects: list[IntentCapsule] = []
        for claim_id in self._owned_claim_ids():
            claim = self.evidence.get_claim(claim_id)
            if claim is None:
                error = LineageCorruptionError if corruption else ValidationError
                raise error("Intent Capsule representation evidence is missing")
            objects.append(self._parse(claim, corruption=corruption))
        ids = [item.id for item in objects]
        if len(ids) != len(set(ids)):
            error = LineageCorruptionError if corruption else ValidationError
            raise error("duplicate Intent Capsule identity detected")
        return tuple(objects)

    def _validate_existing(self) -> None:
        try:
            if self._owned_supersession_exists():
                raise LineageCorruptionError(
                    "Intent Capsule representations are immutable and cannot be superseded"
                )
            self._objects(corruption=True)
        except LineageCorruptionError:
            raise
        except Exception as exc:
            raise LineageCorruptionError(
                "Intent Capsule evidence is unreadable or corrupt"
            ) from exc

    def capsules_for_song(self, song_id: object) -> tuple[IntentCapsule, ...]:
        normalized = self._normalize_song(song_id)
        return tuple(item for item in self._objects() if item.song_id == normalized)

    def get(self, capsule_id: object) -> IntentCapsule | None:
        if not isinstance(capsule_id, str) or not capsule_id.strip():
            raise ValidationError("capsule_id must be non-empty text")
        normalized = capsule_id.strip()
        matches = tuple(item for item in self._objects() if item.id == normalized)
        if not matches:
            return None
        if len(matches) != 1:
            raise LineageCorruptionError("duplicate Intent Capsule identity detected")
        return matches[0]

    def create(
        self,
        *,
        song_id: object,
        intent_kind: object,
        summary: object,
        facets: object,
        source_claim_id: object,
        version_id: object | None = None,
        source_workspace_id: object | None = None,
    ) -> IntentCapsule:
        normalized_song = self._normalize_song(song_id)
        normalized_version = self._normalize_version(normalized_song, version_id)
        normalized_kind = _enum(intent_kind, "intent_kind", INTENT_KINDS)
        normalized_summary = _text(summary, "summary", maximum=1000)
        normalized_facets = self._normalize_facets(facets)
        normalized_workspace = self._normalize_workspace(
            normalized_song,
            source_workspace_id,
        )
        source = self._require_intent_source(
            source_claim_id,
            song_id=normalized_song,
            version_id=normalized_version,
        )
        (
            source_workspace,
            source_observation,
            source_runtime,
            source_host,
        ) = self._source_workspace_binding(normalized_song, normalized_workspace)

        semantic_key = (
            normalized_song,
            normalized_version,
            source_workspace,
            normalized_kind,
            normalized_summary,
            tuple(self._facet_payload(item).items() for item in normalized_facets),
            source.id,
        )
        for existing in self._objects():
            existing_key = (
                existing.song_id,
                existing.version_id,
                existing.source_workspace_id,
                existing.intent_kind,
                existing.summary,
                tuple(self._facet_payload(item).items() for item in existing.facets),
                existing.source_claim_id,
            )
            if existing_key == semantic_key:
                raise ValidationError("semantically duplicate Intent Capsule already exists")

        capsule_id = self._new_capsule_id()
        payload = {
            "schema_version": INTENT_CAPSULE_SCHEMA_VERSION,
            "capsule_id": capsule_id,
            "song_id": normalized_song,
            "version_id": normalized_version,
            "source_workspace_id": source_workspace,
            "source_workspace_observation_id": source_observation,
            "source_runtime_fingerprint": source_runtime,
            "source_host_family": source_host,
            "intent_kind": normalized_kind,
            "summary": normalized_summary,
            "facets": [self._facet_payload(item) for item in normalized_facets],
            "source_claim_id": source.id,
            "source_kind": source.source_kind,
            "source_ref": source.source_ref,
            "source_twin_domain": source.twin_domain,
        }
        representation = self.evidence.record_claim(
            scope_kind="SONG",
            scope_id=normalized_song,
            key=INTENT_CAPSULE_KEY_PREFIX + capsule_id,
            value=payload,
            source_kind="INFERRED",
            source_ref=source.id,
            confidence=source.confidence,
            twin_domain="CREATIVE",
        )
        result = self._parse(representation)
        if result.id != capsule_id:
            raise LineageCorruptionError("new Intent Capsule identity changed on read-back")
        return result

    def _workspace_descends_from(self, workspace_id: str, ancestor_id: str) -> bool:
        seen: set[str] = set()
        current_id: str | None = workspace_id
        while current_id is not None:
            if current_id in seen:
                raise LineageCorruptionError("Workspace lineage contains a cycle")
            seen.add(current_id)
            if current_id == ancestor_id:
                return True
            current = self.workspaces.get(current_id)
            if current is None:
                raise LineageCorruptionError("Workspace lineage references a missing Workspace")
            current_id = current.source_workspace_id
        return False

    @staticmethod
    def _capability_state(items) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        if not items:
            return "NO_EVIDENCE", (), ()
        states = tuple(sorted({item.availability for item in items}))
        ids = tuple(item.id for item in items)
        if "AVAILABLE" in states:
            state = "AVAILABLE"
        elif "UNKNOWN" in states:
            state = "UNKNOWN"
        else:
            state = "UNAVAILABLE"
        if state not in CAPABILITY_STATES:
            raise LineageCorruptionError("unsupported capability assessment state")
        return state, states, ids

    @staticmethod
    def _disposition(facet: IntentFacet, capability_state: str) -> str:
        if facet.preservation_goal == "REFERENCE_ONLY":
            return "REFERENCE_ONLY"
        if capability_state == "AVAILABLE":
            return "ROUTE_AVAILABLE"
        if capability_state in {"UNKNOWN", "NO_EVIDENCE"}:
            return "NEEDS_EVIDENCE"
        if capability_state != "UNAVAILABLE":
            raise LineageCorruptionError("invalid capability state for transfer disposition")
        if facet.fallback_policy == "FREEZE_WITH_RECIPE":
            return "FREEZE_RECIPE_REQUIRED"
        if facet.fallback_policy == "MANUAL_REBUILD_WITH_RECIPE":
            return "MANUAL_REBUILD_REQUIRED"
        return "BLOCKED"

    @staticmethod
    def _readiness(facets: tuple[IntentTransferFacetAssessment, ...]) -> str:
        dispositions = {item.disposition for item in facets}
        if "BLOCKED" in dispositions:
            return "BLOCKED"
        if "NEEDS_EVIDENCE" in dispositions:
            return "NEEDS_EVIDENCE"
        if dispositions & {"FREEZE_RECIPE_REQUIRED", "MANUAL_REBUILD_REQUIRED"}:
            return "FALLBACK_REQUIRED"
        if dispositions == {"REFERENCE_ONLY"}:
            return "REFERENCE_ONLY"
        return "ROUTES_IDENTIFIED"

    def assess_destination(
        self,
        capsule_id: object,
        *,
        destination_workspace_id: object,
    ) -> IntentTransferAssessment:
        capsule = self.get(capsule_id)
        if capsule is None:
            raise NotFoundError(f"Intent Capsule not found: {capsule_id}")
        if capsule.attention_state != "AVAILABLE":
            raise ValidationError(
                "Intent Capsule requires source revalidation before destination assessment"
            )
        destination_id = self._normalize_workspace(
            capsule.song_id,
            destination_workspace_id,
        )
        assert destination_id is not None
        if capsule.source_workspace_id is not None:
            if destination_id == capsule.source_workspace_id:
                raise ValidationError(
                    "destination Workspace must be distinct from the source Workspace"
                )
            if not self._workspace_descends_from(
                destination_id,
                capsule.source_workspace_id,
            ):
                raise ValidationError(
                    "destination Workspace is not derived from the capsule source Workspace"
                )
            lineage_state = "DESCENDS_FROM_SOURCE"
        else:
            lineage_state = "SOURCE_UNBOUND"

        destination = self.workspaces.state(destination_id)
        capability_state = self.capability_evidence.state(destination_id)
        facet_results: list[IntentTransferFacetAssessment] = []
        for facet in capsule.facets:
            if facet.preservation_goal == "REFERENCE_ONLY":
                state = "UNSPECIFIED"
                availability_states: tuple[str, ...] = ()
                evidence_ids: tuple[str, ...] = ()
            else:
                matches = tuple(
                    item
                    for item in capability_state.current
                    if item.capability == facet.required_capability
                )
                state, availability_states, evidence_ids = self._capability_state(matches)
            disposition = self._disposition(facet, state)
            if disposition not in TRANSFER_DISPOSITIONS:
                raise LineageCorruptionError("invalid Intent transfer disposition")
            facet_results.append(
                IntentTransferFacetAssessment(
                    facet_id=facet.facet_id,
                    meaning=facet.meaning,
                    required_capability=facet.required_capability,
                    preservation_goal=facet.preservation_goal,
                    fallback_policy=facet.fallback_policy,
                    capability_state=state,
                    availability_states=availability_states,
                    capability_evidence_ids=evidence_ids,
                    disposition=disposition,
                )
            )
        results = tuple(facet_results)
        readiness = self._readiness(results)
        if readiness not in TRANSFER_READINESS_STATES:
            raise LineageCorruptionError("invalid Intent transfer readiness state")
        return IntentTransferAssessment(
            capsule_id=capsule.id,
            song_id=capsule.song_id,
            source_workspace_id=capsule.source_workspace_id,
            destination_workspace_id=destination.workspace.id,
            destination_workspace_observation_id=destination.current_observation.id,
            destination_runtime_fingerprint=(
                destination.current_observation.host_runtime_fingerprint
            ),
            destination_host_family=destination.workspace.host_family,
            workspace_lineage_state=lineage_state,
            readiness_state=readiness,
            facets=results,
        )
