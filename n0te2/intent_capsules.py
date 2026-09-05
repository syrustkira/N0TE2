from __future__ import annotations

import sqlite3
from dataclasses import replace

from ._intent_capsules_core import (
    CAPABILITY_STATES,
    FALLBACK_POLICIES,
    INTENT_CAPSULE_AUTHORITY,
    INTENT_CAPSULE_KEY_PREFIX,
    INTENT_CAPSULE_SCHEMA_VERSION,
    INTENT_KINDS,
    PRESERVATION_GOALS,
    TRANSFER_DISPOSITIONS,
    TRANSFER_READINESS_STATES,
    IntentCapsule,
    IntentCapsuleService as _CoreIntentCapsuleService,
    IntentFacet,
    IntentTransferAssessment,
    IntentTransferFacetAssessment,
)
from .evidence import EvidenceClaim, EvidenceMemory
from .lineage import LineageCorruptionError, NotFoundError, ValidationError

INTENT_SOURCE_SCHEMA_VERSION = 1
INTENT_SOURCE_KEY_PREFIX = "intent.semantic."
_SOURCE_FIELDS = {"schema_version", "intent_kind", "summary", "facets"}


def _canonical_source_key(value: object) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ValidationError("Intent Capsule source key must be canonical text")
    if not value.startswith(INTENT_SOURCE_KEY_PREFIX):
        raise ValidationError(
            f"Intent Capsule source key must use {INTENT_SOURCE_KEY_PREFIX!r} namespace"
        )
    suffix = value[len(INTENT_SOURCE_KEY_PREFIX) :]
    if not suffix or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789._:-" for ch in suffix):
        raise ValidationError("Intent Capsule source key suffix is not canonical")
    return value


