"""Command-line interface for Quiver."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Annotated

import typer

from quiver.audit.manager import AuditError, AuditManager
from quiver.core.doctor import Doctor
from quiver.core.editing import (
    ConfigEditError,
    apply_settings,
    apply_start_options,
    edit_in_editor,
    has_start_options,
    materialize_vpn_files,
    replace_config,
)
from quiver.core.gui import GuiError, GuiManager, gui_url
from quiver.core.images import ImageError, ImageManager
from quiver.core.lifecycle import LifecycleError, LifecycleManager
from quiver.core.workspaces import (
    WorkspaceError,
    assessment_workspace,
    config_path,
    initialize_workspace,
    latest_session_id,
    load_workspace_config,
    quiver_root,
    service_data_size,
    workspace_lock,
)
from quiver.docker.backend import DockerBackend, DockerError
from quiver.docker.inspect import published_port
from quiver.errors import ImageUnavailableError, VpnError
from quiver.models.config import (
    AssessmentConfig,
    ConfigError,
    default_assessment_config,
)
from quiver.services.manager import ServiceManager
from quiver.services.registry import ServiceError, ServiceRegistry

app = typer.Typer(
    name="quiver",
    help="Disposable assessment-scoped penetration-testing environments.",
    no_args_is_help=True,
)
service_app = typer.Typer(help="Manage internal and Compose assessment services.")
image_app = typer.Typer(help="Manage Quiver image profiles.")
app.add_typer(service_app, name="service")
app.add_typer(image_app, name="image")
workspace_app = typer.Typer(help="Manage assessment workspaces.")
app.add_typer(workspace_app, name="workspace")


class AppState:
    def __init__(self, root: Path | None, context: str | None, verbose: bool) -> None:
        self.root = quiver_root(root)
        self.context = context
        self.verbose = verbose

    def backend(self, config_context: str | None = None) -> DockerBackend:
        context = self.context or config_context

        def on_command(rendered: str) -> None:
            if self.verbose:
                typer.echo(f"docker: {rendered}", err=True)

        return DockerBackend(context=context, on_command=on_command)

    def manager(self, config_context: str | None = None) -> LifecycleManager:
        return LifecycleManager(self.backend(config_context), self.root)

    def services(self, config: AssessmentConfig) -> ServiceManager:
        return ServiceManager(self.backend(config.docker.context), config)


def _state(context: typer.Context) -> AppState:
    return context.obj


def _abort(error: Exception, exit_code: int = 2) -> None:
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(exit_code)


def _abort_workspace(error: Exception, fallback: int = 3) -> None:
    """Abort with the stable workspace-failure code for workspace errors."""
    _abort(error, 7 if isinstance(error, WorkspaceError) else fallback)


def assessment_completion(incomplete: str) -> list[str]:
    """Complete persisted assessment names from the default Quiver root."""
    workspaces = quiver_root() / "workspaces"
    if not workspaces.is_dir():
        return []
    return sorted(
        entry.name
        for entry in workspaces.iterdir()
        if entry.is_dir()
        and (entry / ".quiver.yaml").is_file()
        and entry.name.startswith(incomplete)
    )


AssessmentName = Annotated[
    str, typer.Argument(help="Assessment name.", autocompletion=assessment_completion)
]


def service_completion(incomplete: str) -> list[str]:
    """Complete built-in service identifiers without contacting Docker."""
    return [
        service_id
        for service_id in ServiceRegistry().ids()
        if service_id.startswith(incomplete)
    ]


def profile_completion(incomplete: str) -> list[str]:
    """Complete built-in image profile names."""
    return [
        profile
        for profile, _reference in ImageManager(DockerBackend()).list()
        if profile.startswith(incomplete)
    ]


@app.callback()
def main(
    context: typer.Context,
    root: Annotated[Path | None, typer.Option(help="Override the ~/.quiver state root.")] = None,
    docker_context: Annotated[
        str | None, typer.Option("--context", help="Docker context for this invocation only.")
    ] = None,
    verbose: Annotated[bool, typer.Option(help="Show detailed diagnostics.")] = False,
    no_color: Annotated[bool, typer.Option("--no-color", help="Disable color output.")] = False,
) -> None:
    """Manage Quiver assessment environments."""
    del no_color
    context.obj = AppState(root, docker_context, verbose)


@app.command()
def start(
    context: typer.Context,
    name: AssessmentName,
    image: Annotated[str | None, typer.Option("--image", help="Profile or image reference.")] = None,
    platform: Annotated[str | None, typer.Option(help="Target image platform.")] = None,
    vpn: Annotated[Path | None, typer.Option(help="VPN profile path.")] = None,
    vpn_credentials: Annotated[
        Path | None, typer.Option("--vpn-credentials", help="VPN credentials-file path.")
    ] = None,
    vpn_type: Annotated[str | None, typer.Option(help="VPN type: auto, openvpn, or wireguard.")] = None,
    privileged: Annotated[bool | None, typer.Option("--privileged/--no-privileged")] = None,
    network: Annotated[str | None, typer.Option(help="Network mode.")] = None,
    network_name: Annotated[str | None, typer.Option(help="Custom Docker network name.")] = None,
    publish: Annotated[list[str] | None, typer.Option("--publish", "-p", help="Published port mapping.")] = None,
    service: Annotated[list[str] | None, typer.Option("--service", help="Service to enable and autostart.")] = None,
    packages: Annotated[str | None, typer.Option("--packages", help="Comma-separated Arch packages to install.")] = None,
    gui: Annotated[bool | None, typer.Option("--gui/--no-gui")] = None,
    reconfigure: Annotated[bool, typer.Option(help="Allow creation options to update existing config.")] = False,
    detach: Annotated[bool, typer.Option(help="Start without entering a shell.")] = False,
) -> None:
    """Create (if needed) and start an assessment's disposable container."""
    state = _state(context)
    workspace = assessment_workspace(name, state.root)
    publish = publish or []
    service = service or []
    options = {
        "image": image,
        "platform": platform,
        "vpn": str(vpn) if vpn else None,
        "vpn_credentials": str(vpn_credentials) if vpn_credentials else None,
        "vpn_type": vpn_type,
        "privileged": privileged,
        "network": network,
        "network_name": network_name,
        "publish": publish,
        "services": service,
        "packages": packages,
        "gui": gui,
    }
    try:
        if workspace.exists():
            config = load_workspace_config(name, state.root)
            if has_start_options(**options) and not reconfigure:
                raise ConfigEditError(
                    "creation options cannot modify an existing assessment; use edit or --reconfigure"
                )
            if reconfigure:
                config = apply_start_options(config, **(options | {"vpn": None, "vpn_credentials": None}))
                config = materialize_vpn_files(config, vpn, vpn_credentials)
                replace_config(config_path(workspace), config)
        else:
            config = apply_start_options(
                default_assessment_config(name, state.root),
                **(options | {"vpn": None, "vpn_credentials": None}),
            )
            initialize_workspace(config)
            config = materialize_vpn_files(config, vpn, vpn_credentials)
            replace_config(config_path(workspace), config)
        with workspace_lock(name, state.root):
            status = state.manager(config.docker.context).start(config)
    except (ConfigError, ConfigEditError, WorkspaceError) as error:
        _abort(error, 2)
    except ImageUnavailableError as error:
        _abort(error, 4)
    except (DockerError, GuiError, LifecycleError) as error:
        _abort(error, 3)
    except VpnError as error:
        _abort(error, 5)
    typer.echo(f"{status.name}: {status.state} ({status.container_name})")
    if not detach:
        try:
            exit_code = AuditManager(
                state.backend(config.docker.context)
            ).shell(config)
        except (AuditError, DockerError) as error:
            _abort(error, 3)
        if exit_code:
            raise typer.Exit(exit_code)


