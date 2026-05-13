"""SQLAlchemy engine, session, and ORM models (MySQL or SQLite)."""
import os
import logging
from datetime import datetime
from typing import Generator

from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime, Boolean, Text, func,
)
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import get_database_url, is_mysql

logger = logging.getLogger(__name__)

Base = declarative_base()
_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        url = get_database_url()
        kwargs = {"pool_pre_ping": True, "future": True}
        if is_mysql(url):
            kwargs["pool_recycle"] = 280
        _engine = create_engine(url, **kwargs)
        logger.info("Database engine created (%s)", "mysql" if is_mysql(url) else "sqlite")
    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SessionLocal


def init_db():
    Base.metadata.create_all(bind=get_engine())


def session_scope() -> Generator:
    SessionLocal = get_session_factory()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class SolarWindData(Base):
    __tablename__ = "solar_wind_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    source = Column(String(64), nullable=False)

    bz_gsm = Column(Float, nullable=True)
    by_gsm = Column(Float, nullable=True)
    bx_gsm = Column(Float, nullable=True)
    bt_total = Column(Float, nullable=True)
    sw_speed_kmps = Column(Float, nullable=True)
    proton_density_ccm = Column(Float, nullable=True)
    proton_temp_K = Column(Float, nullable=True)
    xray_flux_Wm2 = Column(Float, nullable=True)
    kp_current = Column(Float, nullable=True)

    quality_flag = Column(String(24), nullable=False, default="UNKNOWN")
    is_interpolated = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class AdvisoryRecord(Base):
    __tablename__ = "advisory_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    generated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    advisory_source = Column(String(32), nullable=False)
    payload_json = Column(Text, nullable=False)
