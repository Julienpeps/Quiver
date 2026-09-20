from pathlib import Path

import pytest

from quiver.vpn.parser import VpnParseError, detect_vpn_type, parse_profile


def test_parses_openvpn_remote_and_full_tunnel(tmp_path: Path) -> None:
    profile_path = tmp_path / "client.ovpn"
    profile_path.write_text("client\nremote vpn.example.test 1194\nredirect-gateway def1\n")

    profile = parse_profile(profile_path)

    assert profile.type == "openvpn"
    assert profile.endpoints[0].host == "vpn.example.test"
    assert profile.endpoints[0].port == 1194
    assert profile.full_tunnel is True


def test_parses_wireguard_endpoint_and_full_tunnel(tmp_path: Path) -> None:
    profile_path = tmp_path / "client.conf"
    profile_path.write_text(
        "[Interface]\nPrivateKey = ignored\n[Peer]\nEndpoint = [2001:db8::1]:51820\nAllowedIPs = 0.0.0.0/0, ::/0\n"
    )

    profile = parse_profile(profile_path, "wireguard")

    assert profile.type == "wireguard"
    assert profile.endpoints[0].host == "2001:db8::1"
    assert profile.endpoints[0].protocol == "udp"
    assert profile.full_tunnel is True


def test_rejects_type_mismatch_and_ambiguous_content(tmp_path: Path) -> None:
    profile_path = tmp_path / "client.ovpn"
    profile_path.write_text("client\nremote vpn.example.test\n")

    with pytest.raises(VpnParseError, match="does not match"):
        parse_profile(profile_path, "wireguard")
    with pytest.raises(VpnParseError, match="ambiguous"):
        detect_vpn_type(Path("ambiguous.conf"), "client\n[Interface]\n")
