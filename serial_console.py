"""Serial link to the camera's fli-cli shell, plus the console UI.

Reads are bounded by a deadline and run on a worker thread, so a camera that
never answers cannot hang the web app. /dev/ttyACM0 is the default port but the
choice is offered, because a second USB serial device shifts the camera to
ttyACM1.
"""
from __future__ import annotations

import asyncio
import math
import re
import threading
import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports as _list_ports
from nicegui import ui

from simulator import PROMPT, MockCli, SimState

DEFAULT_PORT = '/dev/ttyACM0'
DEFAULT_BAUD = 115200
SIM_PORT = 'simulator'
FULL_FRAME = (640, 512)

# Hints used only to sort the likely camera to the top of the dropdown.
_LIKELY = ('ttyacm', 'fli', 'first light', 'c-red', 'cred')


@dataclass
class PortInfo:
    device: str
    description: str
    likely: bool = False

    @property
    def label(self) -> str:
        return f'{"* " if self.likely else ""}{self.device} - {self.description}'


def list_ports(include_simulator: bool = True) -> list[PortInfo]:
    found: list[PortInfo] = []
    for port in _list_ports.comports():
        haystack = f'{port.device} {port.description} {port.manufacturer or ""}'.lower()
        found.append(PortInfo(port.device, port.description or 'serial port',
                              any(h in haystack for h in _LIKELY)))
    found.sort(key=lambda p: (not p.likely, p.device))
    if include_simulator:
        found.append(PortInfo(SIM_PORT, 'simulated camera (no hardware)'))
    return found


def _clean(raw: str, sent: str, prompt: str) -> str:
    """Strip the command echo and the prompt, keeping the line structure.

    Line breaks are preserved deliberately.  Joining everything with spaces
    turned multi-line replies - `help` above all, which is a ~95 row table -
    into one unreadable paragraph.
    """
    lines: list[str] = []
    for line in raw.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = line.replace(prompt, '').rstrip()
        if not line.strip() or line.strip() == sent.strip():
            continue
        lines.append(line)
    return '\n'.join(lines).strip()


def first_line(response: str) -> str:
    """The single-line view of a response, for parsing and one-line logging."""
    return response.split('\n', 1)[0].strip() if response else ''



class SerialLink:
    """Blocking, thread-safe request/response.  Call it off the event loop."""

    def __init__(self, prompt: str = PROMPT) -> None:
        self.ser: serial.Serial | None = None
        self.prompt = prompt
        self.port = ''
        self.baudrate = DEFAULT_BAUD
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    @property
    def is_simulated(self) -> bool:
        return False

    def connect(self, port: str = DEFAULT_PORT, baudrate: int = DEFAULT_BAUD,
                timeout: float = 1.0) -> str:
        self.close()
        self.ser = serial.Serial(
            port=port, baudrate=baudrate,
            bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE, timeout=timeout, write_timeout=timeout,
            xonxoff=False, rtscts=False, dsrdtr=False)
        self.port, self.baudrate = port, baudrate
        time.sleep(0.1)
        self.ser.reset_input_buffer()
        return f'Connected to {port} at {baudrate} baud.'

    def close(self) -> None:
        if self.ser is not None:
            try:
                if self.ser.is_open:
                    self.ser.close()
            except Exception:
                pass
            self.ser = None

    def _drain(self, quiet: float = 0.05, limit: float = 0.5) -> None:
        """Wait for the line to go quiet, consuming anything still in flight.

        reset_input_buffer() alone is not enough: if the previous reply is
        still arriving it throws away the front of it, the tail turns up during
        the next read, and every answer from then on is attributed to the
        command before it.  That is why `extsynchro` once came back empty and
        `swsynchro` answered "External synchronization: off".
        """
        deadline = time.monotonic() + limit
        last = time.monotonic()
        while time.monotonic() < deadline:
            waiting = self.ser.in_waiting
            if waiting:
                self.ser.read(waiting)
                last = time.monotonic()
            elif time.monotonic() - last >= quiet:
                return
            else:
                time.sleep(0.005)

    def command(self, cmd: str, timeout: float = 2.0) -> str:
        if not self.connected:
            raise RuntimeError('serial port is not open')
        with self._lock:
            self._drain()
            self.ser.write((cmd + '\r\n').encode())
            self.ser.flush()

            deadline = time.monotonic() + timeout
            chunks: list[bytes] = []
            while time.monotonic() < deadline:
                waiting = self.ser.in_waiting
                data = self.ser.read(waiting if waiting else 1)
                if data:
                    chunks.append(data)
                    if self.prompt.encode() in b''.join(chunks[-4:]):
                        break
                else:
                    time.sleep(0.005)
            raw = b''.join(chunks).decode('utf-8', errors='replace')

        response = _clean(raw, cmd, self.prompt)
        if not response and not raw:
            raise TimeoutError(f'no response to "{cmd}" within {timeout:.1f}s')
        return response

    def query_geometry(self, timeout: float = 2.0) -> tuple[int, int]:
        return parse_cropping(self.command('cropping raw', timeout))

    def query_fps(self, timeout: float = 2.0) -> float:
        return parse_fps(self.command('fps raw', timeout))

    def query_tint(self, timeout: float = 2.0) -> float:
        """Integration time in seconds."""
        return parse_tint(self.command('tint', timeout))


