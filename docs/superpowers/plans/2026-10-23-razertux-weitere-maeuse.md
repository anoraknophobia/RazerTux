# RazerTux – Weitere Razer-Mäuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** RazerTux unterstützt neben der Naga V2 Pro alle 117 OpenRazer-Mäuse (experimentell), mehrere Mäuse gleichzeitig, „Taste anlernen“ und Gerätegrafiken im Laser-Stil.

**Architecture:** Ein Import-Skript erzeugt aus den OpenRazer-Quellen eine Geräte-Datenbank (JSON, eingecheckt). Ein generischer Treiber `RazerMouse(info, transport)` baut Befehle aus Protokoll-Varianten der Datenbank; die Naga V2 Pro ist eine Unterklasse mit ihren Besonderheiten. Der Dienst verwaltet pro angeschlossenem Modell eine `DeviceSession` (Treiber, Eingabe-Router, Mapper, Profil, Status) und spricht über ein neues, versioniertes D-Bus-Interface `io.github.razertux.Daemon1` mit Fenster und Tray. Grafiken sind schlichte Linien-SVGs mit IDs pro Taste; zur Laufzeit macht `laserize()` daraus leuchtende grüne Linien.

**Tech Stack:** Python 3.14, PySide6 6.12 (QtSvg ohne Filter), python-evdev 2.0, pyudev, dbus-next, pytest + pytest-asyncio. Paket für CachyOS (PKGBUILD).

**Spec:** `docs/superpowers/specs/2026-10-23-razertux-weitere-maeuse-design.md` (freigegeben 2026-10-23, inkl. §6b Claudes Entscheidungen und §6c Laser-Stil). Vorgänger-Spec: `docs/superpowers/specs/2026-10-22-naga-config-design.md`.

## Global Constraints

- Repo `/home/anoraknophobia/REA/naga-config`, Branch `feat/naga-config` (weiterarbeiten, kein neuer Branch). Paket `razertux`, Version am Ende **0.3.0**.
- Volle Testsuite grün: `QT_QPA_PLATFORM=offscreen python -m pytest -q -W error`. Bestehende Naga-Tests dürfen nur angepasst werden, wenn sich ein Name/Pfad ändert; Verhalten der Naga V2 Pro bleibt gleich (Anti-Ziel der Spec).
- Commit-Trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` und `Claude-Session: https://claude.ai/code/session_019cx8LBGwxGXqZKrBnTiXo9`.
- Keine Razer-Bilder, -Logos oder -Fotos im Repo. Grafiken nur eigene Linienzeichnungen. Produktnamen nur als Text. Hinweis „Kein offizielles Razer-Produkt“ bleibt.
- Laser-Stil: Akzent `#44D62C` (Theme „synapse“) bzw. Paletten-Highlight (Theme „system“), Glühen durch gestapelte Striche (QtSvg kann keine Filter), Hervorhebung = Füllung mit Deckkraft `0.35`.
- OpenRazer-Daten: GPL-2.0, Quelle und Commit im JSON-Kopf vermerken. Referenz-Klon: `/tmp/claude-1000/-home-anoraknophobia-REA/26c3406c-5a53-44d1-89a7-08bb13495a0c/scratchpad/ext/openrazer` (Commit `477f4d6`); falls weg: `git clone --depth 1 https://github.com/openrazer/openrazer` und Commit im JSON aktualisieren.
- Schreibbefehle mit Speicher-Flag standardmäßig `NOSTORE` (0x00). Ein experimentelles Gerät bekommt **keinen** Schreibbefehl, bevor die Lese-Prüfung (Task 4) bestanden ist und die Sicherung existiert.
- Eingabegeräte nur exklusiv übernehmen (grab), wenn das aktive Profil des Geräts etwas umbelegt; bei experimentellen Geräten zusätzlich nur, wenn „Tastenbelegung aktivieren“ an ist (Spec §6b.1).
- Unbekannte Razer-PIDs (nicht in der Datenbank): ignorieren – kein Grab, kein Befehl.
- Texte: Deutsch als Quellsprache über `self.tr()` / `QCoreApplication.translate` / `razertux.i18n.tr`; jede neue Zeichenkette auch in `razertux/i18n/{en,es,fr}.json` (test_i18n bleibt grün). Reiter-Titel in Großbuchstaben.
- `razertux/gui/main_window.py` bleibt ≤ 400 Zeilen (aktuell 395: neue Logik in eigene Module).
- Profilwechsel nur manuell (GUI/Tray). Makro-Aufnahme nur im eigenen Fenster.
- Ziel: CachyOS, KDE Plasma (Wayland).

## Review Focus

1. **Zwei verschiedene Razer-Mäuse gleichzeitig ein- und ausstecken** → jede bekommt ihre eigene Sitzung; Abziehen der einen lässt die andere unberührt (Grab, Profil, Status). Test in Task 7.
2. **Razer-Gerät mit PID, die nicht in der Datenbank steht** (z. B. Tastatur, neue Maus) → wird nie gegriffen und bekommt keinen Befehl. Test in Task 5 und Task 7.
3. **Lese-Prüfung schlägt fehl** (Befehl nicht unterstützt, Zeitüberschreitung, unplausible Werte) → Gerät bleibt nur-lesend, auch nach Profilwechsel/Reload; Fenster zeigt Hinweis + „Diagnose kopieren“. Test in Task 4 und Task 7.
4. **Erster Start nach dem Update mit vorhandenen Naga-Profilen/Einstellungen/Sicherung** → alles landet unverändert beim Modell `naga_v2_pro`, aktives Profil bleibt aktiv, alte Dateien bleiben als Sicherung. Test in Task 6.
5. **GUI und Dienst unterschiedlicher Version** (z. B. altes Fenster gegen neuen Dienst nach Update) → kein Absturz: Fenster zeigt „Dienst läuft nicht“/Versionshinweis statt Fehlern. Test in Task 8.

