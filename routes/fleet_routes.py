# routes/fleet_routes.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone
from typing import Any, Dict, List

from core.db import get_conn  # DuckDB connection

bp_fleet = Blueprint("fleet", __name__, url_prefix="/api/fleet")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _client_id() -> int:
    # se você tiver multi-tenant por client_id na tabela vehicles, adapte aqui.
    # no schema atual, vehicles.id é global e não há client_id explícito — então
    # ignoramos no SQL. Mantive a função por compatibilidade.
    return int(getattr(current_user, "id", 0) or 0)

def _rows_to_dicts(conn, rows):
    cols = [c[0] for c in conn.description]  # duckdb: disponível após execute()
    out = []
    for r in rows:
        d = {}
        for k, v in zip(cols, r):
            d[k] = v
        out.append(d)
    return out

def _validate_imei(imei: str) -> bool:
    return imei.isdigit() and len(imei) == 15

def _merge_vehicle(data: Dict[str, Any]) -> None:
    """
    Upsert de vehicle via MERGE no DuckDB.
    Campos aceitos: id, name, plate, driver, capacity, status,
    next_service_km, last_service_km, tags, notes, obd_id
    """
    with get_conn() as con:
        # normaliza defaults
        record = {
            "id": str(data.get("id", "")).strip(),
            "name": (data.get("name") or "").strip() or None,
            "plate": (data.get("plate") or "").strip() or None,
            "driver": (data.get("driver") or "").strip() or None,
            "capacity": int(data.get("capacity") or 0),
            "status": (data.get("status") or "offline"),
            "next_service_km": float(data.get("next_service_km") or 0),
            "last_service_km": float(data.get("last_service_km") or 0),
            "tags": (data.get("tags") or "").strip() or None,
            "notes": (data.get("notes") or "").strip() or None,
            "obd_id": (data.get("obd_id") or "").strip() or None,
        }
        if not record["id"]:
            raise ValueError("missing_id")

        # Garante tabela vehicles (idempotente)
        con.execute("""
          CREATE TABLE IF NOT EXISTS vehicles (
            id TEXT PRIMARY KEY,
            name TEXT,
            plate TEXT,
            driver TEXT,
            capacity INTEGER,
            status TEXT,
            next_service_km DOUBLE,
            last_service_km DOUBLE,
            tags TEXT,
            notes TEXT,
            obd_id TEXT
          );
        """)

        # MERGE
        con.execute("""
          MERGE INTO vehicles AS v
          USING (
            SELECT ?::TEXT AS id,
                   ?::TEXT AS name,
                   ?::TEXT AS plate,
                   ?::TEXT AS driver,
                   ?::INTEGER AS capacity,
                   ?::TEXT AS status,
                   ?::DOUBLE AS next_service_km,
                   ?::DOUBLE AS last_service_km,
                   ?::TEXT AS tags,
                   ?::TEXT AS notes,
                   ?::TEXT AS obd_id
          ) AS s
          ON v.id = s.id
          WHEN MATCHED THEN UPDATE SET
            name = s.name,
            plate = s.plate,
            driver = s.driver,
            capacity = s.capacity,
            status = s.status,
            next_service_km = s.next_service_km,
            last_service_km = s.last_service_km,
            tags = s.tags,
            notes = s.notes,
            obd_id = s.obd_id
          WHEN NOT MATCHED THEN
            INSERT (id, name, plate, driver, capacity, status, next_service_km, last_service_km, tags, notes, obd_id)
            VALUES (s.id, s.name, s.plate, s.driver, s.capacity, s.status, s.next_service_km, s.last_service_km, s.tags, s.notes, s.obd_id);
        """, [
            record["id"], record["name"], record["plate"], record["driver"],
            record["capacity"], record["status"], record["next_service_km"],
            record["last_service_km"], record["tags"], record["notes"], record["obd_id"]
        ])

