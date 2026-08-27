"""Writing and reading the .raw captures, with a JSON sidecar.

The Jetson version encoded the geometry in the file name and left you to
remember the rest.  Every capture here gets a sidecar of the same name with a
.json extension, so `load_raw()` can reshape the file months later without
anybody guessing.
"""
from __future__ import annotations

import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DTYPE = '<u2'  # little-endian uint16; the camera fills the low 14 bits
SCHEMA = 'flcam-raw-1'


def default_filename(width: int, height: int, fps: float, frames: int,
                     when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime('%Y%m%d_%H%M%S')
    return f'capture_{frames}f_{width}x{height}_{fps:.2f}fps_{stamp}.raw'


def sidecar_path(raw_path: str | Path) -> Path:
    return Path(raw_path).with_suffix('.json')


def write_capture(path: str | Path, buffer, *, width: int, height: int,
                  frames: int, fps: float, source: str = 'unknown',
                  tint: float = 0.0, extra: dict | None = None) -> tuple[Path, Path]:
    """Write the raw buffer and its sidecar.  Returns (raw_path, json_path)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as handle:
        handle.write(buffer)

    meta = {
        'schema': SCHEMA,
        'file': path.name,
        'width': width,
        'height': height,
        'frames': frames,
        'dtype': DTYPE,
        'bits_per_pixel': 16,
        'significant_bits': 14,
        'bytes_per_frame': width * height * 2,
        'frame_rate_hz': fps,
        'integration_time_s': tint,
        'source': source,
        'recorded_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'recorded_local': datetime.now().isoformat(timespec='seconds'),
        'host': platform.node(),
        'platform': f'{platform.system()} {platform.release()}',
        'python': sys.version.split()[0],
        'layout': 'frames stacked in order; each frame is height rows of width '
                  'pixels, row-major, no padding',
    }
    if extra:
        meta.update(extra)

    json_path = sidecar_path(path)
    json_path.write_text(json.dumps(meta, indent=2))
    return path, json_path


def read_meta(path: str | Path) -> dict | None:
    """Load the sidecar next to a raw file, if there is one."""
    json_path = sidecar_path(path)
    if not json_path.is_file():
        return None
    try:
        return json.loads(json_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def load_raw(path: str | Path, width: int | None = None, height: int | None = None,
             mmap: bool = True) -> np.ndarray:
    """Load a capture as a (frames, height, width) uint16 array.

    Geometry comes from the sidecar unless you pass it explicitly.  Uses a
    memory map by default so multi-gigabyte captures open instantly.
    """
    path = Path(path)
    if width is None or height is None:
        meta = read_meta(path)
        if meta is None:
            raise ValueError(
                f'No sidecar for {path.name} and no geometry given. '
                'Pass width= and height= explicitly.')
        width = width or int(meta['width'])
        height = height or int(meta['height'])

    frame_bytes = width * height * 2
    size = path.stat().st_size
    frames, remainder = divmod(size, frame_bytes)
    if frames == 0:
        raise ValueError(f'{path.name} is smaller than one {width}x{height} frame.')
    if remainder:
        print(f'Warning: {path.name} has {remainder} trailing bytes '
              f'({frames} whole frames of {width}x{height}); ignoring the tail.')

    data = (np.memmap(path, dtype=DTYPE, mode='r', shape=(frames, height, width))
            if mmap else
            np.fromfile(path, dtype=DTYPE, count=frames * width * height)
            .reshape(frames, height, width))
    return data


RULE = '-' * 66


def log_lines(raw_path: Path, json_path: Path, meta: dict,
              tag_errors: int | None = None) -> list[str]:
    """A block describing one capture, for the terminal.

    The terminal is the natural place for a running log of what was acquired -
    it survives a browser reload and can be piped to a file.
    """
    size_mb = raw_path.stat().st_size / 1e6 if raw_path.exists() else 0.0
    width, height = meta.get('width', 0), meta.get('height', 0)
    fps = meta.get('frame_rate_hz', 0.0)
    measured = meta.get('measured_fps')
    seconds = meta.get('capture_seconds')
    tint = meta.get('integration_time_s') or 0.0

    rate = f'{fps:,.3f} fps'
    if measured:
        rate += f' requested, {measured:,.1f} measured'

    lines = [RULE, f'RECORDING  {meta.get("recorded_local", "")}',
             f'  file        {raw_path.name}',
             f'  geometry    {width} x {height}  '
             f'({meta.get("bytes_per_frame", 0):,} bytes/frame)',
             f'  frames      {meta.get("frames", 0):,}',
             f'  frame rate  {rate}']
    if seconds:
        lines.append(f'  duration    {seconds:,.3f} s  (stream only)')
    startup = meta.get('startup_seconds')
    if startup:
        lines.append(f'  start-up    {startup:,.3f} s  (acquisition to first frame)')
    if tint:
        lines.append(f'  tint        {tint * 1e6:,.1f} us')
    lines.append(f'  size        {size_mb:,.1f} MB')
    if tag_errors is not None:
        lines.append('  tag check   ' + ('clean - no dropped frames'
                                         if tag_errors == 0 else
                                         f'{tag_errors:,} tag errors - frames may be missing'))
    lines += [f'  sidecar     {json_path.name}', f'  source      {meta.get("source", "")}',
              RULE]
    return lines


def describe(path: str | Path) -> str:
    """One-paragraph summary of a capture, for the CLI."""
    path = Path(path)
    meta = read_meta(path) or {}
    lines = [f'{path.name}  ({path.stat().st_size / 1e6:.1f} MB)']
    if meta:
        lines.append(f"  {meta.get('width')}x{meta.get('height')} x "
                     f"{meta.get('frames')} frames @ "
                     f"{meta.get('frame_rate_hz', 0):.2f} fps")
        lines.append(f"  source: {meta.get('source')}   recorded: "
                     f"{meta.get('recorded_local')}")
    else:
        lines.append('  no sidecar found - geometry unknown')
    return '\n'.join(lines)
