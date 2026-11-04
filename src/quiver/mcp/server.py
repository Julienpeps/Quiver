"""MCP adapter for Quiver's workspace and container interfaces."""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict

from quiver.audit.manager import AuditManager
from quiver.core.doctor import Doctor
from quiver.core.gui import GuiManager
from quiver.core.images import ImageManager
from quiver.core.lifecycle import (
    LifecycleError,
    LifecycleManager,
)
from quiver.core.workspaces import (
    WorkspaceError,
    initialize_workspace,
    load_workspace_config,
    quiver_root,
    service_data_size,
    workspace_lock,
)
from quiver.docker.backend import DockerBackend, DockerError
from quiver.mcp.execution import ExecutionBroker
from quiver.models.config import (
    AssessmentConfig,
    ImageConfig,
    default_assessment_config,
)
from quiver.services.manager import ServiceManager


class WorkspacePatch(BaseModel):
    """Typed partial workspace configuration accepted by MCP."""

    model_config = ConfigDict(extra="forbid")

    image_profile: str | None = None
    image_reference: str | None = None
    pull_policy: Literal["missing", "always", "never"] | None = None
    platform: Literal["auto", "linux/amd64", "linux/arm64"] | None = None
    privileged: bool | None = None
    network_mode: Literal["bridge", "host", "none", "custom"] | None = None
    network_name: str | None = None
    custom_packages: list[str] | None = None
    services_enabled: list[str] | None = None
    services_autostart: list[str] | None = None
    gui_enabled: bool | None = None
    logging_enabled: bool | None = None


@dataclass
class _Confirmation:
    operation: str
    workspace: str
    expires_at: float


