import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from n0te2.consumer_shell import ConsumerShell
from n0te2.instance import ProcessIdentity
from n0te2.memory import HeadquartersMemory
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


def new_shell(data_root: Path, state_root: Path, pid: int, token: str) -> ConsumerShell:
    shell = ConsumerShell(
        data_root=data_root,
        state_root=state_root,
        process=process(pid, token),
        probe=Probe(),
    )
    shell.start()
    return shell


def get(shell: ConsumerShell, path: str) -> tuple[int, str]:
    req = Request(shell.address.origin + path, method="GET")
    try:
        with build_opener().open(req, timeout=3.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def post(
    shell: ConsumerShell,
    path: str,
    fields: dict[str, str],
    *,
    origin: str | None = None,
) -> tuple[int, str]:
    payload = urlencode(fields).encode("utf-8")
    req = Request(shell.address.origin + path, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Origin", shell.address.origin if origin is None else origin)
    try:
        with build_opener().open(req, timeout=3.0) as response:
            return response.status, response.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def action_for_path(page: str, path: str) -> str:
    match = re.search(
        rf'<form[^>]+action="{re.escape(path)}".*?name="action" value="([^"]+)"',
        page,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def action_for_label(page: str, label: str) -> str:
    match = re.search(
        rf'<form[^>]+aria-label="{re.escape(label)}".*?name="action" value="([^"]+)"',
        page,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def suggestion_title(page: str) -> str:
    match = re.search(
        r'<h3>One prompt to try</h3>.*?<p><strong>([^<]+)</strong></p>',
        page,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def suggestion_status(page: str) -> tuple[str, str]:
    match = re.search(
        r'<h3>One prompt to try</h3>.*?'
        r'<p class="status good">([^<·]+) · ([^<]+)</p>',
        page,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group(1).strip(), match.group(2).strip()


def seed_song(data_root: Path) -> str:
    hq = HeadquartersMemory.create(data_root, "Controlled Randomize Consumer")
    try:
        song = hq.store.create_song("Glass Signal")
        hq.sessions.start_session(
            song_id=song.id,
            objective="Explore another direction without loosening the brief",
        )
        return hq.store.profile_id
    finally:
        hq.close()


def request_suggestion(
    shell: ConsumerShell,
    *,
    distance: str = "ADJACENT",
    locks: tuple[str, ...] = (),
) -> str:
    _, page = get(shell, "/song")
    fields = {
        "csrf": shell._csrf,
        "action": action_for_path(page, "/suggestion/create"),
        "distance": distance,
    }
    for dimension in locks:
        fields["lock_" + dimension.lower()] = "1"
    status, result = post(shell, "/suggestion/create", fields)
    assert status == 200
    assert "One prompt to try" in result
    return result


def test_randomize_preserves_distance_and_server_held_locks_despite_forged_fields(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id = seed_song(data_root)

    shell = new_shell(data_root, state_root, 15101, "randomize-locked")
    try:
        first = request_suggestion(
            shell,
            distance="ADJACENT",
            locks=("MELODY",),
        )
        first_title = suggestion_title(first)
        first_distance, first_dimension = suggestion_status(first)
        assert first_distance == "Adjacent"
        assert first_dimension != "Melody"
        assert "Try another direction" in first
        assert "Current locks stay fixed: Melody." in first

        token = action_for_label(first, "Try another controlled suggestion")
        status, foreign = post(
            shell,
            "/suggestion/randomize",
            {"csrf": shell._csrf, "action": token},
            origin="https://example.invalid",
        )
        assert status == 403
        assert "did not come from this N0TE window" in foreign

        status, randomized = post(
            shell,
            "/suggestion/randomize",
            {
                "csrf": shell._csrf,
                "action": token,
                "distance": "WILDCARD",
                "variation": "999",
                "lock_melody": "0",
                "lock_arrangement": "1",
            },
        )
        assert status == 200
        assert "Another bounded local direction is ready" in randomized
        assert "distance and locks were preserved" in randomized
        randomized_distance, randomized_dimension = suggestion_status(randomized)
        assert randomized_distance == "Adjacent"
        assert randomized_dimension != "Melody"
        assert suggestion_title(randomized) != first_title
        assert "Current locks stay fixed: Melody." in randomized
        assert "Generated locally and deterministically" in randomized
        assert "No AI provider was called" in randomized

        status, replay = post(
            shell,
            "/suggestion/randomize",
            {"csrf": shell._csrf, "action": token},
        )
        assert status == 409
        assert "already handled or expired" in replay
    finally:
        shell.stop()

    hq = HeadquartersMemory.open(data_root, profile_id)
    try:
        assert hq.suggestion_feedback.history() == ()
        assert hq.suggestion_deferrals.history() == ()
    finally:
        hq.close()

    relaunched = new_shell(data_root, state_root, 15102, "randomize-relaunch")
    try:
        status, page = get(relaunched, "/song")
        assert status == 200
        assert "One prompt to try" not in page
        assert "Try another direction" not in page
    finally:
        relaunched.stop()


def test_randomize_truthfully_preserves_current_prompt_when_no_distinct_option_exists(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)

    shell = new_shell(data_root, state_root, 15103, "randomize-one-role")
    try:
        first = request_suggestion(
            shell,
            distance="FAMILIAR",
            locks=("ARRANGEMENT", "RHYTHM", "HARMONY", "MELODY", "DYNAMICS"),
        )
        assert suggestion_status(first) == ("Familiar", "Sound")
        first_title = suggestion_title(first)
        token = action_for_label(first, "Try another controlled suggestion")

        status, unavailable = post(
            shell,
            "/suggestion/randomize",
            {"csrf": shell._csrf, "action": token},
        )
        assert status == 200
        assert "No different local suggestion is currently available with this distance and these locks" in unavailable
        assert suggestion_title(unavailable) == first_title
        assert suggestion_status(unavailable) == ("Familiar", "Sound")
        assert "Try another direction" in unavailable
    finally:
        shell.stop()


def test_randomize_token_fails_closed_after_current_suggestion_changes(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    seed_song(data_root)

    shell = new_shell(data_root, state_root, 15104, "randomize-stale")
    try:
        first = request_suggestion(shell, distance="ADJACENT")
        stale_token = action_for_label(first, "Try another controlled suggestion")

        replacement = request_suggestion(shell, distance="WILDCARD")
        replacement_title = suggestion_title(replacement)
        assert suggestion_status(replacement)[0] == "Wildcard"

        status, stale = post(
            shell,
            "/suggestion/randomize",
            {"csrf": shell._csrf, "action": stale_token},
        )
        assert status == 409
        assert (
            "already handled or expired" in stale
            or "current suggestion changed" in stale
        )

        _, current = get(shell, "/song")
        assert suggestion_title(current) == replacement_title
        assert suggestion_status(current)[0] == "Wildcard"
    finally:
        shell.stop()


def test_randomized_result_remains_valid_for_existing_more_less_feedback(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id = seed_song(data_root)

    shell = new_shell(data_root, state_root, 15105, "randomize-feedback")
    try:
        first = request_suggestion(shell, distance="ADJACENT")
        status, randomized = post(
            shell,
            "/suggestion/randomize",
            {
                "csrf": shell._csrf,
                "action": action_for_label(first, "Try another controlled suggestion"),
            },
        )
        assert status == 200
        randomized_title = suggestion_title(randomized)
        assert "More like this" in randomized

        status, remembered = post(
            shell,
            "/suggestion/feedback",
            {
                "csrf": shell._csrf,
                "action": action_for_label(randomized, "More like this suggestion"),
            },
        )
        assert status == 200
        assert "More like this remembered as context" in remembered
        assert suggestion_title(remembered) == randomized_title
    finally:
        shell.stop()

    hq = HeadquartersMemory.open(data_root, profile_id)
    try:
        history = hq.suggestion_feedback.history()
        assert len(history) == 1
        assert history[0].direction == "MORE"
        assert history[0].automatic_weighting_applied is False
        assert history[0].external_action_authorized is False
    finally:
        hq.close()


def test_randomize_respects_existing_never_suggest_again_suppression(
    tmp_path: Path,
) -> None:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    profile_id = seed_song(data_root)

    shell = new_shell(data_root, state_root, 15106, "randomize-deferral")
    try:
        sound_only = request_suggestion(
            shell,
            distance="ADJACENT",
            locks=("ARRANGEMENT", "RHYTHM", "HARMONY", "MELODY", "DYNAMICS"),
        )
        assert suggestion_status(sound_only)[1] == "Sound"
        suppressed_title = suggestion_title(sound_only)

        status, deferred = post(
            shell,
            "/suggestion/not-now",
            {
                "csrf": shell._csrf,
                "action": action_for_label(
                    sound_only,
                    "Suppress this exact suggestion pattern across this Artist profile",
                ),
            },
        )
        assert status == 200
        assert "Never suggest again remembered" in deferred
        assert "One prompt to try" not in deferred

        current = request_suggestion(shell, distance="ADJACENT")
        assert suggestion_title(current) != suppressed_title
        status, randomized = post(
            shell,
            "/suggestion/randomize",
            {
                "csrf": shell._csrf,
                "action": action_for_label(current, "Try another controlled suggestion"),
            },
        )
        assert status == 200
        assert suggestion_title(randomized) != suppressed_title
    finally:
        shell.stop()

    hq = HeadquartersMemory.open(data_root, profile_id)
    try:
        history = hq.suggestion_deferrals.history()
        assert len(history) == 1
        assert history[0].scope == "NEVER_SUGGEST_AGAIN"
    finally:
        hq.close()
