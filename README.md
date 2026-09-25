# Generator Set Monitor

A FastAPI application for ingesting operational Excel/CSV export data and presenting a generator monitoring dashboard with site mapping, snapshot comparison, and filtered inspection.

## Purpose

This project is designed to:

- import generator telemetry from Excel/CSV files
- normalize raw field names and values before saving them
- maintain a manual master `sites` table as the source of truth
- match uploaded rows to sites using `router_ip`
- persist generated operational data in `generators`
- compare snapshots by date to calculate variance between selected periods
- expose a compact, filterable dashboard for operator review

## Stack

- Backend: FastAPI
- ORM: SQLAlchemy
- Database: PostgreSQL 16
- Parsing: pandas + openpyxl
- Frontend: static HTML/JavaScript served by FastAPI
- Local server: Uvicorn
- Container support: Docker Compose

## Current data model

The app follows a master/detail pattern:

- `sites`: manual master registry of physical sites and router metadata
- `generators`: operational generator records imported from uploaded files
- `excel_uploads`: upload metadata and audit history
- `raw_data_rows`: raw row payloads as saved from each file
- `rectifiers`: defined for future extension, not the active functional scope

Important operational rule:

- `sites` is authoritative for site identity
- `generators` stores imported operational state
- uploaded files do not create new site records automatically

## Core behavior

### Import flow

1. The user uploads an Excel or CSV file from the UI.
2. Upload metadata is stored in `excel_uploads`.
3. Each row is kept in `raw_data_rows` as raw JSON for traceability.
4. Headers and values are normalized before persistence.
5. Rows are matched to `sites` via `router_ip`.
6. Valid matches are saved into `generators`.
7. Invalid or unmatched rows are ignored rather than creating inconsistent records.

### Snapshot comparison

The dashboard supports date-based variance analysis using snapshot dates derived from the import file. The current logic:

- uses the latest available snapshot as the default active date
- allows explicit reference date selection when needed
- compares current vs previous snapshot using selected variance windows such as DoD, WoW, MoM
- shows a calculated `VAR` column in the main table without duplicating the base metric unnecessarily

## API surface

Key backend routes:

- `GET /health`
- `GET /db-check`
- `GET /sites`
- `POST /sites`
- `GET /generators`
- `GET /analytics/generator-trends`
- `POST /upload-excel`
- `GET /uploads`
- `DELETE /uploads/{id}`

Analytics query parameters:

- `metric`: `running_hours` or `num_starts`
- `granularity`: `month` or `week`
- `reference_date`: optional `YYYY-MM-DD`; defaults to the latest snapshot
- `comparison_period`: `none`, `DoD`, `WoW`, `6DayBack`, or `MoM`

## Frontend

The app serves the dashboard at `/` and includes:

- search by site, IP, REG, and alarm text
- site and REG filters
- variance mode selection
- selected metric controls
- date-based snapshot filters
- dynamic column visibility management
- historical monthly and weekly charts by site, with totals, accumulated values, snapshot comparison, and PNG export
- compact grid presentation for operational review

Open the charts view from the header shortcut. The backend aggregates chart data with pandas; the frontend renders it with Chart.js from a CDN. See [docs/CHARTS.md](docs/CHARTS.md) for the data rules and implementation details.

## Project structure

- `app/main.py` — FastAPI app, routes, upload flow, data logic
- `app/models.py` — SQLAlchemy model definitions
- `app/database.py` — DB engine and session configuration
- `app/config.py` — environment settings
- `app/mapping.py` — header/value normalization rules
- `public/index.html` — dashboard UI and client-side filtering/rendering
- `docs/CHARTS.md` — charts feature design, API contract, calculations, and rendering behavior
- `docker-compose.yml` — local PostgreSQL container config
- `Dockerfile` — application container definition
- `requirements.txt` — Python dependencies
- `.env.example` — environment example file

## Local setup

### 1) Create a virtual environment

```powershell
cd C:\Users\YOFC\Documents\PROJECTS\GE\myapp-ge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2) Install dependencies

```powershell
pip install -r requirements.txt
```

### 3) Configure environment

```powershell
copy .env.example .env
```

Default local configuration:

```env
DATABASE_URL=postgresql+psycopg://myappge:myappge123@localhost:5432/myappge
APP_NAME=myapp-ge
APP_ENV=development
```

### 4) Start PostgreSQL

```powershell
docker compose up -d db
```

### 5) Run the API locally

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Then open:

- http://localhost:8001

## Notes

- The application is currently tuned for the operational workflow described by the project: manual site maintenance + import-driven generator data + snapshot comparison.
- Date handling and comparison logic must remain anchored to the imported snapshot date rather than any browser or upload clock.
- The dashboard is intentionally kept compact for operational monitoring, not for deep analytics reporting.

## Useful commands

### Check the app health

```bash
curl http://localhost:8001/health
```

### Check DB connectivity

```bash
curl http://localhost:8001/db-check
```

### List generators

```bash
curl http://localhost:8001/generators
```

## Local workflow for real use

1. Start PostgreSQL.
2. Start the FastAPI app.
3. Load the master `sites` table manually once.
4. Upload the Excel/CSV file with generator data.
5. Confirm the file is stored in the upload history.
6. Verify that the generator rows match the existing site master data.
7. Use the dashboard to inspect, filter, and review the imported records.

## Current status

This project is now in the operational dashboard phase.

Completed:
- FastAPI backend and API routes
- PostgreSQL schema for sites, generators, uploads, and raw data
- master sites + generator import logic
- Excel/CSV normalization and parsing
- upload history tracking
- dashboard UI with filters and column selection

Current working principle:
- `sites` is controlled manually
- uploaded files populate `generators` after matching by `router_ip`
- raw upload history is preserved for traceability without contaminating the master site list

## Notes

The app is designed to be practical and operational, not just a raw ingestion demo. The current logic intentionally avoids creating new site records from uploaded files; instead, the upload is matched against the official `sites` table, which keeps the data clean and consistent.
