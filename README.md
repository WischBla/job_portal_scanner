# Swiss Job Scanner + Bewerbungs-Tracker

Eine lokale Web-App mit zwei Aufgaben:

1. **Schweizer Jobs finden** — mehrere Portale abfragen, **lokal** hart filtern (Geografie,
   Arbeitsmodell, Ausschlüsse) und die verbleibenden Stellen mit einem **erklärbaren**
   Match-Score bewerten.
2. **Bewerbungen verfolgen** — Treffer in die Bewerbungsliste übernehmen und Status,
   Rückmeldungen, Interviews und Follow-ups dokumentieren.

Alle persönlichen Daten bleiben lokal in `data/applications.db` (SQLite). Keine Telemetrie,
kein externer Login, keine Abhängigkeiten ausserhalb der Python-Standardbibliothek.

---

## Start

```bash
cd /pfad/zum/projekt
python3 run.py
```

Die App läuft auf **http://127.0.0.1:8765** und öffnet den Browser automatisch.
Beenden mit **Ctrl+C**. Voraussetzung: **Python 3.8 oder neuer** — sonst nichts.

```bash
python3 run.py --port 9000     # anderen Port verwenden
python3 run.py --no-browser    # ohne automatisches Browser-Fenster
```

Unter Windows ggf. `python run.py`. `run.bat` / `start.command` / `start_windows.bat` sind
optionale Doppelklick-Helfer und rufen intern nur `run.py` auf — erforderlich sind sie nicht.

Tests:

```bash
python3 -m unittest discover -s tests -t .
```

---

## Filter-Architektur

Die Reihenfolge ist verbindlich und an genau einer Stelle implementiert
(`jobscanner/pipeline.py`):

```
Job-Portale / APIs
        ↓  jobscanner/sources/*      (ein Adapter pro Portal)
Rohdaten
        ↓  jobscanner/normalizer.py  (+ locations.py)
Normalisierte Jobs
        ↓  Deduplizierung
        ↓  jobscanner/filters.py     HARTE FILTER  ← hier endet alles Ungeeignete
        ↓  jobscanner/scoring.py     Match-Score 0–100
        ↓  Mindestscore + Sortierung
        ↓  jobscanner/repository.py  Persistenz + Job-Status
Ranked matching jobs → merken / ignorieren / bewerben → Bewerbungs-Tracker
```

Zwei Regeln, die das Design tragen:

* **Quell-APIs entscheiden nie über die Relevanz.** Ein `geo=switzerland`-Parameter ist
  reine Optimierung (weniger Download); die Entscheidung trifft immer der lokale Filter.
* **Harte Filter laufen vor dem Scoring.** Ein Job aus München kann nicht „durchrutschen“,
  weil er zufällig 78 Punkte erreicht — er wird gar nicht erst bewertet.

### Standort-Normalisierung

`jobscanner/locations.py` ist die einzige Stelle im Code, die „ist das die Schweiz?“
beantworten darf. Jeder Job erhält:

`raw_location`, `normalized_country`, `normalized_city`, `normalized_region`,
`is_remote`, `is_hybrid`, `work_model`, `office_days`, `switzerland_eligible`,
`location_confidence`.

* **Primärquelle** ist das strukturierte Standortfeld.
* **Schreibweisen** werden zusammengeführt: Zürich/Zuerich/Zurigo → Zurich,
  Lucerne → Luzern, Genf/Genève → Geneva, Basle → Basel, CH/CHE → Switzerland.
* **Kantonskürzel** (ZH, ZG, LU, BE, BS, BL, AG, SG, SZ, TI, GE, VD …) gelten nur als
  *bestätigende* Evidenz, nie als alleiniger Beweis — „BE“ ist auch Belgien, „FR“ auch
  Frankreich, „AG“ auch eine Rechtsform.
* **Pauschale Regionen** (Europe, EU, EEA, EMEA, DACH, Benelux, Worldwide, Anywhere …)
  sind **nicht** die Schweiz.
