"""Host-side orchestration of audit artifacts and Docker exec commands."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from quiver.audit.sessions import (
    Recorder,
    create_session,
    replay_command,
    session_paths,
)
from quiver.core.lifecycle import primary_container_name
from quiver.docker.backend import DockerBackend, DockerError
from quiver.models.config import AssessmentConfig


class AuditError(RuntimeError):
    """Raised when audit artifacts or replay operations cannot be completed."""


ProcessRunner = Callable[..., subprocess.CompletedProcess[object]]


class AuditManager:
    """Run audited sessions and retain non-interactive Docker exec output."""

    def __init__(
        self,
        docker: DockerBackend,
        process_runner: ProcessRunner = subprocess.run,
    ) -> None:
        self.docker = docker
        self._process_runner = process_runner

    def shell(self, config: AssessmentConfig, asciinema: bool = False) -> int:
        """Attach an interactive container shell through its recording wrapper."""
        recorder: Recorder = "asciinema" if asciinema else config.logging.shell_recorder
        session_id = _session_id()
        if config.logging.enabled:
            self._create_session_metadata(config, session_id, recorder)
        return self.docker.run_interactive(
            "exec",
            "--interactive",
            "--tty",
            *_host_user_args(),
            "--env",
            f"HOME={config.workspace.container_path}/.quiver/home",
            "--env",
            "SHELL=/usr/bin/fish",
            "--env",
            f"QUIVER_SESSION_ID={session_id}",
            "--env",
            f"QUIVER_RECORDER={recorder}",
            primary_container_name(config.name),
            "/usr/local/bin/quiver-record-shell",
        )

    def _create_session_metadata(self, config: AssessmentConfig, session_id: str, recorder: Recorder) -> None:
        """Record host-known provenance before the session starts (spec 14.2)."""
        try:
            inspection = self.docker.inspect(primary_container_name(config.name)) or {}
        except DockerError:
            inspection = {}
        metadata = {
            "assessment": config.name,
            "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "ended_at": None,
            "container_image": str(inspection.get("Image") or inspection.get("Config", {}).get("Image", "")),
            "container_id": str(inspection.get("Id", "")),
            "shell": "/bin/zsh",
            "recorder": recorder,
            "host": socket.gethostname(),
            "docker_context": self.docker.context or "default",
        }
        try:
            create_session(Path(config.workspace.path), metadata, session_id)
        except OSError:
            pass

    def exec(self, config: AssessmentConfig, command: list[str]) -> tuple[int, Path]:
        """Run a non-interactive command and save stdout/stderr plus metadata."""
        if not command:
            raise AuditError("exec requires a command after --")
        started = datetime.now(UTC)
        result = self.docker.run(
            "exec",
            *_host_user_args(),
            "--env",
            f"HOME={config.workspace.container_path}/.quiver/home",
            "--env",
            "SHELL=/usr/bin/fish",
            primary_container_name(config.name),
            *command,
            check=False,
        )
        ended = datetime.now(UTC)
        artifacts = self._write_exec_artifacts(
            Path(config.workspace.path), command, started, ended, result.returncode, result.stdout, result.stderr
        )
        return result.returncode, artifacts

    def logs(self, config: AssessmentConfig, session_id: str | None = None) -> str:
        """List sessions or return the normalized output of one recorded session."""
        sessions = Path(config.workspace.path) / ".logs" / "sessions"
        if session_id is None:
            if not sessions.is_dir():
                return ""
            return "\n".join(sorted(entry.name for entry in sessions.iterdir() if entry.is_dir()))
        paths = session_paths(Path(config.workspace.path), session_id)
        if not paths.directory.is_dir():
            raise AuditError(f"unknown session {session_id!r}")
        for path in (paths.output_text, paths.output_ansi, paths.cast):
            if path.is_file():
                return path.read_text(errors="replace")
        raise AuditError(f"session {session_id!r} has no recording output")

    def replay(self, config: AssessmentConfig, session_id: str) -> int:
        """Invoke the appropriate local replay tool for a completed session."""
        paths = session_paths(Path(config.workspace.path), session_id)
        if not paths.metadata.is_file():
            raise AuditError(f"unknown session {session_id!r}")
        try:
            metadata = json.loads(paths.metadata.read_text())
            recorder = metadata["recorder"]
        except (OSError, json.JSONDecodeError, KeyError) as error:
            raise AuditError(f"invalid metadata for session {session_id!r}: {error}") from error
        if recorder not in ("script", "asciinema"):
            raise AuditError(f"unsupported recorder in session {session_id!r}: {recorder!r}")
        command = replay_command(paths, recorder)
        try:
            completed = self._process_runner(command, check=False)
        except FileNotFoundError as error:
            raise AuditError(f"replay tool is not installed: {command[0]}") from error
        return completed.returncode

    @staticmethod
    def _write_exec_artifacts(
        workspace: Path,
        command: list[str],
        started: datetime,
        ended: datetime,
        exit_code: int,
        stdout: str,
        stderr: str,
    ) -> Path:
        directory = workspace / ".logs" / "exec"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        stem = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
        record = directory / f"{stem}.json"
        record.with_suffix(".stdout").write_text(stdout)
        record.with_suffix(".stderr").write_text(stderr)
        payload = {
            "command": command,
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "ended_at": ended.isoformat().replace("+00:00", "Z"),
            "exit_code": exit_code,
            "working_directory": "/workspace",
        }
        record.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        for path in (record, record.with_suffix(".stdout"), record.with_suffix(".stderr")):
            path.chmod(0o600)
        return record


def _host_user_args() -> tuple[str, ...]:
    """Return Docker exec user flags for the invoking POSIX user when available."""
    try:
        return ("--user", f"{os.getuid()}:{os.getgid()}")
    except AttributeError:
        return ()


def _session_id() -> str:
    now = datetime.now(UTC)
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"
