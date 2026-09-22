"""Command line entry point for remoku."""

from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="remoku",
        description="Control Roku devices over the local network.",
    )
    parser.add_argument("--ip", help="device IP to use instead of the last one")
    parser.add_argument(
        "--list", action="store_true", help="list discovered devices and exit"
    )
    parser.add_argument(
        "--apps", action="store_true", help="list the device's apps and inputs, then exit"
    )
    parser.add_argument(
        "--key", metavar="KEY", help="send one keypress to the device, then exit"
    )
    args = parser.parse_args(argv)

    if args.list:
        from .discovery import discover

        devices = discover(progress=lambda message: print(message, file=sys.stderr))
        if not devices:
            print("No Roku devices found.", file=sys.stderr)
            return 1
        for device in devices:
            print(f'{device["ip"]:16} {device["name"]:28} {device["model"]}')
        return 0

    if args.apps or args.key:
        from .discovery import last_used_ip
        from .ecp import Roku, RokuError

        ip = args.ip or last_used_ip()
        if not ip:
            print("No device specified and none remembered; pass --ip.", file=sys.stderr)
            return 1
        device = Roku(ip)
        try:
            if args.apps:
                for app in device.apps():
                    print(f"{app.kind:5} {app.id:24} {app.name}")
            if args.key:
                device.keypress(args.key)
                print(f"Sent {args.key} to {ip}")
        except RokuError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    from .app import run

    return run(args.ip)


if __name__ == "__main__":
    sys.exit(main())
