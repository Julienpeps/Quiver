"""Assessment lifecycle orchestration over persisted config and Docker resources."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from quiver.docker.backend import DockerBackend, DockerConflictError, DockerError
from quiver.docker.inspect import (
    ASSESSMENT_LABEL,
    COMPONENT_LABEL,
    MANAGED_LABEL,
    SCHEMA_LABEL,
    ContainerState,
    container_state,
)
from quiver.errors import ImageUnavailableError, VpnError
from quiver.models.config import AssessmentConfig
from quiver.services.registry import ServiceError
from quiver.vpn.parser import VpnParseError, parse_profile

if TYPE_CHECKING:
    from quiver.services.manager import ServiceManager


class LifecycleError(RuntimeError):
    """Raised when an assessment lifecycle operation cannot be completed."""


@dataclass(frozen=True)
class AssessmentStatus:
    name: str
    container_name: str
    running: bool
    state: str
    image: str
    workspace: Path


def primary_container_name(name: str) -> str:
    return f"quiver-{name}"


def assessment_network_name(name: str) -> str:
    return f"quiver-{name}"


def image_reference(config: AssessmentConfig) -> str:
    """Resolve a persisted image configuration to its Docker reference."""
    if config.image.reference:
        return config.image.reference
    if config.image.profile:
        return f"quiver-{config.image.profile}:stable"
    raise LifecycleError("configuration has no image profile or reference")


def _host_uid() -> int | None:
    import os

    try:
        return os.getuid()
    except AttributeError:
        return None


def _host_gid() -> int | None:
    import os

    try:
        return os.getgid()
    except AttributeError:
        return None


class LifecycleManager:
    """Create and remove disposable primary containers for one assessment."""

    def __init__(self, docker: DockerBackend) -> None:
        self.docker = docker

    def status(self, config: AssessmentConfig) -> AssessmentStatus:
        """Return primary container state without creating Docker resources."""
        name = primary_container_name(config.name)
        inspection = self.docker.inspect(name)
        if inspection is None:
            return AssessmentStatus(
                name=config.name,
                container_name=name,
                running=False,
                state="stopped",
                image=image_reference(config),
                workspace=Path(config.workspace.path),
            )
        state = container_state(inspection)
        self._verify_primary(state, config.name)
        return AssessmentStatus(
            name=config.name,
            container_name=name,
            running=state.running,
            state=state.status,
            image=state.image,
            workspace=Path(config.workspace.path),
        )

    def start(self, config: AssessmentConfig) -> AssessmentStatus:
        """Recreate and start a primary container from persisted configuration."""
        workspace = Path(config.workspace.path)
        if not workspace.is_dir():
            raise LifecycleError(f"workspace does not exist: {workspace}")
        image = image_reference(config)
        self._preflight()
        self._validate_vpn(config)
        self._pull_if_needed(config, image)
        self._remove_stale_primary(config)
        if config.docker.network.mode == "bridge":
            self._ensure_network(config.name)
        runtime_config = self._write_runtime_config(config)
        args = self._create_args(config, image, runtime_config)
        try:
            self.docker.run(*args)
            self.docker.run("start", primary_container_name(config.name))
            if config.vpn.enabled:
                self._wait_for_vpn(config)
            self._service_manager(config).start_autostart()
        except VpnError:
            self.docker.run("rm", "--force", primary_container_name(config.name), check=False)
            raise
        except (DockerError, ServiceError) as error:
            self.docker.run("rm", "--force", primary_container_name(config.name), check=False)
            raise LifecycleError(str(error)) from error
        return self.status(config)

    def stop(self, config: AssessmentConfig, *, all_services: bool = False) -> None:
        """Stop services and remove the disposable primary container idempotently."""
        service_error: ServiceError | None = None
        try:
            manager = self._service_manager(config)
            if all_services:
                manager.stop_all_managed()
            else:
                manager.stop_autostart()
        except ServiceError as error:
            service_error = error

        name = primary_container_name(config.name)
        inspection = self.docker.inspect(name)
        if inspection is not None:
            self._verify_primary(container_state(inspection), config.name)
            if config.workspace.fix_ownership_on_stop:
                self._best_effort_ownership(config, name)
            self.docker.run("stop", name, check=False)
            self.docker.run("rm", name, check=False)
        if config.docker.network.mode == "bridge":
            self._remove_network_if_unused(config.name)
        if service_error is not None:
            raise LifecycleError(f"could not stop managed services: {service_error}") from service_error

    def _service_manager(self, config: AssessmentConfig) -> ServiceManager:
        """Delay importing services to avoid the lifecycle/service adapter cycle."""
        from quiver.services.manager import ServiceManager

        return ServiceManager(self.docker, config)

    def _remove_network_if_unused(self, assessment: str) -> None:
        """Remove only Quiver's empty assessment bridge, never user-owned networks."""
        network = assessment_network_name(assessment)
        inspection = self.docker.inspect(network)
        if inspection is None:
            return
        labels = inspection.get("Labels") or {}
        if labels.get(MANAGED_LABEL) != "true" or labels.get(ASSESSMENT_LABEL) != assessment:
            return
        containers = inspection.get("Containers") or {}
        if not containers:
            self.docker.run("network", "rm", network, check=False)

    def _preflight(self) -> None:
        """Reject daemon configurations that cannot safely host workspace mounts."""
        try:
            version = self.docker.version()
            server = version.get("Server") or {}
            if not isinstance(server, dict) or str(server.get("Os", "")).lower() != "linux":
                raise LifecycleError("Quiver requires a Docker daemon running Linux containers")
            context = self.docker.json("context", "inspect", "--format", "{{json .}}")
        except DockerError as error:
            raise LifecycleError(f"Docker preflight failed: {error}") from error
        if isinstance(context, list):
            context = context[0] if context else {}
        endpoints = context.get("Endpoints") if isinstance(context, dict) else {}
        docker_endpoint = (
            endpoints.get("docker") or endpoints.get("Docker") if isinstance(endpoints, dict) else {}
        )
        host = docker_endpoint.get("Host", "") if isinstance(docker_endpoint, dict) else ""
        if not str(host).startswith(("unix://", "npipe://")):
            raise LifecycleError(
                "remote Docker contexts are unsupported because workspace bind mounts require a local daemon"
            )

    def _validate_vpn(self, config: AssessmentConfig) -> None:
        if not config.vpn.enabled:
            return
        profile_path = Path(config.vpn.config or "")
        if not profile_path.is_absolute():
            profile_path = Path(config.workspace.path) / profile_path
        try:
            profile = parse_profile(profile_path, config.vpn.type)
        except VpnParseError as error:
            raise VpnError(f"invalid VPN profile: {error}") from error
        if config.vpn.full_tunnel and profile.type == "wireguard" and not profile.full_tunnel:
            raise VpnError("WireGuard profile does not route the IPv4 default route")

    def _wait_for_vpn(self, config: AssessmentConfig) -> None:
        deadline = time.monotonic() + config.vpn.healthcheck.timeout_seconds
        name = primary_container_name(config.name)
        last_status = "VPN health status is unavailable"
        while time.monotonic() < deadline:
            result = self.docker.run(
                "exec", name, "cat", "/workspace/.vpn/vpn-status.json", check=False
            )
            if result.returncode == 0:
                try:
                    status = json.loads(result.stdout)
                except json.JSONDecodeError:
                    last_status = "VPN health status is malformed"
                else:
                    if status.get("state") == "connected":
                        return
                    last_status = str(status.get("detail") or status.get("state") or last_status)
            time.sleep(0.5)
        raise VpnError(f"VPN did not become healthy: {last_status}")

    def _pull_if_needed(self, config: AssessmentConfig, image: str) -> None:
        policy = config.image.pull_policy
        try:
            if policy == "always":
                self.docker.run("pull", image)
            elif policy == "missing":
                found = self.docker.inspect(image) is not None
                if not found:
                    self.docker.run("pull", image)
            elif policy == "never" and self.docker.inspect(image) is None:
                raise ImageUnavailableError(f"image is not available locally: {image}")
        except ImageUnavailableError:
            raise
        except DockerError as error:
            raise ImageUnavailableError(f"could not pull image {image}: {error}") from error

    def repair_ownership(self, config: AssessmentConfig) -> None:
        """Normalize workspace ownership to the invoking host UID/GID.

        Uses the running primary container when available, otherwise a one-off
        container from the configured image. Failure is caller-handled.
        """
        uid = _host_uid()
        gid = _host_gid()
        if uid is None:
            return
        name = primary_container_name(config.name)
        inspection = self.docker.inspect(name)
        if inspection is not None:
            self._verify_primary(container_state(inspection), config.name)
            self.docker.run(
                "exec", name, "chown", "-R", f"{uid}:{gid}", config.workspace.container_path
            )
            return
        image = image_reference(config)
        self.docker.run(
            "run",
            "--rm",
            "--entrypoint",
            "chown",
            "--mount",
            f"type=bind,src={Path(config.workspace.path)},dst={config.workspace.container_path}",
            image,
            "-R",
            f"{uid}:{gid}",
            config.workspace.container_path,
        )

    def _best_effort_ownership(self, config: AssessmentConfig, name: str) -> None:
        try:
            uid = _host_uid()
            gid = _host_gid()
            if uid is None:
                return
            self.docker.run(
                "exec",
                name,
                "chown",
                "-R",
                f"{uid}:{gid}",
                config.workspace.container_path,
                check=False,
            )
        except DockerError:
            pass

    def _remove_stale_primary(self, config: AssessmentConfig) -> None:
        name = primary_container_name(config.name)
        inspection = self.docker.inspect(name)
        if inspection is None:
            return
        state = container_state(inspection)
        self._verify_primary(state, config.name)
        self.docker.run("rm", "--force", name)

    def _ensure_network(self, assessment: str) -> None:
        network = assessment_network_name(assessment)
        inspection = self.docker.inspect(network)
        if inspection is not None:
            labels = inspection.get("Labels") or {}
            if labels.get(MANAGED_LABEL) != "true" or labels.get(ASSESSMENT_LABEL) != assessment:
                raise LifecycleError(f"network name conflicts with unmanaged resource: {network}")
            return
        self.docker.run(
            "network",
            "create",
            "--driver",
            "bridge",
            "--label",
            f"{MANAGED_LABEL}=true",
            "--label",
            f"{ASSESSMENT_LABEL}={assessment}",
            "--label",
            f"{SCHEMA_LABEL}=1",
            network,
        )

    def _create_args(self, config: AssessmentConfig, image: str, runtime_config: Path) -> list[str]:
        workspace = Path(config.workspace.path)
        args = [
            "create",
            "--name",
            primary_container_name(config.name),
            "--init",
            "--workdir",
            config.workspace.container_path,
            "--label",
            f"{MANAGED_LABEL}=true",
            "--label",
            f"{ASSESSMENT_LABEL}={config.name}",
            "--label",
            f"{COMPONENT_LABEL}=primary",
            "--label",
            f"{SCHEMA_LABEL}=1",
            "--mount",
            f"type=bind,src={workspace},dst={config.workspace.container_path}",
            "--mount",
            f"type=bind,src={runtime_config},dst=/run/quiver/config.yaml,readonly",
        ]
        if config.image.platform != "auto":
            args.extend(("--platform", config.image.platform))
        if config.docker.privileged:
            args.append("--privileged")
        else:
            capabilities = list(config.docker.capabilities.add)
            if config.vpn.enabled and "NET_ADMIN" not in capabilities:
                capabilities.append("NET_ADMIN")
            for capability in capabilities:
                args.extend(("--cap-add", capability))
            if config.vpn.enabled and "/dev/net/tun" not in config.docker.devices:
                args.extend(("--device", "/dev/net/tun"))
            for capability in config.docker.capabilities.drop:
                args.extend(("--cap-drop", capability))
        for device in config.docker.devices:
            args.extend(("--device", device))
        for key, value in config.docker.sysctls.items():
            args.extend(("--sysctl", f"{key}={value}"))
        for key, value in config.docker.environment.items():
            args.extend(("--env", f"{key}={value}"))
        for port in config.docker.ports:
            host_port = "" if port.host_port is None else str(port.host_port)
            args.extend(
                (
                    "--publish",
                    f"{port.host_ip}:{host_port}:{port.container_port}/{port.protocol}",
                )
            )
        if config.gui.enabled and config.docker.network.mode == "bridge":
            gui_port_is_explicit = any(
                port.container_port == config.gui.container_port and port.protocol == "tcp"
                for port in config.docker.ports
            )
            if not gui_port_is_explicit:
                host_port = "" if config.gui.host_port is None else str(config.gui.host_port)
                args.extend(
                    (
                        "--publish",
                        f"{config.gui.host_ip}:{host_port}:{config.gui.container_port}/tcp",
                    )
                )
        network = config.docker.network
        if network.mode == "bridge":
            args.extend(("--network", assessment_network_name(config.name)))
        elif network.mode == "host":
            args.extend(("--network", "host"))
        elif network.mode == "none":
            args.extend(("--network", "none"))
        elif network.name:
            args.extend(("--network", network.name))
        args.extend((image,))
        return args

    def _write_runtime_config(self, config: AssessmentConfig) -> Path:
        runtime_path = Path(config.workspace.path) / ".services" / "runtime-config.yaml"
        runtime_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        runtime_path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False))
        runtime_path.chmod(0o600)
        return runtime_path

    @staticmethod
    def _verify_primary(state: ContainerState, assessment: str) -> None:
        if not state.managed:
            raise DockerConflictError(
                f"container name conflicts with unmanaged resource: {state.name}"
            )
        if state.labels.get(ASSESSMENT_LABEL) != assessment:
            raise DockerConflictError(
                f"container {state.name} belongs to another assessment"
            )
        if state.labels.get(COMPONENT_LABEL) != "primary":
            raise DockerConflictError(f"container {state.name} is not a primary container")
