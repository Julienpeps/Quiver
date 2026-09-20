from pathlib import Path

import pytest

from quiver.core.workspaces import (
    WorkspaceError,
    assessment_workspace,
    config_path,
    initialize_workspace,
    load_workspace_config,
)
from quiver.models.config import default_assessment_config


def test_initialize_workspace_creates_owned_directories(tmp_path: Path) -> None:
    config = default_assessment_config("demo", tmp_path)

    workspace = initialize_workspace(config)

    assert workspace == assessment_workspace("demo", tmp_path)
    assert config_path(workspace).is_file()
    for directory in (".logs/sessions", ".logs/exec", ".logs/services", ".vpn", ".services"):
        assert (workspace / directory).is_dir()
    assert load_workspace_config("demo", tmp_path) == config


def test_initialize_workspace_refuses_to_overwrite_config(tmp_path: Path) -> None:
    config = default_assessment_config("demo", tmp_path)
    initialize_workspace(config)

    with pytest.raises(WorkspaceError, match="already exists"):
        initialize_workspace(config)


def test_load_workspace_config_fails_for_absent_assessment(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceError, match="does not exist"):
        load_workspace_config("missing", tmp_path)
