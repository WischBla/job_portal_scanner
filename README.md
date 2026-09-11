# Quick Start

For macOS:

```bash
cd /path/to/project
python3 run.py
```

For Windows:

```bat
cd C:\path\to\project
python run.py
```

Das war's. Der Server startet auf `http://127.0.0.1:8765` und der Browser öffnet sich automatisch.
Beenden mit **Ctrl+C**.

Voraussetzung: **Python 3.8 oder neuer** — sonst nichts.

Das Projekt verwendet ausschliesslich die Python-Standardbibliothek, es gibt **keine externen
Abhängigkeiten**. Ein `python3 -m pip install -r requirements.txt` ist daher nicht nötig; falls
du es trotzdem ausführst und pip mit `externally-managed-environment` abbricht, kann das
ignoriert werden.

Weitere Optionen:

```bash
python3 run.py --port 9000     # anderen Port verwenden
python3 run.py --no-browser    # ohne automatisches Browser-Fenster starten
```

`run.py` funktioniert unabhängig davon, wo der Projektordner liegt, und benötigt weder
`chmod +x` noch spezielle macOS-Sicherheitsfreigaben.

---

# Lokaler Job-Portal-Scanner + Bewerbungs-Tracker

Eine lokale Web-App für zwei Aufgaben:

1. **Jobs finden:** mehrere Jobquellen scannen, Treffer gegen dein persönliches Profil bewerten und nur relevante Stellen anzeigen.
2. **Bewerbungen verfolgen:** interessante Treffer direkt in die Bewerbungsliste übernehmen und Status, Rückmeldungen, Interviews und Follow-ups dokumentieren.

Alle persönlichen Bewerbungsdaten liegen lokal in `data/applications.db` (SQLite).

## Start

Der offizielle Startweg ist auf allen Plattformen gleich:

1. ZIP entpacken.
2. Terminal im Projektordner öffnen.
3. `python3 run.py` ausführen (unter Windows ggf. `python run.py`).
4. Die App öffnet sich automatisch unter `http://127.0.0.1:8765`.

`run.py` prüft die Python-Version, legt fehlende Ordner an, initialisiert bei Bedarf die
SQLite-Datenbank, startet den Server aus `app.py` und öffnet den Browser erst, wenn der
Server tatsächlich antwortet. Eine bereits vorhandene Datenbank wird dabei nie überschrieben.

### Optionale Hilfsdateien

Diese Dateien sind **nicht erforderlich** und nur Komfort:

- `run.bat` (Windows, Doppelklick)
- `start.command` / `start_windows.bat` (ältere Startwege)

### Häufige Meldungen

- *Port 8765 ... is already in use* — die App läuft bereits. Entweder
  `http://127.0.0.1:8765` öffnen oder mit `python3 run.py --port 8766` starten.
- *Python 3.8 or newer is required* — auf macOS `python3 run.py` statt `python run.py` verwenden.

## Job-Suche

Unter **Job Scout** auf **Jobs scannen** klicken. Die App ruft alle aktivierten Quellen ab, merkt sich bereits bekannte Stellen und berechnet einen Match-Score.

Das Suchprofil ist auf Senior Technical Leadership in der Schweiz vorbereitet:
- Head / Director / Principal
- Technology / Technical Operations
- Engineering / Platform / SRE / Cloud / DevOps / DevSecOps
- Technical Project & Program Management
- AI / AIOps / Automation / Technology Transformation
- bevorzugt Zürich, Zug, Luzern, Bern, Basel sowie weitere deutschsprachige Schweizer Regionen
- Remote bevorzugt, Hybrid akzeptiert
- Gehaltsziel wird nur bewertet, wenn eine Ausschreibung überhaupt Gehaltsdaten enthält

Unter **Filter** kannst du Zielrollen, Skills, Standorte, Ausschlussbegriffe und Mindestscore ändern.

## Portale / Quellen

Unter **Portale** verwaltest du die Quellen. Standardmässig sind aktiv:
- Arbeitnow
- Remotive
- Jobicy

Zusätzlich kannst du beliebig viele Unternehmens-Karriereseiten hinzufügen, wenn sie eines dieser Systeme verwenden:
- **Greenhouse**: Board Token eintragen
- **Lever**: Site-Name und Region eintragen
- **RSS / Atom**: Feed-URL eintragen

Damit kannst du z. B. interessante Technologieunternehmen direkt überwachen, statt nur allgemeine Jobbörsen abzufragen.

LinkedIn und jobs.ch werden nicht über fragile oder inoffizielle Scraper ausgelesen. Wenn eine Quelle eine offizielle API oder einen Feed bereitstellt, kann sie als weiterer Adapter ergänzt werden.

## Bewerbungen

Bei einem passenden Treffer auf **In Bewerbungen** klicken. Danach kannst du pro Bewerbung pflegen:
- Status
- Priorität
- Bewerbungsdatum
- Kontaktperson
- Gehaltsband
- verwendete CV-/Motivationsschreiben-Version
- nächste Aktion
- Follow-up-Datum
- komplette Ereignis-Timeline für E-Mails, Telefonate und Interviews

## Backup

**Backup** exportiert Bewerbungen, Verlauf, Suchprofil, Quellen und gefundene Jobs als JSON.

## Datenschutz

- Bewerbungsdaten und Historie: nur lokale SQLite-Datenbank.
- Bei einer Jobsuche werden nur öffentliche Stellen-Endpunkte aufgerufen.
- Name, CV und Bewerbungsdaten werden nicht an die Jobquellen übertragen.
- Keine Telemetrie und kein externer Login.
