# naga-config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein einziges Linux-Programm (Dienst + Qt-Fenster + Tray), das für die Razer Naga V2 Pro Synapse und OpenRazer ersetzt: Tastenbelegung mit Makros/Hypershift/Profilen, DPI, Beleuchtung, Akku, Mausrad-Rasterung.

**Architecture:** Ein Python-Hintergrunddienst (systemd-User-Service) greift die evdev-Geräte der Naga exklusiv ab, übersetzt Ereignisse über eine reine Mapping-Engine und gibt sie über ein uinput-Gerät aus; er ist die einzige Komponente, die per hidraw (90-Byte-Razer-Feature-Reports) mit der Maus spricht. GUI und Tray (PySide6) bearbeiten JSON-Konfigurationsdateien und steuern den Dienst über D-Bus (Session-Bus).

**Tech Stack:** Python 3.14, python-evdev 2.0, PySide6 6.12, python-pyudev, python-dbus-next, pytest + pytest-asyncio. Alles als CachyOS-Pakete (pacman).

**Spec:** `/home/anoraknophobia/REA/docs/superpowers/specs/2026-10-22-naga-config-design.md`

## Global Constraints

- Gerät: Vendor `0x1532`, PID `0x00A7` (Kabel), `0x00A8` (Funk-Dongle). Razer-Reports: 90 Byte, Transaktions-ID `0x1F`, CRC = XOR der Bytes 2..87.
- Geräte-Schreibbefehle mit Speicher-Flag **`NOSTORE` (0x00)**, nie `VARSTORE`, außer ein Befehl kennt kein Flag; dann vorherigen Wert sichern.
- **Vor dem ersten schreibenden Befehl an der echten Maus muss die Sicherung (Task 2) existieren**: `~/.config/naga-config/mouse-backup.json`.
- Unbekannte Befehle (Mausrad) zuerst nur lesend testen.
- Profilwechsel nur manuell (GUI/Tray). Kein Auto-Wechsel, kein Wechsel per Maustaste.
- Makro-Aufnahme nur über Qt-Ereignisse im eigenen Fenster; udev-Regel nur für Naga-Geräte + `/dev/uinput`.
- Kein Root zur Laufzeit. Keine Razer-Bilder/-Assets im Projekt.
- Konfiguration in `~/.config/naga-config/` (`settings.json`, `macros.json`, `profiles/<id>.json`).
- Oberfläche Deutsch; Strings über `self.tr()` / `QCoreApplication.translate`.
- Lizenz GPL-2.0-or-later. Ziel: CachyOS + KDE Plasma Wayland. Kein Test auf GNOME/X11.
- OpenRazer-Kernelmodul (`razermouse`) darf nicht geladen sein; Dienst warnt (Log + D-Bus-Status), wenn doch.
- Projektordner: `/home/anoraknophobia/REA/naga-config` (eigenes git-Repo). Paketname `naga_config`.
- Referenz für Protokoll-Bytes: OpenRazer-Klon in `/tmp/claude-1000/-home-anoraknophobia-REA/26c3406c-5a53-44d1-89a7-08bb13495a0c/scratchpad/src/openrazer/driver/` (`razerchromacommon.c`, `razermouse_driver.c`, Fälle `USB_DEVICE_ID_RAZER_NAGA_V2_PRO_*`). Falls weg: `git clone --depth 1 https://github.com/openrazer/openrazer`.

## Review Focus

1. **Maus ab-/anstecken oder Kabel↔Dongle wechseln während der Dienst läuft** → Dienst stürzt nicht ab, greift die neuen Geräte, setzt Profil-Einstellungen erneut. (Test in Task 6)
2. **Dienst wird hart beendet (`kill -9`) oder stürzt ab** → Seitentasten verhalten sich sofort wieder wie ab Werk (Grab hängt am Dateideskriptor). (Hardware-Prüfung in Task 6)
3. **Hängende Tasten**: Profilwechsel, Hypershift loslassen oder Dienst-Stopp, während eine Ausgabe-Taste/ein Makro gedrückt ist → alle gedrückten Ausgabe-Tasten werden losgelassen, Makros gestoppt. (Test in Task 5)
4. **Kaputte/unbekannte Konfigurationsdatei** → Dienst lädt Standardprofil, stürzt nicht ab, meldet Fehler über D-Bus; GUI zeigt Hinweis. (Test in Task 4)
5. **Deutsches Tastaturlayout (QWERTZ)** → Belegung speichert evdev-Keycodes, nicht Zeichen; Taste „Z“ im Picker erzeugt beim Drücken „Z“. (Test in Task 8)

---

### Task 1: Projektgerüst + Razer-Report-Kern

**Files:**
- Create: `naga-config/pyproject.toml`, `naga-config/LICENSE` (GPL-2.0 Text), `naga-config/.gitignore`
- Create: `naga-config/naga_config/__init__.py`, `naga_config/razer/__init__.py`, `naga_config/razer/report.py`, `naga_config/razer/transport.py`
- Test: `naga-config/tests/test_report.py`

