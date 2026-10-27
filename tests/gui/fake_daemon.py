from razertux.gui.dbus_client import DaemonUnavailable

NAGA, DA = "naga_v2_pro", "death_adder_v2"


class FakeTransport:
    """Daemon1 stand-in: one connected Naga; add_second_device() adds a DeathAdder."""

    def __init__(self):
        self.up = True
        self.calls = []
        self.on_signal = None
        self.on_owner = None
        self.restore_failed = ["dpi_stages"]
        self.version = "1"
        self.devices = [[NAGA, "Razer Naga V2 Pro", "wireless", True]]
        self.profiles = {NAGA: [("default", "Standard"), ("p2", "Zwei")],
                         DA: [("default", "Standard"), ("dpi", "Präzision")]}
        self.active = {NAGA: "p2", DA: "dpi"}
        self.statuses = {NAGA: {"connected": True, "battery": 80},
                         DA: {"connected": True, "battery": 55}}

    def add_second_device(self, connected=True):
        self.devices.append([DA, "Razer DeathAdder V2", "wired", connected])

    def call(self, member, signature="", args=()):
        self.calls.append((member, tuple(args)))
        if not self.up:
            raise DaemonUnavailable("weg")
        model = args[0] if args else NAGA
        return {
            "GetVersion": lambda: [self.version],
            "ListDevices": lambda: [[list(d) for d in self.devices]],
            "ListProfiles": lambda: [list(self.profiles[model])],
            "GetActiveProfile": lambda: [self.active[model]],
            "GetStatus": lambda: [self.statuses[model]],
            "RestoreOriginal": lambda: [list(self.restore_failed)],
            "GetDiagnostics": lambda: ["probe ok"],
            "Reload": lambda: [], "ActivateProfile": lambda: [],
            "StartLearning": lambda: [], "StopLearning": lambda: [],
            "PauseDevice": lambda: [], "ResumeDevice": lambda: [],
        }[member]()

    def start(self):
        pass
