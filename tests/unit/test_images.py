import subprocess
from pathlib import Path
from typing import Any

import pytest

from quiver.core.images import ImageError, ImageManager, load_package_report
from quiver.docker.backend import DockerBackend


def test_package_report_resolves_required_and_optional_arch_packages() -> None:
    report = load_package_report("web", "linux/arm64")

    assert "nmap" in report.required
    assert "burpsuite" in report.optional
    assert "Profile: web" in report.text()
    assert "Platform: linux/arm64" in report.text()


def test_unknown_profile_has_actionable_error() -> None:
    with pytest.raises(ImageError, match="unknown image profile"):
        load_package_report("unknown", "linux/amd64")


def test_build_constructs_buildx_command_with_arch_report(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    repository = tmp_path
    dockerfile = repository / "images" / "profiles" / "web" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n")

    report = ImageManager(DockerBackend(runner=runner), repository).build("web", "linux/arm64")

    assert report.platform == "linux/arm64"
    assert calls[0][:5] == ["docker", "buildx", "build", "--load", "--platform"]
    assert "OPTIONAL_PACKAGES=burpsuite feroxbuster" in calls[0]


def test_base_build_requires_no_provenance_environment_variables(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    dockerfile = tmp_path / "images" / "base" / "Dockerfile.arm64"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n")

    ImageManager(DockerBackend(runner=runner), tmp_path).build("base", "linux/arm64")

    assert not any("BLACKARCH_STRAP" in argument or "ARCHLINUXARM_ROOTFS" in argument for argument in calls[0])


def test_invalid_manifest_package_data_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("profile: bad\npackages: invalid\narch: {}\n")

    with pytest.raises(ImageError, match="package lists"):
        load_package_report("bad", "linux/amd64", tmp_path)
