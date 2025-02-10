import asyncio
from nicegui import ui
import numpy as np
import cv2
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
                # Query camera settings
                self.serial_console.query_camera_settings()  # Synchronous
                self.width = self.serial_console.width
                self.height = self.serial_console.height
                self.fps = self.serial_console.fps

                # Configure and start acquisition
                self.camera.configure_acquisition(self.width, self.height)
                self.camera.initialize_camera_context()
                self.camera.start_acquisition(mode="viewer")

                # Update UI and start display loop
                self.grab_button.text = "Stop Acquisition"
                self.timer_task = asyncio.create_task(self.update_display_loop())
            except RuntimeError as e:
                self.log_message(f"Error: {e}")
        else:
            # Stop acquisition and display updates
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
                await asyncio.sleep(1 / self.fps)  # Refresh rate independent of camera FPS
        except asyncio.CancelledError:
            pass  # Graceful exit when acquisition stops

    async def update_display(self):
        """Fetches the latest frame and updates the display."""
        latest_frame = self.camera.get_latest_frame()
        if latest_frame is not None:
            try:
                # **Ensure buffer size matches expected size**
                expected_buffer_size = self.width * self.height * 2  # 16-bit (2 bytes per pixel)
                if len(latest_frame) != expected_buffer_size:
                    print(f"[ERROR] Buffer size mismatch! Expected {expected_buffer_size}, Got {len(latest_frame)}")
                    return

                # **Convert to NumPy Array (Assumes Unsigned 16-bit)**
                self.raw_frame = np.frombuffer(latest_frame, dtype=np.uint16).reshape((self.height, self.width))

                # **Convert from 14-Bit to 8-Bit using Right Shift**
                image_8bit = (self.raw_frame >> 6).astype(np.uint8)  # 14-bit to 8-bit conversion

                # **Apply Turbo colormap for accurate saturation**
                color_mapped_image = cv2.applyColorMap(image_8bit, cv2.COLORMAP_TURBO)

                # **Encode to PNG using OpenCV (Faster than PIL)**
                _, encoded_image = cv2.imencode(".png", color_mapped_image)
                img_io = io.BytesIO(encoded_image.tobytes())

                # **Update Image Source in UI**
                self.display_image.source = f"data:image/png;base64,{base64.b64encode(img_io.getvalue()).decode()}"

            except Exception as e:
                print(f"[ERROR] Exception in update_display: {e}")

    def log_message(self, message):
        """Logs messages to the serial console's log window."""
        self.serial_console.log_message(message)
