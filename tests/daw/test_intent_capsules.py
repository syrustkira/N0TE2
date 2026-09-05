from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from n0te2.hosts import HostRuntimeIdentity
from n0te2.intent_capsules import IntentCapsuleService, IntentFacet
from n0te2.lineage import ValidationError
from n0te2.memory import HeadquartersMemory


def _service(hq: HeadquartersMemory) -> IntentCapsuleService:
    return IntentCapsuleService(
        hq.store,
        hq.evidence,
        hq.workspaces,
        hq.capability_evidence,
    )


def _runtime(
    family: str = "ABLETON_LIVE",
    *,
    version: str = "12.1",
    edition: str = "Standard",
    os_name: str = "Darwin",
    machine: str = "arm64",
) -> HostRuntimeIdentity:
    return HostRuntimeIdentity.from_runtime_labels(
        host_family=family,
        version=version,
        edition=edition,
        os_name=os_name,
        machine=machine,
    )


def _editable(
    facet_id: str = "chorus.width",
    *,
    capability: str = "mix.stereo-width",
    fallback: str = "FREEZE_WITH_RECIPE",
) -> IntentFacet:
    return IntentFacet(
        facet_id=facet_id,
        meaning=f"Preserve {facet_id} as an editable musical choice",
        required_capability=capability,
        preservation_goal="EDITABLE",
        fallback_policy=fallback,
    )


def _source(
    hq: HeadquartersMemory,
    *,
    song_id: str,
    facets: tuple[IntentFacet, ...],
    key: str = "intent.semantic.chorus-width",
    summary: str = "Keep the chorus bloom wide without losing vocal focus.",
    intent_kind: str = "SOUND_CHARACTER",
    version_id: str | None = None,
    supersedes: tuple[str, ...] = (),
):
    value = IntentCapsuleService.source_value(
        intent_kind=intent_kind,
        summary=summary,
        facets=facets,
    )
    return hq.evidence.record_claim(
        scope_kind="VERSION" if version_id is not None else "SONG",
        scope_id=version_id if version_id is not None else song_id,
        key=key,
        value=value,
        source_kind="USER_DECLARED",
        twin_domain="CREATIVE",
        supersedes=supersedes,
    )


def _workspace(
    hq: HeadquartersMemory,
    song_id: str,
    *,
    family: str,
    location: str,
):
    return hq.workspaces.create(
        song_id,
        runtime=_runtime(family),
        location_ref=location,
    )


def _derived(
    hq: HeadquartersMemory,
    source_workspace_id: str,
    song_id: str,
    *,
    family: str,
    location: str,
):
    return hq.workspaces.derive(
        source_workspace_id,
        song_id=song_id,
        relation="FORK",
        runtime=_runtime(family),
        location_ref=location,
    )


def _capability(
    hq: HeadquartersMemory,
    workspace_id: str,
    *,
    route_id: str,
    capability: str = "mix.stereo-width",
    availability: str,
    observed_at: int,
):
    state = hq.workspaces.state(workspace_id)
    return hq.capability_evidence.record(
        workspace_id,
        expected_workspace_observation_id=state.current_observation.id,
        expected_host_runtime_fingerprint=state.current_observation.host_runtime_fingerprint,
        route_id=route_id,
        route_kind="HOST_NATIVE",
        capability=capability,
        display_name=route_id,
        availability=availability,
        evidence_kind="ADAPTER_TEST",
        evidence_ref=None if availability == "UNKNOWN" else f"test:{route_id}",
        observed_at_epoch_seconds=observed_at,
        task_fit=0.5,
        editability=0.5,
        locality=1.0,
        privacy=1.0,
        latency=0.5,
        reversibility=0.5,
        cost_efficiency=1.0,
        portability=0.5,
        paid=False,
    )


def test_capsule_semantics_are_owned_by_canonical_source_payload(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Intent Artist")
    try:
        song = hq.store.create_song("Single")
        facets = (_editable(),)
        source = _source(hq, song_id=song.id, facets=facets)
        capsule = _service(hq).create(
            song_id=song.id,
            source_claim_id=source.id,
        )

        assert capsule.song_id == song.id
        assert capsule.intent_kind == "SOUND_CHARACTER"
        assert capsule.summary == "Keep the chorus bloom wide without losing vocal focus."
        assert capsule.facets == facets
        assert capsule.source_claim_id == source.id
        assert capsule.authority == "EVIDENCE_ONLY"
        assert capsule.transfer_verified is False
        assert capsule.mutation_authorized is False
        assert capsule.external_action_authority_granted is False
    finally:
        hq.close()


def test_plain_or_malformed_user_declared_evidence_cannot_mint_semantic_capsule(
    tmp_path: Path,
) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Truth Artist")
    try:
        song = hq.store.create_song("Song")
        bad = hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=song.id,
            key="intent.artist.statement",
            value="keep it wide",
            source_kind="USER_DECLARED",
            twin_domain="CREATIVE",
        )
        with pytest.raises(ValidationError, match="source key"):
            _service(hq).create(song_id=song.id, source_claim_id=bad.id)
    finally:
        hq.close()


