# Excerpt of daemon/openrazer_daemon/hardware/mouse.py (OpenRazer, GPL-2.0) for tests
import re
from openrazer_daemon.hardware.device_base import RazerDeviceBrightnessSuspend as __RazerDeviceBrightnessSuspend, RazerDevice as __RazerDevice


class RazerNagaV2ProWired(__RazerDeviceBrightnessSuspend):
    """
    Class for the Razer Naga V2 Pro (Wired)
    """
    USB_VID = 0x1532
    USB_PID = 0x00A7
    METHODS = ['get_device_type_mouse', 'get_poll_rate', 'set_poll_rate']

    DPI_MAX = 30000


class RazerNagaV2ProWireless(RazerNagaV2ProWired):
    """
    Class for the Razer Naga V2 Pro (Wireless)
    """
    USB_PID = 0x00A8
    METHODS = RazerNagaV2ProWired.METHODS + ['set_charge_effect']


class RazerViperMini(__RazerDevice):
    """
    Class for the Razer Viper Mini
    """
    USB_VID = 0x1532
    USB_PID = 0x008A
    METHODS = ['get_poll_rate', 'set_poll_rate']
    POLL_RATES = [125, 1000]

    DPI_MAX = 8500
