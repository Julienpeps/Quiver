import subprocess
from typing import Any

import pytest

from quiver.docker.backend import DockerBackend, DockerError, DockerUnavailableError
from quiver.docker.inspect import container_state, published_port


def fake_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, 0, '{"Server": {"Os": "linux"}}', "")


def test_backend_applies_context_to_every_command() -> None:
    backend = DockerBackend(context="testing", runner=fake_runner)

    assert backend.command("ps") == ["docker", "--context", "testing", "ps"]
    assert backend.version()["Server"]["Os"] == "linux"


def test_backend_translates_missing_executable() -> None:
    def missing_runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    with pytest.raises(DockerUnavailableError):
        DockerBackend(runner=missing_runner).run("version")


def test_backend_runs_interactive_commands_without_capturing_streams() -> None:
    seen: list[list[str]] = []

    def interactive(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[object]:
        seen.append(args)
        assert kwargs == {"check": False}
        return subprocess.CompletedProcess(args, 23)

    assert DockerBackend(context="testing", interactive_runner=interactive).run_interactive("exec", "demo") == 23
    assert seen == [["docker", "--context", "testing", "exec", "demo"]]


@pytest.mark.parametrize("plugin", ["buildx", "compose"])
def test_backend_falls_back_to_standalone_plugin_with_context(
    plugin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[list[str], dict[str, Any]]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append((args, kwargs))
        if args[0] == "docker" and args[-2:] == [plugin, "version"]:
            return subprocess.CompletedProcess(args, 1, "", f"unknown command: docker {plugin}")
        return subprocess.CompletedProcess(args, 0, f"{plugin} works", "")

    monkeypatch.setattr(
        "quiver.docker.backend.shutil.which",
        lambda executable: f"/opt/homebrew/bin/{executable}" if executable == f"docker-{plugin}" else None,
    )
    backend = DockerBackend(context="testing", runner=runner)

    assert getattr(backend, plugin)("version").stdout == f"{plugin} works"
    assert commands[0][0] == ["docker", "--context", "testing", plugin, "version"]
    assert commands[1][0] == [f"docker-{plugin}", "version"]
    assert commands[1][1]["env"]["DOCKER_CONTEXT"] == "testing"

    getattr(backend, plugin)("version")
    assert len(commands) == 3
    assert commands[-1][0] == [f"docker-{plugin}", "version"]


def test_streaming_plugin_probes_before_standalone_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[0] == "docker":
            assert args[-2:] == ["compose", "version"]
            return subprocess.CompletedProcess(args, 1, "", "unknown command: docker compose")
        assert kwargs["capture_output"] is False
        return subprocess.CompletedProcess(args, 0, None, None)

    monkeypatch.setattr(
        "quiver.docker.backend.shutil.which",
        lambda executable: "/opt/homebrew/bin/docker-compose" if executable == "docker-compose" else None,
    )

    DockerBackend(runner=runner).compose("logs", "--follow", stream=True)

    assert commands == [
        ["docker", "compose", "version"],
        ["docker-compose", "logs", "--follow"],
    ]


def test_backend_rejects_invalid_json() -> None:
    def invalid_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, "not json", "")

    with pytest.raises(DockerError, match="valid JSON"):
        DockerBackend(runner=invalid_runner).json("version")


def test_inspect_helpers_extract_container_and_dynamic_port() -> None:
    inspection = {
        "Id": "abc123",
        "Name": "/quiver-demo",
        "State": {"Running": True, "Status": "running"},
        "Config": {
            "Image": "quiver-base",
            "Labels": {"io.quiver.managed": "true"},
        },
        "NetworkSettings": {
            "Ports": {"6080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49152"}]},
        },
    }

    state = container_state(inspection)

    assert state.name == "quiver-demo"
    assert state.managed is True
    assert published_port(inspection, 6080) == ("127.0.0.1", 49152)