def _merge_tracker_for_vehicle(vehicle_id: str, imei: str | None, vendor: str | None) -> None:
    # garante tabela trackers
    with get_conn() as con:
        con.execute("""
          CREATE TABLE IF NOT EXISTS trackers (
            id         INTEGER PRIMARY KEY,
            vehicle_id TEXT,
            imei       TEXT NOT NULL UNIQUE,
            vendor     TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
          );
        """)

        if imei:
            # upsert do tracker por IMEI e vincula ao vehicle_id
            con.execute("""
              MERGE INTO trackers AS t
              USING (
                SELECT ?::TEXT AS imei,
                       ?::TEXT AS vendor,
                       ?::TEXT AS vehicle_id
              ) AS s
              ON t.imei = s.imei
              WHEN MATCHED THEN
                UPDATE SET vendor = s.vendor, vehicle_id = s.vehicle_id
              WHEN NOT MATCHED THEN
                INSERT (imei, vendor, vehicle_id)
                VALUES (s.imei, s.vendor, s.vehicle_id);
            """, [imei, vendor, vehicle_id])
        else:
            # se IMEI vazio: opcional — desvincular qualquer tracker do veículo
            con.execute("UPDATE trackers SET vehicle_id = NULL WHERE vehicle_id = ?", [vehicle_id])

# ---------------------------------------------------------------------
# Listar veículos (com IMEI/vendor e última posição)
# ---------------------------------------------------------------------
@bp_fleet.get("/vehicles")
@login_required
def api_list_vehicles():
    q = (request.args.get("q") or "").strip()
    only = (request.args.get("status") or "").strip().lower()

    where = []
    params: List[Any] = []

    if q:
        # busca parcial em id/name/plate/driver/tags
        where.append("""(
            v.id ILIKE ? OR v.name ILIKE ? OR v.plate ILIKE ? OR
            v.driver ILIKE ? OR v.tags ILIKE ?
        )""")
        pat = f"%{q}%"
        params += [pat, pat, pat, pat, pat]

    if only in ("online", "offline", "maintenance"):
        where.append("COALESCE(v.status,'offline') = ?")
        params.append(only)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with get_conn() as conn:
        # última posição via agregação em telemetry
        sql = f"""
          SELECT v.id, v.name, v.plate, v.driver, v.capacity, v.status,
                 v.next_service_km, v.last_service_km, v.tags, v.notes, v.obd_id,
                 tp.last_lat, tp.last_lon, tp.last_ts,
                 t.imei, t.vendor
          FROM vehicles v
          LEFT JOIN (
            SELECT vehicle_id,
                   ANY_VALUE(lat) AS last_lat,
                   ANY_VALUE(lon) AS last_lon,
                   MAX(timestamp)  AS last_ts
            FROM telemetry
            GROUP BY vehicle_id
          ) tp ON tp.vehicle_id = v.id
          LEFT JOIN trackers t ON t.vehicle_id = v.id
          {where_sql}
          ORDER BY v.id
        """
        rows = conn.execute(sql, params).fetchall()
        out = _rows_to_dicts(conn, rows)
        # normalização leve
        for r in out:
            # datas -> string ISO
            if r.get("last_ts") is not None:
                r["last_ts"] = str(r["last_ts"])
        return jsonify(out)

# ---------------------------------------------------------------------
# Obter um veículo
# ---------------------------------------------------------------------
@bp_fleet.get("/vehicles/<vid>")
@login_required
def api_get_vehicle(vid):
    with get_conn() as conn:
        rows = conn.execute("""
          SELECT v.id, v.name, v.plate, v.driver, v.capacity, v.status,
                 v.next_service_km, v.last_service_km, v.tags, v.notes, v.obd_id,
                 t.imei, t.vendor
          FROM vehicles v
          LEFT JOIN trackers t ON t.vehicle_id = v.id
          WHERE v.id = ?
        """, [vid]).fetchall()
        if not rows:
            return jsonify({"ok": False, "error": "not_found"}), 404
        d = _rows_to_dicts(conn, rows)[0]
        if d.get("last_ts") is not None:
            d["last_ts"] = str(d["last_ts"])
        return jsonify(d)

