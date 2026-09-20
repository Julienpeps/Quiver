"""Validated, atomic edits to persisted assessment configuration."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from quiver.models.config import (
    AssessmentConfig,
    ConfigError,
    PortMapping,
    load_config,
    write_config,
)


class ConfigEditError(ConfigError):
    """Raised when a requested configuration edit is invalid or unsafe."""


def parse_setting(assignment: str) -> tuple[tuple[str, ...], Any]:
    """Parse one ``field.path=value`` assignment without executing shell syntax."""
    key, separator, raw_value = assignment.partition("=")
    if not separator:
        raise ConfigEditError("--set must use field.path=value syntax")
    parts = tuple(key.split("."))
    if not all(part and part.replace("_", "").isalnum() for part in parts):
        raise ConfigEditError(f"unsafe configuration path: {key!r}")
    try:
        return parts, yaml.safe_load(raw_value)
    except yaml.YAMLError as error:
        raise ConfigEditError(f"invalid YAML value for {key!r}: {error}") from error


def apply_settings(config: AssessmentConfig, assignments: Iterable[str]) -> AssessmentConfig:
    """Return a validated config after applying dotted-path assignments."""
    data = deepcopy(config.model_dump(mode="json"))
    for assignment in assignments:
        parts, value = parse_setting(assignment)
        target: dict[str, Any] = data
        for part in parts[:-1]:
            current = target.get(part)
            if not isinstance(current, dict):
                raise ConfigEditError(f"configuration path does not address a mapping: {'.'.join(parts)}")
            target = current
        target[parts[-1]] = value
    try:
        return AssessmentConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigEditError(f"invalid configuration edit: {error}") from error


def parse_port_mapping(value: str) -> PortMapping:
    """Parse ``[HOST_IP:]HOST_PORT:CONTAINER_PORT[/PROTO]`` for CLI options."""
    address, separator, protocol = value.partition("/")
    if not separator:
        protocol = "tcp"
    if protocol not in {"tcp", "udp"}:
        raise ConfigEditError("published port protocol must be tcp or udp")
    parts = address.rsplit(":", 2)
    if len(parts) == 2:
        host_ip, host_port, container_port = "127.0.0.1", *parts
    elif len(parts) == 3:
        host_ip, host_port, container_port = parts
    else:
        raise ConfigEditError("published port must be [HOST_IP:]HOST_PORT:CONTAINER_PORT[/PROTO]")
    try:
        return PortMapping(
            host_ip=host_ip,
            host_port=int(host_port) if host_port else None,
            container_port=int(container_port),
            protocol=protocol,
        )
    except ValueError as error:
        raise ConfigEditError(f"invalid published port {value!r}: {error}") from error


def materialize_vpn_files(
    config: AssessmentConfig,
    profile_source: Path | None,
    credentials_source: Path | None = None,
) -> AssessmentConfig:
    """Copy VPN source material into the assessment workspace with safe paths."""
    if profile_source is None and credentials_source is None:
        return config
    vpn_directory = Path(config.workspace.path) / ".vpn"
    vpn_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    if profile_source is not None:
        profile_destination = vpn_directory / f"client{profile_source.suffix.lower() or '.conf'}"
        _copy_private_file(profile_source, profile_destination, "VPN profile")
        data["vpn"]["enabled"] = True
        data["vpn"]["config"] = str(profile_destination.relative_to(config.workspace.path))
    if credentials_source is not None:
        credentials_destination = vpn_directory / "auth.txt"
        _copy_private_file(credentials_source, credentials_destination, "VPN credentials")
        data["vpn"]["credentials_file"] = str(
            credentials_destination.relative_to(config.workspace.path)
        )
    try:
        return AssessmentConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigEditError(f"invalid VPN configuration: {error}") from error


def _copy_private_file(source: Path, destination: Path, description: str) -> None:
    source = source.expanduser().resolve()
    if not source.is_file():
        raise ConfigEditError(f"{description} must be a readable regular file: {source}")
    try:
        shutil.copyfile(source, destination)
        destination.chmod(0o600)
    except OSError as error:
        raise ConfigEditError(f"could not copy {description.lower()}: {error}") from error


def replace_config(path: Path, config: AssessmentConfig) -> None:
    """Atomically replace an existing configuration after validation."""
    if not path.is_file():
        raise ConfigEditError(f"configuration does not exist: {path}")
    write_config(path, config)


def editor_command() -> list[str]:
    """Choose a platform-appropriate editor command without invoking a shell."""
    requested = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if requested:
        return shlex.split(requested)
    if sys.platform == "darwin":
        return ["open", "-W", "-t"]
    if sys.platform == "win32":
        return ["notepad"]
    return ["vi"]


def edit_in_editor(path: Path, command: list[str] | None = None) -> AssessmentConfig:
    """Edit a temporary copy and replace the active config only when it validates."""
    original = load_config(path)
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as temporary:
        temporary.write(yaml.safe_dump(original.model_dump(mode="json"), sort_keys=False))
        temporary_path = Path(temporary.name)
    try:
        result = subprocess.run([*(command or editor_command()), str(temporary_path)], check=False)
        if result.returncode:
            raise ConfigEditError(f"editor exited with status {result.returncode}")
        edited = load_config(temporary_path)
        replace_config(path, edited)
        return edited
    finally:
        temporary_path.unlink(missing_ok=True)


def apply_start_options(
    config: AssessmentConfig,
    *,
    image: str | None = None,
    platform: str | None = None,
    vpn: str | None = None,
    vpn_credentials: str | None = None,
    vpn_type: str | None = None,
    privileged: bool | None = None,
    network: str | None = None,
    network_name: str | None = None,
    publish: Iterable[str] = (),
    services: Iterable[str] = (),
    gui: bool | None = None,
) -> AssessmentConfig:
    """Return a validated config with creation/reconfiguration options applied."""
    data = config.model_dump(mode="json")
    if image:
        if "/" in image or ":" in image:
            data["image"]["profile"] = None
            data["image"]["reference"] = image
        else:
            data["image"]["profile"] = image
            data["image"]["reference"] = None
    if platform is not None:
        data["image"]["platform"] = platform
    if vpn is not None:
        data["vpn"]["enabled"] = True
        data["vpn"]["config"] = vpn
    if vpn_credentials is not None:
        # The CLI materializes credentials into .vpn before persisting the path.
        del vpn_credentials
    if vpn_type is not None:
        data["vpn"]["type"] = vpn_type
    if privileged is not None:
        data["docker"]["privileged"] = privileged
    if network is not None:
        data["docker"]["network"]["mode"] = network
        data["docker"]["network"]["name"] = network_name if network == "custom" else None
    elif network_name is not None:
        data["docker"]["network"]["name"] = network_name
    parsed_ports = [port.model_dump(mode="json") for port in map(parse_port_mapping, publish)]
    if parsed_ports:
        data["docker"]["ports"] = parsed_ports
    service_names = list(services)
    if service_names:
        data["services"]["enabled"] = sorted(set(data["services"]["enabled"] + service_names))
        data["services"]["autostart"] = sorted(
            set(data["services"]["autostart"] + service_names)
        )
    if gui is not None:
        data["gui"]["enabled"] = gui
    try:
        return AssessmentConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigEditError(f"invalid start options: {error}") from error


def has_start_options(**options: object) -> bool:
    """Return whether an existing assessment would be mutated by start options."""
    for value in options.values():
        if value is None:
            continue
        if isinstance(value, Mapping) and not value:
            continue
        if isinstance(value, (str, bytes)):
            if value:
                return True
        elif isinstance(value, Iterable):
            if any(True for _ in value):
                return True
        else:
            return True
    return False
