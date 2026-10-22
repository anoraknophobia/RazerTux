# naga-config – Linux-Ersatz für Razer Synapse (Razer Naga V2 Pro)

Stand: 2026-10-22 · Status: wartet auf Freigabe durch den Nutzer

## 1. Absicht und Warum

Der Nutzer hat eine **Razer Naga V2 Pro** und nutzt **CachyOS mit KDE Plasma unter Wayland**. Unter Windows/Mac konfiguriert er sie mit Razer Synapse. Unter Linux fehlt ein Programm, das das kann, was er braucht – vor allem Seitentasten frei belegen, Profile, Hypershift und die haptische Mausrad-Rasterung (HyperScroll Pro). OpenRazer und Polychromatic decken das nicht ab (keine Tastenbelegung, kein HyperScroll).

Ziel: **ein einziges Programm**, das Synapse für diese Maus ersetzt und auch OpenRazer überflüssig macht. Der Nutzer will nicht zwei Tools parallel bedienen.

Erfolg heißt: Er steckt die Naga ein, öffnet das Programm, belegt seine Tasten wie in Synapse, wählt ein Profil – und es funktioniert in allen Programmen und Spielen.

## 2. Ziele

1. **Tastenbelegung** für alle Tasten der Maus:
   - 12 Seitentasten (Daumenplatte; die Maus hat wechselbare Platten mit 2/6/12 Tasten – alle Varianten müssen gehen)
   - Obere Tasten, Mausrad hoch/runter, Rad-Klick, Rad-Kippen links/rechts (sofern vorhanden), Links-/Rechtsklick
   - Sicherung: Linksklick kann nicht weggelegt werden, ohne dass er auf einer anderen Taste (oder in Hypershift) erhalten bleibt – Nutzer darf sich nicht aussperren.
2. **Aktionstypen pro Taste** (wie Synapse):
   - Einzelne Taste (Tastatur oder Maustaste)
   - Tastenkombination (z. B. Strg+Shift+M)
   - Makro
   - „Standard“ (Originalfunktion) und „Deaktiviert“
   - Hypershift-Taste (siehe 4)
3. **Makros** wie in Synapse:
   - Live-Aufnahme von Tastatur- und Maustasten inkl. Pausen, danach bearbeitbar (Schritte einfügen, löschen, verschieben, Pausen ändern)
   - Wiedergabe-Modi: einmal · N-mal wiederholen · wiederholen solange gedrückt · an/aus umschalten
   - Makros gehören global zum Programm und können in jedem Profil verwendet werden
4. **Hypershift**: Eine frei wählbare Taste wird zur Hypershift-Taste. Solange gedrückt, gilt für alle anderen Tasten eine zweite Belegung. Im Fenster per Umschalter „Standard | Hypershift“ bearbeitbar.
5. **Profile**:
   - Beliebig viele, frei benennbar, anlegen/umbenennen/duplizieren/löschen
   - Ein Profil enthält: Tastenbelegung (Standard + Hypershift), Mausrad-Rasterung, DPI-Einstellungen, Beleuchtung
   - Wechsel **nur manuell** – im Programmfenster oder über das Tray-Symbol. **Kein** automatischer Wechsel nach Anwendung, **kein** Wechsel per Maustaste.
   - Export/Import eines Profils als Datei
6. **Mausrad-Rasterung (HyperScroll Pro)** pro Profil:
   - Die 6 Voreinstellungen aus Synapse: Standard, Distinct, Precise, Adaptive, Smooth, Custom
   - Custom mit Widerstand (0–100) und Rastern pro Umdrehung (8–96) – **nur falls das Protokoll dafür gefunden wird** (siehe 7)
7. **Leistung** pro Profil: DPI (100–30.000) mit bis zu 5 DPI-Stufen inkl. aktiver Stufe; Abfragerate 125/500/1000 Hz.
8. **Beleuchtung** pro Profil: Zonen Logo, Mausrad, Daumenfeld getrennt; Helligkeit; Farbe; Effekte Aus, Statisch, Atmen, Spektrum, Welle, Reaktiv.
9. **Akku** (bei Funkbetrieb):
   - Ladestand + Ladezustand im Tray-Symbol und im Fenster
   - Desktop-Benachrichtigung bei schwachem Akku (Schwelle einstellbar)
   - Ruhezeit (Idle-Timeout) und Low-Battery-Schwelle der Maus einstellbar
10. **Kabel und Funk**: Funktioniert mit Kabel (USB-PID `0x00A7`) und Funk-Dongle (`0x00A8`). Bluetooth ist wünschenswert, aber kein Muss (siehe Abschnitt 6).

## 3. Nicht-Ziele

- Automatischer Profilwechsel nach aktiver Anwendung
- Profilwechsel per Maustaste
- Speichern von Profilen im Speicher der Maus (Onboard-Profile)
- Andere Razer-Geräte als die Naga V2 Pro (Code darf erweiterbar sein, wird aber nicht dafür gebaut/getestet)
- Chroma-Synchronisation mit anderen Geräten, Spiele-Integrationen, Cloud-Konto

