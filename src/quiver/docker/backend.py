"""A small, deterministic adapter around the installed Docker CLI."""

from __future__ import annotations

import json
import os
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
        self._docker_plugins: set[str] = set()
        self._standalone_plugins: set[str] = set()

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
        return self._execute(
            self.command(*args),
            self.format_command(args, secrets),
            check=check,
            input=input,
            stream=stream,
        )

    def _execute(
        self,
        command: list[str],
        rendered: str,
        *,
        check: bool,
        input: str | None = None,
        stream: bool = False,
        environment: dict[str, str] | None = None,
    ) -> CommandResult:
        """Run one concrete command and translate executable/exit failures."""
        if self.on_command is not None:
            self.on_command(rendered)
        try:
            completed = self._runner(
                command,
                check=False,
                input=input,
                capture_output=not stream,
                text=True,
                **({"env": environment} if environment is not None else {}),
            )
        except FileNotFoundError as error:
            raise DockerUnavailableError(f"Docker executable {command[0]!r} was not found") from error
        except OSError as error:
            raise DockerUnavailableError(f"could not execute Docker: {error}") from error

        result = CommandResult(
            args=tuple(command),
            returncode=completed.returncode,
            stdout="" if stream else completed.stdout,
            stderr="" if stream else completed.stderr,
        )
        if check and result.returncode:
            self._raise_for_result(result)
        return result

    @staticmethod
    def _raise_for_result(result: CommandResult) -> None:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown Docker error"
        raise DockerError(f"Docker command failed ({result.returncode}): {detail}")

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

    def buildx(self, *args: str, check: bool = True, stream: bool = False) -> CommandResult:
        """Run Buildx, falling back to a standalone ``docker-buildx`` binary."""
        return self._plugin("buildx", *args, check=check, stream=stream)

    def compose(self, *args: str, check: bool = True, stream: bool = False) -> CommandResult:
        """Run Compose, falling back to a standalone ``docker-compose`` binary."""
        return self._plugin("compose", *args, check=check, stream=stream)

    def _plugin(self, plugin: str, *args: str, check: bool, stream: bool) -> CommandResult:
        """Run a Docker CLI plugin, accommodating standalone Homebrew binaries."""
        if plugin in self._standalone_plugins:
            return self._run_standalone_plugin(plugin, *args, check=check, stream=stream)
        if stream and plugin not in self._docker_plugins:
            # Probe quietly before a long-running command so an unavailable CLI
            # plugin does not emit an error before the standalone fallback runs.
            probe = self.run(plugin, "version", check=False)
            if probe.returncode == 0:
                self._docker_plugins.add(plugin)
                return self.run(plugin, *args, check=check, stream=True)
            if self._plugin_unavailable(probe) and shutil.which(f"{self.executable}-{plugin}"):
                self._standalone_plugins.add(plugin)
                return self._run_standalone_plugin(plugin, *args, check=check, stream=True)
            if check:
                self._raise_for_result(probe)
            return probe

        result = self.run(plugin, *args, check=False, stream=stream)
        standalone = f"{self.executable}-{plugin}"
        if (
            result.returncode
            and self._plugin_unavailable(result)
            and shutil.which(standalone) is not None
        ):
            self._standalone_plugins.add(plugin)
            return self._run_standalone_plugin(plugin, *args, check=check, stream=stream)
        if check and result.returncode:
            self._raise_for_result(result)
        if result.returncode == 0:
            self._docker_plugins.add(plugin)
        return result

    @staticmethod
    def _plugin_unavailable(result: CommandResult) -> bool:
        detail = f"{result.stdout}\n{result.stderr}".lower()
        return "unknown command" in detail or "not a docker command" in detail

    def _run_standalone_plugin(
        self, plugin: str, *args: str, check: bool, stream: bool
    ) -> CommandResult:
        executable = f"{self.executable}-{plugin}"
        environment = dict(os.environ)
        if self.context:
            environment["DOCKER_CONTEXT"] = self.context
        prefix = f"DOCKER_CONTEXT={self.context} " if self.context else ""
        return self._execute(
            [executable, *args],
            prefix + " ".join([executable, *args]),
            check=check,
            stream=stream,
            environment=environment,
        )

    def format_command(self, args: Sequence[str], secrets: Sequence[str] = ()) -> str:
        """Render an invocation for diagnostics without exposing supplied secrets."""
        command = " ".join(self.command(*args))
        for secret in secrets:
            if secret:
                command = command.replace(secret, "***")
        return command