---

### Task 1: Geräte-Datenbank aus OpenRazer

**Files:**
- Create: `tools/import_openrazer.py`, `razertux/devices/__init__.py`, `razertux/devices/db.py`, `razertux/devices/openrazer_mice.json` (generiert)
- Test: `tests/test_import_openrazer.py`, `tests/test_device_db.py`, Fixtures `tests/data/openrazer_snippet.c`, `tests/data/openrazer_mouse_snippet.py`

**Interfaces:**
- Produces:
  - `razertux.devices.db.DeviceInfo` (frozen dataclass): `model: str` (Slug, z. B. `"naga_v2_pro"`), `name: str` (z. B. `"Razer Naga V2 Pro"`), `pids: dict[int, str]` (PID → `"wired" | "wireless" | "receiver" | "bluetooth"`), `dpi_max: int`, `poll_rates: tuple[int, ...]`, `features: frozenset[str]`, `zones: dict[str, tuple[str, ...]]` (Zone → Effekte), `protocol: dict[str, Command]`, `interface: int = 0`, `verified: bool = False`.
  - `razertux.devices.db.Command` (frozen dataclass): `fn: str` (OpenRazer-Builder-Name), `txn: int`.
  - `load_devices() -> dict[str, DeviceInfo]` (nach `model`), `by_pid(pid: int) -> DeviceInfo | None`.
  - Feature-Namen (fest): `"dpi"`, `"dpi_stages"`, `"poll_rate"`, `"battery"`, `"charging"`, `"idle_time"`, `"low_battery_threshold"`, `"scroll_mode"`, `"scroll_acceleration"`, `"scroll_smart_reel"`, `"device_mode"`, `"firmware"`, `"serial"`.
  - Protokoll-Schlüssel (fest): `get_dpi`, `set_dpi`, `get_dpi_stages`, `set_dpi_stages`, `get_poll_rate`, `set_poll_rate`, `get_battery`, `get_charging`, `get_idle_time`, `set_idle_time`, `get_low_battery_threshold`, `set_low_battery_threshold`, `get_scroll_mode`, `set_scroll_mode`, `get_scroll_acceleration`, `set_scroll_acceleration`, `get_scroll_smart_reel`, `set_scroll_smart_reel`, `get_device_mode`, `set_device_mode`, `get_firmware`, `get_serial`, und je Zone `get_brightness:<zone>`, `set_brightness:<zone>`, `set_effect:<zone>:<effect>`.

**Vorgehen des Importers:**
- `daemon/openrazer_daemon/hardware/mouse.py` per `ast` lesen: je Klasse `USB_PID`, `DPI_MAX`, `METHODS`, Name aus Docstring („Class for the Razer X (Wired)“), `POLL_RATES` falls vorhanden.
- Modell-Slug: Klassenname ohne `Razer` und ohne Endung `Wired|Wireless|Receiver|Bluetooth|Dongle` (und Varianten wie `WirelessReceiver`), dann snake_case. Gleicher Slug → ein logisches Gerät mit mehreren PIDs.
- `driver/razermouse_driver.c`: `USB_DEVICE_ID_*`-Namen über `driver/razermouse_driver.h` in PIDs auflösen. In jeder Funktion `razer_attr_(read|write)_<attr>` die `switch(device->usb_pid)`-Blöcke zerlegen: Fallgruppe (`case`-Labels bis zum Rumpf) → Rumpftext; daraus der erste `razer_chroma_*`-/`razer_*`-Builder-Aufruf und `transaction_id.id = 0x..` (fehlt sie: `0xFF`, wie im OpenRazer-Default). `default:`-Zweig gilt für alle nicht genannten PIDs.
- In `razer_mouse_probe` die `CREATE_DEVICE_FILE`-Listen je PID-Fallgruppe → welche Attribute ein Gerät hat (Zonen/Effekte aus `*_matrix_effect_*`/`*_led_brightness`).
- Nur Builder, die Task 2 kennt (Liste in `razertux/razer/commands.py: BUILDERS`), werden übernommen; alles andere → Befehl fehlt → Feature ausgeblendet (Spec §6b.4: „nie geraten“). Der Importer schreibt eine Zusammenfassung „übersprungen: <fn> (n Geräte)“ auf stderr.
- Ausgabe-JSON: `{"_source": {"repo": "...", "commit": "...", "license": "GPL-2.0-only (OpenRazer)"}, "devices": [...]}`, sortiert, `indent=1`, deterministisch.
- `naga_v2_pro` bekommt `verified: true` (einziges geprüftes Modell); alle anderen `false`.

- [ ] **Step 1: Fixtures anlegen** – je ein kleiner Auszug aus `razermouse_driver.c` (eine `write_poll_rate`-Funktion mit zwei Fallgruppen und `default:`), `razermouse_driver.h` (drei `#define USB_DEVICE_ID_…`) und `mouse.py` (zwei Klassen Wired/Wireless desselben Modells) nach `tests/data/`.
- [ ] **Step 2: Failing tests schreiben**

