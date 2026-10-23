# RazerTux – Weitere Razer-Mäuse (Konzept-Entwurf)

Stand: 2026-10-23 · Status: **vom Nutzer freigegeben (2026-10-23)**, inkl. Laser-Stil (Abschnitt 6c)
Vorgänger: `2026-10-22-naga-config-design.md` (Naga V2 Pro, umgesetzt und installiert)

## 1. Absicht und Warum

RazerTux unterstützt bisher nur die Razer Naga V2 Pro des Nutzers. Für eine spätere Veröffentlichung auf GitHub
soll es möglichst viele Razer-**Mäuse** unterstützen – mindestens alles, was OpenRazer für Mäuse kann, plus
Tastenbelegung, die OpenRazer nicht bietet. Der Nutzer besitzt nur die Naga V2 Pro; andere Geräte können nicht an
echter Hardware getestet werden.

## 2. Ziele

1. **Geräte-Datenbank** aus den OpenRazer-Quellen erzeugt (aktueller Stand: 117 Maus-Klassen): je Gerät Name,
   USB-Kennungen (Kabel/Funk/Dongle), Funktionen (DPI + Maximum, DPI-Stufen, Abfragerate inkl. HyperPolling,
   Licht-Zonen und Effekte, Akku/Laden, Ruhezeit, Akku-Schwelle, Mausrad-Modi wo vorhanden) und die
   **Protokoll-Variante** je Befehl (Transaktions-ID, Report-Layout). Ein Import-Skript (`tools/import_openrazer.py`)
   erzeugt die Daten reproduzierbar; die Daten werden mit GPL-Hinweis auf OpenRazer eingecheckt.
2. **Gerätetreiber-Schicht verallgemeinert**: `NagaV2Pro` wird zu `RazerMouse(device_info, transport)`;
   die Naga V2 Pro wird ein Eintrag der Datenbank plus ihre bekannten Besonderheiten (Treibermodus,
   Mausrad-Stufen-Meldungen, obere Tasten).
3. **Mehrere Geräte gleichzeitig**: Der Dienst verwaltet alle angeschlossenen unterstützten Mäuse; Profile gehören zu
   einem Gerätetyp. Im Fenster oben eine Geräteauswahl (wie Synapse-Reiter pro Gerät).
4. **Reiter nur für vorhandene Funktionen** (z. B. kein „Energie“ bei Kabelmäusen ohne Akku, „Beleuchtung“ mit den
   tatsächlichen Zonen des Geräts).
5. **Tastenbelegung für jede Maus**:
   - Bekannte Tasten-Tabellen für verbreitete Modelle (Seitentasten = BTN_SIDE/BTN_EXTRA, Naga-Familien = Tastatur-Codes).
   - **„Taste anlernen“** im Fenster: Taste an der Maus drücken → RazerTux merkt sich, welches Signal sie sendet,
     und ordnet es einem Platz in der Grafik zu. Funktioniert auch für unbekannte Modelle.
6. **Grafiken für andere Geräte** (siehe Abschnitt 6).
7. **„Experimentell“-Kennzeichnung**: Alle Geräte außer der Naga V2 Pro gelten als ungetestet. Beim ersten Anschließen
   nur lesende Befehle; schreibende erst, wenn die Lesebefehle plausible Antworten liefern. Fehlerberichte über eine
   Funktion „Diagnose kopieren“ (Gerätekennung, Antworten der Lesebefehle) für GitHub-Issues.

## 3. Nicht-Ziele

- Tastaturen, Headsets, Mauspads, Docks (eigenes Projekt danach)
- Chroma-Synchronisation zwischen Geräten, Spiele-Integration, Firmware-Updates
- Onboard-Profile in der Maus speichern
- Mausrad-Haptik (HyperScroll) setzen – Befehl unbekannt

## 4. Anti-Ziele

- Eine unbekannte Maus darf durch RazerTux nie in einen schlechteren Zustand geraten als vorher
  (Sicherung vor dem ersten Schreiben, wie bei der Naga).
