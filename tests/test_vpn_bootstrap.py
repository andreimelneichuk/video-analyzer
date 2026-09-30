import base64

import pytest

from src.vpn_bootstrap import build_singbox_config, parse_vless_link, pick_vless_link

UUID = "11111111-2222-3333-4444-555555555555"
REALITY = (
    f"vless://{UUID}@vpn.example.com:443?type=tcp&security=reality&flow=xtls-rprx-vision"
    "&sni=www.google.com&fp=chrome&pbk=PUBKEY&sid=ab12#NL"
)
WS_TLS = f"vless://{UUID}@1.2.3.4:8443?type=ws&security=tls&path=%2Fws&host=cdn.example.com#WS"


def test_reality_link():
    out = parse_vless_link(REALITY)
    assert out["server"] == "vpn.example.com"
    assert out["server_port"] == 443
    assert out["uuid"] == UUID
    assert out["flow"] == "xtls-rprx-vision"
    assert out["tls"]["server_name"] == "www.google.com"
    assert out["tls"]["reality"] == {"enabled": True, "public_key": "PUBKEY", "short_id": "ab12"}
    assert out["tls"]["utls"]["fingerprint"] == "chrome"
    assert "transport" not in out


def test_ws_tls_link():
    out = parse_vless_link(WS_TLS)
    assert out["transport"] == {"type": "ws", "path": "/ws", "headers": {"Host": "cdn.example.com"}}
    assert out["tls"]["server_name"] == "cdn.example.com"
    assert "reality" not in out["tls"]


def test_rejects_non_vless():
    with pytest.raises(ValueError):
        parse_vless_link("vmess://abc")


def test_pick_link_from_base64_subscription():
    body = base64.b64encode(f"trojan://x@h:1#t\n{REALITY}\n{WS_TLS}".encode()).decode()
    assert pick_vless_link(body) == REALITY


def test_pick_link_from_plain_link():
    assert pick_vless_link(WS_TLS + "\n") == WS_TLS


def test_config_exposes_local_socks_only():
    config = build_singbox_config(parse_vless_link(REALITY))
    inbound = config["inbounds"][0]
    assert inbound["type"] == "socks"
    assert inbound["listen"] == "127.0.0.1"
    assert inbound["listen_port"] == 1080
    assert config["outbounds"][0]["type"] == "vless"
