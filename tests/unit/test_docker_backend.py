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
