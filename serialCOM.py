#!/usr/bin/env python3
"""Interactive fli-cli serial console.

    sudo python3 serialCOM.py              # connect to /dev/ttyACM0
    sudo python3 serialCOM.py -p /dev/ttyACM1
    python3 serialCOM.py --list            # show serial ports
    python3 serialCOM.py --sim             # talk to the mock camera

Commands are bounded by a timeout instead of blocking forever when the camera
does not answer.
"""
from __future__ import annotations

import argparse

from serial_console import DEFAULT_BAUD, DEFAULT_PORT, SIM_PORT, list_ports, open_link
from simulator import SimState


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('-p', '--port', default=None, help=f'serial port (default {DEFAULT_PORT})')
    parser.add_argument('-b', '--baud', type=int, default=DEFAULT_BAUD)
    parser.add_argument('-t', '--timeout', type=float, default=2.0,
                        help='per-command response timeout in seconds')
    parser.add_argument('--sim', action='store_true', help='use the mock camera')
    parser.add_argument('--list', action='store_true', help='list ports and exit')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    ports = list_ports()
    if args.list:
        for port in ports:
            print(port.label)
        print('\n(* = looks like a First Light camera)')
        return 0

    port = args.port or (SIM_PORT if args.sim else DEFAULT_PORT)
    try:
        link = open_link(port, args.baud, state=SimState())
    except Exception as exc:
        print(f'Could not open {port}: {exc}')
        print('\nPorts available:')
        for p in ports:
            print(f'  {p.label}')
        return 1

    print(f'Connected to {link.port}. Type a command, or "exit" to quit.')
    try:
        while True:
            try:
                command = input('fli> ').strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not command:
                continue
            if command.lower() in ('exit', 'quit'):
                break
            try:
                print(link.command(command, timeout=args.timeout) or '(no response)')
            except Exception as exc:
                print(f'Error: {exc}')
    finally:
        link.close()
        print('Serial connection closed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