@app.command()
def stop(context: typer.Context, name: AssessmentName) -> None:
    """Stop and remove the disposable primary container."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        with workspace_lock(name, state.root):
            state.manager(config.docker.context).stop(config)
    except (WorkspaceError, DockerError, LifecycleError) as error:
        _abort(error, 7 if isinstance(error, WorkspaceError) else 3)
    typer.echo(f"{name}: stopped")


@app.command()
def edit(
    context: typer.Context,
    name: AssessmentName,
    settings: Annotated[list[str] | None, typer.Option("--set", help="Set field.path=YAML_VALUE.")] = None,
    vpn: Annotated[Path | None, typer.Option(help="Set the VPN profile path.")] = None,
    vpn_credentials: Annotated[
        Path | None, typer.Option("--vpn-credentials", help="Set VPN credentials file.")
    ] = None,
    publish: Annotated[list[str] | None, typer.Option("--publish", "-p", help="Replace published ports.")] = None,
    packages: Annotated[str | None, typer.Option("--packages", help="Comma-separated Arch packages to add.")] = None,
    restart: Annotated[bool, typer.Option(help="Recreate the container after editing.")] = False,
) -> None:
    """Validate and atomically update an assessment configuration."""
    state = _state(context)
    settings = settings or []
    publish = publish or []
    try:
        config = load_workspace_config(name, state.root)
        workspace = Path(config.workspace.path)
        with workspace_lock(name, state.root):
            path = config_path(workspace)
            if settings or vpn or vpn_credentials or publish or packages is not None:
                edited = apply_settings(config, settings)
                edited = apply_start_options(edited, publish=publish, packages=packages)
                edited = materialize_vpn_files(edited, vpn, vpn_credentials)
                replace_config(path, edited)
            else:
                edited = edit_in_editor(path)
            if restart:
                manager = state.manager(edited.docker.context)
                manager.stop(edited)
                manager.start(edited)
    except (ConfigError, ConfigEditError, WorkspaceError) as error:
        _abort(error, 2)
    except ImageUnavailableError as error:
        _abort(error, 4)
    except (DockerError, GuiError, LifecycleError) as error:
        _abort(error, 3)
    except VpnError as error:
        _abort(error, 5)
    if restart:
        typer.echo(f"{name}: configuration updated and container recreated")
    else:
        typer.echo(f"{name}: configuration updated; restart required to apply runtime changes")


def _vpn_state(workspace: Path) -> str:
    path = workspace / ".vpn" / "vpn-status.json"
    if not path.is_file():
        return "configured"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return "configured"
    state = str(data.get("state", "configured"))
    detail = data.get("detail")
    return f"{state} ({detail})" if detail else state


def _gui_status_line(config: AssessmentConfig, container_name: str, docker: DockerBackend, running: bool) -> str:
    if not config.gui.enabled:
        return "gui: disabled"
    if not running or config.docker.network.mode not in {"bridge", "custom"}:
        return "gui: configured"
    try:
        inspection = docker.inspect(container_name)
        if inspection is None:
            return "gui: configured"
        endpoint = published_port(inspection, config.gui.container_port)
        if endpoint is None:
            return "gui: configured"
        host_ip, host_port = endpoint
        return f"gui: {gui_url(host_ip, host_port)}"
    except DockerError:
        return "gui: configured"


def _service_status_lines(config: AssessmentConfig, manager: ServiceManager, running: bool) -> list[str]:
    lines = []
    for service_id in sorted(set(config.services.enabled) | set(config.services.autostart)):
        try:
            detail = manager.status(service_id) if running else "stopped"
        except (ServiceError, DockerError):
            detail = "unavailable"
        lines.append(f"service {service_id}: {detail}")
    return lines


@app.command()
def status(context: typer.Context, name: AssessmentName) -> None:
    """Display the discovered primary-container state."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        manager = state.manager(config.docker.context)
        result = manager.status(config)
    except (WorkspaceError, DockerError, LifecycleError) as error:
        _abort_workspace(error)
    workspace = result.workspace
    typer.echo(f"name: {result.name}")
    typer.echo(f"state: {result.state}")
    typer.echo(f"image: {result.image}")
    typer.echo(f"workspace: {result.workspace}")
    typer.echo(f"privileged: {str(config.docker.privileged).lower()}")
    network = f"{config.docker.network.mode}" + (
        f" ({config.docker.network.name})" if config.docker.network.name else ""
    )
    typer.echo(f"network: {network}")
    if config.vpn.enabled:
        typer.echo(f"vpn: {_vpn_state(workspace)}")
    typer.echo(_gui_status_line(config, result.container_name, manager.docker, result.running))
    lines = _service_status_lines(
        config, state.services(config), result.running
    )
    for line in lines:
        typer.echo(line)
    recent = latest_session_id(workspace)
    typer.echo(f"recent session: {recent or 'none'}")


