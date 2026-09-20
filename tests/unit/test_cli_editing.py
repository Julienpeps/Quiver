from pathlib import Path

from typer.testing import CliRunner

from quiver.cli import app
from quiver.core.lifecycle import AssessmentStatus
from quiver.core.workspaces import initialize_workspace
from quiver.models.config import (
    AssessmentConfig,
    default_assessment_config,
    load_config,
)


def test_start_rejects_creation_options_for_existing_assessment(tmp_path: Path) -> None:
    initialize_workspace(default_assessment_config("demo", tmp_path))

    result = CliRunner().invoke(
        app,
        ["--root", str(tmp_path), "start", "demo", "--image", "web", "--detach"],
    )

    assert result.exit_code == 2
    assert "use edit or --reconfigure" in result.output


def test_start_copies_vpn_material_into_new_workspace(tmp_path: Path, monkeypatch: object) -> None:
    profile = tmp_path / "source.ovpn"
    credentials = tmp_path / "source-auth.txt"
    profile.write_text("client\nremote vpn.example.test\n")
    credentials.write_text("user\npass\n")

    def fake_start(_self: object, config: AssessmentConfig) -> AssessmentStatus:
        workspace = Path(config.workspace.path)
        return AssessmentStatus("demo", "quiver-demo", True, "running", "quiver-base:stable", workspace)

    monkeypatch.setattr("quiver.cli.LifecycleManager.start", fake_start)
    result = CliRunner().invoke(
        app,
        [
            "--root",
            str(tmp_path / "state"),
            "start",
            "demo",
            "--vpn",
            str(profile),
            "--vpn-credentials",
            str(credentials),
            "--detach",
        ],
    )

    assert result.exit_code == 0
    config = load_config(tmp_path / "state" / "workspaces" / "demo" / ".quiver.yaml")
    assert config.vpn.config == ".vpn/client.ovpn"
    assert config.vpn.credentials_file == ".vpn/auth.txt"


def test_edit_set_persists_validated_configuration(tmp_path: Path) -> None:
    initialize_workspace(default_assessment_config("demo", tmp_path))

    result = CliRunner().invoke(
        app,
        ["--root", str(tmp_path), "edit", "demo", "--set", "docker.privileged=true"],
    )

    assert result.exit_code == 0
    assert load_config(tmp_path / "workspaces" / "demo" / ".quiver.yaml").docker.privileged
