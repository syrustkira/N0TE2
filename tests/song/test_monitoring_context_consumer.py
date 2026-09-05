from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from n0te2.consumer_shell import ConsumerShell
from n0te2.instance import ProcessIdentity
from n0te2.memory import HeadquartersMemory
from n0te2.monitoring_context import MonitoringContextService
from n0te2.platforms import PlatformEnvironment


class Probe:
    def status(self, process: ProcessIdentity) -> str:
        return "UNKNOWN"


def process(pid: int, token: str) -> ProcessIdentity:
    return ProcessIdentity.from_start_token(
        PlatformEnvironment.from_runtime_labels("Linux", "x86_64"),
        pid=pid,
        start_token=token,
    )


def shell_for(
    data_root: Path,
    state_root: Path,
    *,
    pid: int,
    token: str,
) -> ConsumerShell:
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(pid, token),
        probe=Probe(),
    )
    shell.start()
    return shell


def get(shell: ConsumerShell, path: str) -> tuple[int, str]:
    request = Request(shell.address.origin + path, method="GET")
    try:
        with build_opener().open(request, timeout=3.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def post(
    shell: ConsumerShell,
    path: str,
    fields: dict[str, str],
    *,
    origin: str | None = None,
    host: str | None = None,
) -> tuple[int, str]:
    payload = urlencode(fields).encode("utf-8")
    request = Request(shell.address.origin + path, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if origin is not None:
        request.add_header("Origin", origin)
    if host is not None:
        request.add_header("Host", host)
    try:
        with build_opener().open(request, timeout=3.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def monitoring_action(page: str, label: str) -> str:
    match = re.search(
        r'<form[^>]+action="/monitoring/context".*?'
        r'name="action" value="([^"]+)">'
        + re.escape(label)
        + r"</button>",
        page,
        flags=re.DOTALL,
    )
    assert match is not None
    return html_lib.unescape(match.group(1))


def seed_song(data_root: Path) -> dict[str, str]:
    hq = HeadquartersMemory.create(data_root, "Monitoring Consumer")
    try:
        song = hq.store.create_song("Translation Room")
        version = hq.store.create_version(
            song.id,
            label="Listening pass",
            make_current=True,
        )
        return {
            "profile_id": hq.store.profile_id,
            "artist_id": hq.store.primary_artist_id,
            "song_id": song.id,
            "version_id": version.id,
        }
    finally:
        hq.close()


def test_song_page_exposes_read_only_monitoring_context_without_identity_leaks_or_fake_source_options(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seeded = seed_song(data_root)

    shell = shell_for(
        data_root,
        state_root,
        pid=12401,
        token="monitoring-read",
    )
    try:
        status, page = get(shell, "/song")
        assert status == 200
        assert page.count("<h2>Listening Context</h2>") == 1
        assert "Not represented yet" in page
        assert "Monitoring path" in page
        assert "Listening environment" in page
        assert "Reference level" in page
        assert "Calibration context" in page
        assert "Listener position" in page
        assert "Translation check" in page
        assert "records only what you tell N0TE" in page
        assert "measurement, calibration certificate" in page
        assert "value=\"OBSERVED\"" not in page
        assert "value=\"MEASURED\"" not in page
        assert "value=\"PROVIDER_VERIFIED\"" not in page
        for private in seeded.values():
            assert private not in page
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, seeded["profile_id"])
    try:
        for key in (
            "monitoring.output_path",
            "monitoring.listening_environment",
            "monitoring.reference_level",
            "monitoring.calibration",
            "monitoring.listener_position",
            "monitoring.translation_check",
        ):
            assert reopened.evidence.active_claims(
                "VERSION",
                seeded["version_id"],
                key,
            ) == ()
    finally:
        reopened.close()


def test_exact_version_declaration_revises_only_prior_artist_declaration_and_survives_relaunch(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seeded = seed_song(data_root)

    shell = shell_for(
        data_root,
        state_root,
        pid=12402,
        token="monitoring-revise",
    )
    try:
        _, page = get(shell, "/song")
        first = monitoring_action(page, "Monitoring path")
        status, first_page = post(
            shell,
            "/monitoring/context",
            {
                "csrf": shell._csrf,
                "action": first,
                "value": "Interface outputs 1-2 & desk speakers",
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert "updated as your declaration for this exact Version" in first_page
        assert "Interface outputs 1-2 &amp; desk speakers" in first_page
        assert "You told N0TE" in first_page
        assert "This exact Version" in first_page

        second = monitoring_action(first_page, "Monitoring path")
        status, revised_page = post(
            shell,
            "/monitoring/context",
            {
                "csrf": shell._csrf,
                "action": second,
                "value": "Headphones <main pair>",
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Headphones &lt;main pair&gt;" in revised_page
        assert "<main pair>" not in revised_page
        assert "Interface outputs 1-2 &amp; desk speakers" not in revised_page
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, seeded["profile_id"])
    try:
        claims = reopened.evidence.active_claims(
            "VERSION",
            seeded["version_id"],
            "monitoring.output_path",
        )
        assert len(claims) == 1
        assert claims[0].value == "Headphones <main pair>"
        assert claims[0].source_kind == "USER_DECLARED"
        assert claims[0].source_ref is None
        assert claims[0].twin_domain == "TECHNICAL"
    finally:
        reopened.close()

    relaunched = shell_for(
        data_root,
        state_root,
        pid=12403,
        token="monitoring-relaunch",
    )
    try:
        status, page = get(relaunched, "/song")
        assert status == 200
        assert "Headphones &lt;main pair&gt;" in page
        assert "You told N0TE" in page
    finally:
        relaunched.stop()


def test_measured_evidence_stays_distinct_and_artist_form_cannot_overwrite_it(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seeded = seed_song(data_root)
    source_ref = "calibration-report:private-source-17"

    hq = HeadquartersMemory.open(data_root, seeded["profile_id"])
    try:
        measured = MonitoringContextService(hq.store, hq.evidence).record_fact(
            scope_kind="VERSION",
            scope_id=seeded["version_id"],
            key="monitoring.calibration",
            value="Measured reference alignment",
            source_kind="MEASURED",
            source_ref=source_ref,
        )
        measured_id = measured.id
    finally:
        hq.close()

    shell = shell_for(
        data_root,
        state_root,
        pid=12404,
        token="monitoring-conflict",
    )
    try:
        _, page = get(shell, "/song")
        assert "Measured reference alignment" in page
        assert "Measured" in page
        assert source_ref not in page
        assert measured_id not in page

        token = monitoring_action(page, "Calibration context")
        status, conflict_page = post(
            shell,
            "/monitoring/context",
            {
                "csrf": shell._csrf,
                "action": token,
                "value": "I am not certain this setup is calibrated",
            },
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Conflicting evidence" in conflict_page
        assert "Measured reference alignment" in conflict_page
        assert "I am not certain this setup is calibrated" in conflict_page
        assert "Measured" in conflict_page
        assert "You told N0TE" in conflict_page
        assert source_ref not in conflict_page
        assert measured_id not in conflict_page
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, seeded["profile_id"])
    try:
        claims = reopened.evidence.active_claims(
            "VERSION",
            seeded["version_id"],
            "monitoring.calibration",
        )
        assert {claim.source_kind for claim in claims} == {
            "MEASURED",
            "USER_DECLARED",
        }
        assert any(claim.id == measured_id for claim in claims)
    finally:
        reopened.close()


def test_stale_context_security_and_replay_fail_closed_before_duplicate_evidence(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seeded = seed_song(data_root)

    shell = shell_for(
        data_root,
        state_root,
        pid=12405,
        token="monitoring-auth",
    )
    try:
        _, page = get(shell, "/song")
        token = monitoring_action(page, "Listening environment")
        fields = {
            "csrf": shell._csrf,
            "action": token,
            "value": "Small treated room",
        }

        status, _ = post(
            shell,
            "/monitoring/context",
            fields,
            origin="https://example.invalid",
        )
        assert status == 403

        status, _ = post(
            shell,
            "/monitoring/context",
            fields,
            origin=shell.address.origin,
            host="example.invalid",
        )
        assert status == 403

        status, result = post(
            shell,
            "/monitoring/context",
            fields,
            origin=shell.address.origin,
        )
        assert status == 200
        assert "Small treated room" in result

        status, replay = post(
            shell,
            "/monitoring/context",
            fields,
            origin=shell.address.origin,
        )
        assert status == 409
        assert "already handled or expired" in replay

        _, fresh_page = get(shell, "/song")
        stale_token = monitoring_action(fresh_page, "Reference level")
        external = HeadquartersMemory.open(data_root, seeded["profile_id"])
        try:
            MonitoringContextService(external.store, external.evidence).record_fact(
                scope_kind="VERSION",
                scope_id=seeded["version_id"],
                key="monitoring.listener_position",
                value="Chair moved 20 cm forward",
                source_kind="USER_DECLARED",
            )
        finally:
            external.close()

        status, stale = post(
            shell,
            "/monitoring/context",
            {
                "csrf": shell._csrf,
                "action": stale_token,
                "value": "Quiet conversational level",
            },
            origin=shell.address.origin,
        )
        assert status == 409
        assert "listening context changed" in stale
    finally:
        shell.stop()

    reopened = HeadquartersMemory.open(data_root, seeded["profile_id"])
    try:
        environment = reopened.evidence.active_claims(
            "VERSION",
            seeded["version_id"],
            "monitoring.listening_environment",
        )
        assert len(environment) == 1
        assert environment[0].value == "Small treated room"
        assert reopened.evidence.active_claims(
            "VERSION",
            seeded["version_id"],
            "monitoring.reference_level",
        ) == ()
    finally:
        reopened.close()
