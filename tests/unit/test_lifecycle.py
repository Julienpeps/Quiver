from pathlib import Path

import pytest

from quiver.core.lifecycle import (
    LifecycleManager,
    assessment_network_name,
    image_reference,
    primary_container_name,
)
from quiver.docker.backend import DockerBackend
from quiver.errors import VpnError
from quiver.models.config import AssessmentConfig


def test_resource_names_and_profile_image_are_deterministic() -> None:
    config = AssessmentConfig.model_validate(
        {
            "schema_version": 1,
            "name": "demo",
            "image": {"profile": "internal"},
            "workspace": {"path": "/tmp/demo"},
        }
    )

    assert primary_container_name("demo") == "quiver-demo"
    assert assessment_network_name("demo") == "quiver-demo"
    assert image_reference(config) == "quiver-internal:stable"


def test_create_arguments_include_labels_mounts_ports_and_vpn_permissions(
    tmp_path: Path,
) -> None:
    config = AssessmentConfig.model_validate(
        {
            "schema_version": 1,
            "name": "demo",
            "image": {"reference": "example/quiver:stable", "platform": "linux/arm64"},
            "workspace": {"path": str(tmp_path)},
            "docker": {
                "ports": [{"container_port": 8080}],
                "capabilities": {"add": ["NET_RAW"]},
            },
            "vpn": {"enabled": True, "config": ".vpn/client.ovpn"},
        }
    )

    arguments = LifecycleManager(DockerBackend())._create_args(
        config, "example/quiver:stable", tmp_path / "runtime.yaml"
    )

    assert "io.quiver.managed=true" in arguments
    assert "type=bind,src=" + str(tmp_path) + ",dst=/workspace" in arguments
    assert "127.0.0.1::8080/tcp" in arguments
    assert "NET_RAW" in arguments
    assert "NET_ADMIN" in arguments
    assert "/dev/net/tun" in arguments
    assert "linux/arm64" in arguments
    assert "127.0.0.1::6080/tcp" in arguments
    assert assessment_network_name("demo") in arguments


def test_lifecycle_rejects_wireguard_profile_without_default_route(tmp_path: Path) -> None:
    profile = tmp_path / "client.conf"
    profile.write_text(
        "[Interface]\nPrivateKey = ignored\n[Peer]\nEndpoint = 198.51.100.10:51820\nAllowedIPs = 10.0.0.0/8\n"
    )
    config = AssessmentConfig.model_validate(
        {
            "schema_version": 1,
            "name": "demo",
            "image": {"profile": "base"},
            "workspace": {"path": str(tmp_path)},
            "vpn": {"enabled": True, "type": "wireguard", "config": str(profile)},
        }
    )

    with pytest.raises(VpnError, match="does not route"):
        LifecycleManager(DockerBackend())._validate_vpn(config)
