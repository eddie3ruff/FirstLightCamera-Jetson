"""Everything you rarely touch, behind one disclosure.

Connection, frame geometry, display range and preview rate all live here so the
main screen stays a picture and two buttons.
"""
from __future__ import annotations

import asyncio

from nicegui import ui

import imaging
from serial_console import DEFAULT_BAUD, DEFAULT_PORT, SIM_PORT, list_ports


class SettingsPanel:
    def __init__(self, session, viewer) -> None:
        self.session = session
        self.viewer = viewer
        self.build()
        session.register(self)

    def build(self) -> None:
        session = self.session
        settings = session.settings

        ui.label('Connection').classes('text-xs uppercase opacity-50 mt-2')
        with ui.row().classes('w-full items-end gap-3 flex-wrap'):
            self.port_select = ui.select({}, label='Port').classes('grow min-w-48')
            self.baud_select = ui.select(
                [9600, 19200, 38400, 57600, 115200, 230400],
                value=DEFAULT_BAUD, label='Baud').classes('w-28')
            ui.button(icon='refresh', on_click=lambda: self.refresh_ports()) \
                .props('flat dense')
            self.connect_button = ui.button('Connect', on_click=self.toggle_connection) \
                .props('flat')

        ui.label('Frame').classes('text-xs uppercase opacity-50 mt-3')
        with ui.row().classes('w-full items-end gap-3 flex-wrap'):
            self.geometry_switch = ui.switch('From camera',
                                             value=settings.use_camera_geometry)
            self.width_input = ui.number('Width', value=settings.width, min=1,
                                         max=4096, step=1, format='%d').classes('w-24')
            self.height_input = ui.number('Height', value=settings.height, min=1,
                                          max=4096, step=1, format='%d').classes('w-24')
            self.free_run_switch = ui.switch('Free-run on start',
                                             value=settings.free_run_on_start)
            self.tint_input = ui.number('Tint (us)', value=settings.target_tint * 1e6,
                                        min=0.1, max=1e6, step=1,
                                        format='%.1f').classes('w-32') \
                .tooltip('Integration time applied after every crop change')

        ui.label('Display').classes('text-xs uppercase opacity-50 mt-3')
        with ui.row().classes('w-full items-end gap-3 flex-wrap'):
            self.colormap_select = ui.select(list(imaging.COLORMAPS),
                                             value=settings.colormap,
                                             label='Colormap').classes('w-36')
            self.preview_fps_input = ui.number('Preview fps',
                                               value=settings.preview_fps, min=1,
                                               max=60, step=1,
                                               format='%.0f').classes('w-28')
            self.scale_switch = ui.switch('Auto contrast', value=settings.autoscale)
            self.low_input = ui.number('Min', value=settings.display_low, min=0,
                                       max=65535, step=64, format='%.0f').classes('w-24')
            self.high_input = ui.number('Max', value=settings.display_high, min=1,
                                        max=65535, step=64, format='%.0f').classes('w-24')

        ui.label('Recording').classes('text-xs uppercase opacity-50 mt-3')
        self.output_input = ui.input('Output folder', value=settings.output_dir) \
            .props('dense outlined').classes('w-full')
        self.output_input.on_value_change(
            lambda e: setattr(settings, 'output_dir', e.value or 'captures'))

        with ui.row().classes('w-full items-center gap-2 mt-3'):
            ui.button('Save snapshot', on_click=self.viewer.save_snapshot) \
                .props('flat dense no-caps')
            ui.button('Free running', on_click=self.free_running) \
                .props('flat dense no-caps')

        # ---- wiring
        self.colormap_select.on_value_change(
            lambda e: (setattr(settings, 'colormap', e.value), session.bump_settings()))
        self.scale_switch.on_value_change(
            lambda e: (setattr(settings, 'autoscale', bool(e.value)),
                       session.bump_settings(), self.sync()))
        self.geometry_switch.on_value_change(
            lambda e: (setattr(settings, 'use_camera_geometry', bool(e.value)),
                       self.sync()))
        self.free_run_switch.on_value_change(
            lambda e: setattr(settings, 'free_run_on_start', bool(e.value)))
        self.low_input.on_value_change(
            lambda e: (setattr(settings, 'display_low', float(e.value or 0)),
                       session.bump_settings()))
        self.high_input.on_value_change(
            lambda e: (setattr(settings, 'display_high', float(e.value or 1)),
                       session.bump_settings()))
        self.width_input.on_value_change(
            lambda e: setattr(settings, 'width', int(e.value or 640)))
        self.height_input.on_value_change(
            lambda e: setattr(settings, 'height', int(e.value or 512)))
        self.preview_fps_input.on_value_change(self.on_preview_fps)
        self.tint_input.on_value_change(
            lambda e: setattr(settings, 'target_tint',
                              float(e.value or 10) / 1e6))


        self.refresh_ports(notify=False)
        self.sync()

    # ------------------------------------------------------------ behaviour

    def on_preview_fps(self, event) -> None:
        self.session.settings.preview_fps = float(event.value or 12)
        self.viewer.set_preview_interval(self.session.settings.preview_fps)

    def refresh_ports(self, notify: bool = True) -> None:
        ports = list_ports()
        options = {p.device: p.label for p in ports}
        current = self.port_select.value
        if current in options:
            chosen = current
        elif self.session.link is not None and self.session.link.port in options:
            chosen = self.session.link.port
        elif self.session.prefer_simulator and SIM_PORT in options:
            chosen = SIM_PORT
        elif DEFAULT_PORT in options:
            chosen = DEFAULT_PORT
        else:
            chosen = next(iter(options), None)
        self.port_select.set_options(options, value=chosen)
        if notify:
            ui.notify(f'{len(ports) - 1} serial port(s) found', type='info')

    async def toggle_connection(self) -> None:
        session = self.session
        if session.connected:
            await asyncio.to_thread(session.disconnect)
            session.log('Disconnected.')
        else:
            port = self.port_select.value
            if not port:
                ui.notify('Pick a port first', type='warning')
                return
            self.connect_button.disable()
            try:
                await session.autostart(port)
            except Exception as exc:
                session.log(f'Connect failed: {exc}')
                ui.notify(str(exc), type='negative', multi_line=True)
            finally:
                self.connect_button.enable()
        session.sync_all()

    async def free_running(self) -> None:
        if not self.session.connected:
            ui.notify('Not connected', type='warning')
            return
        await self.session.enable_free_running()

    def sync(self) -> None:
        session = self.session
        settings = session.settings
        connected = session.connected
        self.connect_button.set_text('Disconnect' if connected else 'Connect')
        self.port_select.set_enabled(not connected)
        self.baud_select.set_enabled(not connected)
        self.width_input.set_enabled(not settings.use_camera_geometry)
        self.height_input.set_enabled(not settings.use_camera_geometry)
        self.low_input.set_enabled(not settings.autoscale)
        self.high_input.set_enabled(not settings.autoscale)
        if self.width_input.value != settings.width:
            self.width_input.value = settings.width
        if self.height_input.value != settings.height:
            self.height_input.value = settings.height
