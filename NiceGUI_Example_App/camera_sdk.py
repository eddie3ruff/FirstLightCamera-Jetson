# camera_sdk.py
import ctypes

# Load the shared library for camera SDK
fli_usb = ctypes.CDLL('/opt/first_light_imaging/fliusbsdk/lib/libfliusbsdk.so')

# Configure function return types for the camera SDK
fli_usb.fli_usb_init.restype = ctypes.c_int
fli_usb.fli_usb_exit.restype = ctypes.c_int
fli_usb.fli_usb_detect.restype = ctypes.c_int
fli_usb.fli_usb_open.restype = ctypes.c_void_p
fli_usb.fli_usb_get_associated_tty.restype = ctypes.c_char_p
fli_usb.fli_usb_checkTagEnable.restype = ctypes.c_int
fli_usb.fli_usb_startAcquisition.restype = ctypes.c_int
fli_usb.fli_usb_stopAcquisition.restype = ctypes.c_int
fli_usb.fli_usb_close.restype = ctypes.c_int