def test_conflicting_active_source_beliefs_fail_closed(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Conflict Artist")
    try:
        song = hq.store.create_song("Song")
        facets = (_editable(),)
        first = _source(hq, song_id=song.id, facets=facets)
        second = hq.evidence.record_claim(
            scope_kind="SONG",
            scope_id=song.id,
            key=first.key,
            value=IntentCapsuleService.source_value(
                intent_kind="SOUND_CHARACTER",
                summary="Keep the chorus narrower and intimate.",
                facets=facets,
            ),
            source_kind="USER_DECLARED",
            twin_domain="CREATIVE",
        )
        assert first.id != second.id
        with pytest.raises(ValidationError, match="currently active evidence"):
            _service(hq).create(song_id=song.id, source_claim_id=first.id)
        with pytest.raises(ValidationError, match="currently active evidence"):
            _service(hq).create(song_id=song.id, source_claim_id=second.id)
    finally:
        hq.close()


def test_version_scoped_source_overrides_song_source_in_full_context(
    tmp_path: Path,
) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Version Truth Artist")
    try:
        song = hq.store.create_song("Song")
        version = hq.store.create_version(song.id, label="v1")
        facets = (_editable(),)
        song_source = _source(hq, song_id=song.id, facets=facets)
        service = _service(hq)
        original = service.create(
            song_id=song.id,
            version_id=version.id,
            source_claim_id=song_source.id,
        )
        assert original.attention_state == "AVAILABLE"

        version_source = _source(
            hq,
            song_id=song.id,
            version_id=version.id,
            key=song_source.key,
            facets=facets,
            summary="Keep this Version narrow and centered.",
        )
        assert version_source.scope_kind == "VERSION"

        stale = service.get(original.id)
        assert stale is not None
        assert stale.source_current is False
        assert stale.attention_state == "NEEDS_REVALIDATION"

        with pytest.raises(ValidationError, match="full Song/Version context"):
            service.create(
                song_id=song.id,
                version_id=version.id,
                source_claim_id=song_source.id,
            )
    finally:
        hq.close()


def test_facet_order_is_not_semantic_identity(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Order Artist")
    try:
        song = hq.store.create_song("Song")
        a = _editable("chorus.width", capability="mix.stereo-width")
        b = _editable("chorus.motion", capability="automation.edit")
        source = _source(hq, song_id=song.id, facets=(a, b))
        service = _service(hq)
        capsule = service.create(song_id=song.id, source_claim_id=source.id)
        assert tuple(item.facet_id for item in capsule.facets) == (
            "chorus.motion",
            "chorus.width",
        )

        same_semantics = _source(
            hq,
            song_id=song.id,
            facets=(b, a),
            key="intent.semantic.same-meaning-second-key",
        )
        with pytest.raises(ValidationError, match="semantically duplicate"):
            service.create(song_id=song.id, source_claim_id=same_semantics.id)
    finally:
        hq.close()


def test_semantic_duplicate_creation_is_atomic_across_connections(
    tmp_path: Path,
) -> None:
    root = (tmp_path / "data").resolve()
    hq = HeadquartersMemory.create(root, "Atomic Artist")
    try:
        song = hq.store.create_song("Song")
        facets = (_editable(),)
        source = _source(hq, song_id=song.id, facets=facets)
        profile_id = hq.store.profile_id
        barrier = Barrier(2)

        def create_from_independent_connection() -> str:
            with HeadquartersMemory.open(root, profile_id) as worker:
                barrier.wait()
                return _service(worker).create(
                    song_id=song.id,
                    source_claim_id=source.id,
                ).id

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(create_from_independent_connection)
                for _ in range(2)
            ]
            successes: list[str] = []
            failures: list[Exception] = []
            for future in futures:
                try:
                    successes.append(future.result())
                except Exception as exc:
                    failures.append(exc)

        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], ValidationError)
        assert "semantically duplicate" in str(failures[0])
        assert len(_service(hq).capsules_for_song(song.id)) == 1
    finally:
        hq.close()


def test_append_only_revalidation_recaptures_source_workspace_without_rewriting_history(
    tmp_path: Path,
) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Revalidation Artist")
    try:
        song = hq.store.create_song("Song")
        facets = (_editable(),)
        source = _source(hq, song_id=song.id, facets=facets)
        workspace = _workspace(
            hq,
            song.id,
            family="ABLETON_LIVE",
            location="file:///song/old",
        )
        service = _service(hq)
        original = service.create(
            song_id=song.id,
            source_claim_id=source.id,
            source_workspace_id=workspace.id,
        )
        old_observation = original.source_workspace_observation_id

        hq.workspaces.reconcile_existing(
            workspace.id,
            song_id=song.id,
            relation="SAME_OR_MOVED",
            runtime=_runtime("ABLETON_LIVE", version="12.2"),
            location_ref="file:///song/moved",
        )
        stale = service.get(original.id)
        assert stale is not None
        assert stale.attention_state == "NEEDS_REVALIDATION"

        refreshed = service.revalidate(original.id)
        assert refreshed.id != original.id
        assert refreshed.source_workspace_id == workspace.id
        assert refreshed.source_workspace_observation_id != old_observation
        assert refreshed.attention_state == "AVAILABLE"

        historical = service.get(original.id)
        assert historical is not None
        assert historical.source_workspace_observation_id == old_observation
        assert historical.attention_state == "NEEDS_REVALIDATION"
    finally:
        hq.close()


