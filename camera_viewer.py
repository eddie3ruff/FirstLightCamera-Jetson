"""The live view: an image, a quiet readout line, and two buttons.

Everything that is not "watch the camera" or "record a burst" lives behind a
disclosure in settings_panel.py.  The default screen should be the picture.

The frame is pushed to the browser as a base64 PNG data URI, the way the
original app did it - lossless, so the sensor noise stays sensor noise.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from nicegui import ui

import imaging

#  Cap on preview traffic.  A 640x512 palette PNG is ~280 KB, so 12 fps of it
#  would be 3.4 MB/s down the websocket and the page stops responding to
#  clicks.  The preview rate is whatever the user asked for, or this budget,
#  whichever is slower - a 64x64 window is tiny and never hits it.
PREVIEW_BYTES_PER_SECOND = 1_500_000

PLACEHOLDER = ('data:image/svg+xml;utf8,'
               '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="512">'
               '<rect width="100%" height="100%" fill="%23111"/></svg>')

class CameraViewer:
    def __init__(self, session) -> None:
        self.session = session
        self._last_frame_shown = -1
        self._fps_mark = (0, time.monotonic())
        self._fps = 0.0
        self._reported_error: str | None = None
        self._rendering = False
        self.build()
        session.register(self)

    def build(self) -> None:
        session = self.session

        # Cap the height so the whole default view - image, readout, record
        # row - fits on screen without scrolling.
        # interactive_image puts an inline `aspect-ratio` on its container, so
        # capping the image height alone leaves the box open underneath.  Drive
        # the box from its height instead and let the width follow the ratio -
        # that works for any frame shape, 640x512 or a square 64x64 crop.
        ui.add_css('.cred-preview { height: 52vh !important; width: auto !important;'
                   ' max-width: min(100%, 48rem); margin-left: auto;'
                   ' margin-right: auto; }'
                   '.cred-preview img, .cred-preview svg'
                   ' { height: 100%; width: 100%; object-fit: contain;'
                   # A 64x64 frame blown up to ~700px must show pixels, not a
                   # smoothed blur. This is the other half of the sharpness.
                   ' image-rendering: pixelated; }')
        self.preview = ui.interactive_image(PLACEHOLDER) \
            .classes('cred-preview rounded-lg') \
            .props('no-transition no-spinner')

        with ui.row().classes('w-full max-w-3xl mx-auto items-center '
                              'justify-between gap-4'):
            self.readout = ui.label('').classes('text-xs font-mono opacity-50')
            with ui.row().classes('items-center gap-2'):
                self.acquire_button = ui.button(
                    'Start', on_click=self.toggle_acquisition).props('flat')

        self.preview_timer = ui.timer(
            1.0 / max(session.settings.preview_fps, 1.0), self.refresh_preview)
        ui.timer(0.5, self.refresh_readout)
        self.sync()

    # ------------------------------------------------------------ behaviour

    async def toggle_acquisition(self) -> None:
        session = self.session
        if session.acquiring:
            await asyncio.to_thread(session.stop_acquisition)
        else:
            if not session.connected:
                ui.notify('Not connected - see Settings', type='warning')
                return
            if session.settings.free_run_on_start:
                await session.enable_free_running()
            if session.settings.use_camera_geometry:
                await session.read_geometry(quiet=True)
            try:
                await asyncio.to_thread(session.start_acquisition)
            except Exception as exc:
                session.log(f'Start failed: {exc}')
                ui.notify(str(exc), type='negative', multi_line=True)
        session.sync_all()

    async def save_snapshot(self) -> None:
        session = self.session
        latest = session.hub.latest()
        if latest is None:
            ui.notify('No frame yet', type='warning')
            return
        data, number = latest
        settings = session.settings
        folder = Path(settings.output_dir or 'captures')
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f'snapshot_{time.strftime("%Y%m%d_%H%M%S")}.png'
        limits = None if settings.autoscale else (settings.display_low,
                                                  settings.display_high)
        width, height = session.hub.width, session.hub.height

        def _write() -> None:
            frame = imaging.frame_from_bytes(data, width, height)
            rgb = imaging.to_rgb(frame, colormap=settings.colormap, limits=limits)
            path.write_bytes(imaging.png_bytes(rgb))

        await asyncio.to_thread(_write)
        session.log(f'Snapshot saved: {path} (frame {number})')
        ui.notify(f'Saved {path.name}', type='positive')

    async def refresh_preview(self) -> None:
        """Render the newest frame and push it to the browser as a data URI.

        The encode runs in a worker thread so a 640x512 frame does not stall
        the event loop, and the rate is paced by the preview setting rather
        than the camera's frame rate.
        """
        if self._rendering:
            return                      # still encoding the previous one
        session = self.session
        latest = session.hub.latest()
        if latest is None:
            return
        data, number = latest
        if number == self._last_frame_shown:
            return

        settings = session.settings
        limits = None if settings.autoscale else (settings.display_low,
                                                  settings.display_high)
        width, height = session.hub.width, session.hub.height
        self._rendering = True
        try:
            uri, stats = await asyncio.to_thread(
                imaging.render_preview, data, width, height,
                colormap=settings.colormap, limits=limits)
        except ValueError:
            return                      # geometry changed mid-flight
        finally:
            self._rendering = False
        session.last_stats = stats
        self._last_frame_shown = number
        self.preview.set_source(uri)
        self._pace(len(uri))

    def _pace(self, uri_length: int) -> None:
        """Slow the preview down if the frames are big, leaving the UI usable."""
        payload = uri_length * 3 // 4        # base64 -> bytes
        wanted = max(1.0 / max(self.session.settings.preview_fps, 1.0),
                     payload / PREVIEW_BYTES_PER_SECOND)
        if abs(self.preview_timer.interval - wanted) > 0.01:
            self.preview_timer.interval = wanted

    def refresh_readout(self) -> None:
        """Measure the frame rate here, where it costs nothing.

        Sampling frame_count on a timer keeps the acquisition callback free of
        any timing work - at 9400 fps that mattered.
        """
        session = self.session
        count, now = session.hub.frame_count, time.monotonic()
        last_count, last_time = self._fps_mark
        elapsed = now - last_time
        if elapsed >= 0.5:
            if count >= last_count:
                self._fps = (count - last_count) / elapsed
            self._fps_mark = (count, now)

        error = session.hub.callback_error
        if error and error != self._reported_error:
            self._reported_error = error
            session.log(f'Frame callback error (frames are being dropped): {error}')

        settings = session.settings
        if not session.connected:
            self.readout.set_text('not connected')
            return
        parts = [f'{session.hub.width or settings.width}'
                 f'x{session.hub.height or settings.height}']
        if session.acquiring:
            parts.append(f'{self._fps:,.0f} fps')
        else:
            parts.append('idle')
        if settings.tint:
            parts.append(f'tint {settings.tint * 1e6:,.0f} us')
        # SDK diagnostics go to the terminal; surface just the count here so
        # a stream losing frames is not invisible.
        dropped = session.camera.errors.errors if session.camera else 0
        if dropped:
            parts.append(f'{dropped:,} dropped')
        self.readout.set_text('   '.join(parts))

    def sync(self) -> None:
        session = self.session
        self.acquire_button.set_text('Stop' if session.acquiring else 'Start')

    def set_preview_interval(self, fps: float) -> None:
        self.preview_timer.interval = 1.0 / max(fps, 1.0)
