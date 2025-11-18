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
    # ✅ NOVO: vamos usar as assinaturas / trial pra saber o plano
    get_active_subscription,
    get_active_trial,
)

# ==== imports da parte de OTIMIZAÇÃO ====
from core.models import (
    Location,
    TimeWindow,
    Depot,
    Vehicle,
    Stop,
    OptimizeRequest,
    hhmm_to_minutes,
)
from core.providers.maps import RoutingProvider
from core.solver.vrptw import solve_vrptw

bp_fleet = Blueprint("fleet", __name__, url_prefix="/api/fleet")

# Guarda o último mapa gerado por cliente na memória do processo
_last_map_by_client: Dict[int, str] = {}

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
# ✅ NOVO: limites de veículos por plano
# ---------------------------------------------------------------------
PLAN_LIMITS = {
    "start": 5,          # Plano Start → até 5 veículos
    "pro": 50,           # Plano Pro   → até 50 veículos
    "enterprise": 9_999_999,  # Plano Enterprise → "ilimitado"
}


def _get_plan_name_for_client(client_id: int) -> str | None:
    """
    Descobre o nome do plano do cliente:
    - Se tiver trial ativo, usa o plano do trial
    - Senão, usa o plano da assinatura ativa
    Aceita tanto retorno em dict quanto em tupla.
    """
    trial = get_active_trial(client_id)
    sub = get_active_subscription(client_id)

    def _extract_plan(row):
        if row is None:
            return None

        # Caso 1: dict (ex: {"plan": "START"})
        if isinstance(row, dict):
            return row.get("plan") or row.get("plan_name")

        # Caso 2: tupla/lista (ex: ("START",) ou (id, client_id, "START", ...))
        if isinstance(row, (tuple, list)):
            # 👉 Se o SELECT for "SELECT plan FROM ...", o plano estará no índice 0
            # Se o SELECT for mais colunas, ajuste o índice conforme a ordem
            return row[0]

        # Caso 3: qualquer outra coisa, ignora
        return None

    plan_name = _extract_plan(trial) or _extract_plan(sub)
    return plan_name


def get_vehicle_limit_for_client(client_id: int) -> int:
    """
    Retorna o limite máximo de veículos permitido para esse cliente,
    com base no plano (Start, Pro, Enterprise).
    """
    plan_name = _get_plan_name_for_client(client_id)
    limit = PLAN_LIMITS.get(plan_name, PLAN_LIMITS["start"])
    print(f"[DEBUG] Plano do client_id={client_id}: {plan_name} (limite={limit})")
    return limit


