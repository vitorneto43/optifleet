# routes/account_routes.py
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user
from datetime import datetime, timedelta, timezone
from core.db import get_active_subscription, create_subscription, get_active_trial, create_trial, mark_trial_converted


bp_account = Blueprint("account", __name__, url_prefix="/account")
def _uid(): return int(getattr(current_user, "id", 0) or 0)

@bp_account.get("/")
@login_required
def account_home():
    return render_template("account.html")

@bp_account.get("/account")
@login_required
def account_page():
    uid = _uid()
    sub = get_active_subscription(uid)
    trial = get_active_trial(uid)
    return render_template("account.html", sub=sub, trial=trial)

@bp_account.post("/account/start_trial")
@login_required
def account_start_trial():
    uid = _uid()
    # 14 dias, plano full, 5 veículos
    create_trial(uid, plan="full", vehicles=5, days=14)
    return redirect(url_for("account.account_page"))

@bp_account.post("/account/activate_plan")
@login_required
def account_activate_plan():
    uid = _uid()
    plan = (request.form.get("plan") or "full").lower()     # 'route' | 'full'
    billing = (request.form.get("billing") or "monthly")
    vehicles = int(request.form.get("vehicles") or 5)
    now = datetime.now(timezone.utc)
    end = now + (timedelta(days=30) if billing=="monthly" else timedelta(days=365))
    sid = create_subscription(uid, plan, billing, vehicles, status="active",
                              provider="internal", provider_ref=f"INT-{uid}-{now.timestamp()}",
                              started_at=now, current_period_end=end)
    # se tinha trial, marcar convertido
    trial = get_active_trial(uid)
    if trial:
        mark_trial_converted(trial[0])
    return redirect(url_for("account.account_page"))

@bp_account.post("/activate")
@login_required
def account_activate():
    # Vem da página /account (form/selects)
    plan = (request.form.get("plan") or "").lower()
    billing = (request.form.get("billing") or "monthly").lower()
    vehicles = request.form.get("vehicles") or request.args.get("vehicles") or "1"

    # 👉 NÃO ativa assinatura aqui!
    # 👉 Encaminha para o mesmo fluxo de cobrança
    return redirect(url_for("billing.go", plan=plan, billing=billing, vehicles=vehicles))