from pathlib import Path

import pytest
from pydantic import ValidationError

from quiver.models.config import (
    AssessmentConfig,
    ConfigError,
    default_assessment_config,
    load_config,
    write_config,
)


def test_default_config_uses_resolved_workspace_path(tmp_path: Path) -> None:
    config = default_assessment_config("acme-2026", tmp_path)

    assert config.name == "acme-2026"
    assert config.image.profile == "base"
    assert config.workspace.path == str(tmp_path.resolve() / "workspaces" / "acme-2026")


@pytest.mark.parametrize("name", ["../escape", "with/slash", "-invalid", "invalid-"])
def test_config_rejects_unsafe_assessment_names(name: str) -> None:
    with pytest.raises(ValidationError):
        default_assessment_config(name, Path("/tmp/quiver"))


def test_config_round_trip_preserves_nested_values(tmp_path: Path) -> None:
    config = AssessmentConfig.model_validate(
        {
            "schema_version": 1,
            "name": "acme-2026",
            "image": {"profile": "internal"},
            "workspace": {"path": str(tmp_path / "workspace")},
            "docker": {
                "capabilities": {"add": ["NET_ADMIN"], "drop": ["NET_RAW"]},
                "ports": [{"container_port": 6080}],
            },
            "vpn": {"enabled": True, "type": "openvpn", "config": ".vpn/client.ovpn"},
        }
    )
    path = tmp_path / ".quiver.yaml"

    write_config(path, config)

    assert load_config(path) == config
    assert path.stat().st_mode & 0o777 == 0o600


def test_config_rejects_unknown_top_level_field(tmp_path: Path) -> None:
    path = tmp_path / ".quiver.yaml"
    path.write_text("schema_version: 1\nname: demo\nunknown: true\n")

    with pytest.raises(ConfigError, match="unknown"):
        load_config(path)


def test_enabled_vpn_is_incompatible_with_none_network(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="VPN cannot"):
        AssessmentConfig.model_validate(
            {
                "schema_version": 1,
                "name": "demo",
                "image": {"profile": "base"},
                "workspace": {"path": str(tmp_path / "workspace")},
                "docker": {"network": {"mode": "none"}},
                "vpn": {"enabled": True, "config": ".vpn/client.ovpn"},
            }
        )


def test_config_migrates_older_schema_with_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from quiver.models import config as config_module

    legacy = tmp_path / ".quiver.yaml"
    legacy.write_text(
        "schema_version: 1\nname: demo\nimage: {profile: base}\n"
        + f"workspace: {{path: {str(tmp_path / 'workspace')!r}}}\n"
    )

    def bump(old: dict) -> dict:
        data = dict(old)
        data["schema_version"] = 2
        return data

    monkeypatch.setattr(config_module, "SCHEMA_VERSION", 2)
    monkeypatch.setitem(config_module.MIGRATIONS._steps, 1, bump)

    migrated = load_config(legacy)

    assert migrated.schema_version == 2
    backups = list(tmp_path.glob(".quiver.yaml.bak-*"))
    assert len(backups) == 1
    assert "schema_version: 1" in backups[0].read_text()


def test_config_rejects_newer_schema_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from quiver.models import config as config_module

    path = tmp_path / ".quiver.yaml"
    path.write_text("schema_version: 99\nname: demo\n")
    monkeypatch.setattr(config_module, "SCHEMA_VERSION", 2)

    with pytest.raises(ConfigError, match="newer than"):
        load_config(path)
