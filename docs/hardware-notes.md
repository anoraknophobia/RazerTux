# Hardware-Notizen

## NOSTORE-Prüfung der Schreibbefehle (Task 7)

Alle Set-Befehle mit Speicher-Flag senden `NOSTORE` (0x00) an Arg-Index 0; nur
`restore_backup` nutzt `VARSTORE` (0x01). Befehle ohne Speicher-Flag stehen in
`PERSISTENT_COMMANDS` (`naga_config/razer/naga_v2_pro.py`).

Prüfverfahren je Befehl: Wert setzen, Dienst gestoppt, Maus ab- und wieder anstecken,
Wert per `naga-config status` bzw. Sichtprüfung vergleichen. Ergebnis hier eintragen.

| Befehl | NOSTORE honored? | Notizen |
|---|---|---|
| set_dpi (0x04/0x05) | **nein** | 2026-10-22: NOSTORE 3000 gesetzt → nach Abstecken weiterhin 3000. DPI wirkt dauerhaft; Dienst muss beim Beenden die gesicherte DPI zurückschreiben |
| set_dpi_stages (0x04/0x06) | ungeprüft | vorsichtshalber wie set_dpi als dauerhaft behandeln |
| set_effect off/static/breath/spectrum/wave/reactive (0x0F/0x02), Logo | ja (static geprüft) | Statisch rot ohne Mode-Switcher sichtbar; nach Abstecken Original zurück. Andere Effekte ungeprüft |
| set_effect, Thumb | ja (static geprüft) | Statisch blau sichtbar, nach Abstecken Original |
| set_brightness (0x0F/0x04), Logo | ja | 255 sichtbar, gespeicherter Wert blieb 191 |
| set_brightness, Thumb | ja | wie Logo |
| set_poll_rate (0x00/0x05) | n/a (kein Flag) | persistent, per `restore` zurücksetzen |
| set_idle_time (0x07/0x03) | n/a (kein Flag) | persistent |
| set_low_battery_threshold (0x07/0x01) | n/a (kein Flag) | persistent |
| restore (CLI/D-Bus, VARSTORE) | ok (CLI, Hidraw-Pfad) | status danach = Sicherung (dpi 1800, Stufe 2500, 500 Hz, 191/191, 300 s, 5 %) |

## Offene Annahmen

- Statisch: OpenRazer sendet beim geräteweiten Static (alle LEDs) zuerst einen
  "Mode-Switcher" (0x0F/0x02, Args `00 00 08 00 00 00`) und dann `0x0F/0x03` (Trinity).
  Der Pro-LED-Pfad (`razer_attr_write_matrix_effect_static_common`) nutzt nur
  `0x0F/0x02` mit LED-ID. Wir senden den Pro-LED-Pfad ohne Vorbericht. Falls Statisch
  keine Wirkung zeigt: Vorbericht testweise ergänzen.
- Wave-Richtung 1 und Reactive-Geschwindigkeit 2 sind Standardwerte (`WAVE_DIRECTION`,
  `REACTIVE_SPEED`); OpenRazer erlaubt Wave 0..2, Reactive 1..4.
- Akku-Schwelle: Rohwert = Prozent*255//100 (5 % = 0x0C, 15 % = 0x26, 25 % = 0x3F).
- OpenRazer ignoriert beim DPI-Setzen sein Speicher-Argument und sendet immer VARSTORE;
  wir senden NOSTORE (Layout sonst identisch). Falls die Maus NOSTORE bei 0x04/0x05
  ablehnt, steht das hier.

## Mausrad-Haptik (Vorfall 2026-10-22)

- Im Treibermodus (0x03) fühlt sich das Rad anders an; die Maus erwartet offenbar, dass der Host die Haptik setzt.
- Nach Tests war die Haptik auch im Normalmodus und nach Abstecken verändert (vorher: sehr fein und knackig; jetzt: schwächer, gröber).
  Verdacht: unbestätigter Befehl 0x02/0x98 (aus OpenRazer-PR #2741) schreibt statt zu lesen. Seitdem gesperrt (R16) bis Task 11.
