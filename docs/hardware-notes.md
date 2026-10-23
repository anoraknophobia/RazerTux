# Hardware-Notizen

## NOSTORE-Prüfung der Schreibbefehle (Task 7)

Alle Set-Befehle mit Speicher-Flag senden `NOSTORE` (0x00) an Arg-Index 0; nur
`restore_backup` nutzt `VARSTORE` (0x01). Befehle ohne Speicher-Flag stehen in
`PERSISTENT_COMMANDS` (`razertux/razer/naga_v2_pro.py`).

Prüfverfahren je Befehl: Wert setzen, Dienst gestoppt, Maus ab- und wieder anstecken,
Wert per `razertux status` bzw. Sichtprüfung vergleichen. Ergebnis hier eintragen.

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

## Ungeprüft (0.3.0)

- Platten 6/2 der Naga-Grafik (6er- und 2er-Seitenplatte) sind eine Annahme: das Layout
  wurde aus der 12er-Platte abgeleitet, nicht mit der Hardware gegengeprüft.
- Der generische Treiber (Datenbank aus OpenRazer) wurde nur mit der Naga V2 Pro
  gegengeprüft; auf anderer Hardware ist er ungetestet.

## Mausrad-Haptik (Vorfall 2026-10-22)

- Im Treibermodus (0x03) fühlt sich das Rad anders an; die Maus erwartet offenbar, dass der Host die Haptik setzt.
- Nach Tests war die Haptik auch im Normalmodus und nach Abstecken verändert (vorher: sehr fein und knackig; jetzt: schwächer, gröber).
  Verdacht: unbestätigter Befehl 0x02/0x98 (aus OpenRazer-PR #2741) schreibt statt zu lesen. Seitdem gesperrt (R16) bis Task 11 – aufgeklärt, siehe „Mausrad-Stufen“ unten.

## Mausrad-Stufen (Task 11, mit dem Nutzer am Gerät ermittelt)

- **Normalmodus (0x00):** Die obere Taste **vorne** (TOP_1) schaltet in der Firmware die
  Mausrad-Haptik-Stufe weiter, die **hintere** (TOP_2) die DPI-Stufe. Die Firmware meldet
  das als Input-Report auf dem if01-hidraw-Knoten (derselbe, den der Vendor-Reader liest):
  - Stufe gewechselt: `05 3a <slot> <preset>` – beobachtet `02 81`, `03 82`, `04 84`,
    `05 85`, `06 86`. Slot 6 ist "Ultra-fine" (vom Nutzer bestätigt); die Zuordnung der
    übrigen Slots zu Synapse-Namen ist unbekannt.
  - DPI gewechselt: `05 02 <x hi> <x lo> <y hi> <y lo>` (z. B. `05 02 09 c4 09 c4` = 2500).
  - Ignoriert: `05 31 …` (periodisch), `05 0e …`.
- **Treibermodus (0x03):** TOP_1/TOP_2 senden `04 57` / `04 52` (Loslassen `04 00`), die
  Firmware schaltet dann **keine** Stufen um. Die Rad-Haptik bleibt, wie sie ist (vom Nutzer
  geprüft).
- Ein Befehl zum Lesen oder Setzen des aktiven Slots ist nicht bekannt. `0x02/0x98` gibt nur
  sein Argument zurück (harmlos, nutzlos); `NagaV2Pro.scroll_profile()` und die R16-Sperre
  sind entfernt.
- Synapse-Referenzwerte (Widerstand/Rasten): Ultra-fine 65/96, Standard 60/24,
  Distinct 58/15, Adaptive 75/24, Smooth Scroll 0/32, Custom 50/90.

Umsetzung: Der Gerätemodus folgt dem aktiven Profil (`remap_top_buttons`: an → Treibermodus,
aus → Normalmodus; bei Verbinden, Profilwechsel und Reload), beim sauberen Beenden immer
Normalmodus. Im Normalmodus ignoriert der Dienst die Belegungen von TOP_1/TOP_2. Der Dienst
wertet `05 3a`/`05 02` in beiden Modi aus (Status `scroll_stage`, `dpi`) und merkt sich den
letzten Slot in `~/.config/razertux/state.json`.

## DPI-Stufen in der Maus (2026-10-23)

- Die Stufen-Tabelle, die die obere hintere Taste durchschaltet, liest `0x04/0x83` (VARSTORE, Größe 0x26):
  `[store, aktive Stufen-ID, Anzahl, je Stufe: ID x_hi x_lo y_hi y_lo 00 00]`, IDs 1-basiert (von Synapse geschrieben).
  `0x04/0x06` schreibt dieselbe Tabelle. `0x04/0x86` (OpenRazer) zeigt nur 1 Stufe.
- Beobachtet: aktive ID 0x0b bei 5 Stufen → Maus lief mit 52582×5696 DPI. Ursache vermutlich eine Lese-Suche
  über unbekannte Befehle der Klasse 0x04. Seit 0.2.1 korrigiert der Dienst eine gemeldete DPI außerhalb
  100–30000 sofort (`repair_active_stage` + `set_dpi`).
- Auf Wunsch des Nutzers umsortiert: 800, 1600, 2500 (aktiv), 3200, 6400.

## Mausrad-Stufen: Suche nach Setz-Befehl (2026-10-23)

- Meldung `05 3a <slot> <0x80|preset>`, Preset-IDs: Standard 1, Distinct 2, Ultra-fine 3, Adaptive 4, Smooth 5, Custom 6.
- `0x02/0x18` (OpenRazer-PR #2741) → Status 0x05 (nicht unterstützt). Kein Lese-Befehl liefert Tension/Steps.
  Nächster Schritt: USB-Mitschnitt unter Windows.