```python
def test_switch_groups_map_pids_to_builder_and_txn():
    groups = parse_switch(read("openrazer_snippet.c"), "razer_attr_write_poll_rate", PIDS)
    assert groups[0x0084] == Command("razer_chroma_misc_set_polling_rate", 0x3F)
    assert groups["default"].txn == 0xFF

def test_wired_and_wireless_classes_merge_into_one_model():
    devs = parse_daemon_classes(read("openrazer_mouse_snippet.py"))
    assert devs["naga_v2_pro"].pids == {0x00A7: "wired", 0x00A8: "wireless"}

def test_generated_db_naga_v2_pro_matches_hardware_facts():
    d = load_devices()["naga_v2_pro"]
    assert d.verified and d.dpi_max == 30000 and set(d.pids) == {0x00A7, 0x00A8}
    assert d.protocol["set_dpi"].txn == 0x1F and "battery" in d.features

def test_generated_db_is_reproducible(tmp_path):  # runs the importer against the reference clone
    pytest.importorskip(...)  # skip when the clone is missing
    assert (tmp_path / "out.json").read_text() == DB_PATH.read_text()

def test_db_has_all_openrazer_mice():
    assert len(load_devices()) >= 60 and sum(len(d.pids) for d in load_devices().values()) >= 117
```

- [ ] **Step 3:** `pytest tests/test_import_openrazer.py tests/test_device_db.py -v` → FAIL (Module fehlen).
- [ ] **Step 4:** Importer und `db.py` implementieren; `python tools/import_openrazer.py <clone> > razertux/devices/openrazer_mice.json` ausführen. `by_pid` cached die Liste (`functools.cache`). JSON als Paketdaten in `pyproject.toml` aufnehmen.
- [ ] **Step 5:** Tests → PASS; volle Suite grün.
- [ ] **Step 6: Commit** `feat: device database imported from OpenRazer`.

---

### Task 2: Protokoll-Bausteine (Builder-Port)

**Files:**
- Create: `razertux/razer/commands.py`
- Modify: `razertux/razer/naga_v2_pro.py` (eigene Byte-Layouts auf `commands.py` umstellen, ohne Verhaltensänderung)
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `razertux.razer.report.build_report(cmd_class, cmd_id, args, size, txn)`.
- Produces: `BUILDERS: dict[str, Callable[..., tuple[int, int, bytes, int]]]` – OpenRazer-Builder-Name → Funktion, die `(cmd_class, cmd_id, args, size)` liefert. Lese-Builder haben zusätzlich `PARSERS: dict[str, Callable[[bytes], Any]]` (gleicher Schlüssel). Mindestumfang (alles, was Mäuse in `razermouse_driver.c` für die Protokoll-Schlüssel aus Task 1 benutzen): DPI xy (Standard und Byte-Variante), DPI-Stufen, Abfragerate (Standard und HyperPolling), Helligkeit klassisch (0x03) und erweitert (0x0F), Effekte klassisch-Matrix (0x03) und erweitert (0x0F) für none/static/spectrum/breath(single)/reactive/wave, Akku 0x07/0x80, Laden 0x07/0x84, Leerlauf 0x07/0x83+0x03, Akku-Schwelle 0x07/0x81+0x01, Scroll-Modus/Beschleunigung/Smart-Reel, Gerätemodus 0x00/0x84+0x04, Firmware 0x00/0x81, Seriennummer 0x00/0x82.

- [ ] **Step 1: Failing tests** – je Builder ein Byte-Test gegen die Werte aus dem OpenRazer-Kommentarblock bzw. dem C-Code (Zeilenreferenz als Kommentar im Test), z. B.:

```python
def test_set_polling_rate_standard():
    assert BUILDERS["razer_chroma_misc_set_polling_rate"](1000) == (0x00, 0x05, b"\x01", 0x01)

def test_hyperpolling_rate_4000():
    cls, cid, args, size = BUILDERS["razer_chroma_misc_set_polling_rate2"](4000, 0x00)
    assert (cls, cid, size) == (0x00, 0x40, 0x02) and args == b"\x00\x02"

def test_naga_layouts_unchanged():   # Anti-Ziel: dieselben Bytes wie bisher
    assert BUILDERS["razer_chroma_misc_set_dpi_xy"](0x00, 1800, 1800)[2][:5] == bytes([0, 7, 8, 7, 8])
```

- [ ] **Step 2:** FAIL bestätigen. **Step 3:** `commands.py` schreiben; `NagaV2Pro` ruft seine Bytes darüber ab. **Step 4:** Suite grün (alle Naga-Bytetests unverändert). **Step 5: Commit** `refactor: OpenRazer command builders in one module`.

---

### Task 3: Generischer Treiber `RazerMouse`

**Files:**
- Create: `razertux/razer/mouse.py`
- Modify: `razertux/razer/naga_v2_pro.py` (wird `class NagaV2Pro(RazerMouse)`), `razertux/backup.py` (nimmt `RazerMouse`)
- Test: `tests/test_razer_mouse.py`

**Interfaces:**
- Consumes: `DeviceInfo`, `Command` (Task 1), `BUILDERS`/`PARSERS` (Task 2), `Transport.query`.
- Produces: `class RazerMouse(info: DeviceInfo, transport: Transport)` mit `supports(feature: str) -> bool` und denselben Methodennamen wie heute `NagaV2Pro` (`firmware, serial, dpi, set_dpi, dpi_stages, set_dpi_stages, poll_rate, set_poll_rate, brightness(zone), set_brightness(zone, value, store=NOSTORE), set_effect(zone, LightingZone, store=NOSTORE), battery, charging, idle_time, set_idle_time, low_battery_threshold, set_low_battery_threshold, snapshot`) plus `scroll_mode/set_scroll_mode`, `scroll_acceleration/set_scroll_acceleration`, `scroll_smart_reel/set_scroll_smart_reel`. Nicht unterstützt → `UnsupportedFeature(Exception)`. Zonen über Namen (`"logo"`, `"scroll"`, `"thumb"`, …) statt LED-Konstanten.
- `make_mouse(info, transport) -> RazerMouse`: `NagaV2Pro` für `model == "naga_v2_pro"`, sonst `RazerMouse`.
- `NagaV2Pro` behält: `device_mode/set_device_mode`, `repair_active_stage`, DPI-Stufen über `0x04/0x83` mit 1-basierten IDs (Hardware-Befund 2026-10-23, `docs/hardware-notes.md`), `PERSISTENT_COMMANDS`.

