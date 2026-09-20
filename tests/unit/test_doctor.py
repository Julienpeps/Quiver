import subprocess
from pathlib import Path
from typing import Any

from quiver.core.doctor import Doctor
from quiver.docker.backend import DockerBackend


def runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    if args[-2:] == ["buildx", "version"]:
        return subprocess.CompletedProcess(args, 0, "buildx", "")
    if args[-2:] == ["compose", "version"]:
        return subprocess.CompletedProcess(args, 0, "compose", "")
    if args[1:3] == ["context", "inspect"]:
        return subprocess.CompletedProcess(
            args,
            0,
            '{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}',
            "",
        )
    return subprocess.CompletedProcess(args, 0, '{"Server": {"Os": "linux", "Arch": "arm64"}}', "")


def test_doctor_reports_supported_runtime(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("quiver.docker.backend.shutil.which", lambda executable: "/docker")

    checks = Doctor(DockerBackend(runner=runner), tmp_path).checks()

    assert all(check.status == "OK" for check in checks)
    assert {check.name for check in checks} >= {
        "Docker daemon OS",
        "Docker daemon architecture",
        "Docker context locality",
        "Buildx",
        "Compose v2",
        "Container probe",
        "TUN/VPN capability",
    }


def test_doctor_rejects_remote_docker_context(tmp_path: Path, monkeypatch: Any) -> None:
    def remote_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if args[1:3] == ["context", "inspect"]:
            return subprocess.CompletedProcess(
                args,
                0,
                '{"Endpoints": {"docker": {"Host": "ssh://docker.example.test"}}}',
                "",
            )
        return runner(args, **kwargs)

    monkeypatch.setattr("quiver.docker.backend.shutil.which", lambda executable: "/docker")

    checks = Doctor(DockerBackend(runner=remote_runner), tmp_path).checks()

    locality = next(check for check in checks if check.name == "Docker context locality")
    assert locality.status == "FAIL"
    assert "remote" in locality.remediation


def test_doctor_reports_tun_failure_as_warning(tmp_path: Path, monkeypatch: Any) -> None:
    def no_tun_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "/dev/net/tun" in args:
            return subprocess.CompletedProcess(args, 1, "", "TUN unavailable")
        return runner(args, **kwargs)

    monkeypatch.setattr("quiver.docker.backend.shutil.which", lambda executable: "/docker")

    checks = Doctor(DockerBackend(runner=no_tun_runner), tmp_path).checks()

    tun = next(check for check in checks if check.name == "TUN/VPN capability")
    assert tun.status == "WARN"
