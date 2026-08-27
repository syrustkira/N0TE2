from pathlib import Path
import runpy


_APP_TESTS = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "app" / "test_safe_update.py")
)


def test_safe_update_success_reason_is_visible_during_systemic_repair(tmp_path: Path) -> None:
    values = _APP_TESTS["prepared_update"](tmp_path)
    data_root, _, _, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    result = _APP_TESTS["apply"](
        coordinator,
        plan,
        data_root,
        manifest,
        payload,
        target,
        auth,
        _APP_TESTS["Driver"](),
        proc,
        probe,
    )
    assert result.state == "SUCCEEDED", result.reason
