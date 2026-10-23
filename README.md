# RazerTux

> **Kein offizielles Razer-Produkt, nicht mit Razer Inc. verbunden.**
> **Not an official Razer product; not affiliated with Razer Inc.**
> „Razer“ und „Naga“ sind Marken von Razer Inc.; RazerTux nutzt keine Razer-Logos.
>
> Projekt / Project: <https://github.com/anoraknophobia/razertux>

> **English:** RazerTux (formerly *naga-config*) is an unofficial Linux replacement for
> Razer Synapse, currently for the Razer Naga V2 Pro: a background service (`razertuxd`),
> a command line tool (`razertux`) and a Qt GUI with tray icon (`razertux-gui`) for button
> assignments, macros, Hypershift, DPI stages, polling rate, lighting and power settings.
> Install with `cd packaging && makepkg -si`, then
> `systemctl --user enable --now razertuxd.service`. The GUI is available in German,
> English, Spanish and French (*View → Language*, see below); this README is in German.

Inoffizieller Linux-Ersatz für Razer Synapse, derzeit für die Razer Naga V2 Pro und experimentell weitere Razer-Mäuse (getestet
unter CachyOS/Arch, KDE Plasma Wayland). Besteht aus einem Hintergrunddienst
(`razertuxd`), einer Kommandozeile (`razertux`) und einer Qt-Oberfläche mit Tray-Symbol
(`razertux-gui`). Früherer Name: *naga-config* (siehe „Umstieg von naga-config“).

## Funktionen

- **Tasten**: Belegungen, Tastenkombinationen, Makros, Hypershift, mehrere Profile.
- **Obere Tasten frei belegen**: Aus = Firmware schaltet Mausrad-Stufe bzw. DPI-Stufe
  weiter (Normalmodus). An = Treibermodus, die beiden oberen Tasten sind frei belegbar.
- **Leistung**: DPI und DPI-Stufen. Eine dauerhaft geschriebene DPI wird beim sauberen
  Beenden des Dienstes auf den gesicherten Wert zurückgeschrieben.
- **Beleuchtung**: Logo und Daumenfeld (das Mausrad hat keine LED).
- **Energie/Akku**: Ladestand, Leerlaufzeit, Warnung bei niedrigem Akku.
- **Mausrad**: Anzeige der aktiven Mausrad-Stufe. Die Stufenwerte selbst lassen sich nur in
  Synapse ändern.
- Heller/dunkler Stil umschaltbar.
- Oberfläche auf Deutsch, Englisch, Spanisch und Französisch.

## Installation

```
cd packaging
makepkg -si
systemctl --user enable --now razertuxd.service
```

Der Dienst startet mit der grafischen Sitzung (`graphical-session.target`) und wird
außerdem bei Bedarf per D-Bus (`io.github.razertux`) gestartet.
Das Tray-Symbol startet beim Anmelden automatisch
(`/etc/xdg/autostart/razertux-tray.desktop`).
Eine früher von Hand nach `/etc/udev/rules.d/` kopierte udev-Regel bitte entfernen; das
Paket installiert `70-razertux.rules` nach `/usr/lib/udev/rules.d/`.

Das Paket steht im Konflikt mit `openrazer-daemon` und `openrazer-driver-dkms`: Beide
greifen auf dasselbe Gerät zu und stören sich gegenseitig.

## Umstieg von naga-config

RazerTux ersetzt das Paket `naga-config` (`replaces`/`conflicts` im PKGBUILD); `makepkg -si`
bzw. pacman entfernt es dabei. Danach einmal **als normaler Benutzer** (nicht mit sudo)
den alten Dienst ab- und den neuen anmelden:

```
systemctl --user disable --now naga-configd.service 2>/dev/null
systemctl --user enable --now razertuxd.service
```

Beim ersten Start von Dienst oder Oberfläche wird `~/.config/naga-config/` vollständig nach
`~/.config/razertux/` kopiert (Profile, Makros, Einstellungen, Sicherung
`mouse-backup.json`). Der alte Ordner bleibt unverändert und kann danach von Hand gelöscht
werden; ein vorhandenes `~/.config/razertux/` wird nie überschrieben. Ein noch laufendes
altes Tray-Symbol einfach beenden (oder ab- und wieder anmelden).

## Erste Schritte

1. Maus per Funk-Dongle oder USB verbinden und `razertux-gui` starten (oder das
   Tray-Symbol anklicken). Beim ersten Verbinden sichert der Dienst den Ausgangszustand
   der Maus automatisch nach `~/.config/razertux/mouse-backup.json` (eine vorhandene
   Sicherung wird nie überschrieben).
2. Profil anlegen, Tasten, DPI und Beleuchtung einstellen. Profile lassen sich im Tray
   wechseln.

Kommandozeile: `razertux status` zeigt den Maus-Zustand, `razertux restore` stellt
den gesicherten Zustand wieder her. `razertux backup` legt die Sicherung von Hand an
(optional, nur nötig ohne laufenden Dienst; überschreibt ebenfalls nichts).

## Sprache / Language

*Ansicht → Sprache* (auch im Tray-Menü): Systemsprache, Deutsch, English, Español, Français.
„Systemsprache“ nimmt die Sprache des Systems und, wenn sie nicht dabei ist, Englisch. Die
Oberfläche übernimmt eine neue Sprache nach einem Neustart („Jetzt neu starten“ startet sie
sofort neu); der Dienst übernimmt sie sofort (Akku-Warnung, Konfigurationsfehler). Die
Einstellung steht als `"language"` in `~/.config/razertux/settings.json`.

