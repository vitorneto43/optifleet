# core/fleet_models.py
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from core.db import Base

class Driver(Base):
    __tablename__ = "drivers"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    name = Column(String, index=True)
    cnh = Column(String)
    phone = Column(String)

    # <- relacionamento reverso: aponta para Vehicle.driver
    vehicles = relationship("Vehicle", back_populates="driver")

class Vehicle(Base):
    __tablename__ = "vehicles"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, index=True, nullable=True)
    code = Column(String, index=True, nullable=False)   # placa/código (não único por tenant se preferir)
    model = Column(String)
    capacity = Column(Integer, default=0)
    avg_consumption_km_l = Column(Float, default=0.0)
    active = Column(Boolean, default=True)

    tracker_id = Column(String, index=True, nullable=True)  # IMEI/ID do rastreador

    driver_id = Column(Integer, ForeignKey("drivers.id"), nullable=True)
    # <- relacionamento direto: precisa existir para casar com Driver.vehicles
    driver = relationship("Driver", back_populates="vehicles")

class MaintenanceEvent(Base):
    __tablename__ = "maintenance_events"
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    type = Column(String)  # oleo, pneus, revisão
    when_km = Column(Integer, default=0)
    note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Telemetry(Base):
    __tablename__ = "telemetry"
    id = Column(Integer, primary_key=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    lat = Column(Float)
    lon = Column(Float)
    speed_kmh = Column(Float, default=0.0)
    fuel_pct = Column(Float, default=0.0)
    engine_temp = Column(Float, default=0.0)
    odometer_km = Column(Float, default=0.0)
    obd_alerts = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class RoutePlan(Base):
    __tablename__ = "route_plans"
    id = Column(Integer, primary_key=True)
    plan_key = Column(String, index=True)
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    objective = Column(String)
    total_km = Column(Float, default=0.0)
    total_min = Column(Float, default=0.0)
    co2_kg = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

class RouteExecution(Base):
    __tablename__ = "route_exec"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("route_plans.id"))
    vehicle_id = Column(Integer, ForeignKey("vehicles.id"))
    started_at = Column(DateTime)
    finished_at = Column(DateTime, nullable=True)
    executed_km = Column(Float, default=0.0)
    executed_min = Column(Float, default=0.0)

class StopExecution(Base):
    __tablename__ = "stop_exec"
    id = Column(Integer, primary_key=True)
    route_exec_id = Column(Integer, ForeignKey("route_exec.id"))
    stop_id = Column(String)
    planned_arrival = Column(DateTime, nullable=True)
    actual_arrival = Column(DateTime, nullable=True)
    late_min = Column(Integer, default=0)
