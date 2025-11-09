from flask import Blueprint, jsonify, request
from flask_login import login_required
from core.authz import admin_required

# Funções já existentes no seu core.db (pelos imports que você mostrou)
from core.db import (
    list_trial_users,          # -> lista todos os trials (com paginação/filtros se tiver)
    trial_users_summary,       # -> agregados (ativos, expirados, etc.)
    get_active_trial,          # -> por user_id
    expire_trial,              # -> força expiração por user_id
    create_trial               # -> cria trial por user_id (se precisar)
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

@admin_bp.get("/trials")
@login_required
@admin_required
def admin_list_trials():
    """Lista todos os usuários em trial (não só o current_user)."""
    # filtros opcionais
    status = request.args.get("status")   # ex: active|expired
    q = request.args.get("q")             # ex: busca por email/nome
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    data = list_trial_users(status=status, q=q, page=page, per_page=per_page)
    summary = trial_users_summary()

    return jsonify({
        "summary": summary,
        "results": data,
        "page": page,
        "per_page": per_page
    })

@admin_bp.post("/trials/<user_id>/expire")
@login_required
@admin_required
def admin_expire_trial(user_id):
    """Força expiração do trial de um usuário específico."""
    ok = expire_trial(user_id)
    return jsonify({"user_id": user_id, "expired": bool(ok)}), (200 if ok else 400)

@admin_bp.post("/trials/<user_id>/create")
@login_required
@admin_required
def admin_create_trial(user_id):
    """Cria (ou recria) o trial de um usuário."""
    # você pode aceitar duration_days no body
    duration_days = int((request.json or {}).get("duration_days", 15))
    trial = create_trial(user_id=user_id, duration_days=duration_days)
    return jsonify({"user_id": user_id, "trial": trial}), 201

@admin_bp.get("/trials/<user_id>")
@login_required
@admin_required
def admin_get_trial(user_id):
    """Consulta o trial de um usuário específico."""
    trial = get_active_trial(user_id)
    return jsonify({"user_id": user_id, "trial": trial})
