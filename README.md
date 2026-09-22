# remoku

A small GTK3 remote for Roku TVs and players, built directly on Roku's
public [External Control Protocol (ECP)](https://developer.roku.com/docs/developer-program/dev-tools/external-control-api.md).

Every Roku runs a tiny REST server on TCP port 8060. This app talks to it
directly — no accounts, no cloud, no companion service.

## Features

- Purple Roku-style remote: power, back, home, info, instant replay, D-pad
  with OK, playback, volume
- App shortcuts grid, loaded from the device (`/query/apps`) with real icons;
  click a tile to launch
- TV inputs in their own row, including custom names (e.g. "Nintendo Switch",
  "Soundbar") configured on the TV
- Device discovery: SSDP, plus an ARP-assisted scan of the local /24 that
  stays gentle on consumer routers (broad high-concurrency scans can take the
  whole Wi-Fi network down for ~10 seconds)
- Wake button: Roku TVs in standby only answer `device-info` and reply 403 to
  everything else, so the app offers to wake them with a Wake-on-LAN packet
- Typing: letters, digits and punctuation go straight to the Roku whenever its
  on-screen keyboard is up, with no mode to toggle
- Keyboard control: the arrow keys behave like the D-pad, so the whole remote
  is usable without a mouse

## Requirements

- Python 3.10+
- `python-gobject` (GTK3) and `python-requests`

On Arch/CachyOS:

```bash
sudo pacman -S python-gobject gtk3 python-requests
```

## Usage

```bash
make run                 # start the GUI
./bin/remoku             # same thing
./bin/remoku --ip 192.0.2.50
make list                # print Roku devices found on the network
make apps                # print apps/inputs of the last used device
make key KEY=Home        # send a single keypress
```

## Install a launcher

```bash
make install
```

This adds:

- a `remoku` command in `~/.local/bin` (with a `rokuremote` alias), so you can
  start it from any terminal (and from GNOME's "Run a Command" dialog, Alt+F2)
- a **Remoku** entry in your app grid, searchable as "remoku" or "rokuremote"
  (the desktop entry carries the keywords `remoku;rokuremote;roku;remote;tv;`)

`make uninstall` removes both.

The last used device is remembered in `~/.config/remoku/devices.json`;
app icons are cached in `~/.cache/remoku/icons/`.

## Secret scanning

A pre-commit hook blocks commits that contain secrets and warns (without
blocking) when staged lines look like local/private values such as LAN
addresses, MAC addresses or home directory paths.

```bash
make hooks     # enable the hook for this clone (per-clone git config)
make audit     # scan the whole repo, history included, any time
```

It runs [gitleaks](https://github.com/gitleaks/gitleaks) when installed
(`sudo pacman -S gitleaks`) and falls back to a small built-in check otherwise.
A deliberate example can be marked on its line with the comment
`sensitive-example`; `git commit --no-verify` bypasses the hook entirely.

## Keyboard shortcuts

| Key | Action |
| --- | --- |
| Arrows | Up, Down, Left, Right |
| Enter | Select (OK) |
| Esc | Home |
| Backspace | Backspace (deletes while typing) |
| Letters / digits / punctuation / space | Typed to the Roku |
| Alt + W / A / S / D | Up / Left / Down / Right |
| Alt + B | Back |
| Alt + H | Home |
| Alt + I | Info |
| Alt + O | Select (OK) |
| Alt + R, F or P | Rewind, Fast forward, Play/pause |
| Alt + `,` `.` or `/` | Rewind, Fast forward, Play/pause |
| Alt + `[` or `-` | Volume down |
| Alt + `]` or `+` | Volume up |
| Alt + `\` or M | Mute |
| Ctrl + Up / Down | Volume up / down |
| Ctrl+Enter | Activate the focused on-screen button |
| Tab / Shift+Tab | Move focus through the window |

Printable keys are always forwarded to the Roku as `Lit_` characters. Roku
ignores those unless a text field is focused, so typing simply works whenever
the TV's on-screen keyboard is up. Roku's API has no way to ask whether a
keyboard is open (verified against `/query/active-app`, `/query/device-info`
and friends), which is why the remote keys that used to live on letters moved
to Alt combinations.

## Notes and troubleshooting

- Since Roku OS 14.1, remote commands require **Settings → System → Advanced
  system settings → Control by mobile apps → Enabled** on the device. If a
  command is refused, the app says so in the status bar.
- A Roku TV in standby (`power-mode: Ready`) answers `device-info` but
  refuses every other command with HTTP 403, which looks like the setting
  above being off even when it is on. In that case the app shows a **Wake TV**
  button, which sends a Wake-on-LAN magic packet and waits for the TV to come
  up (this is what Roku's own mobile app does). If waking does not work,
  enable **Settings → System → Power → Fast TV start** on the TV.
- SSDP discovery is unreliable on many Wi-Fi networks because access points
  drop multicast between clients. When that happens the app falls back to
  scanning: it pokes the kernel into resolving every address in the local /24
  via ARP (a single UDP datagram each), then TCP-probes only the hosts that
  actually exist. If the ARP table cannot be read it falls back to a
  low-concurrency TCP scan, and it never scans wider than a /24.
- Connecting to a device is retried up to three times if the network is
  briefly busy, and the startup scan runs only after the first connection
  attempt finishes.
- Power/volume buttons only appear for devices that report support for them
  (Roku TVs, or players using TV controls over HDMI-CEC).