- [ ] **Step 1: Failing tests** – `RazerMouse` mit `FakeTransport` und zwei synthetischen `DeviceInfo` (Transaktions-ID 0x3F ohne Akku; 0x1F mit Akku):

```python
def test_txn_from_database(): ...   # request byte 1 == info.protocol["set_dpi"].txn
def test_unsupported_feature_raises_and_sends_nothing(): ...
def test_make_mouse_returns_naga_subclass_for_naga(): ...
```
- [ ] **Step 2:** FAIL. **Step 3:** Implementieren; `backup.py` auf `RazerMouse` + `supports()` umstellen (nur unterstützte Felder sichern/zurückschreiben). **Step 4:** Suite grün, inklusive aller bisherigen `test_naga_commands.py`/`test_backup.py`. **Step 5: Commit** `feat: generic RazerMouse driver; Naga V2 Pro as subclass`.

---

### Task 4: Lese-Prüfung, Schreibfreigabe, Sicherung je Gerät, Diagnose

**Files:**
- Create: `razertux/razer/probe.py`
- Modify: `razertux/backup.py` (Pfad je Gerät)
- Test: `tests/test_probe.py`

**Interfaces:**
- Produces:
  - `ProbeResult` (dataclass): `ok: bool`, `values: dict[str, Any]`, `problems: list[str]`.
  - `probe(mouse: RazerMouse) -> ProbeResult`: liest Firmware, DPI, Abfragerate (soweit unterstützt). OK nur wenn jeder Lesebefehl Status OK liefert, die Antwort Klasse/ID der Anfrage spiegelt und die Werte im gültigen Bereich liegen (DPI 100…`info.dpi_max`, Abfragerate in `info.poll_rates`, Firmware-Bytes nicht beide 0). Spec §6b.3.
  - `diagnostics(info: DeviceInfo, pid: int, result: ProbeResult, raw: list[tuple[str, bytes]]) -> str`: Klartext für GitHub-Issues (Modell, PID hex, RazerTux-Version, Befund, Rohantworten hex). Keine Seriennummer (Datenschutz) – nur „vorhanden/nicht vorhanden“.
  - `backup_path(model: str, root: Path) -> Path` = `root / "backups" / f"{model}.json"`; Naga-Altpfad `root / "mouse-backup.json"` wird in Task 6 migriert.

- [ ] **Step 1: Failing tests**

```python
def test_probe_ok_with_plausible_answers(): ...
def test_probe_fails_on_unsupported_status(): ...        # FakeTransport default=STATUS_UNSUPPORTED
def test_probe_fails_on_dpi_out_of_range(): ...          # z. B. 52582 wie am 2026-10-23 gesehen
def test_probe_fails_on_mismatched_class_id_echo(): ...  # stale buffer (Befund 2026-10-23)
def test_diagnostics_contains_pid_and_hex_but_no_serial(): ...
```
- [ ] **Step 2–4:** FAIL → implementieren → PASS. **Step 5: Commit** `feat: read probe gates writes on experimental mice`.

---

### Task 5: Eingabe verallgemeinern – Quellen, Tasten-Tabellen, Anlernen

**Files:**
- Modify: `razertux/hw/buttons.py`, `razertux/daemon/devices.py`
- Create: `razertux/hw/button_maps.py`
- Test: `tests/test_buttons.py` (erweitern), `tests/test_button_maps.py`, `tests/test_daemon_devices.py` (classify)

**Interfaces:**
- `ButtonId` (StrEnum) bekommt zusätzlich: `BACK = "back"`, `FORWARD = "forward"`, `DPI_UP = "dpi_up"`, `DPI_DOWN = "dpi_down"`, `CLUTCH = "clutch"`, `PROFILE = "profile"`, `LEARN_1` … `LEARN_16` (`"learn_1"` …). Bestehende Werte unverändert.
- `button_maps.py`:
  - `InputSignal` (frozen dataclass): `source: str`, `ev_type: int`, `code: int`.
  - `ButtonMap = dict[InputSignal, ButtonId]`; `map_for(info: DeviceInfo, learned: dict[ButtonId, InputSignal]) -> ButtonMap`.
  - Tabellen: `NAGA_V2_PRO` (geprüft, = heutiges `identify`), `NAGA_FAMILY` (ungeprüft: Quelle `if02`, KEY_1…KEY_EQUAL → side_1…side_12), `GENERIC` (Quelle `mouse`: BTN_LEFT/RIGHT/MIDDLE, BTN_SIDE → back, BTN_EXTRA → forward). Wahl: `naga_v2_pro` → NAGA_V2_PRO; Slug beginnt mit `naga` → NAGA_FAMILY + GENERIC; sonst GENERIC. Angelernte Signale überschreiben.
  - `identify(map: ButtonMap, source, ev_type, code, value) -> ButtonId | None` (Rad/Kippen wie bisher, unabhängig von der Tabelle).
- `devices.py`:
  - `source_from_links()` generisch: Endung nach dem Produktnamen → `event-mouse` → `mouse`; `ifNN-event-kbd` → `ifNN`; `ifNN-event-mouse` → `ifNN_mouse`; `event-ifNN` → `ifNN_event`.
  - `classify(props, known_pids: set[int]) -> tuple[int, str] | None` → `(pid, source)`; Vendor `1532` und PID in der Datenbank, sonst `None` (Review Focus 2).

