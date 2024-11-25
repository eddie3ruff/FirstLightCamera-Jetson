import ctypes
from datetime import datetime
import asyncio
from nicegui import ui

class CaptureFrames:
    def __init__(self, camera, serial_console):
        self.camera = camera
        self.serial_console = serial_console
        self.setup_ui()

    def setup_ui(self):
        with ui.expansion('Capture Frames', icon='image').classes('w-full'):
            self.frame_input = ui.number("Number of Frames", value=10).classes('w-full')
            self.capture_button = ui.button("Capture", on_click=self.start_capture).classes('w-full')

    def log_message(self, message):
        """Use SerialConsole's log for messages."""
        self.serial_console.log_message(message)
        ui.update()  # Ensure UI updates immediately after logging

    async def start_capture(self):
        if not self.serial_console.connected:
            self.log_message("Camera is not connected.")
            return

        # Run the capture process asynchronously
        await self.run_capture_process()

    async def run_capture_process(self):
        """Performs the capture process asynchronously."""
        # Query the latest camera settings
        self.serial_console.query_camera_settings()

        # Retrieve the latest width, height, and fps from SerialConsole
        width = self.serial_console.width
        height = self.serial_console.height
        fps = self.serial_console.fps

        # Configure the camera acquisition settings with the latest width and height
        self.camera.configure_acquisition(width, height)
        self.camera.initialize_camera_context()

        # Prepare the buffer for frames
        num_frames = int(self.frame_input.value)
        self.camera.acq_buffer = ctypes.create_string_buffer(width * height * 2 * num_frames)
        self.camera.idx.value = 0
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"buffer_{num_frames}frames_{width}x{height}_{fps:.2f}fps_{timestamp}.raw"
        self.log_message(f"Starting capture of {num_frames} frames to {output_file}...")

        # Start acquisition
        self.camera.start_acquisition()

        # Wait until all frames are captured asynchronously
        while self.camera.idx.value < num_frames:
            await asyncio.sleep(0.01)  # Small asynchronous delay to prevent busy-waiting

        # Stop acquisition after capture is complete
        self.camera.stop_acquisition()

        # Save data to file
        self.camera.save_data_to_file(output_file)
        self.log_message(f"Data saved to {output_file}")