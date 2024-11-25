from nicegui import ui
from camera import Camera
from serial_console import SerialConsole
from capture_frames import CaptureFrames
from camera_viewer import CameraViewer

# Initialize the main application layout
with ui.row().classes('w-full justify-center items-center no-wrap'):
    with ui.column().classes('md:w-1/2 w-full'):
        # Create instances of each component
        camera = Camera()  
        serial_console = SerialConsole() 
        capture_frames = CaptureFrames(camera, serial_console) 
        camera_viewer = CameraViewer(camera, serial_console)

# Run NiceGUI
ui.run()