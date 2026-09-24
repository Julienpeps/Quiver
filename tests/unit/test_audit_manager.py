import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from quiver.audit.manager import AuditError, AuditManager
from quiver.audit.sessions import create_session
from quiver.docker.backend import DockerBackend
from quiver.models.config import AssessmentConfig


def config(tmp_path: Path) -> AssessmentConfig:
    return AssessmentConfig.model_validate(
        {
            "schema_version": 1,
            "name": "demo",
            "image": {"profile": "base"},
            "workspace": {"path": str(tmp_path)},
        }
    )


def test_shell_uses_container_recorder_wrapper(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def interactive(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[object]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0)

    exit_code = AuditManager(
        DockerBackend(interactive_runner=interactive)
    ).shell(config(tmp_path), asciinema=True)

    assert exit_code == 0
    command = commands[0]
    assert command[1:7] == ["exec", "--interactive", "--tty", "--user", f"{os.getuid()}:{os.getgid()}", "--env"]
    assert "HOME=/workspace/.quiver/home" in command
    assert "SHELL=/usr/bin/fish" in command
    assert "QUIVER_RECORDER=asciinema" in command
    assert command[-2:] == ["quiver-demo", "/usr/local/bin/quiver-record-shell"]


def test_exec_captures_stdout_stderr_and_exit_metadata(tmp_path: Path) -> None:
    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 7, "normal output", "error output")

    exit_code, record = AuditManager(DockerBackend(runner=runner)).exec(
        config(tmp_path), ["sh", "-c", "exit 7"]
    )

    assert exit_code == 7
    assert record.with_suffix(".stdout").read_text() == "normal output"
    assert record.with_suffix(".stderr").read_text() == "error output"
    assert json.loads(record.read_text())["exit_code"] == 7


def test_logs_and_replay_use_local_session_artifacts(tmp_path: Path) -> None:
    session = create_session(tmp_path, {"recorder": "script"}, "recorded")
    session.output_text.write_text("searchable\n")
    seen: list[list[str]] = []

    def process_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[object]:
        seen.append(args)
        return subprocess.CompletedProcess(args, 0)

    manager = AuditManager(DockerBackend(), process_runner=process_runner)

    assert manager.logs(config(tmp_path)) == "recorded"
    assert manager.logs(config(tmp_path), "recorded") == "searchable\n"
    assert manager.replay(config(tmp_path), "recorded") == 0
    assert seen == [["scriptreplay", str(session.timing), str(session.output_ansi)]]


def test_replay_rejects_unknown_or_invalid_sessions(tmp_path: Path) -> None:
    manager = AuditManager(DockerBackend())

    with pytest.raises(AuditError, match="unknown"):
        manager.replay(config(tmp_path), "missing")
