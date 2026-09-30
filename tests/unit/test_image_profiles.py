from pathlib import Path

from quiver.core.images import load_package_report

ROOT = Path(__file__).parents[2]


def test_each_profile_has_a_data_manifest_and_build_definition() -> None:
    for profile in ("web", "internal", "external", "cloud", "full"):
        report = load_package_report(profile, "linux/amd64")
        assert report.required
        dockerfile = (ROOT / "images" / "profiles" / profile / "Dockerfile").read_text()
        assert "ARG REQUIRED_PACKAGES" in dockerfile
        assert "pacman -Sy --noconfirm --needed $REQUIRED_PACKAGES" in dockerfile
        assert "Skipping unavailable optional package" in dockerfile


def test_base_dockerfiles_bootstrap_without_caller_supplied_inputs() -> None:
    amd64 = (ROOT / "images" / "base" / "Dockerfile.amd64").read_text()
    arm64 = (ROOT / "images" / "base" / "Dockerfile.arm64").read_text()

    assert "https://blackarch.org/strap.sh" in amd64
    assert "DisableSandbox" in amd64
    assert "BLACKARCH_STRAP_" not in amd64
    assert "FROM alpine:3.21 AS archlinuxarm-rootfs" in arm64
    assert "ArchLinuxARM-aarch64-latest.tar.gz" in arm64
    assert "--mount=type=cache,target=/downloads" in arm64
    assert "--continue-at -" in arm64
    assert "DisableSandbox" in arm64
    assert "pacman -Sy --noconfirm --needed" in arm64
    assert "pacman -Syu" not in arm64
    assert "rm -rf /rootfs/usr/lib/firmware /rootfs/usr/lib/modules" in arm64
    assert "ARCHLINUXARM_ROOTFS_" not in arm64
    assert "blackarch.org/strap.sh" not in arm64
    assert "python -m venv /opt/quiver-websockify" in arm64
    assert "/opt/quiver-websockify/bin/pip install --no-cache-dir websockify" in arm64
    assert "--break-system-packages" not in arm64
    assert "https://github.com/novnc/noVNC.git" in arm64
    assert "https://github.com/WireGuard/wireguard-go.git" in arm64
    assert "COPY images/base/rootfs" not in arm64
    for dockerfile in (amd64, arm64):
        for package in ("eza", "fish", "neovim", "starship", "sudo", "tmux", "zoxide"):
            assert package in dockerfile
        assert "quiver-audit-hook.fish" in dockerfile
        assert "ENV SHELL=/usr/bin/fish" in dockerfile


def test_entrypoint_creates_the_host_mapped_sudo_user() -> None:
    entrypoint = (ROOT / "images" / "common" / "entrypoint" / "quiver-entrypoint").read_text()

    assert "QUIVER_HOST_UID" in entrypoint
    assert "useradd --no-create-home" in entrypoint
    assert "NOPASSWD: ALL" in entrypoint


def test_workflows_pin_actions_and_build_without_provenance_inputs() -> None:
    test_workflow = (ROOT / ".github" / "workflows" / "test-cli.yml").read_text()
    build_workflow = (ROOT / ".github" / "workflows" / "build-images.yml").read_text()
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in test_workflow
    assert "BLACKARCH_STRAP_" not in build_workflow
    assert "ARCHLINUXARM_ROOTFS_" not in build_workflow
    assert "REQUIRED_PACKAGES=$required" in build_workflow
    assert "OPTIONAL_PACKAGES=$optional" in build_workflow
    assert "provenance-gate" not in release_workflow
    assert "docker buildx imagetools create" in release_workflow
