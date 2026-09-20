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


def test_arm_root_and_base_dockerfiles_require_verified_provenance() -> None:
    amd64 = (ROOT / "images" / "base" / "Dockerfile.amd64").read_text()
    arm64 = (ROOT / "images" / "base" / "Dockerfile.arm64").read_text()

    assert 'test -n "$BLACKARCH_STRAP_SHA256"' in amd64
    assert "sha256sum --check --status" in amd64
    assert "FROM scratch AS archlinuxarm-root" in arm64
    assert "archlinuxarm-aarch64.tar.gz" in arm64
    assert 'test -n "$ARCHLINUXARM_ROOTFS_SHA256"' in arm64


def test_workflows_pin_actions_and_require_provenance_inputs() -> None:
    test_workflow = (ROOT / ".github" / "workflows" / "test-cli.yml").read_text()
    build_workflow = (ROOT / ".github" / "workflows" / "build-images.yml").read_text()
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in test_workflow
    assert "blackarch_strap_sha256" in build_workflow
    assert "archlinuxarm_rootfs_sha256" in build_workflow
    assert "sha256sum --check --status" in build_workflow
    assert "docker buildx imagetools create" in release_workflow