class SimulatedSerialLink(SerialLink):
    """Same interface, answered by the mock fli-cli."""

    def __init__(self, state: SimState, prompt: str = PROMPT) -> None:
        super().__init__(prompt)
        self.state = state
        self.cli = MockCli(state)
        self._open = False

    @property
    def connected(self) -> bool:
        return self._open

    @property
    def is_simulated(self) -> bool:
        return True

    def connect(self, port: str = SIM_PORT, baudrate: int = DEFAULT_BAUD,
                timeout: float = 1.0) -> str:
        self._open = True
        self.port, self.baudrate = SIM_PORT, baudrate
        return 'Connected to the simulated camera (no hardware attached).'

    def close(self) -> None:
        self._open = False

    def command(self, cmd: str, timeout: float = 2.0) -> str:
        if not self._open:
            raise RuntimeError('simulated link is not open')
        time.sleep(0.01)
        return self.cli.command(cmd)


def open_link(port: str, baudrate: int = DEFAULT_BAUD, *,
              state: SimState | None = None) -> SerialLink:
    if port == SIM_PORT:
        link = SimulatedSerialLink(state or SimState())
        link.connect(port, baudrate)
        return link
    link = SerialLink()
    link.connect(port, baudrate)
    return link


# ----------------------------------------------------------------- parsing

_RAW_CROP = re.compile(r'\b(on|off)\s*:\s*(\d+)\s*-\s*(\d+)\s*:\s*(\d+)\s*-\s*(\d+)', re.I)
_VERBOSE_CROP = re.compile(
    r'columns\s*:\s*(\d+)\s*-\s*(\d+).*?rows\s*:\s*(\d+)\s*-\s*(\d+)', re.I | re.S)


SYNC_PARAMS = ('extsynchro', 'swsynchro')

_REJECTED = re.compile(r'syntax error|\berror\b|invalid|unsupported', re.I)


def command_rejected(response: str) -> bool:
    """True when the camera refused a command.

    fli-cli has no exit codes over serial - a bad parameter comes back as
    ordinary text like "Syntax error next to '...'" - so this is how we tell a
    refusal from a result.
    """
    return bool(_REJECTED.search(response or ''))


#  Sensor windowing granularity: columns snap to 32, rows to 4.
COLUMN_STEP = 32
ROW_STEP = 4
#  Square window sizes, plus FULL_SIZE meaning the whole 640x512 sensor.
WINDOW_SIZES = (64, 128, 256, 512)
FULL_SIZE = 640


