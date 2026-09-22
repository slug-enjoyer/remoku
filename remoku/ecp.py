"""Minimal client for Roku's External Control Protocol (ECP).

Roku documents the whole protocol publicly:
https://developer.roku.com/docs/developer-program/dev-tools/external-control-api.md

Every Roku device runs a small REST server on TCP port 8060. There is no
authentication, which is how phone/desktop remote apps work.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree

import requests

ECP_PORT = 8060
DEFAULT_TIMEOUT = (1.5, 3.0)

# TV inputs are launched by channel id; older Roku OS versions only understand
# the equivalent keypresses, so we keep a fallback map.
INPUT_KEY_FALLBACK = {
    "tvinput.hdmi1": "InputHDMI1",
    "tvinput.hdmi2": "InputHDMI2",
    "tvinput.hdmi3": "InputHDMI3",
    "tvinput.hdmi4": "InputHDMI4",
    "tvinput.av1": "InputAV1",
    "tvinput.cvbs": "InputAV1",
    "tvinput.dtv": "InputTuner",
}


class RokuError(Exception):
    """Raised when a device cannot be reached or refuses a command."""


class RokuPermissionError(RokuError):
    """Raised when the device refuses a command (e.g. Control by mobile apps off)."""


@dataclass
class RokuApp:
    """An app or TV input installed on a device."""

    id: str
    name: str
    kind: str = "app"  # "app" for streaming channels, "input" for TV inputs
    version: str = ""


class Roku:
    """Client for one Roku device."""

    def __init__(self, ip: str, timeout=DEFAULT_TIMEOUT):
        self.ip = ip
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"Roku({self.ip})"

    def url(self, path: str) -> str:
        return f"http://{self.ip}:{ECP_PORT}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        try:
            response = requests.request(
                method, self.url(path), timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise RokuError(f"Could not reach {self.ip}: {exc}") from exc

        if response.status_code == 403:
            raise RokuPermissionError(
                "The Roku refused this command. Enable Settings > System > "
                "Advanced system settings > Control by mobile apps on the device "
                "(required for remote control since Roku OS 14.1)."
            )
        if response.status_code >= 400:
            raise RokuError(
                f"{self.ip} returned HTTP {response.status_code} for /{path.lstrip('/')}"
            )
        return response

    def get(self, path: str) -> requests.Response:
        return self._request("GET", path)

    def post(self, path: str) -> requests.Response:
        return self._request("POST", path)

    # -- remote control ---------------------------------------------------

    def keypress(self, key: str) -> None:
        """Press and release a remote key, e.g. "Home", "Select", "Lit_a"."""
        self.post(f"keypress/{key}")

    def keydown(self, key: str) -> None:
        self.post(f"keydown/{key}")

    def keyup(self, key: str) -> None:
        self.post(f"keyup/{key}")

    def launch(self, app_id: str) -> None:
        self.post(f"launch/{app_id}")

    def launch_input(self, app_id: str) -> None:
        """Launch a TV input, falling back to keypresses on older firmware."""
        try:
            self.launch(app_id)
        except RokuError:
            fallback = INPUT_KEY_FALLBACK.get(app_id)
            if fallback is None:
                raise
            self.keypress(fallback)

    def power_on(self) -> None:
        self.keypress("PowerOn")

    def power_off(self) -> None:
        self.keypress("PowerOff")

    def toggle_power(self, power_mode: Optional[str] = None) -> None:
        if power_mode is None:
            power_mode = self.info().get("power")
        if power_mode in ("Ready", "Off"):
            self.power_on()
        else:
            self.power_off()

    def wake(self, *macs: str) -> None:
        """Wake a sleeping device with Wake-on-LAN magic packets.

        Roku TVs in standby only answer device-info over ECP and refuse
        everything else with HTTP 403. Roku's own mobile app wakes them with a
        magic packet first; this does the same. Several destinations are tried
        because the device may be reachable on its Wi-Fi MAC or its Ethernet
        MAC, and some access points drop broadcast traffic.
        """
        packets = []
        for mac in macs:
            cleaned = re.sub(r"[^0-9a-fA-F]", "", mac or "")
            if len(cleaned) == 12:
                packets.append(b"\xff" * 6 + bytes.fromhex(cleaned) * 16)
        if not packets:
            raise RokuError("No valid MAC address to send a wake packet to")

        subnet_broadcast = str(
            ipaddress.ip_network(f"{self.ip}/24", strict=False).broadcast_address
        )
        destinations = [
            ("255.255.255.255", 9),
            (subnet_broadcast, 9),
            (self.ip, 9),
            (self.ip, 7),
        ]

        sent = False
        for packet in packets:
            for host, port in destinations:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    sock.sendto(packet, (host, port))
                    sent = True
                except OSError:
                    pass
                finally:
                    sock.close()
        if not sent:
            raise RokuError("Could not send the Wake-on-LAN packet")

    # -- queries ----------------------------------------------------------

    def info(self) -> dict:
        """Return a normalized dict of useful /query/device-info fields."""
        root = ElementTree.fromstring(self.get("query/device-info").content)

        def text(tag: str) -> Optional[str]:
            value = root.findtext(tag)
            return value.strip() if value else None

        is_tv = text("is-tv") == "true"
        return {
            "ip": self.ip,
            "name": text("user-device-name") or text("friendly-device-name") or self.ip,
            "model": text("friendly-model-name") or text("model-name") or "",
            "serial": text("serial-number") or "",
            "mac": text("wifi-mac") or text("ethernet-mac") or "",
            "ethernet_mac": text("ethernet-mac") or "",
            "power": text("power-mode") or "",
            "is_tv": is_tv,
            "supports_tv_power": text("supports-tv-power-control") == "true",
            "supports_volume": is_tv or text("supports-audio-volume-control") == "true",
            "supports_find_remote": text("supports-find-remote") == "true",
        }

    def apps(self) -> list[RokuApp]:
        """Return installed streaming apps and TV inputs."""
        root = ElementTree.fromstring(self.get("query/apps").content)
        found = []
        for node in root.findall("app"):
            app_id = node.get("id", "")
            if not app_id:
                continue
            found.append(
                RokuApp(
                    id=app_id,
                    name=(node.text or "").strip(),
                    kind="input" if node.get("type") == "tvin" else "app",
                    version=node.get("version", ""),
                )
            )
        return found

    def icon(self, app_id: str) -> tuple[bytes, str]:
        """Return (image bytes, MIME type) for an app's icon."""
        response = self.get(f"query/icon/{app_id}")
        return response.content, response.headers.get("Content-Type", "image/png")

    def active_app(self) -> Optional[RokuApp]:
        root = ElementTree.fromstring(self.get("query/active-app").content)
        node = root.find("app")
        if node is None or not node.get("id"):
            return None
        return RokuApp(
            id=node.get("id", ""), name=(node.text or "").strip(), version=node.get("version", "")
        )
