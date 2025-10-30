# routes/checkout_routes.py
from flask import Blueprint, request, redirect, url_for, render_template, abort
from flask_login import current_user, login_required
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from core.db import create_subscription, mark_subscription_status

bp_checkout = Blueprint("checkout", __name__, url_prefix="/billing")

# ----------------------------------------
# helpers de período
# ----------------------------------------
def _period_end(billing: str):
    now = datetime.now(timezone.utc)
    return now + (timedelta(days=365) if billing == "annual" else timedelta(days=30))

# ----------------------------------------
# VALIDAÇÃO
# agora aceita: route (start), full (pro), enterprise
# ----------------------------------------
def _validate(plan: str, billing: str, vehicles: int) -> bool:
    return plan in {"route", "full", "enterprise"} and billing in {"monthly", "annual"} and vehicles > 0

# ----------------------------------------
# CÁLCULO DE PREÇO
# ----------------------------------------
def _quote(plan: str, billing: str, vehicles: int):
    """
    - route      -> Start      -> 399/mês   até 5 veículos
    - full       -> Pro        -> 1499/mês  até 50 veículos
    - enterprise -> Enterprise -> 2200/mês  50+ veículos
    anual = 15% OFF no total anual
    """
    if plan == "route":              # START
        base = 399.00
        limit = 5
        plan_label = "Start"
    elif plan == "full":             # PRO
        base = 1499.00
        limit = 50
        plan_label = "Pro"
    else:                            # ENTERPRISE
        base = 2200.00
        limit = 999999               # não trava mais em 51
        plan_label = "Enterprise"

    # só limita se o plano tiver limite real
    vehicles = max(1, min(vehicles, limit))

    if billing == "annual":
        monthly_effective = round(base * 0.85, 2)  # 15% de desconto
        total_per_period = round(monthly_effective * 12, 2)
        period_label = "12 meses (anual)"
    else:
        monthly_effective = base
        total_per_period = base
        period_label = "1 mês (mensal)"

    return {
        "plan": plan,
        "plan_label": plan_label,
        "billing": billing,
        "vehicles": vehicles,
        "limit": limit,
        "monthly_effective": monthly_effective,
        "total_per_period": total_per_period,
        "period_label": period_label,
    }

# ----------------------------------------
# ENTRADA DO FLUXO
# ----------------------------------------
@bp_checkout.get("/billing/go")
@login_required
def start_checkout_get():
    plan    = (request.args.get("plan") or "").strip().lower()
    billing = (request.args.get("billing") or "monthly").strip().lower()
    try:
        vehicles = int(request.args.get("vehicles", "1"))
    except ValueError:
        vehicles = 1

    if not _validate(plan, billing, max(1, vehicles)):
        abort(400, description="Parâmetros inválidos.")

    if not current_user.is_authenticated:
        qs = urlencode({"plan": plan, "billing": billing, "vehicles": vehicles})
        return redirect(url_for("auth.login_page", next=f"/billing/go?{qs}"))

    qs = urlencode({"plan": plan, "billing": billing, "vehicles": vehicles})
    return redirect(url_for("checkout.view_checkout") + f"?{qs}")

# ----------------------------------------
# PÁGINA DE CHECKOUT
# ----------------------------------------
@bp_checkout.get("/checkout")
@login_required
def view_checkout():
    plan    = (request.args.get("plan") or "").strip().lower()
    billing = (request.args.get("billing") or "monthly").strip().lower()
    try:
        vehicles = int(request.args.get("vehicles", "1"))
    except ValueError:
        vehicles = 1

    if not _validate(plan, billing, max(1, vehicles)):
        abort(400, description="Parâmetros inválidos.")

    q = _quote(plan, billing, vehicles)
    # passa os campos que o teu checkout.html espera
    return render_template(
        "checkout.html",
        plan=q["plan"],
        plan_label=q["plan_label"],
        billing=q["billing"],
        vehicles=q["vehicles"],
        price=q["total_per_period"],          # ← aqui vai 22.440 se for enterprise anual
        monthly_equiv=q["monthly_effective"], # ← 1.870,00 no enterprise anual
    )

# ----------------------------------------
# CONFIRMAR (mock)
# ----------------------------------------
@bp_checkout.post("/checkout/confirm")
@login_required
def confirm_checkout():
    plan    = (request.form.get("plan") or "").strip().lower()
    billing = (request.form.get("billing") or "monthly").strip().lower()
    try:
        vehicles = int(request.form.get("vehicles", "1"))
    except ValueError:
        vehicles = 1

    if not _validate(plan, billing, max(1, vehicles)):
        abort(400, description="Parâmetros inválidos.")

    q = _quote(plan, billing, vehicles)

    sub_id = create_subscription(
        user_id=int(current_user.get_id()),
        plan=plan,
        billing=billing,
        vehicles=q["vehicles"],
        status="active",
        provider="mock",
        provider_ref=f"mock-{plan}",
        started_at=datetime.now(timezone.utc),
        current_period_end=_period_end(billing)
    )
    mark_subscription_status(sub_id, "active")

    return redirect(url_for("checkout.return_success", sid=sub_id))

@bp_checkout.get("/return/success")
@login_required
def return_success():
    return redirect(url_for("home", plan="ok"))

@bp_checkout.get("/return/fail")
@login_required
def return_fail():
    return redirect(url_for("home", plan="fail"))





