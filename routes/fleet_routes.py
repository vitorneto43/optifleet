# routes/fleet_routes.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timezone
from typing import Any, Dict, List
import traceback

from core.db import (
    get_conn,
    upsert_vehicle,
    list_vehicles,
    delete_vehicle,
    tracker_bind_vehicle,
)

bp_fleet = Blueprint("fleet", __name__, url_prefix="/api/fleet")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _client_id() -> int:
    return int(getattr(current_user, "id", 0) or 0)


def _validate_imei(imei: str) -> bool:
    return imei.isdigit() and len(imei) == 15


def _ok(payload: dict, code: int = 200):
    return jsonify(payload), code


def _err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


# Campos permitidos para update parcial no veículo (tabela vehicles)
_ALLOWED_FIELDS = {
    "name",
    "plate",
    "driver",
    "capacity",
    "status",
    "tags",
    "obd_id",
    "notes",
    "last_service_km",
    "last_service_date",
    "next_service_km",
}


def _sanitize_vehicle_update(data: Dict[str, Any]) -> Dict[str, Any]:
    """Filtra somente os campos aceitos e ajusta formatos básicos."""
    out: Dict[str, Any] = {}

    for k, v in (data or {}).items():
        if k not in _ALLOWED_FIELDS:
            continue

        if k in {"capacity", "last_service_km", "next_service_km"}:
            # aceita string numérica
            try:
                out[k] = int(v) if v is not None else None
            except Exception:
                continue
        elif k in {"last_service_date"}:
            # aceita YYYY-MM-DD ou datetime; guarda como string padrão ISO date
            if v:
                try:
                    if isinstance(v, str):
                        out[k] = v  # confia que já veio no padrão 'YYYY-MM-DD'
                    else:
                        out[k] = str(v)
                except Exception:
                    continue
        else:
            out[k] = v

    return out