class IntentCapsuleService(_CoreIntentCapsuleService):
    """Hardened source-bound semantic continuity above the verified v1 core.

    The private core owns the already-proven immutable representation and
    qualitative transfer model. This public layer makes the artist's semantic
    source canonical, conflict-aware and order-independent, adds append-only
    revalidation, and rejects destination snapshots that change while read.
    """

    @staticmethod
    def _normalize_facets(values: object) -> tuple[IntentFacet, ...]:
        facets = _CoreIntentCapsuleService._normalize_facets(values)
        return tuple(sorted(facets, key=lambda item: item.facet_id))

    @classmethod
    def source_value(
        cls,
        *,
        intent_kind: object,
        summary: object,
        facets: object,
    ) -> dict[str, object]:
        normalized_kind = cls._normalize_intent_kind(intent_kind)
        normalized_summary = cls._normalize_summary(summary)
        normalized_facets = cls._normalize_facets(facets)
        return {
            "schema_version": INTENT_SOURCE_SCHEMA_VERSION,
            "intent_kind": normalized_kind,
            "summary": normalized_summary,
            "facets": [cls._facet_payload(item) for item in normalized_facets],
        }

    @staticmethod
    def _normalize_intent_kind(value: object) -> str:
        if not isinstance(value, str):
            raise ValidationError("intent_kind must be text")
        normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
        if normalized not in INTENT_KINDS:
            raise ValidationError(f"unsupported intent_kind: {normalized}")
        return normalized

    @staticmethod
    def _normalize_summary(value: object) -> str:
        if not isinstance(value, str):
            raise ValidationError("summary must be text")
        normalized = " ".join(value.split())
        if not normalized:
            raise ValidationError("summary must not be empty")
        if len(normalized) > 1000:
            raise ValidationError("summary is too long")
        return normalized

    @classmethod
    def _source_semantics(
        cls,
        claim: EvidenceClaim,
        *,
        error_type: type[Exception] = ValidationError,
    ) -> tuple[str, str, tuple[IntentFacet, ...]]:
        try:
            _canonical_source_key(claim.key)
            value = claim.value
            if not isinstance(value, dict) or set(value) != _SOURCE_FIELDS:
                raise ValidationError("Intent Capsule source evidence payload shape is invalid")
            if value["schema_version"] != INTENT_SOURCE_SCHEMA_VERSION:
                raise ValidationError("unsupported Intent Capsule source schema version")
            facets_raw = value["facets"]
            if not isinstance(facets_raw, list) or not facets_raw:
                raise ValidationError("Intent Capsule source facets payload is invalid")
            facets: list[IntentFacet] = []
            expected = {
                "facet_id",
                "meaning",
                "required_capability",
                "preservation_goal",
                "fallback_policy",
            }
            for row in facets_raw:
                if not isinstance(row, dict) or set(row) != expected:
                    raise ValidationError("Intent Capsule source facet payload shape is invalid")
                facets.append(
                    IntentFacet(
                        facet_id=row["facet_id"],
                        meaning=row["meaning"],
                        required_capability=row["required_capability"],
                        preservation_goal=row["preservation_goal"],
                        fallback_policy=row["fallback_policy"],
                    )
                )
            normalized = (
                cls._normalize_intent_kind(value["intent_kind"]),
                cls._normalize_summary(value["summary"]),
                cls._normalize_facets(facets),
            )
            canonical = cls.source_value(
                intent_kind=normalized[0],
                summary=normalized[1],
                facets=normalized[2],
            )
            if value != canonical:
                raise ValidationError("Intent Capsule source evidence is not canonical")
            return normalized
        except ValidationError as exc:
            raise error_type(str(exc)) from exc

    @staticmethod
    def _claim_is_active(evidence: EvidenceMemory, claim: EvidenceClaim) -> bool:
        active = evidence.active_claims(claim.scope_kind, claim.scope_id, claim.key)
        return len(active) == 1 and active[0].id == claim.id

    def _source_is_current(
        self,
        claim: EvidenceClaim,
        *,
        song_id: str,
        version_id: str | None,
    ) -> bool:
        resolution = self.evidence.resolve_for_song(
            song_id=song_id,
            key=claim.key,
            version_id=version_id,
        )
        return (
            resolution.status == "RESOLVED"
            and resolution.scope_kind == claim.scope_kind
            and resolution.scope_id == claim.scope_id
            and len(resolution.claims) == 1
            and resolution.claims[0].id == claim.id
        )

    def _require_intent_source(
        self,
        claim_id: object,
        *,
        song_id: str,
        version_id: str | None,
    ) -> EvidenceClaim:
        claim = super()._require_intent_source(
            claim_id,
            song_id=song_id,
            version_id=version_id,
        )
        self._source_semantics(claim)
        if not self._source_is_current(
            claim,
            song_id=song_id,
            version_id=version_id,
        ):
            raise ValidationError(
                "Intent Capsule source must be currently active evidence "
                "for the full Song/Version context"
            )
        return claim

    def _parse(
        self,
        claim: EvidenceClaim,
        *,
        corruption: bool = False,
    ) -> IntentCapsule:
        capsule = super()._parse(claim, corruption=corruption)
        error = LineageCorruptionError if corruption else ValidationError
        source = self.evidence.get_claim(capsule.source_claim_id)
        if source is None:
            raise error("Intent Capsule source evidence is missing")
        kind, summary, facets = self._source_semantics(source, error_type=error)
        if (kind, summary, facets) != (
            capsule.intent_kind,
            capsule.summary,
            capsule.facets,
        ):
            raise error("Intent Capsule semantics diverge from source intent evidence")
        return replace(
            capsule,
            source_current=self._source_is_current(
                source,
                song_id=capsule.song_id,
                version_id=capsule.version_id,
            ),
        )

    @staticmethod
    def _semantic_identity(capsule: IntentCapsule) -> tuple[object, ...]:
        return (
            capsule.song_id,
            capsule.version_id,
            capsule.source_workspace_id,
            capsule.intent_kind,
            capsule.summary,
            tuple(
                (
                    facet.facet_id,
                    facet.meaning,
                    facet.required_capability,
                    facet.preservation_goal,
                    facet.fallback_policy,
                )
                for facet in capsule.facets
            ),
        )

    def _record_representation_locked(
        self,
        *,
        song_id: str,
        capsule_id: str,
        payload: dict[str, object],
        source: EvidenceClaim,
    ) -> str:
        if not self.store._conn.in_transaction:
            raise LineageCorruptionError(
                "Intent Capsule representation insert requires an active transaction"
            )
        representation_claim_id = self.evidence._new_claim_id()
        self.store._conn.execute(
            "INSERT INTO evidence_claims("
            "id, scope_kind, scope_id, key, value_json, source_kind, "
            "source_ref, confidence, twin_domain"
            ") VALUES(?, 'SONG', ?, ?, ?, 'INFERRED', ?, ?, 'CREATIVE')",
            (
                representation_claim_id,
                song_id,
                INTENT_CAPSULE_KEY_PREFIX + capsule_id,
                self.evidence._canonical_value(payload),
                source.id,
                source.confidence,
            ),
        )
        return representation_claim_id

    def create(
        self,
        *,
        song_id: object,
        source_claim_id: object,
        version_id: object | None = None,
        source_workspace_id: object | None = None,
    ) -> IntentCapsule:
        normalized_song = self._normalize_song(song_id)
        normalized_version = self._normalize_version(normalized_song, version_id)
        normalized_workspace = self._normalize_workspace(
            normalized_song,
            source_workspace_id,
        )
        representation_claim_id: str
        capsule_id: str
        try:
            with self.store._tx():
                source = self._require_intent_source(
                    source_claim_id,
                    song_id=normalized_song,
                    version_id=normalized_version,
                )
                kind, summary, facets = self._source_semantics(source)
                (
                    source_workspace,
                    source_observation,
                    source_runtime,
                    source_host,
                ) = self._source_workspace_binding(
                    normalized_song,
                    normalized_workspace,
                )
                candidate = (
                    normalized_song,
                    normalized_version,
                    source_workspace,
                    kind,
                    summary,
                    tuple(
                        (
                            facet.facet_id,
                            facet.meaning,
                            facet.required_capability,
                            facet.preservation_goal,
                            facet.fallback_policy,
                        )
                        for facet in facets
                    ),
                )
                if any(
                    self._semantic_identity(item) == candidate
                    for item in self._objects()
                ):
                    raise ValidationError(
                        "semantically duplicate Intent Capsule already exists; "
                        "use revalidate()"
                    )

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
                    "intent_kind": kind,
                    "summary": summary,
                    "facets": [self._facet_payload(item) for item in facets],
                    "source_claim_id": source.id,
                    "source_kind": source.source_kind,
                    "source_ref": source.source_ref,
                    "source_twin_domain": source.twin_domain,
                }
                representation_claim_id = self._record_representation_locked(
                    song_id=normalized_song,
                    capsule_id=capsule_id,
                    payload=payload,
                    source=source,
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(
                f"invalid Intent Capsule evidence mutation: {exc}"
            ) from exc

        representation = self.evidence.get_claim(representation_claim_id)
        if representation is None:
            raise LineageCorruptionError(
                "new Intent Capsule representation disappeared after commit"
            )
        result = self._parse(representation)
        if result.id != capsule_id:
            raise LineageCorruptionError("new Intent Capsule identity changed on read-back")
        return result

    def revalidate(
        self,
        capsule_id: object,
        *,
        source_claim_id: object | None = None,
    ) -> IntentCapsule:
        existing = self.get(capsule_id)
        if existing is None:
            raise NotFoundError(f"Intent Capsule not found: {capsule_id}")
        selected = existing.source_claim_id if source_claim_id is None else source_claim_id
        representation_claim_id: str
        capsule_id_new: str
        try:
            with self.store._tx():
                locked_existing = self.get(existing.id)
                if locked_existing is None:
                    raise LineageCorruptionError(
                        "Intent Capsule disappeared during revalidation"
                    )
                original_source = self.evidence.get_claim(
                    locked_existing.source_claim_id
                )
                if original_source is None:
                    raise LineageCorruptionError(
                        "Intent Capsule source evidence is missing"
                    )
                source = self._require_intent_source(
                    selected,
                    song_id=locked_existing.song_id,
                    version_id=locked_existing.version_id,
                )
                if source.key != original_source.key:
                    raise ValidationError(
                        "revalidation source must preserve the existing source key"
                    )
                kind, summary, facets = self._source_semantics(source)
                if (kind, summary, facets) != (
                    locked_existing.intent_kind,
                    locked_existing.summary,
                    locked_existing.facets,
                ):
                    raise ValidationError(
                        "revalidation source must preserve the existing capsule semantics"
                    )
                if locked_existing.attention_state == "AVAILABLE":
                    raise ValidationError("Intent Capsule is already current")

                source_workspace, source_observation, source_runtime, source_host = (
                    self._source_workspace_binding(
                        locked_existing.song_id,
                        locked_existing.source_workspace_id,
                    )
                )
                for item in self._objects():
                    if (
                        self._semantic_identity(item)
                        == self._semantic_identity(locked_existing)
                        and item.source_claim_id == source.id
                        and item.source_workspace_observation_id == source_observation
                        and item.source_runtime_fingerprint == source_runtime
                        and item.attention_state == "AVAILABLE"
                    ):
                        raise ValidationError(
                            "Intent Capsule already has a current revalidation"
                        )

                capsule_id_new = self._new_capsule_id()
                payload = {
                    "schema_version": INTENT_CAPSULE_SCHEMA_VERSION,
                    "capsule_id": capsule_id_new,
                    "song_id": locked_existing.song_id,
                    "version_id": locked_existing.version_id,
                    "source_workspace_id": source_workspace,
                    "source_workspace_observation_id": source_observation,
                    "source_runtime_fingerprint": source_runtime,
                    "source_host_family": source_host,
                    "intent_kind": kind,
                    "summary": summary,
                    "facets": [self._facet_payload(item) for item in facets],
                    "source_claim_id": source.id,
                    "source_kind": source.source_kind,
                    "source_ref": source.source_ref,
                    "source_twin_domain": source.twin_domain,
                }
                representation_claim_id = self._record_representation_locked(
                    song_id=locked_existing.song_id,
                    capsule_id=capsule_id_new,
                    payload=payload,
                    source=source,
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(
                f"invalid Intent Capsule evidence mutation: {exc}"
            ) from exc

        representation = self.evidence.get_claim(representation_claim_id)
        if representation is None:
            raise LineageCorruptionError(
                "new Intent Capsule representation disappeared after commit"
            )
        result = self._parse(representation)
        if result.id != capsule_id_new:
            raise LineageCorruptionError("new Intent Capsule identity changed on read-back")
        return result

    @staticmethod
    def _capability_state(items) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        if not items:
            return "NO_EVIDENCE", (), ()
        states = tuple(sorted({item.availability for item in items}))
        ids = tuple(item.id for item in items)
        if len(states) > 1:
            state = "UNKNOWN"
        elif states == ("AVAILABLE",):
            state = "AVAILABLE"
        elif states == ("UNKNOWN",):
            state = "UNKNOWN"
        elif states == ("UNAVAILABLE",):
            state = "UNAVAILABLE"
        else:
            raise LineageCorruptionError("unsupported capability assessment state")
        return state, states, ids

    @staticmethod
    def _snapshot_key(state) -> tuple[object, ...]:
        return (
            state.environment_id,
            state.stale_count,
            tuple(item.id for item in state.current),
        )

    def assess_destination(
        self,
        capsule_id: object,
        *,
        destination_workspace_id: object,
    ) -> IntentTransferAssessment:
        capsule = self.get(capsule_id)
        if capsule is None:
            raise NotFoundError(f"Intent Capsule not found: {capsule_id}")
        destination_id = self._normalize_workspace(
            capsule.song_id,
            destination_workspace_id,
        )
        assert destination_id is not None
        before = self.capability_evidence.state(destination_id)
        result = super().assess_destination(
            capsule.id,
            destination_workspace_id=destination_id,
        )
        after = self.capability_evidence.state(destination_id)
        if self._snapshot_key(before) != self._snapshot_key(after):
            raise ValidationError(
                "destination Workspace capability state changed during assessment; retry"
            )
        current_capsule = self.get(capsule.id)
        if current_capsule is None or current_capsule.attention_state != "AVAILABLE":
            raise ValidationError(
                "Intent Capsule source changed during destination assessment; revalidate"
            )
        if (
            result.destination_workspace_observation_id != before.workspace_observation_id
            or result.destination_runtime_fingerprint != before.host_runtime_fingerprint
            or result.destination_workspace_id != before.workspace_id
        ):
            raise ValidationError(
                "destination assessment mixed Workspace/runtime capability snapshots"
            )
        return result


__all__ = [
    "CAPABILITY_STATES",
    "FALLBACK_POLICIES",
    "INTENT_CAPSULE_AUTHORITY",
    "INTENT_CAPSULE_KEY_PREFIX",
    "INTENT_CAPSULE_SCHEMA_VERSION",
    "INTENT_KINDS",
    "INTENT_SOURCE_KEY_PREFIX",
    "INTENT_SOURCE_SCHEMA_VERSION",
    "PRESERVATION_GOALS",
    "TRANSFER_DISPOSITIONS",
    "TRANSFER_READINESS_STATES",
    "IntentCapsule",
    "IntentCapsuleService",
    "IntentFacet",
    "IntentTransferAssessment",
    "IntentTransferFacetAssessment",
]
