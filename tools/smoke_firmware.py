"""Read-only hardware smoke test: print firmware version response (class 0x00, id 0x81)."""
import sys

from razertux.razer.report import build_report
from razertux.razer.transport import HidrawTransport, find_control_hidraw


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else find_control_hidraw()
    if not path:
        print("no control hidraw found", file=sys.stderr)
        return 1
    with HidrawTransport(path) as t:
        resp = t.query(build_report(0x00, 0x81, b"", size=2))
    print(f"{path}: status={resp.status:#04x} size={resp.size} args={resp.args.hex()}")
    if len(resp.args) >= 2:
        print(f"firmware v{resp.args[0]}.{resp.args[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