@dataclass
class QuiverMcp:
    """Application module behind the MCP protocol adapter."""

    root: Path | None = None
    docker_context: str | None = None
    broker: ExecutionBroker | None = None
    _confirmations: dict[str, _Confirmation] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = quiver_root(Path(self.root) if self.root is not None else None)
        if self.broker is None:
            self.broker = ExecutionBroker(
                DockerBackend(context=self.docker_context), audit_writer=self._audit_execution
            )

    def _audit_execution(
        self,
        workspace: str,
        command: list[str] | str,
        started: Any,
        ended: Any,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> None:
        config = self._config(workspace)
        AuditManager(self.backend(config.docker.context)).record_exec(
            config, command, started, ended, exit_code, stdout, stderr
        )

    def backend(self, config_context: str | None = None) -> DockerBackend:
        return DockerBackend(context=self.docker_context or config_context)

    def _config(self, name: str) -> AssessmentConfig:
        return load_workspace_config(name, self.root)

    def list_workspaces(self) -> list[dict[str, Any]]:
        workspaces = self.root / "workspaces"
        if not workspaces.is_dir():
            return []
        result = []
        for entry in sorted(workspaces.iterdir()):
            if not entry.is_dir() or not (entry / ".quiver.yaml").is_file():
                continue
            try:
                config = load_workspace_config(entry.name, self.root)
                status = LifecycleManager(self.backend(config.docker.context), self.root).status(config)
                result.append({"name": config.name, "state": status.state, "image": status.image})
            except (WorkspaceError, DockerError, LifecycleError) as error:
                result.append({"name": entry.name, "state": "error", "error": str(error)})
        return result

    def workspace_status(self, name: str) -> dict[str, Any]:
        config = self._config(name)
        status = LifecycleManager(self.backend(config.docker.context), self.root).status(config)
        return {
            "name": status.name,
            "container_name": status.container_name,
            "running": status.running,
            "state": status.state,
            "image": status.image,
            "workspace": str(status.workspace),
            "privileged": config.docker.privileged,
            "network": config.docker.network.mode,
        }

    def workspace_get(self, name: str) -> dict[str, Any]:
        config = self._config(name)
        return config.model_dump(mode="json", exclude_none=True)

    def workspace_create(self, name: str, image_profile: str = "base") -> dict[str, Any]:
        config = default_assessment_config(name, self.root)
        if image_profile:
            config = config.model_copy(update={"image": ImageConfig(profile=image_profile)})
        initialize_workspace(config)
        return self.workspace_get(name)

    def workspace_patch(self, name: str, patch: WorkspacePatch) -> dict[str, Any]:
        current = self._config(name)
        raw = current.model_dump(mode="python")
        changes = patch.model_dump(exclude_none=True)
        image = raw["image"]
        for key in ("image_profile", "image_reference", "pull_policy", "platform"):
            if key in changes:
                image[key.removeprefix("image_")] = changes.pop(key)
        docker = raw["docker"]
        if "privileged" in changes:
            docker["privileged"] = changes.pop("privileged")
        network = docker["network"]
        for key in ("network_mode", "network_name"):
            if key in changes:
                network[key.removeprefix("network_")] = changes.pop(key)
        for key, target in (("custom_packages", raw), ("services_enabled", raw["services"]),
                            ("services_autostart", raw["services"]), ("gui_enabled", raw["gui"]),
                            ("logging_enabled", raw["logging"])):
            if key in changes:
                target[key.removeprefix("services_").removeprefix("gui_").removeprefix("logging_")] = changes.pop(key)
        if changes:
            raise ValueError(f"unsupported workspace patch fields: {sorted(changes)}")
        updated = AssessmentConfig.model_validate(raw)
        return self.workspace_update(name, updated)

    def workspace_update(self, name: str, config: AssessmentConfig) -> dict[str, Any]:
        current = self._config(name)
        if config.name != name or Path(config.workspace.path).resolve() != Path(current.workspace.path).resolve():
            raise WorkspaceError("workspace name and canonical path cannot be changed")
        with workspace_lock(name, self.root):
            from quiver.models.config import write_config

            write_config(Path(current.workspace.path) / ".quiver.yaml", config)
        return self.workspace_get(name)

    def workspace_fix_perms(self, name: str) -> dict[str, Any]:
        config = self._config(name)
        with workspace_lock(name, self.root):
            LifecycleManager(self.backend(config.docker.context), self.root).repair_ownership(config)
        return {"name": name, "ownership_repaired": True}

    def service_list(self, name: str) -> list[dict[str, Any]]:
        config = self._config(name)
        return [service.__dict__ for service in ServiceManager(self.backend(config.docker.context), config).list()]

    def service_action(self, name: str, service_id: str, action: str) -> dict[str, Any]:
        config = self._config(name)
        if action not in {"up", "down", "restart"}:
            raise ValueError(f"unsupported service action: {action}")
        with workspace_lock(name, self.root):
            result = getattr(ServiceManager(self.backend(config.docker.context), config), action)(service_id)
        return {"name": name, "service_id": service_id, "action": action, "result": result}

    def service_status(self, name: str, service_id: str | None = None) -> dict[str, Any]:
        config = self._config(name)
        result = ServiceManager(self.backend(config.docker.context), config).status(service_id)
        return {"name": name, "services": result if isinstance(result, dict) else {service_id or "": result}}

    def service_logs(self, name: str, service_id: str, follow: bool = False) -> dict[str, Any]:
        config = self._config(name)
        result = ServiceManager(self.backend(config.docker.context), config).logs(service_id, follow)
        return {"name": name, "service_id": service_id, "logs": result}

    def image_list(self) -> list[dict[str, str]]:
        return [{"profile": profile, "reference": reference} for profile, reference in ImageManager(self.backend()).list()]

    def image_pull(self, profile_or_reference: str) -> dict[str, str]:
        reference = ImageManager(self.backend()).pull(profile_or_reference)
        return {"reference": reference, "pulled": True}

    def image_build(self, profile: str, platform: str | None = None) -> dict[str, Any]:
        report = ImageManager(self.backend()).build(profile, platform)
        return {"profile": profile, "platform": platform, "report": report.text()}

    def doctor(self) -> list[dict[str, Any]]:
        return [check.__dict__ for check in Doctor(self.backend(), self.root).checks()]

    def gui(self, name: str, launch_browser: bool = False) -> dict[str, Any]:
        config = self._config(name)
        with workspace_lock(name, self.root):
            access = GuiManager(LifecycleManager(self.backend(config.docker.context), self.root)).open(
                config, launch_browser=launch_browser
            )
        return access.__dict__

    def audit_logs(self, name: str, session_id: str | None = None) -> dict[str, Any]:
        config = self._config(name)
        result = AuditManager(self.backend(config.docker.context)).logs(config, session_id)
        return {"name": name, "session_id": session_id, "logs": result}

    def audit_log_page(
        self, name: str, session_id: str, cursor: int = 0, max_bytes: int = 65536
    ) -> dict[str, Any]:
        if cursor < 0 or max_bytes <= 0:
            raise ValueError("cursor must be non-negative and max_bytes must be positive")
        result = self.audit_logs(name, session_id)["logs"]
        encoded = str(result).encode()
        page = encoded[cursor : cursor + max_bytes]
        return {
            "name": name,
            "session_id": session_id,
            "content": page.decode(errors="replace"),
            "next_cursor": cursor + len(page),
            "complete": cursor + len(page) >= len(encoded),
        }

    def workspace_start(self, name: str) -> dict[str, Any]:
        config = self._config(name)
        with workspace_lock(name, self.root):
            status = LifecycleManager(self.backend(config.docker.context), self.root).start(config)
        return self.workspace_status(name) | {"started": status.running}

    def workspace_stop(self, name: str, all_services: bool = False) -> dict[str, Any]:
        config = self._config(name)
        assert self.broker is not None
        with workspace_lock(name, self.root):
            affected = self.broker.terminate_workspace(name)
            LifecycleManager(self.backend(config.docker.context), self.root).stop(
                config, all_services=all_services
            )
        return {"name": name, "state": "stopped", "terminated_executions": affected}

    def destroy_plan(self, name: str) -> dict[str, Any]:
        config = self._config(name)
        workspace = Path(config.workspace.path)
        token = secrets.token_urlsafe(24)
        self._confirmations[token] = _Confirmation("destroy", name, time.monotonic() + 300)
        return {
            "operation": "destroy",
            "workspace": name,
            "path": str(workspace),
            "service_data_bytes": service_data_size(workspace),
            "confirmation_token": token,
            "expires_in_seconds": 300,
        }

    def destroy(self, name: str, confirmation_token: str) -> dict[str, Any]:
        confirmation = self._confirmations.pop(confirmation_token, None)
        if (
            confirmation is None
            or confirmation.operation != "destroy"
            or confirmation.workspace != name
            or confirmation.expires_at < time.monotonic()
        ):
            raise WorkspaceError("invalid or expired destruction confirmation token")
        config = self._config(name)
        assert self.broker is not None
        with workspace_lock(name, self.root):
            affected = self.broker.terminate_workspace(name)
            LifecycleManager(self.backend(config.docker.context), self.root).stop(
                config, all_services=True
            )
            workspace = Path(config.workspace.path)
            import shutil

            shutil.rmtree(workspace)
        return {"name": name, "destroyed": True, "terminated_executions": affected}

    def exec_start(
        self,
        name: str,
        command: list[str] | str,
        *,
        tty: bool = False,
        shell: bool = False,
        workdir: str = "/workspace",
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        status = self.workspace_status(name)
        if not status["running"]:
            raise WorkspaceError(f"workspace {name!r} is not running")
        assert self.broker is not None
        try:
            uid = f"{os.getuid()}:{os.getgid()}"
        except AttributeError:
            uid = None
        view = self.broker.start(
            name,
            str(status["container_name"]),
            command,
            tty=tty,
            shell=shell,
            user=uid,
            workdir=workdir,
            environment=environment,
        )
        return view.__dict__

    def exec_status(self, execution_id: str) -> dict[str, Any]:
        assert self.broker is not None
        return self.broker.status(execution_id).__dict__

    def exec_read(self, execution_id: str, cursor: int = 0, max_bytes: int = 65536) -> dict[str, Any]:
        assert self.broker is not None
        result = self.broker.read(execution_id, cursor, max_bytes)
        return {
            "execution": result.execution.__dict__,
            "chunks": [chunk.__dict__ for chunk in result.chunks],
            "next_cursor": result.next_cursor,
            "truncated": result.truncated,
        }

    def exec_write(self, execution_id: str, data: str) -> dict[str, Any]:
        assert self.broker is not None
        self.broker.write(execution_id, data)
        return {"execution_id": execution_id, "written_bytes": len(data.encode())}

    def exec_resize(self, execution_id: str, rows: int, columns: int) -> dict[str, Any]:
        assert self.broker is not None
        self.broker.resize(execution_id, rows, columns)
        return {"execution_id": execution_id, "rows": rows, "columns": columns}

    def exec_signal(self, execution_id: str, signal_number: int = 15) -> dict[str, Any]:
        assert self.broker is not None
        self.broker.signal(execution_id, signal_number)
        return {"execution_id": execution_id, "signal": signal_number}

    def exec_close(self, execution_id: str, force: bool = False) -> dict[str, Any]:
        assert self.broker is not None
        return self.broker.close(execution_id, force=force).__dict__


def create_server(application: QuiverMcp) -> MCPServer:
    """Build the versioned Quiver MCP tool/resource surface."""
    server = MCPServer(
        name="Quiver",
        version="1",
        description="Manage Quiver workspaces and execute commands in their containers.",
    )

    @server.tool(name="workspace_list", description="List persisted Quiver workspaces.")
    def workspace_list() -> dict[str, Any]:
        return {"workspaces": application.list_workspaces()}

    @server.tool(name="workspace_get", description="Return validated workspace configuration.")
    def workspace_get(name: str) -> dict[str, Any]:
        return application.workspace_get(name)

    @server.tool(name="workspace_status", description="Return primary container status.")
    def workspace_status(name: str) -> dict[str, Any]:
        return application.workspace_status(name)

    @server.tool(name="workspace_create", description="Create a validated workspace configuration.")
    def workspace_create(name: str, image_profile: str = "base") -> dict[str, Any]:
        return application.workspace_create(name, image_profile)

    @server.tool(name="workspace_update", description="Replace a workspace with a fully validated typed configuration.")
    def workspace_update(name: str, config: AssessmentConfig) -> dict[str, Any]:
        return application.workspace_update(name, config)

    @server.tool(name="workspace_patch", description="Apply a typed partial workspace configuration update.")
    def workspace_patch(name: str, patch: WorkspacePatch) -> dict[str, Any]:
        return application.workspace_patch(name, patch)

    @server.tool(name="workspace_fix_perms", description="Normalize persistent workspace ownership.")
    def workspace_fix_perms(name: str) -> dict[str, Any]:
        return application.workspace_fix_perms(name)

    @server.tool(name="workspace_start", description="Start a workspace's disposable container.")
    def workspace_start(name: str) -> dict[str, Any]:
        return application.workspace_start(name)

    @server.tool(name="service_list", description="List Quiver services and their workspace enablement.")
    def service_list(name: str) -> dict[str, Any]:
        return {"name": name, "services": application.service_list(name)}

    @server.tool(name="service_action", description="Start, stop, or restart a workspace service.")
    def service_action(name: str, service_id: str, action: str) -> dict[str, Any]:
        return application.service_action(name, service_id, action)

    @server.tool(name="service_status", description="Read one or all configured service states.")
    def service_status(name: str, service_id: str | None = None) -> dict[str, Any]:
        return application.service_status(name, service_id)

    @server.tool(name="service_logs", description="Read service logs.")
    def service_logs(name: str, service_id: str, follow: bool = False) -> dict[str, Any]:
        return application.service_logs(name, service_id, follow)

    @server.tool(name="image_list", description="List built-in image profiles.")
    def image_list() -> dict[str, Any]:
        return {"images": application.image_list()}

    @server.tool(name="image_pull", description="Pull an image profile or reference.")
    def image_pull(profile_or_reference: str) -> dict[str, Any]:
        return application.image_pull(profile_or_reference)

    @server.tool(name="image_build", description="Build a built-in image profile.")
    def image_build(profile: str, platform: str | None = None) -> dict[str, Any]:
        return application.image_build(profile, platform)

    @server.tool(name="doctor", description="Check Docker and Quiver host prerequisites.")
    def doctor() -> dict[str, Any]:
        return {"checks": application.doctor()}

    @server.tool(name="gui_open", description="Open the workspace desktop and return its URL.")
    def gui_open(name: str, launch_browser: bool = False) -> dict[str, Any]:
        return application.gui(name, launch_browser)

    @server.tool(name="audit_logs", description="List or read persisted Quiver session logs.")
    def audit_logs(name: str, session_id: str | None = None) -> dict[str, Any]:
        return application.audit_logs(name, session_id)

    @server.tool(name="audit_log_page", description="Read a bounded page of a persisted session log.")
    def audit_log_page(
        name: str, session_id: str, cursor: int = 0, max_bytes: int = 65536
    ) -> dict[str, Any]:
        return application.audit_log_page(name, session_id, cursor, max_bytes)

    @server.tool(name="workspace_stop", description="Stop and remove a workspace container.")
    def workspace_stop(name: str, all_services: bool = False) -> dict[str, Any]:
        return application.workspace_stop(name, all_services)

    @server.tool(name="workspace_destroy_plan", description="Plan destruction and issue a short-lived confirmation token.")
    def workspace_destroy_plan(name: str) -> dict[str, Any]:
        return application.destroy_plan(name)

    @server.tool(name="workspace_destroy", description="Destroy a workspace using a confirmation token.")
    def workspace_destroy(name: str, confirmation_token: str) -> dict[str, Any]:
        return application.destroy(name, confirmation_token)

    @server.tool(name="exec_start", description="Start a container command; use shell=true only for explicit shell syntax.")
    def exec_start(
        name: str,
        command: list[str] | str,
        tty: bool = False,
        shell: bool = False,
        workdir: str = "/workspace",
        environment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return application.exec_start(
            name, command, tty=tty, shell=shell, workdir=workdir, environment=environment
        )

    @server.tool(name="exec_status", description="Read execution state without consuming output.")
    def exec_status(execution_id: str) -> dict[str, Any]:
        return application.exec_status(execution_id)

    @server.tool(name="exec_read", description="Read bounded execution output after a cursor.")
    def exec_read(execution_id: str, cursor: int = 0, max_bytes: int = 65536) -> dict[str, Any]:
        return application.exec_read(execution_id, cursor, max_bytes)

    @server.tool(name="exec_write", description="Write stdin or PTY input to an execution.")
    def exec_write(execution_id: str, data: str) -> dict[str, Any]:
        return application.exec_write(execution_id, data)

    @server.tool(name="exec_resize", description="Resize a PTY execution.")
    def exec_resize(execution_id: str, rows: int, columns: int) -> dict[str, Any]:
        return application.exec_resize(execution_id, rows, columns)

    @server.tool(name="exec_signal", description="Send a signal to an execution process group.")
    def exec_signal(execution_id: str, signal_number: int = 15) -> dict[str, Any]:
        return application.exec_signal(execution_id, signal_number)

    @server.tool(name="exec_close", description="Close an execution, escalating to kill when requested.")
    def exec_close(execution_id: str, force: bool = False) -> dict[str, Any]:
        return application.exec_close(execution_id, force)

    @server.resource("quiver://workspaces", name="workspaces", mime_type="application/json")
    def workspaces_resource() -> str:
        import json

        return json.dumps(application.list_workspaces(), sort_keys=True)

    return server


def run(
    root: Path | None = None,
    docker_context: str | None = None,
    *,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the MCP server over stdio or localhost Streamable HTTP."""
    if transport not in {"stdio", "streamable-http"}:
        raise ValueError("transport must be stdio or streamable-http")
    server = create_server(QuiverMcp(root=root, docker_context=docker_context))
    if transport == "stdio":
        server.run("stdio")
    else:
        server.run("streamable-http", host=host, port=port)
