import asyncio
from nicegui import ui
import numpy as np
import cv2
from PIL import Image
import io
import base64
import ctypes

class CameraViewer:
    def __init__(self, camera, serial_console):
        self.camera = camera
        self.serial_console = serial_console
        self.width = 640
        self.height = 512
        self.fps = 30.0
        self.display_image = None
        self.timer_task = None
        self.last_display_time = 0
        self.setup_ui()

    def setup_ui(self):
        with ui.expansion('Camera Viewer', icon='photo_camera').classes('w-full'):
            self.grab_button = ui.button("Start Acquisition", on_click=self.toggle_acquisition).classes('w-full')
            self.display_image = ui.interactive_image().props('no-transition no-spinner').classes('w-full')

    async def toggle_acquisition(self):
        if self.grab_button.text == "Start Acquisition":
            if not self.serial_console.connected:
                self.log_message("Camera is not connected.")
                return
            try:
                # Get latest camera settings synchronously
                self.serial_console.query_camera_settings()  # No 'await' here, assuming it's synchronous
                self.width = self.serial_console.width
                self.height = self.serial_console.height
                self.fps = self.serial_console.fps

                # Configure and start acquisition in viewer mode
                self.camera.configure_acquisition(self.width, self.height)
                self.camera.initialize_camera_context()
                self.camera.start_acquisition(mode="viewer")

                # Update button text and start asynchronous display update loop
                self.grab_button.text = "Stop Acquisition"
                self.timer_task = asyncio.create_task(self.update_display_loop())
            except RuntimeError as e:
                self.log_message(f"Error: {e}")
        else:
            # Stop acquisition and cancel the display update loop
            self.camera.stop_acquisition()
            self.grab_button.text = "Start Acquisition"
            if self.timer_task:
                self.timer_task.cancel()
                self.timer_task = None

    async def update_display_loop(self):
        """Asynchronous loop to continuously fetch and display frames."""
        try:
            while True:
                await self.update_display()
                await asyncio.sleep(1 / self.fps)  # Control refresh rate independently of camera FPS
        except asyncio.CancelledError:
            pass  # Handle the task cancellation when stopping acquisition

    async def update_display(self):
        """Fetches the latest frame from the camera ring buffer and displays it."""
        latest_frame = self.camera.get_latest_frame()
        if latest_frame is not None:
            # Convert frame data for display
            frame_data = np.ctypeslib.as_array(
                (ctypes.c_uint8 * (self.width * self.height * 2)).from_buffer(latest_frame)
            )
            image_16bit = frame_data.view(np.uint16).reshape((self.height, self.width))
            image_8bit = (image_16bit / 256).astype('uint8')
            color_mapped_image = cv2.applyColorMap(image_8bit, cv2.COLORMAP_JET)
            pil_img = Image.fromarray(color_mapped_image)

            # Update UI with new frame
            img_io = io.BytesIO()
            pil_img.save(img_io, 'PNG')
            img_io.seek(0)
            self.display_image.source = f"data:image/png;base64,{base64.b64encode(img_io.getvalue()).decode()}"

    def log_message(self, message):
        """Logs messages to the serial console's log window."""
        self.serial_console.log_message(message)