- [ ] **Step 1: Failing tests** – Naga-Aufzeichnung (bestehende Fälle) liefert identische ButtonIds über `NAGA_V2_PRO`; DeathAdder-Fall `mouse` BTN_SIDE → `back`; angelerntes `if01` KEY_F13 → `learn_1`; `classify` mit unbekannter PID → `None`; Links `usb-Razer_Razer_DeathAdder_V2-if01-event-kbd` → `if01`.
- [ ] **Step 2–4:** FAIL → implementieren (`identify()` alt bleibt als dünner Wrapper für die Naga, bis Task 7 umstellt) → PASS. **Step 5: Commit** `feat: per-device button maps and generic input sources`.

---

### Task 6: Konfiguration Schema 2 – Profile je Modell, Geräte-Einstellungen, Migration

**Files:**
- Modify: `razertux/config/store.py`, `razertux/config/model.py`, `razertux/config/migrate.py`
- Test: `tests/test_config.py`, `tests/test_migrate.py`

**Interfaces:**
- Ablage: `profiles/<model>/<id>.json`, `devices/<model>.json` (Geräte-Einstellungen), `backups/<model>.json`, `settings.json` mit `"schema": 2` und `"active_profiles": {model: id}` (ersetzt `active_profile`; `Settings.active_profile` bleibt als Property für das Naga-Modell, damit bestehender GUI-Code bis Task 12 läuft).
- `DeviceSettings` (dataclass, `model.py`): `plate: Literal[12, 6, 2] = 12` (nur Naga V2 Pro), `remap_enabled: bool` (Standard `True` für `verified`, sonst `False`), `learned: dict[ButtonId, InputSignal]`.
- `ConfigStore(root).for_model(model) -> ModelStore` mit den bisherigen Profil-Methoden (`list_profiles`, `load_profile`, `save_profile`, `delete_profile`, `export_profile`, `import_profile`, `default_profile`) und `load_device_settings()/save_device_settings()`. Makros bleiben global (`macros.json`).
- `default_profile(info: DeviceInfo)`: Naga wie heute; andere Mäuse alle Tasten `DefaultAction`, keine Geräteeinstellungen.
- `migrate_schema_2(root: Path) -> bool`: wenn `settings.json` kein `"schema": 2` hat: `profiles/*.json` → `profiles/naga_v2_pro/`, `mouse-backup.json` → `backups/naga_v2_pro.json` (Kopie), `active_profile` → `active_profiles["naga_v2_pro"]`; alte Dateien nach `pre-schema2/` kopieren (nicht löschen). Idempotent.

- [ ] **Step 1: Failing tests** (Review Focus 4): echtes Abbild der heutigen Nutzer-Konfiguration (Profile `test`, `test2-73cb63`, Einstellungen mit `active_profile: "test2-73cb63"`, `dpi_limit`, `scroll_stage_names`, `mouse-backup.json`) → nach Migration lädt `for_model("naga_v2_pro")` beide Profile bytegleich, aktives Profil gleich, Sicherung am neuen Ort, `pre-schema2/` vorhanden; zweiter Lauf ändert nichts.
- [ ] **Step 2–4:** FAIL → implementieren → PASS. **Step 5: Commit** `feat: config schema 2 with per-model profiles`.

---

### Task 7: Dienst mit mehreren Geräten (`DeviceSession`)

**Files:**
- Create: `razertux/daemon/session.py`
- Modify: `razertux/daemon/main.py` (wird schlank: Sitzungen verwalten, Hotplug, Shutdown), `razertux/engine/mapper.py` (Konstante `_DEFAULT_DPI` nur für Naga), `razertux/daemon/devices.py` (`InputRouter` je Sitzung)
- Test: `tests/test_session.py`, `tests/test_daemon_devices.py` (anpassen)

**Interfaces:**
- `class DeviceSession(info, store: ModelStore, out, *, transport_factory, find_control, find_vendor, hidraw_reader)`: besitzt `RazerMouse`, `InputRouter`, `Mapper`, aktives Profil, `ProbeResult`, Status-Dict. Methoden: `add_input(pid, source, dev) -> bool`, `remove_input(path)`, `async activate(profile_id)`, `async reload()`, `status() -> dict`, `diagnostics() -> str`, `async start_learning() / stop_learning()` (liefert über Callback `on_learned(InputSignal)` das nächste Signal einer nicht zugeordneten Taste, ohne es weiterzugeben), `async close()`.
- Grab nur bei Bedarf (Spec §6b.1): `needs_grab(profile, device_settings, info) -> bool` – Profil belegt irgendeine Taste ≠ `DefaultAction` oder hat Makro/Hypershift, und (`info.verified` oder `remap_enabled`). Wechselt das beim Profilwechsel, wird gegriffen bzw. losgelassen.
- Schreibschutz: ist `probe` nicht OK, verwirft die Sitzung jede Geräteeinstellung (DPI, Licht, Abfragerate, Energie) und loggt einmal; Status `"writable": False`.
- Alles Naga-Spezifische aus `main.py` (Gerätemodus, Mausrad-Stufen-Meldungen, `_fix_invalid_dpi`, R17-Rückschreiben, Funk-Aufwachen, Akku-Überwachung) zieht in die Sitzung; Naga-Teile nur wenn `isinstance(mouse, NagaV2Pro)` bzw. Feature vorhanden.
- `Daemon`: `sessions: dict[str, DeviceSession]` nach Modell; Kabel und Funk desselben Modells = eine Sitzung, zuletzt verbunden gilt (Spec §6b.5).