## 4. Anti-Ziele (wäre ein Fehlschlag, auch wenn es technisch läuft)

- Spürbare Eingabeverzögerung oder verschluckte Tastendrücke in Spielen
- Maus nach Absturz des Dienstes unbenutzbar (Tasten bleiben „gefangen“) – der Dienst muss bei Absturz/Beenden die Maus wieder freigeben
- Nutzer muss Konfigurationsdateien von Hand bearbeiten
- Programm braucht dauerhaft Root-Rechte
- Zwei Tools nötig (z. B. weiterhin OpenRazer für Licht)
- Aussehen wie ein Bastel-Tool – es soll sich wie ein ordentliches Desktop-Programm anfühlen

## 5. Entscheidungen des Nutzers

- Ein eigenes, neues Programm; ersetzt Synapse **und** OpenRazer für diese Maus
- Funktionsumfang wie oben (Tasten, Kombinationen, Makros wie Synapse, Hypershift, Profile, Mausrad-Rasterung, DPI, Beleuchtung, Akku)
- Profilwechsel nur manuell in der Software
- **Fensteraufbau Variante A „Synapse-Stil“**: Oben Profil-Auswahl, Reiter und Umschalter „Standard | Hypershift“. Im Reiter „Tasten“ ein Bild der Maus in der Mitte, die Belegungen als anklickbare Beschriftungen links/rechts drumherum (Seitentasten auf einer Seite, obere Tasten/Rad auf der anderen). Klick auf eine Beschriftung öffnet die Auswahl der Aktion.
  - Reiter: **Tasten · Leistung · Beleuchtung · Mausrad · Makros** (+ Akku-Anzeige in der Kopfzeile)

- **Maus bleibt während des Baus am Rechner angeschlossen** → Gerätetests laufen fortlaufend während der Entwicklung, nicht in wenigen Sitzungen. Erkannt (Kabel): `1532:00a7`, Schnittstellen: Maus (`event18`, `hidraw2`), if01 Tastatur (`event20`/`event21`, `hidraw4`), if02 Tastatur (`event19`, `hidraw3`).
- **Nutzer nutzt die Maus gelegentlich weiter unter Windows mit Synapse** → Das Programm darf die in der Maus gespeicherten Einstellungen nicht dauerhaft überschreiben (Details in Abschnitt 6 „Umgang mit dem Maus-Speicher“).
- **Vor den ersten Tests den aktuellen Zustand der Maus sichern und nach den Tests wiederherstellen** (DPI-Stufen, Abfragerate, Licht, Akku-Einstellungen, Mausrad-Profil – alles, was lesbar ist).
- **Zielgruppe**: zuerst eine laufende Version **nur für den eigenen Rechner** (CachyOS, KDE Plasma, Wayland). Eventuell später Veröffentlichung auf GitHub → Code sauber, mit README und Lizenz, aber **keine** Tests/Anpassungen für GNOME, X11 oder andere Distributionen in dieser Version.

## 6. Entscheidungen von Claude (änderbar)

- **Umgang mit dem Maus-Speicher**: Einstellungen werden, wo die Maus es unterstützt, **flüchtig** gesetzt (Speicher-Flag `NOSTORE` statt `VARSTORE` im Razer-Report). Wo nur dauerhaftes Setzen geht, wird der vorherige Wert beim ersten Start gesichert. Der Dienst setzt die Werte des aktiven Profils bei jedem Anschließen/Aufwachen der Maus neu. Beim Beenden des Dienstes bleibt die Maus im zuletzt gesetzten Zustand; der Menüpunkt **„Originalzustand der Maus wiederherstellen“** schreibt die gesicherten Werte zurück. Ob die Naga `NOSTORE` für jeden Befehl beachtet, wird an der Maus geprüft (vorher Sicherung!).
- **Makro-Aufnahme nur im Programmfenster** (Qt-Tastenereignisse + Klicks der Naga selbst). Kein Lesezugriff auf andere Tastaturen → das Programm kann nicht als Keylogger missbraucht werden; die udev-Regel bleibt auf Naga + `/dev/uinput` beschränkt.
- **Lesen vor Schreiben**: Bei allen unbekannten Befehlen (v. a. Mausrad) zuerst nur lesende Befehle testen.

- **Architektur aus zwei Teilen**:
  1. **Hintergrunddienst** (systemd-User-Service, startet beim Login): liest die Eingabegeräte der Naga exklusiv (evdev `EVIOCGRAB`), setzt die Belegung um und gibt Ergebnisse über ein virtuelles Eingabegerät (uinput) aus. Er spricht außerdem per hidraw (90-Byte-Razer-Feature-Reports) mit der Maus für DPI, Licht, Akku und Mausrad. Er ist die **einzige** Stelle, die mit der Maus redet.
  2. **Programmfenster** (GUI) + **Tray-Symbol**: bearbeitet die Konfiguration und spricht mit dem Dienst über D-Bus (Session-Bus). Änderungen gelten sofort, ohne Neustart.
