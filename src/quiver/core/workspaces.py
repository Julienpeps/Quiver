"""Persistent assessment workspace management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from quiver.models.config import (
    AssessmentConfig,
    ConfigError,
    load_config,
    write_config,
)

CONFIG_FILENAME = ".quiver.yaml"
LOCKS_DIRECTORY = "locks"


class WorkspaceError(RuntimeError):
    """Raised when a workspace cannot be safely accessed."""


def quiver_root(root: Path | None = None) -> Path:
    """Return the configured Quiver state root."""
    return (root or Path.home() / ".quiver").expanduser().resolve()


def assessment_workspace(name: str, root: Path | None = None) -> Path:
    """Return the canonical workspace location for an assessment name."""
    return quiver_root(root) / "workspaces" / name


def config_path(workspace: Path) -> Path:
    """Return the persisted configuration location within a workspace."""
    return workspace / CONFIG_FILENAME


def initialize_workspace(config: AssessmentConfig) -> Path:
    """Create a workspace and its Quiver-owned persistent directories."""
    workspace = Path(config.workspace.path).expanduser().resolve()
    configured_workspace = Path(config.workspace.path).expanduser()
    if workspace != configured_workspace:
        raise WorkspaceError("workspace.path must be canonical before initialization")

    existing_config = config_path(workspace)
    if existing_config.exists():
        raise WorkspaceError(f"assessment configuration already exists at {existing_config}")

    try:
        workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in (
            ".logs/sessions",
            ".logs/exec",
            ".logs/services",
            ".vpn",
            ".services",
            ".quiver/home",
        ):
            (workspace / directory).mkdir(mode=0o700, parents=True, exist_ok=True)
        write_config(existing_config, config)
    except (OSError, ConfigError) as error:
        raise WorkspaceError(f"could not initialize workspace {workspace}: {error}") from error

    return workspace


def load_workspace_config(name: str, root: Path | None = None) -> AssessmentConfig:
    """Load an assessment configuration from its canonical workspace."""
    workspace = assessment_workspace(name, root)
    path = config_path(workspace)
    if not path.is_file():
        raise WorkspaceError(f"assessment {name!r} does not exist")
    try:
        return load_config(path)
    except ConfigError as error:
        raise WorkspaceError(str(error)) from error


@contextmanager
def workspace_lock(name: str, root: Path | None = None, timeout_seconds: float = 0) -> Iterator[None]:
    """Serialize mutations under a lock that survives workspace deletion.

    The lock lives in ``<root>/locks/<assessment>.lock`` rather than inside the
    workspace so that ``destroy`` cannot race a concurrent operation onto a
    freshly created lock file.
    """
    locks_root = quiver_root(root) / LOCKS_DIRECTORY
    try:
        locks_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise WorkspaceError(f"could not create lock directory {locks_root}: {error}") from error
    lock = FileLock(locks_root / f"{name}.lock", timeout=timeout_seconds)
    try:
        with lock:
            yield
    except Timeout as error:
        raise WorkspaceError(f"assessment {name!r} is locked by another quiver process") from error


def latest_session_id(workspace: Path) -> str | None:
    """Return the newest recorded session ID, or None when nothing was recorded."""
    sessions = workspace / ".logs" / "sessions"
    if not sessions.is_dir():
        return None
    return max((entry.name for entry in sessions.iterdir() if entry.is_dir()), default=None)


def service_data_size(workspace: Path) -> int:
    """Total size in bytes of persistent external service data under .services."""
    services = workspace / ".services"
    if not services.is_dir():
        return 0
    return sum(entry.stat().st_size for entry in services.rglob("*") if entry.is_file())