def test_revalidation_can_bind_semantically_identical_superseding_source(
    tmp_path: Path,
) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Source Artist")
    try:
        song = hq.store.create_song("Song")
        facets = (_editable(),)
        source = _source(hq, song_id=song.id, facets=facets)
        service = _service(hq)
        original = service.create(song_id=song.id, source_claim_id=source.id)

        replacement = _source(
            hq,
            song_id=song.id,
            facets=facets,
            supersedes=(source.id,),
        )
        stale = service.get(original.id)
        assert stale is not None
        assert stale.source_current is False

        refreshed = service.revalidate(
            original.id,
            source_claim_id=replacement.id,
        )
        assert refreshed.source_claim_id == replacement.id
        assert refreshed.attention_state == "AVAILABLE"
        assert refreshed.id != original.id
    finally:
        hq.close()


def test_revalidation_cannot_switch_to_different_semantic_source_key(
    tmp_path: Path,
) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Key Artist")
    try:
        song = hq.store.create_song("Song")
        facets = (_editable(),)
        source = _source(hq, song_id=song.id, facets=facets)
        service = _service(hq)
        original = service.create(song_id=song.id, source_claim_id=source.id)

        _source(
            hq,
            song_id=song.id,
            facets=facets,
            summary="The artist changed the original intent.",
            supersedes=(source.id,),
        )
        unrelated = _source(
            hq,
            song_id=song.id,
            facets=facets,
            key="intent.semantic.unrelated-but-identical",
        )
        stale = service.get(original.id)
        assert stale is not None
        assert stale.attention_state == "NEEDS_REVALIDATION"

        with pytest.raises(ValidationError, match="existing source key"):
            service.revalidate(
                original.id,
                source_claim_id=unrelated.id,
            )
    finally:
        hq.close()


def test_conflicting_destination_capability_routes_require_evidence(
    tmp_path: Path,
) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Route Artist")
    try:
        song = hq.store.create_song("Song")
        facets = (_editable(),)
        source = _source(hq, song_id=song.id, facets=facets)
        source_ws = _workspace(
            hq,
            song.id,
            family="ABLETON_LIVE",
            location="file:///ableton",
        )
        destination = _derived(
            hq,
            source_ws.id,
            song.id,
            family="LOGIC_PRO",
            location="file:///logic",
        )
        _capability(
            hq,
            destination.id,
            route_id="logic-width-a",
            availability="AVAILABLE",
            observed_at=100,
        )
        _capability(
            hq,
            destination.id,
            route_id="logic-width-b",
            availability="UNAVAILABLE",
            observed_at=101,
        )
        capsule = _service(hq).create(
            song_id=song.id,
            source_claim_id=source.id,
            source_workspace_id=source_ws.id,
        )

        assessment = _service(hq).assess_destination(
            capsule.id,
            destination_workspace_id=destination.id,
        )
        facet = assessment.facets[0]
        assert facet.availability_states == ("AVAILABLE", "UNAVAILABLE")
        assert facet.capability_state == "UNKNOWN"
        assert facet.disposition == "NEEDS_EVIDENCE"
        assert assessment.readiness_state == "NEEDS_EVIDENCE"
        assert assessment.transfer_verified is False
    finally:
        hq.close()


def test_destination_workspace_change_during_assessment_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hq = HeadquartersMemory.create((tmp_path / "data").resolve(), "Race Artist")
    try:
        song = hq.store.create_song("Song")
        facets = (_editable(),)
        source = _source(hq, song_id=song.id, facets=facets)
        source_ws = _workspace(
            hq,
            song.id,
            family="ABLETON_LIVE",
            location="file:///ableton",
        )
        destination = _derived(
            hq,
            source_ws.id,
            song.id,
            family="LOGIC_PRO",
            location="file:///logic",
        )
        _capability(
            hq,
            destination.id,
            route_id="logic-width",
            availability="AVAILABLE",
            observed_at=100,
        )
        service = _service(hq)
        capsule = service.create(
            song_id=song.id,
            source_claim_id=source.id,
            source_workspace_id=source_ws.id,
        )

        original_state = hq.capability_evidence.state
        calls = {"count": 0}

        def racing_state(workspace_id: str):
            calls["count"] += 1
            if calls["count"] == 2:
                hq.workspaces.reconcile_existing(
                    destination.id,
                    song_id=song.id,
                    relation="SAME_OR_MOVED",
                    runtime=_runtime("LOGIC_PRO", version="12.2"),
                    location_ref="file:///logic-moved",
                )
            return original_state(workspace_id)

        monkeypatch.setattr(hq.capability_evidence, "state", racing_state)
        with pytest.raises(
            ValidationError,
            match="changed during assessment|mixed Workspace/runtime",
        ):
            service.assess_destination(
                capsule.id,
                destination_workspace_id=destination.id,
            )
    finally:
        hq.close()
