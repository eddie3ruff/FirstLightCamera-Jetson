"""A fake C-RED camera: synthetic 16-bit frames plus a mock fli-cli serial shell.

This exists so somebody can learn the app, the command set and the raw file
format with no hardware attached.  It is deliberately not a physics model - it
produces plausible-looking 14-bit data with a moving hot blob, fixed-pattern
noise and a few hot pixels, at whatever geometry and frame rate the mock CLI has
been told to use.

The mock CLI and the frame source share one SimState, so `cropping on:0-63:0-63`
in the serial console really does change the size of the frames you record.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import numpy as np

FULL_WIDTH = 640
FULL_HEIGHT = 512
MAX_ADU = 16383  # 14-bit sensor data in a 16-bit container
PROMPT = 'fli-cli>'


@dataclass
class SimState:
    """Everything the mock CLI can change and the frame source has to respect."""
    width: int = FULL_WIDTH
    height: int = FULL_HEIGHT
    col0: int = 0
    col1: int = FULL_WIDTH - 1
    row0: int = 0
    row1: int = FULL_HEIGHT - 1
    cropping: bool = False
    fps: float = 600.0
    tags_enabled: bool = True
    # Defaults to ON so the simulator reproduces the real trap: the camera keeps
    # its trigger mode across sessions, so it opens and starts happily but never
    # delivers a frame until synchronisation is turned off.
    extsynchro: bool = True
    swsynchro: bool = False
    sensor_temp: float = -40.0
    tint: float = 0.000103382
    lock: threading.Lock = field(default_factory=threading.Lock)
    generation: int = 0  # bumped whenever geometry changes

    @property
    def geometry(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def triggered(self) -> bool:
        """True when the camera is waiting on a trigger instead of free running."""
        return self.extsynchro or self.swsynchro

    #  Readout time scales with the number of rows: 105 us for 64 rows, so
    #  each doubling of the window doubles the frame period.  Reading the whole
    #  sensor is its own case rather than a continuation of that line.
    ROW_PERIOD_US = 105 / 64
    FULL_FRAME_PERIOD_US = 1680

    #  Roughly what a USB 3 link carries, for the bandwidth ceiling.
    USB_BYTES_PER_SECOND = 170e6

    @classmethod
    def max_fps_for(cls, width: int, height: int, cropping: bool = True) -> float:
        """Sensor readout rate for a window - what `maxfps` reports."""
        if not cropping or (width, height) == (FULL_WIDTH, FULL_HEIGHT):
            period_us = cls.FULL_FRAME_PERIOD_US
        else:
            period_us = max(1.0, cls.ROW_PERIOD_US * height)
        return 1e6 / period_us

    @classmethod
    def max_fps_usb_for(cls, width: int, height: int) -> float:
        """What the USB link can carry - what `maxfpsusb` reports.

        Unrelated to the sensor's readout rate, and for a small window it reads
        higher: 64x64 frames are tiny, so the link could carry far more of them
        per second than the sensor can produce.
        """
        return cls.USB_BYTES_PER_SECOND / max(1, width * height * 2)

    def _recompute(self) -> None:
        """Geometry follows the window ranges only while cropping is enabled."""
        if self.cropping:
            self.width = self.col1 - self.col0 + 1
            self.height = self.row1 - self.row0 + 1
        else:
            self.width, self.height = FULL_WIDTH, FULL_HEIGHT
        self.fps = self.max_fps_for(self.width, self.height, self.cropping)
        self.generation += 1

    def set_axis(self, which: str, lo: int, hi: int) -> None:
        """Define one axis of the window.

        This does not enable cropping - `set cropping on` does that.  Setting
        the ranges alone leaves the camera on the full frame, which is exactly
        what it looks like when an app forgets the `on`.
        """
        with self.lock:
            if which == 'columns':
                self.col0, self.col1 = lo, hi
            else:
                self.row0, self.row1 = lo, hi
            self._recompute()

    def set_cropping(self, enabled: bool) -> None:
        """Enable or disable the window.  The ranges are kept either way."""
        with self.lock:
            self.cropping = bool(enabled)
            self._recompute()


class SyntheticSource:
    """Generates frames cheaply by scrolling a pre-rendered canvas."""

    def __init__(self, width: int, height: int, tags_enabled: bool = True) -> None:
        self.width = width
        self.height = height
        self.tags_enabled = tags_enabled
        self._counter = 0
        self._out = np.empty((height, width), dtype=np.uint16)
        rng = np.random.default_rng(20260825)

        # Canvas is twice as wide as a frame so we can scroll a window across it.
        cw = width * 2
        yy, xx = np.mgrid[0:height, 0:cw].astype(np.float32)
        base = 900.0 + 1400.0 * (yy / max(height - 1, 1))          # background gradient
        for cx, cy, sigma, amp in (
            (cw * 0.18, height * 0.35, max(width, height) * 0.07, 12000.0),
            (cw * 0.47, height * 0.62, max(width, height) * 0.12, 7000.0),
            (cw * 0.78, height * 0.30, max(width, height) * 0.04, 14500.0),
        ):
            r2 = (xx - cx) ** 2 + (yy - cy) ** 2
            base += amp * np.exp(-r2 / (2.0 * sigma ** 2))
        base += rng.normal(0.0, 60.0, base.shape).astype(np.float32)  # fixed-pattern noise

        # A handful of stuck-hot pixels, the kind you learn to mask out.
        for _ in range(max(4, (width * height) // 40000)):
            base[rng.integers(0, height), rng.integers(0, cw)] = 15800.0

        self._canvas = np.clip(base, 0, MAX_ADU - 400).astype(np.uint16)
        # Separate noise canvas, scrolled at a different rate so it shimmers.
        self._noise = rng.integers(0, 350, size=(height, cw), dtype=np.uint16)

    def next_frame(self) -> bytes:
        """Return one frame as raw little-endian uint16 bytes."""
        w = self.width
        offset = (self._counter * 2) % w
        noff = (self._counter * 7) % w
        np.add(self._canvas[:, offset:offset + w],
               self._noise[:, noff:noff + w], out=self._out)

        if self.tags_enabled:
            # Simulated image tag: a 32-bit frame counter split over the first two
            # pixels, low half first.  The real camera's tag layout is defined by
            # FLI - check the SDK docs before relying on this offline.
            self._out[0, 0] = self._counter & 0xFFFF
            if w > 1:
                self._out[0, 1] = (self._counter >> 16) & 0xFFFF

        self._counter += 1
        return self._out.tobytes()


class MockCli:
    """Answers a useful subset of the fli-cli command set, in the same format."""

    def __init__(self, state: SimState) -> None:
        self.state = state

    def command(self, line: str) -> str:
        cmd = line.strip()
        low = cmd.lower()

        if low == 'help' or low == '?':
            return '\n'.join([
                'fps             Get acquisition frame rate (Hz)',
                'cropping        Get cropping status',
                'tint            Get integration time (s)',
                'temperatures    Get temperatures (degC)',
                'extsynchro      Get usage of external synchronization status',
                'swsynchro       Get usage of software synchronization status',
                'imagetags       Get tag generation status',
                'status          Get camera status',
                'set             Change a parameter',
                '(simulated subset - the real camera lists far more)',
            ])

        if low.startswith('set '):
            return self.command(cmd[4:].strip())

        for param in ('extsynchro', 'swsynchro'):
            if low == param or low.startswith(param + ' '):
                rest = cmd[len(param):].strip().lower()
                label = ('External' if param == 'extsynchro' else 'Software')
                if not rest:
                    on = getattr(self.state, param)
                    return f'{label} synchronization: {"on" if on else "off"}'
                if rest in ('on', '1', 'true', 'enable', 'enabled'):
                    setattr(self.state, param, True)
                elif rest in ('off', '0', 'false', 'disable', 'disabled'):
                    setattr(self.state, param, False)
                else:
                    return f'Error: expected on or off, got "{rest}"'
                state_word = 'on' if getattr(self.state, param) else 'off'
                return f'{label} synchronization: {state_word}'

        if low == 'version':
            return 'Simulated C-RED 2 LITE - mock fli-cli (no hardware attached)'

        if low.startswith('fps'):
            rest = cmd[3:].strip()
            if not rest:
                return f'Frames per second: {self.state.fps:.9f}'
            if rest.lower() == 'raw':
                return f'{self.state.fps:.6f}'
            try:
                value = float(rest)
            except ValueError:
                return f'Error: cannot parse frame rate "{rest}"'
            if not 1.0 <= value <= 20000.0:
                return f'Error: frame rate {value} out of range (1 - 20000)'
            ceiling = self.state.max_fps_for(self.state.width, self.state.height,
                                             self.state.cropping)
            if value > ceiling:
                return (f'Error: {value:.3f} fps exceeds the maximum for this '
                        f'configuration ({ceiling:.3f})')
            with self.state.lock:
                self.state.fps = value
                # The camera re-sets the integration time to about the new
                # frame period whenever the frame rate changes, which is why
                # tint has to be written after fps, not before.
                self.state.tint = 0.998 / value
            return f'Frames per second: {value:.9f}'

        if low.startswith('cropping'):
            rest = cmd[8:].strip()
            st = self.state
            if not rest:
                if st.cropping:
                    return (f'Cropping on : columns: {st.col0}-{st.col1} '
                            f'rows: {st.row0}-{st.row1}')
                return (f'Cropping off: columns: 0-{FULL_WIDTH - 1} '
                        f'rows: 0-{FULL_HEIGHT - 1}')
            low_rest = rest.lower()
            if low_rest == 'raw':
                if st.cropping:
                    return f'on:{st.col0}-{st.col1}:{st.row0}-{st.row1}'
                return f'off:0-{FULL_WIDTH - 1}:0-{FULL_HEIGHT - 1}'
            if low_rest in ('off', 'on'):
                #  The ranges only take effect once cropping is switched on.
                #  Setting columns and rows without this leaves the camera on
                #  the full frame.
                st.set_cropping(low_rest == 'on')
                if st.cropping:
                    return (f'Cropping on : columns: {st.col0}-{st.col1} '
                            f'rows: {st.row0}-{st.row1}')
                return 'Result:OK'

            #  Columns and rows arrive as separate commands, matching the
            #  firmware: `set cropping columns 288-351`, then rows, then on.
            axis = re.match(r'(columns|rows)\s+(\d+)\s*-\s*(\d+)\s*$', rest, re.I)
            if axis:
                which, lo, hi = axis.group(1).lower(), int(axis.group(2)), int(axis.group(3))
                limit = FULL_WIDTH if which == 'columns' else FULL_HEIGHT
                step = 32 if which == 'columns' else 4
                if not (0 <= lo <= hi < limit):
                    return f'Error: {which} outside sensor (0-{limit - 1})'
                if lo % step:
                    return f'Error: {which} must start on a multiple of {step}'
                st.set_axis(which, lo, hi)
                return 'Result:OK'

            return 'Syntax error next to ' + repr(rest)

        if low == 'tint' or low.startswith('tint '):
            rest = cmd[4:].strip()
            if not rest:
                return ('integration time for current configuration: '
                        f'{self.state.tint:.9f}')
            try:
                value = float(rest)
            except ValueError:
                return f'Error: cannot parse integration time "{rest}"'
            period = 1.0 / self.state.fps if self.state.fps else None
            if period and value > period:
                return (f'Error: integration time {value * 1e6:.1f} us exceeds '
                        f'the frame period ({period * 1e6:.1f} us)')
            with self.state.lock:
                self.state.tint = value
            return 'Result:OK'

        if low.startswith('maxfpsusb'):
            ceiling = self.state.max_fps_usb_for(self.state.width, self.state.height)
            return ('Maximum possible frames per second for current '
                    f'configuration (USB): {ceiling:.9f}')

        if low.startswith('maxfps'):
            ceiling = self.state.max_fps_for(self.state.width, self.state.height,
                                             self.state.cropping)
            return ('Maximum possible frames per second for current '
                    f'configuration (Camera Link): {ceiling:.9f}')

        if low in ('temperatures', 'temp'):
            s = self.state
            return (f'Sensor: {s.sensor_temp:.2f}C  Mother board: 31.10C  '
                    'Front end: 28.40C  Power board: 34.70C')

        if low.startswith('imagetags'):
            rest = cmd[9:].strip().lower()
            if rest in ('on', '1', 'true'):
                self.state.tags_enabled = True
            elif rest in ('off', '0', 'false'):
                self.state.tags_enabled = False
            return f'Image tags: {"on" if self.state.tags_enabled else "off"}'

        if low == 'status':
            s = self.state
            return (f'SIMULATED camera | {s.width}x{s.height} @ {s.fps:.3f} fps | '
                    f'cropping {"on" if s.cropping else "off"} | '
                    f'tags {"on" if s.tags_enabled else "off"} | '
                    f'extsynchro {"on" if s.extsynchro else "off"} | '
                    f'swsynchro {"on" if s.swsynchro else "off"}')

        return f'Error: unknown command "{cmd}" (try: help)'


class SimulatedAcquisition:
    """Runs a SyntheticSource on a background thread at the requested rate."""

    #  A real C-RED runs to ~9500 fps cropped; generating that fast in numpy
    #  would burn a core for no visible benefit, so the simulator caps here.
    MAX_GENERATED_FPS = 1000.0

    def __init__(self, state: SimState) -> None:
        self.state = state
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.source: SyntheticSource | None = None
        self.requested_fps = 0.0
        self.generated_fps = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, width: int, height: int, fps: float, on_frame) -> None:
        self.stop()
        self.source = SyntheticSource(width, height, self.state.tags_enabled)
        self.requested_fps = max(fps, 0.1)
        self.generated_fps = min(self.requested_fps, self.MAX_GENERATED_FPS)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(on_frame,), name='sim-acquisition', daemon=True)
        self._thread.start()

    def _loop(self, on_frame) -> None:
        period = 1.0 / self.generated_fps
        next_due = time.perf_counter()
        while not self._stop.is_set():
            if self.state.triggered:
                # Waiting for a trigger edge that is never going to arrive.
                self._stop.wait(0.05)
                continue
            on_frame(self.source.next_frame())
            next_due += period
            delay = next_due - time.perf_counter()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_due = time.perf_counter()  # we fell behind; don't spiral

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
