#!/usr/bin/env python
"""Generate and install a Quiver VPN fail-closed firewall policy."""

from __future__ import annotations

import argparse
import re
import socket
import subprocess
from pathlib import Path

import yaml
from killswitch import (
    AllowedEndpoint,
    KillSwitchConfig,
    render_iptables,
    render_nftables,
)

OPENVPN_REMOTE = re.compile(r"^remote\s+(?P<host>[^\s]+)(?:\s+(?P<port>\d+))?", re.MULTILINE)
OPENVPN_PROTO = re.compile(r"^proto(?:\s+|-client\s+)(?P<protocol>tcp|udp)\b", re.MULTILINE)
WG_ENDPOINT = re.compile(
    r"^Endpoint\s*=\s*(?P<host>\[[^]]+\]|[^:\s]+)(?::(?P<port>\d+))?\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def resolve(host: str, port: int, protocol: str) -> list[AllowedEndpoint]:
    socktype = socket.SOCK_STREAM if protocol == "tcp" else socket.SOCK_DGRAM
    try:
        addresses = socket.getaddrinfo(host.strip("[]"), port, type=socktype)
    except socket.gaierror as error:
        raise SystemExit(f"cannot resolve VPN endpoint {host}: {error}") from error
    return list(
        {
            AllowedEndpoint(address[4][0], port, protocol)
            for address in addresses
            if address[0] in (socket.AF_INET, socket.AF_INET6)
        }
    )


def endpoints(profile: Path, profile_type: str) -> list[AllowedEndpoint]:
    content = profile.read_text()
    if profile_type == "auto":
        profile_type = "wireguard" if "[Interface]" in content else "openvpn"
    if profile_type == "openvpn":
        protocol_match = OPENVPN_PROTO.search(content)
        protocol = protocol_match.group("protocol") if protocol_match else "udp"
        matches = OPENVPN_REMOTE.finditer(content)
        result = [
            endpoint
            for match in matches
            for endpoint in resolve(match.group("host"), int(match.group("port") or 1194), protocol)
        ]
    elif profile_type == "wireguard":
        matches = WG_ENDPOINT.finditer(content)
        result = [
            endpoint
            for match in matches
            for endpoint in resolve(match.group("host"), int(match.group("port") or 51820), "udp")
        ]
    else:
        raise SystemExit(f"unsupported VPN type: {profile_type}")
    if not result:
        raise SystemExit("VPN profile does not declare a resolvable endpoint")
    return result


def resolvers() -> list[str]:
    try:
        return [
            line.split()[1]
            for line in Path("/etc/resolv.conf").read_text().splitlines()
            if line.startswith("nameserver ")
        ]
    except OSError:
        return []


def management_networks() -> list[str]:
    result = subprocess.run(
        ["ip", "route", "show", "scope", "link"], check=False, capture_output=True, text=True
    )
    return [line.split()[0] for line in result.stdout.splitlines() if line and "/" in line.split()[0]]


def base_interface() -> str | None:
    """Return the default-route (base outbound) interface name, if any."""
    result = subprocess.run(["ip", "route", "show", "default"], check=False, capture_output=True, text=True)
    match = re.search(r"dev (\S+)", result.stdout)
    return match.group(1) if match else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--interface", required=True)
    arguments = parser.parse_args()
    config = yaml.safe_load(arguments.config.read_text()) or {}
    vpn = config.get("vpn") or {}
    if not vpn.get("fail_closed", True):
        # The user opted out of fail-closed semantics; leave the network open.
        return
    if not vpn.get("local_management_bypass", True):
        management = ()
    else:
        management = management_networks()
    profile = Path(vpn["config"])
    if not profile.is_absolute():
        profile = Path("/workspace") / profile
    policy = KillSwitchConfig(
        tunnel_interface=arguments.interface,
        endpoints=tuple(endpoints(profile, vpn.get("type", "auto"))),
        management_cidrs=management,
        bypass_cidrs=tuple(vpn.get("bypass_cidrs") or ()),
        dns_servers=tuple(resolvers()),
        dns_through_vpn=bool(vpn.get("dns_through_vpn", False)),
        base_interface=base_interface(),
    )
    if subprocess.run(["nft", "--version"], capture_output=True, check=False).returncode == 0:
        subprocess.run(["nft", "delete", "table", "inet", "quiver_killswitch"], check=False)
        subprocess.run(["nft", "-f", "-"], input=render_nftables(policy), text=True, check=True)
    else:
        for command in render_iptables(policy):
            subprocess.run(command.split(), check=True)


if __name__ == "__main__":
    main()
