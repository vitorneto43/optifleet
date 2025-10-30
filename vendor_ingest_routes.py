# routes/vendor_ingest_routes.py
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from core.db import get_conn

bp_vendor = Blueprint("vendor", __name__, url_prefix="/vendor")

@bp_vendor.get("/ping")
def ping():
    return jsonify({"ok": True, "service": "vendor", "version": 1})

@bp_vendor.post("/vehicles")
@login_required
def upsert_vehicles():
    """
    Ingest de veículos vindos de fornecedor/planilha.
    JSON esperado:
    [
      {"vehicle_id":"V1","imei":"123456789012345","name":"Caminhão 1"},
      {"vehicle_id":"V2","imei":"987654321098765","name":"Van 2"}
    ]
    """
    items = request.get_json(force=True)
    if not isinstance(items, list) or not items:
        return jsonify({"error": "Envie uma lista JSON com veículos"}), 400

    client_id = str(current_user.id)
    con = get_conn()

    # DuckDB não tem UPSERT nativo; fazemos delete+insert por (client_id, vehicle_id)
    for it in items:
        vid  = str(it.get("vehicle_id") or "").strip()
        imei = str(it.get("imei") or "").strip() or None
        name = str(it.get("name") or "").strip() or None
        if not vid:
            continue
        con.execute(
            "DELETE FROM vehicles WHERE client_id = ? AND vehicle_id = ?",
            [client_id, vid],
        )
        con.execute(
            "INSERT INTO vehicles (client_id, vehicle_id, imei, name) VALUES (?, ?, ?, ?)",
            [client_id, vid, imei, name],
        )

    return jsonify({"ok": True, "count": len(items)})

@bp_vendor.post("/telemetry")
def ingest_telemetry_bulk():
    """
    Ingest em lote de telemetria de fornecedor.
    JSON:
    {
      "client_id": "empresa_123" (opcional se logado),
      "points": [
        {"vehicle_id":"V1","lat":-8.05,"lon":-34.9,"speed":60,"fuel":80,"timestamp":"2025-09-19T19:25:00Z"},
        ...
      ]
    }
    """
    data = request.get_json(force=True)
    client_id = data.get("client_id")
    if not client_id and getattr(current_user, "is_authenticated", False):
        client_id = str(current_user.id)
    if not client_id:
        return jsonify({"error": "client_id ausente (ou faça login)"}), 400

    points = data.get("points")
    if not isinstance(points, list) or not points:
        return jsonify({"error": "Envie uma lista em 'points'"}), 400

    con = get_conn()
    inserted = 0
    for p in points:
        vid   = p.get("vehicle_id")
        lat   = p.get("lat")
        lon   = p.get("lon")
        speed = float(p.get("speed") or 0.0)
        fuel  = float(p.get("fuel") or 0.0)
        ts    = p.get("timestamp")  # pode ser ISO 8601; DuckDB aceita TEXT em coluna TIMESTAMP via CAST

        if vid is None or lat is None or lon is None:
            continue

        if ts:
            con.execute(
                """
                INSERT INTO telemetry (client_id, vehicle_id, timestamp, lat, lon, speed, fuel)
                SELECT ?, ?, CAST(? AS TIMESTAMP), ?, ?, ?, ?
                """,
                [client_id, str(vid), str(ts), float(lat), float(lon), speed, fuel],
            )
        else:
            con.execute(
                """
                INSERT INTO telemetry (client_id, vehicle_id, timestamp, lat, lon, speed, fuel)
                VALUES (?, ?, now(), ?, ?, ?, ?)
                """,
                [client_id, str(vid), float(lat), float(lon), speed, fuel],
            )
        inserted += 1

    return jsonify({"ok": True, "inserted": inserted})

