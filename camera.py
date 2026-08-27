"""Camera acquisition: frame plumbing, the SDK camera, and the simulator.

One acquisition feeds both the viewer and the recorder, so you can watch what
you are capturing.

The SDK's void* user context carries an integer token rather than a Python
object pointer: ctypes does not keep such a pointer alive, and the acquisition
thread would eventually call into a collected object.
"""
from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Callable

import camera_sdk as sdk

BYTES_PER_PIXEL = 2  # 16-bit container, camera fills the low 14 bits
Logger = Callable[[str], None]


class CameraError(RuntimeError):
    """Anything that goes wrong opening, starting or stopping the camera."""


# ---------------------------------------------------------------- recording

@dataclass
class RecordJob:
    path: Path
    width: int
    height: int
    frames: int
    fps: float
    buffer: bytearray
    view: ctypes.Array | None = None
    count: int = 0
    #  Plain bool, checked on every frame in the acquisition callback;
    #  Event.is_set() would be a needless call in the hot path.
    complete: bool = False
    #  perf_counter, to match first_frame - the two are subtracted.
    started: float = field(default_factory=time.perf_counter)
    finished: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * BYTES_PER_PIXEL

    @property
    def progress(self) -> float:
        return 0.0 if self.frames <= 0 else min(1.0, self.count / self.frames)


# ------------------------------------------------------------- frame buffer