@app.command("list")
def list_assessments(context: typer.Context) -> None:
    """List persisted assessments and their Docker state."""
    state = _state(context)
    workspaces = state.root / "workspaces"
    if not workspaces.exists():
        return
    for workspace in sorted(workspaces.iterdir()):
        if not workspace.is_dir() or not (workspace / ".quiver.yaml").is_file():
            continue
        try:
            config = load_workspace_config(workspace.name, state.root)
            result = state.manager(config.docker.context).status(config)
            typer.echo(f"{config.name}\t{result.state}\t{result.image}")
        except (WorkspaceError, DockerError, LifecycleError) as error:
            typer.echo(f"{workspace.name}\terror\t{error}")


@app.command()
def shell(
    context: typer.Context,
    name: AssessmentName,
    asciinema: Annotated[bool, typer.Option(help="Use asciinema rather than script.")] = False,
) -> None:
    """Open a recorded interactive shell in a running assessment."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        exit_code = AuditManager(
            state.backend(config.docker.context)
        ).shell(config, asciinema)
    except (WorkspaceError, AuditError, DockerError) as error:
        _abort_workspace(error)
    if exit_code:
        raise typer.Exit(exit_code)


@app.command()
def exec(
    context: typer.Context,
    name: AssessmentName,
    command: Annotated[list[str], typer.Argument(help="Command and arguments, after --.")],
) -> None:
    """Execute a non-interactive command and persist its output artifacts."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        exit_code, artifact = AuditManager(
            state.backend(config.docker.context)
        ).exec(config, command)
    except (WorkspaceError, AuditError, DockerError) as error:
        _abort_workspace(error)
    typer.echo(artifact.with_suffix(".stdout").read_text(), nl=False)
    typer.echo(artifact.with_suffix(".stderr").read_text(), err=True, nl=False)
    typer.echo(f"exec log: {artifact}", err=True)
    if exit_code:
        raise typer.Exit(exit_code)


