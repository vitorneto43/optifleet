# routes/fleet_routes.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone
from typing import Any, Dict, List

from core.db import get_conn, upsert_vehicle, list_vehicles, delete_vehicle, tracker_bind_vehicle

bp_fleet = Blueprint("fleet", __name__, url_prefix="/api/fleet")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _client_id() -> int:
    return int(getattr(current_user, "id", 0) or 0)

def _validate_imei(imei: str) -> bool:
    return imei.isdigit() and len(imei) == 15

def _rows_to_dicts(conn, rows):
    cols = [c[0] for c in conn.description]
    out = []
    for r in rows:
        d = {}
        for k, v in zip(cols, r):
            d[k] = v
        out.append(d)
    return out

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
# Criar/Atualizar veículo (POST para ambos - compatível com frontend)
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles")
@login_required
def api_create_vehicle():
    try:
        data = request.get_json(force=True) or {}
        client_id = _client_id()
        
        print(f"[DEBUG] POST /vehicles - Data: {data}")
        
        # Validação básica
        if not data.get("id"):
            return jsonify({"ok": False, "error": "ID do veículo é obrigatório"}), 400

        # Usa a função upsert_vehicle do core.db (compatível com o schema real)
        upsert_vehicle(client_id, data)
        
        # Se veio IMEI, faz o bind do tracker
        imei = (data.get("imei") or "").strip()
        if imei:
            if not _validate_imei(imei):
                return jsonify({"ok": False, "error": "IMEI inválido (use 15 dígitos)."}), 400
            
            # Usa a função do core.db para vincular tracker
            success = tracker_bind_vehicle(
                client_id=client_id,
                tracker_id=imei,  # Usa IMEI como tracker_id
                vehicle_id=data["id"],
                force=True
            )
            
            if not success:
                return jsonify({"ok": False, "error": "Falha ao vincular tracker"}), 400

        return jsonify({"ok": True, "message": "Veículo salvo com sucesso"})
        
    except Exception as e:
        print(f"[ERROR] POST /vehicles: {e}")
        return jsonify({"ok": False, "error": f"Erro interno: {str(e)}"}), 500

# ---------------------------------------------------------------------
# Atualizar veículo (PUT)
# ---------------------------------------------------------------------
@bp_fleet.put("/vehicles/<vid>")
@login_required
def api_update_vehicle(vid):
    try:
        data = request.get_json(force=True) or {}
        client_id = _client_id()
        
        # Garante que o ID na URL seja usado
        data["id"] = vid
        
        print(f"[DEBUG] PUT /vehicles/{vid} - Data: {data}")
        
        # Usa a função upsert_vehicle do core.db
        upsert_vehicle(client_id, data)
        
        # Se veio IMEI, faz o bind do tracker
        imei = (data.get("imei") or "").strip()
        if imei:
            if not _validate_imei(imei):
                return jsonify({"ok": False, "error": "IMEI inválido (use 15 dígitos)."}), 400
            
            success = tracker_bind_vehicle(
                client_id=client_id,
                tracker_id=imei,
                vehicle_id=vid,
                force=True
            )
            
            if not success:
                return jsonify({"ok": False, "error": "Falha ao vincular tracker"}), 400

        return jsonify({"ok": True, "message": "Veículo atualizado com sucesso"})
        
    except Exception as e:
        print(f"[ERROR] PUT /vehicles/{vid}: {e}")
        return jsonify({"ok": False, "error": f"Erro interno: {str(e)}"}), 500

# ---------------------------------------------------------------------
# Excluir veículo (desvincula tracker)
# ---------------------------------------------------------------------
@bp_fleet.delete("/vehicles/<vid>")
@login_required
def api_delete_vehicle(vid):
    try:
        client_id = _client_id()
        
        # Usa a função do core.db para deletar
        delete_vehicle(client_id, vid)
        
        return jsonify({"ok": True, "message": "Veículo excluído"})
        
    except Exception as e:
        print(f"[ERROR] DELETE /vehicles/{vid}: {e}")
        return jsonify({"ok": False, "error": f"Erro interno: {str(e)}"}), 500

# ---------------------------------------------------------------------
# Importar veículos em lote
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles/import")
@login_required
def api_import_vehicles():
    try:
        data = request.get_json(force=True) or {}
        rows = data.get("rows") or []
        client_id = _client_id()
        
        if not isinstance(rows, list) or not rows:
            return jsonify({"ok": False, "error": "empty_rows"}), 400

        ok, bad = 0, 0
        for row in rows:
            try:
                if not row.get("id"):
                    bad += 1
                    continue
                    
                # Usa a função do core.db
                upsert_vehicle(client_id, row)
                
                # Se veio IMEI, vincula tracker
                imei = (row.get("imei") or "").strip()
                if imei and _validate_imei(imei):
                    tracker_bind_vehicle(
                        client_id=client_id,
                        tracker_id=imei,
                        vehicle_id=row["id"],
                        force=True
                    )
                    
                ok += 1
            except Exception as e:
                print(f"[ERROR] Import row {row.get('id')}: {e}")
                bad += 1

        return jsonify({"ok": True, "imported": ok, "skipped": bad})
        
    except Exception as e:
        print(f"[ERROR] POST /vehicles/import: {e}")
        return jsonify({"ok": False, "error": f"Erro interno: {str(e)}"}), 500

# ---------------------------------------------------------------------
# Rota de debug para testar
# ---------------------------------------------------------------------
@bp_fleet.get("/debug")
@login_required
def api_debug():
    return jsonify({
        "client_id": _client_id(),
        "status": "ok",
        "message": "API Fleet funcionando"
    })

# ---------------------------------------------------------------------
# Atualizar status/última posição a partir de telemetry
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles/refresh_last_pos")
@login_required
def refresh_last_pos():
    try:
        now = datetime.now(timezone.utc)
        client_id = _client_id()
        
        with get_conn() as con:
            # última posição por veículo
            rows = con.execute("""
              WITH lastp AS (
                SELECT vehicle_id, MAX(timestamp) AS last_ts
                FROM telemetry
                WHERE client_id = ?
                GROUP BY vehicle_id
              )
              SELECT p.vehicle_id, p.lat, p.lon, p.speed, p.timestamp AS ts
              FROM telemetry p
              JOIN lastp l
                ON l.vehicle_id = p.vehicle_id AND l.last_ts = p.timestamp
              WHERE p.client_id = ?
            """, [client_id, client_id]).fetchall()
            
            cols = [c[0] for c in con.description]
            for r in rows:
                rec = dict(zip(cols, r))
                is_online = False
                try:
                    ts = rec["ts"]
                    if getattr(ts, "tzinfo", None) is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    is_online = (now - ts).total_seconds() < 600
                except Exception:
                    pass
                
                # Atualiza status do veículo
                upsert_vehicle(client_id, {
                    "id": rec["vehicle_id"],
                    "status": "online" if is_online else "offline",
                    "last_lat": rec["lat"],
                    "last_lon": rec["lon"],
                    "last_speed": rec["speed"],
                    "last_ts": rec["ts"]
                })
                
        return jsonify({"ok": True})
        
    except Exception as e:
        print(f"[ERROR] POST /refresh_last_pos: {e}")
        return jsonify({"ok": False, "error": f"Erro interno: {str(e)}"}), 500




