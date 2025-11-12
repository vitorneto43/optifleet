# routes/admin_routes.py
from flask import Blueprint, render_template, request, jsonify, abort
from flask_login import login_required
from core.authz import admin_required
from core.db import list_trial_users, trial_users_summary

admin_bp = Blueprint("admin", __name__, template_folder="../templates")

@admin_bp.route("/trials")
@login_required
@admin_required
def admin_trials():
    status = request.args.get("status")  # 'active' | 'expired' | None
    q = request.args.get("q")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    rows, total = list_trial_users(status=status, q=q, page=page, per_page=per_page)
    summary = trial_users_summary()

    # Se já tiver template:
    return render_template("admin/trials.html",
                           rows=rows, total=total, page=page, per_page=per_page,
                           status=status, q=q, summary=summary)

@admin_bp.route("/trials.json")
@login_required
@admin_required
def admin_trials_json():
    status = request.args.get("status")
    q = request.args.get("q")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    rows, total = list_trial_users(status=status, q=q, page=page, per_page=per_page)
    data = [{
        "id": r[0],
        "user_id": r[1],
        "email": r[2],
        "started_at": r[3].isoformat() if hasattr(r[3], "isoformat") else r[3],
        "ends_at": r[4].isoformat() if hasattr(r[4], "isoformat") else r[4],
        "status": r[5],
    } for r in rows]

    return jsonify({"total": total, "items": data})

