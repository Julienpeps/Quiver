"""Detection and validation for OpenVPN and wg-quick profiles."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Literal

from quiver.vpn.model import VpnEndpoint, VpnProfile, VpnType


class VpnParseError(ValueError):
    """Raised when a VPN profile is unsupported, ambiguous, or unsafe."""


_OPENVPN_REMOTE = re.compile(r"^remote\s+(?P<host>[^\s]+)(?:\s+(?P<port>\d+))?", re.MULTILINE)
_WIREGUARD_ENDPOINT = re.compile(
    r"^Endpoint\s*=\s*(?P<host>\[[^]]+\]|[^:\s]+)(?::(?P<port>\d+))?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_ALLOWED_IPS = re.compile(r"^AllowedIPs\s*=\s*(?P<value>.+)$", re.MULTILINE | re.IGNORECASE)


def detect_vpn_type(path: Path, content: str | None = None) -> VpnType:
    """Detect the supported profile type from a file's syntax and extension."""
    payload = content if content is not None else path.read_text()
    is_openvpn = bool(re.search(r"^\s*(client|remote|dev\s+(tun|tap))\b", payload, re.MULTILINE))
    is_wireguard = bool(re.search(r"^\s*\[Interface]\s*$", payload, re.MULTILINE | re.IGNORECASE))
    if is_openvpn and is_wireguard:
        raise VpnParseError("VPN profile is ambiguous between OpenVPN and WireGuard")
    if is_openvpn:
        return "openvpn"
    if is_wireguard:
        return "wireguard"
    if path.suffix.lower() == ".ovpn":
        return "openvpn"
    raise VpnParseError("could not detect VPN profile type")


def parse_profile(path: Path, configured_type: Literal["auto", "openvpn", "wireguard"] = "auto") -> VpnProfile:
    """Parse a profile and check its full-tunnel intent without modifying it."""
    try:
        content = path.read_text()
    except OSError as error:
        raise VpnParseError(f"could not read VPN profile {path}: {error}") from error
    detected_type = detect_vpn_type(path, content)
    if configured_type != "auto" and configured_type != detected_type:
        raise VpnParseError(
            f"configured VPN type {configured_type} does not match {detected_type} profile"
        )
    if detected_type == "openvpn":
        return _parse_openvpn(content)
    return _parse_wireguard(content)


def _parse_openvpn(content: str) -> VpnProfile:
    endpoints = tuple(
        VpnEndpoint(match.group("host"), _port(match.group("port")), None)
        for match in _OPENVPN_REMOTE.finditer(content)
    )
    if not endpoints:
        raise VpnParseError("OpenVPN profile does not declare a remote endpoint")
    full_tunnel = bool(re.search(r"^\s*redirect-gateway\b", content, re.MULTILINE))
    return VpnProfile(type="openvpn", endpoints=endpoints, full_tunnel=full_tunnel)


def _parse_wireguard(content: str) -> VpnProfile:
    endpoints = tuple(
        VpnEndpoint(
            match.group("host").strip("[]"), _port(match.group("port")), "udp"
        )
        for match in _WIREGUARD_ENDPOINT.finditer(content)
    )
    if not endpoints:
        raise VpnParseError("WireGuard profile does not declare a peer endpoint")
    allowed = [item.strip() for match in _ALLOWED_IPS.finditer(content) for item in match.group("value").split(",")]
    full_tunnel = _covers_default_route(allowed)
    return VpnProfile(type="wireguard", endpoints=endpoints, full_tunnel=full_tunnel)


def _port(value: str | None) -> int | None:
    if value is None:
        return None
    port = int(value)
    if not 1 <= port <= 65535:
        raise VpnParseError("VPN endpoint port must be between 1 and 65535")
    return port


def _covers_default_route(cidrs: list[str]) -> bool:
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as error:
            raise VpnParseError(f"invalid WireGuard AllowedIPs entry: {cidr}") from error
        if network.version == 4 and network.prefixlen == 0:
            return True
    return False
