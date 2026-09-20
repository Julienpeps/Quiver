"""Persisted assessment configuration and YAML serialization."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
DEFAULT_WORKSPACE_CONTAINER_PATH = "/workspace"


class ConfigError(ValueError):
    """Raised when an assessment configuration cannot be loaded or saved."""


Migration = Callable[[dict[str, Any]], dict[str, Any]]


class _Migrations:
    """Explicit one-way schema migrations keyed by the version they upgrade from."""

    def __init__(self) -> None:
        self._steps: dict[int, Migration] = {}

    def register(self, from_version: int, step: Migration) -> None:
        self._steps[from_version] = step

    def __contains__(self, version: int) -> bool:
        return version in self._steps

    def apply(self, version: int, data: dict[str, Any]) -> dict[str, Any]:
        step = self._steps.get(version)
        if step is None:
            raise ConfigError(f"no migration path from schema version {version} to {SCHEMA_VERSION}")
        return step(data)


MIGRATIONS = _Migrations()


class QuiverModel(BaseModel):
    """Base model which rejects configuration fields unknown to this schema."""

    model_config = ConfigDict(extra="forbid")


class ImageConfig(QuiverModel):
    profile: str | None = None
    reference: str | None = None
    pull_policy: Literal["missing", "always", "never"] = "missing"
    platform: Literal["auto", "linux/amd64", "linux/arm64"] = "auto"

    @model_validator(mode="after")
    def has_image_source(self) -> ImageConfig:
        if not self.profile and not self.reference:
            raise ValueError("image must set profile or reference")
        return self


class NetworkConfig(QuiverModel):
    mode: Literal["bridge", "host", "none", "custom"] = "bridge"
    name: str | None = None

    @model_validator(mode="after")
    def validates_name(self) -> NetworkConfig:
        if self.mode == "custom" and not self.name:
            raise ValueError("docker.network.name is required in custom mode")
        if self.mode != "custom" and self.name is not None:
            raise ValueError("docker.network.name is only valid in custom mode")
        return self


class PortMapping(QuiverModel):
    host_ip: str = "127.0.0.1"
    host_port: int | None = None
    container_port: int
    protocol: Literal["tcp", "udp"] = "tcp"

    @field_validator("host_port", "container_port")
    @classmethod
    def validates_port(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return value


class CapabilitiesConfig(QuiverModel):
    add: list[str] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)


class DockerConfig(QuiverModel):
    context: str | None = None
    privileged: bool = False
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    devices: list[str] = Field(default_factory=list)
    sysctls: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    ports: list[PortMapping] = Field(default_factory=list)


class WorkspaceConfig(QuiverModel):
    path: str
    container_path: str = DEFAULT_WORKSPACE_CONTAINER_PATH
    fix_ownership_on_stop: bool = True

    @field_validator("path")
    @classmethod
    def requires_absolute_path(cls, value: str) -> str:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("workspace.path must be absolute")
        return str(path.resolve())

    @field_validator("container_path")
    @classmethod
    def requires_absolute_container_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("workspace.container_path must be absolute")
        return value


class VpnHealthcheck(QuiverModel):
    timeout_seconds: int = Field(default=30, ge=1)
    require_interface: bool = True


class VpnConfig(QuiverModel):
    enabled: bool = False
    type: Literal["auto", "openvpn", "wireguard"] = "auto"
    config: str | None = None
    credentials_file: str | None = None
    full_tunnel: bool = True
    fail_closed: bool = True
    dns_through_vpn: bool = False
    local_management_bypass: bool = True
    bypass_cidrs: list[str] = Field(default_factory=list)
    healthcheck: VpnHealthcheck = Field(default_factory=VpnHealthcheck)

    @model_validator(mode="after")
    def enabled_vpn_requires_config(self) -> VpnConfig:
        if self.enabled and not self.config:
            raise ValueError("vpn.config is required when VPN is enabled")
        return self


class GuiConfig(QuiverModel):
    enabled: bool = True
    backend: Literal["novnc"] = "novnc"
    desktop: Literal["xfce"] = "xfce"
    container_port: int = Field(default=6080, ge=1, le=65535)
    host_ip: str = "127.0.0.1"
    host_port: int | None = Field(default=None, ge=1, le=65535)
    authentication: bool = True
    clipboard: bool = True
    dynamic_resize: bool = True
    initial_geometry: str = "1600x1000"


class LoggingConfig(QuiverModel):
    enabled: bool = True
    shell_recorder: Literal["script", "asciinema"] = "script"
    record_commands: bool = True
    render_searchable_text: bool = True
    asciinema_idle_limit: int = Field(default=2, ge=0)


class ServicesConfig(QuiverModel):
    autostart: list[str] = Field(default_factory=list)
    enabled: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class AssessmentConfig(QuiverModel):
    schema_version: int = SCHEMA_VERSION
    name: str
    image: ImageConfig
    docker: DockerConfig = Field(default_factory=DockerConfig)
    workspace: WorkspaceConfig
    vpn: VpnConfig = Field(default_factory=VpnConfig)
    gui: GuiConfig = Field(default_factory=GuiConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)

    @field_validator("schema_version")
    @classmethod
    def validates_schema_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version {value}; expected {SCHEMA_VERSION}")
        return value

    @field_validator("name")
    @classmethod
    def validates_assessment_name(cls, value: str) -> str:
        if not 1 <= len(value) <= 63:
            raise ValueError("name must contain 1 to 63 characters")
        if any(character in value for character in "/\\") or ".." in value:
            raise ValueError("name must not contain path traversal or slashes")
        if not value[0].isalnum() or not value[-1].isalnum():
            raise ValueError("name must start and end with an alphanumeric character")
        if not all(character.isascii() and (character.isalnum() or character in "._-") for character in value):
            raise ValueError("name contains characters invalid in Docker object names")
        return value

    @model_validator(mode="after")
    def validates_runtime_combinations(self) -> AssessmentConfig:
        if self.docker.network.mode == "none" and self.vpn.enabled:
            raise ValueError("VPN cannot be enabled with docker.network.mode=none")
        if self.docker.network.mode == "host" and self.vpn.enabled:
            raise ValueError("VPN cannot be enabled with docker.network.mode=host")
        if self.docker.network.mode == "none" and self.gui.enabled:
            raise ValueError("GUI cannot be enabled with docker.network.mode=none")
        return self


def default_assessment_config(name: str, root: Path) -> AssessmentConfig:
    """Create the v1 default configuration for an assessment name."""
    workspace = root.expanduser().resolve() / "workspaces" / name
    return AssessmentConfig(
        name=name,
        image=ImageConfig(profile="base"),
        workspace=WorkspaceConfig(path=str(workspace)),
    )


def load_config(path: Path) -> AssessmentConfig:
    """Load and validate a YAML assessment configuration.

    Configurations on an older schema version are migrated one-way; the original
    file is preserved as a timestamped backup before migration is applied
    (spec 7.1).
    """
    try:
        raw = path.read_text()
    except OSError as error:
        raise ConfigError(f"could not read configuration {path}: {error}") from error
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise ConfigError(f"invalid YAML in {path}: {error}") from error

    if not isinstance(data, Mapping):
        raise ConfigError(f"configuration {path} must contain a YAML mapping")
    version = data.get("schema_version")
    if isinstance(version, int) and version != SCHEMA_VERSION:
        _backup_before_migration(path, raw)
        migrated: dict[str, Any] = dict(data)
        while True:
            current = int(migrated.get("schema_version", 0))
            if current == SCHEMA_VERSION:
                break
            if current >= SCHEMA_VERSION:
                raise ConfigError(
                    f"configuration {path} uses schema version {current}, newer than {SCHEMA_VERSION}"
                )
            migrated = MIGRATIONS.apply(current, migrated)
        data = migrated

    try:
        return AssessmentConfig.model_validate(data)
    except ValueError as error:
        raise ConfigError(f"invalid configuration {path}: {error}") from error


def _backup_before_migration(path: Path, raw: str) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    try:
        backup.write_text(raw)
        backup.chmod(0o600)
    except OSError as error:
        raise ConfigError(f"could not back up {path} before migration: {error}") from error


def write_config(path: Path, config: AssessmentConfig) -> None:
    """Atomically write validated configuration with restrictive permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    payload = yaml.safe_dump(
        config.model_dump(mode="json", by_alias=True), sort_keys=False
    )
    try:
        temporary_path.write_text(payload)
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
    except OSError as error:
        raise ConfigError(f"could not write configuration {path}: {error}") from error
