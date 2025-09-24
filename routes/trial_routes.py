# routes/trial_routes.py
from flask import Blueprint, request, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from core.trial_store import trial_exists_recent, trial_register
from core.db import create_subscription
from datetime import datetime, timedelta, timezone

bp_trial = Blueprint("trial", __name__, url_prefix="/trial")

def _get_ip(req) -> str:
    xff = req.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return req.remote_addr or "0.0.0.0"

@bp_trial.get("/start")
@login_required
def trial_start():
    email = getattr(current_user, "email", "") or ""
    user_id = int(getattr(current_user, "id", 0) or 0)
    ip = _get_ip(request)
    fp = request.args.get("fp") or request.cookies.get("fp_token") or ""

    if trial_exists_recent(email, ip, fp, days=90):
        flash("Já existe um período de teste recente associado a este e-mail/dispositivo/rede.", "error")
        return redirect(url_for("landing"))  # volta para a landing com flash

    started = datetime.now(timezone.utc)
    ends = started + timedelta(days=14)
    create_subscription(
        user_id=user_id,
        plan="trial",
        billing="trial",
        vehicles=5,
        status="active",
        provider="internal",
        provider_ref="trial",
        started_at=started,
        current_period_end=ends
    )
    trial_register(user_id, email, ip, fp, note="trial 14d")
    # Em vez de mandar pro dashboard:
    return redirect(url_for("trial.thanks"))

@bp_trial.get("/thanks")
@login_required
def thanks():
    return render_template("trial_thanks.html")



