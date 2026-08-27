# C-RED Camera Control

A small web app for driving a First Light Imaging C-RED camera from a Jetson
over the simplified USB SDK: live preview, a serial console, a visual crop
picker, and recording to `.raw`.

The Jetson is usually headless, so the app serves a page on the network and you
drive it from a laptop or a phone.

<!-- Add screenshots to images/ and reference them here. -->

---

## Requirements

- **Python 3.10 or newer** (NiceGUI 3.x needs it; JetPack 6 ships 3.10)
- The First Light simplified USB SDK at
  `/opt/first_light_imaging/fliusbsdk/lib/libfliusbsdk.so`
  (or set `FLI_USB_SDK_LIB`)

```bash
pip3 install -r requirements.txt
```

`nicegui`, `numpy` and `pyserial` are required. The preview is encoded by a
small built-in PNG writer, so Pillow is optional and OpenCV is not needed.

### Permissions

The SDK needs access to the USB device and the serial port. A udev rule avoids
running the whole app as root:

```bash
lsusb                                   # find the camera's vendor ID
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="XXXX", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/99-fli-camera.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG dialout $USER          # for /dev/ttyACM0
```

Log out and back in. If you would rather use `sudo python3 main.py`, install
the dependencies system-wide (`sudo pip3 install -r requirements.txt`) - root
does not see packages installed with `pip3 install --user`.

### High frame rates

Raise the USB buffer or you will drop frames:

```bash
sudo sh -c 'echo 1000 > /sys/module/usbcore/parameters/usbfs_memory_mb'
```

Add `usbcore.usbfs_memory_mb=1000` to the kernel command line to make it stick.

Check what the camera negotiated, and what it shares the bus with:

```bash
lsusb -t
```

A C-RED links at `5000M` - USB 3.0, about 425 MB/s of realistic bulk
throughput. Full frame at 595 fps is 390 MB/s, roughly 92% of that; a 64x64
window at 9523 fps is 78 MB/s, about 18%. Occasional dropped frames at full
frame and none on a small window is the bandwidth ceiling, not the frame rate.
Put the camera on a direct port rather than behind a hub, especially a hub
shared with another streaming device.

---

## Running

```bash
python3 main.py
```

It connects, puts the camera in free-running mode, reads its geometry, and
starts streaming, then prints its address:

```
NiceGUI ready to go on http://192.168.1.202:8080
```

| Flag | Meaning |
| --- | --- |
| `--sim` | synthetic frames, no camera needed |
| `--no-auto` | do not connect or start acquisition on launch |
| `--port 8080` | web server port |
| `--host 127.0.0.1` | restrict to the Jetson itself |
| `--sdk <path>` | path to `libfliusbsdk.so` |
| `--show` | open a local browser (only with a display attached) |
| `--reload` | restart on source changes |
| `--verbose` | also print the SDK's INFO messages |

Ctrl-C stops it cleanly. Without a camera, `--sim` gives you the whole app -
synthetic frames and a mock `fli-cli` - which is the easiest way to learn it.

---

## The interface

The screen is the image, a readout (`64x64  9523 fps  tint 10 us`), and
**Start/Stop** and **Record**. The dot in the header is grey when disconnected,
amber when idle, green when streaming.

Three disclosures hold everything else:

**Crop** - pick a window size and drag it where you want it. The grid is the
camera's windowing granularity, so a window you can draw is one the camera will
accept. **Apply** sends it; dragging alone does not talk to the camera.

**Console** - any `fli-cli` command. Enter sends, Up/Down walks history.
Multi-line replies keep their column alignment, so `help` is readable.

**Settings** - port and baud, colormap, preview rate, display range, target
integration time, output folder.

---

## What Apply does

Four steps, and the order matters:

```
set cropping columns 288-351     # define the window
set cropping rows 224-287
set cropping on                  # enable it - without this the camera
                                 # stays on the full frame
set fps <maximum>                # discover the real ceiling, then round
set tint 0.000010000             # last: fps resets tint
```

- **The window needs `set cropping on`.** Setting the ranges alone defines it
  without enabling it, which looks exactly like the crop being ignored. Full
  frame is `set cropping off`. A combined `set cropping columns A-B rows C-D`,
  and the `on:A-B:C-D` form the getter reports, are both syntax errors.
- **The frame rate comes from a table, not from the camera.** Readout time
  scales with the number of rows, so each doubling of the window doubles the
  period:

  | Window | Frame period | Command |
  | --- | --- | --- |
  | 64x64 | 105 us | `set fps 9523` |
  | 128x128 | 210 us | `set fps 4761` |
  | 256x256 | 420 us | `set fps 2380` |
  | 512x512 | 840 us | `set fps 1190` |
  | 640x512 full frame | 1680 us | `set fps 595` |

  The camera's own `maxfps` and `maxfpsusb` both report higher than the sensor
  sustains - at 64x64 they say 10,880 and 9,999 against a real 9,523 - and
  asking for more than the sensor can read out produces frames whose tags do
  not line up rather than a refused command. The table is in
  `FRAME_RATE` in `serial_console.py`.

- **Tint goes last.** Setting the frame rate makes the camera re-set the
  integration time to about the new frame period, so a tint written earlier is
  overwritten. It is also restored after any `set fps` you type into the
  console.

Window origins are snapped to the camera's granularity: **columns to 32, rows
to 4**.

---

## Recording

