from naga_config.gui.dbus_client import DaemonUnavailable


class FakeTransport:
    def __init__(self):
        self.up = True
        self.calls = []
        self.on_signal = None
        self.on_owner = None

    def call(self, member, signature="", args=()):
        self.calls.append((member, tuple(args)))
        if not self.up:
            raise DaemonUnavailable("weg")
        return {
            "ListProfiles": [[("default", "Standard"), ("p2", "Zwei")]],
            "GetActiveProfile": ["p2"],
            "GetStatus": [{"connected": True, "battery": 80}],
            "RestoreOriginal": [["dpi"]],
            "Reload": [], "ActivateProfile": [],
        }[member]

    def start(self):
        pass
