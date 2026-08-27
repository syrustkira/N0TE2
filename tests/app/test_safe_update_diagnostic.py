from pathlib import Path

from tests.app.test_safe_update import Driver, apply, prepared_update


def test_success_path_reports_exact_recovery_reason(tmp_path: Path) -> None:
    values = prepared_update(tmp_path)
    data_root, _, _, proc, probe, coordinator, plan, target, _, manifest, auth, payload, _, _ = values
    result = apply(
        coordinator,
        plan,
        data_root,
        manifest,
        payload,
        target,
        auth,
        Driver(),
        proc,
        probe,
    )
    assert result.state == "SUCCEEDED", result.reason
