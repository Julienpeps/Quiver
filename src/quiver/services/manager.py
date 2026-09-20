"""Uniform service lifecycle facade for internal and Compose services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from quiver.core.lifecycle import primary_container_name
from quiver.docker.backend import DockerBackend
from quiver.models.config import AssessmentConfig
from quiver.services.compose import ComposeServiceAdapter
from quiver.services.internal import SupervisorServiceAdapter
from quiver.services.registry import RegisteredService, ServiceError, ServiceRegistry


@dataclass(frozen=True)
class ServiceInfo:
    id: str
    display_name: str
    kind: str
    enabled: bool
    autostart: bool


class ServiceManager:
    """Dispatch service operations through their descriptor-declared adapter."""

    def __init__(
        self,
        docker: DockerBackend,
        config: AssessmentConfig,
        registry: ServiceRegistry | None = None,
    ) -> None:
        self.docker = docker
        self.config = config
        self.registry = registry or ServiceRegistry()

    def list(self) -> list[ServiceInfo]:
        """List known services with assessment-specific enablement settings."""
        return [
            ServiceInfo(
                id=descriptor.id,
                display_name=descriptor.display_name,
                kind=descriptor.kind,
                enabled=descriptor.id in self.config.services.enabled,
                autostart=descriptor.id in self.config.services.autostart,
            )
            for descriptor in self.registry.list()
        ]

    def up(self, service_id: str) -> str:
        return self._call(service_id, "up")

    def down(self, service_id: str) -> str:
        return self._call(service_id, "down")

    def restart(self, service_id: str) -> str:
        return self._call(service_id, "restart")

    def status(self, service_id: str | None = None) -> str | dict[str, str]:
        """Return one service state, or each enabled service's state when omitted."""
        if service_id is not None:
            return self._call(service_id, "status")
        return {
            service.id: self._call(service.id, "status")
            for service in self.list()
            if service.enabled
        }

    def start_autostart(self) -> None:
        """Start only services explicitly configured to start with the assessment."""
        for service_id in self.config.services.autostart:
            self.up(service_id)

    def stop_autostart(self) -> None:
        """Stop autostart services without deleting their persistent service data."""
        for service_id in self.config.services.autostart:
            self.down(service_id)

    def stop_all_managed(self) -> None:
        """Stop configured or previously materialized managed service stacks."""
        service_ids = set(self.config.services.enabled) | set(self.config.services.autostart)
        root = Path(self.config.workspace.path) / ".services"
        if root.is_dir():
            known_ids = set(self.registry.ids())
            service_ids.update(
                entry.name for entry in root.iterdir() if entry.is_dir() and entry.name in known_ids
            )
        for service_id in sorted(service_ids):
            self.down(service_id)

    def logs(self, service_id: str, follow: bool = False) -> str:
        service = self._service(service_id)
        if service.descriptor.kind == "compose":
            return ComposeServiceAdapter(self.docker, self.config).logs(service, follow)
        return SupervisorServiceAdapter(self.docker, primary_container_name(self.config.name)).logs(
            service_id, follow
        )

    def _call(self, service_id: str, operation: str) -> str:
        service = self._service(service_id)
        if service.descriptor.kind == "compose":
            adapter = ComposeServiceAdapter(self.docker, self.config)
            return getattr(adapter, operation)(service)
        adapter = SupervisorServiceAdapter(self.docker, primary_container_name(self.config.name))
        return getattr(adapter, operation)(service_id)

    def _service(self, service_id: str) -> RegisteredService:
        service = self.registry.get(service_id)
        platform = self.config.image.platform
        if platform != "auto" and platform not in service.descriptor.supported_platforms:
            raise ServiceError(f"service {service_id!r} is not available on {platform}")
        return service
