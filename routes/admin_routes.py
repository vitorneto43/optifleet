# routes/admin_routes.py
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required
from core.authz import admin_required
from core.db import list_trial_users, trial_users_summary

admin_bp = Blueprint("admin", __name__, template_folder="../templates/admin")

@admin_bp.route("/trials")
@login_required
@admin_required
def admin_trials():
    status = request.args.get("status")  # 'active' | 'expired' | None
    q = request.args.get("q")
    page = int(request.args.get("page", 1))
    per_page = int(request.args.get("per_page", 50))

    rows = list_trial_users(status=status, q=q, page=page, per_page=per_page)
    summary = trial_users_summary()

    # Você pode renderizar template ou retornar JSON:
    if request.headers.get("Accept", "").startswith("application/json"):
        return jsonify({"summary": summary, "data": rows})

    return render_template("admin/trials.html", rows=rows, summary=summary, page=page, per_page=per_page)