Set a frame count and press **Record**. A recording gets the camera to itself:
whatever is running is stopped, a fresh acquisition starts with tag checking on
and the preview paused, exactly the requested number of frames are captured,
and acquisition stops before the file is written. The preview comes back
afterwards if it was running.

Nothing competes with the capture that way - no frames being copied into the
preview ring, no PNG encoding, no websocket traffic - which is the difference
between a clean burst and one that reports dropped frames. The burst is
buffered in RAM before it is written, so the size is shown while you choose.

Each capture prints a block to the terminal and writes a `.json` sidecar:

```
------------------------------------------------------------------
RECORDING  2026-08-26T16:56:21
  file        capture_500f_64x64_9523.00fps_20260826_165621.raw
  geometry    64 x 64  (8,192 bytes/frame)
  frames      500
  frame rate  9,523.000 fps requested, 9,511.2 measured
  duration    0.053 s
  tint        10.0 us
  size        4.1 MB
  tag check   clean - no dropped frames
  sidecar     capture_500f_64x64_9523.00fps_20260826_165621.json
  source      fli-usb-sdk
------------------------------------------------------------------
```

Pipe it to keep a log:

```bash
python3 main.py | tee -a ~/acquisitions.log
```

### Tag checking

The camera embeds a frame counter in every frame, and
`fli_usb_checkTagEnable` decides whether the SDK validates it. Validation is
what produces `Invalid tag detected`, one message per suspect frame.

Viewing runs with the check **off** - a preview that misses a frame does not
matter, and at high frame rates the messages are pure noise. Recording runs
with it **on**, and the log reports the SDK's verdict when the burst finishes.
That verdict comes from the SDK, so it does not depend on knowing where in the
frame the tag sits.

### The .raw format

Little-endian `uint16`, frames stacked in order, each frame `height` rows of
`width` pixels, row-major, no header or padding. The camera fills the low 14
bits.

```python
from rawio import load_raw

frames = load_raw('capture.raw')     # (frames, height, width), memory-mapped
print(frames.shape, frames[0].mean())
```

Geometry comes from the sidecar, so nothing has to be reverse-engineered from
the filename.

---

## Command-line tools

```bash
python3 serialCOM.py                       # interactive fli-cli console
python3 serialCOM.py --list                # list serial ports
python3 acquire.py -N 100 out.raw          # geometry read from the camera
python3 acquire.py -W 64 -H 64 -N 100 out.raw
python3 read_raw.py out.raw --stats 5 --tags --png frame0.png
```

All of them take `--sim`.

---

## Trigger mode

The camera keeps its trigger mode across sessions, so an application that left
it in external synchro hands you a camera that opens and starts acquisition
without complaint and then delivers nothing - it is waiting for a trigger edge.

The app logs `extsynchro` and `swsynchro` on connect and turns both off before
acquiring. Switch **Free-run on start** off in Settings if you are driving the
camera with an external trigger.

---

## Files

| File | Role |
| --- | --- |
| `main.py` | arguments, page layout, `ui.run()` |
| `session.py` | shared state: camera, serial link, log, settings |
| `camera_sdk.py` | ctypes bindings |
| `camera.py` | frame buffer, recording, SDK and simulated cameras |
| `serial_console.py` | serial link, command formats, response parsing, console |
| `camera_viewer.py` | live preview |
| `crop_panel.py` | visual crop picker |
| `capture_frames.py` | record to `.raw` |
| `settings_panel.py` | the Settings disclosure |
| `imaging.py` | 16-bit scaling, colormaps, PNG encode |
| `rawio.py` | raw writing and loading, JSON sidecar |
| `simulator.py` | synthetic frames and a mock fli-cli |

`simulator.py` exists so the app can be used and developed without a camera.
Delete it and `SimulatedCamera` if you want the app stripped to hardware only.

Two things are worth preserving if you extend this: the acquisition callback
should do as little as possible - it runs on the SDK's thread holding the GIL,
every 105 us at full speed - and the SDK should be torn down as little as
possible. `fli_usb_exit()` is never called: it destroys libusb's context while
the SDK's threads may still be using it.

---

## Troubleshooting

**"FLI USB SDK not found"** - set `FLI_USB_SDK_LIB`, or use `--sim`.

**No serial ports** - check `/dev/ttyACM0` exists and that you are in the
`dialout` group.

**`fli_usb_startAcquisition` fails** - the geometry must match the camera's
cropping. Leave *From camera* on in Settings.

**Crop seems ignored** - the console log shows every command and reply; look
for whether `set cropping on` was accepted.

**Commands time out** - the console waits for the `fli-cli>` prompt and gives
up after 2 seconds. Check the baud rate (115200) and that nothing else holds
the port.

**`Invalid tag detected`** - the SDK received a frame whose counter was not
what it expected. Recording reports a count when the burst ends; that is the
number that matters.

**Dropped frames only on large windows** - compare the bandwidth, not the
frame rate. `width x height x 2 x fps` against roughly 425 MB/s for a USB 3.0
link. Full frame at full rate is close enough to the ceiling that a hub or a
second device on the same bus will push it over. Recording full frame at a
lower rate is the reliable fix.

If a recording is clean the first few times and then degrades - rising error
counts, `Unable to submit`, eventually a crash - the SDK is not recovering
between acquisitions. `SETTLE_SECONDS` in `camera.py` is how long the app waits
after `fli_usb_stopAcquisition` before anything starts again; raise it if the
pattern persists. `sudo python3 acquire.py -N 1000 out.raw` runs one capture in
a process with no web app in it, which tells you whether the app is involved at
all.
