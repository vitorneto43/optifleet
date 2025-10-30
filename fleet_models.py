# core/fleet_models.py
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text,
    UniqueConstraint, Index
)
from sqlalchemy.orm import relationship

# 👉 Base vem do SQLAlchemy (core/db.py – parte SQLAlchemy)
from core.db import Base


class Driver(Base):
    __tablename__ = "drivers"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, index=True, nullable=False)
    cnh = Column(String, nullable=True)
    phone = Column(String, nullable=True)

    # relação reversa com Vehicle.driver
    vehicles = relationship("Vehicle", back_populates="driver")


class Vehicle(Base):
    """
    Tabela de cadastro de frota (SQLAlchemy). A posição/telemetria em si
    é armazenada no DuckDB (ver core/db.py: duck_con + tabela telemetry).
    """
    __tablename__ = "fleet_vehicles"   # <- evita colidir com DuckDB. Mude aqui se quiser manter "vehicles".
    id = Column(Integer, primary_key=True)

    tenant_id = Column(Integer, index=True, nullable=True)
    code = Column(String, index=True, nullable=False)  # placa/código visível
    model = Column(String, nullable=True)
    capacity = Column(Integer, default=0)
    avg_consumption_km_l = Column(Float, default=0.0)
    active = Column(Boolean, default=True)

    tracker_id = Column(String, index=True, nullable=True)  # IMEI/ID do rastreador

    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True)
    driver = relationship("Driver", back_populates="vehicles")

    __table_args__ = (
        # (opcional) unicidade por tenant + code
        UniqueConstraint("tenant_id", "code", name="uq_fleet_vehicle_tenant_code"),
        Index("ix_fleet_vehicle_tenant", "tenant_id"),
    )


class MaintenanceEvent(Base):
    __tablename__ = "maintenance_events"
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("fleet_vehicles.id"), index=True)
    type = Column(String)  # oleo, pneus, revisão...
    when_km = Column(Integer, default=0)
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


# 🔇 IMPORTANTE:
# Abaixo estava uma tabela SQLAlchemy chamada "telemetry".
# Como agora usamos DuckDB para telemetria (tabela telemetry já existe no DuckDB),
# COMENTAMOS este modelo para evitar conflito de nomes e duplicidade de armazenamento.
#
# Se você realmente quiser manter alguma trilha de eventos via SQLAlchemy,
# renomeie a tabela (ex.: "telemetry_events") e ajuste o restante do código.

# class Telemetry(Base):
#     __tablename__ = "telemetry_events"  # renomeada para não colidir com DuckDB
#     id = Column(Integer, primary_key=True)
#     vehicle_id = Column(Integer, ForeignKey("fleet_vehicles.id"), index=True)
#     lat = Column(Float)
#     lon = Column(Float)
#     speed_kmh = Column(Float, default=0.0)
#     fuel_pct = Column(Float, default=0.0)
#     engine_temp = Column(Float, default=0.0)
#     odometer_km = Column(Float, default=0.0)
#     obd_alerts = Column(Integer, default=0)
#     created_at = Column(DateTime, default=datetime.utcnow)


class RoutePlan(Base):
    __tablename__ = "route_plans"
    id = Column(Integer, primary_key=True)
    plan_key = Column(String, index=True)  # para agrupar execuções
    vehicle_id = Column(Integer, ForeignKey("fleet_vehicles.id"), index=True)
    objective = Column(String)
    total_km = Column(Float, default=0.0)
    total_min = Column(Float, default=0.0)
    co2_kg = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)


class RouteExecution(Base):
    __tablename__ = "route_exec"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("route_plans.id"), index=True)
    vehicle_id = Column(Integer, ForeignKey("fleet_vehicles.id"), index=True)
    started_at = Column(DateTime)
    finished_at = Column(DateTime, nullable=True)
    executed_km = Column(Float, default=0.0)
    executed_min = Column(Float, default=0.0)


class StopExecution(Base):
    __tablename__ = "stop_exec"
    id = Column(Integer, primary_key=True)
    route_exec_id = Column(Integer, ForeignKey("route_exec.id"), index=True)
    stop_id = Column(String)  # id lógico da parada
    planned_arrival = Column(DateTime, nullable=True)
    actual_arrival = Column(DateTime, nullable=True)
    late_min = Column(Integer, default=0)