def get_vehicles_count_for_client(client_id: int) -> int:
    """
    Conta quantos veículos o cliente já possui na tabela vehicles.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM vehicles WHERE client_id = ?",
            [client_id],
        ).fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
        print(f"[DEBUG] client_id={client_id} já tem {count} veículos")
        return count


def vehicle_exists_for_client(client_id: int, vehicle_id: str) -> bool:
    """
    Verifica se um veículo com esse id já existe para o cliente.
    Isso é importante para não bloquear UPDATE quando o plano está cheio.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM vehicles WHERE client_id = ? AND id = ?",
            [client_id, vehicle_id],
        ).fetchone()
        exists = bool(row)
        print(
            f"[DEBUG] vehicle_exists_for_client(client_id={client_id}, id={vehicle_id}) = {exists}"
        )
        return exists


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
                "name": v[2],
                "plate": v[3],
                "driver": v[4],
                "capacity": v[5],
                "status": v[12],
                "last_lat": v[8],
                "last_lon": v[9],
                "last_speed": v[10],
                "last_ts": v[11],
                "last_service_km": v[13],
                "last_service_date": v[14],
                "next_service_km": v[15],
                "notes": v[16],
                "imei": v[17],
                "vendor": v[18],
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
# ✅ AGORA COM TRAVA DE LIMITE POR PLANO
# ---------------------------------------------------------------------
@bp_fleet.post("/vehicles")
@login_required
def api_create_vehicle():
    try:
        data = request.get_json(force=True) or {}
        client_id = _client_id()

        print(f"[DEBUG] POST /vehicles - client_id: {client_id}")
        print(f"[DEBUG] POST /vehicles - data: {data}")

        if not data.get("id"):
            return _err("ID do veículo é obrigatório", 400)

        vehicle_id = str(data["id"]).strip()
        if not vehicle_id:
            return _err("ID do veículo é obrigatório", 400)

        # Verifica se é UPDATE (já existe) ou CREATE (novo veículo)
        is_update = vehicle_exists_for_client(client_id, vehicle_id)

        # Se for veículo NOVO, aplica a trava do plano
        if not is_update:
            current_count = get_vehicles_count_for_client(client_id)
            plan_limit = get_vehicle_limit_for_client(client_id)

            print(
                f"[DEBUG] Plano permite até {plan_limit} veículos. "
                f"client_id={client_id} já tem {current_count}."
            )

            if current_count >= plan_limit:
                return _err(
                    f"Você atingiu o limite de {plan_limit} veículos do seu plano. "
                    "Atualize para um plano superior para cadastrar mais veículos.",
                    403,
                )

        vehicle_data = {
            "id": vehicle_id,
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
                vehicle_id=vehicle_id,
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
@bp_fleet.put("/vehicles/<vid>")
@bp_fleet.patch("/vehicles/<vid>")
@login_required
def api_update_vehicle(vid):
    try:
        client_id = _client_id()
        print(f"[DEBUG] PUT/PATCH /vehicles/{vid} - client_id: {client_id}")

        payload = request.get_json(silent=True) or {}
        print(f"[DEBUG] payload recebido:", payload)

        # Helpers para converter int sem quebrar
        def to_int(val):
            if val in (None, "", "null"):
                return None
            try:
                return int(val)
            except (ValueError, TypeError):
                return None

        name = (payload.get("name") or "").strip()
        plate = (payload.get("plate") or "").strip()
        driver = (payload.get("driver") or "").strip()
        status = (payload.get("status") or "").strip() or "ativo"

        capacity = to_int(payload.get("capacity"))
        notes = (payload.get("notes") or "").strip()

        last_service_km = to_int(payload.get("last_service_km"))
        next_service_km = to_int(payload.get("next_service_km"))
        last_service_date = payload.get("last_service_date") or None  # texto ou None

        if not name:
            return _err("O nome do veículo é obrigatório", 400)
        if not plate:
            return _err("A placa do veículo é obrigatória", 400)

        with get_conn() as conn:
            cur = conn.cursor()

            row = cur.execute(
                "SELECT id FROM vehicles WHERE client_id = ? AND id = ?",
                [client_id, vid],
            ).fetchone()

            if not row:
                return _err("Veículo não encontrado", 404)

            cur.execute(
                """
                UPDATE vehicles
                   SET name = ?,
                       plate = ?,
                       driver = ?,
                       capacity = ?,
                       status = ?,
                       last_service_km = ?,
                       last_service_date = ?,
                       next_service_km = ?,
                       notes = ?
                 WHERE client_id = ?
                   AND id = ?
                """,
                [
                    name,
                    plate,
                    driver,
                    capacity,
                    status,
                    last_service_km,
                    last_service_date,
                    next_service_km,
                    notes,
                    client_id,
                    vid,
                ],
            )

        print(f"[DEBUG] Veículo {vid} atualizado com sucesso")
        return jsonify({"ok": True}), 200

    except Exception as e:
        print(f"[ERROR] PUT/PATCH /vehicles/{vid}: {e}")
        print(traceback.format_exc())
        return _err("Erro interno ao atualizar veículo", 500)


# ---------------------------------------------------------------------
# DELETE - remover um veículo  (VERSÃO AJUSTADA)
# ---------------------------------------------------------------------
@bp_fleet.delete("/vehicles/<vid>")
@login_required
def api_delete_vehicle(vid):
    try:
        client_id = _client_id()
        vid_str = str(vid).strip()

        if not vid_str:
            return _err("ID do veículo inválido", 400)

        print(f"[DEBUG] DELETE /vehicles/{vid_str} - client_id={client_id}")

        success = delete_vehicle(client_id, vid_str)

        if success:
            return _ok({"success": True, "id": vid_str})
        else:
            return _err("Veículo não encontrado ou não pertence ao usuário", 404)

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
