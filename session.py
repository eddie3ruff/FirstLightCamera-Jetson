"""Shared state for one running app: the camera, the serial link, the log.

The hardware is global to the process - there is one camera and one serial
port - while the UI cards are built per browser client.  This object is the
seam between the two.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from camera import Camera, FrameHub, RecordJob, SimulatedCamera
from serial_console import (DEFAULT_BAUD, FULL_FRAME, FULL_SIZE, SIM_PORT,
                            SYNC_PARAMS, SerialLink, command_rejected,
                            crop_commands, first_line, open_link,
                            parse_crop_ranges, parse_cropping, parse_fps,
                            frame_rate_for, set_free_running,
                            WINDOW_SIZES)
from simulator import SimState


@dataclass
class Settings:
    colormap: str = 'turbo'
    autoscale: bool = True
    display_low: float = 0.0
    display_high: float = 16383.0
    preview_fps: float = 12.0
    output_dir: str = 'captures'
    use_camera_geometry: bool = True
    free_run_on_start: bool = True
    #  Crop window: FULL_SIZE (640) means the whole sensor.
    crop_size: int = FULL_SIZE
    crop_x: int = 192
    crop_y: int = 268
    #  Applied after every crop change, along with the fastest frame
    #  rate the new window allows.
    target_tint: float = 10e-6
    width: int = 640
    height: int = 512
    fps: float = 600.0
    tint: float = 0.0


class Session:
    def __init__(self, prefer_simulator: bool = False,
                 sdk_library: str | None = None) -> None:
        self.hub = FrameHub()
        self.sim_state = SimState()
        self.settings = Settings()
        self.prefer_simulator = prefer_simulator
        self.sdk_library = sdk_library
        self.link: SerialLink | None = None
        self.camera = None
        self.last_stats: dict = {}
        self.record_job: RecordJob | None = None
        self.record_task: asyncio.Task | None = None
        self.record_message = ''
        self.settings_token = 0
        self.tag_check_active = False
        self._log: deque = deque(maxlen=1000)
        self._seq = 0
        self._pending_message: str | None = None
        self._pending_stamp = ''
        self._pending_count = 0
        self._cards: list = []

    # ------------------------------------------------------------------ log

    def log(self, message: str) -> None:
        """Queue a line, collapsing an immediate repeat into a count.

        A desynchronised stream emits one SDK diagnostic per frame.  ui.log is
        append-only, so the collapsing has to happen before the line is pushed:
        identical messages just bump a counter, and the pending line is
        flushed when the message changes or the UI next reads the log.
        """
        if message == self._pending_message:
            self._pending_count += 1
            return
        self._flush_pending()
        self._pending_message = message
        self._pending_count = 1
        self._pending_stamp = time.strftime('%H:%M:%S')

    def _flush_pending(self) -> None:
        message = self._pending_message
        if message is None:
            return
        self._pending_message = None
        suffix = f' (x{self._pending_count})' if self._pending_count > 1 else ''
        self._seq += 1
        self._log.append((self._seq, f'[{self._pending_stamp}] {message}{suffix}'))

    def log_block(self, message: str) -> None:
        """Log a multi-line response: stamp the first row, indent the rest."""
        lines = str(message).split('\n')
        self.log(lines[0])
        self._flush_pending()
        pad = ' ' * 11  # line up under the "[HH:MM:SS] " stamp
        for line in lines[1:]:
            self._seq += 1
            self._log.append((self._seq, pad + line))

    def log_since(self, seq: int) -> tuple[list[str], int]:
        self._flush_pending()
        return [text for n, text in self._log if n > seq], self._seq

    # ---------------------------------------------------------------- cards

    def register(self, card) -> None:
        """Cards register so a state change in one refreshes the others."""
        self._cards.append(card)

    def sync_all(self) -> None:
        """Refresh every live card, dropping any whose client has gone away.

        Cards are per browser client, so without the pruning this list would
        grow on every page load and a long-lived Jetson session would leak.
        """
        alive = []
        for card in self._cards:
            try:
                card.sync()
            except Exception:
                continue  # client disconnected; its elements are gone
            alive.append(card)
        self._cards = alive

    def bump_settings(self) -> None:
        """Force the preview to re-fetch after a display setting changes."""
        self.settings_token += 1

    # ---------------------------------------------------------- connection

    @property
    def connected(self) -> bool:
        return self.link is not None and self.link.connected

    @property
    def simulated(self) -> bool:
        return self.link is not None and self.link.is_simulated

    @property
    def acquiring(self) -> bool:
        return self.camera is not None and self.camera.running

    def connect(self, port: str, baudrate: int = DEFAULT_BAUD) -> str:
        self.disconnect()
        self.link = open_link(port, baudrate, state=self.sim_state)
        if self.simulated:
            self.camera = SimulatedCamera(self.hub, self.sim_state, self.log)
        else:
            self.camera = Camera(self.hub, self.log, library=self.sdk_library)
        message = ('Connected to the simulated camera.' if self.simulated
                   else f'Connected to {port} at {baudrate} baud.')
        self.log(self.camera.open())
        return message

    def disconnect(self) -> None:
        self.stop_acquisition()
        if self.camera is not None:
            try:
                self.camera.close()
            except Exception as exc:
                self.log(f'Error closing camera: {exc}')
            self.camera = None
        if self.link is not None:
            self.link.close()
            self.link = None

    # --------------------------------------------------------- acquisition

    async def read_geometry(self, quiet: bool = False,
                            sync_crop: bool = False) -> None:
        """Read the camera's geometry, rate and integration time.

        `sync_crop` also points the crop picker at the window the camera is
        currently using.  It is off by default and on only when connecting or
        just after applying a crop: otherwise a routine geometry read - one
        happens every time acquisition starts - would overwrite the size you
        just picked with the size the camera is still on, and the selector
        would snap back.
        """
        """Ask the camera for its cropping and frame rate."""
        if not self.connected:
            return
        try:
            cropping = await asyncio.to_thread(self.link.command, 'cropping')
            width, height = parse_cropping(cropping)
            if sync_crop:
                self._sync_crop_from(cropping)
            fps = await asyncio.to_thread(self.link.query_fps)
        except Exception as exc:
            self.log(f'Could not read geometry: {exc}')
            if not quiet:
                raise
            return
        self.settings.width, self.settings.height = width, height
        self.settings.fps = fps
        # Integration time is a nice-to-have readout; never let it stop the
        # geometry and frame rate from being applied.
        try:
            self.settings.tint = await asyncio.to_thread(self.link.query_tint)
        except Exception:
            self.settings.tint = 0.0
        detail = (f', tint {self.settings.tint * 1e6:,.1f} us'
                  if self.settings.tint else '')
        self.log(f'Camera reports {width}x{height} @ {fps:.3f} fps{detail}.')
        self.sync_all()

    # ------------------------------------------------------- trigger mode

    #  The camera keeps its trigger mode across sessions, so an app that left it
    #  in external synchro hands you a camera that opens and starts fine but
    #  never delivers a frame.

    async def report_sync_status(self) -> None:
        """Log the trigger-mode parameters verbatim."""
        if not self.connected:
            return
        for param in SYNC_PARAMS:
            try:
                response = await asyncio.to_thread(self.link.command, param)
                self.log(f'  {param}: {first_line(response)}')
            except Exception as exc:
                self.log(f'  {param}: {exc}')

    async def enable_free_running(self) -> None:
        if not self.connected:
            return
        self.log('Setting free-running mode (disabling external/software sync)...')
        await asyncio.to_thread(set_free_running, self.link, self.log)

    # -------------------------------------------------------- frame window

    async def apply_crop(self) -> None:
        """Send the crop the picker is showing, and restart around it.

        Changing the window reconfigures the stream, so acquisition is stopped
        across it and the geometry re-read afterwards.
        """
        if not self.connected:
            return
        was_acquiring = self.acquiring
        if was_acquiring:
            await asyncio.to_thread(self.stop_acquisition)

        # Columns and rows are two separate commands; stop at the first refusal
        # rather than leaving the window half-applied.
        rejected = False
        for command in crop_commands(self.settings.crop_size,
                                     self.settings.crop_x, self.settings.crop_y):
            self.log(f'> {command}')
            try:
                response = await asyncio.to_thread(self.link.command, command)
            except Exception as exc:
                self.log(f'Error: {exc}')
                rejected = True
                break
            self.log_block(response or '(no response)')
            if command_rejected(response):
                rejected = True
                break
        if rejected:
            self.log('The camera rejected that crop - the window may be '
                     'unchanged or half-applied. Check "cropping" in the console.')

        # Frame rate first, integration time last.  Setting the frame rate
        # makes the camera re-set tint to about the new frame period - at
        # 600.013 fps it came back as 1663.7 us, and 1/600.013 is 1666.6 us -
        # so a tint written before the fps would just be overwritten.
        await self._apply_frame_rate()
        await self._apply_tint(self.settings.target_tint)
        await self.read_geometry(quiet=True, sync_crop=True)

        if was_acquiring:
            try:
                await asyncio.to_thread(self.start_acquisition, False)
            except Exception as exc:
                self.log(f'Could not restart acquisition: {exc}')
        self.sync_all()

    async def restore_tint(self) -> None:
        """Re-apply the target integration time.

        The camera re-sets tint to about the frame period whenever the frame
        rate changes, so this runs after any fps change - including one typed
        into the console.
        """
        await self._apply_tint(self.settings.target_tint)

    async def _apply_tint(self, tint: float) -> None:
        """Set the integration time, the same for every crop."""
        if not tint:
            return
        response = await self._send(f'set tint {tint:.9f}')
        if command_rejected(response):
            self.log(f'The camera refused tint {tint * 1e6:.1f} us - check '
                     '"mintint" for the minimum this configuration allows.')

    async def _send(self, command: str) -> str:
        """Send one command, log it and its reply, and hand the reply back."""
        self.log(f'> {command}')
        try:
            response = await asyncio.to_thread(self.link.command, command)
        except Exception as exc:
            self.log(f'Error: {exc}')
            return ''
        self.log_block(response or '(no response)')
        return response

    async def _apply_frame_rate(self) -> None:
        """Set the frame rate for the current window.

        Taken straight from the table in serial_console.FRAME_RATE. The
        camera's own `maxfps` and `maxfpsusb` both report higher than the
        sensor sustains - asking for those produces frames whose tags do not
        line up - so the readout figures are used directly.
        """
        period_us, rate = frame_rate_for(self.settings.crop_size)
        self.log(f'  {self.settings.crop_size if self.settings.crop_size < FULL_SIZE else "full frame"}'
                 f': {period_us} us frame period')
        await self._send(f'set fps {rate}')

    def _sync_crop_from(self, response: str) -> None:
        """Point the crop picker at the window the camera is actually using."""
        ranges = parse_crop_ranges(response)
        if ranges is None:
            self.settings.crop_size = FULL_SIZE
            self.settings.crop_x = self.settings.crop_y = 0
            return
        (col0, col1), (row0, row1) = ranges
        width, height = col1 - col0 + 1, row1 - row0 + 1
        self.settings.crop_x, self.settings.crop_y = col0, row0
        if width == height and width in WINDOW_SIZES:
            self.settings.crop_size = width
        # A window the picker cannot represent (non-square, or an unusual size)
        # is left showing the previous selection; the readout still reports the
        # real geometry.

    def start_acquisition(self, tag_check: bool = False,
                          viewer: bool = True) -> None:
        """Start acquiring. Tag checking and the preview are both optional."""
        if self.camera is None:
            raise RuntimeError('Not connected.')
        s = self.settings
        self.camera.start(int(s.width), int(s.height), s.fps,
                          tag_check=tag_check, viewer=viewer)
        self.tag_check_active = tag_check

    def stop_acquisition(self) -> None:
        if self.camera is not None and self.camera.running:
            self.camera.stop()

    async def autostart(self, port: str | None = None) -> None:
        """Bring the camera up the way you always want it: connected, free
        running, geometry read, streaming.

        Runs once at startup so the default view is a live image rather than a
        form to fill in.
        """
        from serial_console import DEFAULT_PORT, list_ports

        if port is None:
            devices = [p.device for p in list_ports(include_simulator=False)]
            if self.prefer_simulator:
                port = SIM_PORT
            elif DEFAULT_PORT in devices:
                port = DEFAULT_PORT
            else:
                likely = [p.device for p in list_ports(include_simulator=False)
                          if p.likely]
                port = likely[0] if likely else (devices[0] if devices else None)
        if port is None:
            self.log('No serial port found. Open Settings to pick one.')
            return

        try:
            self.log(await asyncio.to_thread(self.connect, port, DEFAULT_BAUD))
        except Exception as exc:
            self.log(f'Could not connect to {port}: {exc}')
            self.log('Open Settings to choose a different port.')
            return

        try:
            await self.enable_free_running()
            await self.read_geometry(quiet=True, sync_crop=True)
            await asyncio.to_thread(self.start_acquisition)
        except Exception as exc:
            self.log(f'Could not start acquisition: {exc}')
        self.sync_all()

    def shutdown(self) -> None:
        """Stop acquiring on app exit; leave the SDK alone.

        Not a full disconnect: tearing the camera down while the SDK's threads
        may still be in flight is the risky path, and the process is ending
        anyway. The original app did nothing at all here.
        """
        if self.acquiring:
            print('Stopping acquisition ...', flush=True)
        try:
            self.stop_acquisition()
        except Exception:
            pass
        if self.link is not None:
            try:
                self.link.close()
            except Exception:
                pass
            self.link = None