- [ ] **Step 1: Failing tests** (Review Focus 1–3): Naga + DeathAdder (FakeTransport je PID) gleichzeitig verbinden → zwei Sitzungen, getrennte Profile; DeathAdder abziehen → Naga-Grab bleibt; unbekannte PID → keine Sitzung; DeathAdder mit Probe-Fehler → nach `activate` mit DPI-Profil kein `0x04/0x05` gesendet; Profil ohne Umbelegung → nichts gegriffen; Anlernen liefert `InputSignal("if01", EV_KEY, KEY_F13)` und gibt das Ereignis nicht aus.
- [ ] **Step 2:** FAIL. **Step 3:** `session.py` herauslösen, `main.py` umbauen. Alle bisherigen Naga-Daemon-Tests laufen über `sessions["naga_v2_pro"]` (nur Zugriffspfade in den Tests anpassen, keine Erwartungen). **Step 4:** Suite grün. **Step 5: Commit** `feat: one session per connected mouse`.

---

### Task 8: D-Bus `Daemon1` + GUI-Client + Tray

**Files:**
- Modify: `razertux/daemon/dbus_service.py`, `razertux/gui/dbus_client.py`, `razertux/gui/tray.py`, `razertux/cli.py`
- Test: `tests/test_dbus_service.py`, `tests/gui/test_dbus_client.py`, `tests/gui/test_tray.py`, `tests/gui/fake_daemon.py`

**Interfaces:**
- Interface-Name `io.github.razertux.Daemon1` (Bus-Name und Pfad bleiben `io.github.razertux`, `/io/github/razertux`). Methoden: `GetVersion() -> s`, `ListDevices() -> a(sssb)` (Modell, Name, Verbindung, verbunden), `ListProfiles(s model) -> a(ss)`, `GetActiveProfile(s model) -> s`, `ActivateProfile(s model, s id)`, `Reload()`, `GetStatus(s model) -> a{sv}` (bisherige Schlüssel + `writable`, `experimental`, `model`, `name`), `RestoreOriginal(s model) -> as`, `GetDiagnostics(s model) -> s`, `StartLearning(s model)`, `StopLearning(s model)`. Signale: `DevicesChanged()`, `ProfileChanged(s model, s id)`, `StatusChanged(s model, a{sv})`, `ButtonLearned(s model, s source, i type, i code)`.
- `DaemonClient` bekommt einen `model`-Parameter je Aufruf und `devices()`; fehlt `Daemon1` beim Dienst (alter Dienst) → `DaemonUnavailable` mit Text „Dienst-Version passt nicht – bitte RazerTux neu starten“ (Review Focus 5).
- Tray: pro verbundenem Gerät ein Untermenü „<Name>“ mit Profilen + Akku; bei nur einem Gerät wie heute flach.
- `razertux status/backup/restore` bekommen `--model` (Standard: einziges verbundenes Gerät).

- [ ] **Step 1: Failing tests** – Fake mit zwei Geräten: Tray zeigt zwei Untermenüs; `ActivateProfile("deathadder_v2", "p")` landet in der richtigen Sitzung; alter Dienst (nur `Daemon`) → Client meldet Versionshinweis, kein Traceback.
- [ ] **Step 2–4:** FAIL → implementieren → PASS. **Step 5: Commit** `feat: device-aware D-Bus interface Daemon1`.

---

### Task 9: Laser-Grafik-Motor + Layout-Format

**Files:**
- Create: `razertux/gui/laser.py`, `razertux/gui/layouts.py`, `razertux/gui/assets/layouts/` (JSON)
- Modify: `razertux/gui/mouse_scene.py`, `razertux/gui/mouse_view.py`
- Test: `tests/gui/test_laser.py`, `tests/gui/test_layouts.py`, `tests/gui/test_mouse_view.py`

**Interfaces:**
- Asset-Format (Linien-SVG): nur `path|rect|circle|ellipse|line` mit `stroke`, ohne `fill`; Tasten als `<g id="<element>">` mit genau einer Form + optional `<text>`. Platzhalter `@ACC@` für die Linienfarbe. Keine Filter, keine Bilder.
- `laserize(svg: str, accent: str, hot: str | None) -> str`: ersetzt jede Linienform durch vier Striche (Breite ×7/×4/×2.2/×1, Deckkraft 0.06/0.12/0.28/1.0) + hellen Kern (`#d8ffd0`, ×0.4, Deckkraft 0.5); Element `hot` bekommt Füllung `accent` mit `fill-opacity="0.35"`. Ergebnis gültig für `QSvgRenderer` (keine Filter).
- Layout-JSON (`assets/layouts/<model>.json` oder `<template>.json`): `{"template": "...", "views": [{"name": "top", "asset": "...svg"}, {"name": "plate", "asset": "...", "variants": {"12": "...", "6": "...", "2": "..."}}], "buttons": {"<ButtonId>": {"view": "top", "element": "wheel", "side": "left|right"}}}`.
- `layout_for(info: DeviceInfo, plate: int | None) -> Layout`: Modell-Datei → sonst Vorlage nach Zuordnung im Layout-Index (`assets/layouts/index.json`: Modell-Slug-Präfix → Vorlage) → sonst `generic.json`.
- `MouseView` arbeitet nur noch mit `Layout` (keine festen `SIDE_BUTTONS/TOP_BUTTONS`); Leitlinien-Geometrie wie bisher (Linien enden auf dem Element-Rechteck, korrekt nach Größenänderung).

- [ ] **Step 1: Failing tests** – `laserize` erzeugt für jede Form 5 Striche und genau eine Füllung für `hot`; Ergebnis lädt mit `QSvgRenderer(...).isValid()`; jedes Layout im Repo: alle `element`s existieren im Asset (`renderer.elementExists`), alle Schlüssel sind gültige `ButtonId`; bestehende Leitlinien-Tests laufen mit dem Naga-Layout.
- [ ] **Step 2–4:** FAIL → implementieren → PASS (Naga nutzt in diesem Task noch ihre alten Assets über ein Layout-JSON). **Step 5: Commit** `feat: laser line rendering and device layouts`.