**Interfaces:**
- Produces:
  - `build_report(cmd_class: int, cmd_id: int, args: bytes, size: int | None = None, txn: int = 0x1F) -> bytes` (90 Byte; `size` default `len(args)`)
  - `parse_report(data: bytes) -> RazerResponse` (dataclass: `status: int, cmd_class: int, cmd_id: int, size: int, args: bytes`)
  - Status-Konstanten `STATUS_OK=0x02, STATUS_BUSY=0x01, STATUS_FAIL=0x03, STATUS_TIMEOUT=0x04, STATUS_UNSUPPORTED=0x05`
  - `class Transport(Protocol): def query(self, report: bytes) -> RazerResponse`
  - `class HidrawTransport(Transport)` — `__init__(self, path: str)`; sendet per `HIDIOCSFEATURE` (Report-ID 0 + 90 Byte), wartet 1 ms, liest per `HIDIOCGFEATURE`; bei `STATUS_BUSY` bis 10× erneut lesen; `RazerError` bei FAIL/TIMEOUT/UNSUPPORTED.
  - `find_control_hidraw(pid_filter=(0x00A7, 0x00A8)) -> str | None` — hidraw-Knoten der Maus-Schnittstelle (Interface 0) per pyudev.

- [ ] **Step 1: Pakete installieren (Nutzer bestätigt sudo)**

Run: `sudo pacman -S --needed python-evdev pyside6 python-pyudev python-dbus-next python-pytest python-pytest-asyncio`

- [ ] **Step 2: git init + Gerüst** (`git init` in `naga-config`, pyproject mit `[project.scripts] naga-config = "naga_config.cli:main"`, `naga-configd = "naga_config.daemon.main:main"`, `naga-config-gui = "naga_config.gui.app:main"`)

- [ ] **Step 3: Failing tests**

```python
def test_build_report_battery_query():
    r = build_report(0x07, 0x80, b"\x00\x00")
    assert len(r) == 90
    assert r[1] == 0x1F and r[5] == 0x02 and r[6] == 0x07 and r[7] == 0x80
    assert r[88] == functools.reduce(operator.xor, r[2:88])

def test_parse_report_roundtrip():
    raw = bytearray(build_report(0x04, 0x85, b"\x00" * 7)); raw[0] = 0x02
    resp = parse_report(bytes(raw))
    assert (resp.status, resp.cmd_class, resp.cmd_id, resp.size) == (0x02, 0x04, 0x85, 7)
```

- [ ] **Step 4:** `pytest tests/test_report.py -v` → FAIL (ImportError)
- [ ] **Step 5:** `report.py` + `transport.py` implementieren
- [ ] **Step 6:** `pytest -v` → PASS
- [ ] **Step 7: Hardware-Rauchtest (nur lesen):** Mit `sudo` einmalig (Rechte kommen erst in Task 6) Firmware lesen: Klasse `0x00`, ID `0x81`, size 2. Erwartet: Status 0x02, zwei Versions-Bytes. Ergebnis in Commit-Nachricht notieren.
- [ ] **Step 8: Commit** `feat: razer report core and hidraw transport`

---

### Task 2: Lese-Befehle + Sicherung des Maus-Zustands

**Files:**
- Create: `naga_config/razer/naga_v2_pro.py`, `naga_config/backup.py`, `naga_config/cli.py`
- Test: `tests/test_naga_commands.py`, `tests/test_backup.py`

**Interfaces:**
- Consumes: `build_report`, `Transport`, `RazerResponse`
- Produces (Klasse `NagaV2Pro(transport: Transport)`, Lese-Teil):
  - `firmware() -> str`, `serial() -> str`
  - `dpi() -> tuple[int, int]`, `dpi_stages() -> tuple[int, list[tuple[int, int]]]` (aktive Stufe 1-basiert, Liste x/y)
  - `poll_rate() -> int` (125/500/1000)
  - `brightness(led: int) -> int` (0–255), LED-Konstanten `LOGO=0x04, SCROLL=0x01, THUMB=0x05` (prüfen gegen OpenRazer `MATRIX_DIMS [1,3]`)
  - `battery() -> int` (0–100 %, Rohwert 0–255 umrechnen), `charging() -> bool`
  - `idle_time() -> int` (Sek.), `low_battery_threshold() -> int` (%)
  - `scroll_profile() -> int | None` (Klasse 0x02, ID 0x98, Args `[VARSTORE]` – **nur lesen**, `None` bei UNSUPPORTED)
  - `snapshot() -> dict` — alle obigen Werte, Fehler pro Feld als `None`
  - `backup.save_backup(dev: NagaV2Pro, path: Path) -> dict` — schreibt nur, wenn Datei noch nicht existiert (nie überschreiben); `backup.load_backup(path) -> dict`
  - CLI: `naga-config backup`, `naga-config status` (gibt `snapshot()` als JSON aus)