*View → Language* (also in the tray menu): system language, German, English, Spanish,
French. "System language" falls back to English if the system language is not supported.
The GUI switches after a restart ("Restart now"); the service switches immediately.

Übersetzungen liegen als JSON in `razertux/i18n/<lang>.json`
(`{"Kontext": {"deutscher Text": "Übersetzung"}}`); `tests/test_i18n.py` prüft, dass jeder
Text übersetzt ist und die Platzhalter stimmen.

## Originalzustand wiederherstellen

```
razertux restore
```

(oder „Originalzustand wiederherstellen“ im Tray) schreibt die gesicherten Werte zurück:
DPI, DPI-Stufen, Abfragerate, Helligkeit und die Energie-Einstellungen (Leerlaufzeit,
Akku-Energiesparschwelle). Effekte und Farben gehören nicht dazu; die setzt die Maus beim
Aus- und wieder Einstecken selbst zurück. Mit `razertux status` lässt sich das Ergebnis
gegen die Sicherung prüfen. Die Sicherung liegt in `~/.config/razertux/mouse-backup.json`.

## Deinstallation

```
razertux restore
systemctl --user disable --now razertuxd.service
sudo pacman -Rns razertux
rm -r ~/.config/razertux
```

`restore` vor der Deinstallation ausführen, solange der Dienst noch da ist. Das Entfernen
von `~/.config/razertux` löscht auch die Sicherung und die Profile.

## Unterstützte Mäuse

- **Razer Naga V2 Pro**: geprüft (Funk-Dongle und USB), voller Funktionsumfang.
- **Alle anderen Razer-Mäuse aus der OpenRazer-Datenbank** (rund 117 Produkt-IDs, siehe
  `razertux/devices/openrazer_mice.json`): **experimentell**. RazerTux liest zuerst nur
  (Lese-Probe) und legt eine Sicherung des Gerätezustands an, bevor etwas geschrieben
  wird. Auf diesen Mäusen sind DPI, Abfragerate und Ähnliches abhängig vom Modell
  verfügbar; ungetestet heißt: kein Gewähr.
- Probleme melden: In der Oberfläche **„Diagnose kopieren“** wählen und den Text in ein
  GitHub-Issue (<https://github.com/anoraknophobia/razertux/issues>) einfügen.
- Die Datenbank stammt aus OpenRazer (GPL-2.0), siehe `OPENRAZER-NOTICE` (installiert
  nach `/usr/share/licenses/razertux/`). Die udev-Regel `70-razertux.rules` wird daraus
  mit `python tools/import_openrazer.py --udev` erzeugt.

## Laser-Grafiken

Die Maus-Ansichten mit den Tasten-Beschriftungen („Laser“-Linien) sind eigene
Zeichnungen (SVG-Vorlagen im Paket). Es werden keine Razer-Bilder verwendet.

## Bildschirmanzeige (OSD)

Beim Profilwechsel und bei DPI-Stufenwechsel zeigt RazerTux kurz eine Bestätigung auf dem
Bildschirm. Im Tray-Menü lässt sich die Anzeige ein- und ausschalten.

## Update auf 0.3.0

Die Konfiguration wird beim ersten Start automatisch auf Schema 2 umgestellt. Die alten
Dateien bleiben unverändert im Unterordner `pre-schema2/` (in `~/.config/razertux/`)
erhalten.

> **English summary (0.3.0):** Naga V2 Pro is verified; all other OpenRazer mice are
> experimental (read probe and backup before any write; use "Diagnose kopieren" /
> copy diagnostics for GitHub issues). Laser drawings are original, no Razer images. An
> on-screen display (OSD) confirms profile/DPI changes and can be toggled in the tray. The
> config migrates automatically to schema 2; old files are kept in `pre-schema2/`. The
> device database is derived from OpenRazer (GPL-2.0), see `OPENRAZER-NOTICE`.

## Bekannte Grenzen

- Bluetooth: nur normale Eingabe, keine Konfiguration. Dafür Funk-Dongle oder USB nutzen.
- Stufenwerte des Mausrads (Haptik) sind nicht änderbar, nur die aktive Stufe wird angezeigt.
- `kill -9` auf den Dienst überspringt das Aufräumen. systemd startet den Dienst neu; das
  betrifft nur „Obere Tasten frei belegen“ (Treibermodus): bis zum Neustart reagieren die
  oberen Tasten nicht, danach stellt der Dienst den Modus wieder passend zum Profil ein.
  Ohne laufenden Dienst hilft Aus- und wieder Einstecken der Maus.
- `dbus-next` ist ab Python 3.15 veraltet (deprecation); dann ist ein Ersatz nötig.
- Parallel laufender OpenRazer-Dienst wird nicht unterstützt (Paketkonflikt).
- Weitere Details zur Hardware: `docs/hardware-notes.md`.

## Danksagung / Credits

Das Wissen über das HID-Protokoll der Razer-Mäuse stammt zu einem großen Teil aus
[OpenRazer](https://github.com/openrazer/openrazer) (GPL-2.0). Vielen Dank an das
OpenRazer-Projekt. Das Programmsymbol (Pinguin) ist eine eigene Zeichnung.

Protocol knowledge comes largely from OpenRazer (GPL-2.0); thanks to the OpenRazer
project. The app icon (penguin) is an original drawing.

## License

This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version. See `LICENSE` for the GPL-2.0 text.
