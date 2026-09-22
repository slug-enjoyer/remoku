"""Find Roku devices on the local network.

Three strategies, in order of preference:

1. SSDP M-SEARCH -- Roku's official discovery mechanism. Many access points
   silently drop multicast between wireless clients, so this often finds
   nothing.
2. ARP sweep + targeted probes -- ask the kernel to resolve every address in
   the local /24 (a single UDP datagram each is enough), then TCP-probe only
   the hosts that actually exist. This is very gentle on the network: hosts
   that do not exist never generate traffic beyond the ARP request itself.
3. Plain TCP scan of the /24 -- fallback for when the ARP table is not
   readable (non-Linux). Kept at low concurrency; broad high-concurrency
   scans can knock consumer routers over for ten seconds or more.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from .ecp import ECP_PORT, Roku, RokuError

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "remoku"
DEVICES_FILE = CONFIG_DIR / "devices.json"

SSDP_TIMEOUT = 1.5
PROBE_TIMEOUT = 0.5
PROBE_CONCURRENCY = 64
ARP_SETTLE_TIME = 1.5

ProgressFn = Optional[Callable[[str], None]]


def ssdp_discover(timeout: float = SSDP_TIMEOUT) -> list[str]:
    """Ask for Roku devices with an SSDP M-SEARCH and return their IPs."""
    message = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: roku:ecp\r\n"
        "\r\n"
    )
    found: list[str] = []
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        sock.settimeout(0.5)
        sock.sendto(message.encode(), ("239.255.255.250", 1900))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            match = re.search(
                r"^location:\s*http://([^:/]+)", data.decode(errors="replace"), re.I | re.M
            )
            ip = match.group(1) if match else addr[0]
            if ip not in found:
                found.append(ip)
    except OSError:
        pass
    finally:
        if sock is not None:
            sock.close()
    return found


def local_networks() -> list[ipaddress.IPv4Network]:
    """Return the /24 networks this machine is attached to (never anything wider)."""
    networks: list[ipaddress.IPv4Network] = []
    try:
        output = subprocess.run(
            ["ip", "-j", "addr"], capture_output=True, text=True, timeout=5
        ).stdout
        for iface in json.loads(output):
            for addr in iface.get("addr_info", []):
                if addr.get("family") not in ("inet", "ipv4") or addr.get("scope") == "host":
                    continue
                ip = addr.get("local")
                prefix = int(addr.get("prefixlen", 32))
                if not ip or prefix >= 31 or ip.startswith("169.254."):
                    continue
                # Never scan wider than a /24, even if the interface is /22.
                network = ipaddress.ip_network(f"{ip}/{max(prefix, 24)}", strict=False)
                if network not in networks:
                    networks.append(network)
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
            networks.append(ipaddress.ip_network(f"{ip}/24", strict=False))
        except OSError:
            pass
    return networks


def _neighbour_ips() -> list[str]:
    """Read the kernel's ARP table (Linux only)."""
    found = []
    try:
        with open("/proc/net/arp") as handle:
            lines = handle.read().splitlines()[1:]
    except OSError:
        return []
    for line in lines:
        fields = line.split()
        if len(fields) >= 4 and fields[3] != "00:00:00:00:00:00":
            found.append(fields[0])
    return found


def arp_sweep(network: ipaddress.IPv4Network) -> list[str]:
    """Resolve every host in a subnet by ARP, then return the ones that exist.

    One UDP datagram per address makes the kernel look up the neighbour; hosts
    that do not exist produce no reply, so the only traffic they generate is
    the local ARP request. No TCP connections, no router NAT entries.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for host in network.hosts():
            try:
                sock.sendto(b"", (str(host), 9))
            except OSError:
                pass
    finally:
        sock.close()

    prefix = str(network.network_address).rsplit(".", 1)[0] + "."
    deadline = time.monotonic() + ARP_SETTLE_TIME
    known: set[str] = set()
    while time.monotonic() < deadline:
        current = {ip for ip in _neighbour_ips() if ip.startswith(prefix)}
        if current and current == known:
            break
        known = current
        time.sleep(0.15)
    return sorted(known, key=lambda ip: tuple(int(part) for part in ip.split(".")))


async def _tcp_probe(ip: str, results: list[str], semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, ECP_PORT), timeout=PROBE_TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        results.append(ip)


def probe_hosts(hosts: list[str], concurrency: int = PROBE_CONCURRENCY) -> list[str]:
    """TCP-probe a small list of hosts for the ECP port."""
    if not hosts:
        return []

    async def run() -> list[str]:
        results: list[str] = []
        semaphore = asyncio.Semaphore(concurrency)
        await asyncio.gather(*(_tcp_probe(host, results, semaphore) for host in hosts))
        return results

    return asyncio.run(run())


def scan_network() -> list[str]:
    """Find hosts with the ECP port open, without flooding the network."""
    found: list[str] = []
    for network in local_networks():
        candidates = arp_sweep(network)
        if not candidates:
            # No ARP table (non-Linux?): fall back to a low-concurrency scan.
            candidates = [str(host) for host in network.hosts()]
        for ip in probe_hosts(candidates):
            if ip not in found:
                found.append(ip)
    return sorted(found, key=lambda ip: tuple(int(part) for part in ip.split(".")))


def _device_info_with_retry(ip: str, attempts: int = 2) -> Optional[dict]:
    for attempt in range(attempts):
        try:
            return Roku(ip).info()
        except RokuError:
            if attempt + 1 < attempts:
                time.sleep(0.8)
    return None


def discover(progress: ProgressFn = None) -> list[dict]:
    """Discover devices, returning normalized info dicts from ecp.Roku.info()."""
    def note(message: str) -> None:
        if progress:
            progress(message)

    note("Looking for Roku devices with SSDP...")
    candidates = ssdp_discover()

    note("Scanning the local network...")
    for ip in scan_network():
        if ip not in candidates:
            candidates.append(ip)

    devices = []
    for ip in candidates:
        note(f"Checking {ip}...")
        info = _device_info_with_retry(ip)
        if info:
            devices.append(info)
    return devices


# -- device bookkeeping ---------------------------------------------------


def load_saved() -> dict:
    """Load {"last": ip|None, "devices": [...]} from disk."""
    try:
        data = json.loads(DEVICES_FILE.read_text())
        if isinstance(data, dict):
            data.setdefault("last", None)
            data.setdefault("devices", [])
            return data
    except (OSError, ValueError):
        pass
    return {"last": None, "devices": []}


def save_saved(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEVICES_FILE.write_text(json.dumps(data, indent=2) + "\n")


def last_used_ip() -> Optional[str]:
    return load_saved().get("last")
