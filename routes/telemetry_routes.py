# routes/telemetry_routes.py
from flask import Blueprint, request, jsonify, Response, render_template
from core.db import SessionLocal
from core.fleet_models import Telemetry
from datetime import datetime
import json

bp_tele = Blueprint("telemetry", __name__)
_subscribers = set()  # simples: clientes SSE

@bp_tele.post("/api/telemetry")
def ingest_telemetry():
    s = SessionLocal()
    try:
        p = request.json
        t = Telemetry(
            vehicle_id=p["vehicle_id"], lat=p["lat"], lon=p["lon"],
            speed_kmh=p.get("speed_kmh", 0.0), fuel_pct=p.get("fuel_pct", 0.0),
            engine_temp=p.get("engine_temp", 0.0), odometer_km=p.get("odometer_km", 0.0),
            obd_alerts=p.get("obd_alerts", 0)
        )
        s.add(t); s.commit(); s.refresh(t)
        # envia atualização para assinantes SSE
        data = {"vehicle_id": t.vehicle_id, "lat": t.lat, "lon": t.lon, "speed": t.speed_kmh, "when": t.created_at.isoformat()}
        dead = []
        for q in list(_subscribers):
            try: q.put_nowait(json.dumps(data))
            except Exception: dead.append(q)
        for q in dead: _subscribers.discard(q)
        return jsonify({"id": t.id}), 201
    finally:
        s.close()

@bp_tele.get("/api/telemetry/stream")
def telemetry_stream():
    # Server-Sent Events simples (subscribe)
    from queue import Queue
    q = Queue()
    _subscribers.add(q)
    def gen():
        try:
            while True:
                msg = q.get()
                yield f"data: {msg}\n\n"
        except GeneratorExit:
            _subscribers.discard(q)
    return Response(gen(), mimetype="text/event-stream")

# ---------- NOVO: página ----------
@bp_tele.get("/telemetry")
def telemetry_page():
    return render_template("telemetry.html")

# ---------- NOVO: último fixo por veículo ----------
@bp_tele.get("/api/telemetry/latest")
def telemetry_latest():
    s = SessionLocal()
    try:
        # pega os últimos N registros e deduplica por vehicle_id
        q = s.query(Telemetry).order_by(Telemetry.created_at.desc()).limit(1000).all()
        latest = {}
        for t in q:
            if t.vehicle_id not in latest:
                latest[t.vehicle_id] = t
        out = []
        for vid, t in latest.items():
            out.append({
                "vehicle_id": vid,
                "lat": t.lat,
                "lon": t.lon,
                "speed_kmh": t.speed_kmh,
                "fuel_pct": t.fuel_pct,
                "engine_temp": t.engine_temp,
                "created_at": t.created_at.isoformat()
            })
        return jsonify(out)
    finally:
        s.close()

# ---------- NOVO: histórico curto por veículo ----------
@bp_tele.get("/api/telemetry/<int:vehicle_id>")
def vehicle_telemetry(vehicle_id: int):
    s = SessionLocal()
    try:
        q = (
            s.query(Telemetry)
            .filter(Telemetry.vehicle_id == vehicle_id)
            .order_by(Telemetry.created_at.desc())
            .limit(50)
            .all()
        )
        return jsonify([
            {
                "lat": t.lat, "lon": t.lon, "speed_kmh": t.speed_kmh,
                "fuel_pct": t.fuel_pct, "engine_temp": t.engine_temp,
                "created_at": t.created_at.isoformat()
            } for t in q
        ])
    finally:
        s.close()

