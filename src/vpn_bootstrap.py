"""
Container entrypoint: optionally starts a local VLESS client (sing-box) that
exposes a SOCKS5 proxy, points YOUTUBE_PROXY at it, then execs uvicorn.

Server IPs get YouTube's bot check; routing anonymous YouTube downloads
through the user's VPN avoids it. Instagram and cookies stay direct because
only YOUTUBE_PROXY is set. VLESS_URL may be a single vless:// link or a
subscription URL (plain or base64 list of links). Any failure here is logged
and the app starts without the proxy.
"""

import base64
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import parse_qs, unquote, urlsplit

logger = logging.getLogger("vpn_bootstrap")

SOCKS_PORT = 1080
CONFIG_PATH = "/tmp/sing-box.json"


def _decode_subscription(body: str) -> str:
    body = body.strip()
    if "://" in body:
        return body
    try:
        return base64.b64decode(body + "=" * (-len(body) % 4)).decode()
    except ValueError:
        return body


def pick_vless_link(text: str) -> str:
    """Returns the first vless:// link from a link, a subscription body or a base64 list."""
    for line in _decode_subscription(text).splitlines():
        line = line.strip()
        if line.startswith("vless://"):
            return line
    raise ValueError("no vless:// link found")


def parse_vless_link(link: str) -> dict:
    """Converts a vless:// share link into a sing-box outbound."""
    url = urlsplit(link)
    if url.scheme != "vless" or not url.hostname or not url.username:
        raise ValueError("not a vless link")
    q = {k: v[0] for k, v in parse_qs(url.query).items()}

    outbound: dict = {
        "type": "vless",
        "tag": "proxy",
        "server": url.hostname,
        "server_port": url.port or 443,
        "uuid": unquote(url.username),
        "packet_encoding": "xudp",
    }
    if q.get("flow"):
        outbound["flow"] = q["flow"]

    security = q.get("security", "none")
    if security in ("tls", "reality"):
        tls: dict = {"enabled": True, "server_name": q.get("sni") or q.get("host") or url.hostname}
        if q.get("fp"):
            tls["utls"] = {"enabled": True, "fingerprint": q["fp"]}
        if q.get("alpn"):
            tls["alpn"] = q["alpn"].split(",")
        if security == "reality":
            tls["reality"] = {
                "enabled": True,
                "public_key": q.get("pbk", ""),
                "short_id": q.get("sid", ""),
            }
            tls.setdefault("utls", {"enabled": True, "fingerprint": "chrome"})
        outbound["tls"] = tls

    transport = q.get("type", "tcp")
    if transport == "ws":
        outbound["transport"] = {"type": "ws", "path": q.get("path", "/")}
        if q.get("host"):
            outbound["transport"]["headers"] = {"Host": q["host"]}
    elif transport == "grpc":
        outbound["transport"] = {"type": "grpc", "service_name": q.get("serviceName", "")}
    elif transport == "httpupgrade":
        outbound["transport"] = {
            "type": "httpupgrade",
            "path": q.get("path", "/"),
            "host": q.get("host", ""),
        }
    elif transport not in ("tcp", "raw"):
        raise ValueError(f"unsupported transport: {transport}")
    return outbound


def build_singbox_config(outbound: dict, port: int = SOCKS_PORT) -> dict:
    return {
        "log": {"level": "warn"},
        "inbounds": [{"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": port}],
        "outbounds": [outbound],
    }


def _resolve_link(source: str) -> str:
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(source, headers={"User-Agent": "sing-box"})
        with urllib.request.urlopen(request, timeout=20) as response:
            source = response.read().decode()
    return pick_vless_link(source)


def _wait_for_port(port: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def start_vless_proxy(source: str) -> str | None:
    """Starts sing-box in the background; returns the SOCKS URL or None on failure."""
    try:
        outbound = parse_vless_link(_resolve_link(source))
        with open(CONFIG_PATH, "w") as f:
            json.dump(build_singbox_config(outbound), f)
        subprocess.Popen(["sing-box", "run", "-c", CONFIG_PATH])
    except (OSError, ValueError) as exc:  # never print the link: it carries credentials
        logger.error("VLESS proxy not started: %s", type(exc).__name__)
        return None
    if not _wait_for_port(SOCKS_PORT):
        logger.error("VLESS proxy did not open port %d", SOCKS_PORT)
        return None
    logger.info("VLESS proxy up on 127.0.0.1:%d for YouTube", SOCKS_PORT)
    return f"socks5://127.0.0.1:{SOCKS_PORT}"


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    source = os.environ.get("VLESS_URL", "").strip()
    if source and not os.environ.get("YOUTUBE_PROXY"):
        proxy = start_vless_proxy(source)
        if proxy:
            os.environ["YOUTUBE_PROXY"] = proxy
    port = os.environ.get("PORT", "8080")
    args = ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", port, *sys.argv[1:]]
    os.execvp(args[0], args)


if __name__ == "__main__":
    main()
