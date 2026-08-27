#!/usr/bin/env python3
"""Capture N frames to a .raw file from the command line.

    sudo python3 acquire.py -N 100 out.raw                 # geometry from the camera
    sudo python3 acquire.py -W 640 -H 512 -N 100 out.raw   # geometry forced
    python3 acquire.py --sim -N 50 test.raw                # no hardware needed

Geometry is read from the camera when you do not supply it, and every capture
gets a .json sidecar so read_raw.py can reshape the file without being told.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import rawio
import camera as camera_module
from camera import Camera, CameraError, FrameHub, SimulatedCamera
from serial_console import (DEFAULT_PORT, SIM_PORT, list_ports, open_link,
                            set_free_running)
from simulator import SimState


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('output', help='destination .raw file')
    parser.add_argument('-N', '--frames', type=int, default=100, help='frames to capture')
    parser.add_argument('-W', '--width', type=int, default=None, help='frame width')
    parser.add_argument('-H', '--height', type=int, default=None, help='frame height')
    parser.add_argument('-p', '--port', default=None,
                        help=f'serial port (default {DEFAULT_PORT})')
    parser.add_argument('--sim', action='store_true', help='use the simulator')
    parser.add_argument('--sdk', default=None, help='path to libfliusbsdk.so')
    parser.add_argument('--timeout', type=float, default=30.0,
                        help='give up if the capture stalls this long (seconds)')
    parser.add_argument('--list-ports', action='store_true',
                        help='list serial ports and exit')
    parser.add_argument('--no-free-run', action='store_true',
                        help='leave the trigger mode alone (external trigger)')
    parser.add_argument('--verbose', action='store_true',
                        help="also print the SDK's INFO messages")
    return parser.parse_args(argv)


def pick_port(requested: str | None, simulate: bool) -> str:
    if simulate:
        return SIM_PORT
    if requested:
        return requested
    devices = [p.device for p in list_ports(include_simulator=False)]
    if DEFAULT_PORT in devices:
        return DEFAULT_PORT
    for port in list_ports(include_simulator=False):
        if port.likely:
            return port.device
    if not devices:
        raise SystemExit('No serial ports found. Use --sim to try the simulator.')
    return devices[0]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list_ports:
        for port in list_ports():
            print(port.label)
        return 0

    camera_module.set_verbose(args.verbose)
    # A shell that backgrounds a job hands the child SIGINT already ignored;
    # restore it so Ctrl-C (and kill -INT) always stop a capture.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    hub = FrameHub()
    sim_state = SimState()
    port = pick_port(args.port, args.sim)
    simulate = port == SIM_PORT

    print(f'Opening {port} ...')
    link = open_link(port, state=sim_state)

    if not args.no_free_run:
        print('Setting free-running mode ...')
        set_free_running(link, print)

    width, height, fps, tint = args.width, args.height, 0.0, 0.0
    try:
        reported_w, reported_h = link.query_geometry()
        fps = link.query_fps()
        try:
            tint = link.query_tint()
        except Exception:
            tint = 0.0
        print(f'Camera reports {reported_w}x{reported_h} @ {fps:.3f} fps'
              + (f', tint {tint * 1e6:.1f} us' if tint else ''))
        width = width or reported_w
        height = height or reported_h
    except Exception as exc:
        print(f'Could not read camera settings ({exc}).')
        if width is None or height is None:
            link.close()
            raise SystemExit('Pass -W and -H explicitly.')

    camera = (SimulatedCamera(hub, sim_state, print) if simulate
              else Camera(hub, print, library=args.sdk))
    try:
        print(camera.open())
        hub.configure(width, height)
        job = hub.arm_recording(Path(args.output), args.frames, fps)
        # This tool only records, so the SDK's frame-counter check stays on.
        camera.start(width, height, fps, tag_check=True, viewer=False)

        print(f'Capturing {args.frames} frames of {width}x{height} ...')
        last_count, stalled_since = 0, time.monotonic()
        while not job.finished.wait(0.1):
            for line in camera.errors.drain():
                print(f'\n{line}', flush=True)
            if job.count != last_count:
                last_count, stalled_since = job.count, time.monotonic()
                print(f'\r  {job.count}/{job.frames}', end='', flush=True)
            elif time.monotonic() - stalled_since > args.timeout:
                print(f'\nStalled at {job.count}/{job.frames} frames.')
                hub.disarm_recording()
                break
        print(f'\r  {job.count}/{job.frames}')

        camera.stop()
        for line in camera.errors.drain():
            print(line, flush=True)
        if job.count == 0:
            raise SystemExit('No frames captured.')
        errors = camera.errors.errors

        elapsed = max((job.last_frame or 0) - (job.first_frame or 0), 1e-9)
        startup = max((job.first_frame or 0) - job.started, 0.0)
        buffer = job.buffer[:job.count * job.frame_bytes]
        raw_path, json_path = rawio.write_capture(
            args.output, buffer, width=width, height=height, frames=job.count,
            fps=fps, source='simulator' if simulate else 'fli-usb-sdk', tint=tint,
            extra={'measured_fps': round(max(job.count - 1, 1) / elapsed, 3),
                   'capture_seconds': round(elapsed, 3),
                   'startup_seconds': round(startup, 3)})
        meta = rawio.read_meta(raw_path) or {}
        for line in rawio.log_lines(raw_path, json_path, meta, errors):
            print(line)
        return 0
    except CameraError as exc:
        print(f'Camera error: {exc}', file=sys.stderr)
        return 1
    finally:
        camera.close()
        link.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('\nInterrupted.')
        raise SystemExit(130)