---

### Task 10: Naga V2 Pro im Laser-Stil + Seitenplatten

**Files:**
- Create: `razertux/gui/assets/naga_v2_pro_top.svg` (ersetzt), `naga_v2_pro_plate12.svg`, `naga_v2_pro_plate6.svg`, `naga_v2_pro_plate2.svg`, `assets/layouts/naga_v2_pro.json`
- Delete: `naga_v2_pro_side.svg` (alte Flächen-Zeichnung)
- Modify: `razertux/gui/buttons_tab.py` (Plattenwahl), `razertux/hw/button_maps.py` (Platten-Tabellen)
- Test: `tests/gui/test_layouts.py`, `tests/gui/test_buttons_tab.py`

**Vorlage:** Entwurf vom 2026-10-23, vom Nutzer mit „passt“ freigegeben: Generator `.superpowers/sdd/2026-10-22-naga-config/naga2-mockup-generator.py`, Bild `.superpowers/sdd/2026-10-22-naga-config/naga2-mockup.png`. Formen von dort übernehmen (Draufsicht: schmale Front, rechter Buckel, Tastenfeld links, Mausrad + zwei obere Tasten in einem Streifen; 12er-Platte 3×4 versetzt; 6er-Platte 2×3 Pfeiltasten mit Beschriftung links 6/5/4, rechts 1/2/3; 2er-Platte zwei große Tasten oben/unten).

**Interfaces:**
- `DeviceSettings.plate` (Task 6) wählt die Plattengrafik und die Tasten-Tabelle. 12er: wie heute. 6er/2er: **vorläufig** dieselben Codes wie Tasten 1–6 bzw. 1–2 der 12er-Platte (Annahme, nicht prüfbar – Nutzer hat die Platten nicht mehr) – Ruling im Ledger; „Taste anlernen“ ist der Ausweg. Tasten außerhalb der gewählten Platte werden in der Liste ausgeblendet, ihre Belegungen bleiben im Profil erhalten.
- Reiter TASTEN: Auswahlliste „Seitenplatte: 12 Tasten / 6 Tasten / 2 Tasten“ (nur Naga V2 Pro), speichert sofort in `devices/naga_v2_pro.json`.

- [ ] **Step 1: Failing tests** – Layout enthält `side_1…side_12` (Platte 12), `side_1…side_6` (6), `side_1…side_2` (2), `wheel`, `top_1`, `top_2`, `left`, `right`; Plattenwechsel blendet Beschriftungen aus, Profil unverändert.
- [ ] **Step 2–4:** FAIL → SVGs zeichnen (Linien-Format aus Task 9) → PASS. Screenshots offscreen 1100×800 (`task-10-tasten-de.png`, je Platte) in den SDD-Ordner. **Step 5: Commit** `feat: Naga V2 Pro laser drawings with all three side plates`.
- Keine Hardware-Prüfung der 6er-/2er-Platte möglich (Nutzer hat die Platten nicht mehr, 2026-10-23). Ihre Tasten-Tabellen bleiben als „ungeprüft“ markiert; im Reiter TASTEN steht bei diesen Platten der Hinweis „Belegung ungeprüft – falls eine Taste nicht stimmt: Taste anlernen“.

---

### Task 11: Vorlagen 2–5, Ersatzgrafik, ~15 Modell-Layouts

**Files:**
- Create: `razertux/gui/assets/template_ergo_top.svg`, `template_ambi_top.svg`, `template_compact_top.svg`, `template_vertical_side.svg`, `template_mmo_top.svg` (Naga-Familie allgemein, 2/6/12 Tasten über Varianten), `generic_top.svg`; Layouts in `assets/layouts/` für: `deathadder_v2`, `deathadder_v3`, `deathadder_v3_pro`, `viper`, `viper_v2_pro`, `viper_mini`, `basilisk_v3`, `basilisk_v3_pro`, `cobra`, `cobra_pro`, `orochi_v2`, `naga_pro`, `naga_x`, `naga_trinity`, `naga_v2_hyperspeed` (Slugs exakt wie im generierten JSON; fehlt einer, Ruling + nächstbester Slug), `index.json`, `generic.json`.
- Test: `tests/gui/test_layouts.py`

**Interfaces:**
- Jede Vorlage hat Elemente `left`, `right`, `wheel` und je nach Form `back`, `forward`, `dpi_up`, `dpi_down`, `side_1…side_n`; Ersatzgrafik zusätzlich `learn_1…learn_8` als nummerierte Punkte an der Flanke.
- `index.json`: Präfix-Regeln (z. B. `"deathadder" → "template_ergo"`, `"basilisk" → "template_ergo"`, `"viper" → "template_ambi"`, `"orochi" → "template_compact"`, `"pro_click_v2_vertical" → "template_vertical"`, `"naga" → "template_mmo"`), alles andere → `generic`.

- [ ] **Step 1: Failing tests** – jedes Modell der Datenbank bekommt ein gültiges Layout (Schleife über `load_devices()`); jedes Layout-Element existiert; Ersatzgrafik zeigt angelernte Tasten.
- [ ] **Step 2–4:** FAIL → zeichnen (eigene Formen, keine Vorlage aus Fotos) → PASS. Eine Übersichtsgrafik aller Vorlagen offscreen rendern (`task-11-vorlagen.png`). **Step 5: Commit** `feat: laser templates for common Razer mouse shapes`.

---

### Task 12: Fenster mit Geräteauswahl, Funktions-Reitern, Experimentell-Hinweis, Taste anlernen