class FrameHub:
    """Latest-frame ring buffer with an optional recording tap.

    The producer side runs on the SDK's acquisition thread, so the hot path is
    one memmove and an index bump - nothing that can block or allocate.
    """

    def __init__(self, ring_size: int = 8) -> None:
        self.ring_size = max(2, ring_size)
        self._lock = threading.Lock()
        self._ring: list[bytearray] = []
        self._views: list[ctypes.Array] = []
        self._write_index = 0
        self.width = 0
        self.height = 0
        self._frame_bytes = 0
        self.frame_count = 0
        self.viewer_frames = 0
        self._viewer_stride = 1
        #  Off during a recording: the preview has no business copying and
        #  encoding frames while the point of the exercise is not dropping any.
        self.viewer_enabled = True
        self.overruns = 0
        self.callback_error: str | None = None
        self._record: RecordJob | None = None

    @property
    def frame_bytes(self) -> int:
        return self.width * self.height * BYTES_PER_PIXEL

    def configure(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError(f'invalid frame geometry {width}x{height}')
        with self._lock:
            if width == self.width and height == self.height and self._ring:
                return
            self.width, self.height = width, height
            nbytes = width * height * BYTES_PER_PIXEL
            self._frame_bytes = nbytes
            self._ring = [bytearray(nbytes) for _ in range(self.ring_size)]
            self._views = [(ctypes.c_char * nbytes).from_buffer(b) for b in self._ring]
            self._write_index = 0
            self.frame_count = 0
            self.viewer_frames = 0

    def reset_counters(self) -> None:
        with self._lock:
            self.frame_count = 0
            self.viewer_frames = 0
            self.overruns = 0
            self.callback_error = None

    def submit_pointer(self, ptr) -> None:
        """Copy a frame out of SDK memory.  Runs on the acquisition thread.

        This is the hot path: at 9400 fps it runs every 106 us, holding the GIL
        the SDK needs to hand over the next frame.  If it is slow, the SDK's
        producer laps its own buffer while the consumer is still reading it,
        the consumer gets a torn frame, and that is reported as "Invalid tag
        detected" - a corruption message, not a dropped-frame count.

        So the recording gets every frame, because that is the point of a
        recording, but the viewer does not.  The preview displays a dozen
        frames a second; copying 9400 of them is 784x more memcpy than it can
        possibly use.  viewer_stride samples it down to ~30 fps worth.
        """
        views = self._views
        if not views:
            return
        nbytes = self._frame_bytes
        count = self.frame_count + 1
        self.frame_count = count

        job = self._record
        if job is not None and not job.complete:
            if job.frame_bytes != nbytes:
                job.cancelled = True
                job.complete = True
                job.finished.set()
            else:
                ctypes.memmove(ctypes.byref(job.view, job.count * nbytes),
                               ptr, nbytes)
                now = time.perf_counter()
                if not job.count:
                    job.first_frame = now
                job.last_frame = now
                job.count += 1
                if job.count >= job.frames:
                    job.complete = True
                    job.finished.set()

        if self.viewer_enabled and count % self._viewer_stride == 0:
            slot = self._write_index
            ctypes.memmove(views[slot], ptr, nbytes)
            self._write_index = (slot + 1) % self.ring_size
            self.viewer_frames += 1

    def submit_bytes(self, data) -> None:
        """Simulator path: same shape as submit_pointer."""
        if not self._views:
            return
        nbytes = self._frame_bytes
        if len(data) != nbytes:
            self.overruns += 1
            return
        count = self.frame_count + 1
        self.frame_count = count

        job = self._record
        if job is not None and not job.complete:
            if job.frame_bytes != nbytes:
                job.cancelled = True
                job.complete = True
                job.finished.set()
            else:
                offset = job.count * nbytes
                job.buffer[offset:offset + nbytes] = data
                now = time.perf_counter()
                if not job.count:
                    job.first_frame = now
                job.last_frame = now
                job.count += 1
                if job.count >= job.frames:
                    job.complete = True
                    job.finished.set()

        if self.viewer_enabled and count % self._viewer_stride == 0:
            slot = self._write_index
            self._ring[slot][:] = data
            self._write_index = (slot + 1) % self.ring_size
            self.viewer_frames += 1

    #  The preview shows a dozen frames a second; sampling the stream down to
    #  roughly this rate keeps the acquisition callback cheap at 9400 fps.
    VIEWER_TARGET_FPS = 30.0

    def set_viewer_stride(self, fps: float) -> int:
        """Sample 1 frame in N for the viewer, based on the acquisition rate."""
        stride = max(1, int(round((fps or 0.0) / self.VIEWER_TARGET_FPS)))
        self._viewer_stride = stride
        return stride

    @property
    def viewer_stride(self) -> int:
        return self._viewer_stride

    def latest(self) -> tuple[bytes, int] | None:
        """Newest viewer frame, copied. Read without a lock."""
        if not self._ring or self.viewer_frames == 0:
            return None
        count = self.viewer_frames
        slot = (self._write_index - 1) % self.ring_size
        return bytes(self._ring[slot]), count

    def arm_recording(self, path, frames: int, fps: float) -> RecordJob:
        if self.width <= 0 or self.height <= 0:
            raise RuntimeError('configure the frame hub before recording')
        if frames <= 0:
            raise ValueError('frame count must be positive')
        nbytes = self.frame_bytes * frames
        job = RecordJob(path=Path(path), width=self.width, height=self.height,
                        frames=frames, fps=fps, buffer=bytearray(nbytes))
        # One long-lived view; building it per frame would allocate in the hot path.
        job.view = (ctypes.c_char * nbytes).from_buffer(job.buffer)
        #  Touch every page now. A fresh buffer faults on first write, and
        #  that cost lands in the acquisition callback - about 86 us on the
        #  first pass over each frame's worth of pages, while the callback is
        #  holding the GIL the SDK's producer thread needs.
        ctypes.memset(job.view, 0, nbytes)
        with self._lock:
            self._record = job
        return job

    def disarm_recording(self) -> None:
        with self._lock:
            job, self._record = self._record, None
        if job is not None and not job.complete:
            job.cancelled = True
            job.complete = True
            job.finished.set()

    @property
    def recording(self) -> RecordJob | None:
        return self._record


# ------------------------------------------------------------ SDK callbacks

_REGISTRY: dict[int, 'Camera'] = {}
_TOKENS = count(1)
_REGISTRY_LOCK = threading.Lock()
_KEEPALIVE: list = []   # trampolines must outlive the acquisition

#  When false, the SDK's INFO-level chatter is suppressed so the terminal reads
#  as a log of what was acquired. Errors and warnings always print.
VERBOSE = False


def set_verbose(value: bool) -> None:
    global VERBOSE
    VERBOSE = bool(value)


@sdk.FRAME_CB
def _frame_trampoline(userctx, frame, status):  # noqa: ANN001 - ctypes callback
    cam = _REGISTRY.get(userctx or 0)
    if cam is None:
        return
    try:
        cam.hub.submit_pointer(frame)
    except Exception as exc:
        # An exception must never unwind into C, but swallowing it silently
        # means a broken callback drops every frame and looks like dead
        # hardware. Keep the first one so the UI can say what happened.
        cam.hub.overruns += 1
        if cam.hub.callback_error is None:
            cam.hub.callback_error = repr(exc)


class ErrorSink:
    """Counts SDK diagnostics; the main thread formats and prints them.

    The callback runs on the SDK's own thread. Printing from there is a
    blocking write - over SSH, to a socket - while holding the GIL that the
    producer thread needs to hand over the next frame. A stream that has
    started dropping frames then reports more errors, which block for longer,
    which drops more frames. Counting is a dict bump and cannot spiral.
    """

    def __init__(self) -> None:
        self._counts: dict[tuple[int, bytes], int] = {}
        self._lock = threading.Lock()
        self.errors = 0

    def record(self, level: int, diag: bytes) -> None:
        """Called from the SDK thread. Keep this cheap."""
        key = (level, diag or b'')
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            if level & sdk.FLI_USB_ERROR_LEVEL_ERROR:
                self.errors += 1

    def drain(self) -> list[str]:
        """Collect and clear, one line per distinct message."""
        with self._lock:
            items = list(self._counts.items())
            self._counts.clear()
        lines = []
        for (level, diag), count in items:
            message = diag.decode(errors='replace') if diag else ''
            suffix = f' (x{count})' if count > 1 else ''
            lines.append(f'{sdk.error_level_name(level)}: {message}{suffix}')
        return lines

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self.errors = 0


@sdk.ERROR_CB
def _error_trampoline(userctx, error, diag):  # noqa: ANN001 - ctypes callback
    cam = _REGISTRY.get(userctx or 0)
    if cam is None:
        return
    try:
        cam.errors.record(error, diag)
    except Exception:  # never let an exception unwind into C
        pass


_KEEPALIVE += [_frame_trampoline, _error_trampoline]


# ----------------------------------------------------------------- cameras

class Camera:
    """The real camera, through the simplified USB SDK."""

    kind = 'sdk'

    #  How long to let the SDK settle after stopping before starting again.
    SETTLE_SECONDS = 0.25

    def __init__(self, hub: FrameHub, logger: Logger | None = None,
                 library: str | None = None, index: int = 0) -> None:
        self.hub = hub
        self._logger = logger or print
        self.library = library
        self.index = index
        self.lib: ctypes.CDLL | None = None
        self.ctx: ctypes.c_void_p | None = None
        self.tty: str | None = None
        self._token = next(_TOKENS)
        self._sdk_started = False
        self._running = False
        self.errors = ErrorSink()

    def log(self, message: str) -> None:
        self._logger(message)

    @property
    def running(self) -> bool:
        return self._running

    def open(self) -> str:
        if self.ctx is not None:
            return f'Camera already open (tty {self.tty}).'

        self.lib = sdk.load(self.library)
        self.log(f'Loaded SDK: {sdk.library_path(self.library)}')

        if not self._sdk_started:
            if self.lib.fli_usb_init() != 1:
                raise CameraError('fli_usb_init() failed.')
            self._sdk_started = True

        with _REGISTRY_LOCK:
            _REGISTRY[self._token] = self

        detected = self.lib.fli_usb_detect()
        if detected <= 0:
            self._forget()
            raise CameraError('No cameras detected. Check USB, power, and '
                              'whether you need to run with sudo.')
        self.log(f'{detected} camera(s) detected.')

        handle = self.lib.fli_usb_open(self.index, _error_trampoline,
                                       ctypes.c_void_p(self._token))
        if not handle:
            self._forget()
            raise CameraError(f'fli_usb_open({self.index}) failed.')
        self.ctx = ctypes.c_void_p(handle)

        tty = self.lib.fli_usb_get_associated_tty(self.ctx)
        self.tty = tty.decode(errors='replace') if tty else None
        return f'Camera opened (ctx {hex(handle)}), serial device {self.tty or "unknown"}'

    def start(self, width: int, height: int, fps: float = 0.0,
              tag_check: bool = False, viewer: bool = True) -> None:
        """Start acquiring.

        `tag_check` turns on the SDK's frame-counter check - off for plain
        viewing, where the messages are noise, on for recording where a dropped
        frame matters. `viewer` feeds the preview; a recording turns it off so
        nothing competes with the capture.
        """
        if self.ctx is None:
            raise CameraError('Camera is not open.')
        if self._running:
            return
        self.hub.configure(width, height)
        self.hub.reset_counters()
        self.hub.viewer_enabled = viewer
        self.errors.reset()
        stride = self.hub.set_viewer_stride(fps)
        if viewer and stride > 1:
            self.log(f'Viewer sampling 1 frame in {stride}.')

        if self.lib.fli_usb_checkTagEnable(self.ctx, 1 if tag_check else 0) != 1:
            self.log(f'Warning: could not turn tag checking '
                     f'{"on" if tag_check else "off"}.')
        if self.lib.fli_usb_startAcquisition(self.ctx, width, height,
                                             _frame_trampoline,
                                             ctypes.c_void_p(self._token)) != 1:
            raise CameraError(
                f'fli_usb_startAcquisition({width}x{height}) failed. '
                'Does the geometry match the camera cropping?')
        self._running = True
        self.log(f'Acquisition started at {width}x{height}'
                 + (' with tag checking.' if tag_check else '.'))

    def stop(self) -> None:
        if not self._running or self.ctx is None:
            self._running = False
            return
        ok = self.lib.fli_usb_stopAcquisition(self.ctx) == 1
        self._running = False
        #  stopAcquisition returns before the SDK's producer and consumer
        #  threads have finished winding down - their "stopping" messages
        #  arrive after it. Starting again immediately leaves transfers from
        #  the old run still in flight, which shows up as "Unable to submit"
        #  and corrupt tags on the new one.
        time.sleep(self.SETTLE_SECONDS)
        self.log('Acquisition stopped.' if ok else 'fli_usb_stopAcquisition() failed.')

    def close(self) -> None:
        """Stop acquiring and release the camera handle.

        Deliberately does NOT call fli_usb_exit().  That tears down libusb's
        context, and if any SDK thread is still alive the next libusb call
        lands on a destroyed mutex - which aborts the process with

            usbi_mutex_lock: Assertion `pthread_mutex_lock(mutex) == 0' failed

        The original app never called fli_usb_close() or fli_usb_exit() at all:
        it opened the camera once and left the SDK up for the life of the
        process.  Leaking it that way is what a long-running acquisition app
        wants anyway, so we follow suit and only release the handle.
        """
        self.stop()
        if self.ctx is not None and self.lib is not None:
            self.lib.fli_usb_close(self.ctx)
            self.ctx = None
            self.log('Camera closed.')
        with _REGISTRY_LOCK:
            _REGISTRY.pop(self._token, None)


class SimulatedCamera:
    """Synthetic frames, for working on the UI without the camera attached.

    Not needed on the Jetson once the camera is connected - delete simulator.py
    and this class if you want the app stripped to hardware only.
    """

    kind = 'simulator'

    def __init__(self, hub: FrameHub, state, logger: Logger | None = None) -> None:
        from simulator import SimulatedAcquisition
        self.hub = hub
        self._logger = logger or print
        self.state = state
        self.acq = SimulatedAcquisition(state)
        self.tty = None
        self._running = False
        self.errors = ErrorSink()

    def log(self, message: str) -> None:
        self._logger(message)

    @property
    def running(self) -> bool:
        return self._running

    def open(self) -> str:
        return 'Simulated camera ready (no hardware attached).'

    def start(self, width: int, height: int, fps: float = 0.0,
              tag_check: bool = False, viewer: bool = True) -> None:
        if self._running:
            return
        self.hub.configure(width, height)
        self.hub.reset_counters()
        self.hub.viewer_enabled = viewer
        rate = fps or self.state.fps
        self.hub.set_viewer_stride(rate)
        self.acq.start(width, height, rate, self.hub.submit_bytes)
        self._running = True
        note = ''
        if self.acq.generated_fps < self.acq.requested_fps:
            note = (f' (generating {self.acq.generated_fps:.0f} fps; the simulator '
                    'caps generation so it does not peg a core)')
        self.log(f'Simulated acquisition started at {width}x{height}, '
                 f'{rate:.1f} fps requested{note}.')

    def stop(self) -> None:
        was_running = self._running
        self.acq.stop()
        self._running = False
        if was_running:
            self.log('Simulated acquisition stopped.')

    def close(self) -> None:
        self.stop()
