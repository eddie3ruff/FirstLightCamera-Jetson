#!/usr/bin/env python3
"""Inspect a .raw capture: summary, per-frame statistics, PNG export.

    python3 read_raw.py captures/capture_100f_640x512_600.00fps_...raw
    python3 read_raw.py capture.raw --frame 10 --png frame10.png
    python3 read_raw.py capture.raw -W 640 -H 512      # no sidecar available

Loading in your own code is two lines:

    from rawio import load_raw
    frames = load_raw('capture.raw')     # (frames, height, width) uint16
"""
from __future__ import annotations

import argparse

import numpy as np

import imaging
import rawio


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('path', help='.raw capture file')
    parser.add_argument('-W', '--width', type=int, default=None)
    parser.add_argument('-H', '--height', type=int, default=None)
    parser.add_argument('--frame', type=int, default=0, help='frame index to export')
    parser.add_argument('--png', default=None, help='write the frame to this PNG')
    parser.add_argument('--colormap', default='turbo', choices=list(imaging.COLORMAPS))
    parser.add_argument('--stats', type=int, default=5,
                        help='how many frames to summarise (0 for none)')
    parser.add_argument('--tags', action='store_true',
                        help='check the embedded frame counter for dropped frames')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(rawio.describe(args.path))

    frames = rawio.load_raw(args.path, args.width, args.height)
    n, height, width = frames.shape
    print(f'\nLoaded {n} frames of {width}x{height} (uint16, low 14 bits used)')

    if args.stats:
        print(f'\n{"frame":>6} {"min":>6} {"max":>6} {"mean":>10} {"std":>9}')
        for i in range(min(args.stats, n)):
            frame = np.asarray(frames[i], dtype=np.float64)
            print(f'{i:>6} {frame.min():>6.0f} {frame.max():>6.0f} '
                  f'{frame.mean():>10.2f} {frame.std():>9.2f}')
        if n > args.stats:
            print(f'   ... {n - args.stats} more frames')

    if args.tags:
        tags = (frames[:, 0, 0].astype(np.int64)
                + (frames[:, 0, 1].astype(np.int64) << 16))
        deltas = np.unique(np.diff(tags))
        print('\nFrame-counter tags:', tags[:6].tolist(), '...')
        if set(deltas.tolist()) == {1}:
            print('  contiguous - no dropped frames')
        else:
            print(f'  DROPPED FRAMES - deltas seen: {deltas.tolist()}')

    if args.png:
        index = max(0, min(args.frame, n - 1))
        rgb = imaging.to_rgb(np.asarray(frames[index]), colormap=args.colormap)
        with open(args.png, 'wb') as handle:
            handle.write(imaging.png_bytes(rgb))
        print(f'\nWrote frame {index} to {args.png}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