- Byte-Layouts aus OpenRazer übernehmen (Naga-V2-Pro-Fälle).

- [ ] **Step 1: Failing tests** mit `FakeTransport` (gibt vorbereitete Antworten zurück, protokolliert Anfragen):

```python
def test_dpi_parses_big_endian():
    t = FakeTransport({(0x04, 0x85): bytes([0x00, 0x03, 0x20, 0x03, 0x20, 0, 0])})
    assert NagaV2Pro(t).dpi() == (800, 800)

def test_snapshot_survives_unsupported_field():
    t = FakeTransport(default=STATUS_UNSUPPORTED)
    assert NagaV2Pro(t).snapshot()["scroll_profile"] is None

def test_backup_never_overwrites(tmp_path):
    p = tmp_path / "mouse-backup.json"; p.write_text('{"keep": 1}')
    save_backup(NagaV2Pro(FakeTransport.full()), p)
    assert json.loads(p.read_text()) == {"keep": 1}

def test_no_write_commands_sent_by_snapshot():
    t = FakeTransport.full(); NagaV2Pro(t).snapshot()
    assert all(cid & 0x80 for (_, cid) in t.sent)   # nur Get-IDs (Bit 7)
```

- [ ] **Step 2:** Tests → FAIL
- [ ] **Step 3:** implementieren
- [ ] **Step 4:** Tests → PASS
- [ ] **Step 5: Hardware: Sicherung jetzt erstellen.** `sudo -E python -m naga_config.cli backup` und danach `chown` auf Nutzer. Prüfen: Datei enthält DPI, Abfragerate, Helligkeiten, Akku-Einstellungen, `scroll_profile` (Wert oder null). Zusätzlich Kopie nach `~/REA/naga-config-mouse-backup-2026-10-22.json` (außerhalb des Repos).
- [ ] **Step 6: Commit** `feat: read commands and one-time mouse state backup`

---

### Task 3: Seitentasten-Codes ermitteln (Hardware-Erkundung)

**Files:**
- Create: `naga_config/hw/buttons.py`, `tools/probe_buttons.py`
- Test: `tests/test_buttons.py`

**Interfaces:**
- Produces:
  - `ButtonId` (StrEnum): `SIDE_1`…`SIDE_12`, `LEFT`, `RIGHT`, `MIDDLE`, `WHEEL_UP`, `WHEEL_DOWN`, `WHEEL_LEFT`, `WHEEL_RIGHT`, `TOP_1`, `TOP_2`, … (genaue Liste nach Erkundung)
  - `identify(source: str, ev_type: int, code: int, value: int) -> ButtonId | None` — `source` ∈ {`mouse`, `if01`, `if02`} (by-id-Suffixe)
  - `NAGA_DEVICE_SUFFIXES = {"event-mouse": "mouse", "if01-event-kbd": "if01", "if02-event-kbd": "if02"}` plus `if01-event` falls relevant

- [ ] **Step 1:** `tools/probe_buttons.py` schreiben: öffnet alle `/dev/input/by-id/usb-Razer_Razer_Naga_V2_Pro-*event*` (sudo), gibt `source type code value` aus, **ohne Grab**.
- [ ] **Step 2: Nutzer drückt nacheinander** Seitentasten 1–12, obere Tasten, Rad-Klick, Rad hoch/runter, Rad kippen links/rechts. Ausgabe in `tools/probe-2026-10-22.txt` speichern. Falls der Nutzer 2er-/6er-Platte hat: auch testen, sonst als offen markieren.
- [ ] **Step 3: Failing tests** aus der Aufzeichnung ableiten, z. B.:

```python
def test_side_1_from_kbd_interface():
    assert identify("if01", EV_KEY, KEY_1, 1) == ButtonId.SIDE_1   # tatsächliche Quelle/Code aus Probe einsetzen
def test_wheel_up():
    assert identify("mouse", EV_REL, REL_WHEEL, 1) == ButtonId.WHEEL_UP
def test_unknown_event_ignored():
    assert identify("mouse", EV_MSC, 4, 0) is None
```

- [ ] **Step 4–5:** Test FAIL → `buttons.py` implementieren → PASS
- [ ] **Step 6: Commit** `feat: naga button identification from hardware probe`

---

### Task 4: Konfigurationsmodell (Profile, Belegungen, Makros)

