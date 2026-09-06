from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_integrity_workflow_binds_audit_to_exact_pull_request_head():
    text = (ROOT / '.github' / 'workflows' / 'integrity-auditor.yml').read_text(encoding='utf-8')
    expression = '${{ github.event.pull_request.head.sha || github.sha }}'
    assert f'ref: {expression}' in text
    assert f'EXPECTED_HEAD: {expression}' in text
    assert f'N0TE2_HEAD_SHA: {expression}' in text
    assert 'git rev-parse HEAD' in text