* **Beschreibung als Sekundärevidenz:** Bei einer reinen Region wird der Freitext geprüft.
  Akzeptiert wird nur eine explizite Aussage — ein Schweiz-Begriff *zusammen mit* einem
  Eignungs-Hinweis (reside, located, based, candidates, eligible, work permit, office …).
  „European“, „DACH“, „German speaking“, „Central Europe“ genügen nie.
  Formulierungen wie „excluding Switzerland“ heben positive Evidenz wieder auf.
  Solche Treffer erhalten `location_confidence = "medium"` und einen sichtbaren Hinweis.

Akzeptiert: `Zurich, Switzerland`, `Zürich, Schweiz`, `Zug, CH`, `Bern`, `Switzerland`,
`Remote Switzerland`, `Remote - Switzerland`, `Switzerland or Germany`,
`Remote Europe` **plus** „must reside in Switzerland“ im Text.

Abgelehnt: `Berlin`, `Munich`, `München`, `Deutschland`, `Remote Germany`, `Remote Europe`,
`Remote EU`, `Europe`, `DACH`, `EMEA`, `Anywhere`, `Austria`, `France`, `Netherlands`.

### Harte Filter

| Code | Regel |
| --- | --- |
| `not_switzerland` | `country_mode = strict` und kein belegbarer Schweiz-Bezug |
| `country_not_allowed` | Land steht nicht in `allowed_countries` |
| `location_not_selected` | Konkrete Stadt ist nicht in den gewählten Standorten (landesweite/Remote-Stellen ohne Stadt bleiben zulässig) |
| `onsite_outside_switzerland` | Onsite-Rolle ausserhalb der Schweiz im Strict-Modus |
| `work_model` | Remote/Hybrid/Onsite im Profil deaktiviert |
| `excluded_title` | Titel enthält einen Begriff aus `exclude_titles` |
| `impossible_seniority` | Junior, Intern, Werkstudent, Trainee, Lehrstelle, Entry Level … |
| `unrelated_function` | Andere Fachrichtung (Recruiting, HR, Finance, Legal, Design, Helpdesk …) |
| `excluded_keyword` | Ausschlussbegriff im Titel ohne technischen Leadership-Bezug |
| `missing_required_keyword` | Ein `required_keywords`-Eintrag fehlt |
| `salary_below_minimum` / `missing_salary` | Gehaltsregeln |
| `below_minimum_score` | Score unter `minimum_match_score` (nach dem Scoring) |

Wichtig für falsch-positive Ausschlüsse: Begriffe wie *sales*, *marketing* oder *finance*
werden nur dann als Ausschluss gewertet, wenn der Titel **kein** technisches Leadership-Signal
enthält. „Head of Sales Engineering Operations“ bleibt drin, „Account Executive DACH“ nicht.
Begriffe, die die Funktion selbst benennen (Recruiter, Graphic Designer, Helpdesk …), sind
absolut — „Technical Recruiter“ ist trotzdem Recruiting.

### Match-Score

Gewichtung (Summe 100), implementiert in `jobscanner/scoring.py`:

| Dimension | Punkte |
| --- | --- |
| Seniorität | 20 |
| Rolle / Verantwortung | 25 |
| Technische Domäne | 20 |
| Leadership / Transformation | 15 |
| Standort / Arbeitsmodell | 10 |
| Gehaltsangabe | 5 |
| Strategie / AI-Relevanz | 5 |

Der Score ist **kein Titel-Matcher**: „Director, Technology Enablement“ oder
„Head of Engineering Productivity“ punkten voll, obwohl der Titel nicht wörtlich im Profil
steht. Jede Dimension liefert Punkte, eine Begründung und ggf. einen Vorbehalt. Die Job-Karte
zeigt deshalb immer:

```
60 MATCH · Senior DevOps Engineer · Proton · Geneva, Switzerland · Onsite

Starker Match                        Mögliche Einschränkungen
+ Role area in title: DevOps         − title does not signal a Head / Director scope
+ Technology match: DevOps, Cloud    − no published salary
+ Geneva
```

Dazu eine ausklappbare **Score-Herleitung** mit Punkten pro Dimension.

### „Warum Jobs gefiltert wurden“

Unter der Trefferliste liegt eine standardmässig eingeklappte Debug-Ansicht mit den
Ablehnungen des letzten Laufs, gruppiert nach Grund — inklusive Klartext wie
*„Structured location names Germany, which is not Switzerland.“* oder
*„Location ‚Remote Europe‘ is a blanket region. Region wording alone does not include Switzerland.“*