**Files:**
- Create: `naga_config/config/model.py`, `naga_config/config/store.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: `ButtonId`
- Produces (dataclasses, JSON-serialisierbar über `to_dict()/from_dict()`):
  - `Action` = Union aus `DefaultAction()`, `DisabledAction()`, `KeyAction(code: int)`, `ComboAction(codes: list[int])` (Modifier zuerst), `MacroAction(macro_id: str, mode: Literal["once","repeat_n","while_held","toggle"], count: int = 1)`, `HypershiftAction()`; Feld `"type"` im JSON.
  - `MacroStep` = `KeyStep(code: int, down: bool)` | `ButtonStep(code: int, down: bool)` | `DelayStep(ms: int)`; `Macro(id: str, name: str, steps: list[MacroStep])`
  - `LightingZone(effect: Literal["off","static","breath","spectrum","wave","reactive"], color: tuple[int,int,int], brightness: int)`
  - `Profile(id: str, name: str, standard: dict[ButtonId, Action], hypershift: dict[ButtonId, Action], dpi_stages: list[tuple[int,int]], active_stage: int, poll_rate: int, lighting: dict[Literal["logo","scroll","thumb"], LightingZone], scroll_profile: int | None)`
  - `Settings(active_profile: str, low_battery_notify: int = 15)`
  - `validate(profile: Profile) -> list[str]` — Fehler, u. a. „Linksklick nicht erreichbar“, mehr als eine Hypershift-Taste, Makro-ID fehlt
  - `ConfigStore(root: Path)`: `load_settings()`, `save_settings(s)`, `list_profiles() -> list[Profile]`, `load_profile(id)`, `save_profile(p)` (atomar: tmp + `os.replace`), `delete_profile(id)`, `load_macros() -> dict[str, Macro]`, `save_macros(m)`, `default_profile() -> Profile`, `export_profile(id, path)`, `import_profile(path) -> Profile` (neue ID bei Kollision)
- Linksklick-Regel: Profil ungültig, wenn `LEFT` in `standard` nicht `Default` ist und kein anderer Button in `standard` `KeyAction` mit `BTN_LEFT` hat.

- [ ] **Step 1: Failing tests**

```python
def test_profile_roundtrip(tmp_path): ...  # save → load gleich
def test_left_click_lockout_rejected():
    p = default_profile(); p.standard[ButtonId.LEFT] = DisabledAction()
    assert "Linksklick nicht erreichbar" in validate(p)
def test_two_hypershift_keys_rejected(): ...
def test_corrupt_profile_falls_back(tmp_path):
    (tmp_path/"profiles").mkdir(); (tmp_path/"profiles/x.json").write_text("{kaputt")
    s = ConfigStore(tmp_path); assert [p.id for p in s.list_profiles()] == ["default"]
    assert s.errors and "x.json" in s.errors[0]
def test_import_with_existing_id_gets_new_id(tmp_path): ...
```

- [ ] **Step 2–4:** FAIL → implementieren → PASS
- [ ] **Step 5: Commit** `feat: profile, binding and macro config model`

---

### Task 5: Mapping-Engine + Makro-Player (ohne Hardware)

**Files:**
- Create: `naga_config/engine/output.py`, `naga_config/engine/mapper.py`, `naga_config/engine/macro.py`
- Test: `tests/test_mapper.py`, `tests/test_macro.py`

**Interfaces:**
- Consumes: `ButtonId`, `Profile`, `Action`-Typen, `Macro`
- Produces:
  - `class Output(Protocol)`: `key(code: int, down: bool)`, `rel(axis: int, value: int)`, `sync()`
  - `class Mapper(profile: Profile, macros: dict[str, Macro], out: Output, passthrough: Callable[[int,int,int], None])`: `async handle(button: ButtonId, value: int)` (1=down, 0=up, 2=repeat; Rad: value = Richtung); `set_profile(profile)`; `async release_all()`
  - `class MacroPlayer(out: Output, sleep=asyncio.sleep)`: `start(macro, mode, count, key: ButtonId)`, `button_up(key)`, `async stop_all()`
- Verhalten:
  - `Default` → Originalereignis über `passthrough`
  - `Key`/`Combo` → bei down alle Codes in Reihenfolge drücken, bei up rückwärts loslassen; Auto-Repeat (value 2) durchreichen
  - Hypershift-Taste: solange gedrückt, gilt `hypershift`-Belegung; Button-up wird an die Belegung geschickt, mit der der Button gedrückt wurde (auch wenn Hypershift zwischendurch losgelassen wurde)
  - `set_profile` und `release_all` lassen alle gedrückten Ausgabe-Codes los und stoppen Makros

- [ ] **Step 1: Failing tests** mit `RecordingOutput`:

```python
async def test_combo_press_release_order():
    # SIDE_1 -> Strg+Shift+M
    ... assert out.log == [(KEY_LEFTCTRL,1),(KEY_LEFTSHIFT,1),(KEY_M,1),(KEY_M,0),(KEY_LEFTSHIFT,0),(KEY_LEFTCTRL,0)]
async def test_hypershift_layer_and_release_after_shift_up():
    # HS down, SIDE_1 down (→F2), HS up, SIDE_1 up  ⇒ F2 down, F2 up (nicht F1)
