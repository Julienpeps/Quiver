"""Persistent host-side audit session artifacts."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

Recorder = Literal["script", "asciinema"]


@dataclass(frozen=True)
class SessionPaths:
    """The canonical on-disk paths for one interactive audit session."""

    session_id: str
    directory: Path
    metadata: Path
    commands: Path
    output_ansi: Path
    timing: Path
    output_text: Path
    cast: Path


def new_session_id(now: datetime | None = None) -> str:
    """Create a UTC-sortable session identifier with a collision-resistant suffix."""
    timestamp = now or datetime.now(UTC)
    return f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def session_paths(workspace: Path, session_id: str) -> SessionPaths:
    """Return paths for a session ID without creating any files."""
    directory = workspace / ".logs" / "sessions" / session_id
    return SessionPaths(
        session_id=session_id,
        directory=directory,
        metadata=directory / "metadata.json",
        commands=directory / "commands.jsonl",
        output_ansi=directory / "output.ansi",
        timing=directory / "timing.log",
        output_text=directory / "output.txt",
        cast=directory / "session.cast",
    )


def create_session(
    workspace: Path,
    metadata: dict[str, Any],
    session_id: str | None = None,
) -> SessionPaths:
    """Create a session directory and its initial, JSON-encoded metadata."""
    paths = session_paths(workspace, session_id or new_session_id())
    paths.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    payload = {"session_id": paths.session_id, **metadata}
    paths.metadata.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    paths.metadata.chmod(0o600)
    return paths


def append_command(
    paths: SessionPaths,
    *,
    timestamp: datetime,
    cwd: str,
    command: str,
    exit_code: int,
    duration_ms: int,
) -> None:
    """Append one safely JSON-encoded command event to an active session."""
    event = {
        "event": "command",
        "ts": timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "cwd": cwd,
        "command": command,
        "exit": exit_code,
        "duration_ms": duration_ms,
    }
    with paths.commands.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    paths.commands.chmod(0o600)


def update_metadata(paths: SessionPaths, **updates: Any) -> None:
    """Atomically merge end-of-session metadata without rewriting command events."""
    try:
        metadata = json.loads(paths.metadata.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read session metadata: {error}") from error
    if not isinstance(metadata, dict):
        raise TypeError("session metadata must be a JSON object")
    metadata.update(updates)
    temporary = paths.metadata.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(paths.metadata)


def replay_command(paths: SessionPaths, recorder: Recorder) -> list[str]:
    """Build the faithful local replay command for a completed recording."""
    if recorder == "script":
        return ["scriptreplay", str(paths.timing), str(paths.output_ansi)]
    return ["asciinema", "play", str(paths.cast)]
