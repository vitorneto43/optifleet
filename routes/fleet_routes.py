from flask import Blueprint, request, jsonify
from core.db import SessionLocal
from core.fleet_models import Vehicle, Driver, MaintenanceEvent

bp_fleet = Blueprint("fleet", __name__)

@bp_fleet.post("/api/drivers")
def create_driver():
    s = SessionLocal()
    try:
        d = Driver(name=request.json["name"], cnh=request.json.get("cnh",""), phone=request.json.get("phone",""))
        s.add(d); s.commit(); s.refresh(d)
        return jsonify({"id": d.id, "name": d.name}), 201
    finally:
        s.close()

@bp_fleet.get("/api/drivers")
def list_drivers():
    s = SessionLocal()
    try:
        data = [{"id": d.id, "name": d.name, "phone": d.phone} for d in s.query(Driver).all()]
        return jsonify(data)
    finally:
        s.close()

@bp_fleet.post("/api/vehicles")
def create_vehicle():
    s = SessionLocal()
    try:
        payload = request.json
        v = Vehicle(
            code=payload["code"],
            model=payload.get("model",""),
            capacity=int(payload.get("capacity",0)),
            avg_consumption_km_l=float(payload.get("avg_consumption_km_l",0)),
            driver_id=payload.get("driver_id")
        )
        s.add(v); s.commit(); s.refresh(v)
        return jsonify({"id": v.id, "code": v.code}), 201
    finally:
        s.close()

@bp_fleet.get("/api/vehicles")
def list_vehicles():
    s = SessionLocal()
    try:
        q = s.query(Vehicle).all()
        data = []
        for v in q:
            data.append({
                "id": v.id,
                "code": v.code,
                "model": v.model,
                "capacity": v.capacity,
                "avg_consumption_km_l": v.avg_consumption_km_l,
                "driver_id": v.driver_id,
                "tracker_id": v.tracker_id,   # 👈 incluir
            })
        return jsonify(data)
    finally:
        s.close()

@bp_fleet.post("/api/maintenance")
def add_maintenance():
    s = SessionLocal()
    try:
        payload = request.json
        ev = MaintenanceEvent(
            vehicle_id=payload["vehicle_id"],
            type=payload.get("type","revisao"),
            when_km=int(payload.get("when_km",0)),
            note=payload.get("note","")
        )
        s.add(ev); s.commit(); s.refresh(ev)
        return jsonify({"id": ev.id}), 201
    finally:
        s.close()
@bp_fleet.patch("/api/vehicles/<int:vehicle_id>/bind_tracker")
def bind_tracker(vehicle_id: int):
    s = SessionLocal()
    try:
        v = s.query(Vehicle).get(vehicle_id)
        if not v:
            return jsonify({"error":"vehicle_not_found"}), 404
        tracker_id = request.json.get("tracker_id","").strip()
        if not tracker_id:
            return jsonify({"error":"tracker_id_required"}), 400
        # garantir unicidade
        exists = s.query(Vehicle).filter(Vehicle.tracker_id == tracker_id, Vehicle.id != vehicle_id).first()
        if exists:
            return jsonify({"error":"tracker_id_in_use"}), 409
        v.tracker_id = tracker_id
        s.commit()
        return jsonify({"ok": True, "vehicle_id": v.id, "tracker_id": v.tracker_id})
    finally:
        s.close()

