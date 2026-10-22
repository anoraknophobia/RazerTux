#!/usr/bin/env python3
"""Record raw button activity of the Razer Naga V2 Pro (read-only, no grab).

Opens every evdev node of the mouse under /dev/input/by-id and the given
hidraw nodes, and writes one line per event: ``<t> <source> <event>``.
REL events (motion, wheel) and EV_SYN are skipped to keep the log small.
Devices are never grabbed, so the system keeps handling the input.

Usage (needs read access to the nodes, usually root):
    sudo python tools/probe_buttons.py --duration 60 --output probe.txt
"""
from __future__ import annotations

import argparse
import glob
import os
import select
import time

import evdev
from evdev import ecodes as E

BY_ID_GLOB = "/dev/input/by-id/usb-Razer_Razer_Naga_V2_Pro-*event*"
DEFAULT_HIDRAW = ["/dev/hidraw2", "/dev/hidraw3", "/dev/hidraw4"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--duration", type=float, default=60.0, help="seconds to record")
    ap.add_argument("--output", default="probe.txt", help="output file")
    ap.add_argument("--hidraw", nargs="*", default=DEFAULT_HIDRAW,
                    help="hidraw nodes to record")
    args = ap.parse_args()

    sources: dict[int, tuple[str, evdev.InputDevice | None]] = {}
    for path in sorted(glob.glob(BY_ID_GLOB)):
        dev = evdev.InputDevice(path)
        sources[dev.fd] = ("ev:" + path.split("Naga_V2_Pro-")[1], dev)
    for path in args.hidraw:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            print("skip", path, exc)
            continue
        sources[fd] = ("raw:" + path, None)

    start = time.time()
    end = start + args.duration
    with open(args.output, "w") as out:
        while time.time() < end:
            ready, _, _ = select.select(list(sources), [], [], 0.5)
            for fd in ready:
                name, dev = sources[fd]
                now = time.time() - start
                if dev is None:
                    out.write(f"{now:7.2f} {name:22} RAW {os.read(fd, 128).hex()}\n")
                    continue
                for ev in dev.read():
                    if ev.type in (E.EV_SYN, E.EV_REL):
                        continue
                    code = E.bytype[ev.type].get(ev.code, ev.code)
                    out.write(f"{now:7.2f} {name:22} {E.EV[ev.type]} {code} {ev.value}\n")
            out.flush()


if __name__ == "__main__":
    main()
