"""Expose Raspberry Pi OS camera and GPIO bindings to station virtual environments.

Picamera2/libcamera and gpiozero/lgpio come from the OS; OCR lives in the venv.
Append the OS package directory so existing venv packages retain their precedence.
This does not open the camera or connect GPIO.
"""
import site
import sys
from pathlib import Path


def enable_system_camera_packages():
    if sys.platform != "linux":
        return
    system_packages = Path(sys.base_prefix) / "lib/python3/dist-packages"
    if system_packages.is_dir() and str(system_packages) not in sys.path:
        site.addsitedir(str(system_packages))
