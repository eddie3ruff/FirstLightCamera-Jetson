
# First Light Imaging Camera Control Suite for Jetson Orin using Python

This repository contains some basic tools for interacting with and controlling First Light Imaging (FLI) cameras in Python on the Jetson Orin AGX using the simplified USB SDK for the camera. It includes command-line scripts for saving a raw file and serial communication. There is also a web-based GUI built with NiceGUI where you can do serial commands, save to raw, and live preview. This works with a headless Jetson configuration so long as you are on the same local network.

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

### 2. `serial_command_interface.py`
A command-line interface for sending and receiving serial commands to/from the FLI camera.

### 3. NiceGUI Camera Control
A web-based GUI for interacting with the camera. Built using the NiceGUI framework, this app allows users to control camera settings and visualize outputs in real-time.

---

## Installation

### 1. Install the Simplified USB SDK

### 2. Install Python Dependencies

### 3. Configure the Jetson USBFS for 1000MB instead of the default 16MB. 

---

## Usage

### 1. Image Acquisition
Use the `acquire_images.py` script to capture raw image frames from the camera.

#### Run the Script
```bash
python acquire_images.py -W <width> -H <height> -N <frames> <output>
```

#### Example
```bash
python acquire_images.py -W 640 -H 512 -N 100 output.raw
```

- Captures 100 frames of 640x512 resolution and saves them to `output.raw`.

---

### 2. Serial Command Interface
The `serial_command_interface.py` script enables communication with the camera via serial commands.

#### Run the Script
```bash
python serial_command_interface.py
```

#### Interaction Example
```plaintext
Connected to /dev/ttyACM0 at 115200 baud.
Enter command to send (type 'exit' to quit): fps raw
Sent: fps raw
Received: 600.01
Enter command to send (type 'exit' to quit): exit
Exiting program.
Serial connection closed.
```

---

### 3. NiceGUI Camera Control App

#### Start the Server
```bash
python main.py
```

#### Access the GUI
Copy/paste the local host IP address into your browser. You may need to add an 's' to http. 


#### Features
- Serial Communication.
- Saving a number of frames to a RAW file.
- Live Preview.

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
