# Generator Set Monitor

FastAPI and PostgreSQL application for reviewing generator readings, daily capture coverage, snapshot comparisons, and historical regional trends.

## Active data architecture

The active dashboard reads the source-system capture tables in the application's PostgreSQL database (`myappge`):

- `public.generator_daily_history` stores the daily generator snapshots used for historical review and charts.
- `public.generator_d0` stores the latest capture and is used by the main dashboard when present.
- `public.sites` is the application's site and region reference table. Capture rows are matched by normalized site name (`lower(trim(site_name))`) to obtain the local `site_id` and region. If no local site matches, the current reader falls back to the source row's `site_id`; it does not create a `sites` record.
- `public.region_historical_baseline` stores manually prepared monthly or weekly regional deltas for periods without operational history. It is required by the charts endpoint, even if it has no matching rows for a requested period.

The application reads these capture tables; the external API/import process is responsible for inserting and maintaining their rows. The application does not create or backfill `generator_d0` or `generator_daily_history`. These source tables are expected to contain at least:

- `snapshot_date`, `captured_at`, `site_id`, `device_type`, `device_index`, `site_name`, `region`, `updated_at`
- Counter and telemetry columns including `running_hours`, `num_starts`, `fuel_level`, `total_fuel_consumption`, `mains_voltage_l1n`, `load_current_l1`, state fields, `alarms`, and `_last_reading`

The supplied schema uses `(site_id, device_type, device_index)` as the `generator_d0` key and `(snapshot_date, site_id, device_type, device_index)` as the daily-history key. Keep those identities stable in the ingestion process. `snapshot_date` is the capture's reporting date; `captured_at` is when the capture batch was written; `updated_at` records the source reading time when available. Do not treat these timestamps as interchangeable.

The current source schema also has checks requiring generator-only rows, `snapshot_date` to match the Lima-local date of `captured_at`, and any non-null `updated_at` to be on that same date and no later than `captured_at`. `alarms` must be null or a JSON array; `_last_reading` must be null or a JSON object. The check named `generator_fallback_only_without_today` is `updated_at IS NULL OR _last_reading IS NULL`; it does not prove that counter values were copied from a prior reading.

### Dashboard source and duplicate handling

`GET /generators` and `GET /sites/{site_id}/generators` read daily history and, when the table exists, union in D0. For duplicate device/date rows, the reader prefers D0 over history, then the later capture time within that source. The API response keeps the dashboard's existing field names (`site`, `reg`, `ip`, and so on) while mapping source columns such as `site_name`, `region`, and `device_ip`.

The dashboard's comparison table uses the reference-date snapshot and the selected target date. A generator's VAR is calculated only when both snapshots contain numeric values for that metric. If either side is missing, the available side's reading remains visible and VAR is left blank. In comparison mode, the displayed device rows are the deduplicated union of devices found on either date.

The dashboard no longer presents an Excel upload form. Legacy tables and routes for manual uploads (`generators`, `excel_uploads`, `raw_data_rows`, and `/upload-excel`, `/uploads`) remain in the backend for compatibility, but they are not the source for the active dashboard, daily capture history, or charts. Uploading through the legacy endpoint does not insert into `generator_daily_history` or `generator_d0`.

## Daily capture history

The **Daily capture history** view calls `GET /history-captures`. It shows one row per `snapshot_date` in `generator_daily_history`, ordered newest first, with generator count, capture time, reading freshness counts, and generators with non-empty alarm arrays. The initial list shows the newest five dates; **Show more** expands it. A date filter narrows the list.

The count **Rows without same-day update** is defined as:

```text
updated_at IS NULL
OR (updated_at AT TIME ZONE 'America/Lima')::date <> snapshot_date
```

This is a freshness classification only. It does not prove that a metric was copied from an older reading, and the view does not fill or recalculate values. To trace a row, download the capture using `GET /history-captures/{snapshot_date}/download` and inspect its metric fields, `updated_at`, and `_last_reading`. If `updated_at` is null, that row does not identify the original reading time. The **Current** badge is based on the maximum generator `snapshot_date` in `generator_d0`.

The Excel download is generated from the selected date's rows in `generator_daily_history`; JSON fields (`alarms` and `_last_reading`) are exported as text. Timestamp fields are exported as ISO strings to preserve their time-zone offsets. It is a database snapshot export, not the original uploaded Excel file.

## Historical charts

Select **Charts & comparison** from the header. Configure metric, grouping, reference date, and optional snapshot comparison. The endpoint and calculation rules are documented in [docs/CHARTS.md](docs/CHARTS.md).

Charts use `generator_daily_history` for both trend deltas and raw snapshot totals. `generator_d0` is not included in the trend calculation. `region_historical_baseline` supplies missing period/region cells. Alongside the main trend, the charts view shows a regional summary for the latest displayed period: `num_starts` VAR, `running_hours` VAR, and the per-region running-hours accumulation. It also lists up to ten sites whose operational running-hours VAR exceeds 10. Baseline data is regional only, so it does not create site-level entries. Chart PNG export is available for both charts; the trend chart also has a copy-image action, which requires browser clipboard support and a secure context such as HTTPS or localhost.

## API routes

Active source-backed routes:

- `GET /health`, `GET /db-check`
- `GET /sites`
- `GET /generators`, `GET /sites/{site_id}/generators`
- `GET /history-captures`
- `GET /history-captures/{snapshot_date}/download`
- `GET /analytics/generator-trends`

Legacy upload routes remain available in the backend:

- `POST /upload-excel`
- `GET /uploads`
- `GET /uploads/{id}/download`
- `DELETE /uploads/{id}`

These legacy routes operate on upload/ORM tables and do not populate the active capture tables.

## Stack and project files

- FastAPI, SQLAlchemy, PostgreSQL, pandas, and openpyxl
- Static frontend: `public/index.html`
- Routes and analytics: `app/main.py`
- ORM models (including legacy upload tables): `app/models.py`
- Database configuration: `app/database.py` and `app/config.py`
- Chart implementation guide: `docs/CHARTS.md`
- Docker configuration: `Dockerfile` and `docker-compose.yml`

Chart.js and its DataLabels plugin load from jsDelivr, so browsers need access to that CDN for charts.

## Local development

From the project directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Configure `DATABASE_URL` in `.env` to point to the PostgreSQL database containing the required source tables and the local `sites` and baseline tables. To use the included development database instead:

```powershell
docker compose up -d db
uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload
```

Open <http://localhost:8001>. The application startup creates/migrates its SQLAlchemy ORM tables, but it does not create or migrate the external `generator_d0` and `generator_daily_history` source tables. Verify those tables, required columns, keys, and grants in the configured database before starting the application.

Useful local checks:

```bash
curl http://localhost:8001/health
curl http://localhost:8001/db-check
curl http://localhost:8001/generators
curl http://localhost:8001/history-captures
```
