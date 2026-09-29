import re
import logging
import math
import os
import mimetypes
from pathlib import Path
from datetime import date, datetime
import unicodedata
from urllib.parse import quote
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect
from sqlalchemy.exc import SQLAlchemyError
import pandas as pd
import json
from io import BytesIO

load_dotenv()
logger = logging.getLogger("myappge.upload")
logging.basicConfig(level=logging.INFO)

ANALYTICS_REGIONS = ("San Martin", "Arequipa", "La Libertad", "Ancash")
REGION_ALIASES = {
    "san martin": {"san martin", "sanmartin", "sm"},
    "arequipa": {"arequipa", "ar"},
    "la libertad": {"la libertad", "lalibertad", "ll"},
    "ancash": {"ancash", "an"},
}


def normalize_analytics_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", normalized.lower().replace("_", " ").replace("-", " ")).strip()


def map_region_name(region_value, site_name):
    region_text = normalize_analytics_text(region_value)
    for canonical, aliases in REGION_ALIASES.items():
        if region_text in aliases or any(alias in region_text for alias in aliases if len(alias) > 2):
            return next(name for name in ANALYTICS_REGIONS if normalize_analytics_text(name) == canonical)

    site_text = normalize_analytics_text(site_name)
    compact_site_text = re.sub(r"[^a-z0-9]+", "", site_text)
    for canonical, aliases in REGION_ALIASES.items():
        if any(
            alias in site_text or alias.replace(" ", "") in compact_site_text
            for alias in aliases
            if len(alias) > 2
        ):
            return next(name for name in ANALYTICS_REGIONS if normalize_analytics_text(name) == canonical)
        tokens = set(re.findall(r"[a-z0-9]+", site_text))
        if tokens.intersection(alias for alias in aliases if len(alias) <= 2):
            return next(name for name in ANALYTICS_REGIONS if normalize_analytics_text(name) == canonical)
    return None


def normalize_baseline_metric(value):
    normalized = re.sub(r"[^a-z0-9]+", "", normalize_analytics_text(value))
    if "running" in normalized or normalized in {"rh", "horas"}:
        return "running_hours"
    if "start" in normalized or "arranque" in normalized:
        return "num_starts"
    return None


def parse_baseline_period(period_type, period_label, reference_year):
    normalized_type = normalize_analytics_text(period_type)
    label = normalize_analytics_text(period_label)
    if normalized_type in {"month", "monthly"}:
        raw_label = str(period_label or "").strip()
        for label_format in ("%b-%y", "%b %Y", "%B %Y", "%Y-%m", "%Y/%m", "%m/%Y"):
            try:
                parsed_label = datetime.strptime(raw_label, label_format)
                return pd.Timestamp(parsed_label.year, parsed_label.month, 1)
            except ValueError:
                continue
        parsed = pd.to_datetime(raw_label, errors="coerce")
        return parsed.to_period("M").start_time if not pd.isna(parsed) else None

    if normalized_type not in {"week", "weekly"}:
        return None

    iso_week = re.search(r"\b(20\d{2})\s*[-_/ ]*\s*w(?:eek)?\s*0?(\d{1,2})\b", label)
    if iso_week:
        year, week = int(iso_week.group(1)), int(iso_week.group(2))
    else:
        week_match = re.search(r"\b(?:week|wk|w)\s*0?(\d{1,2})\b", label)
        if not week_match and re.fullmatch(r"\d{1,2}", label):
            week_match = re.search(r"\d{1,2}", label)
        if not week_match:
            week_match = re.search(r"\b(\d{1,2})[-/ ](20\d{2})\b", label)
            if week_match:
                year, week = int(week_match.group(2)), int(week_match.group(1))
                try:
                    return pd.Timestamp(date.fromisocalendar(year, week, 1))
                except ValueError:
                    return None
        if not week_match:
            return None
        week = int(week_match.group(1))
        year_match = re.search(r"\b(20\d{2})\b", label)
        year = int(year_match.group(1)) if year_match else int(reference_year)

    try:
        return pd.Timestamp(date.fromisocalendar(year, week, 1))
    except ValueError:
        return None

