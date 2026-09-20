"""End-to-end lifecycle tests against a real Docker daemon.

Skipped automatically when Docker (Linux containers) is unavailable.
Covers acceptance criteria 1-4 and 6: start/stop/destroy semantics,
workspace persistence, and disposable overlay behavior.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from quiver.cli import app

pytestmark = pytest.mark.integration

IMAGE_NAME = "quiver-e2e-image:latest"
ASSESSMENT = "e2e-demo"


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    result = _docker("version", "--format", "{{.Server.Os}}")
    return result.returncode == 0 and result.stdout.strip().lower() == "linux"


@pytest.fixture(scope="module")
def image_ref() -> str:
    subprocess.run(
        ["docker", "build", "-q", "-t", "quiver-e2e-image", str(Path(__file__).parents[1] / "fixtures" / "e2e")],
        check=True,
    )
    yield IMAGE_NAME
    _docker("rmi", "quiver-e2e-image")


def _invoke(args: list[str], root: Path):
    return CliRunner().invoke(app, ["--root", str(root), *args])


@pytest.mark.skipif(not docker_available(), reason="Docker with Linux containers is unavailable")
def test_lifecycle_round_trip(image_ref: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    root = tmp_path_factory.mktemp("quiver")
    result = _invoke(["start", ASSESSMENT, "--image", image_ref, "--detach"], root)
    assert result.exit_code == 0, result.output

    assert _docker("inspect", "-f", "{{.State.Running}}", f"quiver-{ASSESSMENT}").stdout.strip() == "true"
    labels = _docker("inspect", "-f", "{{json .Config.Labels}}", f"quiver-{ASSESSMENT}").stdout
    assert "io.quiver.managed" in labels

    workspace = root / "workspaces" / ASSESSMENT
    (workspace / "assessment.txt").write_text("state\n")
    assert _docker("exec", f"quiver-{ASSESSMENT}", "test", "-f", "/workspace/assessment.txt").returncode == 0
    assert _docker("exec", f"quiver-{ASSESSMENT}", "touch", "/quiver-overlay-test").returncode == 0

    result = _invoke(["stop", ASSESSMENT], root)
    assert result.exit_code == 0, result.output
    assert _docker("inspect", f"quiver-{ASSESSMENT}").returncode != 0
    assert (workspace / "assessment.txt").is_file()

    result = _invoke(["start", ASSESSMENT, "--detach"], root)
    assert result.exit_code == 0, result.output
    assert _docker("exec", f"quiver-{ASSESSMENT}", "test", "-f", "/workspace/assessment.txt").returncode == 0
    assert _docker("exec", f"quiver-{ASSESSMENT}", "test", "-f", "/quiver-overlay-test").returncode != 0

    result = _invoke(["status", ASSESSMENT], root)
    assert result.exit_code == 0, result.output
    assert "network: bridge" in result.output
    assert "privileged: false" in result.output

    result = _invoke(["destroy", ASSESSMENT, "--yes"], root)
    assert result.exit_code == 0, result.output
    assert not workspace.exists()
    assert _docker("inspect", f"quiver-{ASSESSMENT}").returncode != 0
    assert _docker("network", "inspect", f"quiver-{ASSESSMENT}").returncode != 0
