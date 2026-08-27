"""ctypes bindings for the First Light simplified USB SDK.

Every prototype is declared. Without argtypes ctypes assumes a C int for each
argument, which truncates the 64-bit camera handle on aarch64.

Override the library location with FLI_USB_SDK_LIB.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import CFUNCTYPE, POINTER, c_char_p, c_int, c_uint8, c_void_p

DEFAULT_LIB = '/opt/first_light_imaging/fliusbsdk/lib/libfliusbsdk.so'

# Error levels reported through the SDK error callback.
FLI_USB_ERROR_LEVEL_ERROR = 0x8000
FLI_USB_ERROR_LEVEL_WARNING = 0x4000
FLI_USB_ERROR_LEVEL_INFO = 0x2000

# void frame_callback(void *userctx, uint8_t *frame, int status)
FRAME_CB = CFUNCTYPE(None, c_void_p, POINTER(c_uint8), c_int)
# void error_callback(void *userctx, int error, const char *diag)
ERROR_CB = CFUNCTYPE(None, c_void_p, c_int, c_char_p)


class SdkNotFound(RuntimeError):
    """The SDK shared object is missing or will not load."""


_CACHE: dict[str, ctypes.CDLL] = {}


def library_path(explicit: str | None = None) -> str:
    return explicit or os.environ.get('FLI_USB_SDK_LIB') or DEFAULT_LIB


def load(explicit: str | None = None) -> ctypes.CDLL:
    """Load the SDK and declare every prototype.  Cached per path."""
    path = library_path(explicit)
    if path in _CACHE:
        return _CACHE[path]

    if not os.path.isfile(path):
        raise SdkNotFound(
            f'FLI USB SDK not found at {path}.\n'
            'Install the simplified USB SDK, or set FLI_USB_SDK_LIB to the '
            'full path of libfliusbsdk.so.')
    try:
        lib = ctypes.CDLL(path)
    except OSError as exc:
        raise SdkNotFound(f'Found {path} but could not load it: {exc}') from exc

    try:
        lib.fli_usb_init.argtypes = []
        lib.fli_usb_init.restype = c_int

        lib.fli_usb_exit.argtypes = []
        lib.fli_usb_exit.restype = c_int

        lib.fli_usb_detect.argtypes = []
        lib.fli_usb_detect.restype = c_int

        lib.fli_usb_open.argtypes = [c_int, ERROR_CB, c_void_p]
        lib.fli_usb_open.restype = c_void_p

        lib.fli_usb_get_associated_tty.argtypes = [c_void_p]
        lib.fli_usb_get_associated_tty.restype = c_char_p

        lib.fli_usb_checkTagEnable.argtypes = [c_void_p, c_int]
        lib.fli_usb_checkTagEnable.restype = c_int

        lib.fli_usb_startAcquisition.argtypes = [c_void_p, c_int, c_int, FRAME_CB, c_void_p]
        lib.fli_usb_startAcquisition.restype = c_int

        lib.fli_usb_stopAcquisition.argtypes = [c_void_p]
        lib.fli_usb_stopAcquisition.restype = c_int

        lib.fli_usb_close.argtypes = [c_void_p]
        lib.fli_usb_close.restype = c_int
    except AttributeError as exc:
        raise SdkNotFound(
            f'{path} loaded but a symbol is missing ({exc}). '
            'Is this the simplified USB SDK?') from exc

    _CACHE[path] = lib
    return lib


def available(explicit: str | None = None) -> tuple[bool, str]:
    """Non-raising check, for the UI banner."""
    path = library_path(explicit)
    if os.path.isfile(path):
        return True, path
    return False, path


def error_level_name(code: int) -> str:
    if code & FLI_USB_ERROR_LEVEL_ERROR:
        return 'ERROR'
    if code & FLI_USB_ERROR_LEVEL_WARNING:
        return 'WARNING'
    if code & FLI_USB_ERROR_LEVEL_INFO:
        return 'INFO'
    return 'UNKNOWN'
