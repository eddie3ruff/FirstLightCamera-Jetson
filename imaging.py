"""Turning 16-bit sensor frames into something a browser can show.

The Jetson version pulled in OpenCV just for a colormap and a PNG encode, and
hard-coded a `>> 6` shift that clips anything not filling the full 14-bit range.
This module does the same job with numpy, autoscales by percentile so a dim
frame is still visible, and encodes with Pillow when it is installed - falling
back to a small pure-stdlib PNG writer when it is not.
"""
from __future__ import annotations

import base64
import struct
import zlib

import numpy as np

try:  # optional: only used for reading images back in, never for the preview
    from PIL import Image
    HAVE_PILLOW = True
except ImportError:  # pragma: no cover - exercised only on bare installs
    Image = None
    HAVE_PILLOW = False

COLORMAPS = ('gray', 'gray inverted', 'turbo', 'hot')
_LUT_CACHE: dict[str, np.ndarray] = {}


def _turbo_lut() -> np.ndarray:
    """Polynomial approximation of Google's Turbo colormap (256 x RGB)."""
    x = np.linspace(0.0, 1.0, 256)
    v4 = np.stack([np.ones_like(x), x, x ** 2, x ** 3], axis=1)
    v2 = np.stack([x ** 4, x ** 5], axis=1)
    red = v4 @ [0.13572138, 4.61539260, -42.66032258, 132.13108234] \
        + v2 @ [-152.94239396, 59.28637943]
    green = v4 @ [0.09140261, 2.19418839, 4.84296658, -14.18503333] \
        + v2 @ [4.27729857, 2.82956604]
    blue = v4 @ [0.10667330, 12.64194608, -60.58204836, 110.36276771] \
        + v2 @ [-89.90310912, 27.34824973]
    rgb = np.clip(np.stack([red, green, blue], axis=1), 0.0, 1.0)
    return (rgb * 255.0 + 0.5).astype(np.uint8)


def _hot_lut() -> np.ndarray:
    x = np.linspace(0.0, 1.0, 256)
    red = np.clip(x / 0.4, 0, 1)
    green = np.clip((x - 0.35) / 0.4, 0, 1)
    blue = np.clip((x - 0.75) / 0.25, 0, 1)
    return (np.stack([red, green, blue], axis=1) * 255.0 + 0.5).astype(np.uint8)


def lut(name: str) -> np.ndarray:
    """256x3 uint8 lookup table for the named colormap."""
    name = (name or 'gray').lower()
    if name not in _LUT_CACHE:
        if name == 'turbo':
            table = _turbo_lut()
        elif name == 'hot':
            table = _hot_lut()
        elif name == 'gray inverted':
            ramp = np.arange(255, -1, -1, dtype=np.uint8)
            table = np.stack([ramp] * 3, axis=1)
        else:
            ramp = np.arange(256, dtype=np.uint8)
            table = np.stack([ramp] * 3, axis=1)
        _LUT_CACHE[name] = table
    return _LUT_CACHE[name]


def frame_from_bytes(data: bytes, width: int, height: int) -> np.ndarray:
    """View raw little-endian uint16 bytes as a (height, width) array."""
    expected = width * height * 2
    if len(data) != expected:
        raise ValueError(f'frame is {len(data)} bytes, expected {expected} '
                         f'for {width}x{height} 16-bit')
    return np.frombuffer(data, dtype='<u2').reshape(height, width)


def decimate(frame: np.ndarray, max_dim: int) -> np.ndarray:
    """Cheap integer-stride downscale to keep the preview payload small."""
    if max_dim <= 0:
        return frame
    step = max(1, int(np.ceil(max(frame.shape) / max_dim)))
    return frame[::step, ::step] if step > 1 else frame


