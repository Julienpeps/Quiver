"""Generated Docker Compose projects for external assessment services."""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from quiver.core.lifecycle import assessment_network_name
from quiver.docker.backend import DockerBackend
from quiver.models.config import AssessmentConfig
from quiver.services.registry import RegisteredService, ServiceError


@dataclass(frozen=True)
class ComposeProject:
    service_id: str
    project_name: str
    directory: Path
    environment_path: Path
    compose_path: Path
    override_path: Path


def compose_project_name(assessment: str, service_id: str) -> str:
    """Return the stable Compose project name for an assessment service."""
    return f"quiver-{assessment}-{service_id}"


class ComposeServiceAdapter:
    """Render and control one external Compose service stack."""

    def __init__(self, docker: DockerBackend, config: AssessmentConfig) -> None:
        self.docker = docker
        self.config = config

    def prepare(self, service: RegisteredService) -> ComposeProject:
        """Materialize resource definition, secrets, and assessment network override."""
        descriptor = service.descriptor
        if descriptor.kind != "compose" or service.compose_yaml is None:
            raise ServiceError(f"service {descriptor.id!r} is not a Compose service")
        network = self._network_name(descriptor.requires_network)
        directory = Path(self.config.workspace.path) / ".services" / descriptor.id
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        project = ComposeProject(
            service_id=descriptor.id,
            project_name=compose_project_name(self.config.name, descriptor.id),
            directory=directory,
            environment_path=directory / ".env",
            compose_path=directory / "compose.yaml",
            override_path=directory / "compose.quiver.yaml",
        )
        project.compose_path.write_text(service.compose_yaml)
        project.override_path.write_text(
            yaml.safe_dump(self._build_override(descriptor, network), sort_keys=False)
        )
        self._write_environment(project)
        for data_directory in ("postgres", "neo4j"):
            (directory / data_directory).mkdir(mode=0o700, exist_ok=True)
        return project

    def up(self, service: RegisteredService) -> str:
        project = self.prepare(service)
        output = self._run(project, "up", "--detach", "--remove-orphans")
        primary = service.descriptor.compose_services[0] if service.descriptor.compose_services else ""
        self._wait_for_healthy(project, primary)
        return output

    def down(self, service: RegisteredService) -> str:
        project = self._existing_project(service)
        if project is None:
            return ""
        return self._run(project, "down")

    def restart(self, service: RegisteredService) -> str:
        project = self._existing_project(service)
        if project is None:
            raise ServiceError(f"service {service.descriptor.id!r} has not been started")
        return self._run(project, "restart")

    def status(self, service: RegisteredService) -> str:
        project = self._existing_project(service)
        if project is None:
            return "not started"
        return self._run(project, "ps", "--format", "json")

    def logs(self, service: RegisteredService, follow: bool = False) -> str:
        project = self._existing_project(service)
        if project is None:
            raise ServiceError(f"service {service.descriptor.id!r} has not been started")
        arguments = ["logs"]
        if follow:
            arguments.append("--follow")
        return self._run(project, *arguments, stream=follow)

    def _existing_project(self, service: RegisteredService) -> ComposeProject | None:
        """Load a previously prepared project without mutating service state."""
        directory = Path(self.config.workspace.path) / ".services" / service.descriptor.id
        project = ComposeProject(
            service_id=service.descriptor.id,
            project_name=compose_project_name(self.config.name, service.descriptor.id),
            directory=directory,
            environment_path=directory / ".env",
            compose_path=directory / "compose.yaml",
            override_path=directory / "compose.quiver.yaml",
        )
        if not all(
            path.is_file()
            for path in (project.environment_path, project.compose_path, project.override_path)
        ):
            return None
        return project

    def _run(self, project: ComposeProject, *command: str, stream: bool = False) -> str:
        result = self.docker.compose(
            "--project-name",
            project.project_name,
            "--env-file",
            str(project.environment_path),
            "--file",
            str(project.compose_path),
            "--file",
            str(project.override_path),
            *command,
            check=False,
            stream=stream,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "Docker Compose failed"
            raise ServiceError(detail)
        return result.stdout.strip()

    def _network_name(self, required: bool) -> str | None:
        mode = self.config.docker.network.mode
        if not required:
            return None
        if mode == "bridge":
            return assessment_network_name(self.config.name)
        if mode == "custom" and self.config.docker.network.name:
            return self.config.docker.network.name
        raise ServiceError("Compose services require bridge or custom assessment networking")

    def _wait_for_healthy(self, project: ComposeProject, service_name: str, timeout_seconds: int = 300) -> None:
        """Wait for the service's compose health status before reporting ready."""
        deadline = time.monotonic() + timeout_seconds
        last_state = "unknown"
        while time.monotonic() < deadline:
            result = self._run(project, "ps", "--format", "json")
            if not result.strip():
                raise ServiceError(f"service {service_name!r} no longer exists (compose ps empty)")
            try:
                rows = json.loads(result)
            except json.JSONDecodeError:
                raise ServiceError(f"compose ps returned non-JSON output: {result[:200]}") from None
            if isinstance(rows, dict):
                rows = [rows]
            if not rows:
                raise ServiceError(f"service {service_name!r} no longer exists (compose ps empty)")
            matched = False
            unhealthy = False
            ready = False
            last_state = "starting"
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if str(row.get("Service", "")).lower() != service_name.lower():
                    continue
                matched = True
                container_state = str(row.get("State", "")).lower()
                health = str(row.get("Health", "")).lower()
                last_state = f"state={container_state or 'unknown'}, health={health or 'unknown'}"
                if "exited" in container_state or health == "unhealthy":
                    unhealthy = True
                    break
                if container_state == "running" and health in {"healthy", "no health check", ""}:
                    ready = True
            if matched and unhealthy:
                raise ServiceError(f"service {service_name!r} reported unhealthy: {last_state}")
            if not matched and not unhealthy:
                raise ServiceError(
                    f"service {service_name!r} missing from compose ps output: {result[:200]}"
                )
            if matched and ready:
                return
            time.sleep(1)
        raise ServiceError(f"service {service_name!r} did not become healthy (last state: {last_state})")

    @staticmethod
    def _build_override(descriptor, network: str | None) -> dict[str, Any]:
        services: dict[str, Any] = {}
        if network is not None:
            for service_name in descriptor.compose_services:
                services.setdefault(service_name, {})["networks"] = ["assessment"]
        if descriptor.compose_services:
            primary = descriptor.compose_services[0]
            if descriptor.healthcheck.command:
                services.setdefault(primary, {})["healthcheck"] = {
                    "test": list(descriptor.healthcheck.command),
                    "interval": "5s",
                    "timeout": "3s",
                    "retries": 60,
                }
            dependencies = [name for name in descriptor.compose_services if name != primary]
            if dependencies:
                services.setdefault(primary, {})["depends_on"] = {
                    name: {
                        "condition": (
                            "service_healthy" if name in descriptor.dependency_healthchecks else "service_started"
                        )
                    }
                    for name in dependencies
                }
            for name, test in descriptor.dependency_healthchecks.items():
                if name != primary:
                    services.setdefault(name, {})["healthcheck"] = {
                        "test": list(test),
                        "interval": "5s",
                        "timeout": "3s",
                        "retries": 60,
                    }
        override: dict[str, Any] = {"services": services}
        if network is not None:
            override["networks"] = {"assessment": {"external": True, "name": network}}
        return override

    def _write_environment(self, project: ComposeProject) -> None:
        existing = self._read_environment(project.environment_path)
        values = {
            "BLOODHOUND_DATA_DIR": str(project.directory),
            "BLOODHOUND_HOST_IP": "127.0.0.1",
            "BLOODHOUND_PORT": existing.get("BLOODHOUND_PORT") or str(_loopback_port()),
            "POSTGRES_USER": "bloodhound",
            "POSTGRES_PASSWORD": existing.get("POSTGRES_PASSWORD", _secret()),
            "NEO4J_PASSWORD": existing.get("NEO4J_PASSWORD", _secret()),
        }
        project.environment_path.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items())
        )
        project.environment_path.chmod(0o600)

    @staticmethod
    def _read_environment(path: Path) -> dict[str, str]:
        if not path.is_file():
            return {}
        try:
            return {
                key: value
                for line in path.read_text().splitlines()
                if "=" in line
                for key, value in [line.split("=", 1)]
            }
        except OSError as error:
            raise ServiceError(f"could not read generated service environment: {error}") from error


def _secret() -> str:
    return secrets.token_urlsafe(24)


def _loopback_port() -> int:
    """Allocate a port in Docker's ephemeral range and persist it per assessment."""
    return secrets.randbelow(16384) + 49152