@app.command()
def logs(
    context: typer.Context,
    name: AssessmentName,
    session_id: Annotated[str | None, typer.Option("--session", help="Recorded session ID.")] = None,
) -> None:
    """List recorded sessions or show a session's searchable output."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        output = AuditManager(
            state.backend(config.docker.context)
        ).logs(config, session_id)
    except (WorkspaceError, AuditError) as error:
        _abort_workspace(error, fallback=7)
    if output:
        typer.echo(output, nl=not output.endswith("\n"))


@app.command()
def replay(
    context: typer.Context,
    name: AssessmentName,
    session_id: Annotated[str, typer.Argument(help="Recorded session ID.")],
) -> None:
    """Replay a locally stored terminal recording."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        exit_code = AuditManager(
            state.backend(config.docker.context)
        ).replay(config, session_id)
    except (WorkspaceError, AuditError) as error:
        _abort_workspace(error, fallback=7)
    if exit_code:
        raise typer.Exit(exit_code)


@app.command()
def gui(
    context: typer.Context,
    name: AssessmentName,
    no_open: Annotated[bool, typer.Option("--no-open", help="Do not open a browser.")] = False,
) -> None:
    """Start the internal noVNC desktop and display its local URL."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        with workspace_lock(name, state.root):
            access = GuiManager(state.manager(config.docker.context)).open(
                config, launch_browser=not no_open
            )
    except (GuiError, WorkspaceError, DockerError, LifecycleError) as error:
        _abort(error, 7 if isinstance(error, WorkspaceError) else 3)
    typer.echo(f"GUI URL: {access.url}")
    if access.warning:
        typer.echo(f"WARNING: {access.warning}")


@service_app.command("list")
def service_list(context: typer.Context, name: AssessmentName) -> None:
    """List built-in services and whether this assessment enables them."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        for service in state.services(config).list():
            enabled = "enabled" if service.enabled else "disabled"
            autostart = " autostart" if service.autostart else ""
            typer.echo(f"{service.id}\t{service.kind}\t{enabled}{autostart}")
    except (WorkspaceError, ServiceError) as error:
        _abort_workspace(error, fallback=6)


def _service_action(context: typer.Context, name: str, service_id: str, action: str) -> None:
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        with workspace_lock(name, state.root):
            result = getattr(state.services(config), action)(service_id)
    except (WorkspaceError, ServiceError, DockerError) as error:
        code = 7 if isinstance(error, WorkspaceError) else 6
        _abort(error, code)
    if result:
        typer.echo(result)


@service_app.command("up")
def service_up(
    context: typer.Context,
    name: AssessmentName,
    service_id: Annotated[
        str, typer.Argument(help="Service identifier.", autocompletion=service_completion)
    ],
) -> None:
    """Start a service."""
    _service_action(context, name, service_id, "up")


@service_app.command("down")
def service_down(
    context: typer.Context,
    name: AssessmentName,
    service_id: Annotated[str, typer.Argument(help="Service identifier.")],
) -> None:
    """Stop a service without deleting its persistent data."""
    _service_action(context, name, service_id, "down")


