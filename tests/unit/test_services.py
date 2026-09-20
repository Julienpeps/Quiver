import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from quiver.docker.backend import DockerBackend
from quiver.models.config import AssessmentConfig
from quiver.services.compose import ComposeServiceAdapter, compose_project_name
from quiver.services.internal import SupervisorServiceAdapter
from quiver.services.manager import ServiceManager
from quiver.services.registry import ServiceError, ServiceRegistry


def config(tmp_path: Path) -> AssessmentConfig:
    return AssessmentConfig.model_validate(
        {
            "schema_version": 1,
            "name": "demo",
            "image": {"profile": "base"},
            "workspace": {"path": str(tmp_path)},
            "services": {"enabled": ["bloodhound-ce"], "autostart": ["bloodhound-ce"]},
        }
    )


def test_resource_backed_registry_loads_bloodhound_descriptor() -> None:
    service = ServiceRegistry().get("bloodhound-ce")

    assert service.descriptor.kind == "compose"
    assert service.descriptor.compose_services == ["bloodhound", "postgres", "neo4j"]
    assert "BLOODHOUND_HOST_IP" in (service.compose_yaml or "")


def test_compose_adapter_generates_loopback_files_and_persistent_credentials(tmp_path: Path) -> None:
    service = ServiceRegistry().get("bloodhound-ce")
    adapter = ComposeServiceAdapter(DockerBackend(), config(tmp_path))

    project = adapter.prepare(service)
    first_env = project.environment_path.read_text()
    override = yaml.safe_load(project.override_path.read_text())

    assert project.project_name == compose_project_name("demo", "bloodhound-ce")
    assert "BLOODHOUND_HOST_IP=127.0.0.1" in first_env
    assert project.environment_path.stat().st_mode & 0o777 == 0o600
    assert (project.directory / "postgres").is_dir()
    assert (project.directory / "neo4j").is_dir()
    assert override["networks"]["assessment"]["name"] == "quiver-demo"
    assert set(override["services"]) == {"bloodhound", "postgres", "neo4j"}
    assert adapter.prepare(service).environment_path.read_text() == first_env


def test_compose_adapter_uses_context_aware_docker_compose(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if "ps" in args:
            payload = '[{"Service": "bloodhound", "State": "running", "Health": "healthy"}]'
            return subprocess.CompletedProcess(args, 0, payload, "")
        return subprocess.CompletedProcess(args, 0, "started", "")

    adapter = ComposeServiceAdapter(DockerBackend(context="test", runner=runner), config(tmp_path))

    assert adapter.up(ServiceRegistry().get("bloodhound-ce")) == "started"
    assert commands[0][:4] == ["docker", "--context", "test", "compose"]
    assert "--project-name" in commands[0]
    assert "up" in commands[0]
    assert "ps" in commands[-1]


def test_compose_up_fails_when_primary_service_reports_unhealthy(tmp_path: Path) -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "ps" in args:
            payload = '[{"Service": "bloodhound", "State": "running", "Health": "unhealthy"}]'
            return subprocess.CompletedProcess(args, 0, payload, "")
        return subprocess.CompletedProcess(args, 0, "started", "")

    adapter = ComposeServiceAdapter(DockerBackend(runner=runner), config(tmp_path))

    with pytest.raises(ServiceError, match="unhealthy"):
        adapter.up(ServiceRegistry().get("bloodhound-ce"))


def test_compose_up_waits_for_healthy_then_succeeds(tmp_path: Path) -> None:
    calls = {"ps": 0}

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "ps" in args:
            calls["ps"] += 1
            health = "health: starting" if calls["ps"] < 3 else "healthy"
            payload = f'[[{{"Service": "bloodhound", "State": "running", "Health": "{health}"}}]]'[1:-1]
            return subprocess.CompletedProcess(args, 0, payload, "")
        return subprocess.CompletedProcess(args, 0, "started", "")

    adapter = ComposeServiceAdapter(DockerBackend(runner=runner), config(tmp_path))

    assert adapter.up(ServiceRegistry().get("bloodhound-ce")) == "started"
    assert calls["ps"] == 3


def test_internal_adapter_uses_supervisorctl() -> None:
    commands: list[list[str]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "gui RUNNING", "")

    adapter = SupervisorServiceAdapter(DockerBackend(runner=runner), "quiver-demo")

    assert adapter.status("gui") == "gui RUNNING"
    assert commands[0] == ["docker", "exec", "quiver-demo", "supervisorctl", "status", "gui"]


def test_manager_lists_assessment_service_enablement(tmp_path: Path) -> None:
    manager = ServiceManager(DockerBackend(), config(tmp_path))

    services = manager.list()

    assert [(service.id, service.enabled, service.autostart) for service in services] == [
        ("bloodhound-ce", True, True)
    ]
