from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_amd64_image_installs_and_exposes_runtime_contract() -> None:
    dockerfile = (ROOT / "images/base/Dockerfile.amd64").read_text()

    assert "FROM archlinux:base-devel" in dockerfile
    assert "blackarch-keyring" in dockerfile
    assert "quiver-entrypoint" in dockerfile
    assert "quiver-record-shell" in dockerfile
    assert "quiver-vpn" in dockerfile
    assert "quiver-gui" in dockerfile
    assert "images/common/gui/assets/quiver-background.png" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/quiver-entrypoint"]' in dockerfile


def test_container_scripts_are_safe_and_supervisor_is_configured() -> None:
    scripts = (
        "images/common/entrypoint/quiver-entrypoint",
        "images/common/shell/quiver-record-shell",
        "images/common/supervisor/quiver-vpn",
        "images/common/gui/quiver-gui",
    )
    for script in scripts:
        content = (ROOT / script).read_text()
        assert content.startswith("#!/usr/bin/env bash\nset -eu\n")

    entrypoint = (ROOT / scripts[0]).read_text()
    assert "mountpoint -q" in entrypoint
    assert "/run/quiver/config.yaml" in entrypoint
    assert "supervisord --nodaemon" in entrypoint
    supervisor = (ROOT / "images/common/supervisor/supervisord.conf").read_text()
    assert "[unix_http_server]" in supervisor
    assert "[rpcinterface:supervisor]" in supervisor
    assert "supervisor.rpcinterface:make_main_rpcinterface" in supervisor
    assert "/etc/supervisor/conf.d/*.conf" in supervisor


def test_base_manifest_keeps_runtime_dependencies_as_data() -> None:
    manifest = (ROOT / "packages/base.yaml").read_text()

    for package in ("firefox", "novnc", "openvpn", "wireguard-tools", "tigervnc", "websockify", "supervisor"):
        assert f"  - {package}" in manifest
