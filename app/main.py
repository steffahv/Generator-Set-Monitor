import re
import logging
import math
import os
from pathlib import Path
from datetime import date, datetime
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect
import pandas as pd
import json
from io import BytesIO

load_dotenv()
logger = logging.getLogger("myappge.upload")
logging.basicConfig(level=logging.INFO)

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


@app.get("/generators")
def list_all_generators(db: Session = Depends(get_db)):
    generators = db.query(Generator).all()
    return [
        {
            "generator_id": generator.generator_id,
            "site_id": generator.site_id,
            "router_ip": generator.router_ip,
            "site": generator.site,
            "site_type": generator.site_type,
            "reg": generator.reg,
            "ip": generator.ip,
            "alarms": generator.alarms,
            "fuel_percent": generator.fuel_percent,
            "mains_voltage": generator.mains_voltage,
            "load_amperes": generator.load_amperes,
            "update_time": generator.update_time,
            "upload_time": generator.upload_time.isoformat() if generator.upload_time else None,
            "snapshot_date": generator.snapshot_date.isoformat() if generator.snapshot_date else None,
            "engine_state": generator.engine_state,
            "controller_mode": generator.controller_mode,
            "running_hours": generator.running_hours,
            "total_fuel_consumption": generator.total_fuel_consumption,
            "num_starts": generator.num_starts,
        }
        for generator in generators
    ]


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

    records = db.query(
        Generator.generator_id,
        Generator.site_id,
        Site.site_name,
        Generator.router_ip,
        Generator.reg,
        Generator.ip,
        Generator.snapshot_date,
        getattr(Generator, metric),
    ).join(Site, Site.site_id == Generator.site_id).filter(Generator.snapshot_date.isnot(None)).all()

    columns = ["generator_id", "site_id", "site_name", "router_ip", "reg", "ip", "snapshot_date", metric]
    df = pd.DataFrame(records, columns=columns)
    if df.empty:
        return {
            "metric": metric,
            "metric_label": metric_labels[metric],
            "granularity": granularity,
            "reference_date": None,
            "labels": [],
            "sites": [],
            "total": [],
            "accumulated": [],
            "comparison": None,
        }

    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"], errors="coerce")
    df[metric] = pd.to_numeric(df[metric], errors="coerce")
    df = df.dropna(subset=["snapshot_date", metric])
    if df.empty:
        return {
            "metric": metric,
            "metric_label": metric_labels[metric],
            "granularity": granularity,
            "reference_date": None,
            "labels": [],
            "sites": [],
            "total": [],
            "accumulated": [],
            "comparison": None,
        }

    identity_columns = ["site_id", "router_ip", "reg", "ip"]
    df["device_key"] = df[identity_columns].fillna("").astype(str).agg("|".join, axis=1)
    df = df.sort_values(["snapshot_date", "generator_id"])
    df = df.drop_duplicates(subset=[*identity_columns, "snapshot_date"], keep="last")
    effective_reference = pd.Timestamp(reference_date) if reference_date else df["snapshot_date"].max()
    df = df[df["snapshot_date"] <= effective_reference].copy()
    if df.empty:
        return {
            "metric": metric,
            "metric_label": metric_labels[metric],
            "granularity": granularity,
            "reference_date": effective_reference.date().isoformat(),
            "labels": [],
            "sites": [],
            "total": [],
            "accumulated": [],
            "comparison": None,
        }

    df = df.sort_values(["device_key", "snapshot_date"])
    reading_delta = df.groupby("device_key")[metric].diff()
    # If a cumulative counter was reset, count the new reading as usage since reset.
    df["period_value"] = reading_delta.where(reading_delta >= 0, df[metric]).fillna(0)

    if granularity == "month":
        df["period_start"] = df["snapshot_date"].dt.to_period("M").dt.start_time
        label_for_period = lambda value: value.strftime("%b-%y")
    else:
        df["period_start"] = (
            df["snapshot_date"] - pd.to_timedelta(df["snapshot_date"].dt.weekday, unit="D")
        ).dt.normalize()
        label_for_period = lambda value: f"Week {value.isocalendar().week} ({value.strftime('%Y')})"

    grouped = df.groupby(["period_start", "site_name"])["period_value"].sum().unstack(fill_value=0).sort_index()
    site_names = sorted(grouped.columns.tolist())
    grouped = grouped.reindex(columns=site_names, fill_value=0)
    total_series = grouped.sum(axis=1)
    accumulated_series = total_series.cumsum()
    visible_periods = grouped.index[-12:]

    def number_list(series, index):
        return [round(float(value), 2) for value in series.reindex(index, fill_value=0).tolist()]

    comparison = None
    if comparison_period != "none":
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
                .groupby("site_name")[metric]
                .sum()
                .to_dict()
            )
            return snapshot, values

        current_date, current_values = values_at_snapshot(active_snapshot)
        previous_date, previous_values = values_at_snapshot(previous_target)
        comparison_sites = sorted(set(current_values) | set(previous_values))
        comparison = {
            "current_date": current_date.date().isoformat() if current_date is not None else None,
            "previous_date": previous_date.date().isoformat() if previous_date is not None else None,
            "sites": comparison_sites,
            "current": [round(float(current_values.get(site, 0)), 2) for site in comparison_sites],
            "previous": [round(float(previous_values.get(site, 0)), 2) for site in comparison_sites],
        }

    return {
        "metric": metric,
        "metric_label": metric_labels[metric],
        "granularity": granularity,
        "reference_date": effective_reference.date().isoformat(),
        "labels": [label_for_period(value) for value in visible_periods],
        "sites": [
            {"name": site, "values": number_list(grouped[site], visible_periods)}
            for site in site_names
        ],
        "total": number_list(total_series, visible_periods),
        "accumulated": number_list(accumulated_series, visible_periods),
        "comparison": comparison,
    }


@app.get("/sites/{site_id}/generators")
def list_generators(site_id: int, db: Session = Depends(get_db)):
    generators = db.query(Generator).filter(Generator.site_id == site_id).all()
    return [
        {
            "generator_id": generator.generator_id,
            "site_id": generator.site_id,
            "router_ip": generator.router_ip,
            "site": generator.site,
            "site_type": generator.site_type,
            "reg": generator.reg,
            "ip": generator.ip,
            "alarms": generator.alarms,
            "fuel_percent": generator.fuel_percent,
            "mains_voltage": generator.mains_voltage,
            "load_amperes": generator.load_amperes,
            "update_time": generator.update_time,
            "upload_time": generator.upload_time.isoformat() if generator.upload_time else None,
            "snapshot_date": generator.snapshot_date.isoformat() if generator.snapshot_date else None,
            "engine_state": generator.engine_state,
            "controller_mode": generator.controller_mode,
            "running_hours": generator.running_hours,
            "total_fuel_consumption": generator.total_fuel_consumption,
            "num_starts": generator.num_starts,
        }
        for generator in generators
    ]


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
    uploads = db.query(ExcelUpload).all()
    return [{
        "id": item.id,
        "filename": item.filename,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "snapshot_date": item.snapshot_date.isoformat() if item.snapshot_date else None,
        "row_count": item.row_count,
        "status": item.status,
    } for item in uploads]


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
