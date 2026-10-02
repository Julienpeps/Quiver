import subprocess
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from quiver.core.images import ImageError, ImageManager, load_package_report
from quiver.docker.backend import DockerBackend


def test_package_report_resolves_required_and_optional_arch_packages() -> None:
    report = load_package_report("web", "linux/arm64")

    assert "gobuster" in report.required
    assert not report.optional
    assert "Profile: web" in report.text()
    assert "Platform: linux/arm64" in report.text()


def test_external_profile_is_available_on_both_architectures() -> None:
    arm64 = load_package_report("external", "linux/arm64")
    amd64 = load_package_report("external", "linux/amd64")

    assert {"gobuster", "findomain", "whois"} <= set(arm64.required)
    assert "assetfinder" not in arm64.required
    assert "assetfinder" in amd64.required
    assert {"bbot", "dirsearch", "gau", "whatweb"} <= set(amd64.required)


def test_cloud_profile_has_cross_arch_provider_clis_and_amd64_specialists() -> None:
    arm64 = load_package_report("cloud", "linux/arm64")
    amd64 = load_package_report("cloud", "linux/amd64")

    assert {"aws-cli-v2", "azure-cli", "kubectl", "trivy"} <= set(arm64.required)
    assert "prowler" not in arm64.required
    assert "prowler" in amd64.required
    dockerfile = Path(str(files("quiver.resources"))) / "images" / "profiles" / "cloud" / "Dockerfile"
    contents = dockerfile.read_text()
    assert "google-cloud-cli-linux-${gcloud_arch}.tar.gz" in contents
    assert "gcloud_arch=arm" in contents


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
    build = next(call for call in calls if "build" in call)
    assert build[:5] == ["docker", "buildx", "build", "--load", "--platform"]
    assert "OPTIONAL_PACKAGES=" in build


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


def test_bundled_assets_are_materialized_as_a_complete_docker_context() -> None:
    contexts: list[Path] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[-1] == "version":
            return subprocess.CompletedProcess(args, 0, "", "")
        context = Path(args[-1])
        contexts.append(context)
        assert (context / "images/base/Dockerfile.amd64").is_file()
        assert (context / "packages/base.yaml").is_file()
        assert (context / "images/common/supervisor/killswitch.py").is_file()
        return subprocess.CompletedProcess(args, 0, "", "")

    ImageManager(DockerBackend(runner=runner)).build("base", "linux/amd64")

    assert len(contexts) == 1
    assert not contexts[0].exists()


def test_container_killswitch_library_matches_the_runtime_library() -> None:
    bundled = files("quiver.resources").joinpath("images/common/supervisor/killswitch.py")
    runtime = files("quiver.vpn").joinpath("killswitch.py")

    assert bundled.read_text() == runtime.read_text()


def test_invalid_manifest_package_data_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("profile: bad\npackages: invalid\narch: {}\n")

    with pytest.raises(ImageError, match="package lists"):
        load_package_report("bad", "linux/amd64", tmp_path)
