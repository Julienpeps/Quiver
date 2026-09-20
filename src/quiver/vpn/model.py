"""VPN profile data extracted from supported configuration formats."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VpnType = Literal["openvpn", "wireguard"]


@dataclass(frozen=True)
class VpnEndpoint:
    host: str
    port: int | None
    protocol: str | None = None


@dataclass(frozen=True)
class VpnProfile:
    type: VpnType
    endpoints: tuple[VpnEndpoint, ...]
    full_tunnel: bool
