"""Record a burst of frames to .raw.

A recording gets the camera to itself: whatever is running is stopped, a fresh
acquisition starts with tag checking on and the preview paused, and it stops
again the moment the requested frames are in hand. Nothing competes with the
capture that way.

The whole burst is buffered in RAM before it is written, so the size is shown
while you are choosing.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from nicegui import ui

import rawio


class CaptureFrames:
    def __init__(self, session) -> None:
        self.session = session
        self.build()
        session.register(self)

    def build(self) -> None:
        with ui.row().classes('w-full max-w-3xl mx-auto items-center gap-3'):
            self.frames_input = ui.number(value=100, min=1, max=1_000_000, step=10,
                                          format='%d').props('dense outlined') \
                .classes('w-24')
            ui.label('frames').classes('text-xs opacity-50')
            self.record_button = ui.button('Record', on_click=self.start) \
                .props('flat').classes('text-red-400')
            self.status_label = ui.label('').classes('text-xs opacity-50')
        self.progress = ui.linear_progress(value=0.0, show_value=False) \
            .classes('w-full max-w-3xl mx-auto').style('height: 2px')

        self.frames_input.on_value_change(lambda e: self.sync())
        ui.timer(0.25, self.refresh)
        self.sync()

    # ------------------------------------------------------------ behaviour

    def size_text(self) -> str:
        settings = self.session.settings
        frames = int(self.frames_input.value or 0)
        mb = frames * int(settings.width) * int(settings.height) * 2 / 1e6
        return f'{mb:,.0f} MB'

    async def start(self) -> None:
        session = self.session
        job = session.record_job
        if job is not None and not job.finished.is_set():
            session.hub.disarm_recording()
            session.log('Recording cancelled.')
            return
        if not session.connected:
            ui.notify('Not connected', type='warning')
            return
        frames = int(self.frames_input.value or 0)
        if frames <= 0:
            return

        # A recording gets the camera to itself: stop whatever is running, then
        # start a fresh acquisition with tag checking on and the preview off,
        # so nothing is copying or encoding frames while the point of the
        # exercise is not dropping any.
        resume_viewer = session.acquiring
        if session.acquiring:
            await asyncio.to_thread(session.stop_acquisition)
        session.log('Recording from idle: acquisition restarted with tag '
                    'checking, preview paused.')
        try:
            await asyncio.to_thread(session.start_acquisition, True, False)
        except Exception as exc:
            session.log(f'Could not start the capture: {exc}')
            ui.notify(str(exc), type='negative', multi_line=True)
            if resume_viewer:
                await asyncio.to_thread(session.start_acquisition, False, True)
            session.sync_all()
            return

        folder = Path(session.settings.output_dir or 'captures')
        name = rawio.default_filename(session.hub.width, session.hub.height,
                                      session.settings.fps, frames)
        try:
            job = session.hub.arm_recording(folder / name, frames,
                                            session.settings.fps)
        except Exception as exc:
            ui.notify(str(exc), type='negative')
            await asyncio.to_thread(session.stop_acquisition)
            if resume_viewer:
                await asyncio.to_thread(session.start_acquisition, False, True)
            session.sync_all()
            return

        session.record_job = job
        session.record_message = ''
        source = 'simulator' if session.simulated else 'fli-usb-sdk'
        session.log(f'Recording {frames} frames of {job.width}x{job.height} '
                    f'to {name} ...')
        print(f'Recording {frames} frames of {job.width}x{job.height} '
              f'to {name} ...', flush=True)
        session.record_task = asyncio.create_task(
            self._run(job, source, resume_viewer))
        session.sync_all()

    async def _run(self, job, source: str, resume_viewer: bool) -> None:
        session = self.session
        try:
            while not job.finished.is_set():
                await asyncio.sleep(0.05)
            # We have the frames; stop before spending time writing them.
            await asyncio.to_thread(session.stop_acquisition)
            if job.cancelled:
                session.record_message = 'cancelled'
                return
            # First frame to last, so acquisition start-up is reported
            # separately rather than dragging the frame rate down.
            elapsed = max((job.last_frame or 0) - (job.first_frame or 0), 1e-9)
            startup = max((job.first_frame or 0) - job.started, 0.0)
            raw_path, json_path = await asyncio.to_thread(
                rawio.write_capture, job.path, job.buffer,
                width=job.width, height=job.height, frames=job.frames,
                fps=job.fps, source=source, tint=session.settings.tint,
                extra={'measured_fps': round(max(job.count - 1, 1) / elapsed, 3),
                       'capture_seconds': round(elapsed, 3),
                       'startup_seconds': round(startup, 3)})
            # The SDK's own verdict on the burst, independent of any
            # assumption about where the tag sits in the frame.
            errors = session.camera.errors.errors if session.camera else 0
            verdict = ('no tag errors - no dropped frames' if errors == 0
                       else f'{errors:,} tag errors - frames may be missing')
            session.record_message = (f'saved {raw_path.name}'
                                      + ('' if errors == 0 else f'  ({errors:,} tag errors)'))
            session.log(f'Saved {raw_path} '
                        f'({raw_path.stat().st_size / 1e6:.1f} MB) + '
                        f'{json_path.name}')
            session.log(f'Recording check: {verdict}.')

            # Release the capture buffer now the file is on disk. At 512x512 a
            # 1000-frame burst is half a gigabyte, and holding it until the
            # next recording replaces it means two of them coexist.
            job.view = None
            job.buffer = bytearray()

            # A durable record of the acquisition, in the terminal: it survives
            # a browser reload and can be piped to a file.
            meta = rawio.read_meta(raw_path) or {}
            for line in rawio.log_lines(raw_path, json_path, meta, errors):
                print(line, flush=True)
        except Exception as exc:
            session.record_message = 'failed'
            session.log(f'Recording failed: {exc}')
        finally:
            session.hub.disarm_recording()
            if session.acquiring:
                await asyncio.to_thread(session.stop_acquisition)
            if resume_viewer:
                try:
                    await asyncio.to_thread(session.start_acquisition, False, True)
                except Exception as exc:
                    session.log(f'Could not resume the preview: {exc}')
            session.sync_all()

    def refresh(self) -> None:
        job = self.session.record_job
        if job is not None and not job.finished.is_set():
            self.progress.set_value(job.progress)
            self.status_label.set_text(f'{job.count} / {job.frames}')
        else:
            self.progress.set_value(0.0)
            self.status_label.set_text(
                self.session.record_message or self.size_text())

    def sync(self) -> None:
        job = self.session.record_job
        recording = job is not None and not job.finished.is_set()
        self.record_button.set_text('Cancel' if recording else 'Record')
        if not recording:
            self.status_label.set_text(self.size_text())
