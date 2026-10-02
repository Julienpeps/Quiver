"""Deterministic fail-closed firewall rules for in-container VPN clients."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Literal


class KillSwitchError(ValueError):
    """Raised when rules cannot be safely generated from runtime inputs."""


@dataclass(frozen=True)
class AllowedEndpoint:
    address: str
    port: int
    protocol: Literal["tcp", "udp"]

    def __post_init__(self) -> None:
        try:
            ipaddress.ip_address(self.address)
        except ValueError as error:
            raise KillSwitchError(f"VPN endpoint must be an IP address: {self.address}") from error
        if not 1 <= self.port <= 65535:
            raise KillSwitchError("VPN endpoint port must be between 1 and 65535")


@dataclass(frozen=True)
class KillSwitchConfig:
    tunnel_interface: str
    endpoints: tuple[AllowedEndpoint, ...]
    management_cidrs: tuple[str, ...] = ()
    bypass_cidrs: tuple[str, ...] = ()
    dns_servers: tuple[str, ...] = ()
    dns_through_vpn: bool = False
    base_interface: str | None = None

    def __post_init__(self) -> None:
        if not self.tunnel_interface or any(char.isspace() for char in self.tunnel_interface):
            raise KillSwitchError("tunnel interface must be a non-empty interface name")
        if not self.endpoints:
            raise KillSwitchError("at least one resolved VPN endpoint is required")
        for cidr in (*self.management_cidrs, *self.bypass_cidrs):
            _network(cidr)
        for server in self.dns_servers:
            _address(server)
        if self.base_interface is not None:
            if any(char.isspace() for char in self.base_interface):
                raise KillSwitchError("base interface must be a single interface name")
            if self.base_interface == self.tunnel_interface:
                raise KillSwitchError("base interface must differ from the tunnel interface")


def render_nftables(config: KillSwitchConfig) -> str:
    """Render an nftables table with a restrictive output policy.

    The table is deliberately independent of the VPN process lifetime. A
    supervisor restart therefore leaves normal egress blocked until the tunnel
    interface becomes available again.
    """
    lines = [
        "table inet quiver_killswitch {",
        "  chain output {",
        "    type filter hook output priority filter; policy drop;",
        '    oifname "lo" accept',
        "    ct state established,related accept",
        f'    oifname "{config.tunnel_interface}" accept',
    ]
    base = f'osifname "{config.base_interface}" ' if config.base_interface else ""
    lines.extend(_nft_network_rules((*config.management_cidrs, *config.bypass_cidrs), base))
    if not config.dns_through_vpn:
        for address in config.dns_servers:
            family = _family(address)
            lines.extend(
                (
                    f"    {base}{family} daddr {address} udp dport 53 accept",
                    f"    {base}{family} daddr {address} tcp dport 53 accept",
                )
            )
    for endpoint in config.endpoints:
        family = _family(endpoint.address)
        lines.append(
            f"    {base}{family} daddr {endpoint.address} {endpoint.protocol} dport {endpoint.port} accept"
        )
    lines.extend(("  }", "}"))
    return "\n".join(lines) + "\n"


def render_iptables(config: KillSwitchConfig) -> list[str]:
    """Render idempotent iptables commands for IPv4-only fallback support."""
    commands = [
        "iptables -N QUIVER_KILLSWITCH 2>/dev/null || true",
        "iptables -F QUIVER_KILLSWITCH",
        "iptables -D OUTPUT -j QUIVER_KILLSWITCH 2>/dev/null || true",
        "iptables -I OUTPUT 1 -j QUIVER_KILLSWITCH",
        "iptables -A QUIVER_KILLSWITCH -o lo -j ACCEPT",
        "iptables -A QUIVER_KILLSWITCH -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT",
        f"iptables -A QUIVER_KILLSWITCH -o {config.tunnel_interface} -j ACCEPT",
    ]
    base = f"-o {config.base_interface} " if config.base_interface else ""
    for cidr in (*config.management_cidrs, *config.bypass_cidrs):
        if _network(cidr).version == 4:
            commands.append(f"iptables -A QUIVER_KILLSWITCH {base}-d {cidr} -j ACCEPT")
    if not config.dns_through_vpn:
        for address in config.dns_servers:
            if _address(address).version == 4:
                commands.extend(
                    (
                        f"iptables -A QUIVER_KILLSWITCH {base}-d {address} -p udp --dport 53 -j ACCEPT",
                        f"iptables -A QUIVER_KILLSWITCH {base}-d {address} -p tcp --dport 53 -j ACCEPT",
                    )
                )
    for endpoint in config.endpoints:
        if _address(endpoint.address).version == 4:
            commands.append(
                "iptables -A QUIVER_KILLSWITCH "
                f"{base}-d {endpoint.address} -p {endpoint.protocol} --dport {endpoint.port} -j ACCEPT"
            )
    commands.append("iptables -A QUIVER_KILLSWITCH -j REJECT")
    return commands


def _nft_network_rules(cidrs: tuple[str, ...], base_prefix: str = "") -> list[str]:
    rules: list[str] = []
    for cidr in cidrs:
        network = _network(cidr)
        family = "ip" if network.version == 4 else "ip6"
        rules.append(f"    {base_prefix}{family} daddr {network.with_prefixlen} accept")
    return rules


def _network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError as error:
        raise KillSwitchError(f"invalid bypass or management CIDR: {value}") from error


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(value)
    except ValueError as error:
        raise KillSwitchError(f"invalid DNS server address: {value}") from error


def _family(address: str) -> str:
    return "ip" if _address(address).version == 4 else "ip6"
