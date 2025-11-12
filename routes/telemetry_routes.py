# routes/telemetry_routes.py
from flask import Blueprint, request, jsonify, Response
from flask_login import login_required, current_user
from datetime import datetime, timezone
import json, time

from core.db import (
    get_tracker_owner, insert_telemetry, latest_positions,
    tracker_get_or_create, tracker_bind_vehicle,
    get_conn,  # ✅ IMPORTA get_conn
)

bp_tele = Blueprint("telemetry", __name__, url_prefix="/api")

@bp_tele.post("/trackers/link")
@login_required
def link_tracker():
    data = request.get_json(force=True) or {}
    imei = str(data.get("imei", "")).strip()
    token = str(data.get("secret_token", "")).strip()
    vehicle_id = str(data.get("vehicle_id", "")).strip() or None
    if not imei or not token:
        return jsonify({"ok": False, "error": "imei e secret_token são obrigatórios"}), 400

    # ✅ usa as funções que você importou
    tracker = tracker_get_or_create(client_id=str(current_user.id), imei=imei, secret_token=token)
    if vehicle_id:
        tracker_bind_vehicle(client_id=str(current_user.id), imei=imei, vehicle_id=vehicle_id)
    return jsonify({"ok": True})

@bp_tele.post("/trackers/ingest")
def ingest():
    token = (request.headers.get("X-Tracker-Token") or "").strip()
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "JSON inválido"}), 400

    imei = str(data.get("imei", "")).strip()
    if not imei or not token:
        return jsonify({"ok": False, "error": "imei e token obrigatórios"}), 400

    owner = get_tracker_owner(imei, token)
    if not owner:
        return jsonify({"ok": False, "error": "tracker/token inválidos"}), 403
    client_id, bound_vehicle = owner

    if "lat" not in data or "lon" not in data:
        return jsonify({"ok": False, "error": "lat e lon são obrigatórios"}), 400
    try:
        lat = float(data["lat"]); lon = float(data["lon"])
    except Exception:
        return jsonify({"ok": False, "error": "lat/lon inválidos"}), 400

    vehicle_id = (str(data.get("vehicle_id", "")).strip() or bound_vehicle or "UNKNOWN")
    speed = float(data.get("speed", 0.0))
    fuel = float(data.get("fuel", 0.0))
    ts = data.get("timestamp")
    if ts:
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    insert_telemetry(client_id, vehicle_id, ts, lat, lon, speed, fuel)
    return jsonify({"ok": True})

@bp_tele.get("/telemetry/latest")
@login_required
def api_latest():
    rows = latest_positions(str(current_user.id))
    out = [{
        "vehicle_id": r[0],
        "lat": r[1],
        "lon": r[2],
        "speed": r[3],
        "fuel": r[4],
        "ts": r[5].isoformat() if r[5] else None
    } for r in rows]
    return jsonify(out)

@bp_tele.get("/telemetry/stream")
@login_required
def stream():
    client_id = str(current_user.id)
    def gen():
        try:
            while True:
                rows = latest_positions(client_id)
                payload = [{
                    "vehicle_id": r[0],
                    "lat": r[1],
                    "lon": r[2],
                    "speed": r[3],
                    "fuel": r[4],
                    "ts": r[5].isoformat() if r[5] else None
                } for r in rows]
                yield f"data: {json.dumps(payload)}\n\n"
                time.sleep(2)
        except GeneratorExit:
            return
        except Exception:
            time.sleep(2)
    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

# ✅ CORRIGIDO: sem /api extra; com filtro por dono; e nomes de coluna alinhados
@bp_tele.get("/telemetry/last_many")
@login_required
def api_last_many():
    ids = [int(x) for x in (request.args.get('ids') or '').split(',') if x.strip().isdigit()]
    if not ids:
        return jsonify([])

    qmarks = ",".join("?" for _ in ids)
    sql = f"""
    WITH last_pos AS (
      SELECT p.vehicle_id, MAX(p.id) AS last_id
      FROM positions p
      WHERE p.vehicle_id IN ({qmarks})
      GROUP BY p.vehicle_id
    )
    SELECT v.id, v.name, v.imei,
           p.lat AS lat, p.lon AS lon, p.speed AS speed, p.timestamp AS ts
    FROM vehicles v
    LEFT JOIN last_pos lp ON lp.vehicle_id = v.id
    LEFT JOIN positions p ON p.id = lp.last_id
    WHERE v.id IN ({qmarks}) AND v.owner_id = ?  -- 🔒 escopo por dono
    ORDER BY v.id;
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (*ids, *ids, str(current_user.id))).fetchall()
    # normaliza timestamp para ISO (caso venha string)
    out = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("ts"), datetime):
            d["ts"] = d["ts"].isoformat()
        out.append(d)
    return jsonify(out)

# routes/telemetry_routes.py
@bp_tele.get("/telemetry/<vehicle_id>")
@login_required
def api_vehicle_track(vehicle_id):
    """Últimos 200 pontos do veículo (ajuste o LIMIT se quiser)."""
    sql = """
      SELECT lat, lon, timestamp AS ts
      FROM positions
      WHERE vehicle_id = ?
      ORDER BY id DESC
      LIMIT 200
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (int(vehicle_id),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # normaliza tipos
        d["lat"] = float(d["lat"])
        d["lon"] = float(d["lon"])
        if hasattr(d["ts"], "isoformat"):
            d["ts"] = d["ts"].isoformat()
        out.append(d)
    return jsonify(out)