# ---------------------------------------------------------------------
# Criar veículo (com IMEI/vendor)
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles")
@login_required
def api_create_vehicle():
    data = request.get_json(force=True) or {}
    if not data.get("id"):
        return jsonify({"ok": False, "error": "missing_id"}), 400

    imei = (data.get("imei") or "").strip()
    vendor = (data.get("vendor") or "").strip() or None

    if imei and not _validate_imei(imei):
        return jsonify({"ok": False, "error": "IMEI inválido (use 15 dígitos)."}), 400

    try:
        _merge_vehicle(data)
        _merge_tracker_for_vehicle(str(data["id"]).strip(), imei if imei else None, vendor)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})

# ---------------------------------------------------------------------
# Atualizar veículo (com IMEI/vendor)
# ---------------------------------------------------------------------
@bp_fleet.put("/vehicles/<vid>")
@login_required
def api_update_vehicle(vid):
    data = request.get_json(force=True) or {}
    data["id"] = vid

    imei = (data.get("imei") or "").strip()
    vendor = (data.get("vendor") or "").strip() or None
    if imei and not _validate_imei(imei):
        return jsonify({"ok": False, "error": "IMEI inválido (use 15 dígitos)."}), 400

    try:
        _merge_vehicle(data)
        _merge_tracker_for_vehicle(str(vid), imei if imei else None, vendor)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    return jsonify({"ok": True})

# ---------------------------------------------------------------------
# Excluir veículo (desvincula tracker)
# ---------------------------------------------------------------------
@bp_fleet.delete("/vehicles/<vid>")
@login_required
def api_delete_vehicle(vid):
    with get_conn() as con:
        # garante tabela vehicles
        con.execute("""
          CREATE TABLE IF NOT EXISTS vehicles (
            id TEXT PRIMARY KEY
          );
        """)
        # desvincula trackers
        con.execute("UPDATE trackers SET vehicle_id = NULL WHERE vehicle_id = ?", [vid])
        # remove o veículo
        con.execute("DELETE FROM vehicles WHERE id = ?", [vid])
    return jsonify({"ok": True})

# ---------------------------------------------------------------------
# Importar veículos em lote (aceita imei/vendor)
# Body: { rows: [{id,name,plate,driver,capacity,tags,obd_id,status,next_service_km,notes,imei,vendor}, ...] }
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles/import")
@login_required
def api_import_vehicles():
    data = request.get_json(force=True) or {}
    rows = data.get("rows") or []
    if not isinstance(rows, list) or not rows:
        return jsonify({"ok": False, "error": "empty_rows"}), 400

    ok, bad = 0, 0
    for row in rows:
        try:
            if not row.get("id"):
                bad += 1
                continue
            imei = (row.get("imei") or "").strip()
            vendor = (row.get("vendor") or "").strip() or None
            if imei and not _validate_imei(imei):
                # ignora IMEI inválido, mas importa o veículo
                imei = None
            _merge_vehicle(row)
            _merge_tracker_for_vehicle(str(row["id"]).strip(), imei, vendor)
            ok += 1
        except Exception:
            bad += 1

    return jsonify({"ok": True, "imported": ok, "skipped": bad})

# ---------------------------------------------------------------------
# (Opcional) Atualizar status/última posição a partir de telemetry
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles/refresh_last_pos")
@login_required
def refresh_last_pos():
    now = datetime.now(timezone.utc)
    with get_conn() as con:
        # última posição por veículo
        rows = con.execute("""
          WITH lastp AS (
            SELECT vehicle_id, MAX(timestamp) AS last_ts
            FROM telemetry
            GROUP BY vehicle_id
          )
          SELECT p.vehicle_id, p.lat, p.lon, p.speed, p.timestamp AS ts
          FROM telemetry p
          JOIN lastp l
            ON l.vehicle_id = p.vehicle_id AND l.last_ts = p.timestamp
        """).fetchall()
        cols = [c[0] for c in con.description]
        for r in rows:
            rec = dict(zip(cols, r))
            is_online = False
            try:
                ts = rec["ts"]
                # ts pode vir naive — assume UTC
                if getattr(ts, "tzinfo", None) is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                is_online = (now - ts).total_seconds() < 600
            except Exception:
                pass
            _merge_vehicle({
                "id": rec["vehicle_id"],
                "status": "online" if is_online else "offline",
                # campos de “last_*” estão no schema? se não, ignore.
                # Mantendo apenas status aqui para não conflitar com seu schema atual.
            })
    return jsonify({"ok": True})



