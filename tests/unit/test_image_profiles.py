from pathlib import Path

from quiver.core.images import load_package_report

ROOT = Path(__file__).parents[2]


def test_each_profile_has_a_data_manifest_and_build_definition() -> None:
    for profile in ("web", "internal", "full"):
        report = load_package_report(profile, "linux/amd64")
        assert report.required
        dockerfile = (ROOT / "images" / "profiles" / profile / "Dockerfile").read_text()
        assert "ARG REQUIRED_PACKAGES" in dockerfile
        assert "Skipping unavailable optional package" in dockerfile


def test_base_dockerfiles_bootstrap_without_caller_supplied_inputs() -> None:
    amd64 = (ROOT / "images" / "base" / "Dockerfile.amd64").read_text()
    arm64 = (ROOT / "images" / "base" / "Dockerfile.arm64").read_text()

    assert "https://blackarch.org/strap.sh" in amd64
    assert "BLACKARCH_STRAP_" not in amd64
    assert "FROM alpine:3.21 AS archlinuxarm-rootfs" in arm64
    assert "ArchLinuxARM-aarch64-latest.tar.gz" in arm64
    assert "ARCHLINUXARM_ROOTFS_" not in arm64
    assert "COPY images/base/rootfs" not in arm64


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