from app.database import Base, engine, get_db
from app.mapping import normalize_excel_row
from app.models import ExcelUpload, RawDataRow, Site, Generator, Rectifier

app = FastAPI(title="myapp-ge", version="0.1.0")
PUBLIC_IMAGES_DIR = Path(__file__).resolve().parent.parent / "public" / "images"
app.mount("/images", StaticFiles(directory=str(PUBLIC_IMAGES_DIR)), name="images")


def extract_snapshot_date(filename: str):
    if not filename:
        return None

    patterns = [
        r"(\d{4})[-_](\d{1,2})[-_](\d{1,2})",
        r"(\d{1,2})[-_](\d{1,2})[-_](\d{4})",
        r"(\d{1,2})/(\d{1,2})/(\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, filename)
        if not match:
            continue

        if len(match.groups()) == 3:
            a, b, c = match.groups()
            if len(a) == 4:
                year, month, day = int(a), int(b), int(c)
            else:
                day, month, year = int(a), int(b), int(c)
            try:
                return datetime(year, month, day).date()
            except ValueError:
                continue

    return None


def json_safe_value(value):
    if isinstance(value, dict):
        return {str(key): json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(item) for item in value]
    if pd.isna(value):
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    return value


def sanitize_generator_value(key: str, value):
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return None

    numeric_fields = {
        "fuel_percent",
        "mains_voltage",
        "load_amperes",
        "running_hours",
        "total_fuel_consumption",
        "engine_state",
        "controller_mode",
    }

    if key in numeric_fields:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return round(numeric, 3)

    if key == "num_starts":
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        return int(round(numeric))

    if key == "update_time":
        text = str(value).strip()
        if not text:
            return None
        match = re.search(r"^(\d{2})/(\d{2})/(\d{2,4})\s+(\d{1,2}):(\d{1,2}):(\d+)$", text)
        if not match:
            return text
        day, month, year, hour, minute, second = match.groups()
        second_digits = str(second)
        if len(second_digits) > 2:
            second = second_digits[:2]
        try:
            parsed = datetime.strptime(f"{day}/{month}/{year} {hour}:{minute}:{second}", "%d/%m/%Y %H:%M:%S")
        except ValueError:
            return None
        return parsed.strftime("%d/%m/%Y %H:%M:%S")

    return value


@app.get("/")
def read_index():
    return FileResponse("public/index.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def ensure_database_schema():
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    def add_missing_column(table_name: str, column_name: str, ddl: str):
        if table_name not in existing_tables:
            return
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        if column_name in columns:
            return
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {ddl}"))

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    add_missing_column("excel_uploads", "snapshot_date", "snapshot_date DATE")
    add_missing_column("excel_uploads", "file_content", "file_content BYTEA")
    add_missing_column("sites", "region", "region VARCHAR(100)")
    add_missing_column("generators", "snapshot_date", "snapshot_date DATE")
    add_missing_column("generators", "upload_time", "upload_time TIMESTAMPTZ")
    add_missing_column("rectifiers", "snapshot_date", "snapshot_date DATE")
    add_missing_column("rectifiers", "upload_time", "upload_time TIMESTAMPTZ")


@app.on_event("startup")
def startup_event():
    ensure_database_schema()


@app.get("/health")
def health_check():
    return {"status": "ok", "app": "myapp-ge"}


@app.get("/db-check")
def db_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "database_ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database connection error: {exc}")


@app.post("/sites")
def create_site(
    router_ip: str,
    site_name: str,
    site_type: str | None = None,
    region: str | None = None,
    alarms: str | None = None,
    db: Session = Depends(get_db),
):
    existing = db.query(Site).filter(Site.router_ip == router_ip).first()
    if existing:
        raise HTTPException(status_code=400, detail="A site with this router IP already exists.")

    site = Site(
        router_ip=router_ip,
        site_name=site_name,
        site_type=site_type,
        region=region,
        alarms=alarms,
    )
    db.add(site)
    db.commit()
    db.refresh(site)
    return {"message": "Site created", "site_id": site.site_id}


@app.get("/sites")
def list_sites(db: Session = Depends(get_db)):
    sites = db.query(Site).all()
    return [
        {
            "site_id": site.site_id,
            "router_ip": site.router_ip,
            "site_name": site.site_name,
            "site_type": site.site_type,
            "region": site.region,
            "alarms": site.alarms,
        }
        for site in sites
    ]


def read_generator_snapshot_rows(db: Session, site_id: int | None = None):
    """Read dashboard snapshots from D0 plus history, keeping the API response shape."""
    existing_tables = set(inspect(engine).get_table_names(schema="public"))
    if "generator_daily_history" not in existing_tables:
        raise HTTPException(status_code=503, detail="generator_daily_history is not available in myappge.")

    if "generator_d0" in existing_tables:
        source_rows = """
            SELECT
                h.snapshot_date, h.captured_at, h.site_id, h.device_type, h.device_index,
                h.site_name, h.router_ip, h.site_type, h.region, h.device_ip, h.updated_at,
                h.fuel_level, h.mains_voltage_l1n, h.load_current_l1, h.engine_state,
                h.controller_mode, h.running_hours, h.total_fuel_consumption, h.num_starts,
                h.alarms, 1 AS source_priority
            FROM public.generator_daily_history AS h
            WHERE h.device_type = 'generator'
            UNION ALL
            SELECT
                d.snapshot_date, d.captured_at, d.site_id, d.device_type, d.device_index,
                d.site_name, d.router_ip, d.site_type, d.region, d.device_ip, d.updated_at,
                d.fuel_level, d.mains_voltage_l1n, d.load_current_l1, d.engine_state,
                d.controller_mode, d.running_hours, d.total_fuel_consumption, d.num_starts,
                d.alarms, 0 AS source_priority
            FROM public.generator_d0 AS d
            WHERE d.device_type = 'generator'
        """
    else:
        source_rows = """
            SELECT
                h.snapshot_date, h.captured_at, h.site_id, h.device_type, h.device_index,
                h.site_name, h.router_ip, h.site_type, h.region, h.device_ip, h.updated_at,
                h.fuel_level, h.mains_voltage_l1n, h.load_current_l1, h.engine_state,
                h.controller_mode, h.running_hours, h.total_fuel_consumption, h.num_starts,
                h.alarms, 1 AS source_priority
            FROM public.generator_daily_history AS h
            WHERE h.device_type = 'generator'
        """

    site_filter = "AND canonical_site_id = :site_id" if site_id is not None else ""
    rows = db.execute(text(f"""
        WITH source_rows AS ({source_rows}), mapped_rows AS (
            SELECT
                source_rows.*,
                COALESCE(s.site_id, source_rows.site_id) AS canonical_site_id,
                COALESCE(NULLIF(source_rows.region, ''), s.region) AS canonical_region,
                COALESCE(source_rows.site_name, s.site_name) AS canonical_site_name,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        COALESCE(s.site_id, source_rows.site_id),
                        source_rows.device_type,
                        source_rows.device_index,
                        source_rows.snapshot_date
                    ORDER BY source_rows.source_priority, source_rows.captured_at DESC
                ) AS row_num
            FROM source_rows
            LEFT JOIN public.sites AS s
              ON lower(trim(s.site_name)) = lower(trim(source_rows.site_name))
        )
        SELECT
            device_index AS generator_id,
            canonical_site_id AS site_id,
            router_ip,
            canonical_site_name AS site,
            site_type,
            canonical_region AS reg,
            device_ip AS ip,
            alarms::text AS alarms,
            fuel_level AS fuel_percent,
            mains_voltage_l1n AS mains_voltage,
            load_current_l1 AS load_amperes,
            updated_at AS update_time,
            captured_at AS upload_time,
            snapshot_date,
            engine_state,
            controller_mode,
            running_hours,
            total_fuel_consumption,
            num_starts,
            device_type,
            device_index
        FROM mapped_rows
        WHERE row_num = 1 {site_filter}
        ORDER BY snapshot_date DESC, site, device_index
    """), {"site_id": site_id} if site_id is not None else {}).mappings().all()

    result = []
    for row in rows:
        item = dict(row)
        for field in ("update_time", "upload_time", "snapshot_date"):
            if item[field] is not None:
                item[field] = item[field].isoformat()
        result.append(item)
    return result


@app.get("/generators")
def list_all_generators(db: Session = Depends(get_db)):
    return read_generator_snapshot_rows(db)


@app.get("/analytics/generator-trends")
def generator_trends(
    metric: str = "running_hours",
    granularity: str = "month",
    reference_date: date | None = None,
    comparison_period: str = "none",
    db: Session = Depends(get_db),
):
    metric_labels = {
        "running_hours": "Running hours",
        "num_starts": "Number of starts",
    }
    if metric not in metric_labels:
        raise HTTPException(status_code=400, detail="Unsupported metric")
    if granularity not in {"month", "week"}:
        raise HTTPException(status_code=400, detail="Granularity must be month or week")
    if comparison_period not in {"none", "DoD", "WoW", "6DayBack", "MoM"}:
        raise HTTPException(status_code=400, detail="Unsupported comparison period")

    history_records = db.execute(text(f"""
        WITH ranked_history AS (
            SELECT
                h.device_index AS generator_id,
                COALESCE(s.site_id, h.site_id) AS site_id,
                COALESCE(h.site_name, s.site_name) AS site_name,
                COALESCE(NULLIF(h.region, ''), s.region) AS region,
                h.router_ip,
                COALESCE(NULLIF(h.region, ''), s.region) AS reg,
                h.device_ip AS ip,
                h.device_type,
                h.device_index,
                h.snapshot_date,
                h.{metric} AS metric_value,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        COALESCE(s.site_id, h.site_id),
                        h.device_type,
                        h.device_index,
                        h.snapshot_date
                    ORDER BY h.captured_at DESC, h.site_id DESC
                ) AS row_num
            FROM public.generator_daily_history AS h
            LEFT JOIN public.sites AS s
              ON lower(trim(s.site_name)) = lower(trim(h.site_name))
            WHERE h.device_type = 'generator'
              AND h.snapshot_date IS NOT NULL
        )
        SELECT
            generator_id, site_id, site_name, region, router_ip, reg, ip,
            device_type, device_index, snapshot_date, metric_value AS {metric}
        FROM ranked_history
        WHERE row_num = 1
    """)).mappings().all()

    columns = [
        "generator_id", "site_id", "site_name", "region", "router_ip", "reg", "ip",
        "device_type", "device_index", "snapshot_date", metric,
    ]
    df = pd.DataFrame(history_records, columns=columns)

    if not df.empty:
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce")
        df[metric] = pd.to_numeric(df[metric], errors="coerce")
        df = df.dropna(subset=["snapshot_date", metric])
        identity_columns = ["site_id", "device_type", "device_index"]
        df["device_key"] = df[identity_columns].fillna("").astype(str).agg("|".join, axis=1)
        df = df.sort_values(["snapshot_date", "generator_id"])
        df = df.drop_duplicates(subset=[*identity_columns, "snapshot_date"], keep="last")
        df["region_name"] = df.apply(lambda row: map_region_name(row["region"], row["site_name"]), axis=1)
        unmapped_sites = sorted(df.loc[df["region_name"].isna(), "site_name"].dropna().astype(str).unique())
        if unmapped_sites:
            logger.warning("Analytics skipped sites without a recognized region: %s", unmapped_sites)
        df = df.dropna(subset=["region_name"])

    try:
        baseline_rows = db.execute(text(
            "SELECT period_type, period_label, region_name, metric_name, delta_value "
            "FROM region_historical_baseline"
        )).mappings().all()
    except SQLAlchemyError as exc:
        db.rollback()
        logger.exception("Could not read region_historical_baseline")
        raise HTTPException(
            status_code=503,
            detail="Could not read region_historical_baseline; verify that the table and required columns exist.",
        ) from exc

    latest_operational_date = df["snapshot_date"].max() if not df.empty else None
    reference_year = (
        pd.Timestamp(reference_date).isocalendar().year
        if reference_date
        else latest_operational_date.isocalendar().year
        if latest_operational_date is not None
        else date.today().isocalendar().year
    )
    baseline_values = {}
    for baseline in baseline_rows:
        baseline_metric = normalize_baseline_metric(baseline["metric_name"])
        normalized_period_type = normalize_analytics_text(baseline["period_type"])
        if baseline_metric != metric:
            continue
        if granularity == "month" and normalized_period_type not in {"month", "monthly"}:
            continue
        if granularity == "week" and normalized_period_type not in {"week", "weekly"}:
            continue

        region_name = map_region_name(baseline["region_name"], None)
        period_start = parse_baseline_period(baseline["period_type"], baseline["period_label"], reference_year)
        delta_value = pd.to_numeric(baseline["delta_value"], errors="coerce")
        if region_name not in ANALYTICS_REGIONS or period_start is None or pd.isna(delta_value):
            continue
        key = (pd.Timestamp(period_start), region_name)
        baseline_values[key] = baseline_values.get(key, 0.0) + float(delta_value)

    reference_dates = []
    if latest_operational_date is not None:
        reference_dates.append(pd.Timestamp(latest_operational_date))
    if baseline_values:
        reference_dates.extend(period for period, _ in baseline_values)
    effective_reference = (
        pd.Timestamp(reference_date)
        if reference_date
        else max(reference_dates) if reference_dates else None
    )

    if not df.empty and effective_reference is not None:
        df = df[df["snapshot_date"] <= effective_reference].copy()

    live_weekly_values = {}
    live_period_query = None
    if effective_reference is not None:
        # Keep operational deltas at weekly grain. The monthly view is rolled
        # up from these same weekly regional variances below.
        period_unit = "week"
        live_period_query = text(f"""
            WITH ranked_snapshots AS (
                SELECT
                    g.device_index AS generator_id,
                    COALESCE(s.site_id, g.site_id) AS site_id,
                    COALESCE(g.site_name, s.site_name) AS site_name,
                    COALESCE(NULLIF(g.region, ''), s.region) AS region,
                    g.router_ip,
                    COALESCE(NULLIF(g.region, ''), s.region) AS reg,
                    g.device_ip AS ip,
                    g.device_type,
                    g.device_index,
                    g.snapshot_date,
                    g.{metric} AS metric_value,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            COALESCE(s.site_id, g.site_id),
                            g.device_type,
                            g.device_index,
                            g.snapshot_date
                        ORDER BY g.captured_at DESC, g.site_id DESC
                    ) AS row_num
                FROM public.generator_daily_history AS g
                LEFT JOIN public.sites AS s
                  ON lower(trim(s.site_name)) = lower(trim(g.site_name))
                WHERE g.device_type = 'generator'
                  AND g.snapshot_date IS NOT NULL
                  AND g.snapshot_date <= :reference_date
                  AND g.{metric} IS NOT NULL
            ), deduplicated_snapshots AS (
                SELECT *
                FROM ranked_snapshots
                WHERE row_num = 1
            ), deltas AS (
                SELECT
                    site_id,
                    site_name,
                    region,
                    device_type,
                    device_index,
                    snapshot_date,
                    metric_value,
                    LAG(metric_value) OVER (
                        PARTITION BY site_id, device_type, device_index
                        ORDER BY snapshot_date
                    ) AS previous_value
                FROM deduplicated_snapshots
            )
            SELECT
                date_trunc('{period_unit}', snapshot_date::timestamp)::date AS period_start,
                site_name,
                region,
                CASE
                    WHEN previous_value IS NULL THEN 0
                    WHEN metric_value >= previous_value THEN metric_value - previous_value
                    ELSE metric_value
                END AS period_value
            FROM deltas
        """)
        live_rows = db.execute(
            live_period_query,
            {"reference_date": effective_reference.date()},
        ).mappings().all()
        for row in live_rows:
            region_name = map_region_name(row["region"], row["site_name"])
            if region_name not in ANALYTICS_REGIONS:
                continue
            key = (pd.Timestamp(row["period_start"]), region_name)
            live_weekly_values[key] = live_weekly_values.get(key, 0.0) + float(row["period_value"] or 0)

    def reporting_month_for_week(week_start):
        # A week belongs to the month of its Sunday. For the in-progress week,
        # cap that date at the reference date so Week 40 on Sep 28 stays in Sep.
        week_end = pd.Timestamp(week_start) + pd.Timedelta(days=6)
        if effective_reference is not None:
            week_end = min(week_end, pd.Timestamp(effective_reference))
        return week_end.to_period("M").start_time

    if granularity == "week":
        live_values = live_weekly_values
    else:
        # Monthly operational values are the sum of weekly regional variances.
        live_values = {}
        for (week_start, region_name), value in live_weekly_values.items():
            key = (reporting_month_for_week(week_start), region_name)
            live_values[key] = live_values.get(key, 0.0) + value

    if effective_reference is not None:
        baseline_values = {
            key: value for key, value in baseline_values.items()
            if key[0] <= effective_reference
        }

    merged_values = dict(live_values)
    baseline_cells_used = 0
    period_sources = {key: "operational" for key in live_values}
    for key, value in baseline_values.items():
        if key not in live_values:
            merged_values[key] = value
            period_sources[key] = "baseline"
            baseline_cells_used += 1

    if merged_values:
        available_periods = sorted({period for period, _ in merged_values})
        frequency = "MS" if granularity == "month" else "W-MON"
        timeline = pd.date_range(available_periods[0], available_periods[-1], freq=frequency)
        grouped = pd.DataFrame(0.0, index=timeline, columns=ANALYTICS_REGIONS)
        for (period, region), value in merged_values.items():
            if period in grouped.index:
                grouped.at[period, region] = value
    else:
        grouped = pd.DataFrame(columns=ANALYTICS_REGIONS, index=pd.DatetimeIndex([]), dtype=float)

    if granularity == "month":
        label_for_period = lambda value: value.strftime("%b-%y")
        accumulated_series = grouped.sum(axis=1).cumsum()
    else:
        label_for_period = lambda value: f"Week {value.isocalendar().week} ({value.isocalendar().year})"
        # Group weekly accumulation by its reporting month, not Monday's month;
        # this keeps Week 36 (Aug 31–Sep 6) in September's running total.
        week_reporting_months = pd.Series(
            [reporting_month_for_week(period) for period in grouped.index],
            index=grouped.index,
        )
        accumulated_series = grouped.sum(axis=1).groupby(week_reporting_months).cumsum()

    total_series = grouped.sum(axis=1)
    visible_periods = grouped.index[-12:]

    def number_list(series, index):
        return [round(float(value), 2) for value in series.reindex(index, fill_value=0).tolist()]

    comparison = None
    if comparison_period != "none" and not df.empty:
        active_snapshot = df["snapshot_date"].max()
        if comparison_period == "DoD":
            previous_target = active_snapshot - pd.Timedelta(days=1)
        elif comparison_period == "WoW":
            previous_target = active_snapshot - pd.Timedelta(days=7)
        elif comparison_period == "6DayBack":
            previous_target = active_snapshot - pd.Timedelta(days=6)
        else:
            previous_target = active_snapshot - pd.DateOffset(months=1)

        def values_at_snapshot(target):
            eligible = df[df["snapshot_date"] <= target]
            if eligible.empty:
                return None, {}
            snapshot = eligible["snapshot_date"].max()
            values = (
                eligible[eligible["snapshot_date"] == snapshot]
                .groupby("region_name")[metric]
                .sum()
                .to_dict()
            )
            return snapshot, values

        current_date, current_values = values_at_snapshot(active_snapshot)
        previous_date, previous_values = values_at_snapshot(previous_target)
        comparison = {
            "current_date": current_date.date().isoformat() if current_date is not None else None,
            "previous_date": previous_date.date().isoformat() if previous_date is not None else None,
            "sites": list(ANALYTICS_REGIONS),
            "current": [round(float(current_values.get(region, 0)), 2) for region in ANALYTICS_REGIONS],
            "previous": [round(float(previous_values.get(region, 0)), 2) for region in ANALYTICS_REGIONS],
        }

    return {
        "metric": metric,
        "metric_label": metric_labels[metric],
        "granularity": granularity,
        "reference_date": effective_reference.date().isoformat() if effective_reference is not None else None,
        "labels": [label_for_period(value) for value in visible_periods],
        "sites": [
            {"name": region, "values": number_list(grouped[region], visible_periods)}
            for region in ANALYTICS_REGIONS
        ],
        "total": number_list(total_series, visible_periods),
        "accumulated": number_list(accumulated_series, visible_periods),
        "comparison": comparison,
        "baseline_cells_used": baseline_cells_used,
        "period_sources": [
            {
                "period": label_for_period(period),
                "regions": {
                    region: period_sources.get((pd.Timestamp(period), region), "empty")
                    for region in ANALYTICS_REGIONS
                },
            }
            for period in visible_periods
        ],
    }


@app.get("/sites/{site_id}/generators")
def list_generators(site_id: int, db: Session = Depends(get_db)):
    return read_generator_snapshot_rows(db, site_id=site_id)


@app.get("/sites/{site_id}/rectifiers")
def list_rectifiers(site_id: int, db: Session = Depends(get_db)):
    rectifiers = db.query(Rectifier).filter(Rectifier.site_id == site_id).all()
    return [
        {
            "rectifier_id": rectifier.rectifier_id,
            "site_id": rectifier.site_id,
            "status": rectifier.status,
            "manufacturer": rectifier.manufacturer,
            "model": rectifier.model,
            "output_voltage": rectifier.output_voltage,
            "output_current": rectifier.output_current,
            "temperature_c": rectifier.temperature_c,
            "last_service_date": rectifier.last_service_date,
            "notes": rectifier.notes,
            "upload_time": rectifier.upload_time.isoformat() if rectifier.upload_time else None,
            "snapshot_date": rectifier.snapshot_date.isoformat() if rectifier.snapshot_date else None,
        }
        for rectifier in rectifiers
    ]


@app.post("/upload-excel")
def upload_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith((".xls", ".xlsx", ".csv")):
        raise HTTPException(status_code=400, detail="Only Excel or CSV files are allowed.")

    logger.info("Upload started: filename=%s type=%s", file.filename, file.content_type)

    contents = file.file.read()
    buffer = BytesIO(contents)

    try:
        if file.filename.lower().endswith(".csv"):
            df = pd.read_csv(buffer)
        else:
            df = pd.read_excel(buffer)
    except Exception as exc:
        logger.exception("Failed to read Excel/CSV file: %s", file.filename)
        raise HTTPException(status_code=400, detail=f"Error reading file: {exc}")

    logger.info("Parsed file: rows=%s columns=%s", len(df.index), list(df.columns))
    snapshot_date = extract_snapshot_date(file.filename)
    logger.info("Snapshot date extracted from filename: %s", snapshot_date)

    upload = ExcelUpload(
        filename=file.filename,
        row_count=int(len(df.index)),
        status="uploaded",
        snapshot_date=snapshot_date,
        file_content=contents,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)

    raw_rows = [
        RawDataRow(
            upload_id=upload.id,
            source_row=index,
            data=json.dumps(json_safe_value(row.to_dict()), ensure_ascii=False),
        )
        for index, row in df.iterrows()
    ]
    db.add_all(raw_rows)

    matched_rows = 0
    ignored_rows = 0

    for index, row in df.iterrows():
        row_dict = row.to_dict()
        mapped = normalize_excel_row(row_dict)
        logger.debug("Row %s raw=%s mapped=%s", index, row_dict, mapped)

        if not mapped:
            logger.warning("Row %s skipped: no mapped fields recognized", index)
            ignored_rows += 1
            continue

        router_ip = (mapped.get("router_ip") or "").strip()
        if not router_ip:
            logger.warning("Row %s skipped: empty router_ip. mapped=%s", index, mapped)
            ignored_rows += 1
            continue

        site = db.query(Site).filter(Site.router_ip == router_ip).first()
        if not site:
            logger.warning("Row %s skipped: no Site match for router_ip=%s. mapped=%s", index, router_ip, mapped)
            ignored_rows += 1
            continue

        logger.info("Row %s matched: router_ip=%s -> site_id=%s site_name=%s", index, router_ip, site.site_id, site.site_name)

        sanitized = {key: sanitize_generator_value(key, value) for key, value in mapped.items()}
        field_debug = {
            "router_ip": router_ip,
            "reg": sanitized.get("reg"),
            "ip": sanitized.get("ip"),
            "alarms": sanitized.get("alarms"),
            "fuel_percent": sanitized.get("fuel_percent"),
            "mains_voltage": sanitized.get("mains_voltage"),
            "load_amperes": sanitized.get("load_amperes"),
            "update_time": sanitized.get("update_time"),
            "engine_state": sanitized.get("engine_state"),
            "controller_mode": sanitized.get("controller_mode"),
            "running_hours": sanitized.get("running_hours"),
            "total_fuel_consumption": sanitized.get("total_fuel_consumption"),
            "num_starts": sanitized.get("num_starts"),
        }
        logger.info("Row %s fields before insert: %s", index, field_debug)

        try:
            generator = Generator(
                site_id=site.site_id,
                router_ip=router_ip,
                site=site.site_name,
                site_type=site.site_type,
                reg=sanitized.get("reg"),
                ip=sanitized.get("ip"),
                alarms=sanitized.get("alarms"),
                fuel_percent=sanitized.get("fuel_percent"),
                mains_voltage=sanitized.get("mains_voltage"),
                load_amperes=sanitized.get("load_amperes"),
                update_time=str(sanitized.get("update_time")) if sanitized.get("update_time") is not None else None,
                upload_time=upload.created_at,
                snapshot_date=snapshot_date,
                engine_state=sanitized.get("engine_state"),
                controller_mode=sanitized.get("controller_mode"),
                running_hours=sanitized.get("running_hours"),
                total_fuel_consumption=sanitized.get("total_fuel_consumption"),
                num_starts=sanitized.get("num_starts"),
            )
            db.add(generator)
            matched_rows += 1
        except Exception as exc:
            logger.exception("Row %s failed final insert sanitization: %s | sanitized=%s", index, exc, field_debug)
            ignored_rows += 1
            continue

    db.commit()
    logger.info("Upload completed: filename=%s matched_rows=%s ignored_rows=%s", file.filename, matched_rows, ignored_rows)

    return {
        "message": "File uploaded successfully",
        "upload_id": upload.id,
        "filename": upload.filename,
        "row_count": len(df.index),
        "matched_rows": matched_rows,
        "ignored_rows": ignored_rows,
    }


@app.get("/uploads")
def list_uploads(db: Session = Depends(get_db)):
    uploads = db.query(
        ExcelUpload.id,
        ExcelUpload.filename,
        ExcelUpload.created_at,
        ExcelUpload.snapshot_date,
        ExcelUpload.row_count,
        ExcelUpload.status,
        ExcelUpload.file_content.isnot(None).label("has_original_file"),
    ).all()
    return [{
        "id": item.id,
        "filename": item.filename,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot_date": item.snapshot_date.isoformat() if item.snapshot_date else None,
        "row_count": item.row_count,
        "status": item.status,
        "has_original_file": item.has_original_file,
    } for item in uploads]


@app.get("/uploads/{upload_id}/download")
def download_upload(upload_id: int, db: Session = Depends(get_db)):
    upload = db.query(ExcelUpload).filter(ExcelUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload record not found")

    if upload.file_content:
        filename = Path(str(upload.filename).replace("\\", "/")).name
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return StreamingResponse(
            BytesIO(upload.file_content),
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    raw_rows = db.query(RawDataRow).filter(
        RawDataRow.upload_id == upload_id
    ).order_by(RawDataRow.source_row).all()
    if not raw_rows:
        raise HTTPException(status_code=404, detail="No stored file or raw rows are available for this upload")

    parsed_rows = [json.loads(row.data) for row in raw_rows]
    excel_buffer = BytesIO()
    pd.DataFrame(parsed_rows).to_excel(excel_buffer, index=False, engine="openpyxl")
    excel_buffer.seek(0)
    source_filename = str(upload.filename).replace("\\", "/")
    filename = f"{Path(source_filename).stem}_reconstructed.xlsx"
    return StreamingResponse(
        excel_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.delete("/uploads/{upload_id}")
def delete_upload_history(upload_id: int, db: Session = Depends(get_db)):
    upload = db.query(ExcelUpload).filter(ExcelUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Upload record not found")

    raw_rows = db.query(RawDataRow).filter(RawDataRow.upload_id == upload_id).all()
    for row in raw_rows:
        db.delete(row)

    db.delete(upload)
    db.commit()
    return {"message": "Upload history record deleted", "upload_id": upload_id}
