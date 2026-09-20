"""Host and Docker capability checks used before creating assessments."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quiver.docker.backend import DockerBackend, DockerError


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    remediation: str


class Doctor:
    """Run disposable, safely mockable host capability checks."""

    def __init__(self, docker: DockerBackend, root: Path) -> None:
        self.docker = docker
        self.root = root

    def checks(self) -> list[Check]:
        results = [self._python(), self._docker_executable(), self._workspace()]
        if results[1].status == "FAIL":
            return results
        try:
            version = self.docker.version()
        except DockerError as error:
            results.append(Check("Docker daemon", "FAIL", str(error)))
            return results
        server = _server(version)
        os_name = str(server.get("Os", "unknown"))
        results.append(
            Check(
                "Docker daemon OS",
                "OK" if os_name.lower() == "linux" else "FAIL",
                "Use a Docker daemon configured for Linux containers.",
            )
        )
        architecture = str(server.get("Arch") or server.get("Architecture") or "unknown")
        results.append(Check("Docker daemon architecture", "OK", architecture))
        results.append(self._local_context())
        results.append(self._buildx())
        results.append(self._compose())
        if os_name.lower() == "linux":
            results.append(self._probe_container())
            results.append(self._tun_probe())
        return results

    def _python(self) -> Check:
        """The project's Python requirement guarantees this at runtime."""
        return Check("Python", "OK", "")

    def _docker_executable(self) -> Check:
        if self.docker.is_available():
            return Check("Docker executable", "OK", "")
        return Check("Docker executable", "FAIL", "Install Docker and ensure it is on PATH.")

    def _workspace(self) -> Check:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            probe = self.root / ".write-probe"
            probe.write_text("")
            probe.unlink()
        except OSError as error:
            return Check("Workspace root", "FAIL", f"Choose a writable --root path: {error}")
        return Check("Workspace root", "OK", "")

    def _local_context(self) -> Check:
        try:
            data = self.docker.json("context", "inspect", "--format", "{{json .}}")
        except DockerError as error:
            return Check("Docker context locality", "WARN", f"Could not inspect context: {error}")
        host = _context_host(data)
        if host.startswith(("unix://", "npipe://")):
            return Check("Docker context locality", "OK", host)
        return Check(
            "Docker context locality",
            "FAIL",
            f"{host or 'unknown endpoint'} is remote; workspace bind mounts require a local daemon.",
        )

    def _probe_container(self) -> Check:
        try:
            self.docker.run("run", "--rm", "busybox:1.36", "true")
        except DockerError as error:
            return Check("Container probe", "FAIL", f"Could not run a disposable container: {error}")
        return Check("Container probe", "OK", "")

    def _tun_probe(self) -> Check:
        try:
            self.docker.run(
                "run",
                "--rm",
                "--cap-add",
                "NET_ADMIN",
                "--device",
                "/dev/net/tun",
                "busybox:1.36",
                "sh",
                "-ec",
                "test -c /dev/net/tun",
            )
        except DockerError as error:
            return Check(
                "TUN/VPN capability",
                "WARN",
                f"TUN probe failed; VPN assessments may not work: {error}",
            )
        return Check("TUN/VPN capability", "OK", "")

    def _buildx(self) -> Check:
        try:
            self.docker.buildx("version")
        except DockerError as error:
            return Check("Buildx", "FAIL", str(error))
        return Check("Buildx", "OK", "")

    def _compose(self) -> Check:
        try:
            self.docker.compose("version")
        except DockerError as error:
            return Check("Compose v2", "FAIL", str(error))
        return Check("Compose v2", "OK", "")


def _server(version: dict[str, Any]) -> dict[str, Any]:
    server = version.get("Server") or {}
    return server if isinstance(server, dict) else {}


def _context_host(data: Any) -> str:
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return ""
    endpoints = data.get("Endpoints") or {}
    if not isinstance(endpoints, dict):
        return ""
    docker = endpoints.get("docker") or endpoints.get("Docker") or {}
    return str(docker.get("Host", "")) if isinstance(docker, dict) else ""
