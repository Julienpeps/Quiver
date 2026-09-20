"""Host-side noVNC access and per-assessment credential management."""

from __future__ import annotations

import secrets
import string
import time
from dataclasses import dataclass
from pathlib import Path

from quiver.core.browser import BrowserLauncher
from quiver.core.lifecycle import LifecycleManager, primary_container_name
from quiver.docker.backend import DockerError
from quiver.docker.inspect import published_port
from quiver.models.config import AssessmentConfig

GUI_PASSWORD_FILENAME = "gui-password"


class GuiError(RuntimeError):
    """Raised when Quiver cannot start or expose the local GUI."""


@dataclass(frozen=True)
class GuiAccess:
    url: str
    password: str | None
    warning: str | None


def gui_password_path(config: AssessmentConfig) -> Path:
    """Return the implementation-owned GUI credential location."""
    return Path(config.workspace.path) / ".services" / GUI_PASSWORD_FILENAME


def ensure_gui_credential(config: AssessmentConfig) -> str | None:
    """Create a restrictive per-assessment VNC password when auth is enabled."""
    if not config.gui.authentication:
        return None
    path = gui_password_path(config)
    try:
        if path.is_file():
            return path.read_text().strip()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        alphabet = string.ascii_letters + string.digits
        password = "".join(secrets.choice(alphabet) for _ in range(8))
        path.write_text(f"{password}\n")
        path.chmod(0o600)
        return password
    except OSError as error:
        raise GuiError(f"could not create GUI credential {path}: {error}") from error


def gui_url(host_ip: str, host_port: int) -> str:
    """Build the noVNC URL, including resize and clipboard-safe defaults."""
    return f"http://{host_ip}:{host_port}/vnc.html?autoconnect=true&resize=remote"


class GuiManager:
    """Coordinate an on-demand internal GUI Supervisor service."""

    def __init__(self, lifecycle: LifecycleManager) -> None:
        self.lifecycle = lifecycle
        self.docker = lifecycle.docker

    def open(
        self,
        config: AssessmentConfig,
        *,
        launch_browser: bool = True,
        browser: BrowserLauncher | None = None,
    ) -> GuiAccess:
        """Ensure the primary container and GUI service are ready for local access."""
        if not config.gui.enabled:
            raise GuiError("GUI is disabled; enable it with quiver edit or start --gui")
        if config.docker.network.mode == "none":
            raise GuiError("GUI is unavailable with docker.network.mode=none")
        password = ensure_gui_credential(config)
        status = self.lifecycle.status(config)
        if not status.running:
            self.lifecycle.start(config)
        name = primary_container_name(config.name)
        self._start_and_wait(name)
        host_ip, host_port = self._endpoint(config, name)
        warning = None
        if host_ip not in {"127.0.0.1", "::1", "localhost"}:
            warning = f"GUI is exposed on non-loopback address {host_ip}"
        access = GuiAccess(gui_url(host_ip, host_port), password, warning)
        if launch_browser:
            (browser or BrowserLauncher()).open(access.url)
        return access

    def _start_and_wait(self, name: str) -> None:
        result = self.docker.run("exec", name, "supervisorctl", "start", "gui", check=False)
        if result.returncode and "already started" not in result.stdout.lower():
            raise GuiError(result.stderr.strip() or result.stdout.strip() or "could not start GUI service")
        for _ in range(20):
            status = self.docker.run("exec", name, "supervisorctl", "status", "gui", check=False)
            if status.returncode == 0 and "RUNNING" in status.stdout:
                return
            time.sleep(0.25)
        raise GuiError("GUI service did not become healthy")

    def _endpoint(self, config: AssessmentConfig, name: str) -> tuple[str, int]:
        if config.docker.network.mode == "host":
            return config.gui.host_ip, config.gui.container_port
        try:
            inspection = self.docker.inspect(name)
        except DockerError as error:
            raise GuiError(str(error)) from error
        if inspection is None:
            raise GuiError("primary container disappeared before GUI port discovery")
        endpoint = published_port(inspection, config.gui.container_port)
        if endpoint is None:
            raise GuiError("GUI port is not published; recreate the assessment")
        return endpoint
