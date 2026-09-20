from pathlib import Path

import pytest
from pydantic import ValidationError

from quiver.core.editing import materialize_vpn_files
from quiver.core.lifecycle import LifecycleError, LifecycleManager
from quiver.docker.backend import CommandResult, DockerBackend
from quiver.models.config import AssessmentConfig, default_assessment_config


def test_vpn_material_is_copied_into_workspace_and_persisted_relatively(tmp_path: Path) -> None:
    source = tmp_path / "outside.ovpn"
    credentials = tmp_path / "outside-auth.txt"
    source.write_text("client\nremote vpn.example.test\n")
    credentials.write_text("user\npass\n")
    workspace_root = tmp_path / "state"
    config = default_assessment_config("demo", workspace_root)

    materialized = materialize_vpn_files(config, source, credentials)
    workspace = Path(materialized.workspace.path)

    assert materialized.vpn.config == ".vpn/client.ovpn"
    assert materialized.vpn.credentials_file == ".vpn/auth.txt"
    assert (workspace / materialized.vpn.config).read_text() == source.read_text()
    assert (workspace / materialized.vpn.credentials_file).read_text() == credentials.read_text()
    assert (workspace / materialized.vpn.credentials_file).stat().st_mode & 0o777 == 0o600


def test_vpn_host_network_configuration_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="network.mode=host"):
        AssessmentConfig.model_validate(
            {
                "schema_version": 1,
                "name": "demo",
                "image": {"profile": "base"},
                "workspace": {"path": str(tmp_path)},
                "docker": {"network": {"mode": "host"}},
                "vpn": {"enabled": True, "config": ".vpn/client.ovpn"},
            }
        )


class PreflightDocker(DockerBackend):
    def __init__(self, version: dict[str, object], context: object) -> None:
        self.version_data = version
        self.context_data = context

    def version(self) -> dict[str, object]:
        return self.version_data

    def json(self, *args: str) -> object:
        assert args == ("context", "inspect", "--format", "{{json .}}")
        return self.context_data

    def run(self, *args: str, **kwargs: object) -> CommandResult:
        raise AssertionError(f"Docker mutation should not run during preflight: {args}")


@pytest.mark.parametrize(
    ("version", "context", "message"),
    [
        ({"Server": {"Os": "windows"}}, {}, "Linux containers"),
        (
            {"Server": {"Os": "linux"}},
            {"Endpoints": {"docker": {"Host": "ssh://docker.example.test"}}},
            "remote Docker contexts",
        ),
    ],
)
def test_start_preflight_rejects_unsafe_daemons_before_mutation(
    tmp_path: Path, version: dict[str, object], context: object, message: str
) -> None:
    config = default_assessment_config("demo", tmp_path)
    Path(config.workspace.path).mkdir(parents=True)

    with pytest.raises(LifecycleError, match=message):
        LifecycleManager(PreflightDocker(version, context)).start(config)


def test_image_scripts_wire_audit_hook_and_asciinema() -> None:
    repository = Path(__file__).parents[2]
    shell = (repository / "images/common/shell/quiver-record-shell").read_text()
    gui = (repository / "images/common/gui/quiver-gui").read_text()
    for dockerfile in ("Dockerfile.amd64", "Dockerfile.arm64"):
        contents = (repository / "images/base" / dockerfile).read_text()
        assert "asciinema" in contents
        assert "/etc/zsh/zshrc" in contents
    assert "ZDOTDIR=$session_dir" in shell
    assert "asciinema cat" in shell
    assert "output.txt" in shell
    assert "quiver-record-shell" in gui
