# routes/fleet_routes.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone
from core.db import list_vehicles, upsert_vehicle, delete_vehicle, get_vehicle, import_vehicles_bulk, get_conn

bp_fleet = Blueprint("fleet", __name__, url_prefix="/api/fleet")

def _client_id():
    return int(getattr(current_user, "id", 0) or 0)

@bp_fleet.get("/vehicles")
@login_required
def api_list_vehicles():
    q = request.args.get("q")
    only = request.args.get("status")
    rows = list_vehicles(_client_id(), q, only)
    # converte Row para dict
    out = [dict(r) for r in rows]
    return jsonify(out)

@bp_fleet.get("/vehicles/<vid>")
@login_required
def api_get_vehicle(vid):
    r = get_vehicle(_client_id(), vid)
    if not r:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify(dict(r))

@bp_fleet.post("/vehicles")
@login_required
def api_create_vehicle():
    data = request.get_json(force=True)
    if not data.get("id"):
        return jsonify({"ok": False, "error": "missing_id"}), 400
    data["id"] = str(data["id"]).strip()
    data["status"] = data.get("status") or "offline"
    upsert_vehicle(_client_id(), data)
    return jsonify({"ok": True})

@bp_fleet.put("/vehicles/<vid>")
@login_required
def api_update_vehicle(vid):
    data = request.get_json(force=True)
    data["id"] = vid
    upsert_vehicle(_client_id(), data)
    return jsonify({"ok": True})

@bp_fleet.delete("/vehicles/<vid>")
@login_required
def api_delete_vehicle(vid):
    delete_vehicle(_client_id(), vid)
    return jsonify({"ok": True})

@bp_fleet.post("/vehicles/import")
@login_required
def api_import_vehicles():
    """
    Body: { rows: [{id,name,plate,driver,capacity,tags,obd_id,status,next_service_km,notes}, ...] }
    """
    data = request.get_json(force=True)
    rows = data.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return jsonify({"ok": False, "error": "empty_rows"}), 400
    import_vehicles_bulk(_client_id(), rows)
    return jsonify({"ok": True, "count": len(rows)})

# (Opcional) Marcar status manualmente
@bp_fleet.post("/vehicles/<vid>/status")
@login_required
def api_set_status(vid):
    st = (request.json or {}).get("status")
    if st not in ("online","offline","maintenance"):
        return jsonify({"ok": False, "error": "bad_status"}), 400
    upsert_vehicle(_client_id(), {"id": vid, "status": st})
    return jsonify({"ok": True})

# (Opcional) Atualizar “última posição” a partir das posições recentes
@bp_fleet.post("/vehicles/refresh_last_pos")
@login_required
def refresh_last_pos():
    con = get_conn()
    client_id = _client_id()
    # pega última posição por veículo
    rows = con.execute("""
      WITH lastp AS (
        SELECT vehicle_id, max(ts) AS last_ts
        FROM telem_positions
        WHERE client_id = ?
        GROUP BY vehicle_id
      )
      SELECT p.vehicle_id, p.lat, p.lon, p.speed, p.ts
      FROM telem_positions p
      JOIN lastp l ON l.vehicle_id=p.vehicle_id AND l.last_ts=p.ts
      WHERE p.client_id=?
    """, [client_id, client_id]).fetchall()

    now = datetime.now(timezone.utc)
    for r in rows:
        # status online se última posição < 10min
        is_online = (now - r["ts"]).total_seconds() < 600
        upsert_vehicle(client_id, {
            "id": r["vehicle_id"],
            "last_lat": r["lat"], "last_lon": r["lon"],
            "last_speed": r["speed"], "last_ts": r["ts"],
            "status": "online" if is_online else "offline"
        })
    return jsonify({"ok": True, "count": len(rows)})

# ---- Vehicles ----
@bp_fleet.get("/vehicles")
@login_required
def vehicles_list():
    client_id = int(current_user.id)
    return jsonify(db.vehicles_list_with_tracker(client_id))

@bp_fleet.post("/vehicles")
@login_required
def vehicles_create():
    client_id = int(current_user.id)
    payload = request.get_json(force=True) or {}
    # front manda: {code, model, capacity, avg_consumption_km_l}
    veh_id = (payload.get("code") or "").strip()
    if not veh_id:
        return jsonify({"error": "Informe o código/placa (code)."}), 400
    db.vehicle_upsert(client_id, {
        "id": veh_id,
        "plate": veh_id,
        "name": payload.get("model"),
        "capacity": int(payload.get("capacity") or 0),
        "tags": None,
    })
    return jsonify({"id": veh_id})

@bp_fleet.patch("/vehicles/<vid>/bind_tracker")
@login_required
def vehicles_bind_tracker(vid):
    client_id = int(current_user.id)
    body = request.get_json(force=True) or {}
    tracker_id = (body.get("tracker_id") or "").strip()
    force = bool(body.get("force"))
    if not tracker_id:
        return jsonify({"error": "Informe tracker_id"}), 400

    if not db.vehicle_get(client_id, vid):
        return jsonify({"error": "Veículo não encontrado"}), 404

    # cria/obtém tracker e tenta vincular
    db.tracker_get_or_create(client_id, tracker_id)
    ok = db.tracker_bind_vehicle(client_id, tracker_id, vid, force=force)
    if not ok:
        return jsonify({"error": "Rastreador já vinculado a outro veículo. Envie force=true para sobrescrever."}), 409
    return jsonify({"status": "ok", "tracker_id": tracker_id})

@bp_fleet.patch("/vehicles/<vid>/unbind_tracker")
@login_required
def vehicles_unbind_tracker(vid):
    client_id = int(current_user.id)
    if not db.vehicle_get(client_id, vid):
        return jsonify({"error": "Veículo não encontrado"}), 404
    db.tracker_unbind_vehicle(client_id, vid)
    return jsonify({"status": "ok"})

# ---- Trackers (opcional: telas futuras) ----
@bp_fleet.get("/trackers")
@login_required
def trackers_list():
    client_id = int(current_user.id)
    rows = db.tracker_list(client_id)
    out = []
    for r in rows:
        out.append({
            "tracker_id": r[0],
            "secret_token": r[1],     # exibir só se necessário
            "vehicle_id": r[2],
            "imei": r[3],
            "status": r[4],
        })
    return jsonify(out)

@bp_fleet.post("/trackers/<tracker_id>/rotate_token")
@login_required
def trackers_rotate(tracker_id):
    client_id = int(current_user.id)
    newtok = db.tracker_rotate_token(client_id, tracker_id)
    return jsonify({"tracker_id": tracker_id, "secret_token": newtok})



