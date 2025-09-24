# routes/telemetry_ingest.py (exemplo)
from flask import Blueprint, request, jsonify
from datetime import datetime, timezone
from core.db import get_tracker_owner, insert_telemetry

bp_ingest = Blueprint("ingest", __name__, url_prefix="/api/ingest")

@bp_ingest.post("")
def ingest():
    j = request.get_json(force=True) or {}
    tracker_id = (j.get("tracker_id") or j.get("imei") or "").strip()
    token = (j.get("token") or j.get("secret_token") or "").strip()
    lat = float(j.get("lat")); lon = float(j.get("lon"))
    speed = float(j.get("speed") or 0); fuel = float(j.get("fuel") or 0)

    owner = get_tracker_owner(tracker_id, token)
    if not owner:
        return jsonify({"ok": False, "error": "tracker/token inválidos"}), 403
    client_id, vehicle_id = owner
    insert_telemetry(str(client_id), str(vehicle_id), datetime.now(timezone.utc), lat, lon, speed, fuel)
    return jsonify({"ok": True})
