from pathlib import Path

import pytest

from quiver.core.editing import (
    ConfigEditError,
    apply_settings,
    apply_start_options,
    has_start_options,
    parse_port_mapping,
    replace_config,
)
from quiver.models.config import default_assessment_config, load_config, write_config


def test_apply_settings_updates_nested_value_after_validation(tmp_path: Path) -> None:
    config = default_assessment_config("demo", tmp_path)

    edited = apply_settings(
        config,
        ["docker.privileged=true", "docker.network.mode=host", "gui.host_port=6080"],
    )

    assert edited.docker.privileged is True
    assert edited.docker.network.mode == "host"
    assert edited.gui.host_port == 6080


def test_apply_settings_rejects_unknown_or_non_mapping_paths(tmp_path: Path) -> None:
    config = default_assessment_config("demo", tmp_path)

    with pytest.raises(ConfigEditError, match="invalid configuration edit"):
        apply_settings(config, ["docker.unknown=true"])
    with pytest.raises(ConfigEditError, match="does not address a mapping"):
        apply_settings(config, ["gui.enabled.value=true"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("8443:443", ("127.0.0.1", 8443, 443, "tcp")),
        ("127.0.0.1:5353:53/udp", ("127.0.0.1", 5353, 53, "udp")),
    ],
)
def test_parse_port_mapping(value: str, expected: tuple[str, int, int, str]) -> None:
    port = parse_port_mapping(value)

    assert (port.host_ip, port.host_port, port.container_port, port.protocol) == expected


def test_reconfigure_options_update_persisted_configuration(tmp_path: Path) -> None:
    config = default_assessment_config("demo", tmp_path)
    edited = apply_start_options(
        config,
        image="ghcr.io/example/quiver:stable",
        platform="linux/arm64",
        vpn=".vpn/client.ovpn",
        vpn_type="openvpn",
        network="custom",
        network_name="assessment-net",
        publish=["8081:8081"],
        services=["bloodhound-ce"],
        gui=False,
    )
    path = tmp_path / ".quiver.yaml"
    write_config(path, config)

    replace_config(path, edited)

    loaded = load_config(path)
    assert loaded.image.reference == "ghcr.io/example/quiver:stable"
    assert loaded.image.profile is None
    assert loaded.docker.network.name == "assessment-net"
    assert loaded.docker.ports[0].host_port == 8081
    assert loaded.services.autostart == ["bloodhound-ce"]
    assert loaded.gui.enabled is False


def test_has_start_options_ignores_empty_values() -> None:
    assert has_start_options(image=None, publish=[], gui=None) is False
    assert has_start_options(image="base", publish=[]) is True