---

## Suchprofil

Das Profil liegt in der Tabelle `search_profile` (genau eine Zeile). **SQLite ist die einzige
Wahrheit** — das Frontend hält bewusst keine eigene Kopie, sondern liest nach jedem Speichern
neu. Felder:

`country_mode`, `allowed_countries`, `allowed_locations`, `optional_locations`,
`remote_policy`, `hybrid_max_office_days`, `seniority_levels`, `include_titles`,
`exclude_titles`, `required_keywords`, `preferred_keywords`, `excluded_keywords`,
`minimum_match_score`, `minimum_salary_chf`, `allow_missing_salary`,
`language_preferences`, `sources_enabled`, `auto_hours`.

Standard: `country_mode = strict`, Land Schweiz, Standorte Zürich · Zug · Luzern · Bern · Basel
(sekundär St. Gallen · Schwyz · Aargau · Lugano), Remote/Hybrid/Onsite erlaubt,
maximal 2 Bürotage pro Woche, Mindestscore 65.

Im Job Scout steht der Filterblock direkt auf der Seite. **SAVE FILTERS** speichert, zeigt
„Filter erfolgreich gespeichert“ und lädt die gespeicherten Werte sofort neu; ein Reload oder
Neustart zeigt exakt dieselben Werte, und der nächste Scan verwendet genau diese.

API:

| Methode | Pfad | Zweck |
| --- | --- | --- |
| `GET` | `/api/search-profile` | gespeichertes Profil lesen |
| `PUT` | `/api/search-profile` | Profil speichern (validiert, vollständig) |
| `POST` | `/api/scan` | Scan mit dem gespeicherten Profil starten |
| `GET` | `/api/scout/jobs` | Treffer |
| `GET` | `/api/scout/rejected` | Ablehnungen des letzten Laufs |
| `GET` | `/api/scout/summary` | Lauf-Statistik und Zähler |
| `PUT` | `/api/scout/jobs/{id}/state` | `NEW · SEEN · SAVED · IGNORED · APPLIED · EXPIRED` |
| `POST` | `/api/scout/jobs/{id}/convert` | in den Bewerbungs-Tracker übernehmen |

`/api/scout/profile` bleibt als Alias erhalten.

---

## Job-Status und Deduplizierung

Jeder Treffer hat genau einen Status: `NEW`, `SEEN`, `SAVED`, `IGNORED`, `APPLIED`, `EXPIRED`.
Von Hand gesetzte Status (`SAVED`, `IGNORED`, `APPLIED`) überleben jeden Rescan.

Identität: **`source_type` + externe Job-ID**; fällt die weg, die bereinigte URL (ohne
Tracking-Parameter), sonst Firma + Titel + Ort. Dieselbe Stelle aus zwei Portalen landet
deshalb nur einmal in der Liste, und wiederholte Scans erzeugen keine Duplikate.

Was ein erfolgreich abgefragtes Portal nicht mehr liefert **oder** was durch verschärfte
Filter herausfällt, wird auf `EXPIRED` gesetzt und verschwindet aus der Liste. Ohne das
würde eine Filter-Verschärfung scheinbar wirkungslos bleiben, weil alte Treffer liegen blieben.

---

## Bewerbungs-Tracker

**APPLY / In Bewerbungen** übernimmt Firma, Jobtitel, Standort, Quelle, Job-URL,
Arbeitsmodell, Gehalt (falls bekannt), Match-Score samt Begründung und Fundtag in den
Bewerbungsdatensatz. Startstatus ist **Vorbereitung** (= „Preparation“).

Der Tracker behält seine deutschen Statusbezeichnungen, damit bestehende Datensätze gültig
bleiben. Sie entsprechen 1:1 den Stufen aus der Aufgabenstellung:

| Deutsch | Englisch |
| --- | --- |
| Vorbereitung | Preparation |
| Beworben | Applied |
| Eingangsbestätigung | (Acknowledged) |
| Screening / HR | HR Screening |
| Interview 1 / Interview 2 | Interview 1 / Interview 2 |
| Case / Assessment | Assessment |
| Final Interview | Final Interview |
| Angebot | Offer |
| On Hold | (On Hold) |
| Abgelehnt | Rejected |
| Zurückgezogen | Withdrawn |

