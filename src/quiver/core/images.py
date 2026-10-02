"""Profile package manifests and Docker image operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Literal

import yaml

from quiver.docker.backend import DockerBackend, DockerError

Platform = Literal["linux/amd64", "linux/arm64"]
PROFILE_NAMES = ("base", "web", "internal", "external", "cloud", "full")


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


def _manifest_root() -> Traversable:
    """Return package manifests bundled with the installed distribution."""
    return files("quiver.resources").joinpath("packages")


def load_package_report(
    profile: str,
    platform: Platform,
    manifest_root: Path | Traversable | None = None,
) -> PackageReport:
    """Load a profile manifest and resolve mandatory/optional packages for an architecture."""
    root = manifest_root if manifest_root is not None else _manifest_root()
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
        # This injection point is retained for tests and external build contexts.
        # Normal installations build from assets bundled in the wheel.
        self.repository_root = repository_root

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
        with self._build_context() as context_root:
            dockerfile = self._dockerfile(context_root, profile, target)
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
                str(context_root),
            ]
            try:
                self.docker.buildx(*args)
            except DockerError as error:
                raise ImageError(
                    f"failed to build {profile} for {target}: {error}\n{report.text()}"
                ) from error
        return report

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

    @contextmanager
    def _build_context(self) -> Iterator[Path]:
        """Yield a filesystem Docker context for injected or bundled assets.

        Docker cannot consume :mod:`importlib.resources` traversables directly.
        Copying bundled assets also supports non-filesystem importers.
        """
        if self.repository_root is not None:
            yield self.repository_root
            return
        with TemporaryDirectory(prefix="quiver-build-") as directory:
            root = Path(directory)
            _copy_resource_tree(files("quiver.resources"), root)
            yield root

    def _dockerfile(self, context_root: Path, profile: str, platform: Platform) -> Path:
        if profile == "base":
            architecture = platform.removeprefix("linux/")
            return context_root / "images" / "base" / f"Dockerfile.{architecture}"
        return context_root / "images" / "profiles" / profile / "Dockerfile"


def _copy_resource_tree(source: Traversable, destination: Path) -> None:
    """Copy an importlib resource tree into Docker's filesystem-only context."""
    destination.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_dir():
            _copy_resource_tree(entry, target)
        else:
            target.write_bytes(entry.read_bytes())
