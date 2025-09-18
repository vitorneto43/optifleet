# routes/health_routes.py
from flask import Blueprint, jsonify
from core.db import SessionLocal
from core.fleet_models import Vehicle, Telemetry
from core.services.health import maintenance_risk_score, risk_color

bp_health = Blueprint("health", __name__)

@bp_health.get("/api/fleet/health")
def fleet_health():
    s = SessionLocal()
    try:
        items = []
        for v in s.query(Vehicle).all():
            t = s.query(Telemetry).filter(Telemetry.vehicle_id==v.id).order_by(Telemetry.created_at.desc()).first()
            if t:
                score = maintenance_risk_score(int(t.odometer_km), 60, int(t.obd_alerts))
                items.append({
                    "vehicle_id": v.id,
                    "code": v.code,
                    "score": score,
                    "color": risk_color(score)
                })
        return jsonify(items)
    finally:
        s.close()
