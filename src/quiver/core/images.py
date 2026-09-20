"""Profile package manifests and Docker image operations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from quiver.docker.backend import DockerBackend, DockerError

Platform = Literal["linux/amd64", "linux/arm64"]
PROFILE_NAMES = ("base", "web", "internal", "full")


class ImageError(RuntimeError):
    """Raised for invalid profile manifests or image operations."""


@dataclass(frozen=True)
class PackageReport:
    """Resolved package sets for one profile and target architecture."""

    profile: str
    platform: Platform
    required: tuple[str, ...]
    optional: tuple[str, ...]

    def text(self) -> str:
        """Render a readable build package report."""
        required = ", ".join(self.required) or "(none)"
        optional = ", ".join(self.optional) or "(none)"
        return (
            f"Profile: {self.profile}\nPlatform: {self.platform}\n"
            f"Required packages: {required}\nOptional packages: {optional}"
        )


def _manifest_root() -> Path:
    return Path(__file__).parents[3] / "packages"


def load_package_report(
    profile: str,
    platform: Platform,
    manifest_root: Path | None = None,
) -> PackageReport:
    """Load a profile manifest and resolve mandatory/optional packages for an architecture."""
    root = manifest_root or _manifest_root()
    manifest_path = root / f"{profile}.yaml"
    if not manifest_path.is_file():
        raise ImageError(f"unknown image profile: {profile}")
    try:
        data = yaml.safe_load(manifest_path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise ImageError(f"could not load package manifest {manifest_path}: {error}") from error
    if not isinstance(data, dict) or data.get("profile") != profile:
        raise ImageError(f"invalid package manifest: {manifest_path}")
    architecture = platform.removeprefix("linux/")
    arch = data.get("arch", {}).get(architecture, {})
    if not isinstance(arch, dict):
        raise ImageError(f"invalid architecture section for {architecture} in {manifest_path}")
    required = _package_names(data.get("packages"), manifest_path)
    required += _package_names(arch.get("packages"), manifest_path)
    optional = _package_names(arch.get("optional"), manifest_path)
    return PackageReport(
        profile=profile,
        platform=platform,
        required=tuple(dict.fromkeys(required)),
        optional=tuple(dict.fromkeys(optional)),
    )


def _package_names(value: Any, manifest_path: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ImageError(f"package lists in {manifest_path} must contain non-empty strings")
    return value


def profile_reference(profile: str) -> str:
    if profile not in PROFILE_NAMES:
        raise ImageError(f"unknown image profile: {profile}")
    return f"quiver-{profile}:stable"


class ImageManager:
    """Resolve and invoke local Docker image profile operations."""

    def __init__(self, docker: DockerBackend, repository_root: Path | None = None) -> None:
        self.docker = docker
        self.repository_root = repository_root or Path(__file__).parents[3]

    def list(self) -> list[tuple[str, str]]:
        """Return built-in profile names and their default references."""
        return [(profile, profile_reference(profile)) for profile in PROFILE_NAMES]

    def pull(self, profile_or_reference: str) -> str:
        """Pull a built-in profile or explicit image reference."""
        reference = self._resolve_reference(profile_or_reference)
        self.docker.run("pull", reference)
        return reference

    def build(self, profile: str, platform: Platform | None = None) -> PackageReport:
        """Build a profile through Buildx after validating its package manifest.

        Defaults to the Docker daemon's native platform when ``platform`` is not
        supplied (spec 16.3).
        """
        target = platform or self._native_platform()
        if target not in ("linux/amd64", "linux/arm64"):
            raise ImageError("unsupported build platform: " + str(target))
        report = load_package_report(profile, target)
        dockerfile = self._dockerfile(profile, target)
        if not dockerfile.is_file():
            raise ImageError(f"no Dockerfile for profile {profile} on {target}")
        args = [
            "build",
            "--load",
            "--platform",
            target,
            "--file",
            str(dockerfile),
            "--tag",
            profile_reference(profile),
            "--build-arg",
            f"REQUIRED_PACKAGES={' '.join(report.required)}",
            "--build-arg",
            f"OPTIONAL_PACKAGES={' '.join(report.optional)}",
        ]
        if profile == "base":
            args.extend(self._provenance_args(target))
        args.append(str(self.repository_root))
        try:
            self.docker.buildx(*args)
        except DockerError as error:
            raise ImageError(f"failed to build {profile} for {target}: {error}\n{report.text()}") from error
        return report

    def _provenance_args(self, platform: Platform) -> list[str]:
        values = {
            "BLACKARCH_STRAP_URL": os.environ.get("QUIVER_BLACKARCH_STRAP_URL", ""),
            "BLACKARCH_STRAP_SHA256": os.environ.get("QUIVER_BLACKARCH_STRAP_SHA256", ""),
        }
        if platform == "linux/arm64":
            values["ARCHLINUXARM_ROOTFS_SHA256"] = os.environ.get(
                "QUIVER_ARCHLINUXARM_ROOTFS_SHA256", ""
            )
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ImageError(
                "base image build requires verified provenance environment variables: "
                + ", ".join(missing)
            )
        return [item for name, value in values.items() for item in ("--build-arg", f"{name}={value}")]

    def _native_platform(self) -> Platform:
        """Return the Docker daemon's native Linux platform."""
        try:
            version = self.docker.version()
        except DockerError as error:
            raise ImageError(f"could not query Docker daemon architecture: {error}") from error
        server = version.get("Server") or {}
        arch = str(server.get("Arch", "")).lower()
        mapping = {
            "x86_64": "linux/amd64",
            "amd64": "linux/amd64",
            "aarch64": "linux/arm64",
            "arm64": "linux/arm64",
        }
        if arch not in mapping:
            raise ImageError(f"unsupported Docker daemon architecture: {arch!r}")
        return mapping[arch]

    def _resolve_reference(self, profile_or_reference: str) -> str:
        if profile_or_reference in PROFILE_NAMES:
            return profile_reference(profile_or_reference)
        if not profile_or_reference or any(character.isspace() for character in profile_or_reference):
            raise ImageError("image reference must not be empty or contain whitespace")
        return profile_or_reference

    def _dockerfile(self, profile: str, platform: Platform) -> Path:
        if profile == "base":
            architecture = platform.removeprefix("linux/")
            return self.repository_root / "images" / "base" / f"Dockerfile.{architecture}"
        return self.repository_root / "images" / "profiles" / profile / "Dockerfile"
