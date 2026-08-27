#!/usr/bin/env python3
"""C-RED camera control app for the Jetson.

    python3 main.py

Connects to the camera, puts it in free-running mode, reads its geometry and
frame rate, and starts streaming - so the first thing you see is the picture.
Everything else is behind a disclosure.

Binds to 0.0.0.0 and does not open a browser, because the Jetson is normally
headless: open the URL it prints from any machine on the same network.

    python3 main.py --sim       # synthetic frames, no camera needed
    python3 main.py --no-auto   # do not connect or start on launch
"""
from __future__ import annotations

import argparse

from nicegui import app, ui

import camera
from camera_viewer import CameraViewer
from capture_frames import CaptureFrames
from crop_panel import CropPanel
from serial_console import SerialConsole
from session import Session
from settings_panel import SettingsPanel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default='0.0.0.0',
                        help='bind address (default 0.0.0.0, reachable on the LAN)')
    parser.add_argument('--port', type=int, default=8080, help='web server port')
    parser.add_argument('--sim', action='store_true',
                        help='use the built-in simulator instead of the camera')
    parser.add_argument('--sdk', default=None,
                        help='path to libfliusbsdk.so (overrides FLI_USB_SDK_LIB)')
    parser.add_argument('--no-auto', action='store_true',
                        help='do not connect and start acquisition on launch')
    parser.add_argument('--show', action='store_true',
                        help='open a local browser (only with a display attached)')
    parser.add_argument('--reload', action='store_true',
                        help='restart on source changes (development)')
    parser.add_argument('--verbose', action='store_true',
                        help="also print the SDK's INFO messages to the terminal")
    return parser.parse_args()


ARGS = parse_args()
camera.set_verbose(ARGS.verbose)
SESSION = Session(prefer_simulator=ARGS.sim, sdk_library=ARGS.sdk)
app.on_shutdown(SESSION.shutdown)

if not ARGS.no_auto:
    # Bring the camera up once, as the process starts - not per browser client.
    app.on_startup(SESSION.autostart)


@ui.page('/')
def index() -> None:
    """Built per client, so two browsers do not fight over the same widgets."""
    with ui.header().classes('items-center justify-between px-5 py-2') \
            .style('background: #161616; box-shadow: none'):
        ui.label('C-RED').classes('text-sm tracking-widest opacity-70')
        dot = ui.icon('circle', size='10px').classes('opacity-60')

    with ui.column().classes('w-full max-w-3xl mx-auto px-4 py-4 gap-4'):
        viewer = CameraViewer(SESSION)
        CaptureFrames(SESSION)

        with ui.column().classes('w-full gap-0 mt-2'):
            with ui.expansion('Crop', icon='crop_free').classes('w-full'):
                CropPanel(SESSION)
            with ui.expansion('Console', icon='terminal').classes('w-full'):
                SerialConsole(SESSION)
            with ui.expansion('Settings', icon='tune').classes('w-full'):
                SettingsPanel(SESSION, viewer)

    class StatusDot:
        """Registered like a card so any state change repaints the header."""

        def sync(self) -> None:
            if not SESSION.connected:
                dot.classes(replace='opacity-30 text-grey')
            elif SESSION.acquiring:
                dot.classes(replace='opacity-90 text-green-400')
            else:
                dot.classes(replace='opacity-70 text-amber-400')

    badge = StatusDot()
    SESSION.register(badge)
    badge.sync()


def main() -> None:
    try:
        ui.run(
            host=ARGS.host,
            port=ARGS.port,
            title='C-RED',
            favicon='📷',
            dark=True,            # dark only - no toggle
            show=ARGS.show,       # headless Jetson: do not try to launch a browser
            reload=ARGS.reload,
            uvicorn_logging_level='warning',
            # Every preview frame is an outgoing message carrying a base64
            # PNG.  NiceGUI keeps the last 1000 per client so a reconnecting
            # browser can catch up, which at a few hundred KB a frame is
            # hundreds of MB of retained payload and a steadily slower event
            # loop.  The preview is live video - a reconnecting client wants
            # the next frame, not the last thousand.
            message_history_length=0,
        )
    except KeyboardInterrupt:
        # uvicorn shuts down cleanly on Ctrl-C and then re-raises the signal it
        # captured, which otherwise dumps a traceback over an ordinary exit.
        pass
    finally:
        # on_shutdown has usually run by now; this is the belt-and-braces path
        # for the cases where it has not.
        SESSION.shutdown()
        print('\nStopped. Camera released.')


# NiceGUI's reloader re-imports this module as __mp_main__ in a child process,
# so both names must reach ui.run().
if __name__ in {'__main__', '__mp_main__'}:
    main()