def align_and_clamp(value: int, step: int, span: int, size: int) -> int:
    """Snap an origin to the granularity and keep the window on the sensor."""
    max_start = max(0, ((span - size) // step) * step)
    return max(0, min(int(round(value / step)) * step, max_start))


def window_shape(size: int) -> tuple[int, int]:
    """(width, height) for a crop size; FULL_SIZE means the whole sensor."""
    return FULL_FRAME if int(size) >= FULL_SIZE else (int(size), int(size))


def crop_commands(size: int, col0: int, row0: int) -> list[str]:
    """The commands that set the frame window.

    Three commands, in this order: columns, rows, then `set cropping on`.
    Setting the ranges only defines the window - without the `on` the camera
    stays on the full frame, which looks exactly like the crop being ignored.

    A combined `set cropping columns A-B rows C-D`, and the `on:A-B:C-D` form
    the getter reports, are both rejected with a syntax error.
    """
    if int(size) >= FULL_SIZE:
        return ['set cropping off']
    width, height = window_shape(size)
    return [f'set cropping columns {col0}-{col0 + width - 1}',
            f'set cropping rows {row0}-{row0 + height - 1}',
            'set cropping on']


#  Frame period and rate for each window, from the sensor's readout time.
#  Readout scales with the number of rows, so each doubling of the window
#  doubles the period. These are used directly - the camera's own maxfps and
#  maxfpsusb both report higher than the sensor sustains.
FRAME_RATE = {
    64: (105, 9523),
    128: (210, 4761),
    256: (420, 2380),
    512: (840, 1190),
    FULL_SIZE: (1680, 595),
}


def frame_rate_for(size: int) -> tuple[int, int]:
    """(frame period in us, frame rate in fps) for a crop size."""
    size = int(size)
    if size in FRAME_RATE:
        return FRAME_RATE[size]
    #  Anything the picker does not offer: same rule, period proportional to
    #  rows, rate floored so it stays at or under it.
    period_us = max(1, round(105 * size / 64))
    return period_us, max(1, math.floor(1e6 / period_us))


def set_free_running(link, log=print) -> None:
    """Turn off external and software sync so the camera streams on its own.

    A camera left in external synchro by another application will open, start
    acquisition and then deliver nothing - it is waiting for a trigger edge.
    """
    for param in SYNC_PARAMS:
        command = f'set {param} off'
        try:
            log(f'  {command} -> {first_line(link.command(command))}')
        except Exception as exc:
            log(f'  {command} -> {exc}')


def parse_cropping(response: str, full: tuple[int, int] = FULL_FRAME) -> tuple[int, int]:
    """Handles both `on:0-63:0-63` and `Cropping off: columns: 0-639 rows: 0-511`."""
    match = _RAW_CROP.search(response)
    if match:
        state, c0, c1, r0, r1 = match.groups()
        if state.lower() == 'off':
            return full
        return int(c1) - int(c0) + 1, int(r1) - int(r0) + 1

    match = _VERBOSE_CROP.search(response)
    if match:
        c0, c1, r0, r1 = (int(g) for g in match.groups())
        if re.search(r'cropping\s+off', response, re.I):
            return full
        return c1 - c0 + 1, r1 - r0 + 1

    raise ValueError(f'cannot parse cropping response: {response!r}')


#  Parameters that change the shape or timing of the frame stream.  Setting one
#  of these while acquisition is running is what produces an endless
#  "Invalid tag detected" storm: the camera reconfigures underneath a stream the
#  SDK is still tag-checking against the old configuration.
ACQUISITION_PARAMS = frozenset({
    'fps', 'cropping', 'tint', 'sensibility', 'sensitivity', 'hdr', 'imagetags',
    'unsigned', 'flip', 'slowmode', 'extsynchro', 'swsynchro', 'rawimages',
    'badpixel', 'bias', 'flat', 'filtering', 'thresholding', 'agc', 'aduoffset',
    'sensor', 'snake', 'tlsydel', 'nbframesperswtrig', 'syncdelay',
    'darkoptimlevel', 'excludeBorders', 'imagepattern',
})


def reconfigures_stream(command: str) -> str | None:
    """Return the parameter name if this command would reconfigure the stream.

    Only setters count - a bare `fps` just reads the value and is harmless.
    """
    tokens = (command or '').strip().split()
    if not tokens:
        return None
    if tokens[0].lower() == 'set':
        tokens = tokens[1:]
    if len(tokens) < 2:
        return None  # a getter, no value supplied
    name = tokens[0]
    for param in ACQUISITION_PARAMS:
        if name.lower() == param.lower():
            return param
    return None


def _first_number(response: str) -> float:
    match = re.search(r'([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)', response or '')
    if not match:
        raise ValueError(f'no number in response: {response!r}')
    return float(match.group(1))


def parse_crop_ranges(response: str) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Full crop ranges from a `cropping` reply, or None when cropping is off.

    parse_cropping() gives only the size; this keeps the origin too, so the
    crop picker can show where the window actually is.
    """
    if re.search(r'\b(cropping\s+off|^off:)', response or '', re.I | re.M):
        return None
    match = _RAW_CROP.search(response or '')
    if match:
        state, c0, c1, r0, r1 = match.groups()
        if state.lower() == 'off':
            return None
        return (int(c0), int(c1)), (int(r0), int(r1))
    match = _VERBOSE_CROP.search(response or '')
    if match:
        c0, c1, r0, r1 = (int(g) for g in match.groups())
        return (c0, c1), (r0, r1)
    return None


def parse_fps(response: str) -> float:
    return _first_number(response)


def parse_tint(response: str) -> float:
    """`integration time for current configuration: 0.000103382` -> seconds."""
    return _first_number(response)


# --------------------------------------------------------------- UI card

class SerialConsole:
    """Command entry and the log.  Connection lives in the settings panel."""

    def __init__(self, session) -> None:
        self.session = session
        self._log_seq = 0
        self._history: list[str] = []
        self._history_pos = 0
        self.build()

    def build(self) -> None:
        self.command_input = ui.input('Command').classes('w-full') \
            .props('clearable dense outlined')
        self.log_view = ui.log(max_lines=2000).classes('w-full h-64 text-xs font-mono')

        self.command_input.on('keydown.enter', lambda: self.run())
        self.command_input.on('keydown.up', lambda: self.recall(-1))
        self.command_input.on('keydown.down', lambda: self.recall(1))
        ui.timer(0.25, self.drain_log)

    async def run(self, text: str | None = None) -> None:
        session = self.session
        command = (text if text is not None else self.command_input.value or '').strip()
        if not command:
            return
        if not session.connected:
            session.log('Not connected.')
            return
        if text is None:
            self.command_input.value = ''
            self._history.append(command)
            self._history_pos = len(self._history)

        param = reconfigures_stream(command)
        if param and session.acquiring:
            session.log(f'Note: "{param}" changes the frame stream. Stop '
                        'acquisition, change it, then start again.')

        session.log(f'> {command}')
        try:
            response = await asyncio.to_thread(session.link.command, command)
            session.log_block(response or '(no response)')
        except Exception as exc:
            session.log(f'Error: {exc}')
            return

        if param == 'fps':
            # The camera re-sets tint to about the frame period whenever the
            # rate changes, so put the target integration time back.
            await session.restore_tint()

    def recall(self, step: int) -> None:
        if not self._history:
            return
        self._history_pos = max(0, min(len(self._history), self._history_pos + step))
        self.command_input.value = (self._history[self._history_pos]
                                    if self._history_pos < len(self._history) else '')

    def drain_log(self) -> None:
        camera = self.session.camera
        if camera is not None:
            for line in camera.errors.drain():
                self.session.log(line)
        lines, self._log_seq = self.session.log_since(self._log_seq)
        for line in lines:
            self.log_view.push(line)
