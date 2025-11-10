# routes/fleet_routes.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone
from typing import Any, Dict, List
import traceback

from core.db import get_conn, upsert_vehicle, list_vehicles, delete_vehicle, tracker_bind_vehicle

bp_fleet = Blueprint("fleet", __name__, url_prefix="/api/fleet")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _client_id() -> int:
    return int(getattr(current_user, "id", 0) or 0)

def _validate_imei(imei: str) -> bool:
    return imei.isdigit() and len(imei) == 15

# ---------------------------------------------------------------------
# Listar veículos
# ---------------------------------------------------------------------
@bp_fleet.get("/vehicles")
@login_required
def api_list_vehicles():
    try:
        client_id = _client_id()
        q = (request.args.get("q") or "").strip()
        only = (request.args.get("status") or "").strip().lower()

        print(f"[DEBUG] GET /vehicles - client_id: {client_id}, q: {q}, only: {only}")
        
        # Usa a função do core.db que já está testada
        vehicles = list_vehicles(client_id, q or None, only or None)
        
        # Converte para o formato esperado pelo frontend
        result = []
        for v in vehicles:
            vehicle_data = {
                "id": v[0], "name": v[1], "plate": v[2], "driver": v[3],
                "capacity": v[4], "status": v[5], 
                "last_lat": v[6], "last_lon": v[7], "last_speed": v[8], "last_ts": v[9],
                "last_service_km": v[10], "last_service_date": v[11], 
                "next_service_km": v[12], "notes": v[13],
                "tracker_id": v[14], "imei": v[15], "vendor": v[16]
            }
            # Converte datetime para string
            if vehicle_data.get("last_ts"):
                vehicle_data["last_ts"] = str(vehicle_data["last_ts"])
            if vehicle_data.get("last_service_date"):
                vehicle_data["last_service_date"] = str(vehicle_data["last_service_date"])
                
            result.append(vehicle_data)
            
        print(f"[DEBUG] GET /vehicles - encontrados: {len(result)} veículos")
        return jsonify(result)
        
    except Exception as e:
        print(f"[ERROR] GET /vehicles: {e}")
        print(traceback.format_exc())
        return jsonify({"error": "Erro interno ao listar veículos"}), 500

# ---------------------------------------------------------------------
# Criar/Atualizar veículo (POST)
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles")
@login_required
def api_create_vehicle():
    try:
        data = request.get_json(force=True) or {}
        client_id = _client_id()
        
        print(f"[DEBUG] POST /vehicles - client_id: {client_id}")
        print(f"[DEBUG] POST /vehicles - data: {data}")
        
        # Validação básica
        if not data.get("id"):
            return jsonify({"error": "ID do veículo é obrigatório"}), 400

        # Prepara dados para upsert_vehicle
        vehicle_data = {
            "id": data["id"],
            "name": data.get("name", ""),
            "plate": data.get("plate", ""),
            "driver": data.get("driver", ""),
            "capacity": data.get("capacity", 0),
            "status": data.get("status", "offline"),
            "tags": data.get("tags", ""),
            "obd_id": data.get("obd_id", ""),
            "notes": data.get("notes", "")
        }
        
        print(f"[DEBUG] POST /vehicles - vehicle_data: {vehicle_data}")
        
        # Usa a função upsert_vehicle do core.db
        upsert_vehicle(client_id, vehicle_data)
        print(f"[DEBUG] POST /vehicles - upsert_vehicle concluído")
        
        # Se veio IMEI, faz o bind do tracker
        imei = (data.get("imei") or "").strip()
        if imei:
            print(f"[DEBUG] POST /vehicles - processando IMEI: {imei}")
            if not _validate_imei(imei):
                return jsonify({"error": "IMEI inválido (use 15 dígitos)."}), 400
            
            success = tracker_bind_vehicle(
                client_id=client_id,
                tracker_id=imei,
                vehicle_id=data["id"],
                force=True
            )
            
            print(f"[DEBUG] POST /vehicles - tracker_bind_vehicle resultado: {success}")
            
            if not success:
                return jsonify({"error": "Falha ao vincular tracker"}), 400

        return jsonify({"success": True, "message": "Veículo salvo com sucesso"})
        
    except Exception as e:
        print(f"[ERROR] POST /vehicles: {str(e)}")
        print(f"[ERROR] POST /vehicles traceback: {traceback.format_exc()}")
        return jsonify({"error": f"Erro interno: {str(e)}"}), 500

# ---------------------------------------------------------------------
# Obter um veículo
# ---------------------------------------------------------------------
@bp_fleet.get("/vehicles/<vid>")
@login_required
def api_get_vehicle(vid):
    try:
        client_id = _client_id()
        print(f"[DEBUG] GET /vehicles/{vid} - client_id: {client_id}")
        
        with get_conn() as conn:
            row = conn.execute("""
                SELECT v.id, v.name, v.plate, v.driver, v.capacity, v.status,
                       v.last_lat, v.last_lon, v.last_speed, v.last_ts,
                       v.last_service_km, v.last_service_date, v.next_service_km, v.notes,
                       t.tracker_id, t.imei, t.vendor
                FROM vehicles v
                LEFT JOIN trackers t ON t.client_id = v.client_id AND t.vehicle_id = v.id
                WHERE v.client_id = ? AND v.id = ?
            """, [client_id, vid]).fetchone()
            
            if not row:
                return jsonify({"error": "Veículo não encontrado"}), 404
                
            vehicle_data = {
                "id": row[0], "name": row[1], "plate": row[2], "driver": row[3],
                "capacity": row[4], "status": row[5], 
                "last_lat": row[6], "last_lon": row[7], "last_speed": row[8], "last_ts": row[9],
                "last_service_km": row[10], "last_service_date": row[11], 
                "next_service_km": row[12], "notes": row[13],
                "tracker_id": row[14], "imei": row[15], "vendor": row[16]
            }
            
            # Converte datetime para string
            if vehicle_data.get("last_ts"):
                vehicle_data["last_ts"] = str(vehicle_data["last_ts"])
            if vehicle_data.get("last_service_date"):
                vehicle_data["last_service_date"] = str(vehicle_data["last_service_date"])
                
            return jsonify(vehicle_data)
            
    except Exception as e:
        print(f"[ERROR] GET /vehicles/{vid}: {e}")
        print(traceback.format_exc())
        return jsonify({"error": "Erro interno ao buscar veículo"}), 500

# ---------------------------------------------------------------------
# Rota de debug para testar
# ---------------------------------------------------------------------
@bp_fleet.get("/debug")
@login_required
def api_debug():
    try:
        client_id = _client_id()
        return jsonify({
            "client_id": client_id,
            "status": "ok",
            "message": "API Fleet funcionando",
            "user_authenticated": current_user.is_authenticated
        })
    except Exception as e:
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

# ---------------------------------------------------------------------
# Health check (sem autenticação)
# ---------------------------------------------------------------------
@bp_fleet.get("/health")
def health_check():
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        return jsonify({"status": "healthy", "database": "connected"})
    except Exception as e:
        return jsonify({"status": "unhealthy", "error": str(e)}), 500





