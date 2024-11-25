
# C-RED-2 LITE on Jetson Orin AGX using Python, 64 x 64 @ 9500Hz

This repository contains some basic tools for interacting with and controlling First Light Imaging (FLI) cameras in Python on the Jetson Orin AGX using the simplified USB SDK for the camera. It includes command-line scripts for saving a raw file and serial communication. There is also an easy-to-use, web-based GUI where you can do serial commands, save frames to a RAW file, and a live preview. This works with a headless Jetson configuration so long as you are on the same local network.

## Table of Contents

- [Overview](#overview)
- [Components](#components)
- [Installation](#installation)
- [Usage](#usage)
  - [1. Image Acquisition](#1-image-acquisition)
  - [2. Serial Command Interface](#2-serial-command-interface)
  - [3. NiceGUI Camera Control App](#3-nicegui-camera-control-app)
- [Dependencies](#dependencies)
- [License](#license)
- [Troubleshooting](#troubleshooting)

---

## Overview

This project allows users to do the following in Python:
1. Acquire and save raw image frames using the FLI USB SDK on a Jetson.
2. Communicate with FLI cameras via serial commands.
3. Control the camera using a web-based interface with NiceGUI.

## Components

### 1. `acquire_images.py`
A script to acquire image frames from an FLI USB camera using the FLI USB SDK. Frames are saved as raw binary files.

### 2. `serialCOM.py`
A command-line interface for sending and receiving serial commands to/from the FLI camera.

### 3. NiceGUI Camera Control
A web-based GUI for interacting with the camera. Built using the NiceGUI framework, this app allows users to control camera settings and visualize outputs in real-time.

---

## Installation

### 1. Install the Simplified USB SDK provided by First Light onto the Jetson

### 2. Install Python Dependencies

### 3. Configure the Jetson USBFS for 1000MB instead of the default 16MB. This ensures no dropped frames at high framerates. Learn more here. [Optimizing Performance on NVIDIA Jetson - Application Note](https://cdn.alliedvision.com/fileadmin/content/documents/products/software/software/embedded/Optimizing-Performance-Jetson_app

---

## Usage

### 1. Image Acquisition
Use the `acquire.py` script to capture raw image frames from the camera. Be sure the camera cropping configuration matches your image size.

#### Run the Script
```bash
sudo python3 acquire.py -W <width> -H <height> -N <frames> <output>
```

#### Example
```bash
sudo python3 acquire.py -W 640 -H 512 -N 100 output.raw
```

- Captures 100 frames of 640x512 resolution and saves them to `output.raw`.

### Example Output
```plaintext
1 camera(s) detected
Camera opened successfully, cam_ctx: 0xaaaadba6fe60
Associated TTY: /dev/ttyACM0
Tag checking enabled
Informative message
Info message code 8192 - diag Producer thread starting...
Informative message
Info message code 8192 - diag Consumer thread starting...
Acquisition started...
Informative message
Info message code 8192 - diag Producer thread stopping...
Informative message
Info message code 8192 - diag Consumer thread stopping...
Acquisition stopped successfully
Data saved to output.raw
```

---

### 2. Serial Command Interface
The `serialCOM.py` script enables communication with the camera via serial commands.

#### Run the Script
```bash
sudo python3 serialCOM.py
```

#### Interaction Example
```plaintext
Connected to /dev/ttyACM0 at 115200 baud.
Enter command to send (type 'exit' to quit): fps
Sent: fps
Received: Frames per second: 602.673549000
Enter command to send (type 'exit' to quit): cropping
Sent: cropping
Received: Cropping off: columns: 0-639 rows:    0-511
Enter command to send (type 'exit' to quit): exit
Exiting program.
Serial connection closed.
```

---

### 3. NiceGUI Camera Control App

#### Start the Server
```bash
sudo python3 main.py
```

#### Access the GUI
Copy/paste the local host IP address provided in the terminal by NiceGUI into your browser. You may need to add an 's' to http. For example, https://192.168.1.202:8080/

[INSERT IMAGE OF GUI]

#### Features
- Serial communication.
- Saving frames to a RAW file.
- Live preview.

---

## Dependencies

- **Python Libraries**:
  - `ctypes`
  - `time`
  - `argparse`
  - `pyserial`
  - `nicegui`
  
- **FLI USB SDK**:
  - The `libfliusbsdk.so` shared library must be installed and accessible.

---

## License

This project is provided as-is. Please ensure compliance with the First Light Imaging USB SDK license and the licenses of any third-party dependencies used.

---

## Troubleshooting

### Common Issues

1. **No Cameras Detected**:
   - Ensure the camera is connected and powered on.
   - Verify the USB SDK is installed correctly.

2. **Serial Connection Errors**:
   - Ensure the serial port is correct (`/dev/ttyACM0` by default).
   - Verify device permissions.

---