- **Sprache/Bibliotheken**: Python 3, PySide6 (Qt 6, passt zu KDE), python-evdev für Eingaben/uinput, D-Bus über Qt (QtDBus) oder dbus-next.
- **Konfiguration**: lesbare Dateien (JSON oder TOML) in `~/.config/naga-config/` – eine Datei pro Profil, eine für Makros, eine für globale Einstellungen (aktives Profil).
- **Rechte**: Eine udev-Regel gibt dem angemeldeten Nutzer Zugriff auf die hidraw-/event-Geräte der Naga (Vendor `1532`, PIDs `00A7`/`00A8`) und auf `/dev/uinput`. Einmalige Einrichtung mit Passwort; danach kein Root.
- **Mausbild**: eigene, schlichte Vektorgrafik (SVG) der Naga V2 Pro – keine Razer-Bilder (Urheberrecht).
- **Paket**: PKGBUILD für CachyOS/Arch, Installation per `pacman`/`makepkg`. Enthält udev-Regel und systemd-User-Unit.
- **Protokollwissen**: Befehle für DPI, Abfragerate, Licht und Akku aus dem OpenRazer-Treiber übernehmen (dort für die Naga V2 Pro vorhanden und erprobt). Nur das *Wissen* über die Bytes wird übernommen; OpenRazer ist GPL-2.0 – Lizenz des Projekts daher **GPL-2.0-or-later**, um auf der sicheren Seite zu sein.
- **Bauliche Reihenfolge** (damit das Wichtigste zuerst nutzbar ist):
  1. Dienst + Tastenbelegung + Profile + Fenster (Reiter Tasten)
  2. Makros + Hypershift
  3. Leistung (DPI), Beleuchtung, Akku + Tray
  4. Mausrad-Rasterung (Forschungsteil, siehe 7)
  5. Paket
- **Sprache der Oberfläche**: Deutsch (Texte so angelegt, dass Übersetzen später möglich ist).
- **Bluetooth**: nur Tastenbelegung (läuft über evdev auch bei Bluetooth); Licht/DPI/Akku/Mausrad über Bluetooth sind nicht Teil der ersten Version.

## 7. Bekannte Risiken und offene Forschung

**Mausrad-Rasterung (HyperScroll Pro)** ist nicht öffentlich dokumentiert.
- Spur: Ein zurückgezogener OpenRazer-Vorschlag (PR #2741) nutzt Befehlsklasse `0x02`, ID `0x18` (setzen) / `0x98` (lesen), Argumente `[VARSTORE, profil 0–5]`. Der Autor schrieb, es habe „nicht richtig funktioniert“. Unbestätigt.
- Die Mac-Synapse im Ordner `~/REA` enthält die gerätespezifischen Module **nicht** (werden zur Laufzeit geladen) – daraus ist nichts zu gewinnen.
- Vorgehen: An der angeschlossenen Maus vorsichtig testen (zuerst nur **lesende** Befehle, dann setzen). Falls nötig: Nutzer zeichnet unter Windows mit Wireshark/USBPcap auf, was Synapse beim Ändern der Rasterung sendet.
- **Wenn es nicht klappt**, wird der Reiter „Mausrad“ ausgeblendet bzw. als „noch nicht unterstützt“ markiert. Alles andere bleibt voll funktionsfähig.

**Weitere Risiken**
- Die Seitentasten senden ab Werk Tastaturcodes (1–0, -, =) über eine eigene Tastatur-Schnittstelle. Welche Codes genau und über welche Schnittstelle, muss an der echten Maus geprüft werden (auch für 2er/6er-Platte).
- Funk-Dongle: Razer-Befehle über den Dongle nutzen teils eine andere Transaktions-ID als per Kabel; aus OpenRazer übernehmen und an der Maus prüfen.
- Gleichzeitige Nutzung mit OpenRazer würde kollidieren – OpenRazer darf nicht installiert sein (ist es aktuell nicht). Das Programm soll warnen, wenn der OpenRazer-Treiber geladen ist.

## 8. Tests

- Logik (Belegung, Makro-Ablauf, Hypershift, Profil-Laden/Speichern, Report-Bau) mit automatischen Tests ohne Maus.
- Echte Maus: ist dauerhaft angeschlossen; Gerätetests laufen während der Entwicklung. Für Dinge, die nur der Nutzer prüfen kann (Haptik des Mausrads, Tastendruck), kurze Rückfragen an ihn.

## 9. Dem Builder überlassen

- Genaues Dateiformat der Konfiguration, D-Bus-Schnittstelle, interne Modulstruktur
- Details des Makro-Editors (Layout), solange Funktionen aus Ziel 3 da sind
- Gestaltung des Mausbilds und Farbschema (soll zu KDE passen, hell/dunkel)
- Name der ausführbaren Datei / des Pakets (Arbeitstitel `naga-config`)