async def test_profile_switch_releases_held_keys(): ...
async def test_macro_while_held_stops_on_release(): ...   # Fake-sleep
async def test_macro_toggle_second_press_stops(): ...
async def test_macro_repeat_n_runs_exactly_n(): ...
async def test_default_passes_through(): ...
```

- [ ] **Step 2–4:** FAIL → implementieren → PASS
- [ ] **Step 5: Commit** `feat: mapping engine with hypershift and macro player`

---

### Task 6: Dienst (evdev-Grab, uinput, Hotplug, D-Bus, Rechte)

**Files:**
- Create: `naga_config/daemon/main.py`, `naga_config/daemon/devices.py`, `naga_config/daemon/uinput_out.py`, `naga_config/daemon/dbus_service.py`
- Create: `packaging/70-naga-config.rules`, `packaging/naga-configd.service`
- Test: `tests/test_daemon_devices.py`, `tests/test_dbus_service.py`

**Interfaces:**
- Consumes: `identify`, `NAGA_DEVICE_SUFFIXES`, `Mapper`, `ConfigStore`, `NagaV2Pro`, `find_control_hidraw`
- Produces:
  - `UinputOutput(Output)` — ein virtuelles Gerät „naga-config virtual“ mit allen KEY_*, BTN_LEFT..BTN_TASK, REL_X/Y/WHEEL/HWHEEL(+_HI_RES)
  - `DeviceWatcher(on_added: Callable[[str, InputDevice], None], on_removed: Callable[[str], None])` — pyudev-Monitor; ignoriert eigenes uinput-Gerät
  - D-Bus: Name `io.github.nagaconfig`, Pfad `/io/github/nagaconfig`, Interface `io.github.nagaconfig.Daemon`:
    - Methoden: `ListProfiles() -> a(ss)` (id, name), `GetActiveProfile() -> s`, `ActivateProfile(s id)`, `Reload()`, `GetStatus() -> a{sv}` (connected, connection: "wired"/"wireless", battery, charging, openrazer_loaded, config_errors), `RestoreOriginal()`, `ScrollProfileSupported() -> b`
    - Signale: `ProfileChanged(s)`, `StatusChanged(a{sv})`
- udev-Regel: `SUBSYSTEM=="hidraw"` und `SUBSYSTEM=="input"` mit `ATTRS{idVendor}=="1532", ATTRS{idProduct}=="00a7|00a8"` → `TAG+="uaccess"`; `KERNEL=="uinput", TAG+="uaccess"`
- Service: `Type=dbus`, `BusName=io.github.nagaconfig`, `Restart=on-failure`, `WantedBy=default.target`

- [ ] **Step 1: Failing tests**: Device-Zuordnung über Fake-Pfade (by-id-Suffix → source); D-Bus-Service mit Fake-Mapper/Store (ActivateProfile ruft `set_profile` + `save_settings`, sendet `ProfileChanged`; unbekannte ID → D-Bus-Fehler); Watcher entfernt Gerät → Mapper `release_all` wird aufgerufen.
- [ ] **Step 2–4:** FAIL → implementieren → PASS. Grab per `dev.grab()`; Signal-Handler SIGTERM/SIGINT → `release_all`, `ungrab`, uinput schließen. Profil-Geräteeinstellungen (Task 7) werden hier über einen Hook `apply_device_settings(profile)` aufgerufen – in diesem Task noch No-op.
- [ ] **Step 5: Rechte einrichten (Nutzer bestätigt sudo):** Regel nach `/etc/udev/rules.d/`, `sudo udevadm control --reload && sudo udevadm trigger`; Maus neu anstecken; prüfen `getfacl /dev/hidraw2` zeigt Nutzer.
- [ ] **Step 6: Hardware-Test:** Dienst im Vordergrund starten (`python -m naga_config.daemon.main`), Testprofil: SIDE_1 → `F13`… besser sichtbar: SIDE_1 → Taste `A`. Prüfen in einem Editor: Seitentaste 1 tippt „a“, Seitentaste 2 tippt wie ab Werk „2“. Dann `kill -9` → Seitentaste 1 tippt wieder „1“. Maus ab- und anstecken bei laufendem Dienst → Belegung greift wieder.
- [ ] **Step 7: Commit** `feat: daemon with evdev grab, uinput output, hotplug and dbus`

---

### Task 7: Schreib-Befehle für DPI, Abfragerate, Licht, Akku + Wiederherstellen

**Files:**
- Modify: `naga_config/razer/naga_v2_pro.py` (Schreib-Teil), `naga_config/daemon/main.py` (`apply_device_settings`)
- Modify: `naga_config/backup.py` (`restore_backup(dev, data)`)
- Test: `tests/test_naga_commands.py`, `tests/test_backup.py`

**Interfaces:**
- Produces (`NagaV2Pro`):
  - `set_dpi(x: int, y: int)`, `set_dpi_stages(active: int, stages: list[tuple[int,int]])` (1–5 Stufen, 100–30000, Schritt 50 → sonst `ValueError`)
  - `set_poll_rate(hz: Literal[125, 500, 1000])`
  - `set_effect(led: int, zone: LightingZone)` (Extended-Matrix 0x0F/0x02; Welle/Spektrum ohne Farbe), `set_brightness(led: int, value: int)`
  - `set_idle_time(seconds: int)` (60–900), `set_low_battery_threshold(percent: int)`
  - Alle mit Speicher-Flag `NOSTORE`, wo das Layout ein Flag hat.
  - `restore_backup(dev, data: dict) -> list[str]` — schreibt gesicherte Werte zurück **mit dem Flag, das die Maus vorher hatte (VARSTORE)**, gibt nicht wiederherstellbare Felder zurück.

- [ ] **Step 1: Failing tests:** Byte-Layout je Befehl (Klasse/ID/Args) gegen OpenRazer-Werte; `NOSTORE` an Arg-Index 0 bei allen Set-Befehlen mit Flag; `set_dpi_stages` mit 6 Stufen → `ValueError`; `apply_device_settings` ruft die Set-Befehle in fester Reihenfolge (poll, dpi_stages, lighting, brightness).
- [ ] **Step 2–4:** FAIL → implementieren → PASS
- [ ] **Step 5: Hardware: NOSTORE prüfen.** Mit vorhandener Sicherung: Logo auf Statisch Rot setzen (NOSTORE) → sichtbar rot. Maus ab- und anstecken **bei gestopptem Dienst** → Logo zeigt wieder den gesicherten Zustand ⇒ NOSTORE wirkt. Gleiches mit DPI (Wert per `naga-config status`). Ergebnis je Befehl in `docs/hardware-notes.md` notieren. Falls ein Befehl NOSTORE ignoriert: im Code als `persistent=True` markieren; der Dienst setzt ihn trotzdem, `RestoreOriginal` stellt ihn zurück.
- [ ] **Step 6:** `naga-config restore` (CLI) testen → Werte wie in Sicherung.
- [ ] **Step 7: Commit** `feat: device write commands with nostore and restore`

---

### Task 8: GUI-Grundgerüst – Variante A (Mausbild + Tasten-Reiter) und Tray

**Files:**
- Create: `naga_config/gui/app.py`, `gui/main_window.py`, `gui/dbus_client.py`, `gui/mouse_view.py`, `gui/action_dialog.py`, `gui/keynames.py`, `gui/tray.py`
- Create: `naga_config/gui/assets/naga_v2_pro_side.svg`, `naga_v2_pro_top.svg` (eigene schlichte Vektorgrafik)
- Test: `tests/gui/test_keynames.py`, `tests/gui/test_action_dialog.py` (pytest-qt nicht vorausgesetzt; `QT_QPA_PLATFORM=offscreen` + `QApplication` direkt)

**Interfaces:**
- Consumes: `ConfigStore`, `Profile`, `Action`-Typen, `validate`, D-Bus-API aus Task 6
- Produces:
  - `DaemonClient(QObject)`: Slots/Methoden spiegeln D-Bus; Qt-Signale `profileChanged(str)`, `statusChanged(dict)`, `available(bool)`
  - `keycode_from_qt(event: QKeyEvent) -> int` = `event.nativeScanCode() - 8` (Wayland/X11 XKB-Offset)
  - `key_label(code: int) -> str` — layoutgerechter Name über `QKeySequence`/xkb; Fallback evdev-Name
  - `ActionDialog(parent, current: Action, macros: dict[str, Macro]) -> Action | None` (Typ-Auswahl: Standard, Deaktiviert, Taste, Kombination, Makro + Modus/Anzahl, Hypershift; Taste/Kombination per „Drücken zum Aufnehmen“)
  - `MouseView(QWidget)`: SVG in der Mitte, links Seitentasten-Beschriftungen 1–12, rechts Links/Rechts/Rad/obere Tasten; Klick auf Beschriftung → `bindingClicked(ButtonId)`
- Fenster: Kopfzeile Profil-Combo (+ Neu, Umbenennen, Duplizieren, Löschen, Export, Import), Umschalter „Standard | Hypershift“, Akku-Anzeige; Reiter **Tasten · Leistung · Beleuchtung · Mausrad · Makros** (letzte vier als Platzhalter, gefüllt in Task 9–11). Speichern → `ConfigStore.save_profile` → `DaemonClient.Reload()`. Validierungsfehler blockieren das Speichern mit Meldung.
- Tray (`QSystemTrayIcon`): Menü mit Profilliste (Radio), aktivem Profil, Akku-Text, „Fenster öffnen“, „Originalzustand wiederherstellen“, „Beenden“ (beendet nur GUI/Tray, nicht den Dienst). Wenn Dienst nicht erreichbar: Hinweis-Banner im Fenster.
- Die GUI erscheint nach dem bereits gewählten Entwurf „A · Synapse-Stil“ (`/home/anoraknophobia/REA/.superpowers/brainstorm/8299-1792656802/content/layout.html`).

- [ ] **Step 1: Failing tests**

```python
def test_keycode_from_qt_qwertz_z():
    # evdev-Codes sind physisch: die Taste rechts neben T heißt KEY_Y, beschriftet auf QWERTZ mit "Z"
    ev = fake_key_event(native_scan_code=KEY_Y + 8)
    assert keycode_from_qt(ev) == KEY_Y