**Files:**
- Create: `razertux/gui/device_bar.py` (Geräteauswahl + Experimentell-Hinweis), `razertux/gui/learn_dialog.py`
- Modify: `razertux/gui/main_window.py` (≤ 400 Zeilen), `razertux/gui/header.py` (Gerätename aus Status statt `DEVICE_NAME`), `razertux/gui/lighting_tab.py` (Zonen aus `DeviceInfo`), `razertux/gui/power_tab.py`, `razertux/gui/performance_tab.py` (`dpi_max` und Abfrageraten aus `DeviceInfo`)
- Test: `tests/gui/test_main_window.py`, `tests/gui/test_device_bar.py`, `tests/gui/test_learn_dialog.py`

**Interfaces:**
- `DeviceBar(QWidget)`: Signal `deviceChanged(str model)`; zeigt verbundene Geräte als Pills (wie Synapse-Geräte-Reiter); bei experimentellem Gerät Banner „Experimentell – ungetestet“ mit Knöpfen „Diagnose kopieren“ (Text aus `GetDiagnostics` in die Zwischenablage) und Schalter „Tastenbelegung aktivieren“ (`DeviceSettings.remap_enabled`). Bei `writable == False` zusätzlich „Nur lesend: die Maus hat auf die Prüfbefehle nicht wie erwartet geantwortet.“
- Reiter nur für vorhandene Funktionen: LEISTUNG (`dpi`/`poll_rate`), BELEUCHTUNG (Zonen ≠ leer), ENERGIE (`battery`), MAUSRAD (Naga-Stufen oder Scroll-Optionen aus Task 13), TASTEN und MAKROS immer.
- `LearnDialog(client, model, button: ButtonId)`: „Drück jetzt die Taste an der Maus …“, ruft `StartLearning`, wartet auf `ButtonLearned`, speichert `learned[button]` und ruft `Reload`; Abbrechen ruft `StopLearning`. Zeitlimit 15 s.
- Kein Gerät verbunden: Fenster zeigt das zuletzt benutzte Modell (aus `settings.json: "last_model"`), Bearbeiten möglich.

- [ ] **Step 1: Failing tests** – Fake mit Naga + DeathAdder: Umschalten lädt Profile des richtigen Modells; DeathAdder ohne Akku → kein Reiter ENERGIE; Banner + Diagnose in Zwischenablage; Anlernen speichert Signal; Naga-Fenster sieht aus wie bisher (bestehende Tests).
- [ ] **Step 2–4:** FAIL → implementieren → PASS. Screenshots offscreen (`task-12-zwei-geraete.png`). **Step 5: Commit** `feat: device selector, capability tabs and learn-a-button`.

---

### Task 13: Mausrad-Optionen und Licht-Zonen anderer Mäuse

**Files:**
- Modify: `razertux/gui/scroll_tab.py`, `razertux/config/model.py` (`Profile.scroll: dict[str, int | bool]` – `mode`, `acceleration`, `smart_reel`; leer = nicht ändern), `razertux/daemon/session.py` (anwenden), `razertux/gui/lighting_tab.py`
- Test: `tests/gui/test_scroll_tab.py`, `tests/test_session.py`

**Interfaces:**
- Karte „SCROLL-OPTIONEN“ nur bei `scroll_mode`/`scroll_acceleration`/`scroll_smart_reel` im Gerät: Modus „Gerastert / Frei drehend“, „Beschleunigung“, „Smart Reel“ (je „nicht ändern“ möglich, R9).
- Naga-Karten (Stufen, Referenz) bleiben nur für `naga_v2_pro`.
- Licht: Zonen und erlaubte Effekte aus `DeviceInfo.zones`; Zonen-Namen übersetzt (`logo` „Logo“, `scroll` „Mausrad“, `thumb` „Daumenfeld“, `left` „Links“, `right` „Rechts“, `backlight` „Hintergrund“, unbekannte → Rohname).

- [ ] **Step 1–4:** Failing tests (Basilisk-V3-ähnliches Gerät zeigt alle drei Optionen, Naga nicht; Profil-Rundreise; Sitzung sendet nur bei gesetzten Werten und nur wenn `writable`) → implementieren → PASS. **Step 5: Commit** `feat: scroll options and per-device lighting zones`.

---

### Task 14: Paket, udev, Doku, Version 0.3.0

**Files:**
- Modify: `packaging/70-razertux.rules`, `packaging/PKGBUILD`, `pyproject.toml`, `razertux/__init__.py`, `README.md`, `docs/hardware-notes.md`, `tests/test_packaging.py`

**Interfaces / Werte:**
- udev: `hidraw`/`input` für alle PIDs der Datenbank (Regel wird von `tools/import_openrazer.py --udev` erzeugt, eine Zeile je Subsystem mit `ATTRS{idProduct}=="…|…"`), uinput wie bisher. Test: jede PID aus `load_devices()` steht in der Regel.
- PKGBUILD: Version `0.3.0`, `openrazer_mice.json` und `assets/layouts/*` im Paket, Lizenzhinweis OpenRazer (GPL-2.0) in `README.md` und `/usr/share/licenses/razertux/`.
- README: Abschnitt „Unterstützte Mäuse“ (Naga V2 Pro geprüft, alle anderen experimentell, wie man Diagnose meldet), Abschnitt „Laser-Grafiken“ (eigene Zeichnungen, keine Razer-Bilder).

- [ ] **Step 1–4:** Failing packaging tests → umsetzen → volle Suite grün, `makepkg -f` baut. **Step 5: Commit** `chore: package 0.3.0 for all supported mice`.
- [ ] **Step 6 (Nutzer):** `sudo pacman -U …/razertux-0.3.0-1-any.pkg.tar.zst`; Controller startet Dienst/Tray neu und prüft mit dem Nutzer: Naga-Tasten, Makro, Profilwechsel, DPI-Stufen, Mausrad-Anzeige, 12er-Platte in der neuen Grafik.
