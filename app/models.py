from sqlalchemy import Column, Integer, String, DateTime, Float, Text, ForeignKey, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class ExcelUpload(Base):
    __tablename__ = "excel_uploads"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255), nullable=False)
    row_count = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="uploaded")
    snapshot_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class RawDataRow(Base):
    __tablename__ = "raw_data_rows"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, index=True, nullable=False)
    source_row = Column(Integer, nullable=False)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Site(Base):
    __tablename__ = "sites"

    site_id = Column(Integer, primary_key=True, index=True)
    router_ip = Column(String(64), unique=True, nullable=False, index=True)
    site_name = Column(String(255), nullable=False)
    site_type = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    alarms = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    generators = relationship("Generator", back_populates="site_rel", cascade="all, delete-orphan")
    rectifiers = relationship("Rectifier", back_populates="site", cascade="all, delete-orphan")


class Generator(Base):
    __tablename__ = "generators"

    generator_id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.site_id"), nullable=False, index=True)

    router_ip = Column(String(64), nullable=True)
    site = Column(String(255), nullable=True)
    site_type = Column(String(100), nullable=True)
    reg = Column(String(100), nullable=True)
    ip = Column(String(64), nullable=True)
    alarms = Column(String(255), nullable=True)
    fuel_percent = Column(Float, nullable=True)
    mains_voltage = Column(Float, nullable=True)
    load_amperes = Column(Float, nullable=True)
    update_time = Column(String(100), nullable=True)
    upload_time = Column(DateTime(timezone=True), nullable=True)
    snapshot_date = Column(Date, nullable=True)
    engine_state = Column(String(100), nullable=True)
    controller_mode = Column(String(100), nullable=True)
    running_hours = Column(Float, nullable=True)
    total_fuel_consumption = Column(Float, nullable=True)
    num_starts = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    site_rel = relationship("Site", back_populates="generators")


class Rectifier(Base):
    __tablename__ = "rectifiers"

    rectifier_id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("sites.site_id"), nullable=False, index=True)
    status = Column(String(50), nullable=True)
    manufacturer = Column(String(150), nullable=True)
    model = Column(String(150), nullable=True)
    output_voltage = Column(Float, nullable=True)
    output_current = Column(Float, nullable=True)
    temperature_c = Column(Float, nullable=True)
    last_service_date = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)
    upload_time = Column(DateTime(timezone=True), nullable=True)
    snapshot_date = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    site = relationship("Site", back_populates="rectifiers")
