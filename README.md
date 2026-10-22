# naga-config

> **English:** Linux replacement for Razer Synapse for the Razer Naga V2 Pro: a background
> service (`naga-configd`), a command line tool (`naga-config`) and a Qt GUI with tray icon
> (`naga-config-gui`) for button assignments, macros, Hypershift, DPI stages, polling rate,
> lighting and power settings. Install with `cd packaging && makepkg -si`, then
> `systemctl --user enable --now naga-configd`. The GUI is available in German, English,
> Spanish and French (*View → Language*, see below); this README is in German.

Linux-Ersatz für Razer Synapse für die Razer Naga V2 Pro (getestet unter CachyOS/Arch,
KDE Plasma Wayland). Besteht aus einem Hintergrunddienst (`naga-configd`), einer
Kommandozeile (`naga-config`) und einer Qt-Oberfläche mit Tray-Symbol (`naga-config-gui`).

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
systemctl --user enable --now naga-configd
```

Der Dienst startet mit der grafischen Sitzung (`graphical-session.target`) und wird
außerdem bei Bedarf per D-Bus gestartet. Wer ihn mit einer älteren Version schon aktiviert
hatte, aktiviert ihn einmal neu: `systemctl --user reenable naga-configd`.
Das Tray-Symbol startet beim Anmelden automatisch
(`/etc/xdg/autostart/naga-config-tray.desktop`).
Eine früher von Hand nach `/etc/udev/rules.d/` kopierte Regel `70-naga-config.rules`
bitte entfernen; das Paket installiert sie nach `/usr/lib/udev/rules.d/`.

Das Paket steht im Konflikt mit `openrazer-daemon` und `openrazer-driver-dkms`: Beide
greifen auf dasselbe Gerät zu und stören sich gegenseitig.

## Erste Schritte

1. Maus per Funk-Dongle oder USB verbinden und `naga-config-gui` starten (oder das
   Tray-Symbol anklicken). Beim ersten Verbinden sichert der Dienst den Ausgangszustand
   der Maus automatisch nach `~/.config/naga-config/mouse-backup.json` (eine vorhandene
   Sicherung wird nie überschrieben).
2. Profil anlegen, Tasten, DPI und Beleuchtung einstellen. Profile lassen sich im Tray
   wechseln.

Kommandozeile: `naga-config status` zeigt den Maus-Zustand, `naga-config restore` stellt
den gesicherten Zustand wieder her. `naga-config backup` legt die Sicherung von Hand an
(optional, nur nötig ohne laufenden Dienst; überschreibt ebenfalls nichts).

## Sprache / Language

*Ansicht → Sprache* (auch im Tray-Menü): Systemsprache, Deutsch, English, Español, Français.
„Systemsprache“ nimmt die Sprache des Systems und, wenn sie nicht dabei ist, Englisch. Die
Oberfläche übernimmt eine neue Sprache nach einem Neustart („Jetzt neu starten“ startet sie
sofort neu); der Dienst übernimmt sie sofort (Akku-Warnung, Konfigurationsfehler). Die
Einstellung steht als `"language"` in `~/.config/naga-config/settings.json`.

*View → Language* (also in the tray menu): system language, German, English, Spanish,
French. "System language" falls back to English if the system language is not supported.
The GUI switches after a restart ("Restart now"); the service switches immediately.

Übersetzungen liegen als JSON in `naga_config/i18n/<lang>.json`
(`{"Kontext": {"deutscher Text": "Übersetzung"}}`); `tests/test_i18n.py` prüft, dass jeder
Text übersetzt ist und die Platzhalter stimmen.

## Originalzustand wiederherstellen

```
naga-config restore
```

(oder „Originalzustand wiederherstellen“ im Tray) schreibt die gesicherten Werte zurück:
DPI, DPI-Stufen, Abfragerate, Helligkeit und die Energie-Einstellungen (Leerlaufzeit,
Akku-Energiesparschwelle). Effekte und Farben gehören nicht dazu; die setzt die Maus beim
Aus- und wieder Einstecken selbst zurück. Mit `naga-config status` lässt sich das Ergebnis
gegen die Sicherung prüfen. Die Sicherung liegt in `~/.config/naga-config/mouse-backup.json`.

## Deinstallation

```
naga-config restore
systemctl --user disable --now naga-configd
sudo pacman -Rns naga-config
rm -r ~/.config/naga-config
```

`restore` vor der Deinstallation ausführen, solange der Dienst noch da ist. Das Entfernen
von `~/.config/naga-config` löscht auch die Sicherung und die Profile.

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

## License

This program is free software; you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation; either version 2 of the License, or (at your option) any later
version. See `LICENSE` for the GPL-2.0 text.
