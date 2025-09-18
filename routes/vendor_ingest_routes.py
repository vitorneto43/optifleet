import os, json
from flask import Blueprint, request, jsonify
from core.db import SessionLocal
from core.fleet_models import Vehicle, Telemetry
from core.telemetry.adapters import ADAPTERS

bp_vendor = Blueprint("vendor", __name__)
WEBHOOK_TOKEN = os.getenv("VENDOR_WEBHOOK_TOKEN","")

def _auth_ok(req):
    # header Authorization: Bearer <token> OU query ?token=
    auth = req.headers.get("Authorization","")
    if auth == f"Bearer {WEBHOOK_TOKEN}":
        return True
    tok = req.args.get("token")
    return bool(WEBHOOK_TOKEN) and tok == WEBHOOK_TOKEN

@bp_vendor.post("/api/telemetry/vendor/<provider>")
def vendor_ingest(provider: str):
    if not _auth_ok(request):
        return jsonify({"error":"unauthorized"}), 401

    adapter = ADAPTERS.get(provider, ADAPTERS["generic"])
    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        # alguns provedores mandam text/plain
        raw = request.data.decode("utf-8","ignore")
        try: payload = json.loads(raw)
        except: return jsonify({"error":"invalid_json"}), 400

    # alguns provedores mandam lista de posições
    items = payload if isinstance(payload, list) else [payload]
    s = SessionLocal()
    created = 0
    try:
        for itm in items:
            norm = adapter(itm)
            tracker_id = (norm.get("tracker_id") or "").strip()
            if not tracker_id:
                continue
            v = s.query(Vehicle).filter(Vehicle.tracker_id == tracker_id).first()
            if not v:
                # sem vínculo → ignore ou crie regra para fallback
                continue
            t = Telemetry(
                vehicle_id=v.id,
                lat=float(norm["lat"]), lon=float(norm["lon"]),
                speed_kmh=float(norm.get("speed_kmh",0.0)),
                fuel_pct=float(norm.get("fuel_pct",0.0)),
                engine_temp=float(norm.get("engine_temp",0.0)),
                odometer_km=float(norm.get("odometer_km",0.0)),
                obd_alerts=int(norm.get("obd_alerts",0)),
            )
            s.add(t); created += 1
        s.commit()
        return jsonify({"ok": True, "saved": created})
    except Exception as e:
        s.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        s.close()
