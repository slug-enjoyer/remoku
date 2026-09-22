"""GTK3 user interface for remoku."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from .discovery import discover, load_saved, save_saved
from .ecp import Roku, RokuPermissionError

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "remoku"
ICON_DIR = CACHE_DIR / "icons"
STYLE_PATH = Path(__file__).with_name("style.css")
ICON_PATH = Path(__file__).with_name("assets") / "remoku.svg"

# Navigation keys: these always behave like the remote. Everything printable
# is sent to the Roku as text instead (Roku ignores it unless its on-screen
# keyboard is up), because the ECP API cannot tell us whether a text field is
# focused. The remote functions that used to live on printable keys are on
# Alt+<key> now.
NAV_KEYS = {
    Gdk.KEY_Left: "Left",
    Gdk.KEY_Right: "Right",
    Gdk.KEY_Up: "Up",
    Gdk.KEY_Down: "Down",
    Gdk.KEY_Return: "Select",
    Gdk.KEY_KP_Enter: "Select",
    Gdk.KEY_Escape: "Home",
    Gdk.KEY_BackSpace: "Backspace",
}

# Ctrl+<key> shortcuts. Volume on the arrow keys mirrors a physical remote's
# side rocker.
CTRL_SHORTCUTS = {
    Gdk.KEY_Up: "VolumeUp",
    Gdk.KEY_Down: "VolumeDown",
}

ALT_SHORTCUTS = {
    Gdk.KEY_b: "Back",
    Gdk.KEY_h: "Home",
    Gdk.KEY_i: "Info",
    Gdk.KEY_o: "Select",
    Gdk.KEY_r: "Rev",
    Gdk.KEY_f: "Fwd",
    Gdk.KEY_p: "Play",
    Gdk.KEY_m: "VolumeMute",
    Gdk.KEY_comma: "Rev",
    Gdk.KEY_period: "Fwd",
    Gdk.KEY_slash: "Play",
    Gdk.KEY_bracketleft: "VolumeDown",
    Gdk.KEY_minus: "VolumeDown",
    Gdk.KEY_bracketright: "VolumeUp",
    Gdk.KEY_plus: "VolumeUp",
    Gdk.KEY_equal: "VolumeUp",
    Gdk.KEY_backslash: "VolumeMute",
    Gdk.KEY_w: "Up",
    Gdk.KEY_a: "Left",
    Gdk.KEY_s: "Down",
    Gdk.KEY_d: "Right",
}

ICON_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
}


def _is_standby(info: dict | None) -> bool:
    """True when a device reports a sleeping power state."""
    return bool(info) and info.get("power") in ("Ready", "Off", "Suspend")


class RemoteWindow(Gtk.Window):
    def __init__(self, initial_ip: str | None = None):
        super().__init__(title="Remoku")
        self.set_default_size(400, 860)

        self.device: Roku | None = None
        self.info: dict | None = None
        self.devices: list[dict] = []
        self.scanning = False
        self._updating_combo = False
        self._typed = ""
        self.remote_buttons: list[Gtk.Widget] = []

        self._load_css()
        self._build_ui()

        self.connect("destroy", Gtk.main_quit)

    # -- setup ------------------------------------------------------------

    def _load_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_path(str(STYLE_PATH))
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self) -> None:
        if ICON_PATH.exists():
            try:
                self.set_icon_from_file(str(ICON_PATH))
            except GLib.Error:
                pass

        # Custom titlebar so the whole window keeps the purple remote look.
        titlebar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        titlebar.get_style_context().add_class("titlebar")
        title = Gtk.Label(label="Remoku")
        title.get_style_context().add_class("title")
        titlebar.pack_start(title, False, False, 0)

        self.device_combo = Gtk.ComboBoxText()
        self.device_combo.get_style_context().add_class("device-combo")
        self.device_combo.set_hexpand(True)
        self.device_combo.set_tooltip_text("Choose a Roku device")
        self.device_combo.connect("changed", self._on_device_selected)
        titlebar.pack_start(self.device_combo, True, True, 0)

        self.scan_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.BUTTON
        )
        self.scan_button.get_style_context().add_class("titlebar-button")
        self.scan_button.set_tooltip_text("Scan the network for Roku devices")
        self.scan_button.connect("clicked", lambda _button: self.scan())
        titlebar.pack_start(self.scan_button, False, False, 0)
        self.set_titlebar(titlebar)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.get_style_context().add_class("remote")
        root.set_border_width(12)
        self.add(root)

        # Power / info / replay / keyboard.
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.set_homogeneous(True)
        self.power_button = self._button(
            "system-shutdown-symbolic", cls="power", tooltip="Power"
        )
        self.power_button.connect("clicked", lambda _button: self.toggle_power())
        top.pack_start(self.power_button, False, False, 0)
        top.pack_start(
            self._button("dialog-information-symbolic", key="Info", tooltip="Info"),
            False,
            False,
            0,
        )
        top.pack_start(
            self._button(
                "view-refresh-symbolic", key="InstantReplay", tooltip="Instant replay"
            ),
            False,
            False,
            0,
        )
        root.pack_start(top, False, False, 0)

        # Back / D-pad / Home, like the middle of a Roku remote.
        middle = Gtk.Grid(column_spacing=14)
        middle.set_halign(Gtk.Align.CENTER)
        middle.attach(
            self._button("go-previous-symbolic", key="Back", tooltip="Back"), 0, 0, 1, 1
        )
        middle.attach(self._build_dpad(), 1, 0, 1, 1)
        middle.attach(
            self._button("go-home-symbolic", key="Home", tooltip="Home"), 2, 0, 1, 1
        )
        root.pack_start(middle, False, False, 0)

        # Playback row.
        playback = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        playback.set_halign(Gtk.Align.CENTER)
        playback.pack_start(
            self._button(
                "media-seek-backward-symbolic", key="Rev", tooltip="Rewind"
            ),
            False,
            False,
            0,
        )
        playback.pack_start(
            self._button(
                "media-playback-start-symbolic", key="Play", tooltip="Play / pause"
            ),
            False,
            False,
            0,
        )
        playback.pack_start(
            self._button(
                "media-seek-forward-symbolic", key="Fwd", tooltip="Fast forward"
            ),
            False,
            False,
            0,
        )
        root.pack_start(playback, False, False, 0)

        # Volume row (Roku TVs, or players with TV volume control enabled).
        self.volume_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.volume_row.set_halign(Gtk.Align.CENTER)
        self.volume_row.pack_start(
            self._button(
                "audio-volume-low-symbolic", key="VolumeDown", tooltip="Volume down"
            ),
            False,
            False,
            0,
        )
        self.volume_row.pack_start(
            self._button(
                "audio-volume-muted-symbolic", key="VolumeMute", tooltip="Mute"
            ),
            False,
            False,
            0,
        )
        self.volume_row.pack_start(
            self._button(
                "audio-volume-high-symbolic", key="VolumeUp", tooltip="Volume up"
            ),
            False,
            False,
            0,
        )
        root.pack_start(self.volume_row, False, False, 0)

        # TV inputs (custom names usually come from the TV's own settings).
        self.inputs_label = Gtk.Label(label="Inputs", xalign=0)
        self.inputs_label.get_style_context().add_class("section-label")
        self.inputs_flow = Gtk.FlowBox()
        self.inputs_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.inputs_flow.set_max_children_per_line(99)
        self.inputs_flow.set_row_spacing(4)
        self.inputs_flow.set_column_spacing(6)
        root.pack_start(self.inputs_label, False, False, 0)
        root.pack_start(self.inputs_flow, False, False, 0)

        # App shortcuts.
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.get_style_context().add_class("panel")
        panel_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        apps_label = Gtk.Label(label="Apps", xalign=0)
        apps_label.get_style_context().add_class("section-label")
        apps_label.set_hexpand(True)
        panel_header.pack_start(apps_label, True, True, 0)
        self.apps_refresh_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic", Gtk.IconSize.MENU
        )
        self.apps_refresh_button.get_style_context().add_class("titlebar-button")
        self.apps_refresh_button.set_tooltip_text("Reload the app list")
        self.apps_refresh_button.connect("clicked", lambda _button: self.reload_apps())
        panel_header.pack_start(self.apps_refresh_button, False, False, 0)
        panel.pack_start(panel_header, False, False, 0)

        self.apps_flow = Gtk.FlowBox()
        self.apps_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.apps_flow.set_homogeneous(True)
        self.apps_flow.set_max_children_per_line(4)
        self.apps_flow.set_min_children_per_line(3)
        self.apps_flow.set_row_spacing(6)
        self.apps_flow.set_column_spacing(6)
        apps_scroller = Gtk.ScrolledWindow()
        apps_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        apps_scroller.set_propagate_natural_width(False)
        apps_scroller.set_propagate_natural_height(False)
        apps_scroller.set_min_content_height(150)
        apps_scroller.set_vexpand(True)
        apps_scroller.add(self.apps_flow)
        panel.pack_start(apps_scroller, True, True, 0)
        root.pack_start(panel, True, True, 0)

        # Status line.
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.wake_button = Gtk.Button.new_with_label("Wake TV")
        self.wake_button.get_style_context().add_class("input-button")
        self.wake_button.set_tooltip_text(
            "Send a Wake-on-LAN packet and wait for the device to come up"
        )
        self.wake_button.set_visible(False)
        self.wake_button.connect("clicked", lambda _button: self.wake_tv())
        bottom.pack_start(self.wake_button, False, False, 0)
        self.spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(label="Looking for devices...", xalign=0)
        self.status_label.get_style_context().add_class("status")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        bottom.pack_start(self.spinner, False, False, 0)
        bottom.pack_start(self.status_label, True, True, 0)
        root.pack_start(bottom, False, False, 0)

        self._set_remote_sensitive(False)

        # Route key presses from every widget through the remote shortcuts.
        # GTK widgets such as combo boxes and flow boxes normally consume the
        # arrow keys for their own navigation; a handler connected to a widget
        # runs before its built-in handler, so the arrow keys always behave
        # like the D-pad on the remote instead.
        self._install_key_handlers(self)
        titlebar = self.get_titlebar()
        if titlebar is not None:
            self._install_key_handlers(titlebar)

    def _install_key_handlers(self, widget: Gtk.Widget) -> None:
        if not isinstance(widget, Gtk.Entry):
            widget.connect("key-press-event", self._on_key_press)
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                self._install_key_handlers(child)

    def _button(
        self,
        icon: str | None = None,
        label: str | None = None,
        key: str | None = None,
        cls: str = "",
        tooltip: str | None = None,
    ) -> Gtk.Button:
        if icon is not None:
            button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
        else:
            button = Gtk.Button.new_with_label(label or "")
        context = button.get_style_context()
        context.add_class("remote")
        if cls:
            context.add_class(cls)
        if tooltip:
            button.set_tooltip_text(tooltip)
        button.set_size_request(52, 52)
        button.set_halign(Gtk.Align.CENTER)
        button.set_valign(Gtk.Align.CENTER)
        if key:
            button.connect("clicked", lambda _button, k=key: self.send_key(k))
        self.remote_buttons.append(button)
        return button

    def _build_dpad(self) -> Gtk.Grid:
        grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True)
        grid.get_style_context().add_class("dpad")
        grid.set_size_request(216, 216)

        def wedge(icon: str, key: str, css_class: str, column: int, row: int) -> None:
            button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            context = button.get_style_context()
            context.add_class("dpad-key")
            context.add_class(css_class)
            button.set_tooltip_text(key)
            button.connect("clicked", lambda _button, k=key: self.send_key(k))
            self.remote_buttons.append(button)
            grid.attach(button, column, row, 1, 1)

        wedge("pan-up-symbolic", "Up", "dpad-up", 1, 0)
        wedge("pan-start-symbolic", "Left", "dpad-left", 0, 1)
        wedge("pan-end-symbolic", "Right", "dpad-right", 2, 1)
        wedge("pan-down-symbolic", "Down", "dpad-down", 1, 2)

        ok = Gtk.Button(label="OK")
        ok.get_style_context().add_class("dpad-ok")
        ok.set_tooltip_text("Select")
        ok.set_size_request(64, 64)
        ok.connect("clicked", lambda _button: self.send_key("Select"))
        self.remote_buttons.append(ok)
        grid.attach(ok, 1, 1, 1, 1)
        return grid

    # -- startup / device handling ---------------------------------------

    def start(self, initial_ip: str | None = None) -> None:
        saved = load_saved()
        devices = saved.get("devices", [])
        target = initial_ip or saved.get("last")
        if target and target not in {device.get("ip") for device in devices}:
            devices.append({"ip": target, "name": target})
        self.devices = devices
        self._render_devices(devices, active_ip=target)
        if target:
            # Connect first and only scan afterwards: a scan while connecting
            # can knock the network over and make the first attempt time out.
            self._connect(target, then_scan=True)
        else:
            self.scan()

    def _render_devices(self, devices: list[dict], active_ip: str | None = None) -> None:
        self._updating_combo = True
        self.device_combo.remove_all()
        active_found = False
        for device in devices:
            ip = device.get("ip")
            if not ip:
                continue
            self.device_combo.append(ip, device.get("name") or ip)
            if ip == active_ip:
                active_found = True
        if active_found:
            self.device_combo.set_active_id(active_ip)
        self._updating_combo = False

    def _on_device_selected(self, _combo: Gtk.ComboBoxText) -> None:
        if self._updating_combo:
            return
        ip = self.device_combo.get_active_id()
        if ip:
            self._connect(ip)

    def _select_device(self, ip: str) -> None:
        if self.device and self.device.ip == ip:
            return
        self._updating_combo = True
        self.device_combo.set_active_id(ip)
        self._updating_combo = False
        self._connect(ip)

    def _connect(self, ip: str, then_scan: bool = False) -> None:
        self.device = Roku(ip)
        self.info = None
        self._set_remote_sensitive(False)
        self._set_status(f"Connecting to {ip}...")
        device = self.device

        def work():
            last_error = None
            for attempt in range(3):
                try:
                    info = device.info()
                    break
                except RokuPermissionError:
                    raise  # retrying will not help
                except Exception as exc:
                    last_error = exc
                    if attempt < 2:
                        GLib.idle_add(
                            self._set_status,
                            f"Could not reach {ip}, retrying ({attempt + 2}/3)...",
                        )
                        time.sleep(1.5)
            else:
                raise last_error

            # A sleeping TV refuses everything except device-info, so a
            # refused app list must not stop the remote from connecting.
            try:
                return info, device.apps(), None
            except Exception as exc:
                return info, [], exc

        def done(result, error):
            if then_scan:
                self.scan()
            if error:
                self._set_status(f"Connection failed: {error}")
                return
            info, apps, apps_error = result
            self.info = info
            self._apply_info(info)
            self._populate_apps(apps)
            self._remember(info)
            self._set_remote_sensitive(True)
            if _is_standby(info):
                self._set_status(
                    f"Connected to {info['name']} - it is in standby, press Wake TV"
                )
            elif apps_error:
                self._set_status(
                    f"Connected to {info['name']} - app list unavailable ({apps_error})"
                )
            else:
                self._set_status(f"Connected to {info['name']}")

        self.run_async(work, done)

    def _apply_info(self, info: dict) -> None:
        self.power_button.set_visible(bool(info.get("is_tv") or info.get("supports_tv_power")))
        self.volume_row.set_visible(bool(info.get("is_tv") or info.get("supports_volume")))
        self.wake_button.set_visible(_is_standby(info))

    def _remember(self, info: dict) -> None:
        saved = load_saved()
        devices = {
            device.get("ip"): device
            for device in saved.get("devices", [])
            if device.get("ip")
        }
        devices[info["ip"]] = {
            key: info.get(key)
            for key in (
                "ip",
                "name",
                "model",
                "serial",
                "mac",
                "ethernet_mac",
                "is_tv",
                "supports_tv_power",
                "supports_volume",
                "supports_find_remote",
            )
        }
        ordered = sorted(devices.values(), key=lambda device: (device.get("name") or "").lower())
        save_saved({"last": info["ip"], "devices": ordered})
        self.devices = ordered

    # -- scanning ---------------------------------------------------------

    def scan(self) -> None:
        if self.scanning:
            return
        self.scanning = True
        self.spinner.start()
        self._set_status("Scanning the network for Roku devices...")

        def work():
            return discover(progress=lambda message: GLib.idle_add(self._scan_progress, message))

        def done(result, error):
            self.scanning = False
            self.spinner.stop()
            if error:
                self._set_status(f"Scan failed: {error}")
                return
            saved = load_saved()
            by_ip = {
                device["ip"]: device
                for device in saved.get("devices", [])
                if device.get("ip")
            }
            for info in result:
                by_ip[info["ip"]] = {**by_ip.get(info["ip"], {}), **info}
            devices = sorted(
                by_ip.values(), key=lambda device: (device.get("name") or "").lower()
            )
            save_saved({"last": saved.get("last"), "devices": devices})
            self.devices = devices
            active = self.device.ip if self.device else saved.get("last")
            self._render_devices(devices, active_ip=active)
            # If the remembered device could not be reached, fall back to the
            # first device the scan found.
            if not self.info and devices:
                self._select_device(devices[0]["ip"])
            if self.info and _is_standby(self.info):
                # More important than the device count: the user cannot send
                # commands until this one is awake.
                self._set_status(
                    f"Connected to {self.info['name']} - it is in standby, press Wake TV"
                )
                return
            count = len(result)
            self._set_status(
                f"Found {count} Roku device{'s' if count != 1 else ''}"
                if count
                else "No Roku devices found"
            )

        self.run_async(work, done)

    def _scan_progress(self, message: str) -> None:
        """Show scan progress, but never hide the standby hint."""
        if self.info and _is_standby(self.info):
            return
        self._set_status(message)

    # -- apps -------------------------------------------------------------

    def reload_apps(self) -> None:
        if self.device is None:
            return
        device = self.device
        self._set_status("Reloading apps...")

        def work():
            return device.apps()

        def done(result, error):
            if error:
                self._set_status(f"Could not load apps: {error}")
                return
            self._populate_apps(result)
            self._set_status("App list updated")

        self.run_async(work, done)

    def _populate_apps(self, apps) -> None:
        streaming = [app for app in apps if app.kind == "app"]
        inputs = [app for app in apps if app.kind == "input"]

        for child in self.apps_flow.get_children():
            self.apps_flow.remove(child)
        for app in streaming:
            self.apps_flow.add(self._app_tile(app))
        self.apps_flow.show_all()

        for child in self.inputs_flow.get_children():
            self.inputs_flow.remove(child)
        for app in inputs:
            button = Gtk.Button.new_with_label(app.name)
            button.get_style_context().add_class("input-button")
            button.set_tooltip_text(app.name)
            button.connect("clicked", lambda _button, a=app: self.launch_app(a))
            self.inputs_flow.add(button)
        self.inputs_flow.show_all()

        has_inputs = bool(inputs)
        self.inputs_label.set_visible(has_inputs)
        self.inputs_flow.set_visible(has_inputs)

    def _app_tile(self, app) -> Gtk.Button:
        button = Gtk.Button()
        button.get_style_context().add_class("app-tile")
        button.set_tooltip_text(app.name)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        image = Gtk.Image()
        image.set_size_request(48, 48)
        image.set_from_icon_name("application-x-executable-symbolic", Gtk.IconSize.DIALOG)
        label = Gtk.Label(label=app.name)
        label.get_style_context().add_class("app-label")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(12)
        box.pack_start(image, True, True, 0)
        box.pack_start(label, False, False, 0)
        button.add(box)
        button.connect("clicked", lambda _button, a=app: self.launch_app(a))
        self._load_icon_async(app, image)
        return button

    def _load_icon_async(self, app, image: Gtk.Image) -> None:
        cached = self._cached_icon_path(app.id)
        if cached:
            self._apply_icon(image, cached)
            return
        if self.device is None:
            return
        device = self.device

        def work():
            data, content_type = device.icon(app.id)
            mime = content_type.split(";")[0].strip().lower()
            extension = ICON_EXTENSIONS.get(mime, ".png")
            ICON_DIR.mkdir(parents=True, exist_ok=True)
            path = ICON_DIR / f"{app.id.replace('/', '_')}{extension}"
            path.write_bytes(data)
            return path

        def done(result, error):
            if not error and result:
                self._apply_icon(image, result)

        self.run_async(work, done)

    @staticmethod
    def _apply_icon(image: Gtk.Image, path) -> None:
        """Show an app icon scaled to fit a 48x48 tile."""
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(path), 48, 48, True
            )
        except GLib.Error:
            return
        image.set_from_pixbuf(pixbuf)

    def _cached_icon_path(self, app_id: str) -> Path | None:
        for extension in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
            path = ICON_DIR / f"{app_id.replace('/', '_')}{extension}"
            if path.exists():
                return path
        return None

    # -- commands ---------------------------------------------------------

    def send_key(self, key: str) -> None:
        if self.device is None:
            self._set_status("No device connected")
            return
        device = self.device

        def work():
            device.keypress(key)

        def done(_result, error):
            if error:
                self._report_error(error)

        self.run_async(work, done)

    def _report_error(self, error: Exception) -> None:
        if isinstance(error, RokuPermissionError) and _is_standby(self.info):
            self._set_status("The device is in standby - press Wake TV to turn it on")
        else:
            self._set_status(str(error))

    def wake_tv(self) -> None:
        """Wake a sleeping device.

        Roku TVs in standby only answer device-info and refuse everything else
        with HTTP 403, which looks like the remote being disabled. Roku's own
        app sends a Wake-on-LAN magic packet first; this does the same and then
        waits for the device to come up.
        """
        if self.device is None:
            return
        info = self.info or {}
        macs = [mac for mac in (info.get("mac"), info.get("ethernet_mac")) if mac]
        if not macs:
            self._set_status("No MAC address known for this device, cannot wake it")
            return
        device = self.device
        self.wake_button.set_sensitive(False)
        self.spinner.start()
        self._set_status("Sending a wake packet...")

        def work():
            device.wake(*macs)
            deadline = time.time() + 30
            while time.time() < deadline:
                time.sleep(2)
                try:
                    info = device.info()
                except Exception:
                    continue
                if not _is_standby(info):
                    return info
            return None

        def done(result, error):
            self.spinner.stop()
            self.wake_button.set_sensitive(True)
            if error:
                self._set_status(f"Wake failed: {error}")
                return
            if result is None:
                self._set_status(
                    "Wake packet sent, but the TV did not come up. On the TV, "
                    "enable Settings > System > Power > Fast TV start"
                )
                return
            self.info = result
            self._apply_info(result)
            self.reload_apps()
            self._set_status(f"{result['name']} is awake")

        self.run_async(work, done)

    def toggle_power(self) -> None:
        if self.device is None:
            return
        power_mode = (self.info or {}).get("power")
        device = self.device

        def work():
            device.toggle_power(power_mode)

        def done(_result, error):
            if error:
                self._report_error(error)
            else:
                self._set_status("Power toggled")
                GLib.timeout_add(1500, self._refresh_power)

        self.run_async(work, done)

    def _refresh_power(self) -> bool:
        if self.device is None:
            return False
        device = self.device

        def work():
            return device.info()

        def done(result, error):
            if not error:
                self.info = result
                self._apply_info(result)

        self.run_async(work, done)
        return False

    def launch_app(self, app) -> None:
        if self.device is None:
            return
        device = self.device
        self._set_status(f"Launching {app.name}...")

        def work():
            if app.kind == "input":
                device.launch_input(app.id)
            else:
                device.launch(app.id)

        def done(_result, error):
            if error:
                self._report_error(error)

        self.run_async(work, done)

    # -- keyboard input ---------------------------------------------------

    def _on_key_press(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        focus = self.get_focus()
        if isinstance(focus, Gtk.Entry):
            return False
        keyval = Gdk.keyval_to_lower(event.keyval)

        # Ctrl+Enter activates the focused on-screen widget. The other Ctrl/Super
        # combinations are left to the window manager.
        if event.state & (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.MOD4_MASK
            | Gdk.ModifierType.SUPER_MASK
        ):
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                key = CTRL_SHORTCUTS.get(keyval)
                if key:
                    self._typed = ""
                    self.send_key(key)
                    return True
                if (
                    keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
                    and focus is not None
                ):
                    if isinstance(focus, Gtk.Button):
                        focus.emit("clicked")
                    else:
                        focus.activate()
                    return True
            return False

        # Alt+<key> shortcuts for the remote functions that used to have
        # single-key bindings.
        if event.state & Gdk.ModifierType.MOD1_MASK:
            key = ALT_SHORTCUTS.get(keyval)
            if key:
                self._typed = ""
                self.send_key(key)
                return True
            return False

        # Navigation keys always act like the remote.
        key = NAV_KEYS.get(keyval)
        if key:
            self._typed = ""
            self.send_key(key)
            return True

        # Everything printable is sent to the Roku as text. Roku ignores these
        # unless its on-screen keyboard is showing, so this is harmless in
        # every other context and means typing just works in a text field.
        text = event.string
        if text and text.isprintable():
            for character in text:
                self.send_key(f"Lit_{character}")
            self._typed = (self._typed + text)[-40:]
            self._set_status(f"Typed to Roku: {self._typed}")
            return True
        return False

    # -- helpers ----------------------------------------------------------

    def _set_remote_sensitive(self, sensitive: bool) -> None:
        for widget in self.remote_buttons:
            widget.set_sensitive(sensitive)
        self.apps_refresh_button.set_sensitive(sensitive)

    def _set_status(self, message: str) -> None:
        self.status_label.set_text(message)

    def run_async(self, work, done) -> None:
        """Run work() in a thread, then call done(result, error) on the UI thread."""

        def worker():
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001 - surfaced in the status bar
                GLib.idle_add(done, None, exc)
            else:
                GLib.idle_add(done, result, None)

        threading.Thread(target=worker, daemon=True).start()


def run(initial_ip: str | None = None) -> int:
    window = RemoteWindow(initial_ip)
    window.show_all()
    window.volume_row.set_visible(True)
    window.start(initial_ip)
    Gtk.main()
    return 0