# ---------------------------------------------------------------------
# OPTIONS (preflight) - ajuda CORS para /vehicles e /vehicles/<vid>
# ---------------------------------------------------------------------
@bp_fleet.route("/vehicles", methods=["OPTIONS"])
@bp_fleet.route("/vehicles/<vid>", methods=["OPTIONS"])
def api_vehicles_options(vid=None):
    # Flask já responde a OPTIONS automaticamente, mas retornar 204 explícito ajuda depuração
    return ("", 204)


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

        vehicles = list_vehicles(client_id, q or None, only or None)

        # Converte para o formato esperado pelo frontend
        result = []
        for v in vehicles:
            vehicle_data = {
                "id": v[0],
                "name": v[1],
                "plate": v[2],
                "driver": v[3],
                "capacity": v[4],
                "status": v[5],
                "last_lat": v[6],
                "last_lon": v[7],
                "last_speed": v[8],
                "last_ts": v[9],
                "last_service_km": v[10],
                "last_service_date": v[11],
                "next_service_km": v[12],
                "notes": v[13],
                "tracker_id": v[14],
                "imei": v[15],
                "vendor": v[16],
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
        return _err("Erro interno ao listar veículos", 500)


# ---------------------------------------------------------------------
# Criar/Atualizar (upsert) veículo via POST
# Mantém seu comportamento: exige id e faz bind do IMEI se enviado.
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles")
@login_required
def api_create_vehicle():
    try:
        data = request.get_json(force=True) or {}
        

        print(f"[DEBUG] POST /vehicles - client_id: {client_id}")
        print(f"[DEBUG] POST /vehicles - data: {data}")

        

        vehicle_data = {
            "id": data["id"],
            "name": data.get("name", ""),
            "plate": data.get("plate", ""),
            "driver": data.get("driver", ""),
            "capacity": data.get("capacity", 0),
            "status": data.get("status", "offline"),
            "tags": data.get("tags", ""),
            "obd_id": data.get("obd_id", ""),
            "notes": data.get("notes", ""),
        }

        print(f"[DEBUG] POST /vehicles - vehicle_data: {vehicle_data}")

        upsert_vehicle(client_id, vehicle_data)
        print(f"[DEBUG] POST /vehicles - upsert_vehicle concluído")

        # Bind do IMEI opcional
        imei = (data.get("imei") or "").strip()
        if imei:
            print(f"[DEBUG] POST /vehicles - processando IMEI: {imei}")
            if not _validate_imei(imei):
                return _err("IMEI inválido (use 15 dígitos).", 400)

            success = tracker_bind_vehicle(
                client_id=client_id,
                tracker_id=imei,
                vehicle_id=data["id"],
                force=True,
            )
            print(f"[DEBUG] POST /vehicles - tracker_bind_vehicle resultado: {success}")

            if not success:
                return _err("Falha ao vincular tracker", 400)

        return _ok({"success": True, "message": "Veículo salvo com sucesso"})

    except Exception as e:
        print(f"[ERROR] POST /vehicles: {str(e)}")
        print(f"[ERROR] POST /vehicles traceback: {traceback.format_exc()}")
        return _err(f"Erro interno: {str(e)}", 500)


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
            row = conn.execute(
                """
                SELECT v.id, v.name, v.plate, v.driver, v.capacity, v.status,
                       v.last_lat, v.last_lon, v.last_speed, v.last_ts,
                       v.last_service_km, v.last_service_date, v.next_service_km, v.notes,
                       t.tracker_id, t.imei, t.vendor
                FROM vehicles v
                LEFT JOIN trackers t
                  ON t.client_id = v.client_id AND t.vehicle_id = v.id
                WHERE v.client_id = ? AND v.id = ?
            """,
                [client_id, vid],
            ).fetchone()

            if not row:
                return _err("Veículo não encontrado", 404)

            vehicle_data = {
                "id": row[0],
                "name": row[1],
                "plate": row[2],
                "driver": row[3],
                "capacity": row[4],
                "status": row[5],
                "last_lat": row[6],
                "last_lon": row[7],
                "last_speed": row[8],
                "last_ts": row[9],
                "last_service_km": row[10],
                "last_service_date": row[11],
                "next_service_km": row[12],
                "notes": row[13],
                "tracker_id": row[14],
                "imei": row[15],
                "vendor": row[16],
            }

            if vehicle_data.get("last_ts"):
                vehicle_data["last_ts"] = str(vehicle_data["last_ts"])
            if vehicle_data.get("last_service_date"):
                vehicle_data["last_service_date"] = str(
                    vehicle_data["last_service_date"]
                )

            return jsonify(vehicle_data)

    except Exception as e:
        print(f"[ERROR] GET /vehicles/{vid}: {e}")
        print(traceback.format_exc())
        return _err("Erro interno ao buscar veículo", 500)


# ---------------------------------------------------------------------
# Update parcial (PUT/PATCH) - resolve o 405 do /vehicles/<vid>
# ---------------------------------------------------------------------
@bp_fleet.route("/vehicles/<vid>", methods=["PUT", "PATCH"])
@login_required
def api_update_vehicle(vid):
    try:
        client_id = _client_id()
        data = request.get_json(silent=True) or {}
        print(f"[DEBUG] {request.method} /vehicles/{vid} - client_id: {client_id}")
        print(f"[DEBUG] {request.method} /vehicles/{vid} - data: {data}")

        # filtra campos permitidos
        update = _sanitize_vehicle_update(data)

        # caso especial: permitir bind/alteração do IMEI via update
        imei = (data.get("imei") or "").strip() if data.get("imei") else None
        if imei is not None and not _validate_imei(imei):
            return _err("IMEI inválido (use 15 dígitos).", 400)

        if not update and imei is None:
            return _err("Nenhum campo válido para atualizar", 400)

        with get_conn() as conn:
            # verifica existência
            exists = (
                conn.execute(
                    "SELECT 1 FROM vehicles WHERE client_id=? AND id=?",
                    [client_id, vid],
                ).fetchone()
                is not None
            )
            if not exists:
                return _err("Veículo não encontrado", 404)

            if update:
                sets = ", ".join(f"{k}=?" for k in update.keys())
                params = list(update.values()) + [client_id, vid]
                conn.execute(
                    f"UPDATE vehicles SET {sets} WHERE client_id=? AND id=?",
                    params,
                )

            if imei is not None:
                # bind/alterar o tracker para este veículo (force=True dissocia de outro)
                ok = tracker_bind_vehicle(
                    client_id=client_id, tracker_id=imei, vehicle_id=vid, force=True
                )
                if not ok:
                    return _err("Falha ao vincular tracker", 400)

            conn.commit()

        return _ok({"success": True, "id": vid, "updated": {**update, **({"imei": imei} if imei is not None else {})}})

    except Exception as e:
        print(f"[ERROR] {request.method} /vehicles/{vid}: {e}")
        print(traceback.format_exc())
        return _err("Erro interno ao atualizar veículo", 500)


# ---------------------------------------------------------------------
# DELETE - remover um veículo
# ---------------------------------------------------------------------
@bp_fleet.delete("/vehicles/<vid>")
@login_required
def api_delete_vehicle(vid):
    try:
        client_id = _client_id()
        print(f"[DEBUG] DELETE /vehicles/{vid} - client_id: {client_id}")

        # preferir função utilitária se já existente
        if delete_vehicle:
            ok = delete_vehicle(client_id, vid)
            if not ok:
                return _err("Veículo não encontrado", 404)
        else:
            with get_conn() as conn:
                cur = conn.execute(
                    "DELETE FROM vehicles WHERE client_id=? AND id=?",
                    [client_id, vid],
                )
                conn.commit()
                if cur.rowcount == 0:
                    return _err("Veículo não encontrado", 404)

        return _ok({"success": True, "id": vid})

    except Exception as e:
        print(f"[ERROR] DELETE /vehicles/{vid}: {e}")
        print(traceback.format_exc())
        return _err("Erro interno ao remover veículo", 500)


# ---------------------------------------------------------------------
# Rota de debug para testar
# ---------------------------------------------------------------------
@bp_fleet.get("/debug")
@login_required
def api_debug():
    try:
        client_id = _client_id()
        return jsonify(
            {
                "client_id": client_id,
                "status": "ok",
                "message": "API Fleet funcionando",
                "user_authenticated": current_user.is_authenticated,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


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