def autoscale(frame: np.ndarray, low_pct: float = 0.5,
              high_pct: float = 99.5) -> tuple[float, float]:
    """Percentile limits, computed on a subsample so it stays cheap at speed."""
    sample = frame[::4, ::4] if frame.size > 20000 else frame
    lo, hi = np.percentile(sample, [low_pct, high_pct])
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def to_rgb(frame: np.ndarray, *, colormap: str = 'gray',
           limits: tuple[float, float] | None = None) -> np.ndarray:
    """Scale to 8 bits between `limits` and apply the colormap."""
    lo, hi = limits if limits is not None else autoscale(frame)
    if hi <= lo:
        hi = lo + 1.0
    scaled = (frame.astype(np.float32) - lo) * (255.0 / (hi - lo))
    indices = np.clip(scaled, 0.0, 255.0).astype(np.uint8)
    return lut(colormap)[indices]


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack('>I', len(payload)) + tag + payload
            + struct.pack('>I', zlib.crc32(tag + payload) & 0xFFFFFFFF))


def _png_bytes(rgb: np.ndarray, level: int = 1) -> bytes:
    """Minimal truecolour PNG writer, so Pillow stays optional."""
    height, width, _ = rgb.shape
    rows = np.concatenate(
        [np.zeros((height, 1), dtype=np.uint8), rgb.reshape(height, width * 3)],
        axis=1)  # a leading 0 = "no filter" on each scanline
    header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header)
            + _chunk(b'IDAT', zlib.compress(rows.tobytes(), level))
            + _chunk(b'IEND', b''))


def _png_indexed(indices: np.ndarray, palette: np.ndarray, level: int = 1) -> bytes:
    """Palette PNG: one byte per pixel instead of three.

    The colormap is already a 256-entry lookup table, so it can go in the PNG's
    own palette and the image stays a plain index array.  That is a third of
    the bytes of a truecolour PNG for an identical picture - which matters a
    lot when every preview frame is pushed down the websocket as base64.
    """
    height, width = indices.shape
    rows = np.concatenate(
        [np.zeros((height, 1), dtype=np.uint8), indices], axis=1)
    header = struct.pack('>IIBBBBB', width, height, 8, 3, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + _chunk(b'IHDR', header)
            + _chunk(b'PLTE', palette.astype(np.uint8).tobytes())
            + _chunk(b'IDAT', zlib.compress(rows.tobytes(), level))
            + _chunk(b'IEND', b''))


def png_bytes(rgb: np.ndarray, level: int = 6) -> bytes:
    """Encode an RGB array as PNG.  Lossless, for snapshots and exports."""
    return _png_bytes(rgb, level)


def encode(rgb: np.ndarray, level: int = 1) -> tuple[bytes, str]:
    """Encode an RGB array as PNG.  Lossless, on purpose.

    JPEG is smaller over the wire but visibly destroys a noisy 64x64 frame once
    it is scaled up on screen, and the camera data is the point here.
    """
    return _png_bytes(rgb, level), 'image/png'


def to_indices(frame: np.ndarray, limits: tuple[float, float] | None = None
               ) -> tuple[np.ndarray, float, float]:
    """Scale to 8-bit colormap indices, without expanding to RGB."""
    lo, hi = limits if limits is not None else autoscale(frame)
    if hi <= lo:
        hi = lo + 1.0
    scaled = (frame.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(scaled, 0.0, 255.0).astype(np.uint8), lo, hi


def render_preview(data: bytes, width: int, height: int, *, colormap: str = 'gray',
                   limits: tuple[float, float] | None = None,
                   max_dim: int = 4096, level: int = 1
                   ) -> tuple[str, dict]:
    """Raw frame bytes in, a base64 PNG data URI plus statistics out.

    Returns a data URI so the image can be pushed straight down the websocket.
    Encoded as a palette PNG: lossless, full resolution, and a third of the
    bytes of truecolour.
    """
    frame = frame_from_bytes(data, width, height)
    small = decimate(frame, max_dim)
    indices, lo, hi = to_indices(small, limits)
    payload = _png_indexed(indices, lut(colormap), level)
    stats = {
        'min': int(frame.min()),
        'max': int(frame.max()),
        'mean': float(frame.mean()),
        'display_low': lo,
        'display_high': hi,
        'preview_shape': (int(small.shape[1]), int(small.shape[0])),
    }
    return 'data:image/png;base64,' + base64.b64encode(payload).decode(), stats