@service_app.command("restart")
def service_restart(
    context: typer.Context,
    name: AssessmentName,
    service_id: Annotated[str, typer.Argument(help="Service identifier.")],
) -> None:
    """Restart a service."""
    _service_action(context, name, service_id, "restart")


@service_app.command("status")
def service_status(
    context: typer.Context,
    name: AssessmentName,
    service_id: Annotated[str | None, typer.Argument(help="Optional service identifier.")] = None,
) -> None:
    """Show one service's status, or each enabled service when omitted."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        result = state.services(config).status(service_id)
    except (WorkspaceError, ServiceError, DockerError) as error:
        _abort(error, 6)
    if isinstance(result, dict):
        for item_id, item_status in result.items():
            typer.echo(f"{item_id}\t{item_status}")
    elif result:
        typer.echo(result)


@service_app.command("logs")
def service_logs(
    context: typer.Context,
    name: AssessmentName,
    service_id: Annotated[str, typer.Argument(help="Service identifier.")],
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Follow service logs.")] = False,
) -> None:
    """Show logs for an internal or Compose service."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        result = state.services(config).logs(service_id, follow)
    except (WorkspaceError, ServiceError, DockerError) as error:
        _abort(error, 6)
    if result:
        typer.echo(result)


@image_app.command("list")
def image_list(context: typer.Context) -> None:
    """List built-in image profiles and their default references."""
    state = _state(context)
    for profile, reference in ImageManager(state.backend()).list():
        typer.echo(f"{profile}\t{reference}")


@image_app.command("pull")
def image_pull(
    context: typer.Context,
    profile_or_reference: Annotated[str, typer.Argument(help="Profile name or image reference.")],
) -> None:
    """Pull an image profile or explicit registry reference."""
    state = _state(context)
    try:
        reference = ImageManager(state.backend()).pull(profile_or_reference)
    except (ImageError, DockerError) as error:
        _abort(error, 4)
    typer.echo(f"pulled {reference}")


@image_app.command("build")
def image_build(
    context: typer.Context,
    profile: Annotated[
        str, typer.Argument(help="Built-in profile name.", autocompletion=profile_completion)
    ],
    platform: Annotated[str | None, typer.Option(help="linux/amd64 or linux/arm64.")] = None,
) -> None:
    """Build a profile locally with Docker Buildx."""
    state = _state(context)
    if platform not in (None, "linux/amd64", "linux/arm64"):
        _abort(ImageError("platform must be linux/amd64 or linux/arm64"), 2)
    try:
        report = ImageManager(state.backend()).build(profile, platform)
    except (ImageError, DockerError) as error:
        _abort(error, 4)
    typer.echo(report.text())


@app.command()
def doctor(context: typer.Context) -> None:
    """Check the host Docker runtime and basic local prerequisites."""
    state = _state(context)
    checks = Doctor(state.backend(), state.root).checks()
    for check in checks:
        detail = f" — {check.remediation}" if check.remediation else ""
        typer.echo(f"{check.status:<4} {check.name}{detail}")
    if any(check.status == "FAIL" for check in checks):
        raise typer.Exit(3)


@app.command()
def destroy(
    context: typer.Context,
    name: AssessmentName,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the deletion confirmation.")] = False,
) -> None:
    """Delete all managed resources and the persistent workspace."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        workspace = Path(config.workspace.path)
        data_bytes = service_data_size(workspace)
        message = f"Delete assessment workspace {workspace}?"
        if data_bytes:
            message += f" Persistent service data size: {data_bytes} bytes."
        if not yes and not typer.confirm(message):
            raise typer.Abort()
        with workspace_lock(name, state.root):
            state.manager(config.docker.context).stop(config, all_services=True)
        shutil.rmtree(workspace)
    except typer.Abort:
        raise
    except WorkspaceError as error:
        _abort(error, 7)
    except (DockerError, LifecycleError, OSError) as error:
        _abort(error, 3)
    typer.echo(f"{name}: destroyed")


@workspace_app.command("fix-perms")
def workspace_fix_perms(
    context: typer.Context,
    name: AssessmentName,
) -> None:
    """Best-effort normalization of workspace ownership to the host UID/GID."""
    state = _state(context)
    try:
        config = load_workspace_config(name, state.root)
        with workspace_lock(name, state.root):
            state.manager(config.docker.context).repair_ownership(config)
    except (WorkspaceError, DockerError, LifecycleError) as error:
        _abort(error, 7 if isinstance(error, WorkspaceError) else 3)
    typer.echo(f"{name}: workspace ownership normalized")
