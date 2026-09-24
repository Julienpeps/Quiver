"""Adapter for Supervisor-backed services in the primary container."""

from __future__ import annotations

from quiver.docker.backend import DockerBackend
from quiver.services.registry import ServiceError

SUPERVISOR_CONFIG = "/etc/supervisor/supervisord.conf"


class InternalServiceError(ServiceError):
    """Raised when Supervisor cannot control an internal service."""


class SupervisorServiceAdapter:
    """Translate Quiver service actions to supervisorctl in a primary container."""

    def __init__(self, docker: DockerBackend, container_name: str) -> None:
        self.docker = docker
        self.container_name = container_name

    def up(self, service_id: str) -> str:
        return self._control("start", service_id)

    def down(self, service_id: str) -> str:
        return self._control("stop", service_id)

    def restart(self, service_id: str) -> str:
        return self._control("restart", service_id)

    def status(self, service_id: str | None = None) -> str:
        return self._control("status", service_id)

    def logs(self, service_id: str, follow: bool = False) -> str:
        args = ["exec", self.container_name, "supervisorctl", "-c", SUPERVISOR_CONFIG, "tail"]
        if follow:
            args.append("-f")
        args.append(service_id)
        return self._run(*args, stream=follow)

    def _control(self, action: str, service_id: str | None = None) -> str:
        args = ["exec", self.container_name, "supervisorctl", "-c", SUPERVISOR_CONFIG, action]
        if service_id:
            args.append(service_id)
        return self._run(*args)

    def _run(self, *args: str, stream: bool = False) -> str:
        result = self.docker.run(*args, check=False, stream=stream)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "supervisorctl failed"
            raise InternalServiceError(detail)
        return result.stdout.strip()
