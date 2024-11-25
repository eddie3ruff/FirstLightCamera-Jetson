import ctypes
from ctypes import POINTER, c_uint8, c_int, c_void_p
from camera_sdk import fli_usb

# Constants for error levels
FLI_USB_ERROR_LEVEL_ERROR = 0x8000
FLI_USB_ERROR_LEVEL_WARNING = 0x4000
FLI_USB_ERROR_LEVEL_INFO = 0x2000

class Camera:
    RING_BUFFER_SIZE = 10  # Size of the viewer ring buffer

    def __init__(self):
        self.cam_ctx = None
        self.sdk_initialized = False
        self.acq_buffer = None
        self.idx = ctypes.c_int(0)
        self.width = 640  # Default width
        self.height = 512  # Default height
        
        # Ring buffer for live viewer frames
        self.viewer_ring_buffer = [None] * self.RING_BUFFER_SIZE
        self.buffer_index = 0

    def initialize_camera_context(self):
        """Initializes SDK and opens the camera context."""
        if not self.sdk_initialized:
            if fli_usb.fli_usb_init() != 1:
                raise RuntimeError("Failed to initialize SDK.")
            self.sdk_initialized = True
            print("SDK initialized successfully.")

        if self.cam_ctx is not None:
            print("Camera context already initialized.")
            return

        nb_cam = fli_usb.fli_usb_detect()
        if nb_cam <= 0:
            raise RuntimeError("No cameras detected")

        cam_ctx = fli_usb.fli_usb_open(0, error_callback, None)
        self.cam_ctx = ctypes.c_void_p(cam_ctx)
        if not self.cam_ctx:
            raise RuntimeError("Failed to open camera")
        print(f"Camera opened successfully, cam_ctx: {hex(self.cam_ctx.value)}")

        tty_name = fli_usb.fli_usb_get_associated_tty(self.cam_ctx)
        print(f"Associated TTY: {tty_name.decode() if tty_name else 'None'}")

    def configure_acquisition(self, width, height):
        """Sets the width and height before starting acquisition."""
        self.width = width
        self.height = height

    def start_acquisition(self, mode="record"):
        """Starts camera acquisition using the specified mode: 'record' or 'viewer'."""
        if not self.cam_ctx:
            raise RuntimeError("Invalid camera context.")

        if fli_usb.fli_usb_checkTagEnable(self.cam_ctx, 1) != 1:
            raise RuntimeError("Failed to enable tag checking.")

        # Choose the correct callback based on mode
        callback = viewer_callback if mode == "viewer" else data_callback
        if fli_usb.fli_usb_startAcquisition(self.cam_ctx, self.width, self.height, callback, ctypes.py_object(self)) != 1:
            raise RuntimeError("Failed to start acquisition.")
        print("Camera acquisition started.")

    def stop_acquisition(self):
        """Stops camera acquisition."""
        if self.cam_ctx and fli_usb.fli_usb_stopAcquisition(self.cam_ctx) != 1:
            print("Failed to stop acquisition.")
        else:
            print("Camera acquisition stopped.")

    def get_latest_frame(self):
        """Returns the latest frame in the ring buffer for live viewing."""
        latest_frame = self.viewer_ring_buffer[(self.buffer_index - 1) % self.RING_BUFFER_SIZE]
        return latest_frame
    
    def save_data_to_file(self, output_file):
        """Saves acquisition buffer to a file."""
        with open(output_file, "wb") as outfile:
            outfile.write(self.acq_buffer.raw)
        print(f"Data saved to {output_file}")

@ctypes.CFUNCTYPE(None, c_void_p, POINTER(c_uint8), c_int)
def viewer_callback(userctx, frame, status):
    """Callback to store frames in the viewer ring buffer."""
    camera = ctypes.cast(userctx, ctypes.py_object).value
    frame_size = camera.width * camera.height * 2

    # Allocate space for frame data if not already allocated
    if camera.viewer_ring_buffer[camera.buffer_index] is None:
        camera.viewer_ring_buffer[camera.buffer_index] = ctypes.create_string_buffer(frame_size)

    # Copy the frame data into the ring buffer slot
    ctypes.memmove(camera.viewer_ring_buffer[camera.buffer_index], frame, frame_size)
    camera.buffer_index = (camera.buffer_index + 1) % camera.RING_BUFFER_SIZE

@ctypes.CFUNCTYPE(None, c_void_p, POINTER(c_uint8), c_int)
def data_callback(userctx, frame, status):
    """Callback to process frame data during acquisition for recording."""
    camera = ctypes.cast(userctx, ctypes.py_object).value
    if camera.idx.value < int(camera.acq_buffer._length_ / (camera.width * camera.height * 2)):
        offset = camera.idx.value * camera.width * camera.height * 2
        ctypes.memmove(ctypes.byref(camera.acq_buffer, offset), frame, camera.width * camera.height * 2)
        camera.idx.value += 1

@ctypes.CFUNCTYPE(None, c_void_p, c_int, ctypes.c_char_p)
def error_callback(userctx, error, diag):
    """Error callback function for SDK."""
    if error & FLI_USB_ERROR_LEVEL_ERROR:
        level = "Critical error"
    elif error & FLI_USB_ERROR_LEVEL_WARNING:
        level = "Warning"
    elif error & FLI_USB_ERROR_LEVEL_INFO:
        level = "Info"
    else:
        level = "Unknown"
    print(f"{level}: {diag.decode()}")