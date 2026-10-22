"""Transport for Razer reports over Linux hidraw feature reports."""
import fcntl
import os
import time
from typing import Protocol

import pyudev

from .report import (
    REPORT_LEN,
    STATUS_BUSY,
    STATUS_OK,
    RazerResponse,
    parse_report,
)

RAZER_VID = "1532"
_IOC_WRITE, _IOC_READ = 1, 2
BUSY_RETRIES = 10


class RazerError(Exception):
    pass


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def HIDIOCSFEATURE(length: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, 0x06, length)


def HIDIOCGFEATURE(length: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, 0x07, length)


class Transport(Protocol):
    def query(self, report: bytes) -> RazerResponse: ...


class _FcntlIo:
    """Thin ioctl layer (replaced by a fake in tests)."""

    def set_feature(self, fd: int, buf: bytes) -> None:
        fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), bytearray(buf))

    def get_feature(self, fd: int, length: int) -> bytes:
        buf = bytearray(length)  # buf[0] = report id 0
        fcntl.ioctl(fd, HIDIOCGFEATURE(length), buf)
        return bytes(buf)


class HidrawTransport:
    def __init__(self, path: str):
        self.path = path
        self._io = _FcntlIo()
        self._fd = os.open(path, os.O_RDWR)

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def query(self, report: bytes) -> RazerResponse:
        self._io.set_feature(self._fd, b"\x00" + report)
        resp = None
        for _ in range(BUSY_RETRIES + 1):
            time.sleep(0.001)
            raw = self._io.get_feature(self._fd, REPORT_LEN + 1)
            resp = parse_report(raw[1:])
            if resp.status != STATUS_BUSY:
                break
        if resp.status != STATUS_OK:
            raise RazerError(
                f"command {resp.cmd_class:#04x}/{resp.cmd_id:#04x} "
                f"failed with status {resp.status:#04x}"
            )
        return resp


def _find_hidraw(interface: str, pid_filter) -> str | None:
    pids = {f"{p:04x}" for p in pid_filter}
    for dev in pyudev.Context().list_devices(subsystem="hidraw"):
        usb_if = dev.find_parent("usb", "usb_interface")
        usb_dev = dev.find_parent("usb", "usb_device")
        if usb_if is None or usb_dev is None:
            continue
        if (
            usb_dev.attributes.asstring("idVendor").lower() == RAZER_VID
            and usb_dev.attributes.asstring("idProduct").lower() in pids
            and usb_if.attributes.asstring("bInterfaceNumber") == interface
        ):
            return dev.device_node
    return None


def find_control_hidraw(pid_filter=(0x00A7, 0x00A8)) -> str | None:
    """Return the hidraw node of the mouse interface (interface 0), or None."""
    return _find_hidraw("00", pid_filter)


def find_vendor_hidraw(pid_filter=(0x00A7, 0x00A8)) -> str | None:
    """Return the hidraw node of interface 1 (vendor input reports: top
    buttons in driver mode), or None."""
    return _find_hidraw("01", pid_filter)