---

## Portale / Quellen

Unter **Portale** lassen sich Quellen aktivieren, ergänzen und löschen. Standardmässig aktiv:
Arbeitnow, Remotive, Jobicy.

| Quelle | Upstream-Filter | Grenzen |
| --- | --- | --- |
| **Arbeitnow** | keiner | Board ist stark deutschlastig und bietet keinen Länderparameter; praktisch alles wird lokal verworfen. |
| **Remotive** | nur Freitextsuche | Weltweites Remote-Board; `candidate_required_location` ist meist „Europe“ oder „Worldwide“ — ohne explizite Schweiz-Aussage im Text wird abgelehnt. |
| **Jobicy** | `geo=switzerland` | Remote-only; die Schweiz-Abfrage liefert sehr wenige Stellen, `jobGeo` ist oft eine Region. |
| **Greenhouse** | keiner | Liefert das ganze Board; Standortqualität hängt am Arbeitgeber. Board-Token eintragen. |
| **Lever** | keiner | Wie Greenhouse; Standorte kommen aus dem Kategorie-Feld. Site-Name und Region eintragen. |
| **RSS / Atom** | keiner | Feeds haben selten ein Standortfeld; der Adapter versucht den Ort aus dem Titel zu lesen („… (Zurich)“). |

**Wichtig:** Die drei allgemeinen Remote-Boards enthalten kaum Schweizer Senior-Technology-
Leadership-Stellen. Wer regelmässig Treffer sehen will, ergänzt gezielt Karriereseiten von
Unternehmen mit Schweizer Standorten (Greenhouse-Board-Token oder Lever-Site). Zwei solche
Quellen sind als Beispiel eingetragen (*Scandit Careers*, *Proton Careers*) und lassen sich
unter **Portale** jederzeit deaktivieren oder löschen.

LinkedIn und jobs.ch werden nicht über inoffizielle Scraper ausgelesen.

---

## Datenbank

`data/applications.db` wird nie gelöscht oder neu aufgebaut. Beim Start laufen idempotente
Migrationen (`jobscanner/db.py`), die ausschliesslich additiv sind:

* neue Tabellen `search_profile`, `rejected_jobs`, `schema_meta`
* neue Spalten in `discovered_jobs` (normalisierter Standort, Arbeitsmodell, Seniorität,
  Score-Herleitung, `state`) und in `scout_runs`
* Übernahme des alten Profils aus `job_search_profile` (Standorte, Ausschlüsse, Skills,
  Zielrollen, Gehaltsgrenze) beim ersten Start
* Übersetzung der alten Status (`Neu`, `Gemerkt`, `Ignoriert`, `Übernommen`) in `NEW`,
  `SAVED`, `IGNORED`, `APPLIED`

Die alte Tabelle `job_search_profile` bleibt unangetastet erhalten. Die Datenbank steht in
`.gitignore` und wird nie committet.

**Backup** exportiert Bewerbungen, Verlauf, Suchprofil, Quellen und gefundene Jobs als JSON.

---

## Projektstruktur

```
run.py                    Start (Version prüfen, DB migrieren, Server, Browser)
app.py                    HTTP-Schicht: Routing, JSON, statische Dateien
jobscanner/
  db.py                   Verbindung + idempotente Migrationen
  profile.py              kanonisches Suchprofil (Defaults, Validierung)
  locations.py            LocationNormalizer — die Schweiz-Entscheidung
  normalizer.py           Rohdaten → normalisierter Job
  filters.py              HardFilter (vor dem Scoring)
  scoring.py              MatchScorer (erklärbar, 0–100)
  repository.py           Persistenz, Deduplizierung, Job-Status
  pipeline.py             fetch → normalize → dedupe → filter → score → persist
  sources/                ein Adapter pro Portal
static/                   Frontend (index.html, app.js, styles.css)
tests/                    unittest-Suite
```

## Datenschutz

* Bewerbungsdaten und Historie: nur lokale SQLite-Datenbank.
* Bei einer Jobsuche werden ausschliesslich öffentliche Stellen-Endpunkte aufgerufen.
* Name, CV und Bewerbungsdaten werden nicht an die Jobquellen übertragen.
* Keine Telemetrie, kein externer Login.