def test_key_label_uses_layout():  # unter de-Layout zeigt KEY_Y das Label "Z"
    assert key_label(KEY_Y) in ("Z", "z")
def test_action_dialog_returns_combo(): ...  # simulierte Eingabe Strg+Shift+M
def test_save_blocked_on_validation_error(): ...
```

- [ ] **Step 2–4:** FAIL → implementieren → PASS
- [ ] **Step 5: Manuell mit Nutzer:** GUI starten, SIDE_3 auf „Strg+Z“ legen, speichern, in Kate testen (Rückgängig). Hypershift auf SIDE_12, in Hypershift SIDE_1 → `F5`, in Browser testen. Profil im Tray wechseln.
- [ ] **Step 6: Commit** `feat: synapse-style gui with button tab and tray`

---

### Task 9: Makro-Reiter mit Aufnahme und Editor

**Files:**
- Create: `naga_config/gui/macro_tab.py`, `naga_config/gui/macro_recorder.py`
- Test: `tests/gui/test_macro_recorder.py`

**Interfaces:**
- Consumes: `Macro`, `MacroStep`-Typen, `keycode_from_qt`, `ConfigStore.load_macros/save_macros`
- Produces:
  - `MacroRecorder(QObject)`: `start(record_delays: bool)`, `stop() -> list[MacroStep]`; Eingabe über `eventFilter` auf dem Aufnahmefeld (Tasten + Maustasten im Feld); Pausen gemessen per `QElapsedTimer`, ≥ 1 ms; Option „Pausen fest: N ms“
  - `MacroTab(QWidget)`: Liste der Makros (Neu, Umbenennen, Löschen – Löschen warnt, wenn in Profilen benutzt), Schrittliste (Einfügen Taste/Maustaste/Pause, Löschen, Hoch/Runter, Pause bearbeiten)

- [ ] **Step 1: Failing tests:** Aufnahme von `a` down/up mit Pause 50 ms ergibt `[KeyStep(KEY_A,True), DelayStep(~50), KeyStep(KEY_A,False)]` (Toleranz ±20 ms); „Pausen fest 10 ms“ setzt alle DelaySteps auf 10; Löschen eines benutzten Makros liefert Warnliste der Profilnamen.
- [ ] **Step 2–4:** FAIL → implementieren → PASS
- [ ] **Step 5: Manuell:** Makro „Gruß“ (H, a, l, l, o) aufnehmen, auf SIDE_4 legen (einmal), in Kate testen; Modus „solange gedrückt“ testen.
- [ ] **Step 6: Commit** `feat: macro recorder and editor`

---

### Task 10: Reiter Leistung + Beleuchtung, Akku-Anzeige und Warnung

**Files:**
- Create: `naga_config/gui/performance_tab.py`, `naga_config/gui/lighting_tab.py`, `naga_config/daemon/battery.py`
- Test: `tests/test_battery.py`, `tests/gui/test_performance_tab.py`

**Interfaces:**
- Consumes: `Profile.dpi_stages/active_stage/poll_rate/lighting`, `NagaV2Pro`-Setter (über Dienst: Profil speichern → `Reload` → `apply_device_settings`), `StatusChanged`
- Produces:
  - Leistung: 1–5 DPI-Stufen (Spinbox 100–30000, Schritt 50, X/Y gekoppelt mit „getrennt“-Option), aktive Stufe, Abfragerate 125/500/1000
  - Beleuchtung: je Zone Logo/Mausrad/Daumenfeld Effekt, Farbe (`QColorDialog`), Helligkeit 0–100 % (→ 0–255); „Auf alle Zonen anwenden“
  - `BatteryMonitor(dev, interval_s=60, on_change)` im Dienst: fragt Akku/Laden nur bei Funkbetrieb ab; sendet `StatusChanged`; Benachrichtigung über `org.freedesktop.Notifications` einmal beim Unterschreiten von `Settings.low_battery_notify`, erneut erst nach Laden über Schwelle+5.
  - Akku-Einstellungen (Ruhezeit, Schwelle der Maus) im Reiter Leistung, Abschnitt „Akku“.

- [ ] **Step 1: Failing tests:** BatteryMonitor benachrichtigt bei 14 % genau einmal (Folgewerte 13, 12 → keine weitere), nach 25 % und erneut 14 % wieder; Kabelbetrieb → keine Abfrage; DPI-Spinbox lehnt 6. Stufe ab.
- [ ] **Step 2–4:** FAIL → implementieren → PASS
- [ ] **Step 5: Manuell:** zwei Profile mit unterschiedlichem Licht/DPI, im Tray wechseln → Licht und DPI wechseln sichtbar/spürbar. Falls Dongle vorhanden: auf Funk umstecken, Akku im Tray sichtbar.
- [ ] **Step 6: Commit** `feat: performance, lighting and battery`

---

### Task 11: Mausrad-Rasterung (Forschung + Reiter)

**Files:**
- Create: `tools/probe_scroll.py`, `naga_config/gui/scroll_tab.py`
- Modify: `naga_config/razer/naga_v2_pro.py` (`set_scroll_profile`, ggf. `set_scroll_custom`), `docs/hardware-notes.md`
- Test: `tests/test_naga_commands.py`

**Interfaces:**
- Produces:
  - `SCROLL_PROFILES = {0: "Standard", 1: "Distinct", 2: "Precise", 3: "Adaptive", 4: "Smooth", 5: "Custom"}` (Reihenfolge an Hardware bestätigen)
  - `set_scroll_profile(profile: int)` — Klasse 0x02, ID 0x18, Args `[NOSTORE oder VARSTORE, profile]` (Flag laut Hardware-Befund)
  - optional `set_scroll_custom(tension: int, steps: int)` (0–100, 8–96) – nur wenn Befehl gefunden
  - `ScrollTab`: sechs Voreinstellungen als Auswahl; Custom-Regler nur wenn unterstützt; wenn `ScrollProfileSupported()` false → Reiter zeigt „Wird für dieses Gerät noch nicht unterstützt“.

- [ ] **Step 1: Nur lesen:** `probe_scroll.py get` sendet 0x02/0x98 mit `[VARSTORE]` und `[NOSTORE]`; Ausgabe protokollieren. Nutzer stellt (falls möglich) vorher unter Windows ein bekanntes Profil ein – sonst Startwert aus Sicherung als Referenz.
- [ ] **Step 2: Setzen:** `probe_scroll.py set N` für N=0..5 nacheinander; **Nutzer dreht das Rad und beschreibt das Gefühl**; nach jedem Set erneut lesen. Danach Wert aus Sicherung zurückschreiben.
- [ ] **Step 3: Custom suchen (zeitbegrenzt ≤ 30 Min):** Get-Abfragen 0x02/0x99–0x9F lesen und Antworten vergleichen, wenn Profil 5 aktiv ist. Kein blindes Setzen unbekannter IDs. Ohne Fund: Custom-Regler entfallen, Notiz in `hardware-notes.md`, Nutzer kann später einen USB-Mitschnitt unter Windows liefern.
- [ ] **Step 4: Failing tests** für bestätigte Byte-Layouts → implementieren → PASS
- [ ] **Step 5:** Reiter bauen; `apply_device_settings` setzt `profile.scroll_profile`, falls nicht `None`.
- [ ] **Step 6: Manuell:** Profilwechsel im Tray ändert die Rasterung spürbar.
- [ ] **Step 7: Commit** `feat: hyperscroll profiles` (bzw. `docs: hyperscroll research notes` ohne Erfolg)

---

### Task 12: Paket, Autostart, Abschluss

**Files:**
- Create: `packaging/PKGBUILD`, `packaging/naga-config.desktop`, `packaging/io.github.nagaconfig.service` (D-Bus-Aktivierung), `README.md`, `docs/hardware-notes.md` (vervollständigen)
- Modify: `packaging/naga-configd.service`

**Interfaces:**
- Consumes: alle Einstiegspunkte aus `pyproject.toml`
- Produces: Paket `naga-config` installiert: Python-Modul, `/usr/lib/udev/rules.d/70-naga-config.rules`, `/usr/lib/systemd/user/naga-configd.service`, `/usr/share/applications/naga-config.desktop`, Autostart-Eintrag für den Tray (`/etc/xdg/autostart/naga-config-tray.desktop`, startet `naga-config-gui --tray`). `depends=(python python-evdev pyside6 python-pyudev python-dbus-next)`, `conflicts=(openrazer-daemon openrazer-driver-dkms)`.

- [ ] **Step 1:** `cd packaging && makepkg -si` (Nutzer bestätigt sudo); manuell installierte udev-Regel aus Task 6 aus `/etc/udev/rules.d/` entfernen.
- [ ] **Step 2:** `systemctl --user enable --now naga-configd` → `systemctl --user status naga-configd` zeigt `active (running)`.
- [ ] **Step 3: Ende-zu-Ende nach Abmelden/Anmelden:** Tray-Symbol da, Profil aktiv, Seitentasten belegt, Licht/DPI nach Profil.
- [ ] **Step 4: README** (Deutsch): Installation, Erste Schritte, Originalzustand wiederherstellen, Deinstallation, bekannte Grenzen (Bluetooth nur Tasten, HyperScroll-Status).
- [ ] **Step 5: Originalzustand:** Nutzer entscheidet: Profil weiter nutzen oder `naga-config restore` für Synapse-Zustand. Mindestens einmal `restore` ausführen und mit `naga-config status` gegen Sicherung vergleichen.
- [ ] **Step 6: Commit** `build: arch package, autostart and readme`
