"""Resource-backed descriptors for Quiver-managed services."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


class ServiceError(RuntimeError):
    """Raised for invalid, unknown, or unavailable service definitions."""


class Healthcheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["http", "supervisor"]
    target: str
    command: list[str] | None = None


class ServiceDescriptor(BaseModel):
    """Validated metadata for an internal or Compose-managed service."""

    model_config = ConfigDict(extra="forbid")

    id: str
    display_name: str
    kind: Literal["internal", "compose"]
    autostart_default: bool = False
    requires_network: bool = True
    supported_platforms: list[Literal["linux/amd64", "linux/arm64"]]
    healthcheck: Healthcheck
    compose_services: list[str] = []
    dependency_healthchecks: dict[str, list[str]] = {}


@dataclass(frozen=True)
class RegisteredService:
    """A descriptor paired with the resource files required to run it."""

    descriptor: ServiceDescriptor
    compose_yaml: str | None = None


class ServiceRegistry:
    """Load built-in service definitions from package resources."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root

    def list(self) -> list[ServiceDescriptor]:
        """Return all known descriptors sorted by stable identifier."""
        return [self.get(service_id).descriptor for service_id in self.ids()]

    def ids(self) -> list[str]:
        """Return the available built-in service identifiers."""
        return sorted(
            entry.name
            for entry in self._services_root().iterdir()
            if entry.is_dir() and (entry / "service.yaml").is_file()
        )

    def get(self, service_id: str) -> RegisteredService:
        """Load and validate one built-in service descriptor."""
        service_root = self._services_root() / service_id
        descriptor_path = service_root / "service.yaml"
        try:
            data = yaml.safe_load(descriptor_path.read_text())
        except OSError as error:
            raise ServiceError(f"unknown service {service_id!r}") from error
        except yaml.YAMLError as error:
            raise ServiceError(f"invalid descriptor for service {service_id!r}: {error}") from error
        try:
            descriptor = ServiceDescriptor.model_validate(data)
        except ValidationError as error:
            raise ServiceError(f"invalid descriptor for service {service_id!r}: {error}") from error
        if descriptor.id != service_id:
            raise ServiceError(
                f"descriptor ID {descriptor.id!r} does not match service directory {service_id!r}"
            )
        compose_yaml = None
        if descriptor.kind == "compose":
            try:
                compose_yaml = (service_root / "compose.yaml").read_text()
            except OSError as error:
                raise ServiceError(f"Compose definition missing for service {service_id!r}") from error
        return RegisteredService(descriptor, compose_yaml)

    def _services_root(self) -> Path:
        if self._root:
            return self._root
        resource_root = files("quiver.resources.services")
        return Path(str(resource_root))


def parse_service_descriptor(data: dict[str, Any]) -> ServiceDescriptor:
    """Validate descriptor data supplied by tests or future external registries."""
    try:
        return ServiceDescriptor.model_validate(data)
    except ValidationError as error:
        raise ServiceError(f"invalid service descriptor: {error}") from error
