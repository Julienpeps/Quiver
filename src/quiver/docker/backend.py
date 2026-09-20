"""A small, deterministic adapter around the installed Docker CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any


class DockerError(RuntimeError):
    """Base error for Docker CLI failures."""


class DockerUnavailableError(DockerError):
    """Docker is not installed or its daemon cannot be reached."""


class DockerConflictError(DockerError):
    """A Docker resource conflicts with a Quiver-managed resource."""


@dataclass(frozen=True)
class CommandResult:
    """Captured result of a Docker CLI invocation."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[..., subprocess.CompletedProcess[str]]
InteractiveRunner = Callable[..., subprocess.CompletedProcess[object]]
CommandObserver = Callable[[str], None]


class DockerBackend:
    """Execute Docker commands without changing the user's selected context."""

    def __init__(
        self,
        context: str | None = None,
        executable: str = "docker",
        runner: Runner = subprocess.run,
        interactive_runner: InteractiveRunner = subprocess.run,
        on_command: CommandObserver | None = None,
    ) -> None:
        self.context = context
        self.executable = executable
        self._runner = runner
        self._interactive_runner = interactive_runner
        self.on_command = on_command

    def command(self, *args: str) -> list[str]:
        """Build a Docker command, applying the invocation-scoped context."""
        command = [self.executable]
        if self.context:
            command.extend(("--context", self.context))
        command.extend(args)
        return command

    def is_available(self) -> bool:
        """Return whether the configured Docker executable can be found."""
        return shutil.which(self.executable) is not None

    def run(
        self,
        *args: str,
        check: bool = True,
        input: str | None = None,
        secrets: Sequence[str] = (),
        stream: bool = False,
    ) -> CommandResult:
        """Run Docker with captured text output and translate execution failures.

        With ``stream`` the child process inherits this process's standard output
        and error so long-running output (for example ``logs --follow``) is not
        buffered in memory.
        """
        command = self.command(*args)
        if self.on_command is not None:
            self.on_command(self.format_command(args, secrets))
        try:
            completed = self._runner(
                command,
                check=False,
                input=input,
                capture_output=not stream,
                text=True,
            )
        except FileNotFoundError as error:
            raise DockerUnavailableError(
                f"Docker executable {self.executable!r} was not found"
            ) from error
        except OSError as error:
            raise DockerUnavailableError(f"could not execute Docker: {error}") from error

        result = CommandResult(
            args=tuple(command),
            returncode=completed.returncode,
            stdout="" if stream else completed.stdout,
            stderr="" if stream else completed.stderr,
        )
        if check and result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown Docker error"
            raise DockerError(f"Docker command failed ({result.returncode}): {detail}")
        return result

    def run_interactive(self, *args: str) -> int:
        """Run Docker attached to this process's standard streams."""
        command = self.command(*args)
        try:
            completed = self._interactive_runner(command, check=False)
        except FileNotFoundError as error:
            raise DockerUnavailableError(
                f"Docker executable {self.executable!r} was not found"
            ) from error
        except OSError as error:
            raise DockerUnavailableError(f"could not execute Docker: {error}") from error
        return completed.returncode

    def json(self, *args: str) -> Any:
        """Run Docker and decode a JSON result."""
        result = self.run(*args)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DockerError(f"Docker did not return valid JSON: {error}") from error

    def version(self) -> dict[str, Any]:
        """Return client/server version information."""
        data = self.json("version", "--format", "{{json .}}")
        if not isinstance(data, dict):
            raise DockerError("Docker version response must be an object")
        return data

    def inspect(self, resource: str) -> dict[str, Any] | None:
        """Inspect one resource, returning None when it does not exist."""
        result = self.run("inspect", resource, check=False)
        if result.returncode:
            lowered = result.stderr.lower()
            if "no such" in lowered or "not found" in lowered or "is not a container" in lowered:
                return None
            raise DockerError(result.stderr.strip() or f"could not inspect {resource}")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise DockerError(f"Docker inspect did not return valid JSON: {error}") from error
        if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
            raise DockerError("Docker inspect response must be a one-item object list")
        return data[0]

    def compose(self, *args: str, check: bool = True, stream: bool = False) -> CommandResult:
        """Run Docker Compose v2 with the same invocation-scoped Docker context."""
        return self.run("compose", *args, check=check, stream=stream)

    def format_command(self, args: Sequence[str], secrets: Sequence[str] = ()) -> str:
        """Render an invocation for diagnostics without exposing supplied secrets."""
        command = " ".join(self.command(*args))
        for secret in secrets:
            if secret:
                command = command.replace(secret, "***")
        return command
