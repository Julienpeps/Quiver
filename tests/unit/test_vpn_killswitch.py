from importlib.resources import files
from pathlib import Path

import pytest

from quiver.vpn.killswitch import (
    AllowedEndpoint,
    KillSwitchConfig,
    KillSwitchError,
    render_iptables,
    render_nftables,
)


def policy() -> KillSwitchConfig:
    return KillSwitchConfig(
        tunnel_interface="tun0",
        endpoints=(AllowedEndpoint("198.51.100.10", 1194, "udp"),),
        management_cidrs=("172.24.0.0/16",),
        bypass_cidrs=("10.20.30.0/24", "2001:db8:1::/64"),
        dns_servers=("127.0.0.11", "2001:4860:4860::8888"),
    )


def test_nftables_policy_is_fail_closed_and_narrowly_allows_required_traffic() -> None:
    rules = render_nftables(policy())

    assert "policy drop" in rules
    assert 'oifname "tun0" accept' in rules
    assert "ip daddr 172.24.0.0/16 accept" in rules
    assert "ip daddr 10.20.30.0/24 accept" in rules
    assert "ip6 daddr 2001:db8:1::/64 accept" in rules
    assert "ip daddr 127.0.0.11 udp dport 53 accept" in rules
    assert "ip daddr 198.51.100.10 udp dport 1194 accept" in rules


def test_dns_allowlist_is_omitted_when_dns_must_use_tunnel() -> None:
    config = KillSwitchConfig(
        tunnel_interface="wg0",
        endpoints=(AllowedEndpoint("198.51.100.10", 51820, "udp"),),
        dns_servers=("127.0.0.11",),
        dns_through_vpn=True,
    )

    assert "127.0.0.11" not in render_nftables(config)


def test_iptables_fallback_rejects_unmatched_ipv4_traffic() -> None:
    commands = render_iptables(policy())

    assert "iptables -I OUTPUT 1 -j QUIVER_KILLSWITCH" in commands
    assert "iptables -A QUIVER_KILLSWITCH -d 198.51.100.10 -p udp --dport 1194 -j ACCEPT" in commands
    assert commands[-1] == "iptables -A QUIVER_KILLSWITCH -j REJECT"


@pytest.mark.parametrize(
    ("endpoint", "message"),
    [
        (("vpn.example.test", 1194, "udp"), "IP address"),
        (("198.51.100.1", 0, "udp"), "between 1 and 65535"),
    ],
)
def test_invalid_endpoints_are_rejected(endpoint: tuple[str, int, str], message: str) -> None:
    with pytest.raises(KillSwitchError, match=message):
        AllowedEndpoint(*endpoint)


def test_base_interface_scopes_allowlist_rules() -> None:
    config = KillSwitchConfig(
        tunnel_interface="tun0",
        endpoints=(AllowedEndpoint("198.51.100.10", 1194, "udp"),),
        management_cidrs=("172.24.0.0/16",),
        dns_servers=("127.0.0.11",),
        base_interface="eth0",
    )

    rules = render_nftables(config)
    assert 'osifname "eth0" ip daddr 198.51.100.10 udp dport 1194 accept' in rules
    assert 'osifname "eth0" ip daddr 127.0.0.11 udp dport 53 accept' in rules
    assert 'osifname "eth0" ip daddr 172.24.0.0/16 accept' in rules
    # The tunnel interface itself stays unscoped.
    assert 'oifname "tun0" accept' in rules

    commands = render_iptables(config)
    assert "iptables -A QUIVER_KILLSWITCH -o eth0 -d 198.51.100.10 -p udp --dport 1194 -j ACCEPT" in commands


def test_base_interface_must_differ_from_tunnel() -> None:
    with pytest.raises(KillSwitchError, match="differ"):
        KillSwitchConfig(
            tunnel_interface="tun0",
            endpoints=(AllowedEndpoint("198.51.100.10", 1194, "udp"),),
            base_interface="tun0",
        )


def test_vpn_scripts_keep_firewall_rules_after_tunnel_exit() -> None:
    root = Path(str(files("quiver.resources")))
    script = (root / "images/common/supervisor/quiver-vpn").read_text()
    helper = (root / "images/common/supervisor/quiver-killswitch.py").read_text()

    assert "quiver-killswitch.py" in script
    assert "nft delete table" not in script
    assert "iptables -F" not in script
    assert "render_nftables(policy)" in helper
    assert "render_iptables(policy)" in helper
