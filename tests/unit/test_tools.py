from pathlib import Path

import pytest

from enterprise_agent.tools.calculate import calculate
from enterprise_agent.tools.web.access_policy import WebAccessDenied, validate_public_url
from enterprise_agent.tools.workspace import Workspace, WorkspaceDenied


def test_calculate_is_deterministic_and_rejects_zero_denominator():
    assert calculate("mean", [2, 4]) == 3
    assert calculate("growth_rate", [10, 15]) == 0.5
    with pytest.raises(ValueError, match="zero"):
        calculate("growth_rate", [1], denominator=0)


def test_workspace_rejects_traversal_and_symlink(tmp_path: Path):
    workspace = Workspace(tmp_path / "tenant" / "run")
    with pytest.raises(WorkspaceDenied):
        workspace.read("../../secret")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (workspace.root / "link").symlink_to(outside)
    with pytest.raises(WorkspaceDenied):
        workspace.read("link")


def test_web_policy_blocks_private_and_metadata_addresses():
    for url in ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest"]:
        with pytest.raises(WebAccessDenied):
            validate_public_url(url)

