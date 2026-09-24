"""Fixed global dotfiles directory for interactive Quiver shells."""

from __future__ import annotations

from pathlib import Path

from quiver.core.workspaces import WorkspaceError, quiver_root

DOTFILES_DIRECTORY = "dotfiles"


def dotfiles_directory(root: Path | None = None) -> Path:
    """Return the host directory mounted as ``~/.config`` in Quiver shells."""
    return quiver_root(root) / DOTFILES_DIRECTORY


def ensure_dotfiles_directory(root: Path | None = None) -> Path:
    """Create the fixed host dotfiles directory with private host permissions."""
    directory = dotfiles_directory(root)
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as error:
        raise WorkspaceError(f"could not create dotfiles directory {directory}: {error}") from error
    return directory
