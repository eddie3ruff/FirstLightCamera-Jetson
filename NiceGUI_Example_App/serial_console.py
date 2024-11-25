from nicegui import ui
import serial
import time
import re

class SerialConsole:
    def __init__(self):
        self.ser = None
        self.connected = False
        self.last_response = ""
        self.width = 640   # Default width
        self.height = 512  # Default height
        self.fps = 30.0    # Default FPS
        self.setup_ui()

    def setup_ui(self):
        with ui.expansion('Serial Console', icon='terminal').classes('w-full'):
            self.command_input = ui.input('Enter Command').classes('w-full')
            self.command_input.on('keydown.enter', self.send_command)  # Trigger on Enter
            self.log = ui.log().classes('w-full')
            self.connect_button = ui.button('Connect Serial', on_click=self.toggle_connection).classes('w-full')

    def log_message(self, message: str):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        self.log.push(f"[{timestamp}] {message}")

    def toggle_connection(self):
        if self.connected:
            self.disconnect_serial()
        else:
            self.connect_serial()

    def connect_serial(self):
        try:
            self.ser = serial.Serial(
                port='/dev/ttyACM0',  # Replace with your actual serial port if different
                baudrate=115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False
            )
            if self.ser.is_open:
                self.connected = True
                self.log_message(f"Connected to {self.ser.port} at {self.ser.baudrate} baud.")
                self.connect_button.set_text('Disconnect Serial')
        except serial.SerialException as e:
            self.log_message(f"Serial error: {e}")

    def disconnect_serial(self):
        if self.ser and self.ser.is_open:
            self.ser.close()
            self.connected = False
            self.log_message("Serial connection closed.")
            self.connect_button.set_text('Connect Serial')

    def send_command(self, event=None):
        """Send a command to the serial device and log the response."""
        if not self.connected:
            self.log_message("Serial is not connected.")
            return

        command = self.command_input.value.strip()
        if not command:
            self.log_message("Please enter a command.")
            return
        if command.lower() == 'exit':
            self.disconnect_serial()
            return

        try:
            # Send the command
            self.ser.write((command + '\r\n').encode())
            self.log_message(f"Sent: {command}")

            # Read the response
            full_response = ""
            while True:
                line = self.ser.readline().decode().strip()
                if line:
                    full_response += line + " "
                if "fli-cli>" in line:  # Stop reading after detecting prompt
                    break

            # Clean up the response by removing duplicate prompts
            cleaned_response = re.sub(r"(fli-cli>\s*)+", "fli-cli>", full_response).replace("fli-cli>", "").strip()
            self.last_response = cleaned_response
            self.log_message(f"Received: {self.last_response}")

        except Exception as e:
            self.log_message(f"Error sending command: {e}")
        finally:
            # Clear input field after sending to reset for next input
            self.command_input.value = ''

    def query_camera_settings(self):
        """Queries the camera to update width, height, and fps."""
        self.send_command_with_callback("cropping raw", self.update_dimensions)
        self.send_command_with_callback("fps raw", self.update_fps)

    def send_command_with_callback(self, command, callback):
        """Helper to send a command and execute a callback on the response."""
        self.command_input.value = command
        self.send_command()
        callback()

    def update_dimensions(self):
        """Update width and height based on the last response and cropping state."""
        response = self.last_response

        # Check if cropping is on or off
        if response.startswith("on"):
            # If cropping is on, extract the cropping dimensions
            match = re.search(r"on:(\d+)-(\d+):(\d+)-(\d+)", response)
            if match:
                self.width = int(match.group(2)) - int(match.group(1)) + 1
                self.height = int(match.group(4)) - int(match.group(3)) + 1
                self.log_message(f"Cropping ON: Width: {self.width}, Height: {self.height} obtained from camera")
            else:
                # Default to full frame if parsing fails (this shouldn't usually happen)
                self.width, self.height = 640, 512
                self.log_message("Cropping ON but failed to parse dimensions, defaulting to 640x512.")
        elif response.startswith("off"):
            # If cropping is off, set to the default full frame dimensions
            self.width, self.height = 640, 512
            self.log_message("Cropping OFF: Full frame dimensions set to 640x512.")
        else:
            # In case of an unexpected response format
            self.log_message("Unexpected response format. Unable to determine cropping state.")

    def update_fps(self):
        """Update FPS based on last response."""
        response = self.last_response
        match = re.search(r"([\d.]+)", response)
        if match:
            self.fps = float(match.group(1))
            self.log_message(f"FPS: {self.fps} obtained from camera")