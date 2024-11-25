import ctypes
import time
import argparse

# Load the shared library
fli_usb = ctypes.CDLL('/opt/first_light_imaging/fliusbsdk/lib/libfliusbsdk.so')

# Define function return types for consistency
fli_usb.fli_usb_open.restype = ctypes.c_void_p  # cam_ctx should be treated as a pointer
fli_usb.fli_usb_get_associated_tty.restype = ctypes.c_char_p  # TTY name should be a C string

# Constants for error levels (from the SDK documentation)
FLI_USB_ERROR_LEVEL_ERROR = 0x8000
FLI_USB_ERROR_LEVEL_WARNING = 0x4000
FLI_USB_ERROR_LEVEL_INFO = 0x2000

# Define error and data callback functions
@ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_char_p)
def error_callback(userctx, error, diag):
    # Determine the message type based on error level
    if error & FLI_USB_ERROR_LEVEL_ERROR:
        message_type = "Critical error"
    elif error & FLI_USB_ERROR_LEVEL_WARNING:
        message_type = "Warning"
    elif error & FLI_USB_ERROR_LEVEL_INFO:
        message_type = "Informative message"
    else:
        message_type = "Unknown message type"

    print(f"{message_type}\nInfo message code {error} - diag {diag.decode()}")

@ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint8), ctypes.c_int)
def data_callback(userctx, frame, status):
    if idx.value < count:
        offset = idx.value * width * height * 2
        ctypes.memmove(ctypes.byref(acq_buffer, offset), frame, width * height * 2)
        idx.value += 1

# Argument parser for command-line inputs
parser = argparse.ArgumentParser(description="Acquire images from a First Light Imaging USB camera.")
parser.add_argument("-W", "--width", type=int, default=640, help="Width of the image (default: 640)")
parser.add_argument("-H", "--height", type=int, default=512, help="Height of the image (default: 512)")
parser.add_argument("-N", "--frames", type=int, default=400, help="Number of frames to capture (default: 400)")
parser.add_argument("output", type=str, help="Output file to save image data")

args = parser.parse_args()
width = args.width
height = args.height
count = args.frames
output_file = args.output

# Allocate the buffer and set frame index
acq_buffer = ctypes.create_string_buffer(width * height * 2 * count)
idx = ctypes.c_int(0)

# Initialize the SDK and detect cameras
if fli_usb.fli_usb_init() == 1:
    nb_cam = fli_usb.fli_usb_detect()
    if nb_cam > 0:
        print(f"{nb_cam} camera(s) detected")

        # Open the first camera and check if cam_ctx is valid
        cam_ctx = fli_usb.fli_usb_open(0, error_callback, None)
        cam_ctx = ctypes.c_void_p(cam_ctx)  # Cast for consistent handling

        if cam_ctx:
            print(f"Camera opened successfully, cam_ctx: {hex(cam_ctx.value)}")

            # Get the associated TTY name to verify cam_ctx validity
            tty_name = fli_usb.fli_usb_get_associated_tty(cam_ctx)
            print(f"Associated TTY: {tty_name.decode() if tty_name else 'None'}")

            if not tty_name:
                print("Error: TTY name is not assigned. The camera context may be incomplete.")
            else:
                # Enable tag checking and start acquisition if TTY is valid
                if fli_usb.fli_usb_checkTagEnable(cam_ctx, 1) == 1:
                    print("Tag checking enabled")

                    if fli_usb.fli_usb_startAcquisition(cam_ctx, width, height, data_callback, None) == 1:
                        print("Acquisition started...")

                        # Wait until all frames are received
                        while idx.value < count:
                            time.sleep(1)  # Sleep for a short time to avoid busy-waiting

                        # Stop acquisition
                        if fli_usb.fli_usb_stopAcquisition(cam_ctx) == 1:
                            print("Acquisition stopped successfully")
                        else:
                            print("Failed to stop acquisition")

                        # Save data to file
                        with open(output_file, "wb") as outfile:
                            outfile.write(acq_buffer.raw)
                        print(f"Data saved to {output_file}")
                    else:
                        print("Failed to start acquisition - cam_ctx may be invalid.")
                else:
                    print("Failed to enable tag checking - cam_ctx might be invalid or improperly initialized")

            # Close camera and SDK, ensuring cam_ctx is valid
            fli_usb.fli_usb_close(cam_ctx)
        else:
            print("Failed to open camera")
    else:
        print("No cameras detected")

    # Final SDK cleanup
    fli_usb.fli_usb_exit()
else:
    print("Failed to initialize the USB SDK")