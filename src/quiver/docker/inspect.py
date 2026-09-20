"""Typed views over Docker inspect data used by Quiver runtime discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from quiver.docker.backend import DockerError

MANAGED_LABEL = "io.quiver.managed"
ASSESSMENT_LABEL = "io.quiver.assessment"
COMPONENT_LABEL = "io.quiver.component"
SCHEMA_LABEL = "io.quiver.schema"


@dataclass(frozen=True)
class ContainerState:
    id: str
    name: str
    running: bool
    status: str
    image: str
    labels: dict[str, str]

    @property
    def managed(self) -> bool:
        return self.labels.get(MANAGED_LABEL) == "true"


def container_state(data: dict[str, Any]) -> ContainerState:
    """Extract Quiver's required runtime state from a container inspection."""
    try:
        state = data["State"]
        config = data["Config"]
        name = str(data["Name"]).removeprefix("/")
        labels = config.get("Labels") or {}
        if not isinstance(labels, dict):
            raise TypeError("labels are not a mapping")
        return ContainerState(
            id=str(data["Id"]),
            name=name,
            running=bool(state["Running"]),
            status=str(state["Status"]),
            image=str(config["Image"]),
            labels={str(key): str(value) for key, value in labels.items()},
        )
    except (KeyError, TypeError) as error:
        raise DockerError(f"Docker inspect result is missing container state: {error}") from error


def published_port(data: dict[str, Any], container_port: int, protocol: str = "tcp") -> tuple[str, int] | None:
    """Return the first assigned host endpoint for a published container port."""
    try:
        ports = data["NetworkSettings"]["Ports"]
        bindings = ports.get(f"{container_port}/{protocol}")
        if not bindings:
            return None
        binding = bindings[0]
        return str(binding["HostIp"]), int(binding["HostPort"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise DockerError(f"Docker inspect result has invalid port data: {error}") from error
