import re
import logging
import math
from datetime import datetime
from fastapi import FastAPI, File, UploadFile, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import text, inspect
import pandas as pd
import json
from io import BytesIO

logger = logging.getLogger("myappge.upload")
logging.basicConfig(level=logging.INFO)

from app.database import Base, engine, get_db
from app.mapping import normalize_excel_row
from app.models import ExcelUpload, RawDataRow, Site, Generator, Rectifier

app = FastAPI(title="myapp-ge", version="0.1.0")


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