- Die Naga V2 Pro des Nutzers darf durch den Umbau nichts verlieren (alle bisherigen Tests bleiben grün,
  bestehende Profile werden übernommen).

## 5. Entscheidungen des Nutzers

- Nach Sprachen und Optik zuerst **Mäuse**, Tastaturen später.
- Grafiken für andere Geräte gehören dazu; Linien müssen auf die richtigen Stellen zeigen.
- Projektname **RazerTux** (inoffiziell, keine Razer-Logos).

## 6. Grafiken (Claudes Vorschlag)

- **Ein Format**: jede Gerätegrafik ist ein SVG, in dem jede Taste ein Element mit fester ID trägt
  (`left`, `right`, `wheel`, `wheel_left`, `wheel_right`, `side_1` … `side_n`, `top_1` …, `dpi_up` …).
  Die Linien und das Hervorheben suchen ihr Ziel über diese ID – dasselbe Prinzip wie die neue Naga-Grafik
  aus dem Optik-Feinschliff.
- **Vorlagen statt Einzelbildern**: wenige Grundformen, aus denen Gerätegrafiken entstehen:
  1. MMO mit Seitenfeld (Naga-Familie, 2/6/12 Tasten)
  2. Ergonomisch Rechtshänder (DeathAdder, Basilisk, Cobra)
  3. Beidhändig symmetrisch (Viper, Orochi, Lancehead, Mamba)
  4. Kompakt/Mobil (Orochi, Pro Click Mini)
  5. Vertikal (Pro Click V2 Vertical)
  Pro Gerät eine kleine Layout-Datei: Vorlage, Anzahl und Lage der Tasten, Licht-Zonen.
- **Ersatzgrafik**: generische Maus mit nummerierten Tasten für Geräte ohne eigene Grafik.
- Erste Ausbaustufe: Vorlagen 1–3 und die ~15 verbreitetsten Modelle; der Rest nutzt Vorlage oder Ersatzgrafik.

## 6b. Entscheidungen von Claude (änderbar, nach Builder-Check)

1. **Eingabe-Grab nur bei Bedarf**: Die Eingabegeräte einer Maus werden nur exklusiv übernommen, wenn das aktive Profil
   dieses Geräts tatsächlich etwas umbelegt (oder Makros/Hypershift nutzt). Bei experimentellen Geräten ist
   „Tastenbelegung aktivieren“ pro Gerät zusätzlich ein bewusster Schalter (Standard aus); bei der Naga V2 Pro an.
   „Taste anlernen“ lernt nur evdev-Signale (Knoten-Typ, Ereignistyp, Code). Herstellerspezifische hidraw-Tasten
   (Treibermodus) bleiben Naga-V2-Pro-spezifisch.
2. **Tasten-Tabellen**: geprüft nur Naga V2 Pro. Generische Standardtabelle für alle (Links/Rechts/Mitte, Rad,
   BTN_SIDE/BTN_EXTRA, Kippen). Für die Naga-Familie (2/6/12 Seitentasten senden Tastatur-Codes 1…=) eine Tabelle
   als „ungeprüft“. Alles andere über „Taste anlernen“.
3. **Freigabe zum Schreiben**: automatisch, sobald Firmware, DPI und Abfragerate mit Status OK, passender
   Klasse/ID-Antwort und Werten im gültigen Bereich gelesen wurden. Danach sind auch Funktionen ohne Lesebefehl
   erlaubt (Licht/Effekte sind NOSTORE und kommen beim Abstecken zurück). Die Sicherung enthält alle lesbaren Werte.
   Schlägt die Prüfung fehl: Gerät nur lesend, Hinweis im Fenster + „Diagnose kopieren“.
4. **Import-Umfang**: Transaktions-ID, Matrix-Variante (klassisch 0x03 vs. erweitert 0x0F), LED-IDs/Zonen,
   Abfrageraten-Kodierung (Standard vs. HyperPolling), DPI-Kodierung, Akku-Befehle, Feature-Report-Interface.
   Was der Import für ein Gerät nicht sicher zuordnen kann, wird für dieses Gerät ausgeblendet (nie geraten).
   Byte-Vergleichstests gegen OpenRazer-Funktionen für eine Auswahl von Geräten.
