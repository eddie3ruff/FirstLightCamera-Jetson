"""Visual crop picker: pick a size, drag the window, apply it.

The grid shows the camera's windowing granularity - columns snap to 32, rows
to 4 - so a window you can draw is a window the camera will accept.

Dragging only moves the box and updates the readout.  **Apply** is what talks to
the camera, because applying stops and restarts acquisition and you do not want
that happening on every mouse move.
"""
from __future__ import annotations

import asyncio

from nicegui import ui

from serial_console import (COLUMN_STEP, FULL_FRAME, FULL_SIZE, ROW_STEP,
                            WINDOW_SIZES, align_and_clamp, crop_commands,
                            window_shape)

FRAME_W, FRAME_H = FULL_FRAME


def _build_base_src() -> str:
    """The sensor with its granularity grid, as a data URI.  Built once."""
    lines = []
    for x in range(0, FRAME_W + 1, COLUMN_STEP):
        lines.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{FRAME_H}" '
                     f'stroke="#555" stroke-width="0.5" />')
    for y in range(0, FRAME_H + 1, ROW_STEP):
        lines.append(f'<line x1="0" y1="{y}" x2="{FRAME_W}" y2="{y}" '
                     f'stroke="#555" stroke-width="0.5" />')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{FRAME_W}" '
           f'height="{FRAME_H}" viewBox="0 0 {FRAME_W} {FRAME_H}" '
           f'preserveAspectRatio="xMidYMid meet">'
           f'<rect x="0" y="0" width="{FRAME_W}" height="{FRAME_H}" '
           f'fill="#282828" stroke="#191919" stroke-width="2"/>'
           + ''.join(lines) + '</svg>')
    return 'data:image/svg+xml;utf8,' + svg.replace('#', '%23')


BASE_SRC = _build_base_src()


class CropPanel:
    def __init__(self, session) -> None:
        self.session = session
        self._dragging = False
        self._grab_dx = 0
        self._grab_dy = 0
        #  True while sync() is writing the selector, so the resulting
        #  on_change is not mistaken for the user picking a new size.
        self._syncing = False
        self.build()
        session.register(self)

    # ---------------------------------------------------------------- state

    @property
    def size(self) -> int:
        return int(self.session.settings.crop_size)

    @property
    def is_full_frame(self) -> bool:
        return self.size >= FULL_SIZE

    def shape(self) -> tuple[int, int]:
        return window_shape(self.size)

    def ranges(self) -> tuple[tuple[int, int], tuple[int, int]]:
        width, height = self.shape()
        x, y = int(self.session.settings.crop_x), int(self.session.settings.crop_y)
        return (x, x + width - 1), (y, y + height - 1)

    def commands(self) -> list[str]:
        return crop_commands(self.size, int(self.session.settings.crop_x),
                             int(self.session.settings.crop_y))

    # ------------------------------------------------------------------ UI

    def build(self) -> None:
        settings = self.session.settings
        with ui.row().classes('w-full items-end gap-3 flex-wrap'):
            self.size_select = ui.select(
                {**{s: f'{s}x{s}' for s in WINDOW_SIZES},
                 FULL_SIZE: f'{FRAME_W}x{FRAME_H} full frame'},
                value=settings.crop_size, label='Crop size',
                on_change=self.on_size_change).classes('w-48')
            self.apply_button = ui.button('Apply', on_click=self.apply).props('flat')
        self.readout = ui.label('').classes('text-xs font-mono opacity-60')
        self.image = ui.interactive_image(
            BASE_SRC, on_mouse=self.on_mouse,
            events=['mousedown', 'mousemove', 'mouseup'],
        ).classes('w-full max-w-2xl mx-auto')

        self.clamp_origin()
        self.draw()

    # ------------------------------------------------------------ behaviour

    def clamp_origin(self) -> None:
        settings = self.session.settings
        width, height = self.shape()
        if self.is_full_frame:
            settings.crop_x, settings.crop_y = 0, 0
            return
        settings.crop_x = align_and_clamp(settings.crop_x, COLUMN_STEP, FRAME_W, width)
        settings.crop_y = align_and_clamp(settings.crop_y, ROW_STEP, FRAME_H, height)

    def on_size_change(self, event) -> None:
        """User picked a size.  Ignored when sync() is writing the selector."""
        if self._syncing:
            return
        self.set_size(int(event.value))

    def set_size(self, new_size: int) -> None:
        """Change size, keeping the window centred on where it already was."""
        settings = self.session.settings
        old_w, old_h = self.shape()
        centre_x = settings.crop_x + old_w / 2
        centre_y = settings.crop_y + old_h / 2

        settings.crop_size = int(new_size)
        new_w, new_h = self.shape()
        settings.crop_x = int(round(centre_x - new_w / 2))
        settings.crop_y = int(round(centre_y - new_h / 2))
        self.clamp_origin()
        self.draw()

    def on_mouse(self, event) -> None:
        x, y = getattr(event, 'image_x', None), getattr(event, 'image_y', None)
        if x is None or y is None or self.is_full_frame:
            return
        settings = self.session.settings
        width, height = self.shape()

        if event.type == 'mousedown':
            inside = (settings.crop_x <= x <= settings.crop_x + width
                      and settings.crop_y <= y <= settings.crop_y + height)
            self._dragging = inside
            if inside:
                self._grab_dx = x - settings.crop_x
                self._grab_dy = y - settings.crop_y
        elif event.type == 'mousemove' and self._dragging:
            new_x = align_and_clamp(int(x - self._grab_dx), COLUMN_STEP, FRAME_W, width)
            new_y = align_and_clamp(int(y - self._grab_dy), ROW_STEP, FRAME_H, height)
            if (new_x, new_y) != (settings.crop_x, settings.crop_y):
                settings.crop_x, settings.crop_y = new_x, new_y
                self.draw()
        elif event.type == 'mouseup':
            self._dragging = False

    def draw(self) -> None:
        width, height = self.shape()
        settings = self.session.settings
        self.image.content = (
            f'<rect x="{settings.crop_x}" y="{settings.crop_y}" '
            f'width="{width}" height="{height}" '
            f'fill="#8000ff" fill-opacity="0.45" '
            f'stroke="#8000ff" stroke-width="2"/>')
        self.readout.set_text('   '.join(self.commands()))

    async def apply(self) -> None:
        if not self.session.connected:
            ui.notify('Not connected', type='warning')
            return
        self.apply_button.disable()
        try:
            await self.session.apply_crop()
        finally:
            self.apply_button.enable()

    def sync(self) -> None:
        """Reflect session state.  Never recentres - the origin is already set."""
        if self.size_select.value != self.session.settings.crop_size:
            self._syncing = True
            try:
                self.size_select.value = self.session.settings.crop_size
            finally:
                self._syncing = False
        self.draw()