5. **Kabel/Funk**: Kabel- und Funk-Kennung eines Modells sind ein logisches Gerät (ein Profil, ein Reiter), wie bei
   der Naga. Sind beide gleichzeitig verbunden, gilt die zuletzt verbundene (wie heute). Das Interface für
   Feature-Reports steht pro Gerät in der Datenbank (Standard 0).
6. **Profile pro Modell**: Profile gehören zu einem Modell (nicht zur Seriennummer). Bestehende Naga-Profile werden
   beim ersten Start in den Bereich des Modells „naga_v2_pro“ verschoben (Schema-Version 2, automatische Migration,
   alte Dateien als Sicherung behalten). D-Bus-Schnittstelle neu und versioniert (`io.github.razertux.Daemon1`);
   Dienst und Fenster werden immer zusammen ausgeliefert.
7. **Grafiken**: Claude zeichnet alle Vorlagen selbst (keine Razer-Bilder). Erste Modellliste (~15):
   DeathAdder V2, DeathAdder V3, DeathAdder V3 Pro, Viper, Viper V2 Pro, Viper Mini, Basilisk V3, Basilisk V3 Pro,
   Cobra, Cobra Pro, Orochi V2, Naga Pro, Naga X, Naga Trinity, Naga V2 HyperSpeed. Alle übrigen: passende Vorlage
   mit nummerierten Tasten oder Ersatzgrafik, inkl. Linien und Hover.
8. **Mausrad-Optionen**: Wo OpenRazer sie anbietet (Scroll-Modus, Beschleunigung, Smart Reel), erscheinen sie im
   Reiter „Mausrad“.

## 6c. Grafik-Stil „Laser“ (Entscheidung des Nutzers, 2026-10-23)

- Alle Gerätegrafiken – auch die Naga V2 Pro – werden als **leuchtende Umrisslinien in Razer-Grün (#44D62C)**
  auf dunklem Grund gezeichnet: keine Flächen-Schattierung, keine Fotos, keine Razer-Bilder oder -Logos.
  Grund: keine Rechte-Probleme mit Bildern.
- Leuchten ohne SVG-Filter (QtSvg kann keine): je Linie gestapelte Striche (breit/transparent → schmal/voll)
  plus heller Kern. Hervorhebung einer Taste = grüne Füllung mit ~35 % Deckkraft.
- Im Theme „Systemfarben“ wird das Grün durch die Akzentfarbe der Palette ersetzt (wie bisher per Platzhalter).
- Die Naga-Form wird an der echten Maus des Nutzers nachgebessert (Nutzer: „Form sieht in Realität ein wenig
  anders aus“) – Referenz: Fotos des Nutzers, nur als Vorlage zum Nachzeichnen, nicht eingebettet.
- Mockup: `.superpowers/sdd/…/laser-mockup.png` (Entwurf vom 2026-10-23).

## 7. Offene Fragen an den Nutzer (nur wenn er etwas anders will)

1. Sollen von Anfang an **alle 117** OpenRazer-Mäuse freigeschaltet sein (als experimentell), oder nur eine Liste
   verbreiteter Modelle? *Vorschlag: alle, klar als experimentell markiert.* → **freigegeben**
2. **Mehrere Mäuse gleichzeitig** unterstützen? *Vorschlag: ja.* → **freigegeben**

## 8. Risiken

- Protokoll-Varianten sind im OpenRazer-C-Code in `switch`-Blöcken verteilt; der Import muss sie zuverlässig
  zuordnen. Absicherung: Tests, die für ausgewählte Geräte die erzeugten Bytes mit den OpenRazer-Funktionen vergleichen.
- Ohne Hardware sind Fehler erst durch Nutzer anderer Geräte sichtbar → Diagnose-Funktion + experimentelles Label.
- Funk-Geräte mit Dongle und zusätzlichem Kabel (wie die Naga) haben je Gerät eigene Kennungen; der Import muss
  Paare erkennen.
